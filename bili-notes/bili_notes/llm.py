"""大模型调用：DeepSeek / 任意 OpenAI 兼容接口，以及 Claude。

整个流程里只有这里需要知道用的是哪家模型，输入输出都是纯文本。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

from .config import LLMConfig

# 这些 Claude 模型支持服务端 fallbacks="default"：被安全分类器误拒时自动换模型重试
CLAUDE_FALLBACK_MODELS = {"claude-opus-5", "claude-opus-5-5", "claude-fable-5-1"}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    truncated: bool = False


def generate(cfg: LLMConfig, system: str, user: str, http: httpx.Client | None = None) -> LLMResult:
    if not cfg.api_key and cfg.provider != "claude":
        raise LLMError(f"没有配置 {cfg.provider} 的 API Key（DEEPSEEK_API_KEY 或 LLM_API_KEY）")
    if cfg.provider == "claude":
        return _claude(cfg, system, user)
    if not cfg.base_url or not cfg.model:
        raise LLMError("使用 openai 兼容接口时需要设置 LLM_BASE_URL 和 LLM_MODEL")
    return _openai_compatible(cfg, system, user, http)


# ---- DeepSeek / OpenAI 兼容接口 -----------------------------------------------


def _openai_compatible(cfg: LLMConfig, system: str, user: str, http: httpx.Client | None) -> LLMResult:
    own = http is None
    http = http or httpx.Client(timeout=httpx.Timeout(30, read=300))
    payload = {
        "model": cfg.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": cfg.max_tokens,
        "stream": True,  # 长输出用流式，避免网关超时
    }
    try:
        for attempt in range(3):
            try:
                return _stream_chat(http, cfg, payload)
            except _Retryable as e:
                if attempt == 2:
                    raise LLMError(str(e)) from e
                time.sleep(5 * (attempt + 1))
    finally:
        if own:
            http.close()
    raise AssertionError("unreachable")


class _Retryable(Exception):
    pass


def _stream_chat(http: httpx.Client, cfg: LLMConfig, payload: dict) -> LLMResult:
    url = f"{cfg.base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    parts: list[str] = []
    finish = None
    try:
        with http.stream("POST", url, json=payload, headers=headers) as resp:
            if resp.status_code != 200:
                body = resp.read().decode("utf-8", "replace")[:300]
                msg = f"{cfg.provider} 接口返回 HTTP {resp.status_code}：{body}"
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise _Retryable(msg)
                raise LLMError(msg)
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue  # 空行或 ": keep-alive" 注释
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("error"):
                    raise LLMError(f"{cfg.provider} 返回错误：{chunk['error']}")
                for choice in chunk.get("choices") or []:
                    content = (choice.get("delta") or {}).get("content")
                    if content:
                        parts.append(content)
                    finish = choice.get("finish_reason") or finish
    except httpx.TransportError as e:
        raise _Retryable(f"连接 {cfg.provider} 失败：{e}") from e
    text = "".join(parts).strip()
    if not text:
        raise LLMError(f"{cfg.provider} 没有返回内容（finish_reason={finish}）")
    return LLMResult(text, truncated=finish == "length")


# ---- Claude ---------------------------------------------------------------


def _claude(cfg: LLMConfig, system: str, user: str) -> LLMResult:
    import anthropic

    client = anthropic.Anthropic(api_key=cfg.api_key or None)
    kwargs: dict = {}
    if cfg.model in CLAUDE_FALLBACK_MODELS:
        kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    with client.beta.messages.stream(
        model=cfg.model,
        max_tokens=cfg.max_tokens,
        thinking={"type": "adaptive"},
        output_config={"effort": cfg.effort},
        system=system,
        messages=[{"role": "user", "content": user}],
        **kwargs,
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        detail = getattr(message.stop_details, "explanation", None) if message.stop_details else None
        raise LLMError(f"模型拒绝了这次请求：{detail or '未说明原因'}")
    text = "".join(b.text for b in message.content if b.type == "text").strip()
    return LLMResult(text, truncated=message.stop_reason == "max_tokens")
