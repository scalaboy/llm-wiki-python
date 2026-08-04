"""
select.py — 读取 userComQuest 最近 N 轮对话

用法：
    from utils.select import get_recent_quests
    text = get_recent_quests("u001", "c001")
"""

import pymysql

from utils.env_config import load_mysql_config


def get_connection():
    import os
    print(f"[select] get_connection: APP_ENV={os.environ.get('APP_ENV','?')!r} ENV={os.environ.get('ENV','?')!r}")
    cfg = load_mysql_config()
    print(f"[select] MYSQL_HOST={cfg.get('MYSQL_HOST','?')}")
    return pymysql.connect(
        host=cfg["MYSQL_HOST"],
        port=int(cfg.get("MYSQL_PORT", 3306)),
        user=cfg["MYSQL_USER"],
        password=cfg["MYSQL_PASSWORD"],
        database=cfg["MYSQL_DB"],
        charset="utf8mb4",
    )


def get_recent_quests(userid: str, companyid: str, limit: int = 25) -> str:
    """
    读取指定用户和企业的最近 N 轮对话，拼接 questText + createTime 后返回。

    Args:
        userid:    用户ID
        companyid: 企业ID
        limit:     返回最近 N 轮，默认 25

    Returns:
        拼接后的字符串，格式：
        [turn=3 2025-01-01 12:00:00] 问题文本...
        [turn=2 2025-01-01 11:00:00] 问题文本...
        （无匹配记录时返回空字符串）
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # 1. 找该用户+企业的最大 turn
            cur.execute(
                "SELECT MAX(turn) FROM userComQuest "
                "WHERE userid = %s AND companyid = %s",
                (userid, companyid),
            )
            row = cur.fetchone()
            max_turn = row[0] if row and row[0] is not None else 0

            if max_turn == 0:
                return ""

            # 2. 取最近 limit 轮（max_turn 往前数），按 turn 升序
            min_turn = max(1, max_turn - limit + 1)
            cur.execute(
                "SELECT turn, questText, createTime FROM userComQuest "
                "WHERE userid = %s AND companyid = %s "
                "AND turn >= %s AND turn <= %s "
                "ORDER BY turn ASC",
                (userid, companyid, min_turn, max_turn),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return ""

    parts = []
    for turn, quest_text, create_time in rows:
        ts = str(create_time) if create_time else ""
        text = quest_text or ""
        parts.append(f"[turn={turn} {ts}] {text}")

    return "\n".join(parts)


if __name__ == "__main__":
    # 简单自测
    result = get_recent_quests("u001", "c001")
    if result:
        print(result)
    else:
        print("(无记录)")
