"""
fillid.py

用 prod 数据库 company 表的 id 填充 workflow_test 数据库
company_addType 表的 external_company_id 字段，按 name 匹配。

用法：
    python tools/fillid.py
"""

from pathlib import Path

import pymysql

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_MYSQL_PATH = BASE_DIR / "env" / "mysql"


# -------------------------------------------
#  数据库连接
# -------------------------------------------


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


def get_prod_connection(cfg: dict):
    """连接 prod 数据库（company 表所在库）。"""
    return pymysql.connect(
        host=cfg["prod_HOST"],
        port=int(cfg.get("prod_PORT", 3306)),
        user=cfg["prod_USER"],
        password=cfg["prod_PASSWORD"],
        database=cfg["prod_DB"],
        charset="utf8mb4",
    )


def get_local_connection(cfg: dict):
    """连接 workflow_test 数据库（company_addType 表所在库）。"""
    return pymysql.connect(
        host=cfg["MYSQL_HOST"],
        port=int(cfg.get("MYSQL_PORT", 3306)),
        user=cfg["MYSQL_USER"],
        password=cfg["MYSQL_PASSWORD"],
        database=cfg["MYSQL_DB"],
        charset="utf8mb4",
    )


# -------------------------------------------
#  主流程
# -------------------------------------------


def main():
    cfg = load_mysql_config()

    conn_prod = get_prod_connection(cfg)
    conn_local = get_local_connection(cfg)

    try:
        # 1. 从 company_addType 取所有 name
        print("读取 company_addType ...")
        with conn_local.cursor() as cur:
            cur.execute("SELECT id, name, external_company_id FROM company_addType")
            local_rows = cur.fetchall()
        print(f"  共 {len(local_rows)} 条")

        # 统计待填充数量
        need_fill = [r for r in local_rows if not r[2]]
        already = len(local_rows) - len(need_fill)
        print(f"  已填充: {already}, 待填充: {len(need_fill)}")

        if not need_fill:
            print("无需填充，退出。")
            return

        # 2. 从 prod company 表取对应 id
        names = [r[1] for r in need_fill]
        name_to_company_id: dict[str, int] = {}

        print(f"\n从 prod company 表查询 {len(names)} 个名称...")
        batch_size = 500
        with conn_prod.cursor() as cur:
            for start in range(0, len(names), batch_size):
                batch = names[start : start + batch_size]
                placeholders = ",".join(["%s"] * len(batch))
                cur.execute(
                    f"SELECT id, name FROM company WHERE name IN ({placeholders})",
                    batch,
                )
                for cid, cname in cur.fetchall():
                    name_to_company_id[cname] = cid
                pct = min(start + batch_size, len(names)) * 100 // len(names)
                print(f"  进度: {min(start + batch_size, len(names))}/{len(names)} ({pct}%)")

        matched = len(name_to_company_id)
        unmatched = len(names) - matched
        print(f"\n  匹配成功: {matched}, 未匹配: {unmatched}")

        # 3. 批量更新 company_addType
        print("\n更新 company_addType.external_company_id ...")
        update_sql = "UPDATE company_addType SET external_company_id = %s WHERE id = %s"
        updated = 0
        with conn_local.cursor() as cur:
            for local_id, name, _ in need_fill:
                company_id = name_to_company_id.get(name)
                if company_id is not None:
                    cur.execute(update_sql, (str(company_id), local_id))
                    updated += 1

        conn_local.commit()
        print(f"  更新完成: {updated} 条")

    finally:
        conn_prod.close()
        conn_local.close()


if __name__ == "__main__":
    main()
