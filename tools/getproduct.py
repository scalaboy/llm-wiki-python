"""
getproduct.py

补齐 agentBaseinfo 表中 agentTags 字段。
根据 agentDescription 的内容，用大模型提取产品/服务标签，
格式为 JSON 数组：["产品1", "产品2", "产品3"]

用法：
    python tools/getproduct.py              # 全量
    python tools/getproduct.py --test       # 只跑 10 条
    python tools/getproduct.py --fix-skipped  # 修复"信息不足"的公司（根据公司名推断）
"""

import json
import re
import sys
from pathlib import Path

import openai
import pymysql

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_MYSQL_PATH = BASE_DIR / "env" / "mysql"
ENV_LLM_PATH = BASE_DIR / "env" / "llm"

BATCH_SIZE = 30  # 每批发送给大模型的企业数量


# ── LLM ────────────────────────────────────────────────────────────

def _read_llm_config() -> dict[str, str]:
    cfg: dict[str, str] = {}
    for line in ENV_LLM_PATH.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def create_llm() -> openai.OpenAI:
    cfg = _read_llm_config()
    return openai.OpenAI(
        api_key=cfg["deepseek_key"],
        base_url=cfg["deepseek_url"] + "/v1",
        timeout=120.0,
        max_retries=2,
    )


# ── 数据库 ─────────────────────────────────────────────────────────

