#!/usr/bin/env python3
"""
llmbase.py — deepseek-v4 调用底座
=================================

从 env/llm 读取 360_key / 360Url，用 OpenAI 兼容接口调用
deepseek/deepseek-v4-pro。供 getwiki.py 等脚本复用。

用法：
    from llmbase import call_llm
    reply = call_llm("你好")
"""

from __future__ import annotations

import os
from pathlib import Path

import openai

# ── 配置 ────────────────────────────────────────────────────────────────
# llmbase.py 位于 <repo>/baseAtoml/ 下，env/llm 在 <repo>/env/llm。
REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_LLM_FILE = REPO_ROOT / "env" / "llm"

DEFAULT_MODEL = "deepseek/deepseek-v4-pro"
DEFAULT_MAX_TOKENS = 393216

_CLIENT: openai.OpenAI | None = None  # 懒加载单例


def _read_env_config() -> dict[str, str]:
    """从 env/llm 读取 key=value 配置（忽略空行与 # 注释）。"""
    if not ENV_LLM_FILE.exists():
        raise FileNotFoundError(f"未找到 LLM 配置文件: {ENV_LLM_FILE}")
    cfg: dict[str, str] = {}
    for line in ENV_LLM_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def create_llm() -> openai.OpenAI:
    """读取 env/llm 中的 360 配置，返回 OpenAI 兼容客户端。"""
    cfg = _read_env_config()
    try:
        api_key, base = cfg["360_key"], cfg["360Url"].rstrip("/")
    except KeyError as e:
        raise KeyError(f"env/llm 缺少必需字段: {e}") from e
    return openai.OpenAI(api_key=api_key, base_url=base + "/v1",
                         timeout=120.0, max_retries=2)


def _client() -> openai.OpenAI:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = create_llm()
    return _CLIENT


def call_llm(
    prompt: str,
    *,
    model: str | None = None,
    system: str = "",
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> str:
    """调用 deepseek-v4 并返回文本回复。

    Args:
        prompt:     用户提示词。
        model:      模型名，默认取环境变量 LLM_MODEL 或 deepseek/deepseek-v4-pro。
        system:     可选的 system 提示。
        max_tokens: 最大返回 token 数（默认 1M）；传 0 / None 则不限制。
    """
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict = {"model": model or os.getenv("LLM_MODEL", DEFAULT_MODEL),
                    "messages": messages}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens

    resp = _client().chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


if __name__ == "__main__":
    print(call_llm("只回复两个字：连通", max_tokens=32))
