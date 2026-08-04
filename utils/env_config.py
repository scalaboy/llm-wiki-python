"""
env_config.py

根据 APP_ENV 环境变量（K8s deployment 注入）自动选择配置：
  - APP_ENV=test → env/env_test
  - APP_ENV=uat  → env/env_uat
  - APP_ENV=prod → env/env_prod

提供 MySQL / MQ 配置读取，带模块级缓存。
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# 模块级缓存
_env_cache: dict[str, dict[str, str]] = {}


def _get_env_path() -> Path:
    """根据 APP_ENV 返回配置文件路径。APP_ENV → ENV 兜底。"""
    app_env = os.environ.get("APP_ENV", "").strip().lower()
    # Dockerfile 构建时注入的 ENV 兜底
    if not app_env:
        app_env = os.environ.get("ENV", "").strip().lower()
    if app_env not in ("test", "uat", "prod"):
        raise RuntimeError(
            f"APP_ENV 环境变量未设置或值无效: '{app_env}'，"
            f"请设为 test、uat 或 prod"
        )
    filename = f"env_{app_env}"
    print('sonofbithch',filename,app_env)
    return BASE_DIR / "env" / filename


def _load_env_config() -> dict[str, str]:
    """读取 env 配置文件所有 KEY=VALUE 对（带缓存）。"""
    path = _get_env_path()
    cache_key = str(path)
    if cache_key in _env_cache:
        return _env_cache[cache_key]

    config: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip()

    _env_cache[cache_key] = config
    print(
        f"[env_config] 已加载环境配置: {path} "
        f"(MYSQL_HOST={config.get('MYSQL_HOST','?')}, MQ_HOST={config.get('MQ_HOST','?')})"
    )
    return config


# ── 公共 API ──────────────────────────────────────────────────────────

def load_mysql_config() -> dict[str, str]:
    """读取 MySQL 配置。

    Returns:
        dict: MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DB
    """
    app_env = os.environ.get("APP_ENV", "").strip().lower()
    print(f"[env_config] load_mysql_config: APP_ENV={app_env!r}")
    cfg = _load_env_config()
    return {
        "MYSQL_HOST": cfg.get("MYSQL_HOST", ""),
        "MYSQL_PORT": cfg.get("MYSQL_PORT", "3306"),
        "MYSQL_USER": cfg.get("MYSQL_USER", ""),
        "MYSQL_PASSWORD": cfg.get("MYSQL_PASSWORD", ""),
        "MYSQL_DB": cfg.get("MYSQL_DB", ""),
    }


def load_mq_config() -> str:
    """读取 RabbitMQ 配置，返回 AMQP URL。

    Returns:
        str: amqp://user:pass@host:port/
    """
    app_env = os.environ.get("APP_ENV", "").strip().lower()
    print(f"[env_config] load_mq_config: APP_ENV={app_env!r}")
    cfg = _load_env_config()
    host = cfg.get("MQ_HOST", "")
    port = cfg.get("MQ_PORT", "5672")
    username = cfg.get("MQ_USERNAME", "guest")
    password = cfg.get("MQ_PASSWORD", "guest")

    if not host:
        raise KeyError("env 配置中缺少 MQ_HOST")

    return f"amqp://{username}:{password}@{host}:{port}/"


def load_mq_queue_name() -> str:
    """读取 MQ 队列名称，默认 'addCompanyDoc_queue'。"""
    app_env = os.environ.get("APP_ENV", "").strip().lower()
    print(f"[env_config] load_mq_queue_name: APP_ENV={app_env!r}")
    cfg = _load_env_config()
    return cfg.get("MQ_QUEUE", "addCompanyDoc_queue")
