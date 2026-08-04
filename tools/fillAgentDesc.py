"""
fillAgentDesc.py

补齐 agentBaseinfo 表中 agentDescription 字段。
从 company_addType 表取 main_business，补齐仅含 "{公司名}的门户经纪人," 的记录。

用法：
    python tools/fillAgentDesc.py         # 全量
    python tools/fillAgentDesc.py --test  # 只跑 10 条
"""

import sys
from pathlib import Path

import pymysql

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_MYSQL_PATH = BASE_DIR / "env" / "mysql"


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


def extract_company_name(agent_name: str) -> str:
    """从 agentName 提取公司名。"""
    return agent_name.split(".门户经纪人.")[0] if ".门户经纪人." in agent_name else agent_name


def main():
    test_mode = "--test" in sys.argv
    conn = get_connection()
    limit = 10 if test_mode else None

    try:
        with conn.cursor() as cur:
            # 1. 查询待补齐的记录
            cur.execute(
                "SELECT a.id, a.agentName, a.agentDescription, c.main_business "
                "FROM agentBaseinfo a "
                "INNER JOIN company_addType c "
                "  ON SUBSTRING_INDEX(a.agentName, '.门户经纪人.', 1) = c.name "
                "WHERE a.agentDescription LIKE '%的门户经纪人,%' "
                "  AND TRIM(SUBSTRING_INDEX(a.agentDescription, ',', -1)) = '' "
                "ORDER BY a.id "
                + (f"LIMIT {limit}" if limit else "")
            )
            rows = cur.fetchall()

        mode_label = "测试模式 (10条)" if test_mode else "全量模式"
        print(f"[{mode_label}] 可匹配记录: {len(rows)} 条\n")

        if not rows:
            print("没有需要补齐的记录。")
            return

        updated = 0
        skipped = 0

        with conn.cursor() as cur:
            for rec_id, agent_name, old_desc, main_biz in rows:
                company = extract_company_name(agent_name)

                if not main_biz or not main_biz.strip():
                    print(f"  SKIP (main_business 为空): {company}")
                    skipped += 1
                    continue

                new_desc = f"{company}的门户经纪人,{main_biz.strip()}"

                if limit:
                    print(f"---  id={rec_id}")
                    print(f"  company:   {company}")
                    print(f"  old:       {old_desc}")
                    print(f"  new:       {new_desc[:120]}{'...' if len(new_desc) > 120 else ''}")
                    print()

                cur.execute(
                    "UPDATE agentBaseinfo SET agentDescription = %s WHERE id = %s",
                    (new_desc, rec_id),
                )
                updated += 1

        conn.commit()
        print(f"[完成] 更新 {updated} 条，跳过 {skipped} 条")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
