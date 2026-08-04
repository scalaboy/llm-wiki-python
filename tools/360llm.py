#!/usr/bin/env python3
"""
360 LLM 调用
============
从 env/llm 读取 360_key 和 360Url，
用 OpenAI 兼容接口调用 deepseek/deepseek-v4-pro。

用法：
    from tools.360llm import create_llm, chat

    llm = create_llm()
    reply = chat(llm, "你好")
"""

from pathlib import Path
import openai


# ---- 读配置 ----

def _read_config() -> dict[str, str]:
    cfg: dict[str, str] = {}
    path = Path(__file__).resolve().parent.parent / "env" / "llm"
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


# ---- 建客户端 ----

def create_llm(model: str = "deepseek/deepseek-v4-pro") -> openai.OpenAI:
    """读取 env/llm 中 360 配置，返回 OpenAI 客户端。"""
    cfg = _read_config()
    return openai.OpenAI(
        api_key=cfg["360_key"],
        base_url=cfg["360Url"] + "/v1",
        timeout=120.0,
        max_retries=2,
    )


# ---- 推理 ----

def chat(
    llm: openai.OpenAI,
    prompt: str,
    *,
    model: str = "deepseek/deepseek-v4-pro",
    system: str = "",
) -> str:
    """输入文本，返回模型回复。"""
    msgs: list[dict[str, str]] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    r = llm.chat.completions.create(model=model, messages=msgs)
    return r.choices[0].message.content or ""


# ---- 自检 ----

if __name__ == "__main__":
    llm = create_llm()
    print(chat(llm, "谈谈你对恩施玉露的理解,请简单回答"))
