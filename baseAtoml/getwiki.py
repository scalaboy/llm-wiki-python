#!/usr/bin/env python3
"""
getwiki.py — 把源文档摄取进 LLM Wiki（deepseek-v4-flash 版）
============================================================

参考 thirdPart/tools/ingest.py 的摄取流程，LLM 调用层使用
baseAtoml/llmdeepseek.py（DeepSeek 官方 API），模型固定为
deepseek-v4-flash。

用法：
    python baseAtoml/getwiki.py <path-to-source>
    python baseAtoml/getwiki.py raw/articles/my-article.md
    python baseAtoml/getwiki.py report.pdf                 # 自动转 .md
    python baseAtoml/getwiki.py slides.pptx notes.docx      # 批量、混合格式
    python baseAtoml/getwiki.py raw/mixed/ --no-convert     # 跳过自动转换
    python baseAtoml/getwiki.py --validate-only             # 只跑校验

支持的格式（经 markitdown 自动转换）：
    .pdf .docx .pptx .xlsx .html .htm .txt .csv .json .xml
    .rst .rtf .epub .ipynb .yaml .yml .tsv .wav .mp3

模型读源文档、抽取知识并更新 wiki：
  - 生成 wiki/sources/<slug>.md
  - 更新 wiki/index.md
  - 更新 wiki/overview.md（按需）
  - 创建/更新实体页与概念页
  - 追加 wiki/log.md
  - 标记矛盾
  - 摄取后校验（坏链、index 覆盖）
"""

from __future__ import annotations

import os
import re
import sys
import json
import hashlib
import tempfile
from pathlib import Path
from datetime import date, datetime

# 让脚本无论从哪里运行都能找到同目录下的 llmdeepseek
sys.path.insert(0, str(Path(__file__).resolve().parent))
from llmdeepseek import call_llm

# 摄取使用的模型（DeepSeek 官方 API）
DEFAULT_MODEL = "deepseek-v4-flash"


# ── 路径 ────────────────────────────────────────────────────────────────
# getwiki.py 位于 <repo>/baseAtoml/ 下，故 REPO_ROOT 为其上两级（工作区根）。
REPO_ROOT = Path(__file__).resolve().parent.parent
WIKI_DIR = REPO_ROOT / "wiki"
RAW_DIR = REPO_ROOT / "raw"
INDEX_FILE = WIKI_DIR / "index.md"
LOG_FILE = WIKI_DIR / "log.md"
OVERVIEW_FILE = WIKI_DIR / "overview.md"

# 摄取清单：记录已成功摄取的源（按内容哈希），用于续跑/跳过已完成文档。
MANIFEST_FILE = WIKI_DIR / ".ingest_manifest.json"

# 复用 thirdPart 里已有的 schema 说明书。若不存在则退化为空 schema。
SCHEMA_FILE = REPO_ROOT / "thirdPart" / "CLAUDE.md"

# 可自动转换为 markdown 的扩展名（.md 直接摄取，无需转换）。
CONVERTIBLE_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xls",
    ".html", ".htm", ".txt", ".csv", ".json", ".xml",
    ".rst", ".rtf", ".epub", ".ipynb",
    ".yaml", ".yml", ".tsv",
    ".wav", ".mp3",
}
ALL_SUPPORTED_EXTENSIONS = {".md"} | CONVERTIBLE_EXTENSIONS


# ── 摄取清单 (续跑/跳过) ─────────────────────────────────────────────────

def load_manifest() -> dict:
    """加载摄取清单。键: 源文件绝对路径, 值: {hash, slug, ingested_at}。"""
    if not MANIFEST_FILE.exists():
        return {}
    try:
        return json.loads(read_file(MANIFEST_FILE))
    except (json.JSONDecodeError, ValueError):
        return {}


def save_manifest(manifest: dict):
    """保存摄取清单为格式化的 JSON。"""
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_file(MANIFEST_FILE, json.dumps(manifest, ensure_ascii=False, indent=2))


def source_hash(path: Path) -> str:
    """计算文件的 SHA-256 前 16 位（用于检测内容变更）。"""
    return sha256(path.read_bytes().hex(), truncate=16)


def is_ingested(source_path: str, force: bool = False) -> bool:
    """检查源文件是否已在清单中且内容未变。"""
    if force:
        return False
    path = Path(source_path).resolve()
    manifest = load_manifest()
    key = str(path)
    if key not in manifest:
        return False
    try:
        current_hash = source_hash(path)
    except OSError:
        return False
    return manifest[key].get("hash") == current_hash


