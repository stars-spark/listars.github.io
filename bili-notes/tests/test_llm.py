import json
from types import SimpleNamespace

import httpx
import pytest

from bili_notes import llm as llm_mod
from bili_notes.config import LLMConfig
from bili_notes.llm import LLMError, generate


def sse(*chunks, done=True):
    lines = [": keep-alive", ""]
    for c in chunks:
        lines += [f"data: {json.dumps(c, ensure_ascii=False)}", ""]
    if done:
        lines += ["data: [DONE]", ""]
    return "\n".join(lines)


def delta(text=None, finish=None):
    d = {"content": text} if text is not None else {}
    return {"choices": [{"index": 0, "delta": d, "finish_reason": finish}]}


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_deepseek_streaming(llm):
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, text=sse(delta("## 一句"), delta("话概括"), delta(finish="stop")))

    result = generate(llm, "系统", "用户", http=client(handler))
    assert result.text == "## 一句话概括" and not result.truncated
    assert seen["url"] == "https://api.deepseek.com/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "deepseek-chat"
    assert seen["body"]["stream"] is True
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]


def test_length_finish_marks_truncated(llm):
    handler = lambda req: httpx.Response(200, text=sse(delta("x"), delta(finish="length")))
    assert generate(llm, "s", "u", http=client(handler)).truncated


def test_client_error_is_not_retried(llm):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "Authentication Fails"}})

    with pytest.raises(LLMError, match="401"):
        generate(llm, "s", "u", http=client(handler))
    assert len(calls) == 1


def test_server_error_is_retried(llm, monkeypatch):
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    responses = [httpx.Response(503, text="busy"), httpx.Response(200, text=sse(delta("ok"), delta(finish="stop")))]
    assert generate(llm, "s", "u", http=client(lambda req: responses.pop(0))).text == "ok"


def test_missing_key(llm):
    llm.api_key = ""
    with pytest.raises(LLMError, match="API Key"):
        generate(llm, "s", "u")


def test_config_presets(monkeypatch):
    for k in ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MAX_TOKENS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    cfg = LLMConfig.from_env()
    assert (cfg.provider, cfg.model, cfg.api_key, cfg.base_url) == ("deepseek", "deepseek-chat", "sk-ds", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1/")
    monkeypatch.setenv("LLM_MODEL", "qwen2.5")
    cfg = LLMConfig.from_env()
    assert cfg.base_url == "http://localhost:11434/v1" and cfg.model == "qwen2.5"


def test_claude_request_shape(monkeypatch):
    calls = []
    message = SimpleNamespace(stop_reason="end_turn", stop_details=None,
                              content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text="笔记")])

    class Stream:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get_final_message(self): return message

    class FakeAnthropic:
        def __init__(self, api_key=None):
            self.beta = SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: calls.append(kw) or Stream()))

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    cfg = LLMConfig(provider="claude", model="claude-opus-5", max_tokens=64000)
    assert generate(cfg, "s", "u").text == "笔记"
    assert calls[0]["fallbacks"] == "default" and calls[0]["thinking"] == {"type": "adaptive"}
    generate(LLMConfig(provider="claude", model="claude-sonnet-5"), "s", "u")
    assert "fallbacks" not in calls[1]
