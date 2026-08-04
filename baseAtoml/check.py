#!/usr/bin/env python3
from __future__ import annotations

"""
check.py — 知识库定期巡检（deepseek-v4 版）
==========================================

参考 thirdPart/tools/lint.py，对 wiki 做一次体检并输出巡检报告。
LLM 调用复用 baseAtoml/llmbase.py（deepseek/deepseek-v4-pro）。

巡检项：
  结构类（确定性、零 LLM 调用）
    - 空页/桩页       正文过短（限流或写入失败的痕迹）
    - 孤儿页          没有任何入链
    - 坏链            [[WikiLink]] 指向不存在的页
    - 缺失实体页      被提及 >=3 次却没有独立页（可用 heal 补建）
    - 稀疏页          出链 < 2，易变孤儿
  图感知类（需 graph/graph.json）
    - hub 桩          高连接度但内容单薄的节点
    - 脆弱桥          两个社区仅靠 1 条边连接
    - 孤立社区        零外部连接的知识孤岛
  语义类（deepseek-v4）
    - 矛盾 / 过时内容 / 数据缺口 / 深度不足

用法：
    python baseAtoml/check.py                 # 打印巡检报告
    python baseAtoml/check.py --save          # 另存到 wiki/lint-report.md
    python baseAtoml/check.py --no-llm         # 只跑确定性检查（便宜，可高频）
    python baseAtoml/check.py --json           # 输出结构化 JSON（隐含 --no-llm）
"""

import os
import re
import sys
import json
import argparse
import statistics
from pathlib import Path
from collections import defaultdict
from datetime import date

# 让脚本无论从哪里运行都能找到同目录下的 llmbase
sys.path.insert(0, str(Path(__file__).resolve().parent))
from llmbase import call_llm, DEFAULT_MODEL


# ── 路径 ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
WIKI_DIR = REPO_ROOT / "wiki"
GRAPH_DIR = REPO_ROOT / "graph"
INDEX_FILE = WIKI_DIR / "index.md"
LOG_FILE = WIKI_DIR / "log.md"
OVERVIEW_FILE = WIKI_DIR / "overview.md"
GRAPH_JSON = GRAPH_DIR / "graph.json"

_META_EXCLUDE = {"index.md", "log.md", "lint-report.md", "health-report.md"}

# 语义巡检的返回上限（报告不长，无需动用 llmbase 的大默认值）。
SEMANTIC_MAX_TOKENS = 8192
# 结构阈值
STUB_THRESHOLD_CHARS = 100     # 正文短于此视为桩页
MIN_OUTBOUND_LINKS = 2         # 出链少于此视为稀疏页
ENTITY_MENTION_THRESHOLD = 3   # 被提及达到此次数却无页 → 缺失实体
HUB_MIN_CONTENT_CHARS = 500    # hub 节点内容短于此视为“桩”


# ── 通用小工具 ────────────────────────────────────────────────────────────

def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def all_wiki_pages() -> list[Path]:
    """wiki/ 下所有 .md（排除元数据文件）。"""
    if not WIKI_DIR.exists():
        return []
    return [p for p in WIKI_DIR.rglob("*.md") if p.name not in _META_EXCLUDE]


def extract_wikilinks(content: str) -> list[str]:
    return re.findall(r"\[\[([^\]]+)\]\]", content)


def wikilink_target(raw: str) -> str:
    """把 [[目标|显示名#锚点]] 归一化为解析用的“目标”名。"""
    return raw.split("|", 1)[0].split("#", 1)[0].strip()


def strip_frontmatter(content: str) -> str:
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            return content[end + 3:].strip()
    return content.strip()


def append_log(entry: str):
    """把巡检记录追加到 wiki/log.md（append-only）。"""
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


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# ── 结构检查（确定性）─────────────────────────────────────────────────────

def page_name_to_path(name: str, pages: list[Path]) -> list[Path]:
    """把 [[WikiLink]] 解析成实际页面路径。"""
    target = wikilink_target(name)
    return [p for p in pages if p.stem.lower() == target.lower()]