def load_mysql_config() -> dict:
    config = {}
    with open(ENV_MYSQL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip()
    return config


def get_connection():
    cfg = load_mysql_config()
    return pymysql.connect(
        host=cfg["MYSQL_HOST"],
        port=int(cfg.get("MYSQL_PORT", 3306)),
        user=cfg["MYSQL_USER"],
        password=cfg["MYSQL_PASSWORD"],
        database=cfg["MYSQL_DB"],
        charset="utf8mb4",
    )


# ── 大模型生成产品标签 ──────────────────────────────────────────────

SYSTEM_PROMPT = (
    "你是一个企业产品分析专家。请根据企业的业务描述，提取该企业的核心产品/服务标签。\n\n"
    "要求：\n"
    "  1. 每个标签 2-6 个字，简洁精准\n"
    "  2. 标签覆盖企业的主要业务方向，3-8 个为宜\n"
    "  3. 不要出现公司名、地名等专有名词\n"
    "  4. 标签应为名词性短语，如\"供应链管理\"\"软件开发\"\"冷链物流\"\n"
    "  5. 按重要性从高到低排列\n"
    "  6. 政府机关/事业单位：提取其职能标签，如\"社会救助\"\"基础教育\"\n\n"
    "请返回纯 JSON 对象，key 是企业名称，value 是标签数组。\n"
    "例如：{\"某某科技有限公司\": [\"软件开发\", \"系统集成\", \"技术服务\"]}\n"
    "必须覆盖全部输入条目，不要遗漏任何 key。"
)


def generate_tags(llm, companies: list[dict]) -> dict[str, list[str]]:
    """
    批量调用大模型生成产品标签。
    companies: [{"name": "公司名", "desc": "业务描述"}, ...]
    返回 dict: 公司名 → 标签列表
    """
    entries = []
    for c in companies:
        entries.append(f"{c['name']} | {c['desc'][:200]}")
    entries_text = "\n".join(entries)

    user_prompt = (
        f"请为以下企业提取产品/服务标签：\n\n{entries_text}\n\n"
        "返回 JSON，key=企业名称，value=标签数组。"
    )

    resp = llm.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
    raw = resp.choices[0].message.content or ""

    # 解析 JSON
    json_str = raw.strip()
    m = re.search(r"\{.*\}", json_str, re.DOTALL)
    if m:
        json_str = m.group(0)

    return json.loads(json_str)


# ── 公司名提取 ─────────────────────────────────────────────────────

def extract_company_name(agent_name: str) -> str:
    return agent_name.split(".门户经纪人.")[0] if ".门户经纪人." in agent_name else agent_name


def extract_biz_desc(agent_description: str) -> str:
    """从 agentDescription 中提取纯业务描述（去掉前缀"公司名的门户经纪人,"）。"""
    if "," in agent_description:
        parts = agent_description.split(",", 1)
        return parts[1].strip() if len(parts) > 1 else ""
    return agent_description


# ── 跳过记录修复：根据公司名推断业务描述和标签 ─────────────────────

FIX_SYSTEM_PROMPT = (
    "你是一个中国企业信息专家。请根据企业名称推断其主营业务和产品标签。\n\n"
    "要求：\n"
    "  1. desc: 一段 30-80 字的业务描述，陈述句，客观精炼\n"
    "  2. tags: 3-5 个产品/服务标签，每个 2-6 字，名词性短语\n"
    "  3. 根据公司名中的关键词推断（如\"物流\"→运输配送、\"化工\"→化工产品、\"农机\"→农业机械）\n"
    "  4. 不要出现公司名、地名等专有名词\n\n"
    "请返回纯 JSON 对象，格式：\n"
    "{\"公司名\": {\"desc\": \"业务描述\", \"tags\": [\"标签1\", \"标签2\"]}}\n"
    "必须覆盖全部输入条目，不要遗漏任何 key。"
)


def fix_skipped(company_names: list[str] | None = None):
    """
    修复被跳过的公司：根据公司名通过大模型推断 agentDescription 和 agentTags。
    如果不传 company_names，则自动查询 agentTags 为空 + agentDescription 含"信息不足"的记录。

    用法：
        python tools/getproduct.py --fix-skipped
    """
    conn = get_connection()
    llm = create_llm()

    try:
        # 1. 确定要修复的公司列表
        if company_names:
            placeholders = ", ".join(["%s"] * len(company_names))
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id, agentName, agentDescription FROM agentBaseinfo "
                    f"WHERE SUBSTRING_INDEX(agentName, '.门户经纪人.', 1) IN ({placeholders})",
                    company_names,
                )
                rows = cur.fetchall()
        else:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, agentName, agentDescription FROM agentBaseinfo "
                    "WHERE (agentTags = '' OR agentTags IS NULL) "
                    "AND (agentDescription LIKE '%信息不足%' "
                    "  OR agentDescription LIKE '%未找到%' "
                    "  OR agentDescription LIKE '%无法找到%' "
                    "  OR agentDescription LIKE '%无法确认%' "
                    "  OR agentDescription LIKE '%暂无%' "
                    "  OR agentDescription LIKE '%未检索%' "
                    "  OR agentDescription LIKE '%信息未提及%') "
                    "ORDER BY id"
                )
                rows = cur.fetchall()

        if not rows:
            print("没有需要修复的记录。")
            return

        print(f"待修复: {len(rows)} 条\n")

        # 2. 提取公司名
        records = []
        for rec_id, agent_name, _agent_desc in rows:
            records.append({
                "id": rec_id,
                "name": extract_company_name(agent_name),
            })

        # 3. 分批调大模型
        all_results: dict[str, dict] = {}

        batches = [records[i:i + BATCH_SIZE] for i in range(0, len(records), BATCH_SIZE)]
        print(f"共 {len(batches)} 个批次，每批 {BATCH_SIZE} 条\n")

        for batch_idx, batch in enumerate(batches, 1):
            company_list = [r["name"] for r in batch]
            entries_text = "\n".join(company_list)
            print(f"batch {batch_idx}/{len(batches)} ({len(batch)} 条)...", end=" ", flush=True)

            try:
                resp = llm.chat.completions.create(
                    model="deepseek-chat",
                    messages=[
                        {"role": "system", "content": FIX_SYSTEM_PROMPT},
                        {"role": "user", "content": f"请为以下企业推断业务描述和标签：\n\n{entries_text}\n\n返回 JSON。"},
                    ],
                    temperature=0.3,
                )
                raw = resp.choices[0].message.content or ""

                json_str = raw.strip()
                m = re.search(r"\{.*\}", json_str, re.DOTALL)
                if m:
                    json_str = m.group(0)

                result = json.loads(json_str)
                all_results.update(result)
                print(f"OK (累计 {len(all_results)})")
            except Exception as e:
                print(f"FAIL: {e}")

        # 4. 预览 & 更新
        print(f"\n{'='*60}")
        print("预览 & 更新")
        print(f"{'='*60}\n")

        updated = 0
        skipped = 0

        with conn.cursor() as cur:
            for rec in records:
                info = all_results.get(rec["name"])
                if not info or not info.get("desc") or not info.get("tags"):
                    print(f"  SKIP (LLM未返回): {rec['name']}")
                    skipped += 1
                    continue

                new_desc = f"{rec['name']}的门户经纪人,{info['desc']}"
                new_tags = json.dumps(info["tags"], ensure_ascii=False)

                print(f"---  id={rec['id']}")
                print(f"  company: {rec['name']}")
                print(f"  desc:    {info['desc']}")
                print(f"  tags:    {new_tags}")

                cur.execute(
                    "UPDATE agentBaseinfo SET agentDescription = %s, agentTags = %s WHERE id = %s",
                    (new_desc, new_tags, rec["id"]),
                )
                updated += 1
                print()

        conn.commit()
        print(f"[完成] 更新 {updated} 条，跳过 {skipped} 条")

    finally:
        conn.close()


