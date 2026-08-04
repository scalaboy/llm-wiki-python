#!/usr/bin/env python3
"""
online.py — 万联易达企业经纪人在线问答服务
=============================================

基于 LLM Wiki + DeepSeek API 的 FastAPI 在线问答服务。
以「万联易达企业经纪人」身份，专业、正式、礼貌地
回答客户关于产品的咨询。

启动：
    python baseAtoml/online.py                  # 默认 0.0.0.0:8000
    python baseAtoml/online.py --port 9000       # 自定义端口
    python baseAtoml/online.py --host 127.0.0.1  # 仅本地

端点：
    GET  /              — 简易对话界面（HTML）
    POST /chat          — 发送问题，返回完整答案（JSON）
    POST /chat/stream   — 发送问题，SSE 流式返回答案
    GET  /health        — 健康检查
"""

from __future__ import annotations

import re
import sys
import json
import threading
from pathlib import Path
from datetime import date, datetime
from typing import AsyncGenerator
# 让脚本无论从哪里运行都能找到同目录下的 llmdeepseek
sys.path.insert(0, str(Path(__file__).resolve().parent))
from llmdeepseek import call_llm, call_llm_stream

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

# ── 模型 ────────────────────────────────────────────────────────────────
DEFAULT_MODEL = "deepseek-v4-flash"


# ── 路径 ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
WIKI_DIR = REPO_ROOT / "wiki"
INDEX_FILE = WIKI_DIR / "index.md"
LOG_FILE = WIKI_DIR / "log.md"
OVERVIEW_FILE = WIKI_DIR / "overview.md"
GRAPH_JSON = REPO_ROOT / "graph" / "graph.json"
INPUT_FILE = REPO_ROOT / "inputBycustomer"

# ── 参数 ────────────────────────────────────────────────────────────────
MAX_PAGES = 15
SELECT_MAX_TOKENS = 512
ANSWER_MAX_TOKENS = 8192
GRAPH_CONF_THRESHOLD = 0.7

# ── 经纪人 System Prompt ────────────────────────────────────────────────
BROKER_SYSTEM_PROMPT = """你是万联易达的企业经纪人，负责回答客户关于公司产品的咨询。

## 说话风格（重要）
- 口语化、自然，就像微信聊天一样，不要长篇大论。
- 每次回答控制在3-5句话以内，直奔主题，不啰嗦。
- 如果客户的问题能用一句话答清楚，就一句话。不要硬凑长度。
- 用「您」称呼客户，语气亲切但保持专业。

## 规则
- 只能基于知识库内容回答，不知道就说"这个我暂时不太清楚，建议您联系客户经理了解详情"。
- 不要编造数据、优惠或商务条款。
- 不要贬低竞品。

## 万联易达核心产品线
- 万油通：柴油/天然气智慧能源管理
- 易达宝：物流履约与运力匹配平台
- 万贸达：跨境贸易与供应链金融
- AI 外呼：智能语音外呼系统

根据对话历史和知识库内容，用口语化、简洁的方式回答客户。"""


# ── 工具函数 ────────────────────────────────────────────────────────────

def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def append_log(entry: str):
    header = (
        "# Wiki Log\n\n"
        "> Records important additions, revisions, and clarifications in the "
        "project knowledge layer. Maintained in append-only mode for agent and "
        "human traceability."
    )
    entry = entry.strip()
    if not LOG_FILE.exists():
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text(header + "\n\n" + entry + "\n", encoding="utf-8")
        return
    existing = read_file(LOG_FILE).rstrip() or header
    LOG_FILE.write_text(existing + "\n\n" + entry + "\n", encoding="utf-8")


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


# ── 对话历史（inputBycustomer）────────────────────────────────────────────

_HISTORY_MAX_LINES = 20  # 每次注入 LLM 的最近历史条数


def read_history() -> str:
    """读取最近的对话历史，作为多轮对话背景。"""
    if not INPUT_FILE.exists():
        return ""
    lines = INPUT_FILE.read_text(encoding="utf-8").strip().splitlines()
    # 取最后 N 行
    recent = lines[-_HISTORY_MAX_LINES:] if len(lines) > _HISTORY_MAX_LINES else lines
    return "\n".join(recent)


def append_history(question: str):
    """把用户问题追加到对话历史文件。"""
    ts = datetime.now().strftime("%m-%d %H:%M")
    line = f"[{ts}] 客户：{question.strip()}"
    if INPUT_FILE.exists():
        existing = INPUT_FILE.read_text(encoding="utf-8").rstrip()
        INPUT_FILE.write_text(existing + "\n" + line + "\n", encoding="utf-8")
    else:
        INPUT_FILE.write_text(line + "\n", encoding="utf-8")


