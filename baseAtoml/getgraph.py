#!/usr/bin/env python3
"""
getgraph.py — 从 LLM Wiki 构建知识图谱（deepseek-v4 版）
=====================================================

参考 thirdPart/tools/build_graph.py，做了精简。两趟构图：
  1. 确定性：解析所有 [[wikilink]] → EXTRACTED 边
  2. 语义：用 deepseek-v4 推断隐含关系 → INFERRED / AMBIGUOUS 边
再做 Louvain 社区检测，输出：
  graph/graph.json   节点/边数据（按页面 SHA256 缓存，仅重算变更页）
  graph/graph.html   自包含的 vis.js 交互可视化

LLM 调用复用 baseAtoml/llmbase.py。

用法：
    python baseAtoml/getgraph.py              # 完整构建
    python baseAtoml/getgraph.py --no-infer   # 跳过语义推断（快）
    python baseAtoml/getgraph.py --open        # 构建后打开 graph.html
    python baseAtoml/getgraph.py --clean       # 清空缓存后重建
    python baseAtoml/getgraph.py --report      # 附带打印图健康报告
    python baseAtoml/getgraph.py --report --save  # 报告存到 graph/graph-report.md
"""

from __future__ import annotations

import re
import sys
import json
import argparse
import hashlib
import webbrowser
from pathlib import Path
from datetime import date
from collections import defaultdict

# 让脚本无论从哪里运行都能找到同目录下的 llmbase
sys.path.insert(0, str(Path(__file__).resolve().parent))
from llmbase import call_llm, DEFAULT_MODEL

try:
    import networkx as nx
    from networkx.algorithms import community as nx_community
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False


# ── 路径与常量 ────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
WIKI_DIR = REPO_ROOT / "wiki"
GRAPH_DIR = REPO_ROOT / "graph"
LOG_FILE = WIKI_DIR / "log.md"
GRAPH_JSON = GRAPH_DIR / "graph.json"
GRAPH_HTML = GRAPH_DIR / "graph.html"
CACHE_FILE = GRAPH_DIR / ".cache.json"

_META_EXCLUDE = {"index.md", "log.md", "lint-report.md", "health-report.md"}
INFER_MAX_TOKENS = 8192     # 每页语义推断的返回上限（deepseek-v4 会先思考，需留足空间）
INFER_CONTENT_CHARS = 2000  # 送给模型的正文截断长度

TYPE_COLORS = {
    "source": "#4CAF50", "entity": "#2196F3", "concept": "#FF9800",
    "synthesis": "#9C27B0", "unknown": "#9E9E9E",
}
EDGE_COLORS = {"EXTRACTED": "#555555", "INFERRED": "#FF5722", "AMBIGUOUS": "#BDBDBD"}
COMMUNITY_COLORS = [
    "#E91E63", "#00BCD4", "#8BC34A", "#FF5722", "#673AB7",
    "#FFC107", "#009688", "#F44336", "#3F51B5", "#CDDC39",
]


# ── 通用小工具 ────────────────────────────────────────────────────────────

def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def all_wiki_pages() -> list[Path]:
    if not WIKI_DIR.exists():
        return []
    return [p for p in WIKI_DIR.rglob("*.md") if p.name not in _META_EXCLUDE]


def page_id(path: Path) -> str:
    return path.relative_to(WIKI_DIR).as_posix().removesuffix(".md")


def wikilink_target(raw: str) -> str:
    """[[目标|显示名#锚点]] → 解析用的“目标”名。"""
    return raw.split("|", 1)[0].split("#", 1)[0].strip()


def extract_wikilinks(content: str) -> list[str]:
    return re.findall(r"\[\[([^\]]+)\]\]", content)


def frontmatter_type(content: str) -> str:
    m = re.search(r"^type:\s*(\S+)", content, re.MULTILINE)
    return m.group(1).strip("\"'") if m else "unknown"


def frontmatter_title(content: str, fallback: str) -> str:
    m = re.search(r'^title:\s*"?([^"\n]+)"?', content, re.MULTILINE)
    return m.group(1).strip() if m else fallback


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


def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_cache(cache: dict):
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 第一趟：节点 + 确定性边 ────────────────────────────────────────────────

def build_nodes(pages: list[Path]) -> list[dict]:
    nodes = []
    for p in pages:
        content = read_file(p)
        ntype = frontmatter_type(content)
        body = re.sub(r"^---\n.*?\n---\n?", "", content, flags=re.DOTALL)
        preview = " ".join(l.strip() for l in body.splitlines() if l.strip())[:220]
        nodes.append({
            "id": page_id(p),
            "label": frontmatter_title(content, p.stem),
            "type": ntype,
            "color": TYPE_COLORS.get(ntype, TYPE_COLORS["unknown"]),
            "path": str(p.relative_to(REPO_ROOT)),
            "preview": preview,
        })
    return nodes


