"""
toCnnicfill.py

统计 agentBaseinfo 表中 agentDescription 字段仅包含
"{公司名}的门户经纪人"（逗号后无业务描述）的记录条数。

用法：
    python tools/toCnnicfill.py
"""

from pathlib import Path

import pymysql

# 项目根目录（tools 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_MYSQL_PATH = BASE_DIR / "env" / "mysql"


def load_mysql_config() -> dict:
    """从 env/mysql 读取配置，忽略注释和空行。"""
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


def main():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # agentDescription 格式为 "{公司名}的门户经纪人,{业务描述}"
            # 仅统计逗号后无实质内容（即只有公司名+门户经纪人）的记录
            cur.execute(
                "SELECT COUNT(*) FROM agentBaseinfo "
                "WHERE agentDescription LIKE '%的门户经纪人,%' "
                "AND TRIM(SUBSTRING_INDEX(agentDescription, ',', -1)) = ''"
            )
            count = cur.fetchone()[0]

        print(f"agentDescription 仅含'公司名+门户经纪人'的记录数: {count}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
