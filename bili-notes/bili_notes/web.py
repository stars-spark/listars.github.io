"""极简的本地网页服务：粘贴链接 → 后台生成 → 在浏览器里读笔记。

默认只监听 127.0.0.1。如果要部署到服务器给手机用，请自己加一层鉴权（例如反向代理的 Basic Auth），
因为这个服务会用你的 Cookie 和 API Key 干活。
"""

from __future__ import annotations

import html
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from .cli import config_from_env
from .pipeline import process


@dataclass
class Job:
    id: str
    video: str
    status: str = "排队中"
    logs: list[str] = field(default_factory=list)
    note: Path | None = None


app = FastAPI(title="bili-notes")
JOBS: dict[str, Job] = {}
_lock = threading.Lock()  # 一次只跑一个任务，避免同时占满显卡 / 触发风控

PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>B 站笔记</title>
<style>body{{font:16px/1.7 system-ui,sans-serif;max-width:860px;margin:0 auto;padding:16px;
color:#222;background:#fafafa}}input{{width:100%;padding:10px;font-size:16px;box-sizing:border-box}}
button{{margin-top:8px;padding:8px 18px;font-size:16px}}pre{{white-space:pre-wrap;background:#eee;padding:8px}}
a{{color:#0a66c2}}li{{margin:4px 0}}@media(prefers-color-scheme:dark){{body{{background:#161616;color:#ddd}}
pre{{background:#262626}}a{{color:#6cb6ff}}}}</style></head><body>{body}</body></html>"""


def page(body: str) -> HTMLResponse:
    return HTMLResponse(PAGE.format(body=body))


def _run(job: Job) -> None:
    with _lock:
        job.status = "处理中"
        try:
            job.note = process(job.video, config_from_env(), log=job.logs.append)
            job.status = "完成"
        except Exception as e:
            job.logs.append(f"❌ {e}")
            job.status = "失败"


@app.get("/")
def index() -> HTMLResponse:
    notes_dir = config_from_env().out_dir
    notes = sorted(notes_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    jobs = "".join(
        f'<li><a href="/jobs/{j.id}">{html.escape(j.video)}</a> — {j.status}</li>'
        for j in reversed(JOBS.values())
    )
    items = "".join(
        f'<li><a href="/notes/{html.escape(n.name)}">{html.escape(n.stem)}</a></li>' for n in notes
    )
    return page(
        '<h1>B 站视频 → 知识笔记</h1><form method="post" action="/jobs">'
        '<input name="video" placeholder="粘贴 BV 号或视频链接" required autofocus>'
        "<button>生成笔记</button></form>"
        + (f"<h2>任务</h2><ul>{jobs}</ul>" if jobs else "")
        + f"<h2>已有笔记</h2><ul>{items or '<li>还没有</li>'}</ul>"
    )


@app.post("/jobs")
def create_job(video: str = Form(...)) -> RedirectResponse:
    job = Job(id=uuid.uuid4().hex[:8], video=video.strip())
    JOBS[job.id] = job
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return RedirectResponse(f"/jobs/{job.id}", status_code=303)


@app.get("/jobs/{job_id}")
def job_page(job_id: str) -> HTMLResponse:
    job = JOBS.get(job_id) or _not_found()
    refresh = '<meta http-equiv="refresh" content="5">' if job.status in ("排队中", "处理中") else ""
    link = f'<p><a href="/notes/{html.escape(job.note.name)}">打开笔记 →</a></p>' if job.note else ""
    return page(
        f'{refresh}<p><a href="/">← 返回</a></p><h2>{html.escape(job.video)}</h2>'
        f"<p>状态：{job.status}</p>{link}<pre>{html.escape(chr(10).join(job.logs))}</pre>"
    )


@app.get("/notes/{name}")
def note_page(name: str) -> HTMLResponse:
    path = _note_path(name)
    md = path.read_text(encoding="utf-8").split("---", 2)[-1]  # 去掉 frontmatter
    # 用 marked 在浏览器端渲染 Markdown
    return page(
        f'<p><a href="/">← 返回</a> · <a href="/notes/{html.escape(name)}/raw">下载 .md</a></p>'
        '<div id="c"></div><script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>'
        f"<script>document.getElementById('c').innerHTML=marked.parse({_js_string(md)});</script>"
    )


@app.get("/notes/{name}/raw")
def note_raw(name: str) -> PlainTextResponse:
    return PlainTextResponse(_note_path(name).read_text(encoding="utf-8"), media_type="text/markdown")


def _note_path(name: str) -> Path:
    notes_dir = config_from_env().out_dir.resolve()
    path = (notes_dir / name).resolve()
    if path.parent != notes_dir or path.suffix != ".md" or not path.exists():
        _not_found()
    return path


def _js_string(s: str) -> str:
    import json

    # 防止笔记内容里的 </script> 提前结束脚本
    return json.dumps(s).replace("</", "<\\/")


def _not_found():
    raise HTTPException(404)


def run(host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)