def build_extracted_edges(pages: list[Path]) -> list[dict]:
    """解析 [[wikilink]] 得到确定性边（别名/锚点已归一化）。"""
    stem_map = {p.stem.lower(): page_id(p) for p in pages}
    edges, seen = [], set()
    for p in pages:
        src = page_id(p)
        for raw in set(extract_wikilinks(read_file(p))):
            target = stem_map.get(wikilink_target(raw).lower())
            if target and target != src and (src, target) not in seen:
                seen.add((src, target))
                edges.append({
                    "id": f"{src}->{target}:EXTRACTED", "from": src, "to": target,
                    "type": "EXTRACTED", "color": EDGE_COLORS["EXTRACTED"],
                    "confidence": 1.0, "title": "", "label": "",
                })
    return edges


# ── 第二趟：语义推断（deepseek-v4，SHA256 缓存增量）──────────────────────────

def _extract_json(raw: str) -> dict | None:
    """从模型响应里稳健地取出第一个 JSON 对象（容忍代码围栏、前后废话、思维链）。"""
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw).strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 平衡括号：从第一个 '{' 起配对，避免贪婪匹配抓过界
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _edge_from_rel(src: str, rel: dict) -> dict | None:
    if not (isinstance(rel, dict) and rel.get("to")):
        return None
    conf = float(rel.get("confidence", 0.7))
    etype = rel.get("type") or ("INFERRED" if conf >= 0.7 else "AMBIGUOUS")
    return {
        "id": f"{src}->{rel['to']}:{etype}", "from": src, "to": rel["to"],
        "type": etype, "title": rel.get("relationship", ""), "label": "",
        "color": EDGE_COLORS.get(etype, EDGE_COLORS["INFERRED"]), "confidence": conf,
    }


def infer_edges(pages: list[Path], cache: dict) -> list[dict]:
    """对每页推断隐含关系。页面内容未变则复用缓存，避免重复调用。"""
    node_list = "\n".join(f"- {page_id(p)} ({frontmatter_type(read_file(p))})" for p in pages)
    valid_ids = {page_id(p) for p in pages}
    edges: list[dict] = []

    changed = [p for p in pages if cache.get(str(p), {}).get("hash") != sha256(read_file(p))]
    print(f"  语义推断：{len(changed)}/{len(pages)} 页需要重算（其余命中缓存）")

    # 先把命中缓存的边取回
    for p in pages:
        entry = cache.get(str(p))
        if entry and entry.get("hash") == sha256(read_file(p)):
            for rel in entry.get("edges", []):
                e = _edge_from_rel(page_id(p), rel)
                if e:
                    edges.append(e)

    for i, p in enumerate(changed, 1):
        src = page_id(p)
        content = read_file(p)[:INFER_CONTENT_CHARS]
        print(f"    [{i}/{len(changed)}] 推断 '{src}' ... ", end="", flush=True)
        prompt = f"""Analyze this wiki page and identify implicit semantic relationships to OTHER pages in the wiki (relationships not already written as [[wikilinks]]).

Source page: {src}
Content:
{content}

All available pages:
{node_list}

Return ONLY a raw JSON object beginning with {{ and ending with }}, exactly:
{{"edges": [{{"to": "page-id", "relationship": "one-line desc", "confidence": 0.0-1.0, "type": "INFERRED or AMBIGUOUS"}}]}}

Rules:
- "to" MUST be one of the page-ids listed above, and never equal to the source page.
- confidence >= 0.7 → INFERRED, else AMBIGUOUS.
- Return {{"edges": []}} if none. No prose, no markdown fences.
"""
        rels: list[dict] = []
        try:
            raw = call_llm(prompt, max_tokens=INFER_MAX_TOKENS)
            data = _extract_json(raw)
            edges_list = data.get("edges", []) if isinstance(data, dict) else []
            for rel in edges_list:
                if isinstance(rel, dict) and rel.get("to") in valid_ids and rel["to"] != src:
                    rels.append({
                        "to": rel["to"],
                        "relationship": rel.get("relationship", ""),
                        "confidence": float(rel.get("confidence", 0.7)),
                        "type": rel.get("type", ""),
                    })
            for rel in rels:
                e = _edge_from_rel(src, rel)
                if e:
                    edges.append(e)
            cache[str(p)] = {"hash": sha256(read_file(p)), "edges": rels}
            save_cache(cache)  # 每页落盘：任何中断都能续跑
            print(f"-> {len(rels)} 条")
        except Exception as e:  # noqa: BLE001 —— 单页失败不应中断整体
            print(f"-> [跳过] {str(e).splitlines()[0][:60]}")

    return edges


