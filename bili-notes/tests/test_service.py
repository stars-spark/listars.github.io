import httpx
import pytest

from bili_notes import notify, service as service_mod
from bili_notes.bilibili import FavItem
from bili_notes.service import Service


class FakeBili:
    folder: list[FavItem] = []
    watch: list[FavItem] = []
    fail = None

    def __init__(self, cookie=None):
        pass

    def resolve_folder(self, spec):
        if self.fail:
            raise self.fail
        return 42

    def folder_items(self, media_id, since=0, limit=200):
        return [i for i in self.folder if i.added_at > since][:limit]

    def watch_later(self, since=0):
        return [i for i in self.watch if i.added_at > since]

    def close(self):
        pass


@pytest.fixture
def svc(settings, llm, monkeypatch, store):
    monkeypatch.setattr(service_mod, "BiliClient", FakeBili)
    monkeypatch.setattr(service_mod.time, "sleep", lambda s: None)
    FakeBili.fail = None
    sent = []
    monkeypatch.setattr(notify, "send", lambda s, title, body, url="", log=print, http=None: sent.append((title, body, url)))
    settings.fav_folder = "待整理"
    settings.initial_sync_limit = 2
    s = Service(settings, llm, store)
    s.sent = sent
    return s


def fav(n, t):
    return FavItem(f"BV{n:010d}", f"视频{n}", t)


def test_first_sync_takes_only_recent_then_incremental(svc):
    FakeBili.folder = [fav(3, 300), fav(2, 200), fav(1, 100)]  # 新 → 旧
    assert svc.sync_once() == 2
    assert [v.bvid for v in svc.store.recent()] == ["BV0000000002", "BV0000000003"]
    assert svc.store.kv_get("since:fav:42") == "300"
    assert svc.sync_once() == 0
    FakeBili.folder = [fav(4, 400)] + FakeBili.folder
    assert svc.sync_once() == 1
    # 先收藏的先处理
    assert [svc.store.claim_next().bvid for _ in range(3)] == ["BV0000000002", "BV0000000003", "BV0000000004"]


def test_empty_first_sync_sets_since_to_now(svc):
    FakeBili.folder = []
    assert svc.sync_once() == 0
    assert int(svc.store.kv_get("since:fav:42")) > 0


def test_sync_error_is_recorded_and_notified_once(svc):
    FakeBili.fail = RuntimeError("账号未登录")
    svc.sync_once()
    svc.sync_once()
    assert svc.store.kv_get("last_sync_error") == "账号未登录"
    assert len(svc.sent) == 1 and "同步收藏夹失败" in svc.sent[0][0]


def test_process_next_success_and_failure(svc, monkeypatch):
    def fake_process(store, video, settings, llm, log):
        if video.bvid == "BVbadbadbadb":
            raise RuntimeError("拿不到字幕")
        store.update_info(video.id, title="好视频", part_title="", owner="up", duration=60, tags="")
        store.finish(video.id, "## 一句话概括\n核心结论\n", "deepseek/deepseek-chat")
        return store.get(video.id)

    monkeypatch.setattr(service_mod, "process_video", fake_process)
    ok, _ = svc.store.add("BVgoodgoodgo")
    bad, _ = svc.store.add("BVbadbadbadb")
    assert svc.process_next() and svc.process_next() and not svc.process_next()
    assert svc.store.get(ok).status == "done"
    assert svc.store.get(bad).status == "failed" and svc.store.get(bad).error == "拿不到字幕"
    assert svc.sent[0] == ("笔记已生成：好视频", "核心结论", f"https://notes.example/v/{ok}")
    assert svc.sent[1][0].startswith("笔记生成失败")


def test_notify_channels(settings):
    settings.bark_key = "barkkey"
    settings.serverchan_key = "sctp123tABC"
    settings.pushplus_token = "pp"
    seen = []

    def handler(req):
        seen.append(req)
        if "pushplus" in req.url.host:
            return httpx.Response(200, json={"code": 200, "msg": "ok"})
        if "day.app" in req.url.host:
            return httpx.Response(200, json={"code": 200, "message": "success"})
        return httpx.Response(200, json={"code": 0})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert notify.send(settings, "标题", "内容", "https://n/v/1", http=http) == 3
    urls = [str(r.url) for r in seen]
    assert urls == [
        "https://api.day.app/push",
        "https://123.push.ft07.com/send/sctp123tABC.send",
        "https://www.pushplus.plus/send",
    ]
    assert b"https://n/v/1" in seen[0].content


def test_notify_failure_is_logged_not_raised(settings):
    settings.bark_key = "k"
    logs = []
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"code": 400, "message": "bad"})))
    assert notify.send(settings, "t", "b", log=logs.append, http=http) == 0
    assert "Bark 推送失败" in logs[0]
