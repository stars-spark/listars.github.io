"""处理一条视频：取信息 → 取逐字稿 → 生成笔记 → 存入知识库并导出 Markdown。

刻意把两步分开：
- 获取内容（fetch_transcript）：需要 B 站 Cookie，只产出纯文本逐字稿；
- 处理内容（make_note）：只接收文字，不接触账号。
以后做托管版时，第一步可以放到用户自己的浏览器插件 / 本地程序里完成，服务器只收文字。
"""

from __future__ import annotations

import datetime as dt
import re
import tempfile
from pathlib import Path

from .bilibili import BiliClient, BiliError, Segment, VideoInfo
from .config import LLMConfig, Settings
from .store import Store, Video
from .summarize import SYSTEM_PROMPT, format_timestamp, summarize

_TS = r"(?:\d{1,2}:)?\d{1,2}:\d{2}"
# [12:34] 或时间范围 [12:34–15:00]，后面紧跟 ( 的说明已经是链接
TIMESTAMP_RE = re.compile(rf"\[({_TS})(\s*[-–—~至]\s*{_TS})?\](?!\()")


def merge_segments(segments: list[Segment], window: float = 30.0, max_chars: int = 240) -> list[str]:
    """把细碎的字幕行合并成约 30 秒一段，每段带一个时间戳，省 token 也方便引用。"""
    lines: list[str] = []
    buf: list[str] = []
    start = 0.0
    for seg in segments:
        if not buf:
            start = seg.start
        buf.append(seg.text)
        if seg.end - start >= window or sum(map(len, buf)) >= max_chars:
            lines.append(f"[{format_timestamp(start)}] {' '.join(buf)}")
            buf = []
    if buf:
        lines.append(f"[{format_timestamp(start)}] {' '.join(buf)}")
    return lines


def parse_timestamp(ts: str) -> int:
    total = 0
    for part in ts.split(":"):
        total = total * 60 + int(part)
    return total


def video_url_at(bvid: str, page: int, seconds: int) -> str:
    base = f"https://www.bilibili.com/video/{bvid}"
    return f"{base}?p={page}&t={seconds}" if page > 1 else f"{base}?t={seconds}"


def linkify_timestamps(markdown: str, bvid: str, page: int = 1) -> str:
    """把笔记里的 [12:34] 变成可点击、直接跳到视频该时间点的链接。"""
    return TIMESTAMP_RE.sub(
        lambda m: f"[{m.group(1)}{m.group(2) or ''}]({video_url_at(bvid, page, parse_timestamp(m.group(1)))})",
        markdown,
    )


def safe_filename(name: str, limit: int = 60) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name).strip()
    return name[:limit].rstrip() or "untitled"


# ---- 第一步：获取内容 ---------------------------------------------------------


def fetch_transcript(bili: BiliClient, info: VideoInfo, settings: Settings, log) -> tuple[list[str], str]:
    result = bili.subtitles(info)
    if result:
        segments, lang = result
        log(f"找到字幕：{lang}，共 {len(segments)} 行")
        return merge_segments(segments), f"字幕（{lang}）"

    if not settings.asr_enabled:
        raise BiliError("该视频没有可用字幕，且未开启语音识别（ASR_ENABLED）")
    if not bili.logged_in:
        log("没有拿到字幕（AI 字幕需要登录，建议设置 BILI_COOKIE）。改用语音识别…")
    else:
        log("该视频没有字幕，改用语音识别…")
    urls, audio_seconds = bili.audio_stream(info)
    if info.duration > 60 and audio_seconds and audio_seconds < info.duration * 0.9:
        raise BiliError(
            f"只拿到 {audio_seconds:.0f} 秒的试看片段（全长 {info.duration} 秒）。"
            "充电专属视频需要用已充电账号的 Cookie。"
        )
    from .transcribe import transcribe

    with tempfile.TemporaryDirectory() as tmp:
        audio = bili.download(urls, Path(tmp) / "audio.m4a")
        log(f"音频已下载（{audio.stat().st_size / 1e6:.1f} MB），开始识别…")
        segments = transcribe(audio, settings.whisper_model, hint=info.title, log=log)
    lines = merge_segments(segments)
    if not lines:
        raise BiliError("没有识别出任何语音内容，无法生成笔记")
    return lines, f"语音识别（whisper {settings.whisper_model}）"


