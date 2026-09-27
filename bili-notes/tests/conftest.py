import pytest

from bili_notes.config import LLMConfig, Settings
from bili_notes.store import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, app_password="pw", public_url="https://notes.example")


@pytest.fixture
def llm():
    return LLMConfig(provider="deepseek", model="deepseek-chat", api_key="sk-test",
                     base_url="https://api.deepseek.com")


@pytest.fixture
def store(settings):
    return Store(settings.db_path)


TRANSCRIPT = "\n".join([
    "[00:00] 今天讲明朝的张居正改革",
    "[00:30] 一条鞭法把赋役合并 折算成银两征收",
    "[01:02] 考成法整顿官僚体系",
])


def make_done(store, bvid="BV1ThZnYxEZi", page=1, note="## 一句话概括\n张居正改革的得失\n\n## 核心观点\n- 一条鞭法 [00:30]",
              title="张居正改革", transcript=TRANSCRIPT):
    vid, _ = store.add(bvid, page)
    store.update_info(vid, title=title, part_title="", owner="某UP", duration=222, tags="历史")
    store.save_transcript(vid, "字幕（中文）", transcript)
    store.finish(vid, note, "deepseek/deepseek-chat")
    return vid
