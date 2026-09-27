"""B 站接口封装：解析链接、WBI 签名、视频信息、字幕、音频流下载。

只使用你自己账号的 Cookie（SESSDATA）访问你本来就有权限观看的内容。
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# WBI 签名的固定置换表（来自 B 站前端，社区文档 bilibili-API-collect 有记录）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
AV_RE = re.compile(r"(?:^|[/=])av(\d+)", re.IGNORECASE)


class BiliError(RuntimeError):
    pass


@dataclass
class Segment:
    start: float  # 秒
    end: float
    text: str


@dataclass
class VideoRef:
    bvid: str | None = None
    aid: int | None = None
    page: int = 1  # 分 P，从 1 开始


@dataclass
class VideoInfo:
    bvid: str
    aid: int
    cid: int
    page: int
    title: str
    part_title: str
    owner: str
    duration: int  # 当前分 P 的时长（秒）
    desc: str
    tags: list[str] = field(default_factory=list)
    upower_exclusive: bool = False

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}" + (
            f"?p={self.page}" if self.page > 1 else ""
        )

    def url_at(self, seconds: int) -> str:
        sep = "&" if "?" in self.url else "?"
        return f"{self.url}{sep}t={seconds}"


def parse_ref(text: str, client: httpx.Client | None = None) -> VideoRef:
    """接受 BV 号 / av 号 / 完整链接 / b23.tv 短链。"""
    text = text.strip()
    if "b23.tv" in text:
        if client is None:
            raise BiliError("解析 b23.tv 短链需要网络客户端")
        url = text if text.startswith("http") else "https://" + text
        text = str(client.get(url, follow_redirects=True).url)

    page = 1
    if "?" in text:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(text).query)
        if query.get("p", [""])[0].isdigit():
            page = int(query["p"][0])

    if m := BV_RE.search(text):
        return VideoRef(bvid=m.group(1), page=page)
    if m := AV_RE.search(text):
        return VideoRef(aid=int(m.group(1)), page=page)
    raise BiliError(f"无法识别的视频链接或编号：{text}")


def mixin_key(img_key: str, sub_key: str) -> str:
    orig = img_key + sub_key
    return "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def wbi_sign(params: dict, key: str, wts: int | None = None) -> dict:
    """给请求参数加上 wts 与 w_rid。"""
    params = dict(params, wts=wts if wts is not None else int(time.time()))
    cleaned = {
        k: "".join(ch for ch in str(v) if ch not in "!'()*")
        for k, v in sorted(params.items())
    }
    query = urllib.parse.urlencode(cleaned)
    cleaned["w_rid"] = hashlib.md5((query + key).encode()).hexdigest()
    return cleaned


def parse_cookie_string(cookie: str) -> dict[str, str]:
    out = {}
    for part in cookie.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class BiliClient:
    def __init__(self, cookie: str | None = None, timeout: float = 20.0):
        self.http = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"},
            timeout=timeout,
        )
        if cookie:
            # 既支持只填 SESSDATA 的值，也支持整段 Cookie 字符串
            pairs = parse_cookie_string(cookie) if "=" in cookie else {"SESSDATA": cookie}
            for k, v in pairs.items():
                self.http.cookies.set(k, v, domain=".bilibili.com")
        self._wbi_key: str | None = None
        self.logged_in = False
        self._ensure_buvid()

    def close(self) -> None:
        self.http.close()

    # ---- 基础请求 ----------------------------------------------------------

    def _ensure_buvid(self) -> None:
        # 没有 buvid3 时很多接口会触发风控（412 / -352）
        if self.http.cookies.get("buvid3", domain=".bilibili.com"):
            return
        try:
            data = self._get_json("https://api.bilibili.com/x/frontend/finger/spi")
            self.http.cookies.set("buvid3", data["b_3"], domain=".bilibili.com")
            self.http.cookies.set("buvid4", data["b_4"], domain=".bilibili.com")
        except (BiliError, httpx.HTTPError, KeyError):
            pass

    def _get_json(self, url: str, params: dict | None = None, sign: bool = False):
        if sign:
            params = wbi_sign(params or {}, self._get_wbi_key())
        resp = self.http.get(url, params=params)
        if resp.status_code == 412:
            raise BiliError("被 B 站风控拦截（HTTP 412），请稍后再试或填写 Cookie")
        resp.raise_for_status()
        body = resp.json()
        code = body.get("code", 0)
        if code == -101 and "nav" in url:
            return body.get("data")  # 未登录时 nav 仍会返回 wbi_img
        if code != 0:
            raise BiliError(f"B 站接口返回错误 {code}：{body.get('message')}（{url}）")
        return body.get("data")

    def _get_wbi_key(self) -> str:
        if self._wbi_key is None:
            nav = self._get_json("https://api.bilibili.com/x/web-interface/nav")
            self.logged_in = bool(nav.get("isLogin"))
            stem = lambda u: u.rsplit("/", 1)[-1].split(".")[0]  # noqa: E731
            img = nav["wbi_img"]
            self._wbi_key = mixin_key(stem(img["img_url"]), stem(img["sub_url"]))
        return self._wbi_key

    # ---- 视频信息 ----------------------------------------------------------

    def video_info(self, ref: VideoRef) -> VideoInfo:
        params = {"bvid": ref.bvid} if ref.bvid else {"aid": ref.aid}
        data = self._get_json(
            "https://api.bilibili.com/x/web-interface/wbi/view", params, sign=True
        )
        pages = data.get("pages") or []
        if not 1 <= ref.page <= max(len(pages), 1):
            raise BiliError(f"该视频只有 {len(pages)} P，没有第 {ref.page} P")
        page = pages[ref.page - 1] if pages else {"cid": data["cid"], "part": "", "duration": data["duration"]}

        tags: list[str] = []
        try:
            tag_data = self._get_json(
                "https://api.bilibili.com/x/tag/archive/tags", {"bvid": data["bvid"]}
            )
            tags = [t["tag_name"] for t in tag_data or []]
        except (BiliError, httpx.HTTPError):
            pass

        return VideoInfo(
            bvid=data["bvid"],
            aid=data["aid"],
            cid=page["cid"],
            page=ref.page,
            title=data["title"],
            part_title=page.get("part", "") if len(pages) > 1 else "",
            owner=data["owner"]["name"],
            duration=int(page.get("duration") or data["duration"]),
            desc=data.get("desc", ""),
            tags=tags,
            upower_exclusive=bool(data.get("is_upower_exclusive")),
        )

    # ---- 字幕 -------------------------------------------------------------

    def subtitles(self, info: VideoInfo) -> tuple[list[Segment], str] | None:
        """优先人工中文字幕，其次 AI 中文字幕。返回 (字幕段, 语言描述)。

        AI 字幕需要登录后才能拿到。
        """
        data = self._get_json(
            "https://api.bilibili.com/x/player/wbi/v2",
            {"aid": info.aid, "cid": info.cid},
            sign=True,
        )
        subs = [s for s in (data.get("subtitle") or {}).get("subtitles") or [] if s.get("subtitle_url")]
        if not subs:
            return None
        chosen = pick_subtitle(subs)
        url = chosen["subtitle_url"]
        if url.startswith("//"):
            url = "https:" + url
        body = self.http.get(url).json().get("body") or []
        segments = subtitle_json_to_segments(body)
        if not segments:
            return None
        return segments, chosen.get("lan_doc") or chosen.get("lan", "")

    # ---- 音频 -------------------------------------------------------------

    def audio_stream(self, info: VideoInfo) -> tuple[list[str], float]:
        """返回最小码率音频流的候选地址（主地址 + 备用）以及音频时长。"""
        data = self._get_json(
            "https://api.bilibili.com/x/player/wbi/playurl",
            {"bvid": info.bvid, "cid": info.cid, "fnval": 16, "fnver": 0, "fourk": 0},
            sign=True,
        )
        dash = data.get("dash") or {}
        audios = dash.get("audio") or []
        if not audios:
            raise BiliError("没有拿到音频流（可能需要登录或充电后才能观看）")
        # 语音识别不需要高码率，挑最小的省流量
        best = min(audios, key=lambda a: a.get("bandwidth", 0))
        urls = [best.get("baseUrl") or best.get("base_url")]
        urls += best.get("backupUrl") or best.get("backup_url") or []
        return [u for u in urls if u], float(dash.get("duration") or 0)

    def download(self, urls: list[str], dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        last_err: Exception | None = None
        for url in urls:
            tmp = dest.with_suffix(dest.suffix + ".part")
            try:
                with self.http.stream("GET", url, timeout=60) as resp:
                    resp.raise_for_status()
                    with tmp.open("wb") as f:
                        for chunk in resp.iter_bytes(1 << 16):
                            f.write(chunk)
                tmp.replace(dest)
                return dest
            except httpx.HTTPError as e:
                last_err = e
                tmp.unlink(missing_ok=True)
        raise BiliError(f"音频下载失败：{last_err}")

    # ---- 稍后再看 ----------------------------------------------------------

    def watch_later(self) -> list[str]:
        data = self._get_json("https://api.bilibili.com/x/v2/history/toview")
        return [item["bvid"] for item in (data or {}).get("list") or []]


def pick_subtitle(subs: list[dict]) -> dict:
    def rank(s: dict) -> int:
        lan = (s.get("lan") or "").lower()
        if lan.startswith("zh") or lan in ("cmn-hans", "cmn-hant"):
            return 0  # 人工中文字幕
        if lan.startswith("ai-zh"):
            return 1  # AI 中文字幕
        if lan.startswith("ai-"):
            return 3
        return 2

    return min(subs, key=rank)


def subtitle_json_to_segments(body: list[dict]) -> list[Segment]:
    return [
        Segment(float(item["from"]), float(item["to"]), item["content"].strip())
        for item in body
        if item.get("content", "").strip()
    ]
