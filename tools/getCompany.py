"""
getCompany.py

连接 env/mysql 中的生产环境数据库，查询 company_microsite 表前 10 条。
用法：
    python tools/getCompany.py
"""

from pathlib import Path

import pymysql

# 项目根目录（tools 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_MYSQL_PATH = BASE_DIR / "env" / "mysql"


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


def get_prod_connection():
    """使用 env/mysql 中 prod_* 前缀的配置连接生产数据库。"""
    cfg = load_mysql_config()
    return pymysql.connect(
        host=cfg["prod_HOST"],
        port=int(cfg.get("prod_PORT", 3306)),
        user=cfg["prod_USER"],
        password=cfg["prod_PASSWORD"],
        database=cfg["prod_DB"],
        charset="utf8mb4",
    )


def main():
    conn = get_prod_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM company_microsite LIMIT 10")
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]

        print(f"company_microsite 前 {len(rows)} 条\n")
        print(" | ".join(columns))
        print("-" * 80)

        for row in rows:
            print(" | ".join(str(val) for val in row))

    finally:
        conn.close()


if __name__ == "__main__":
    main()
