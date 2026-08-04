"""
FastAPI web service for Aira Agent WebPortal.
接口：/testfirst、/api/addCompanyDoc、/chatbywiki。
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Dict

import pika
import pymysql
import uvicorn
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from utils.insertTable import insert_company_doc, insert_userComQuest
from utils.select import get_recent_quests

# 复用 baseAtoml/online.py 的 wiki 检索与流式问答逻辑
from baseAtoml.online import (
    ANSWER_MAX_TOKENS,
    DEFAULT_MODEL,
    build_user_prompt,
    call_llm_stream,
)

# ---- 路径常量 ----
MAINDIR = Path(__file__).resolve().parent / "maindir"


# ---- 企业 Wiki 检索 ----

def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def _find_company_pages(question: str, index_content: str, wiki_dir: Path) -> list[Path]:
    """从企业 wiki 的 index.md 中挑出与问题相关的页面。"""
    md_links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", index_content)
    q = question.lower()
    relevant: list[Path] = []

    for title, href in md_links:
        t = title.lower()
        if _has_cjk(title):
            matched = any(
                t[j:j + 2] in q
                for j in range(len(t) - 1)
                if _has_cjk(t[j:j + 2])
            )
        else:
            matched = any(w in q for w in t.split() if len(w) > 2)
        if matched:
            p = wiki_dir / href
            if p.exists() and p not in relevant:
                relevant.append(p)

    # overview 总是放在最前面
    overview = wiki_dir / "overview.md"
    if overview.exists() and overview not in relevant:
        relevant.insert(0, overview)

    return relevant[:15]


def gather_company_context(companyid: str, question: str) -> tuple[str, list[str], str]:
    """
    从 maindir/<companyid>/ 加载企业 wiki 并检索相关页面。

    Returns:
        (pages_context, used_paths, index_content)
        如果企业 wiki 不存在，返回 ("", [], "") 不报错。
    """
    wiki_dir = MAINDIR / companyid
    index_file = wiki_dir / "index.md"

    if not index_file.exists():
        return "", [], ""

    index_content = index_file.read_text(encoding="utf-8")
    if not index_content.strip():
        return "", [], ""

    pages = _find_company_pages(question, index_content, wiki_dir)

    if not pages:
        pages_context = f"\n\n### {companyid}/index.md\n{index_content}"
        used = ["index.md"]
    else:
        parts = []
        for p in pages:
            content = p.read_text(encoding="utf-8")
            parts.append(f"\n\n### {p.relative_to(wiki_dir)}\n{content}")
        pages_context = "".join(parts)
        used = [str(p.relative_to(wiki_dir)) for p in pages]

    return pages_context, used, index_content


def build_system_prompt(companyname: str) -> str:
    """根据企业名称动态生成经纪人 system prompt。"""
    return f"""你是{companyname}的企业经纪人，负责回答客户关于公司产品的咨询。

## 说话风格（重要）
- 口语化、自然，就像微信聊天一样，不要长篇大论。
- 每次回答控制在3-5句话以内，直奔主题，不啰嗦。
- 如果客户的问题能用一句话答清楚，就一句话。不要硬凑长度。
- 用「您」称呼客户，语气亲切但保持专业。

## 规则
- 只能基于知识库内容回答，不知道就说"这个我暂时不太清楚，建议您联系客户经理了解详情"。
- 不要编造数据、优惠或商务条款。
- 不要贬低竞品。

