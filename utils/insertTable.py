"""
insertTable.py

向 companyDocAdd 表插入一条记录。
用法：
    from utils.insertTable import insert_company_doc
    insert_company_doc("u001", "c001", "某某公司", "http://.../doc")
"""

from datetime import datetime

import pymysql

from utils.env_config import load_mysql_config


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


def insert_company_doc(user: str, companyId: str, companyName: str, docAddress: str):
    """
    向 companyDocAdd 表插入一条记录，gettask 固定为 1。

    Args:
        user: 用户
        companyId: 公司ID
        companyName: 公司名称
        docAddress: 文档地址

    Returns:
        (retcode, message):
            retcode: 插入成功为 1，失败为 0
            message: 成功为空字符串，失败为 "新增文档失败" 并带上 companyId 和 user
    """
    sql = """
        INSERT INTO companyDocAdd
            (user, companyId, companyName, docaddress, gettask, docAddwiki, createTime)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s)
    """
    retcode = 1
    message = ""
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                sql,
                (user, companyId, companyName, docAddress, 1, 0, datetime.now()),
            )
        conn.commit()
    except Exception as e:
        retcode = 0
        message = f"新增文档失败, companyId={companyId}, user={user}docAddress={docAddress}"
        print(f"[ERROR] {message}: {e}")
    finally:
        if conn is not None:
            conn.close()

    return retcode, message


def insert_userComQuest(userid: str, companyid: str, questText: str) -> tuple[int, str]:
    """
    向 userComQuest 表插入一条记录。turn 按 userid+companyid 自动递增。

    Args:
        userid:    用户ID
        companyid: 企业ID
        questText: 问题文本

    Returns:
        (retcode, message):
            retcode: 插入成功为 1，失败为 0
            message: 成功为空字符串，失败为错误信息
    """
    retcode = 1
    message = ""
    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            # 自增 turn：当前 MAX(turn) + 1，首次为 1
            cursor.execute(
                "SELECT MAX(turn) FROM userComQuest "
                "WHERE userid = %s AND companyid = %s",
                (userid, companyid),
            )
            row = cursor.fetchone()
            next_turn = (row[0] + 1) if (row and row[0] is not None) else 1

            cursor.execute(
                "INSERT INTO userComQuest (userid, companyid, turn, questText, createTime) "
                "VALUES (%s, %s, %s, %s, %s)",
                (userid, companyid, next_turn, questText, datetime.now()),
            )
        conn.commit()
    except Exception as e:
        retcode = 0
        message = f"插入 userComQuest 失败: {e}"
        print(f"[ERROR] {message}")
    finally:
        if conn is not None:
            conn.close()

    return retcode, message


if __name__ == "__main__":
    # 简单自测
    insert_company_doc("u001", "c001", "示例公司", "http://example.com/doc")