def deduplicate_edges(edges: list[dict]) -> list[dict]:
    """合并重复/双向边，保留置信度最高的一条。"""
    best: dict[tuple[str, str], dict] = {}
    for e in edges:
        key = (min(e["from"], e["to"]), max(e["from"], e["to"]))
        if key not in best or e.get("confidence", 0) > best[key].get("confidence", 0):
            best[key] = e
    return list(best.values())


# ── 社区检测 ──────────────────────────────────────────────────────────────

def detect_communities(nodes: list[dict], edges: list[dict]) -> dict[str, int]:
    if not HAS_NETWORKX or not edges:
        return {}
    g = nx.Graph()
    g.add_nodes_from(n["id"] for n in nodes)
    g.add_edges_from((e["from"], e["to"]) for e in edges)
    try:
        comms = nx_community.louvain_communities(g, seed=42)
        return {node: i for i, comm in enumerate(comms) for node in comm}
    except Exception:
        return {}


# ── 可视化 ────────────────────────────────────────────────────────────────

def render_html(nodes: list[dict], edges: list[dict]) -> str:
    data = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False)
    legend = "".join(
        f'<span style="margin-right:12px"><b style="color:{c}">●</b> {t}</span>'
        for t, c in TYPE_COLORS.items() if t != "unknown"
    )
    return f"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8"><title>LLM Wiki 知识图谱</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  html,body{{margin:0;height:100%;font-family:system-ui,Arial,sans-serif}}
  #bar{{padding:8px 12px;background:#fafafa;border-bottom:1px solid #ddd}}
  #search{{padding:4px 8px;width:240px}}
  #net{{width:100%;height:calc(100% - 90px)}}
  #info{{position:fixed;right:12px;top:96px;width:300px;max-height:70%;overflow:auto;
         background:#fff;border:1px solid #ccc;border-radius:8px;padding:12px;display:none;
         box-shadow:0 2px 8px rgba(0,0,0,.15)}}
</style>
</head>
<body>
<div id="bar">
  <input id="search" placeholder="搜索节点，回车定位">
  <span style="margin-left:16px">{legend}</span>
  <span id="stats" style="float:right;color:#888"></span>
</div>
<div id="net"></div>
<div id="info"></div>
<script>
const DATA = {data};
const nodes = new vis.DataSet(DATA.nodes.map(n => ({{...n, value:(n.value||1)}})));
const edges = new vis.DataSet(DATA.edges);
const net = new vis.Network(document.getElementById("net"), {{nodes, edges}}, {{
  nodes:{{shape:"dot", scaling:{{min:6,max:40}}, font:{{size:14}}}},
  edges:{{smooth:{{type:"continuous"}}, arrows:{{to:{{enabled:true,scaleFactor:.5}}}}}},
  physics:{{stabilization:true, barnesHut:{{gravitationalConstant:-8000,springLength:120}}}},
  interaction:{{hover:true, tooltipDelay:120}},
}});
document.getElementById("stats").textContent =
  DATA.nodes.length + " 节点 · " + DATA.edges.length + " 边";
