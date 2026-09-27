"""推送通知：Bark（iPhone）、Server 酱 / PushPlus（微信）。配了哪个就推哪个。"""

from __future__ import annotations

import re

import httpx

from .config import Settings


def channels(settings: Settings) -> list[str]:
    return [
        name
        for name, key in (
            ("Bark", settings.bark_key),
            ("Server酱", settings.serverchan_key),
            ("PushPlus", settings.pushplus_token),
        )
        if key
    ]


def send(settings: Settings, title: str, body: str, url: str = "", log=print,
         http: httpx.Client | None = None) -> int:
    """发送到所有已配置的渠道，返回成功的个数。单个渠道失败只记日志。"""
    own = http is None
    http = http or httpx.Client(timeout=15)
    ok = 0
    try:
        if settings.bark_key:
            ok += _try(log, "Bark", lambda: _bark(http, settings, title, body, url))
        if settings.serverchan_key:
            ok += _try(log, "Server酱", lambda: _serverchan(http, settings.serverchan_key, title, body, url))
        if settings.pushplus_token:
            ok += _try(log, "PushPlus", lambda: _pushplus(http, settings.pushplus_token, title, body, url))
    finally:
        if own:
            http.close()
    return ok


def _try(log, name: str, fn) -> int:
    try:
        fn()
        return 1
    except Exception as e:
        log(f"{name} 推送失败：{e}")
        return 0


def _check(resp: httpx.Response, ok_field: str = "code", ok_value=0) -> None:
    resp.raise_for_status()
    data = resp.json()
    if data.get(ok_field) not in (ok_value, 200):
        raise RuntimeError(data.get("message") or data.get("msg") or str(data)[:200])


def _bark(http, settings: Settings, title: str, body: str, url: str) -> None:
    payload = {"device_key": settings.bark_key, "title": title, "body": body, "group": "B站笔记"}
    if url:
        payload["url"] = url
    _check(http.post(f"{settings.bark_server}/push", json=payload))


def _serverchan(http, key: str, title: str, body: str, url: str) -> None:
    # Server 酱³ 的 SendKey 以 sctp<uid>t 开头，接口地址不同
    if m := re.match(r"sctp(\d+)t", key):
        api = f"https://{m.group(1)}.push.ft07.com/send/{key}.send"
    else:
        api = f"https://sctapi.ftqq.com/{key}.send"
    desp = body + (f"\n\n[打开笔记]({url})" if url else "")
    _check(http.post(api, data={"title": title[:32], "desp": desp}))


def _pushplus(http, token: str, title: str, body: str, url: str) -> None:
    content = body + (f"\n\n[打开笔记]({url})" if url else "")
    _check(http.post(
        "https://www.pushplus.plus/send",
        json={"token": token, "title": title, "content": content, "template": "markdown"},
    ), ok_value=200)
