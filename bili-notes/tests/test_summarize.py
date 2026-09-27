from types import SimpleNamespace

from bili_notes.bilibili import VideoInfo
from bili_notes.pipeline import render_note
from bili_notes.summarize import build_user_message, summarize


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeClient:
    def __init__(self, message):
        self.calls = []
        outer = self

        class Messages:
            def stream(self, **kwargs):
                outer.calls.append(kwargs)
                return FakeStream(message)

        self.beta = SimpleNamespace(messages=Messages())


INFO = VideoInfo(
    bvid="BV1ThZnYxEZi", aid=1, cid=2, page=1, title='被架空的"皇帝"', part_title="",
    owner="UP", duration=222, desc="参考：《万历十五年》", tags=["历史"],
)


def text_message(text, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason, stop_details=None,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
    )


def test_summarize_request_shape_and_output():
    client = FakeClient(text_message("## 一句话概括\n杀权臣不难，收权难 [02:02]"))
    out = summarize(INFO, "[00:00] 你好", client=client)
    assert out.startswith("## 一句话概括")
    call = client.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["fallbacks"] == "default"
    assert "<transcript>\n[00:00] 你好\n</transcript>" in call["messages"][0]["content"]


def test_no_fallbacks_for_other_models():
    client = FakeClient(text_message("x"))
    summarize(INFO, "t", model="claude-sonnet-5", client=client)
    assert "fallbacks" not in client.calls[0] and "betas" not in client.calls[0]


def test_truncation_is_flagged():
    out = summarize(INFO, "t", client=FakeClient(text_message("x", "max_tokens")))
    assert "截断" in out


def test_user_message_includes_metadata():
    msg = build_user_message(INFO, "t")
    assert "《万历十五年》" in msg and "标签：历史" in msg and "时长：03:42" in msg


def test_render_note_frontmatter_and_links():
    note = render_note(INFO, "要点 [02:02]", "字幕（中文）", "claude-opus-5")
    assert note.startswith("---\ntitle: \"被架空的'皇帝'\"")
    assert "(https://www.bilibili.com/video/BV1ThZnYxEZi?t=122)" in note
