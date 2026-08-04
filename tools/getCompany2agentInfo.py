"""
getCompany2agentInfo.py

从 workflow_test.company_addType 读取数据，
按字段映射插入 workflow_test.agentBaseinfo。

用法：
    python tools/getCompany2agentInfo.py
"""

import json
import re
from pathlib import Path

import openai
import pymysql

# ====== 测试开关：设为 None 跑全量，设为 10 只跑前 10 条 ======
TEST_LIMIT = None  # None = 全量，设为数字则只跑前 N 条

# 项目根目录（tools 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_MYSQL_PATH = BASE_DIR / "env" / "mysql"
ENV_LLM_PATH = BASE_DIR / "env" / "llm"


def _read_llm_config() -> dict[str, str]:
    """读取 env/llm 配置。"""
    cfg: dict[str, str] = {}
    for line in ENV_LLM_PATH.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def create_llm() -> openai.OpenAI:
    """用 DeepSeek 官方 API 调用。"""
    cfg = _read_llm_config()
    return openai.OpenAI(
        api_key=cfg["deepseek_key"],
        base_url=cfg["deepseek_url"] + "/v1",
        timeout=120.0,
        max_retries=2,
    )


# -------------------------------------------
#  数据库连接（参考 interface.py）
# -------------------------------------------


def load_mysql_config(path: Path = ENV_MYSQL_PATH) -> dict:
    """从 env/mysql 读取 KEY=VALUE 配置，忽略注释和空行。"""
    config = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
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


# -------------------------------------------
#  enterpriseScale 大模型判断
# -------------------------------------------

ENTERPRISE_SCALE_BATCH = 200  # 每批发送给大模型的企业数量（避免超上下文）


def scaleTrans(conn) -> dict[str, str]:
    """
    使用大模型判断企业规模，输入 name + main_business，
    返回 dict: name → enterpriseScale 编码（01/02/03/04/99）
    """
    # 1. 取所有 unique (name, main_business)（测试模式只取前 N 条）
    limit_clause = f"LIMIT {TEST_LIMIT}" if TEST_LIMIT else ""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT name, main_business FROM company_addType "
            f"WHERE name IS NOT NULL AND name != '' {limit_clause}"
        )
        rows = cur.fetchall()
    total = len(rows)
    print(f"  unique enterprises: {total}")

    system_prompt = (
        "你是一个企业规模分类专家。请根据企业名称和主营业务描述，判断企业规模。\n\n"
        "编码表（来自 PDF 企业规模编码表）：\n"
        "  01 - 大型\n"
        "  02 - 中型\n"
        "  03 - 小型\n"
        "  04 - 微型\n"
        "  99 - 其他\n\n"
        "判断标准：\n"
        "  ★ 判为 99（其他/非企业）的典型特征：\n"
        "    政府机关、事业单位（学校、医院、研究所、疾控中心、气象局等）、\n"
        "    协会/学会/商会/基金会、村委会/居委会/街道办事处、\n"
        "    民政局/财政局/公安局等行政单位、部队/军队相关单位\n"
        "  ★ 大型（01）：央企、国企集团、上市公司/子公司、知名跨国企业、\n"
        "    全国性龙头企业、注册资本或年营收明显在 4 亿以上\n"
        "  ★ 中型（02）：省级/市级知名企业、员工 300-1000 人规模、\n"
        "    营收 2000 万-4 亿区间\n"
        "  ★ 小型（03）：地方性企业、员工 20-300 人、营收 300 万-2000 万\n"
        "  ★ 微型（04）：个体户/工作室/初创企业、员工 < 20 人、营收 < 300 万\n\n"
        "请返回纯 JSON 对象，key 是企业名称，value 是两位编码字符串（01/02/03/04/99）。\n"
        "不要包含任何解释，只返回 JSON。"
    )

    result: dict[str, str] = {}
    llm = create_llm()

    batches = [rows[i:i + ENTERPRISE_SCALE_BATCH] for i in range(0, total, ENTERPRISE_SCALE_BATCH)]
    print(f"  共 {len(batches)} 个批次，每批 {ENTERPRISE_SCALE_BATCH} 条")

    for batch_idx, batch in enumerate(batches, 1):
        # 构建紧凑格式的企业列表
        entries = []
        for name, biz in batch:
            biz_short = (biz or "")[:150].replace("\n", " ")
            entries.append(f"{name} | {biz_short}")
        entries_text = "\n".join(entries)

        print(f"  batch {batch_idx}/{len(batches)} ({len(batch)} 条)...", end=" ", flush=True)

        try:
            resp = llm.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"请判断以下企业的规模编码：\n\n{entries_text}\n\n返回 JSON。"},
                ],
                temperature=0.0,
            )
            raw = resp.choices[0].message.content or ""

            # 解析 JSON
            json_str = raw.strip()
            m = re.search(r"\{.*\}", json_str, re.DOTALL)
            if m:
                json_str = m.group(0)

            batch_result = json.loads(json_str)
            for k, v in batch_result.items():
                v_str = str(v).strip().zfill(2)
                if v_str in ("01", "02", "03", "04", "99"):
                    result[k] = v_str
                else:
                    result[k] = "99"

            ok = sum(1 for v in result.values() if v)
            print(f"OK (累计 {ok})")
        except Exception as e:
            print(f"FAIL: {e}")

    matched = sum(1 for v in result.values() if v)
    print(f"  总计匹配: {matched}/{total}")
    return result


