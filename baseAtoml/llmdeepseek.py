#!/usr/bin/env python3
"""
llmdeepseek.py — DeepSeek 官方 API 调用底座
=============================================

从 env/llm 读取 deepseek_key / deepseek_url，用 OpenAI 兼容接口
直连 DeepSeek 官方 API。与 llmbase.py（360 代理）互为替代。

用法：
    from llmdeepseek import call_llm
    reply = call_llm("你好")

    # 指定模型
    reply = call_llm("推理题", model="deepseek-reasoner")

环境变量覆盖（可选）：
    DEEPSEEK_MODEL — 覆盖默认模型（默认 deepseek-chat）
"""

from __future__ import annotations

import os
from pathlib import Path

import openai

# ── 配置 ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_LLM_FILE = REPO_ROOT / "env" / "llm"

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_REASONER_MODEL = "deepseek-reasoner"

# DeepSeek API 对各模型的最大输出 token 限制参考值
DEFAULT_MAX_TOKENS = 8192          # deepseek-chat 默认
REASONER_MAX_TOKENS = 65536        # deepseek-reasoner 上限（含思维链）

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
    """读取 env/llm 中的 deepseek 配置，返回 OpenAI 兼容客户端。"""
    cfg = _read_env_config()
    try:
        api_key = cfg["deepseek_key"]
        base_url = cfg["deepseek_url"].rstrip("/")
    except KeyError as e:
        raise KeyError(f"env/llm 缺少必需字段: {e}") from e

    return openai.OpenAI(
        api_key=api_key,
        base_url=base_url + "/v1",
        timeout=180.0,
        max_retries=2,
    )


def _client() -> openai.OpenAI:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = create_llm()
    return _CLIENT


def _resolve_model(model: str | None) -> str:
    """按优先级解析模型名: 参数 > DEEPSEEK_MODEL 环境变量 > 默认。"""
    if model:
        return model
    return os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL)


def _resolve_max_tokens(model: str, max_tokens: int | None) -> int | None:
    """根据模型返回合理的 max_tokens 默认值。传 0 表示不限制。"""
    if max_tokens is not None:
        return max_tokens if max_tokens > 0 else None
    if "reasoner" in model:
        return REASONER_MAX_TOKENS
    return DEFAULT_MAX_TOKENS


def call_llm(
    prompt: str,
    *,
    model: str | None = None,
    system: str = "",
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> str:
    """调用 DeepSeek API 并返回文本回复。

    Args:
        prompt:      用户提示词。
        model:       模型名，默认 deepseek-chat。
                     可选: deepseek-reasoner（推理模型）。
        system:      可选的 system 提示。
        max_tokens:  最大返回 token 数。None 用模型默认值；0 不限制。
        temperature: 采样温度。None 用 API 默认值。
                     注意: deepseek-reasoner 不支持 temperature 参数。
    """
    resolved_model = _resolve_model(model)
    resolved_max_tokens = _resolve_max_tokens(resolved_model, max_tokens)

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict = {
        "model": resolved_model,
        "messages": messages,
    }
    if resolved_max_tokens is not None:
        kwargs["max_tokens"] = resolved_max_tokens
    if temperature is not None:
        # deepseek-reasoner 不支持 temperature
        if "reasoner" not in resolved_model:
            kwargs["temperature"] = temperature

    resp = _client().chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def call_llm_stream(
    prompt: str,
    *,
    model: str | None = None,
    system: str = "",
    max_tokens: int | None = None,
    temperature: float | None = None,
):
    """调用 DeepSeek API 并以流式返回文本块（生成器）。

    Args:
        与 call_llm 相同。

    Yields:
        str: 每次 yield 一个增量文本块。
    """
    resolved_model = _resolve_model(model)
    resolved_max_tokens = _resolve_max_tokens(resolved_model, max_tokens)

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict = {
        "model": resolved_model,
        "messages": messages,
        "stream": True,
    }
    if resolved_max_tokens is not None:
        kwargs["max_tokens"] = resolved_max_tokens
    if temperature is not None:
        if "reasoner" not in resolved_model:
            kwargs["temperature"] = temperature

    stream = _client().chat.completions.create(**kwargs)
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content


if __name__ == "__main__":
    # 快速连通性测试
    print("deepseek-chat 连通测试：", end="", flush=True)
    try:
        result = call_llm("只回复两个字：连通", max_tokens=32)
        print(result)
    except Exception as e:
        print(f"失败: {e}")

    # 如果指定了 --reasoner 则也测试推理模型
    if "--reasoner" in os.sys.argv:
        print("deepseek-reasoner 连通测试：", end="", flush=True)
        try:
            result = call_llm("1+1等于几？只回复数字", model="deepseek-reasoner", max_tokens=128)
            print(result)
        except Exception as e:
            print(f"失败: {e}")