def check_empty_pages(pages: list[Path]) -> list[dict]:
    """空页 / 桩页：正文（去 frontmatter）过短。"""
    out = []
    for p in pages:
        body = strip_frontmatter(read_file(p))
        if len(body) < STUB_THRESHOLD_CHARS:
            out.append({
                "path": rel(p),
                "body_chars": len(body),
                "status": "empty" if len(body) == 0 else "stub",
            })
    return sorted(out, key=lambda x: x["body_chars"])


def find_orphans(pages: list[Path]) -> list[Path]:
    """没有任何入链的页（overview 除外）。"""
    inbound: dict[Path, int] = defaultdict(int)
    for p in pages:
        for link in extract_wikilinks(read_file(p)):
            for target in page_name_to_path(link, pages):
                inbound[target] += 1
    return [p for p in pages if inbound[p] == 0 and p != OVERVIEW_FILE]


def find_broken_links(pages: list[Path]) -> list[tuple[Path, str]]:
    """指向不存在页面的 [[WikiLink]]。"""
    broken = []
    for p in pages:
        for link in extract_wikilinks(read_file(p)):
            if not page_name_to_path(link, pages):
                broken.append((p, link))
    return broken


def find_missing_entities(pages: list[Path]) -> list[str]:
    """被提及 >= 阈值 次却没有独立页的实体名。"""
    counts: dict[str, int] = defaultdict(int)
    existing = {p.stem.lower() for p in pages}
    for p in pages:
        for link in extract_wikilinks(read_file(p)):
            target = wikilink_target(link)
            if target.lower() not in existing:
                counts[target] += 1
    return [name for name, c in counts.items() if c >= ENTITY_MENTION_THRESHOLD]


def check_link_density(pages: list[Path]) -> list[dict]:
    """出链少于 MIN_OUTBOUND_LINKS 的页（overview 除外）。"""
    out = []
    for p in pages:
        if p == OVERVIEW_FILE:
            continue
        links = {l.lower() for l in extract_wikilinks(read_file(p))}
        if len(links) < MIN_OUTBOUND_LINKS:
            out.append({"path": rel(p), "outbound": len(links), "links": sorted(links)})
    return sorted(out, key=lambda x: x["outbound"])


# ── 图感知检查（需 graph.json）────────────────────────────────────────────

