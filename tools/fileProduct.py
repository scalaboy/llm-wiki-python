"""
fileProduct.py

将 aira_enterprise_product_info 中匹配 company_addType.name 的
product_name 聚合为 JSON 数组，存入 company_addType.creator_id。

用法：
    python tools/fileProduct.py
"""

import json
from pathlib import Path


import pymysql

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_MYSQL_PATH = BASE_DIR / "env" / "mysql"


def load_mysql_config(path: Path = ENV_MYSQL_PATH) -> dict:
    """从 env/mysql 读取 KEY=VALUE 配置。"""
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


def main():
    conn = get_connection()
    try:
        # 1. 用 JOIN 聚合每个公司的产品名
        print("聚合 aira_enterprise_product_info 产品数据...")
        sql = """
            SELECT c.id, GROUP_CONCAT(DISTINCT p.product_name SEPARATOR '\n') AS products
            FROM company_addType c
            INNER JOIN aira_enterprise_product_info p ON c.name COLLATE utf8mb4_unicode_ci = p.company_name COLLATE utf8mb4_unicode_ci
            GROUP BY c.id
        """
        # 0. 确保 creator_id 列够宽
        with conn.cursor() as cur:
            cur.execute("ALTER TABLE company_addType MODIFY creator_id TEXT COMMENT '产品名JSON数组'")
        conn.commit()
        print("[OK] creator_id 列已改为 TEXT")

        with conn.cursor() as cur:
            # 避免产品名过多被 GROUP_CONCAT 截断
            cur.execute("SET SESSION group_concat_max_len = 1048576")
            cur.execute(sql)
            rows = cur.fetchall()
        print(f"  匹配到 {len(rows)} 家公司有产品数据")

        # 2. 构建 id → JSON 数组 映射
        update_data = []
        for cid, products_str in rows:
            if products_str:
                product_list = [p.strip() for p in products_str.split("\n") if p.strip()]
                # 去重保持顺序
                seen = set()
                unique = []
                for p in product_list:
                    if p not in seen:
                        seen.add(p)
                        unique.append(p)
                update_data.append((json.dumps(unique, ensure_ascii=False), cid))

        # 3. 批量更新 company_addType.creator_id
        print(f"更新 company_addType.creator_id ...")
        update_sql = "UPDATE company_addType SET creator_id = %s WHERE id = %s"
        with conn.cursor() as cur:
            cur.executemany(update_sql, update_data)
        conn.commit()
        print(f"[OK] 更新 {len(update_data)} 条")

        # 4. 预览
        print("\n--- 前 5 条预览 ---")
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, creator_id FROM company_addType "
                "WHERE creator_id != '' LIMIT 5"
            )
            for name, products in cur.fetchall():
                arr = json.loads(products)
                print(f"  {name}: {arr[:3]}...")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
