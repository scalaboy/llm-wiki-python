# tools/sleep_tool.py — 耗时工具函数：sleep 30s，消费 RabbitMQ 消息后返回 success
import time
import json
import logging
import pika

logger = logging.getLogger(__name__)

RABBITMQ_URL = "amqp://admin:bHw8JUmY9O4z@10.3.1.117:5672/"
QUEUE_NAME = "task_queue"


def long_running_task(task_id: str) -> str:
    """
    模拟耗时任务：
    1. sleep 30 秒
    2. 从 RabbitMQ 消费对应消息（确认任务闭环）
    3. 返回 "success"
    """
    logger.info(f"[{task_id}] 任务开始，sleep 30s ...")
    time.sleep(30)
    logger.info(f"[{task_id}] sleep 结束，开始消费 RabbitMQ 消息 ...")

    try:
        connection = pika.BlockingConnection(pika.URLParameters(RABBITMQ_URL))
        channel = connection.channel()
        channel.queue_declare(queue=QUEUE_NAME, durable=True)

        # 消费一条消息（自动 ack）
        method_frame, _header_frame, body = channel.basic_get(
            queue=QUEUE_NAME, auto_ack=True
        )

        if body:
            msg = json.loads(body)
            logger.info(f"[{task_id}] 消费到消息: {msg}")
        else:
            logger.warning(f"[{task_id}] 队列为空，没有消息可消费")

        connection.close()
    except Exception as e:
        logger.error(f"[{task_id}] RabbitMQ 消费失败: {e}")
        # 即使 RabbitMQ 不可用，也继续返回 success（演示容错）
        return "success"

    logger.info(f"[{task_id}] 任务完成，返回 success")
    return "success"