# ---- 第二步：处理内容 ---------------------------------------------------------


def make_note(info: VideoInfo, transcript: str, llm: LLMConfig, settings: Settings) -> str:
    system = settings.prompt_file.read_text(encoding="utf-8") if settings.prompt_file else SYSTEM_PROMPT
    body = summarize(info, transcript, llm, system)
    return linkify_timestamps(body, info.bvid, info.page)


def one_line_summary(note: str) -> str:
    """取笔记「一句话概括」下面的第一段，用于推送通知。"""
    m = re.search(r"##\s*一句话概括\s*\n+(.+)", note)
    text = m.group(1) if m else note.strip().splitlines()[0] if note.strip() else ""
    return re.sub(r"\]\([^)]*\)", "]", text).strip()[:200]


def render_markdown_file(video: Video) -> str:
    title = video.display_title.replace('"', "'")
    front = "\n".join([
        "---",
        f'title: "{title}"',
        f"url: {video.url}",
        f'up: "{video.owner.replace(chr(34), chr(39))}"',
        f"duration: {format_timestamp(video.duration)}",
        f"source: {video.transcript_source}",
        f"model: {video.model}",
        f"created: {(video.done_at or dt.date.today().isoformat())[:10]}",
        "---",
    ])
    header = (
        f"# {video.display_title}\n\n> UP 主：{video.owner} ｜ 时长：{format_timestamp(video.duration)}"
        f" ｜ [原视频]({video.url})"
    )
    return f"{front}\n\n{header}\n\n{video.note}\n"


def export_markdown(video: Video, notes_dir: Path) -> Path:
    """每篇笔记同时导出一份 .md，可以直接同步进 Obsidian。"""
    suffix = f" P{video.page}" if video.page > 1 else ""
    path = notes_dir / f"{safe_filename(video.title)}{suffix} [{video.bvid}].md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown_file(video), encoding="utf-8")
    return path


# ---- 串起来 -----------------------------------------------------------------


def process_video(store: Store, video: Video, settings: Settings, llm: LLMConfig, log=print) -> Video:
    from .bilibili import VideoRef

    bili = BiliClient(settings.bili_cookie)
    try:
        info = bili.video_info(VideoRef(bvid=video.bvid, page=video.page))
        store.update_info(
            video.id, title=info.title, part_title=info.part_title, owner=info.owner,
            duration=info.duration, tags="、".join(info.tags),
        )
        log(f"《{info.title}》{' P%d' % info.page if info.page_count > 1 else ''} "
            f"UP：{info.owner} 时长 {format_timestamp(info.duration)}")
        if info.upower_exclusive and not bili.logged_in:
            log("⚠️ 这是充电专属视频，未登录很可能拿不到完整内容。")

        # 收藏夹里的多 P 课程：第一 P 处理时把后面几 P 也加入队列
        if video.source in ("fav", "watchlater") and video.page == 1 and info.page_count > 1:
            last = min(info.page_count, settings.max_parts)
            for p in range(2, last + 1):
                store.add(info.bvid, p, source="parts", title=info.title)
            log(f"这是 {info.page_count} P 的合集，已把 P2–P{last} 加入队列")

        if video.transcript:
            log("使用已保存的逐字稿")
            transcript = video.transcript
        else:
            lines, source = fetch_transcript(bili, info, settings, log)
            transcript = "\n".join(lines)
            store.save_transcript(video.id, source, transcript)
    finally:
        bili.close()

    log(f"逐字稿 {len(transcript)} 字，交给 {llm.provider}/{llm.model} 整理…")
    note = make_note(info, transcript, llm, settings)
    store.finish(video.id, note, f"{llm.provider}/{llm.model}")
    done = store.get(video.id)
    path = export_markdown(done, settings.notes_dir)
    log(f"✅ 笔记已完成：{path.name}")
    return done
