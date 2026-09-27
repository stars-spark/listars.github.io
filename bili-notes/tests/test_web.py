import pytest
from fastapi.testclient import TestClient

from bili_notes.web import create_app

from conftest import make_done


@pytest.fixture
def app(settings, llm):
    return create_app(settings, llm, start_service=False)


@pytest.fixture
def client(app):
    c = TestClient(app)
    r = c.post("/login", data={"password": "pw", "next": "/"}, follow_redirects=False)
    assert r.status_code == 303
    return c


def test_requires_login(app):
    c = TestClient(app)
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert c.post("/add", data={"link": "BV1ThZnYxEZi"}).status_code == 401
    assert c.post("/login", data={"password": "wrong"}, follow_redirects=False).headers["location"] == "/login?error=1"
    for path in ("/manifest.webmanifest", "/sw.js", "/static/style.css", "/healthz"):
        assert c.get(path).status_code == 200


def test_login_rejects_open_redirect(app):
    c = TestClient(app)
    r = c.post("/login", data={"password": "pw", "next": "//evil.com"}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_add_from_share_text_and_list(client, app):
    r = client.get("/share", params={"text": "【讲明朝的视频】 https://www.bilibili.com/video/BV1ThZnYxEZi/?share_source=copy"})
    assert r.status_code == 200 and "已加入队列" in r.text
    assert "BV1ThZnYxEZi" in r.text and "排队中" in r.text
    # 重复添加直接跳到已有条目
    r = client.post("/add", data={"link": "BV1ThZnYxEZi"}, follow_redirects=False)
    assert r.headers["location"] == "/v/1"
    assert "没有在内容里找到" in client.post("/add", data={"link": "随便"}).text


def test_note_page_renders_markdown_safely(client, app):
    store = app.state.service.store
    vid = make_done(store, note="## 核心观点\n- 一条鞭法 [00:30](https://www.bilibili.com/video/BV1ThZnYxEZi?t=30)\n\n<script>alert(1)</script>")
    r = client.get(f"/v/{vid}")
    assert "<h2>核心观点</h2>" in r.text
    assert "<script>alert(1)</script>" not in r.text
    assert 'href="https://www.bilibili.com/video/BV1ThZnYxEZi?t=30"' in r.text  # 逐字稿时间戳可点
    md = client.get(f"/v/{vid}/note.md")
    assert md.text.startswith("---\ntitle: \"张居正改革\"")


def test_search_page(client, app):
    make_done(app.state.service.store)
    r = client.get("/search", params={"q": "一条鞭法"})
    assert "张居正改革" in r.text and "?t=30" in r.text


def test_retry_delete(client, app):
    store = app.state.service.store
    vid = make_done(store)
    client.post(f"/v/{vid}/retry")
    assert store.get(vid).status == "pending" and store.get(vid).transcript
    client.post(f"/v/{vid}/delete")
    assert client.get(f"/v/{vid}").status_code == 404