def load_graph_data() -> dict | None:
    if not GRAPH_JSON.exists():
        return None
    try:
        return json.loads(GRAPH_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        print("  [warn] graph.json 损坏，跳过图感知检查")
        return None


def _degree_map(graph: dict) -> dict[str, int]:
    deg: dict[str, int] = {n["id"]: 0 for n in graph.get("nodes", [])}
    for e in graph.get("edges", []):
        deg[e["from"]] = deg.get(e["from"], 0) + 1
        deg[e["to"]] = deg.get(e["to"], 0) + 1
    return deg


def _community_map(graph: dict) -> dict[str, int]:
    return {n["id"]: n.get("group", -1) for n in graph.get("nodes", [])}


def check_hub_stubs(graph: dict, pages: list[Path]) -> list[dict]:
    """度数 > μ+2σ 但内容单薄的 hub 节点。"""
    deg = _degree_map(graph)
    vals = list(deg.values())
    if len(vals) < 2:
        return []
    threshold = statistics.mean(vals) + 2 * statistics.stdev(vals)
    node_to_path = {p.relative_to(WIKI_DIR).as_posix().replace(".md", ""): p for p in pages}
    out = []
    for nid, d in deg.items():
        if d <= threshold:
            continue
        p = node_to_path.get(nid)
        if p and len(read_file(p)) < HUB_MIN_CONTENT_CHARS:
            out.append({"node": nid, "degree": d, "content_chars": len(read_file(p)), "path": rel(p)})
    return sorted(out, key=lambda x: x["degree"], reverse=True)


def check_fragile_bridges(graph: dict) -> list[dict]:
    """两个社区之间只有 1 条边连接。"""
    comm = _community_map(graph)
    cross: dict[tuple[int, int], list[dict]] = {}
    for e in graph.get("edges", []):
        ca, cb = comm.get(e["from"], -1), comm.get(e["to"], -1)
        if ca < 0 or cb < 0 or ca == cb:
            continue
        cross.setdefault((min(ca, cb), max(ca, cb)), []).append(e)
    return [
        {"comm_a": k[0], "comm_b": k[1], "from": v[0]["from"], "to": v[0]["to"]}
        for k, v in sorted(cross.items()) if len(v) == 1
    ]


def check_isolated_communities(graph: dict) -> list[dict]:
    """零外部连接的社区（知识孤岛）。"""
    comm = _community_map(graph)
    members: dict[int, list[str]] = {}
    for nid, cid in comm.items():
        if cid >= 0:
            members.setdefault(cid, []).append(nid)
    has_external = set()
    for e in graph.get("edges", []):
        ca, cb = comm.get(e["from"], -1), comm.get(e["to"], -1)
        if ca >= 0 and cb >= 0 and ca != cb:
            has_external.update({ca, cb})
    return [
        {"community": cid, "nodes": len(m), "members": m[:10]}
        for cid, m in sorted(members.items())
        if len(m) >= 2 and cid not in has_external
    ]


# ── 语义巡检（deepseek-v4）─────────────────────────────────────────────────

def semantic_review(pages: list[Path]) -> str:
    sample = pages[:20]
    ctx = "".join(f"\n\n### {rel(p)}\n{read_file(p)[:1500]}" for p in sample)
    prompt = f"""You are inspecting an LLM Wiki. Review the pages below and identify:
1. Contradictions between pages (claims that conflict)
2. Stale content (summaries newer sources have superseded)
3. Data gaps (important questions the wiki can't answer — suggest specific sources)
4. Concepts mentioned but lacking depth

Wiki pages (sample of {len(sample)} pages):
{ctx}

Return a markdown report with these sections (be specific, name exact pages/claims):
## Contradictions
## Stale Content
## Data Gaps & Suggested Sources
## Concepts Needing More Depth
"""
    return call_llm(prompt, max_tokens=SEMANTIC_MAX_TOKENS)


# ── 编排与报告 ────────────────────────────────────────────────────────────

def run_structural(pages: list[Path]) -> dict:
    results = {
        "empty": check_empty_pages(pages),
        "orphans": [rel(p) for p in find_orphans(pages)],
        "broken": [(rel(p), link) for p, link in find_broken_links(pages)],
        "missing_entities": find_missing_entities(pages),
        "sparse": check_link_density(pages),
    }
    graph = load_graph_data()
    if graph and graph.get("nodes") and graph.get("edges"):
        results["hub_stubs"] = check_hub_stubs(graph, pages)
        results["fragile_bridges"] = check_fragile_bridges(graph)
        results["isolated_communities"] = check_isolated_communities(graph)
        results["graph"] = True
    else:
        results["graph"] = False
    return results


def format_report(res: dict, semantic: str | None, page_count: int) -> str:
    today = date.today().isoformat()
    L = [f"# Wiki 巡检报告 — {today}", "", f"共扫描 {page_count} 个页面。", "", "## 结构问题", ""]

    empty = res["empty"]
    L.append(f"### 空页 / 桩页 ({len(empty)})")
    if empty:
        for e in empty:
            emoji = "🔴" if e["status"] == "empty" else "🟡"
            L.append(f"- {emoji} `{e['path']}` — 正文 {e['body_chars']} 字符")
    else:
        L.append("所有页面均有正文内容。✅")
    L.append("")

    L.append(f"### 孤儿页（无入链, {len(res['orphans'])}）")
    L += [f"- `{p}`" for p in res["orphans"]] or ["无孤儿页。✅"]
    L.append("")

    L.append(f"### 坏链（指向不存在页面, {len(res['broken'])}）")
    L += [f"- `{p}` → `[[{link}]]`" for p, link in res["broken"]] or ["无坏链。✅"]
    L.append("")

    me = res["missing_entities"]
    L.append(f"### 缺失实体页（被提及 >= {ENTITY_MENTION_THRESHOLD} 次却无页, {len(me)}）")
    if me:
        L.append("> 提示：可写一个 heal 脚本自动补建这些实体页。")
        L += [f"- `[[{n}]]`" for n in me]
    else:
        L.append("无缺失实体页。✅")
    L.append("")

    sp = res["sparse"]
    L.append(f"### 稀疏页（出链 < {MIN_OUTBOUND_LINKS}, {len(sp)}）")
    if sp:
        for s in sp:
            existing = ", ".join(f"`[[{l}]]`" for l in s["links"]) or "—"
            L.append(f"- `{s['path']}` — 出链 {s['outbound']}：{existing}")
    else:
        L.append("链接密度充足。✅")
    L.append("")

    # 图感知
    L.append("## 图感知问题")
    L.append("")
    if not res["graph"]:
        L.append("> 未找到 graph/graph.json，跳过图感知检查。先构建知识图谱再巡检可获得更全的结论。")
        L.append("")
    else:
        hs = res["hub_stubs"]
        L.append(f"### hub 桩（高连接度但内容单薄, {len(hs)}）")
        L += [f"- `{h['path']}` — 度数 {h['degree']}，内容 {h['content_chars']} 字符" for h in hs] \
            or ["无 hub 桩。✅"]
        L.append("")
        fb = res["fragile_bridges"]
        L.append(f"### 脆弱桥（社区间仅 1 条边, {len(fb)}）")
        L += [f"- 社区 {b['comm_a']} ↔ {b['comm_b']}：`{b['from']}` → `{b['to']}`" for b in fb] \
            or ["无脆弱桥。✅"]
        L.append("")
        ic = res["isolated_communities"]
        L.append(f"### 孤立社区（零外部连接, {len(ic)}）")
        L += [f"- 社区 {c['community']}（{c['nodes']} 节点）：{', '.join(c['members'][:5])}" for c in ic] \
            or ["无孤立社区。✅"]
        L.append("")

    if semantic is not None:
        L.append("---")
        L.append("")
        L.append("## 语义巡检（deepseek-v4）")
        L.append("")
        L.append(semantic)

    return "\n".join(L)


def main():
    parser = argparse.ArgumentParser(description="知识库定期巡检")
    parser.add_argument("--save", action="store_true", help="另存到 wiki/lint-report.md")
    parser.add_argument("--no-llm", action="store_true", help="只跑确定性检查（跳过语义巡检）")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON（隐含 --no-llm）")
    args = parser.parse_args()

    pages = all_wiki_pages()
    if not pages:
        print("wiki 为空，无内容可巡检。先用 getwiki.py 摄取一些来源。")
        return

    print(f"巡检 {len(pages)} 个 wiki 页面...")
    res = run_structural(pages)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    print(f"  空页/桩页: {len(res['empty'])}")
    print(f"  孤儿页: {len(res['orphans'])}")
    print(f"  坏链: {len(res['broken'])}")
    print(f"  缺失实体页: {len(res['missing_entities'])}")
    print(f"  稀疏页: {len(res['sparse'])}")
    if res["graph"]:
        print(f"  hub 桩: {len(res['hub_stubs'])}  脆弱桥: {len(res['fragile_bridges'])}"
              f"  孤立社区: {len(res['isolated_communities'])}")
    else:
        print("  图感知检查: 跳过（无 graph.json）")

    semantic = None
    if not args.no_llm:
        print(f"  语义巡检中（{os.getenv('LLM_MODEL', DEFAULT_MODEL)}）...")
        try:
            semantic = semantic_review(pages)
        except Exception as e:
            print(f"  [warn] 语义巡检失败，仅输出结构报告: {e}")

    report = format_report(res, semantic, len(pages))
    print("\n" + report)

    if args.save:
        out = WIKI_DIR / "lint-report.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"\n已保存: {rel(out)}")

    append_log(f"## [{date.today().isoformat()}] check | 知识库巡检\n\n"
               f"扫描 {len(pages)} 页。空页{len(res['empty'])} 孤儿{len(res['orphans'])} "
               f"坏链{len(res['broken'])} 缺失实体{len(res['missing_entities'])} 稀疏{len(res['sparse'])}。")


if __name__ == "__main__":
    main()
