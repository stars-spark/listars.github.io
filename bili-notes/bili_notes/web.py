"""手机网页（PWA）：知识库列表、笔记阅读、搜索、添加链接、系统分享入口。

部署在公网时必须设置 APP_PASSWORD：这个服务会用你的 B 站 Cookie 和模型 API Key 干活。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt

from . import notify
from .bilibili import BV_RE, USER_AGENT, BiliClient, BiliError, parse_ref
from .config import LLMConfig, Settings
from .pipeline import parse_timestamp, render_markdown_file, video_url_at
from .service import Service
from .store import TS_LINE_RE, Video
from .summarize import format_timestamp

HERE = Path(__file__).parent
COOKIE = "bn_auth"
PUBLIC_PATHS = ("/login", "/static/", "/manifest.webmanifest", "/sw.js", "/healthz")
SHORT_LINK_RE = re.compile(r"https?://(?:b23\.tv|bili2233\.cn)/[0-9A-Za-z]+")

md = MarkdownIt("commonmark", {"html": False}).enable("table")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["ts"] = format_timestamp

STATUS_LABEL = {"pending": "排队中", "processing": "处理中", "done": "已完成", "failed": "失败", "deleted": "已删除"}
templates.env.globals["status_label"] = STATUS_LABEL


def extract_link(text: str) -> str:
    """从 B 站 App 分享出来的文字里找出视频链接或 BV 号。"""
    if m := BV_RE.search(text):
        return m.group(1)
    if m := SHORT_LINK_RE.search(text):
        return m.group(0)
    if m := re.search(r"av\d+", text, re.IGNORECASE):
        return m.group(0)
    raise BiliError("没有在内容里找到 B 站视频链接")


def resolve_bvid(text: str, cookie: str = "") -> tuple[str, int]:
    link = extract_link(text)
    if link.startswith("BV"):
        return link, 1
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=15) as http:
        ref = parse_ref(link, http)
    if ref.bvid:
        return ref.bvid, ref.page
    bili = BiliClient(cookie)  # 只有 av 号时要查一次才知道 BV 号
    try:
        return bili.video_info(ref).bvid, ref.page
    finally:
        bili.close()


def create_app(settings: Settings, llm: LLMConfig, start_service: bool = True) -> FastAPI:
    service = Service(settings, llm)
    store = service.store
    auth_token = _auth_token(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_service:
            service.start()
        yield
        service.stop.set()

    app = FastAPI(title="bili-notes", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.service = service
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if auth_token and not path.startswith(PUBLIC_PATHS):
            if not hmac.compare_digest(request.cookies.get(COOKIE, ""), auth_token):
                if request.method == "GET":
                    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
                return PlainTextResponse("未登录", status_code=401)
        return await call_next(request)

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx)

    def get_video(vid: int) -> Video:
        video = store.get(vid)
        if not video or video.status == "deleted":
            raise HTTPException(404, "没有这条笔记")
        return video

    # ---- 登录 --------------------------------------------------------------

    @app.get("/login")
    def login_form(request: Request, next: str = "/", error: str = ""):
        return page(request, "login.html", next=next, error=error)

    @app.post("/login")
    def login(password: str = Form(...), next: str = Form("/")):
        if not auth_token or not hmac.compare_digest(_hash(settings, password), auth_token):
            time.sleep(1)  # 减慢暴力猜密码
            return RedirectResponse("/login?error=1", status_code=303)
        resp = RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/", status_code=303)
        resp.set_cookie(COOKIE, auth_token, max_age=365 * 86400, httponly=True, samesite="lax")
        return resp

    # ---- 首页 / 列表 -------------------------------------------------------

    @app.get("/")
    def index(request: Request, msg: str = "", page_no: int = 1):
        per_page = 30
        videos = store.recent(per_page + 1, (page_no - 1) * per_page)
        return page(
            request, "index.html",
            videos=videos[:per_page], has_more=len(videos) > per_page, page_no=page_no,
            counts=store.counts(), msg=msg, service=service,
            last_sync=store.kv_get("last_sync"), sync_error=store.kv_get("last_sync_error"),
            sync_enabled=bool(settings.fav_folder or settings.sync_watchlater),
            channels=notify.channels(settings), llm=llm,
        )

    @app.post("/add")
    def add(link: str = Form(...)):
        return _add(link)

    @app.get("/share")
    def share(title: str = "", text: str = "", url: str = ""):
        # Android 上安装 PWA 后，B 站 App 的「分享」菜单里会出现本应用，分享内容从这里进来
        return _add(" ".join([url, text, title]))

    def _add(text: str):
        try:
            bvid, page_no = resolve_bvid(text, settings.bili_cookie)
        except (BiliError, httpx.HTTPError) as e:
            return RedirectResponse(f"/?msg={_q(f'添加失败：{e}')}", status_code=303)
        vid, new = store.add(bvid, page_no, source="manual")
        service.wake_worker.set()
        if not new:
            return RedirectResponse(f"/v/{vid}", status_code=303)
        return RedirectResponse(f"/?msg={_q('已加入队列')}", status_code=303)

    @app.post("/sync")
    def sync_now():
        service.wake_sync.set()
        return RedirectResponse(f"/?msg={_q('已开始同步收藏夹')}", status_code=303)

    # ---- 笔记 --------------------------------------------------------------

    @app.get("/v/{vid}")
    def video_page(request: Request, vid: int):
        video = get_video(vid)
        transcript = []
        for line in video.transcript.splitlines():
            if m := TS_LINE_RE.match(line):
                ts = m.group(1)
                transcript.append((ts, video_url_at(video.bvid, video.page, parse_timestamp(ts)), m.group(2)))
        return page(
            request, "video.html", v=video, note_html=md.render(video.note) if video.note else "",
            transcript=transcript, service=service,
        )

    @app.get("/v/{vid}/note.md")
    def video_markdown(vid: int):
        video = get_video(vid)
        if video.status != "done":
            raise HTTPException(404, "笔记还没生成")
        return Response(
            render_markdown_file(video), media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"inline; filename*=UTF-8''{_q(video.bvid)}.md"},
        )

    @app.post("/v/{vid}/retry")
    def retry(vid: int):
        get_video(vid)
        store.requeue(vid, keep_transcript=True)
        service.wake_worker.set()
        return RedirectResponse(f"/v/{vid}", status_code=303)

    @app.post("/v/{vid}/refetch")
    def refetch(vid: int):
        get_video(vid)
        store.requeue(vid, keep_transcript=False)
        service.wake_worker.set()
        return RedirectResponse(f"/v/{vid}", status_code=303)

    @app.post("/v/{vid}/delete")
    def delete(vid: int):
        get_video(vid)
        store.delete(vid)
        return RedirectResponse(f"/?msg={_q('已删除')}", status_code=303)

    # ---- 搜索 --------------------------------------------------------------

    @app.get("/search")
    def search(request: Request, q: str = ""):
        hits = store.search(q) if q.strip() else []
        results = [
            (h.video, [(ts, video_url_at(h.video.bvid, h.video.page, parse_timestamp(ts)) if ts else "", text)
                       for ts, text in h.snippets])
            for h in hits
        ]
        return page(request, "search.html", q=q, results=results)

    # ---- PWA 与杂项 --------------------------------------------------------

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(HERE / "static" / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js")
    def service_worker():
        # Service Worker 必须从根路径提供，才能管理整个站点
        return FileResponse(HERE / "static" / "sw.js", media_type="text/javascript",
                            headers={"Cache-Control": "no-cache"})

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


def _q(s: str) -> str:
    from urllib.parse import quote

    return quote(s)


def _hash(settings: Settings, password: str) -> str:
    return hmac.new(_secret(settings), password.encode(), hashlib.sha256).hexdigest()


def _auth_token(settings: Settings) -> str:
    return _hash(settings, settings.app_password) if settings.app_password else ""


def _secret(settings: Settings) -> bytes:
    path = settings.data_dir / "secret.key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip().encode()


def run(host: str, port: int) -> None:
    import logging

    import uvicorn

    settings = Settings.from_env()
    if host not in ("127.0.0.1", "localhost", "::1") and not settings.app_password:
        raise SystemExit("在公网 / 局域网上提供服务时必须设置 APP_PASSWORD（见 .env.example）")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # 每个请求一行太吵
    uvicorn.run(create_app(settings, LLMConfig.from_env()), host=host, port=port, proxy_headers=True)
