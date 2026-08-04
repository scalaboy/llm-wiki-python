"""
interface.py

解析 assets 目录下 PDF 文件中的国家行政区划代码，入库 addressnum 表。
字段：province_code(省份代码) province_name(省份名称)
      prefecture_code(地级代码) prefecture_name(地级名称)

用法：
    python tools/interface.py
"""

import re
import subprocess
from pathlib import Path

import pymysql

# 项目根目录（tools 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_MYSQL_PATH = BASE_DIR / "env" / "mysql"
PDF_PATH = BASE_DIR / "assets" / "智能体信息接口V1.1.pdf"

# -------------------------------------------
#  数据库连接
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
#  PDF 解析
# -------------------------------------------


def extract_pdf_text(pdf_path: Path) -> str:
    """使用 pdftotext 提取 PDF 文本内容（layout 模式保留表格对齐）。"""
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pdftotext 提取失败: {result.stderr}")
    return result.stdout


def parse_address_data(text: str) -> list[dict]:
    """
    从 PDF 文本中解析「国家行政区划代码」章节。

    PDF 中该章节的布局：
      - 省份代码（如 110000）单独占一行
      - 数据行格式：<省份名称>  <地级代码>  <地级名称>

    由于省份代码行不一定出现在该省第一条数据之前（受 PDF 分栏影响），
    这里采用从地级代码推导省份代码的策略：province_code = 地级代码前2位 + "0000"
    """
    section_start = text.find("国家行政区划代码")
    if section_start == -1:
        raise ValueError("未找到 '国家行政区划代码' 章节")

    section = text[section_start:]
    lines = section.splitlines()

    province_code_map: dict[str, str] = {}  # province_code -> province_name
    rows: list[dict] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 跳过标题和表头
        if "省份代码" in line or "国家行政区划代码" in line:
            continue

        # 独立的 6 位省份代码行，记录一下（用于构建 province_code -> name 映射）
        if re.match(r"^\d{6}$", line):
            continue  # 省份名称从数据行中获取

        # 数据行：省份名称 + 6位地级代码 + 地级名称
        m = re.match(r"^(.+?)(\d{6})\s+(.+)$", line)
        if m:
            province_name = m.group(1).strip()
            prefecture_code = m.group(2)
            prefecture_name = m.group(3).strip()

            # 从地级代码推导省份代码（前 2 位 + "0000"）
            province_code = prefecture_code[:2] + "0000"

            # 记录省份名称
            province_code_map[province_code] = province_name

            rows.append(
                {
                    "province_code": province_code,
                    "province_name": province_name,
                    "prefecture_code": prefecture_code,
                    "prefecture_name": prefecture_name,
                }
            )

    return rows


# -------------------------------------------
#  建表 & 入库
# -------------------------------------------

CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS addressnum (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
        province_code CHAR(6) NOT NULL DEFAULT '' COMMENT '省份代码',
        province_name VARCHAR(100) NOT NULL DEFAULT '' COMMENT '省份名称',
        prefecture_code CHAR(6) NOT NULL DEFAULT '' COMMENT '地级代码',
        prefecture_name VARCHAR(100) NOT NULL DEFAULT '' COMMENT '地级名称',
        PRIMARY KEY (id),
        KEY idx_province_code (province_code),
        KEY idx_prefecture_code (prefecture_code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='国家行政区划代码表';
"""

INSERT_SQL = """
    INSERT INTO addressnum (province_code, province_name, prefecture_code, prefecture_name)
    VALUES (%s, %s, %s, %s)
"""


# -------------------------------------------
#  智能体信息表 & 数据字典（基于 PDF §2.4 record 字段说明）
# -------------------------------------------

# 数据字典：智能体类型编码
AGENT_TYPE_ROWS = [
    ("01", "研发设计"),
    ("02", "生产制造"),
    ("03", "质量检测"),
    ("04", "运行维护"),
    ("05", "经营管理"),
    ("99", "其他"),
]

# 数据字典：企业规模编码表
ENTERPRISE_SCALE_ROWS = [
    ("01", "大型"),
    ("02", "中型"),
    ("03", "小型"),
    ("04", "微型"),
    ("99", "其他"),
]

# 数据字典：中小企业类型编码表
SME_TYPE_ROWS = [
    ("01", "科技和创新型中小企业"),
    ("02", "专精特新中小企业"),
    ("03", "专精特新“小巨人”企业"),
    ("99", "其他中小企业"),
]

CREATE_DICT_TABLES_SQL = """
    CREATE TABLE IF NOT EXISTS agent_type_dict (
        code CHAR(2) NOT NULL COMMENT '智能体类型编码',
        name VARCHAR(50) NOT NULL COMMENT '类型名称',
        PRIMARY KEY (code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='智能体类型编码（数据字典）';

    CREATE TABLE IF NOT EXISTS enterprise_scale_dict (
        code CHAR(2) NOT NULL COMMENT '企业规模编码',
        name VARCHAR(50) NOT NULL COMMENT '规模名称',
        PRIMARY KEY (code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='企业规模编码表（数据字典）';

    CREATE TABLE IF NOT EXISTS sme_type_dict (
        code CHAR(2) NOT NULL COMMENT '中小企业类型编码',
        name VARCHAR(100) NOT NULL COMMENT '类型名称',
        PRIMARY KEY (code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='中小企业类型编码表（数据字典）';
"""

CREATE_AGENT_BASEINFO_SQL = """
    CREATE TABLE IF NOT EXISTS agentBaseinfo (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '智能体ID',
        agentName VARCHAR(253) NOT NULL DEFAULT '' COMMENT '智能体名称',
        agentVersion VARCHAR(20) NOT NULL DEFAULT '' COMMENT '智能体版本号',
        agentPlatform VARCHAR(64) NOT NULL DEFAULT '' COMMENT '所属平台名称',
        agentType CHAR(2) NOT NULL DEFAULT '' COMMENT '智能体类型（关联 agent_type_dict.code）',
        agentTags TEXT COMMENT '标签，多标签逗号分隔',
        agentDescription TEXT COMMENT '智能体功能描述',
        protocolSupport VARCHAR(500) NOT NULL DEFAULT '' COMMENT '支持的协议类型，多协议逗号分隔',
        enterpriseName VARCHAR(200) NOT NULL DEFAULT '' COMMENT '所属企业全称',
        enterpriseScale CHAR(2) NOT NULL DEFAULT '' COMMENT '企业规模（关联 enterprise_scale_dict.code）',
        smeType CHAR(2) NOT NULL DEFAULT '' COMMENT '中小企业类型（关联 sme_type_dict.code）',
        location CHAR(6) NOT NULL DEFAULT '' COMMENT '企业所在地区（行政区划代码）',
        auditStatus VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT '审核状态：PENDING-待审核, APPROVED-已通过, REJECTED-已驳回',
        accessInterface TEXT COMMENT '解析记录（JSON 数组）',
        createTime DATETIME COMMENT '创建时间',
        updateTime DATETIME COMMENT '更新时间',
        PRIMARY KEY (id),
        UNIQUE KEY uk_agentName (agentName),
        KEY idx_agentPlatform (agentPlatform),
        KEY idx_agentType (agentType),
        KEY idx_enterpriseScale (enterpriseScale),
        KEY idx_location (location),
        KEY idx_auditStatus (auditStatus)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='智能体基础信息表';
"""


def setup_agent_baseinfo(conn):
    """创建 agentBaseinfo 表及三个数据字典表，并灌入字典数据。"""
    with conn.cursor() as cursor:
        # 1. 创建数据字典表（多条 DDL，逐条执行）
        for stmt in CREATE_DICT_TABLES_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                cursor.execute(stmt)

        # 2. 灌入字典数据（REPLACE 保证幂等）
        for table, rows in [
            ("agent_type_dict", AGENT_TYPE_ROWS),
            ("enterprise_scale_dict", ENTERPRISE_SCALE_ROWS),
            ("sme_type_dict", SME_TYPE_ROWS),
        ]:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            existing = cursor.fetchone()[0]
            if existing == 0:
                cursor.executemany(
                    f"INSERT INTO {table} (code, name) VALUES (%s, %s)",
                    rows,
                )
                print(f"  [OK] {table} 灌入 {len(rows)} 条字典数据")
            else:
                print(f"  [SKIP] {table} 已有 {existing} 条数据，跳过灌入")

        # 3. 创建 agentBaseinfo 表
        cursor.execute(CREATE_AGENT_BASEINFO_SQL)
        print("[OK] agentBaseinfo 表已就绪")

    conn.commit()


# -------------------------------------------
#  主流程
# -------------------------------------------


def import_address_data(conn):
    """从 PDF 解析国家行政区划代码，写入 addressnum 表。"""
    print("=" * 50)
    print("1. 国家行政区划代码 → addressnum")
    print("=" * 50)

    print(f"提取 PDF: {PDF_PATH}")
    text = extract_pdf_text(PDF_PATH)

    print("解析行政区划数据...")
    rows = parse_address_data(text)
    print(f"  共解析到 {len(rows)} 条记录")

    provinces = sorted(set(r["province_code"] for r in rows))
    print(f"  涵盖 {len(provinces)} 个省份/直辖市/自治区")

    with conn.cursor() as cursor:
        cursor.execute(CREATE_TABLE_SQL)
        print("[OK] addressnum 表已就绪")

        cursor.execute("TRUNCATE TABLE addressnum")
        print("  已清空旧数据")

        batch = [
            (r["province_code"], r["province_name"], r["prefecture_code"], r["prefecture_name"])
            for r in rows
        ]
        cursor.executemany(INSERT_SQL, batch)
        print(f"[OK] 插入 {len(batch)} 条记录")

    conn.commit()

    print("\n--- 前 10 条预览 ---")
    for r in rows[:10]:
        print(
            f"  {r['province_code']}  {r['province_name']}  "
            f"{r['prefecture_code']}  {r['prefecture_name']}"
        )


# -------------------------------------------
#  主流程
# -------------------------------------------


def main():
    """依次执行：行政区划代码入库 → 智能体信息表建表。"""
    conn = get_connection()
    try:
        #import_address_data(conn)

        #print()
        setup_agent_baseinfo(conn)

        print("\n全部完成。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