const info = document.getElementById("info");
net.on("click", p => {{
  if(!p.nodes.length){{ info.style.display="none"; return; }}
  const n = nodes.get(p.nodes[0]);
  info.innerHTML = `<h3 style="margin:.2em 0">${{n.label||n.id}}</h3>`+
    `<div style="color:#888">${{n.type}}${{n.group!=null?" · 社区 "+n.group:""}}</div>`+
    `<p>${{(n.preview||"").replace(/</g,"&lt;")}}</p>`+
    `<code style="font-size:12px">${{n.path||""}}</code>`;
  info.style.display="block";
}});
document.getElementById("search").addEventListener("keydown", e => {{
  if(e.key!=="Enter") return;
  const q = e.target.value.trim().toLowerCase();
  const hit = DATA.nodes.find(n => (n.label||n.id).toLowerCase().includes(q) ||
                                    n.id.toLowerCase().includes(q));
  if(hit){{ net.selectNodes([hit.id]); net.focus(hit.id,{{scale:1.2,animation:true}}); }}
}});
</script>
</body>
</html>
"""


# ── 图健康报告（紧凑版）────────────────────────────────────────────────────

def find_phantom_hubs(pages: list[Path], min_refs: int = 2) -> list[dict]:
    """被 >=min_refs 个页面引用、却指向不存在页面的双链（应优先补建的信号）。"""
    stems = {p.stem.lower() for p in pages}
    refs: dict[str, set[str]] = defaultdict(set)
    for p in pages:
        for raw in extract_wikilinks(read_file(p)):
            t = wikilink_target(raw)
            if t.lower() not in stems:
                refs[t].add(page_id(p))
    out = [{"name": n, "refs": len(s)} for n, s in refs.items() if len(s) >= min_refs]
    return sorted(out, key=lambda x: x["refs"], reverse=True)


def build_report(nodes, edges, communities, pages) -> str:
    deg: dict[str, int] = defaultdict(int)
    for e in edges:
        deg[e["from"]] += 1
        deg[e["to"]] += 1
    n = len(nodes)
    orphans = [x["id"] for x in nodes if deg[x["id"]] == 0]
    ratio = round(len(edges) / n, 2) if n else 0
    ncomm = len(set(communities.values())) if communities else 0
    hubs = sorted(nodes, key=lambda x: deg[x["id"]], reverse=True)[:5]
    phantoms = find_phantom_hubs(pages)

    L = [f"# 知识图谱报告 — {date.today().isoformat()}", "",
         f"- 节点 **{n}**，边 **{len(edges)}**，边/节点比 **{ratio}**",
         f"- 社区 **{ncomm}** 个，孤儿节点 **{len(orphans)}** 个", "",
         "## 连接度最高的节点（hub）"]
    L += [f"- `{h['id']}` — 度数 {deg[h['id']]}" for h in hubs] or ["- （无）"]
    L += ["", f"## 孤儿节点（{len(orphans)}）"]
    L += [f"- `{o}`" for o in orphans] or ["- 无孤儿节点。✅"]
    L += ["", f"## 幽灵 hub（被 2+ 页引用却无页, {len(phantoms)}）"]
    L += [f"- `[[{p['name']}]]` — 被 {p['refs']} 页引用" for p in phantoms] or ["- 无。✅"]
    return "\n".join(L)


# ── 编排 ──────────────────────────────────────────────────────────────────

def build_graph(infer=True, open_browser=False, clean=False, report=False, save=False):
    pages = all_wiki_pages()
    if not pages:
        print("wiki 为空。先用 getwiki.py 摄取来源。")
        return
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    if clean and CACHE_FILE.exists():
        CACHE_FILE.unlink()
        print("  已清空缓存")

    print(f"从 {len(pages)} 个页面构建图谱...")
    nodes = build_nodes(pages)
    edges = build_extracted_edges(pages)
    print(f"  确定性边（EXTRACTED）：{len(edges)}")

    if infer:
        cache = load_cache()
        edges += infer_edges(pages, cache)
        save_cache(cache)

    edges = deduplicate_edges(edges)

    communities = detect_communities(nodes, edges)
    if communities:
        print(f"  Louvain 社区：{len(set(communities.values()))} 个")
    elif not HAS_NETWORKX:
        print("  [提示] 未安装 networkx，跳过社区检测（pip install networkx）")

    # 社区上色 + 按度数定节点大小
    deg: dict[str, int] = defaultdict(int)
    for e in edges:
        deg[e["from"]] += 1
        deg[e["to"]] += 1
    for node in nodes:
        cid = communities.get(node["id"], -1)
        node["group"] = cid
        if cid >= 0:
            node["color"] = COMMUNITY_COLORS[cid % len(COMMUNITY_COLORS)]
        node["value"] = deg[node["id"]] + 1

    GRAPH_JSON.write_text(
        json.dumps({"nodes": nodes, "edges": edges, "built": date.today().isoformat()},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    GRAPH_HTML.write_text(render_html(nodes, edges), encoding="utf-8")
    print(f"  已写出：graph/graph.json（{len(nodes)} 节点, {len(edges)} 边）")
    print(f"  已写出：graph/graph.html")

    n_ext = sum(1 for e in edges if e["type"] == "EXTRACTED")
    n_inf = len(edges) - n_ext
    append_log(f"## [{date.today().isoformat()}] graph | 知识图谱重建\n\n"
               f"{len(nodes)} 节点，{len(edges)} 边（{n_ext} 提取，{n_inf} 推断）。")

    if report:
        rep = build_report(nodes, edges, communities, pages)
        print("\n" + rep)
        if save:
            (GRAPH_DIR / "graph-report.md").write_text(rep, encoding="utf-8")
            print("\n已保存: graph/graph-report.md")

    if open_browser:
        webbrowser.open(GRAPH_HTML.as_uri())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="构建 LLM Wiki 知识图谱")
    parser.add_argument("--no-infer", action="store_true", help="跳过语义推断（快）")
    parser.add_argument("--open", action="store_true", help="构建后在浏览器打开 graph.html")
    parser.add_argument("--clean", action="store_true", help="清空缓存后重建")
    parser.add_argument("--report", action="store_true", help="附带打印图健康报告")
    parser.add_argument("--save", action="store_true", help="把报告存到 graph/graph-report.md")
    args = parser.parse_args()
    build_graph(infer=not args.no_infer, open_browser=args.open,
                clean=args.clean, report=args.report, save=args.save)