# -------------------------------------------
#  location 大模型映射
# -------------------------------------------


def build_location_mapping(conn) -> dict[str, str]:
    """
    使用大模型，综合 name + region + general_strength + main_business
    将每家企业映射到 addressnum 中的行政区划代码。

    返回 dict: name → code（6位字符串，强制不空，兜底 110000）
    """
    # 1. 取所有 unique (name, region, general_strength, main_business)
    limit_clause = f"LIMIT {TEST_LIMIT}" if TEST_LIMIT else ""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT DISTINCT name, region, general_strength, main_business "
            f"FROM company_addType "
            f"WHERE name IS NOT NULL AND name != '' {limit_clause}"
        )
        rows = cur.fetchall()
    total = len(rows)
    print(f"  unique enterprises: {total}")

    # 2. 取 addressnum 全表
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT province_code, province_name, prefecture_code, prefecture_name
            FROM addressnum
            ORDER BY province_code, prefecture_code
        """)
        addr_rows = cur.fetchall()
    print(f"  addressnum rows: {len(addr_rows)}")

    # 3. 构建 addressnum 代码表（每批复用）
    addr_lines = "\n".join(
        f"{pc} {pn} {fpc} {fpn}" for pc, pn, fpc, fpn in addr_rows
    )

    system_prompt = (
        "你是一个中国行政区划专家。请根据企业的名称、region、综合实力和主营业务，"
        "综合判断企业所在地的行政区划代码。\n\n"
        "映射规则（按 PDF 文档约定）：\n"
        "1. 直辖市（北京/上海/天津/重庆）→ 省份代码（前2位+0000，如北京→110000）\n"
        "2. 其他情况 → 地级代码（6位）\n"
        "3. region 可能是省+市+县组合（如\"四川省隆昌市\"），请用知识找到地级市代码\n"
        "4. region 可能是裸县名（如\"清涧县\"），请找到所属地级市\n"
        "5. region=\"中国\" → 默认输出 110000（北京）\n"
        "6. region 是脏数据/乱码 → 根据 name + strength + business 推断所在地\n"
        "   例如：name=\"中国农业银行股份有限公司\" → 北京 110000\n"
        "   例如：name=\"某某县民政局\" → 从县名反查地级市\n"
        "7. **绝对不允许返回空字符串**，如果实在无法判断，兜底返回 110000\n"
        "8. **必须为输入列表中的每一条企业都返回一个条目**，不允许遗漏任何 key\n\n"
        "请返回纯 JSON 对象，key 是企业名称，value 是 6 位代码字符串。\n"
        "必须覆盖全部输入条目，key 与企业名称完全一致，不要包含任何解释，只返回 JSON。"
    )

    # 4. 分批调用大模型（避免超上下文）
    LOCATION_BATCH = 200  # 每批企业数量
    batches = [rows[i:i + LOCATION_BATCH] for i in range(0, total, LOCATION_BATCH)]
    print(f"  共 {len(batches)} 个批次，每批 {LOCATION_BATCH} 条")

    llm = create_llm()
    mapping: dict[str, str] = {}

    for batch_idx, batch in enumerate(batches, 1):
        # 构建该批的企业列表
        entry_lines = []
        for name, region, strength, biz in batch:
            region = (region or "").replace("\n", " ")
            strength = (strength or "")[:80].replace("\n", " ")
            biz = (biz or "")[:80].replace("\n", " ")
            entry_lines.append(f"{name} | region={region} | strength={strength} | business={biz}")
        entries_text = "\n".join(entry_lines)

        user_prompt = (
            "以下是 addressnum 行政区划代码表（省份代码 省份名称 地级代码 地级名称）：\n"
            f"{addr_lines}\n\n"
            "以下是需要映射的企业列表（格式：名称 | region=xx | strength=xx | business=xx）：\n"
            f"{entries_text}\n\n"
            "请返回 JSON 映射，key=企业名称，value=6位代码。绝对不要有空值。"
        )

        print(f"  batch {batch_idx}/{len(batches)} ({len(batch)} 条)...", end=" ", flush=True)

        try:
            resp = llm.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
            )
            raw = resp.choices[0].message.content or ""

            # 解析 JSON
            json_str = raw.strip()
            m = re.search(r"\{.*\}", json_str, re.DOTALL)
            if m:
                json_str = m.group(0)

            batch_result = json.loads(json_str)
            for k, v in batch_result.items():
                mapping[k] = v

            print(f"OK (累计 {len(mapping)})")
        except Exception as e:
            print(f"FAIL: {e}")

    # 5. 清洗：强制非空，兜底 110000；确保每个输入 name 都有值
    all_names = [r[0] for r in rows]
    clean: dict[str, str] = {}
    empty_count = 0
    missing_count = 0

    for name in all_names:
        v = mapping.get(name)
        if v is not None:
            v_str = str(v).strip()
            if re.match(r"^\d{6}$", v_str):
                clean[name] = v_str
            else:
                clean[name] = "110000"
                empty_count += 1
        else:
            clean[name] = "110000"
            missing_count += 1

    real_matched = sum(1 for v in clean.values() if v != "110000")
    if missing_count:
        print(f"  {missing_count} 条 LLM 未返回，兜底 110000")
    if empty_count:
        print(f"  {empty_count} 条 LLM 返回空值，兜底 110000")
    print(f"  有效匹配: {real_matched}/{len(clean)}（兜底: {missing_count + empty_count}）")
    return clean


# -------------------------------------------
#  字段映射 & 转换函数
# -------------------------------------------

# 每条字段规则有四种形式：
#   {"target": "字段名", "fixed": "固定值"}                        → 固定值
#   {"target": "字段名", "source": "源字段"}                        → 直接映射源字段
#   {"target": "字段名", "source": "源字段", "fmt": "..."}          → 格式化，{val} 占位
#   {"target": "字段名", "source": "源字段", "map": "映射表名"}     → 用外部映射表做值查找
#
# 按 INSERT 顺序排列，后面复杂的字段逐步追加。
FIELD_RULES: list[dict] = [
    {"target": "agentName",    "source": "name", "fmt": "{val}.门户经纪人.万联摩尔.企业智联.中国"},
    {"target": "agentPlatform","fixed": "万联摩尔"},
    {"target": "agentVersion", "fixed": "v1.1.0"},
    {"target": "agentDescription", "source": "name", "fmt": "{name}门户经纪人。{name}{main_business}"},
    {"target": "protocolSupport", "fixed": "ANP"},
    {"target": "enterpriseName", "source": "name"},
    {"target": "agentType", "fixed": "05"},
    {"target": "accessInterface", "source": "external_company_id", "fmt": "https://m.wanmol.com/pages/internetPortal/chat/index?companyId={val}"},
    {"target": "location", "source": "name", "map": "location"},
    {"target": "agentTags", "source": "creator_id"},
    {"target": "enterpriseScale", "source": "name", "map": "scale"},
]


def build_agent_values(source_row: dict, maps: dict[str, dict[str, str]] | None = None) -> list:
    """
    根据 FIELD_RULES 将一条源表记录转换为目标表的一行值。

    Args:
        source_row: 源表行，dict 格式 {列名: 值}
        maps: 外部映射表，key 对应 FIELD_RULES 中的 "map" 字段名

    Returns:
        与 FIELD_RULES 顺序一致的值列表，可直接用于 cursor.execute()
    """
    if maps is None:
        maps = {}

    values = []
    for rule in FIELD_RULES:
        if "fixed" in rule:
            values.append(rule["fixed"])
        elif "source" in rule:
            raw = source_row.get(rule["source"], "")
            # 如果有 "map" 配置，使用外部映射表做值替换
            if "map" in rule and rule["map"] in maps:
                lookup = maps[rule["map"]]
                raw = lookup.get(str(raw) if raw else "", "")
            elif "fmt" in rule:
                # 支持 {col_name} 引用 source_row 中任意字段
                fmt = rule["fmt"]
                # 先替换 {val}（兼容旧写法）
                fmt = fmt.replace("{val}", str(raw) if raw else "")
                # 再替换 {col_name} 形式的字段引用
                for col, val2 in source_row.items():
                    fmt = fmt.replace("{" + col + "}", str(val2) if val2 else "")
                raw = fmt
            values.append(raw)
        else:
            values.append("")  # 未配置的字段填空串
    return values


# -------------------------------------------
#  主流程
# -------------------------------------------


def read_source(conn, limit=None):
    """读取源表 company_addType。limit=None 表示全量，否则限制 N 条。"""
    with conn.cursor() as cursor:
        if limit is not None:
            cursor.execute(f"SELECT * FROM company_addType LIMIT {limit}")
        else:
            cursor.execute("SELECT * FROM company_addType")
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
    return columns, rows


def build_insert_sql(rules: list) -> str:
    """根据 FIELD_RULES 生成 INSERT SQL 模板。"""
    if not rules:
        return ""
    target_cols = [r["target"] for r in rules]
    placeholders = ", ".join(["%s"] * len(target_cols))
    return (
        f"INSERT INTO agentBaseinfo ({', '.join(target_cols)}) "
        f"VALUES ({placeholders})"
    )


def main():
    conn = get_connection()
    try:
        if TEST_LIMIT:
            print(f"[测试模式] 只处理前 {TEST_LIMIT} 条\n")
        else:
            print(f"[全量模式]\n")

        # 0. 预计算 location 映射（调用大模型一次）
        print("=" * 60)
        print("0. 构建 location 映射（大模型）")
        print("=" * 60)
        location_map = build_location_mapping(conn)

        # 0b. 预计算 enterpriseScale 映射（调用大模型批量判断）
        print("\n" + "=" * 60)
        print("0b. 构建 enterpriseScale 映射（大模型）")
        print("=" * 60)
        scale_map = scaleTrans(conn)

        maps = {"location": location_map, "scale": scale_map}

        # 1. 读取源数据
        print("\n" + "=" * 60)
        if TEST_LIMIT:
            print(f"1. 读取 company_addType 前 {TEST_LIMIT} 条")
        else:
            print("1. 读取 company_addType（全量）")
        print("=" * 60)

        columns, rows = read_source(conn, limit=TEST_LIMIT)
        print(f"源表字段 ({len(columns)}): {columns}")
        print(f"共读取 {len(rows)} 条\n")

        # 2. 预览转换结果
        print("=" * 60)
        print("2. 字段映射 & 转换结果预览")
        print("=" * 60)
        for rule in FIELD_RULES:
            if "fixed" in rule:
                print(f"  agentBaseinfo.{rule['target']}  ←  固定值: '{rule['fixed']}'")
            elif "source" in rule:
                desc = f"源字段 {rule['source']}"
                if "map" in rule:
                    desc += f"，大模型映射 ({rule['map']})"
                elif "fmt" in rule:
                    desc += f"，格式化: {rule['fmt']}"
                print(f"  agentBaseinfo.{rule['target']}  ←  {desc}")
            else:
                print(f"  agentBaseinfo.{rule['target']}  ←  (未配置)")
        print()

        for i, row in enumerate(rows, 1):
            source_dict = dict(zip(columns, row))
            target_values = build_agent_values(source_dict, maps=maps)
            print(f"--- 第 {i} 条 (源id={source_dict['id']}, name={source_dict['name']}, region={source_dict.get('region','')}) ---")
            for rule, val in zip(FIELD_RULES, target_values):
                val_str = str(val) if val else "(空)"
                if len(val_str) > 100:
                    val_str = val_str[:100] + "..."
                print(f"  {rule['target']}: {val_str}")
            print()

        # 3. 执行 INSERT
        insert_sql = build_insert_sql(FIELD_RULES)
        print(f"INSERT SQL:\n  {insert_sql}")

        with conn.cursor() as cursor:
            inserted = 0
            for row in rows:
                source_dict = dict(zip(columns, row))
                values = build_agent_values(source_dict, maps=maps)
                # 跳过 agentName 重复的
                cursor.execute("SELECT COUNT(*) FROM agentBaseinfo WHERE agentName = %s", (values[0],))
                if cursor.fetchone()[0] > 0:
                    print(f"  SKIP (agentName 重复): {values[0][:60]}...")
                    continue
                cursor.execute(insert_sql, values)
                inserted += 1
        conn.commit()
        print(f"\n[OK] 插入 {inserted} 条到 agentBaseinfo")
        print("\n(其他未插入的条目因 agentName 重复已跳过)")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
