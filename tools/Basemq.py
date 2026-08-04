# app.py — 万联智链 异步任务测试服务 (FastAPI + RabbitMQ)
#
# 启动: uvicorn app:app --host 0.0.0.0 --port 34560
#
# 流程:
#   GET /test
#     → 立即返回 {"status": "accepted", "task_id": "..."}
#     → 后台异步: 1) 发消息到 RabbitMQ  2) 调用 sleep_tool(30s)
#     → sleep_tool: sleep 30s → 消费 RabbitMQ 消息 → 返回 "success"

import json
import logging
import uuid
from datetime import datetime, timezone

import pika
from fastapi import FastAPI, BackgroundTasks

from tools.sleep_tool import long_running_task, RABBITMQ_URL, QUEUE_NAME

# ---------- logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app")

# ---------- FastAPI ----------
app = FastAPI(title="alphaDeal Async Test")

# ---------- 内存状态（演示用，生产环境应换 Redis / DB）----------
task_store: dict[str, dict] = {}


# ---------- RabbitMQ 发布 ----------

def publish_to_rabbitmq(task_id: str) -> None:
    """向 RabbitMQ 发布一条任务消息。"""
    message = {
        "task_id": task_id,
        "status": "processing",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "description": "sleep_tool 异步任务已启动，30s 后将被消费",
    }
    try:
        connection = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
        channel = connection.channel()
        channel.queue_declare(queue=QUEUE_NAME, durable=True)
        channel.basic_publish(
            exchange="",
            routing_key=QUEUE_NAME,
            body=json.dumps(message, ensure_ascii=False),
            properties=pika.BasicProperties(delivery_mode=2),  # 持久化
        )
        connection.close()
        logger.info(f"[{task_id}] 已发布消息到 RabbitMQ: {message}")
    except Exception as e:
        logger.error(f"[{task_id}] RabbitMQ 发布失败: {e}")


# ---------- 后台任务 ----------

def background_task(task_id: str) -> None:
    """后台任务：发消息 → 调工具 → 更新状态。"""
    task_store[task_id] = {"status": "processing", "started_at": datetime.now(timezone.utc).isoformat()}

    # 1) 发布消息到 RabbitMQ（工具函数 sleep 30s 后会消费它）
    publish_to_rabbitmq(task_id)

    # 2) 异步调用工具函数（阻塞式，但在 BackgroundTasks 中运行）
    result = long_running_task(task_id)

    # 3) 更新状态
    task_store[task_id] = {
        "status": "completed",
        "result": result,
        "started_at": task_store[task_id]["started_at"],
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    logger.info(f"[{task_id}] 后台任务完成: {result}")


# ---------- 路由 ----------

@app.get("/test")
async def test(background_tasks: BackgroundTasks):
    """
    异步测试接口：
    - 立即返回 task_id
    - 后台执行: RabbitMQ publish → sleep 30s → RabbitMQ consume → success
    """
    task_id = str(uuid.uuid4())[:8]
    background_tasks.add_task(background_task, task_id)
    logger.info(f"[{task_id}] 已接收请求，加入后台任务队列")
    return {
        "status": "accepted",
        "task_id": task_id,
        "message": "任务已提交，30s 后完成。查看 /status/{task_id} 获取结果",
    }


@app.get("/status/{task_id}")
async def get_status(task_id: str):
    """查询异步任务状态。"""
    if task_id not in task_store:
        return {"status": "not_found", "task_id": task_id}
    return {"task_id": task_id, **task_store[task_id]}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------- main ----------

def main() -> None:
    import uvicorn
    uvicorn.run("app:app", host="192.168.68.22", port=34560, reload=True)


if __name__ == "__main__":
    main()