# ── 主流程 ─────────────────────────────────────────────────────────

def main():
    test_mode = "--test" in sys.argv
    conn = get_connection()
    limit = 10 if test_mode else None

    try:
        # 1. 查询 agentTags 为空的记录
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, agentName, agentDescription FROM agentBaseinfo "
                "WHERE (agentTags = '' OR agentTags IS NULL) "
                "AND agentDescription != '' "
                "ORDER BY id "
                + (f"LIMIT {limit}" if limit else "")
            )
            rows = cur.fetchall()

        mode_label = "测试模式 (10条)" if test_mode else "全量模式"
        print(f"[{mode_label}] agentTags 为空: {len(rows)} 条待处理\n")

        if not rows:
            print("没有需要处理的记录。")
            return

        # 2. 提取公司名和业务描述
        records: list[dict] = []
        for rec_id, agent_name, agent_desc in rows:
            records.append({
                "id": rec_id,
                "name": extract_company_name(agent_name),
                "desc": extract_biz_desc(agent_desc),
            })

        # 3. 分批调大模型
        llm = create_llm()
        all_tags: dict[str, list[str]] = {}

        batches = [records[i:i + BATCH_SIZE] for i in range(0, len(records), BATCH_SIZE)]
        print(f"共 {len(batches)} 个批次，每批 {BATCH_SIZE} 条\n")

        for batch_idx, batch in enumerate(batches, 1):
            print(f"batch {batch_idx}/{len(batches)} ({len(batch)} 条)...", end=" ", flush=True)
            try:
                companies = [{"name": r["name"], "desc": r["desc"]} for r in batch]
                result = generate_tags(llm, companies)
                all_tags.update(result)
                print(f"OK (累计 {len(all_tags)})")
            except Exception as e:
                print(f"FAIL: {e}")

        # 4. 预览 & 更新
        print(f"\n{'='*60}")
        print("预览 & 更新")
        print(f"{'='*60}\n")

        updated = 0
        skipped = 0

        with conn.cursor() as cur:
            for rec in records:
                tags = all_tags.get(rec["name"], [])
                if not tags:
                    print(f"  SKIP (无标签): {rec['name']}")
                    skipped += 1
                    continue

                tags_json = json.dumps(tags, ensure_ascii=False)

                if limit:
                    print(f"---  id={rec['id']}")
                    print(f"  company: {rec['name']}")
                    print(f"  desc:    {rec['desc'][:100]}...")
                    print(f"  tags:    {tags_json}")
                    print()

                cur.execute(
                    "UPDATE agentBaseinfo SET agentTags = %s WHERE id = %s",
                    (tags_json, rec["id"]),
                )
                updated += 1

        conn.commit()
        print(f"[完成] 更新 {updated} 条，跳过 {skipped} 条")

    finally:
        conn.close()


if __name__ == "__main__":
    if "--fix-skipped" in sys.argv:
        fix_skipped()
    else:
        main()
