"""
createTable.py

根据 APP_ENV 自动选择环境配置，连接 MySQL 并创建数据表 companyDocAdd。
用法：
    python utils/createTable.py

说明：
    - 连接参数通过 utils/env_config 根据 APP_ENV 自动选择 env/env_test 或 env/env_uat。
    - 下方 TABLE_SCHEMAS 中定义待创建的表，按需增删。
"""

import pymysql

from utils.env_config import load_mysql_config


# 待创建的表结构，key 为表名，value 为 CREATE TABLE 语句。
TABLE_SCHEMAS = {
    "companyDocAdd": """
        CREATE TABLE IF NOT EXISTS companyDocAdd (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
            user CHAR(50) NOT NULL DEFAULT '' COMMENT '用户',
            companyId CHAR(50) NOT NULL DEFAULT '' COMMENT '公司ID',
            companyName TEXT COMMENT '公司名称',
            docaddress TEXT COMMENT '文档地址',
            gettask TINYINT NOT NULL DEFAULT 0 COMMENT '是否获取任务 0-1',
            docAddwiki TINYINT NOT NULL DEFAULT 0 COMMENT '是否已入wiki 0-1',
            createTime DATETIME COMMENT '创建时间',
            add2wikitime DATETIME COMMENT '入wiki时间',
            PRIMARY KEY (id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='公司文档新增表';
    """,
    "userComQuest": """
        CREATE TABLE IF NOT EXISTS userComQuest (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
            userid CHAR(100) NOT NULL DEFAULT '' COMMENT '用户ID',
            companyid CHAR(100) NOT NULL DEFAULT '' COMMENT '企业ID',
            turn INT NOT NULL DEFAULT 0 COMMENT '对话轮次',
            questText TEXT COMMENT '问题文本',
            createTime DATETIME COMMENT '创建时间',
            PRIMARY KEY (id),
            INDEX idx_userid_companyid (userid, companyid)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户企业问答记录表';
    """,
}


def create_tables():
    cfg = load_mysql_config()
    conn = pymysql.connect(
        host=cfg["MYSQL_HOST"],
        port=int(cfg.get("MYSQL_PORT", 3306)),
        user=cfg["MYSQL_USER"],
        password=cfg["MYSQL_PASSWORD"],
        database=cfg["MYSQL_DB"],
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cursor:
            for table_name, ddl in TABLE_SCHEMAS.items():
                cursor.execute(ddl)
                print(f"[OK] table ready: {table_name}")
        conn.commit()
        print("All tables created successfully.")
    finally:
        conn.close()


if __name__ == "__main__":
    create_tables()