根据对话历史和知识库内容，用口语化、简洁的方式回答客户。"""


app = FastAPI(
    title="Aira Assistant Agent API",
    description="门户对话智能体服务",
    version="1.0.0",
)


@app.get("/testfirst")
async def test_first():
    """测试接口，直接返回成功。"""
    return {"success": True, "message": "success"}


# ---- RabbitMQ ----

from utils.env_config import load_mq_config, load_mq_queue_name
from utils.addComDocFromMQ import create_company_wiki

ADD_COMPANY_DOC_QUEUE = "addCompanyDoc_queue"

# 后台任务状态追踪
_task_store: dict[str, dict] = {}


def _get_mq_url() -> str:
    """从 env 配置获取 RabbitMQ AMQP URL。"""
    return load_mq_config()


def _get_mq_queue() -> str:
    """从 env 配置获取队列名，默认 addCompanyDoc_queue。"""
    return load_mq_queue_name()


def _publish_to_mq(task_id: str, user: str, companyId: str,
                   companyName: str, docAddress: str) -> None:
    """向 RabbitMQ 发布一条 addCompanyDoc 任务消息。"""
    mq_url = _get_mq_url()
    queue = _get_mq_queue()
    message = {
        "task_id": task_id,
        "user": user,
        "companyId": companyId,
        "companyName": companyName,
        "docAddress": docAddress,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    connection = pika.BlockingConnection(pika.URLParameters(mq_url))
    channel = connection.channel()
    channel.queue_declare(queue=queue, durable=True)
    channel.basic_publish(
        exchange="",
        routing_key=queue,
        body=json.dumps(message, ensure_ascii=False),
        properties=pika.BasicProperties(delivery_mode=2),
    )
    connection.close()


def _background_add_company_doc(task_id: str, user: str, companyId: str,
                                companyName: str, docAddress: str) -> None:
    """后台任务：发 MQ → 下载文档 → 构建 wiki。"""
    _task_store[task_id] = {
        "status": "processing",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    # 1) 发布消息到 RabbitMQ（持久化，保证不丢）
    _publish_to_mq(task_id, user, companyId, companyName, docAddress)
    print(f"[{task_id}] 已发布到 RabbitMQ: companyId={companyId}")

    # 2) 异步执行 wiki 构建（下载文件 + LLM 生成）
    ok = create_company_wiki(companyId, companyName, docAddress)

    # 3) 更新状态
    _task_store[task_id] = {
        "status": "completed" if ok else "failed",
        "result": "success" if ok else "wiki_build_failed",
        "started_at": _task_store[task_id]["started_at"],
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    print(f"[{task_id}] 后台任务完成: {'success' if ok else 'failed'}")


# ---- Routes ----


@app.get("/api/addCompanyDoc")
async def get_addCompanyDoc(request: Request, background_tasks: BackgroundTasks):
    """接收请求 → 入 MQ → 后台异步下载文档并构建 wiki → 立即返回。"""
    values: Dict[str, str] = request.query_params._dict
    print(values)

    user = values.get("user", "")
    companyId = values.get("companyId", "")
    companyName = values.get("companyName", "")
    docAddress = values.get("docAddress", "")

    task_id = str(uuid.uuid4())[:8]

    try:
        background_tasks.add_task(
            _background_add_company_doc,
            task_id, user, companyId, companyName, docAddress,
        )
        print(f"[{task_id}] 已接收请求，加入后台任务队列: companyId={companyId}")
        return JSONResponse(content={
            "retcode": 1,
            "task_id": task_id,
            "message": "任务已提交，后台异步执行中",
        })
    except Exception as e:
        print(f"[{task_id}] 提交后台任务失败: {e}")
        return JSONResponse(content={
            "retcode": 0,
            "task_id": task_id,
            "message": f"服务异常: {e}",
        })


@app.get("/api/addCompanyDoc/status/{task_id}")
async def get_add_company_doc_status(task_id: str):
    """查询异步任务状态。"""
    if task_id not in _task_store:
        return JSONResponse(content={"status": "not_found", "task_id": task_id})
    return JSONResponse(content={"task_id": task_id, **_task_store[task_id]})


@app.get("/api/getDataByWanmol1")
async def get_data_by_wanmol(request: Request):
    """返回 assets/baseAgent.json 的完整内容（原样返回）。"""
    values: Dict[str, str] = request.query_params._dict
    print(values)

    pageSize = values.get("pageSize", "")
    pageNum = values.get("pageNum", "")
    print("pageSize:", pageSize, "pageNum:", pageNum)

    json_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "assets", "baseAgent.json"
    )
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return JSONResponse(content=data)


# ---- MySQL ----

from utils.env_config import load_mysql_config


def _get_mysql_conn():
    cfg = load_mysql_config()
    return pymysql.connect(
        host=cfg["MYSQL_HOST"],
        port=int(cfg.get("MYSQL_PORT", 3306)),
        user=cfg["MYSQL_USER"],
        password=cfg["MYSQL_PASSWORD"],
        database=cfg["MYSQL_DB"],
        charset="utf8mb4",
    )


# ---- Routes ----

@app.get("/api/getDataByWanmol")
async def get_table2cinnic(request: Request):
    """分页返回 agentBaseinfo 表数据，格式对齐 assets/baseAgent.json。"""
    values: Dict[str, str] = request.query_params._dict
    try:
        page_size = int(values.get("pageSize", 10))
    except (TypeError, ValueError):
        page_size = 10
    try:
        page_num = int(values.get("pageNum", 1))
    except (TypeError, ValueError):
        page_num = 1

    conn = _get_mysql_conn()
    try:
        with conn.cursor() as cur:
            # 总数
            cur.execute("SELECT COUNT(*) FROM agentBaseinfo")
            total = cur.fetchone()[0]

            # 分页查询
            offset = (page_num - 1) * page_size
            cur.execute(
                "SELECT agentName, agentVersion, agentPlatform, agentType, "
                "agentTags, agentDescription, enterpriseName, enterpriseScale, "
                "location, accessInterface "
                "FROM agentBaseinfo "
                "ORDER BY id "
                "LIMIT %s OFFSET %s",
                (page_size, offset),
            )
            rows_raw = cur.fetchall()
    finally:
        conn.close()

    # 转换为 baseAgent.json 格式
    rows = []
    for r in rows_raw:
        agent_name, version, platform, atype, tags_str, desc, ent_name, scale, loc, access_url = r

        # agentTags: JSON 字符串 → 列表
        try:
            agent_tags = json.loads(tags_str) if tags_str else []
        except (json.JSONDecodeError, TypeError):
            agent_tags = []

        # accessInterface: URL → "p=ANP;v=v1.0;url=..." 包装
        access_interface = []
        if access_url:
            access_interface.append(f"p=ANP;v=v1.0;url={access_url}")

        rows.append(
            {
                "agentName": agent_name or "",
                "agentDescription": desc or "",
                "agentPlatform": platform or "",
                "agentTags": agent_tags,
                "agentType": atype or "",
                "agentVersion": version or "",
                "accessInterface": access_interface,
                "enterpriseName": ent_name or "",
                "enterpriseScale": scale or "",
                "location": loc or "",
            }
        )

    return JSONResponse(
        content={
            "total": total,
            "rows": rows,
            "pageSize": page_size,
            "pageNum": page_num,
            "code": 200,
            "msg": "查询成功",
        }
    )


@app.get("/api/chatbywiki")
async def chatbywiki(request: Request):
    """基于企业 wiki 知识库的流式问答，返回 SSE 格式的 StreamingResponse。"""
    values: Dict[str, str] = request.query_params._dict
    print(values)
    question = values.get("question", "").strip()
    userid = values.get("userid", "")
    companyid = values.get("companyid", "")
    companyname = values.get("companyname", "")

    async def event_generator() -> AsyncGenerator[str, None]:
        if not question:
            yield f"data: {json.dumps({'error': '问题不能为空'})}\n\n"
            return

        # 1. 从 DB 读取历史对话，与当前问题拼接
        history_text = get_recent_quests(userid, companyid)
        if history_text:
            history_text = history_text + "\n" + question
        else:
            history_text = question

        # 2. 将当前问题写入 DB（turn 自动递增）
        insert_userComQuest(userid, companyid, question)

        # 3. 加载企业专属 wiki（maindir/<companyid>/），没有则空加载
        pages_context, used, index_content = gather_company_context(companyid, question)
        if not index_content:
            pages_context = ""
            used = []

        # 4. 推送检索到的来源列表
        yield f"data: {json.dumps({'type': 'sources', 'sources': used})}\n\n"

        # 5. 动态 system prompt
        system_prompt = build_system_prompt(companyname)

        user_prompt = build_user_prompt(question, pages_context, history=history_text)

        try:
            for chunk in call_llm_stream(
                user_prompt,
                model=DEFAULT_MODEL,
                system=system_prompt,
                max_tokens=ANSWER_MAX_TOKENS,
            ):
                yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            return

        # 结束标记
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
    #uvicorn.run("app:app", host="192.168.68.22", port=18000, reload=True, log_level="info")