# ── Wiki 缓存 ────────────────────────────────────────────────────────────

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, str]] = {}  # path_str -> (mtime, content)


def _cached_read(path: Path) -> str:
    """读取文件，按 mtime 缓存。不存在返回空串。"""
    if not path.exists():
        return ""
    key = str(path)
    mtime = path.stat().st_mtime
    with _cache_lock:
        if key in _cache:
            cached_mtime, content = _cache[key]
            if cached_mtime == mtime:
                return content
        content = read_file(path)
        _cache[key] = (mtime, content)
        return content


def refresh_cache():
    """强制刷新 wiki 核心文件缓存（index, overview, graph）。"""
    for p in [INDEX_FILE, OVERVIEW_FILE, GRAPH_JSON]:
        if p.exists():
            key = str(p)
            with _cache_lock:
                _cache.pop(key, None)
            _cached_read(p)


# ── 页面检索（复用 getanswer.py 逻辑）────────────────────────────────────

def find_relevant_pages(question: str, index_content: str) -> list[Path]:
    """从 index 里挑出与问题相关的页面。对中文用 2 字滑窗匹配。"""
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
            p = WIKI_DIR / href
            if p.exists() and p not in relevant:
                relevant.append(p)

    # 图扩展
    graph_raw = _cached_read(GRAPH_JSON)
    if graph_raw and relevant:
        try:
            graph = json.loads(graph_raw)
            ids = {p.relative_to(WIKI_DIR).as_posix().replace(".md", "") for p in relevant}
            neighbors: set[str] = set()
            for e in graph.get("edges", []):
                if e.get("confidence", 0) >= GRAPH_CONF_THRESHOLD:
                    if e["from"] in ids:
                        neighbors.add(e["to"])
                    elif e["to"] in ids:
                        neighbors.add(e["from"])
            for nid in neighbors:
                np = WIKI_DIR / f"{nid}.md"
                if np.exists() and np not in relevant:
                    relevant.append(np)
        except (json.JSONDecodeError, KeyError):
            pass

    if OVERVIEW_FILE.exists() and OVERVIEW_FILE not in relevant:
        relevant.insert(0, OVERVIEW_FILE)
    return relevant[:MAX_PAGES]


def select_pages_via_llm(question: str, index_content: str) -> list[Path]:
    """关键词兜底：让模型直接从 index 里挑相关页。"""
    prompt = (
        f"Given this wiki index:\n\n{index_content}\n\n"
        f'Which pages are most relevant to answering: "{question}"\n\n'
        'Return ONLY a JSON array of relative file paths as listed in the index, '
        'e.g. ["sources/foo.md", "concepts/Bar.md"]. Maximum 10 pages.'
    )
    raw = call_llm(prompt, model=DEFAULT_MODEL, max_tokens=SELECT_MAX_TOKENS).strip()
    # 清理 markdown 围栏
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        paths = json.loads(raw)
        return [WIKI_DIR / p for p in paths if (WIKI_DIR / p).exists()]
    except (json.JSONDecodeError, TypeError):
        return []


# ── 核心：检索 + 合成 ───────────────────────────────────────────────────

def gather_context(question: str) -> tuple[str, list[str], str]:
    """检索相关页面并构建上下文。返回 (pages_context, used_paths, index_content)。"""
    index_content = _cached_read(INDEX_FILE)
    if not index_content:
        return "", [], ""

    pages = find_relevant_pages(question, index_content)
    llm_picked = []
    if len(pages) <= 1:
        llm_picked = select_pages_via_llm(question, index_content)
        for p in llm_picked:
            if p not in pages:
                pages.append(p)

    if not pages:
        pages_context = f"\n\n### wiki/index.md\n{index_content}"
        used = ["index.md"]
    else:
        parts = []
        for p in pages:
            content = read_file(p)
            parts.append(f"\n\n### {p.relative_to(REPO_ROOT)}\n{content}")
        pages_context = "".join(parts)
        used = [str(p.relative_to(WIKI_DIR)) for p in pages]

    return pages_context, used, index_content


def build_user_prompt(question: str, pages_context: str, history: str | None = None) -> str:
    """构建发送给 LLM 的用户提示词，含对话历史作为背景。

    Args:
        question:      当前用户问题
        pages_context: 检索到的知识库页面内容
        history:       对话历史文本。为 None 时从 inputBycustomer 文件读取。
    """
    if history is None:
        history = read_history()
    history_block = ""
    if history:
        history_block = f"""## 对话历史（之前的客户问题，帮助你理解上下文）
{history}

"""

    return f"""{history_block}## 知识库相关内容

{pages_context}

---
当前客户问题：{question}

用口语化的方式回答，控制在3-5句话。"""