def record_ingested(source_path: str, slug: str):
    """将成功摄取的源记录到清单。"""
    path = Path(source_path).resolve()
    manifest = load_manifest()
    try:
        h = source_hash(path)
    except OSError:
        h = "unknown"
    manifest[str(path)] = {
        "hash": h,
        "slug": slug,
        "ingested_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_manifest(manifest)


def show_manifest_status():
    """打印所有已摄取源的状态。"""
    manifest = load_manifest()
    if not manifest:
        print("摄取清单为空 — 还没有成功摄取的文档。")
        return
    print(f"摄取清单: {len(manifest)} 个文档\n")
    for i, (path_str, entry) in enumerate(sorted(manifest.items()), 1):
        name = Path(path_str).name
        h = entry.get("hash", "???")
        slug = entry.get("slug", "???")
        ts = entry.get("ingested_at", "???")
        print(f"  [{i}] {name}")
        print(f"       slug: {slug}  hash: {h}  at: {ts}")
    in_sync = 0
    stale = 0
    for path_str, entry in manifest.items():
        try:
            if entry.get("hash") == source_hash(Path(path_str)):
                in_sync += 1
            else:
                stale += 1
        except OSError:
            stale += 1
    print(f"\n  ✓ 同步: {in_sync}   ⚠ 源文件已变更(需 --force 重摄): {stale}")


# ── 文件 I/O ──────────────────────────────────────────────────────────────

def read_file(path: Path) -> str:
    """读文件（UTF-8）。不存在返回空串。"""
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_file(path: Path, content: str):
    """写文件（UTF-8），自动创建父目录。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    try:
        print(f"  wrote: {path.relative_to(REPO_ROOT)}")
    except ValueError:
        print(f"  wrote: {path}")


def sha256(text: str, truncate: int = 0) -> str:
    """text 的 SHA-256 十六进制摘要，可截断到 truncate 位。"""
    h = hashlib.sha256(text.encode()).hexdigest()
    return h[:truncate] if truncate else h


def extract_wikilinks(content: str, unique: bool = False) -> list[str]:
    """抽取所有 [[WikiLink]] 目标。"""
    links = re.findall(r"\[\[([^\]]+)\]\]", content)
    return list(set(links)) if unique else links


_META_EXCLUDE = {"index.md", "log.md", "lint-report.md"}


def all_wiki_pages(extra_exclude: set[str] | None = None) -> list[Path]:
    """返回 wiki/ 下所有 .md（排除元数据文件）。"""
    exclude = _META_EXCLUDE | (extra_exclude or set())
    if not WIKI_DIR.exists():
        return []
    return [p for p in WIKI_DIR.rglob("*.md") if p.name not in exclude]


def append_log(entry: str):
    """把日志条目追加到 wiki/log.md（保持 append-only）。"""
    entry_text = entry.strip()
    header = (
        "# Wiki Log\n\n"
        "> Records important additions, revisions, and clarifications in the "
        "project knowledge layer. Maintained in append-only mode for agent and "
        "human traceability."
    )
    if not LOG_FILE.exists():
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text(header + "\n\n" + entry_text + "\n", encoding="utf-8")
        return
    existing = read_file(LOG_FILE).rstrip() or header
    LOG_FILE.write_text(existing + "\n\n" + entry_text + "\n", encoding="utf-8")


# ── 摄取逻辑（沿用 ingest.py）─────────────────────────────────────────────

def clip(text: str, limit: int = 260) -> str:
    """按词边界截断。"""
    if len(text) <= limit:
        return text
    clipped = text[: limit - 3].rsplit(" ", 1)[0].rstrip()
    return clipped + "..."


def build_wiki_context() -> str:
    parts = []
    if INDEX_FILE.exists():
        parts.append(f"## wiki/index.md\n{read_file(INDEX_FILE)}")
    if OVERVIEW_FILE.exists():
        parts.append(f"## wiki/overview.md\n{read_file(OVERVIEW_FILE)}")
    # 带上最近几个源页，供矛盾检查
    sources_dir = WIKI_DIR / "sources"
    if sources_dir.exists():
        recent = sorted(sources_dir.glob("*.md"),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:5]
        for p in recent:
            parts.append(f"## {p.relative_to(REPO_ROOT)}\n{p.read_text(encoding='utf-8')}")
    return "\n\n---\n\n".join(parts)


def parse_json_from_response(text: str) -> dict:
    # 去掉可能的 markdown 代码围栏
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())
    # 取最外层 JSON 对象
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        raise ValueError("响应中未找到 JSON 对象")
    return json.loads(match.group())


def update_index(new_entry: str, section: str = "Sources"):
    content = read_file(INDEX_FILE)
    if not content:
        content = ("# Wiki Index\n\n## Overview\n- [Overview](overview.md) — living synthesis\n\n"
                   "## Sources\n\n## Entities\n\n## Concepts\n\n## Syntheses\n")
    section_header = f"## {section}"
    if not new_entry:
        # 模型可能漏返回 index_entry，此时只确保 index 文件存在，不追加空条目。
        write_file(INDEX_FILE, content)
        return
    if section_header in content:
        content = content.replace(section_header + "\n",
                                  section_header + "\n" + new_entry + "\n")
    else:
        content += f"\n{section_header}\n{new_entry}\n"
    write_file(INDEX_FILE, content)


def validate_ingest(changed_pages: list[str] | None = None) -> dict:
    """摄取后校验 wiki 完整性：坏链 + 未入 index。"""
    existing_pages = {p.stem.lower() for p in all_wiki_pages()}
    index_content = read_file(INDEX_FILE).lower()

    if changed_pages:
        scan_paths = [WIKI_DIR / p for p in changed_pages if (WIKI_DIR / p).exists()]
    else:
        scan_paths = [p for p in WIKI_DIR.rglob("*.md")
                      if p.name not in ("index.md", "log.md", "lint-report.md")]

    broken_links = []
    for page_path in scan_paths:
        content = read_file(page_path)
        rel = str(page_path.relative_to(WIKI_DIR))
        for link in extract_wikilinks(content):
            link_stem = Path(link).stem.lower() if "/" in link else link.lower()
            if link_stem not in existing_pages:
                broken_links.append((rel, link))

    unindexed = []
    for p in (changed_pages or []):
        page_path = WIKI_DIR / p
        if page_path.exists():
            stem = page_path.stem.lower()
            if stem not in index_content and p not in ("log.md", "overview.md"):
                unindexed.append(p)

    return {"broken_links": broken_links, "unindexed": unindexed}


def convert_to_md(source: Path) -> Path:
    """用 markitdown 把非 markdown 文件转成 .md。"""
    try:
        from markitdown import MarkItDown
    except ImportError:
        print("Error: 未安装 markitdown（转换非 .md 文件需要它）。")
        print("  安装: pip install markitdown")
        sys.exit(1)

    md = MarkItDown(enable_plugins=False)
    try:
        result = md.convert(str(source))
    except Exception as e:
        print(f"Error: 转换 '{source.name}' 失败: {e}")
        sys.exit(1)

    output = source.with_suffix(".md")
    try:
        output.write_text(result.text_content, encoding="utf-8")
    except OSError:
        tmp = Path(tempfile.mkdtemp()) / f"{source.stem}.md"
        tmp.write_text(result.text_content, encoding="utf-8")
        output = tmp

    print(f"  ✓ 已转换 {source.name} → {output.name}")
    return output


def ingest(source_path: str, auto_convert: bool = True, force: bool = False):
    source = Path(source_path)
    if not source.exists():
        print(f"Error: 文件不存在: {source_path}")
        sys.exit(1)

    # ── 续跑检查：如果该源已成功摄取且内容未变，跳过 ──
    if is_ingested(source_path, force=force):
        manifest = load_manifest()
        key = str(source.resolve())
        entry = manifest.get(key, {})
        print(f"  ⏭ 跳过 {source.name} — 已摄取 (slug={entry.get('slug', '?')}, "
              f"hash={entry.get('hash', '?')}, 使用 --force 强制重摄)")
        return

    # 自动转换非 markdown 文件
    converted_path = None
    if source.suffix.lower() != ".md":
        if not auto_convert:
            print(f"  跳过非 .md 文件 (--no-convert): {source.name}")
            return
        if source.suffix.lower() not in CONVERTIBLE_EXTENSIONS:
            print(f"  ⚠️  不支持的格式: {source.suffix} — 跳过 {source.name}")
            print(f"       支持: {', '.join(sorted(ALL_SUPPORTED_EXTENSIONS))}")
            return
        print(f"  正在把 {source.name} 转为 markdown...")
        converted_path = convert_to_md(source)
        source = converted_path

    source_content = source.read_text(encoding="utf-8")
    source_hash = sha256(source_content, truncate=16)
    today = date.today().isoformat()

    print(f"\n摄取: {source.name}  (hash: {source_hash})")

    wiki_context = build_wiki_context()
    schema = read_file(SCHEMA_FILE)

    try:
        source_ref = source.relative_to(REPO_ROOT)
    except ValueError:
        source_ref = source.name

    prompt = f"""You are maintaining an LLM Wiki. Process this source document and integrate its knowledge into the wiki.

Schema and conventions:
{schema}

Current wiki state (index + recent pages):
{wiki_context if wiki_context else "(wiki is empty — this is the first source)"}

New source to ingest (file: {source_ref}):
=== SOURCE START ===
{source_content}
=== SOURCE END ===

Today's date: {today}

Return ONLY a valid JSON object with these fields (no markdown fences, no prose outside the JSON):
{{
  "title": "Human-readable title for this source",
  "slug": "kebab-case-slug-for-filename",
  "source_page": "full markdown content for wiki/sources/<slug>.md — use the source page format from the schema. CRITICAL: Aggressively convert key people, products, concepts and projects into [[Wikilinks]] inline in the text. Omitting [[ ]] for known terms is a failure.",
  "index_entry": "- [Title](sources/slug.md) — one-line summary",
  "overview_update": "full updated content for wiki/overview.md, or null if no update needed",
  "entity_pages": [
    {{"path": "entities/EntityName.md", "content": "full markdown content"}}
  ],
  "concept_pages": [
    {{"path": "concepts/ConceptName.md", "content": "full markdown content"}}
  ],
  "contradictions": ["describe any contradiction with existing wiki content, or empty list"],
  "log_entry": "## [{today}] ingest | <title>\\n\\nAdded source. Key claims: ..."
}}
"""

    print(f"  调用 {DEFAULT_MODEL} (via DeepSeek API)...")
    raw = call_llm(prompt, model=DEFAULT_MODEL, max_tokens=393216)
    try:
        data = parse_json_from_response(raw)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"Error 解析模型响应失败: {e}")
        debug_path = Path(tempfile.gettempdir()) / "getwiki_debug.txt"
        debug_path.write_text(raw, encoding="utf-8")
        print(f"原始响应已保存到 {debug_path}")
        sys.exit(1)

    # 规范化必需字段：模型偶尔会漏字段或返回 null，给出安全兜底。
    title = data.get("title") or source.stem
    slug = data.get("slug") or (re.sub(r"\s+", "-", source.stem).strip("-") or "source")
    source_page = data.get("source_page")
    if not source_page:
        print("Error: 模型未返回 source_page，无法写入源页。")
        debug_path = Path(tempfile.gettempdir()) / "getwiki_debug.txt"
        debug_path.write_text(raw, encoding="utf-8")
        print(f"原始响应已保存到 {debug_path}")
        sys.exit(1)
    index_entry = data.get("index_entry") or f"- [{title}](sources/{slug}.md) — {title}"
    log_entry = data.get("log_entry") or f"## [{today}] ingest | {title}"

    # 写源页
    write_file(WIKI_DIR / "sources" / f"{slug}.md", source_page)

    # 写实体页
    for page in data.get("entity_pages", []):
        write_file(WIKI_DIR / page["path"], page["content"])

    # 写概念页
    for page in data.get("concept_pages", []):
        write_file(WIKI_DIR / page["path"], page["content"])

    # 更新 overview
    if data.get("overview_update"):
        write_file(OVERVIEW_FILE, data["overview_update"])

    # 更新 index
    update_index(index_entry, section="Sources")

    # 追加 log
    append_log(log_entry)

    # 记录到摄取清单（用于续跑/跳过）
    record_ingested(source_path, slug)

    # 矛盾报告
    contradictions = data.get("contradictions", [])
    if contradictions:
        print("\n  ⚠️  检测到矛盾:")
        for c in contradictions:
            print(f"     - {c}")

    # ── 摄取后校验 ──
    created_pages = [f"sources/{slug}.md"]
    for page in data.get("entity_pages", []):
        created_pages.append(page["path"])
    for page in data.get("concept_pages", []):
        created_pages.append(page["path"])
    updated_pages = ["index.md", "log.md"]
    if data.get("overview_update"):
        updated_pages.append("overview.md")

    validation = validate_ingest(created_pages)

    print(f"\n{'='*50}")
    print(f"  ✅ 已摄取: {title}")
    print(f"{'='*50}")
    print(f"  新建 : {len(created_pages)} 页")
    for p in created_pages:
        print(f"           + wiki/{p}")
    print(f"  更新 : {len(updated_pages)} 页")
    for p in updated_pages:
        print(f"           ~ wiki/{p}")
    if contradictions:
        print(f"  警告 : {len(contradictions)} 处矛盾")
    if validation["broken_links"]:
        print(f"  ⚠️  坏链: {len(validation['broken_links'])}")
        for page, link in validation["broken_links"][:10]:
            print(f"           wiki/{page} → [[{link}]]")
        if len(validation["broken_links"]) > 10:
            print(f"           ... 还有 {len(validation['broken_links']) - 10} 处")
    if validation["unindexed"]:
        print(f"  ⚠️  未入 index.md: {len(validation['unindexed'])}")
        for p in validation["unindexed"][:10]:
            print(f"           wiki/{p}")
    if not validation["broken_links"] and not validation["unindexed"]:
        print("  ✓ 校验通过 — 无坏链，所有页均已入 index")
    print()


def _run_validate_only():
    print("运行 wiki 校验（不摄取）...\n")
    result = validate_ingest()
    if result["broken_links"]:
        print(f"坏链: {len(result['broken_links'])}")
        for page, link in result["broken_links"][:20]:
            print(f"  wiki/{page} → [[{link}]]")
        if len(result["broken_links"]) > 20:
            print(f"  ... 还有 {len(result['broken_links']) - 20} 处")
    else:
        print("未发现坏链。")
    print()
    index_content = read_file(INDEX_FILE).lower()
    unindexed_all = []
    for p in WIKI_DIR.rglob("*.md"):
        if p.name in ("index.md", "log.md", "lint-report.md", "overview.md"):
            continue
        if p.stem.lower() not in index_content:
            unindexed_all.append(str(p.relative_to(WIKI_DIR)))
    if unindexed_all:
        print(f"未入 index.md 的页: {len(unindexed_all)}")
        for up in unindexed_all[:20]:
            print(f"  wiki/{up}")
    else:
        print("所有页均已入 index。")


if __name__ == "__main__":
    # --status: 显示摄取清单状态
    if "--status" in sys.argv:
        show_manifest_status()
        sys.exit(0)

    # --validate-only
    if len(sys.argv) == 2 and sys.argv[1] == "--validate-only":
        _run_validate_only()
        sys.exit(0)

    force = "--force" in sys.argv
    no_convert = "--no-convert" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if not args:
        print("用法: python baseAtoml/getwiki.py <path-to-source> [path2 ...] [dir1 ...]")
        print("      python baseAtoml/getwiki.py --validate-only")
        print("      python baseAtoml/getwiki.py --status         # 查看摄取清单")
        print("      python baseAtoml/getwiki.py --force           # 强制重摄所有文件(忽略清单)")
        print("      python baseAtoml/getwiki.py --no-convert       # 跳过非 .md 文件的自动转换")
        print(f"\n支持格式: {', '.join(sorted(ALL_SUPPORTED_EXTENSIONS))}")
        sys.exit(1)

    paths_to_process = []
    for arg in args:
        p = Path(arg)
        if p.is_file():
            if p.suffix.lower() in ALL_SUPPORTED_EXTENSIONS:
                paths_to_process.append(p)
            else:
                print(f"  ⚠️  跳过不支持的格式: {p.name} ({p.suffix})")
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and f.suffix.lower() in ALL_SUPPORTED_EXTENSIONS:
                    paths_to_process.append(f)
        else:
            import glob
            for f in glob.glob(arg, recursive=True):
                g_p = Path(f)
                if g_p.is_file() and g_p.suffix.lower() in ALL_SUPPORTED_EXTENSIONS:
                    paths_to_process.append(g_p)

    # 去重保序
    unique_paths = []
    seen = set()
    for p in paths_to_process:
        abs_p = p.resolve()
        if abs_p not in seen:
            seen.add(abs_p)
            unique_paths.append(p)

    if not unique_paths:
        print("Error: 没有找到可摄取的受支持文件。")
        print(f"支持格式: {', '.join(sorted(ALL_SUPPORTED_EXTENSIONS))}")
        sys.exit(1)

    if len(unique_paths) > 1:
        print(f"批量模式: 找到 {len(unique_paths)} 个文件待摄取。")

    for p in unique_paths:
        ingest(str(p), auto_convert=not no_convert, force=force)
