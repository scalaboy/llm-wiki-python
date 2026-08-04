#!/usr/bin/env python3
"""
getanswer.py — 基于 LLM Wiki 回答问题（deepseek-v4-flash 版）
=============================================================

参考 thirdPart/tools/query.py：读 index 找相关页 → 读页面 →
用 deepseek-v4-flash 综合出带 [[wikilink]] 引用的精准答案，可选存回
wiki/syntheses/。LLM 调用使用 baseAtoml/llmdeepseek.py（DeepSeek 官方 API）。

用法：
    python baseAtoml/getanswer.py "万油通如何帮物流企业降本？"
    python baseAtoml/getanswer.py "易达宝和万贸达的区别？" --save
    python baseAtoml/getanswer.py "梳理AI外呼能力" --save syntheses/ai-waihu.md
"""

from __future__ import annotations

import re
import sys
import json
import argparse
from pathlib import Path
from datetime import date

# 让脚本无论从哪里运行都能找到同目录下的 llmdeepseek
sys.path.insert(0, str(Path(__file__).resolve().parent))
from llmdeepseek import call_llm

# QA 使用的模型（DeepSeek 官方 API）
DEFAULT_MODEL = "deepseek-v4-flash"


# ── 路径 ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
WIKI_DIR = REPO_ROOT / "wiki"
INDEX_FILE = WIKI_DIR / "index.md"
LOG_FILE = WIKI_DIR / "log.md"
OVERVIEW_FILE = WIKI_DIR / "overview.md"
GRAPH_JSON = REPO_ROOT / "graph" / "graph.json"
SCHEMA_FILE = REPO_ROOT / "thirdPart" / "CLAUDE.md"

MAX_PAGES = 15            # 送入合成的页面数上限，避免上下文过长
SELECT_MAX_TOKENS = 512   # LLM 兜底选页的返回上限
ANSWER_MAX_TOKENS = 8192  # 合成答案的返回上限
GRAPH_CONF_THRESHOLD = 0.7  # 图扩展时采纳的边置信度


def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def append_log(entry: str):
    """把查询记录追加到 wiki/log.md（append-only）。"""
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


def find_relevant_pages(question: str, index_content: str) -> list[Path]:
    """从 index 里挑出与问题相关的页面。对中文用 2 字滑窗匹配。"""
    md_links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", index_content)
    q = question.lower()
    relevant: list[Path] = []

    for title, href in md_links:
        t = title.lower()
        if _has_cjk(title):
            # 中文：标题里任一含中文的 2 字片段出现在问题中即命中
            matched = any(
                t[j:j + 2] in q
                for j in range(len(t) - 1)
                if _has_cjk(t[j:j + 2])
            )
        else:
            # 拉丁：按词匹配（长度 > 2 的词）
            matched = any(w in q for w in t.split() if len(w) > 2)
        if matched:
            p = WIKI_DIR / href
            if p.exists() and p not in relevant:
                relevant.append(p)

    # 图扩展：把命中页在 graph.json 上的高置信邻居也纳入
    if GRAPH_JSON.exists() and relevant:
        try:
            graph = json.loads(GRAPH_JSON.read_text(encoding="utf-8"))
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

    # 始终带上 overview 作为全局背景
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
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        paths = json.loads(raw)
        return [WIKI_DIR / p for p in paths if (WIKI_DIR / p).exists()]
    except (json.JSONDecodeError, TypeError):
        return []


def answer(question: str, save_path: str | None = None):
    today = date.today().isoformat()

    index_content = read_file(INDEX_FILE)
    if not index_content:
        print("wiki 为空。先用 getwiki.py 摄取一些来源。")
        sys.exit(1)

    pages = find_relevant_pages(question, index_content)
    if len(pages) <= 1:
        print("  关键词命中不足，改用模型选页...")
        picked = select_pages_via_llm(question, index_content)
        for p in picked:
            if p not in pages:
                pages.append(p)

    if not pages:
        # 兜底：直接用 index 作为上下文
        pages_context = f"\n\n### wiki/index.md\n{index_content}"
        used = ["index.md"]
    else:
        pages_context = "".join(
            f"\n\n### {p.relative_to(REPO_ROOT)}\n{read_file(p)}" for p in pages
        )
        used = [str(p.relative_to(WIKI_DIR)) for p in pages]

    print(f"  基于 {len(used)} 个页面合成答案（{DEFAULT_MODEL}）...")
    prompt = f"""You are answering a question using an LLM Wiki. Use ONLY the wiki pages below to synthesize a precise, well-structured answer. Cite sources inline using [[PageName]] wikilink syntax. If the wiki does not contain enough information, say so explicitly rather than inventing facts.

Wiki pages:
{pages_context}

Question: {question}

用中文写一个结构清晰的 markdown 答案（可用小标题、要点）。所有关键结论都要用 [[页面名]] 标注来源。结尾加一个 ## 参考来源 小节，列出你引用的页面。
"""
    result = call_llm(prompt, model=DEFAULT_MODEL, max_tokens=ANSWER_MAX_TOKENS)

    print("\n" + "=" * 60)
    print(result)
    print("=" * 60)

    if save_path is not None:
        if save_path == "":
            slug = input("\n存为（slug，如 'my-analysis'）: ").strip()
            if not slug:
                print("跳过保存。")
                save_path = None
            else:
                save_path = f"syntheses/{slug}.md"

    if save_path:
        full = WIKI_DIR / save_path
        frontmatter = (
            f"---\ntitle: \"{question[:80]}\"\ntype: synthesis\ntags: []\n"
            f"sources: []\nlast_updated: {today}\n---\n\n"
        )
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(frontmatter + result, encoding="utf-8")
        print(f"  已保存: wiki/{save_path}")

        # 登记到 index 的 Syntheses 区
        idx = read_file(INDEX_FILE)
        entry = f"- [{question[:60]}]({save_path}) — synthesis"
        if "## Syntheses" in idx:
            idx = idx.replace("## Syntheses\n", f"## Syntheses\n{entry}\n")
            INDEX_FILE.write_text(idx, encoding="utf-8")
            print("  已登记到 index.md")

    append_log(
        f"## [{today}] query | {question[:80]}\n\n"
        f"基于 {len(used)} 个页面合成答案。"
        + (f" 已保存到 {save_path}。" if save_path else "")
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="基于 LLM Wiki 回答问题")
    parser.add_argument("question", help="要问的问题")
    parser.add_argument("--save", nargs="?", const="", default=None,
                        help="把答案存回 wiki（可选指定路径，如 syntheses/x.md）")
    args = parser.parse_args()
    answer(args.question, args.save)