# ── FastAPI 应用 ────────────────────────────────────────────────────────

app = FastAPI(
    title="万联易达企业经纪人在线问答",
    description="基于 LLM Wiki + DeepSeek 的企业产品咨询服务",
    version="1.0.0",
)

# 允许跨域访问（浏览器直接打开 HTML 文件或不同端口/域名访问时均需）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[str]
    model: str


@app.on_event("startup")
async def startup():
    """服务启动时预热缓存。"""
    refresh_cache()
    print(f"  ✓ Wiki 缓存已预热")
    print(f"  ✓ 模型: {DEFAULT_MODEL}")
    print(f"  ✓ 服务就绪: http://0.0.0.0:8000")


@app.get("/health")
async def health():
    """健康检查。"""
    index_ok = INDEX_FILE.exists()
    return {
        "status": "ok" if index_ok else "degraded",
        "wiki_index": index_ok,
        "model": DEFAULT_MODEL,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    """简易对话界面。"""
    return HTMLResponse(content=LANDING_HTML)


@app.options("/chat")
@app.options("/chat/stream")
async def chat_options():
    """处理 CORS 预检请求（OPTIONS）。"""
    return {}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """非流式问答：返回完整答案。"""
    question = req.question.strip()
    if not question:
        return JSONResponse(status_code=400, content={"error": "问题不能为空"})

    # 记入对话历史
    append_history(question)

    pages_context, used, index_content = gather_context(question)
    if not index_content:
        return JSONResponse(
            status_code=503,
            content={"error": "知识库为空，请先使用 getwiki.py 摄取文档。"},
        )

    user_prompt = build_user_prompt(question, pages_context)

    try:
        answer = call_llm(
            user_prompt,
            model=DEFAULT_MODEL,
            system=BROKER_SYSTEM_PROMPT,
            max_tokens=ANSWER_MAX_TOKENS,
        )
    except Exception as e:
        return JSONResponse(
            status_code=502,
            content={"error": f"LLM 调用失败: {e}"},
        )

    # 记录日志
    today = date.today().isoformat()
    append_log(
        f"## [{today}] online-query | {question[:80]}\n\n"
        f"基于 {len(used)} 个页面合成答案。"
    )

    return ChatResponse(answer=answer, sources=used, model=DEFAULT_MODEL)


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """流式问答：SSE 逐 token 推送。"""

    async def event_generator() -> AsyncGenerator[str, None]:
        question = req.question.strip()
        if not question:
            yield f"data: {json.dumps({'error': '问题不能为空'})}\n\n"
            return

        # 记入对话历史
        append_history(question)

        pages_context, used, index_content = gather_context(question)
        if not index_content:
            yield f"data: {json.dumps({'error': '知识库为空'})}\n\n"
            return

        # 先推送检索到的来源列表
        yield f"data: {json.dumps({'type': 'sources', 'sources': used})}\n\n"

        user_prompt = build_user_prompt(question, pages_context)

        try:
            for chunk in call_llm_stream(
                user_prompt,
                model=DEFAULT_MODEL,
                system=BROKER_SYSTEM_PROMPT,
                max_tokens=ANSWER_MAX_TOKENS,
            ):
                yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            return

        # 结束标记
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

        # 记录日志
        today = date.today().isoformat()
        append_log(
            f"## [{today}] online-query (stream) | {question[:80]}\n\n"
            f"基于 {len(used)} 个页面合成答案。"
        )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── 简易 Web 界面 ───────────────────────────────────────────────────────

LANDING_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>万联易达 · 企业经纪人</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         background: #f5f7fa; color: #1a1a2e; display: flex; justify-content: center; }
  .container { width: 100%; max-width: 800px; min-height: 100vh; display: flex;
               flex-direction: column; background: #fff; box-shadow: 0 0 20px rgba(0,0,0,.06); }
  header { background: linear-gradient(135deg, #1a1a2e, #16213e); color: #fff;
           padding: 20px 24px; text-align: center; }
  header h1 { font-size: 20px; font-weight: 600; letter-spacing: 1px; }
  header p  { font-size: 13px; opacity: .7; margin-top: 4px; }
  #chatbox { flex: 1; overflow-y: auto; padding: 20px 24px; display: flex;
             flex-direction: column; gap: 16px; }
  .msg { max-width: 85%; padding: 12px 16px; border-radius: 12px; line-height: 1.65;
         font-size: 15px; word-wrap: break-word; }
  .msg.user { align-self: flex-end; background: #e8f0fe; color: #1a1a2e; }
  .msg.bot  { align-self: flex-start; background: #f0f2f5; color: #1a1a2e; }
  .msg.loading { background: #f0f2f5; color: #888; font-style: italic; }
  .sources { font-size: 12px; color: #888; margin-top: 6px; border-top: 1px solid #e0e0e0;
             padding-top: 6px; }
  .input-area { display: flex; padding: 12px 16px 20px; border-top: 1px solid #eee;
                gap: 8px; background: #fafbfc; }
  .input-area input { flex: 1; padding: 12px 16px; border: 1px solid #ddd;
                      border-radius: 24px; font-size: 15px; outline: none; }
  .input-area input:focus { border-color: #1a1a2e; }
  .input-area button { padding: 12px 24px; background: #1a1a2e; color: #fff;
                       border: none; border-radius: 24px; cursor: pointer; font-size: 15px; }
  .input-area button:hover { background: #16213e; }
  .input-area button:disabled { background: #aaa; cursor: not-allowed; }
  .status { text-align: center; font-size: 12px; color: #aaa; padding: 4px; }
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>🏢 万联易达 · 企业经纪人</h1>
    <p>专业产品咨询 · 正式商务解答</p>
  </header>
  <div id="chatbox">
    <div class="msg bot">
      您好，欢迎咨询万联易达。我是您的企业经纪人，请问有什么可以帮助您的？
    </div>
  </div>
  <div class="status" id="status">就绪</div>
  <div class="input-area">
    <input id="q" type="text" placeholder="请输入您的问题..." autofocus
           onkeydown="if(event.key==='Enter') send()">
    <button id="sendBtn" onclick="send()">发送</button>
  </div>
</div>
<script>
const chatbox = document.getElementById('chatbox');
const input = document.getElementById('q');
const btn = document.getElementById('sendBtn');
const statusEl = document.getElementById('status');

function addMsg(role, html) {
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.innerHTML = html;
  chatbox.appendChild(div);
  chatbox.scrollTop = chatbox.scrollHeight;
  return div;
}

function setStatus(s) { statusEl.textContent = s; }

async function send() {
  const q = input.value.trim();
  if (!q) return;
  input.value = '';
  btn.disabled = true;
  setStatus('检索知识库...');

  addMsg('user', escapeHtml(q));
  const botDiv = addMsg('bot', '');

  try {
    const resp = await fetch('/chat/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({question: q})
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let sources = [];

    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buf += decoder.decode(value, {stream: true});
      const lines = buf.split('\\n');
      buf = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const data = JSON.parse(line.slice(6));
          if (data.type === 'sources') {
            sources = data.sources;
            setStatus('基于 ' + sources.length + ' 个页面合成中...');
          } else if (data.type === 'token') {
            botDiv.innerHTML += escapeHtml(data.content);
            chatbox.scrollTop = chatbox.scrollHeight;
          } else if (data.type === 'done') {
            setStatus('就绪');
          } else if (data.type === 'error') {
            botDiv.innerHTML = '⚠️ ' + escapeHtml(data.content);
            setStatus('错误');
          }
        } catch(e) {}
      }
    }

    if (sources.length > 0) {
      const srcDiv = document.createElement('div');
      srcDiv.className = 'sources';
      srcDiv.textContent = '📚 参考来源：' + sources.join(', ');
      botDiv.appendChild(srcDiv);
    }

    if (!botDiv.innerHTML.trim()) {
      botDiv.innerHTML = '（未能生成回答，请稍后重试）';
    }
  } catch (e) {
    botDiv.innerHTML = '⚠️ 网络异常：' + escapeHtml(e.message);
    setStatus('连接失败');
  } finally {
    btn.disabled = false;
    input.focus();
  }
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
</script>
</body>
</html>"""


# ── 入口 ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="万联易达企业经纪人在线问答服务")
    parser.add_argument("--host", default="192.168.68.22", help="监听地址 (默认 192.168.68.22)")
    parser.add_argument("--port", type=int, default=12345, help="监听端口 (默认 12345)")
    args = parser.parse_args()

    print(f"\n  万联易达企业经纪人 · 在线问答服务")
    print(f"  ====================================")
    print(f"  模型: {DEFAULT_MODEL}")
    print(f"  地址: http://{args.host}:{args.port}")
    print(f"  界面: http://{args.host}:{args.port}/")
    print(f"  客户: htmls/customerAgent.html  (对接 {args.host}:{args.port})")
    print(f"  API:  POST /chat | POST /chat/stream | GET /health")
    print()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
