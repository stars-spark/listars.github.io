"""完整流程：链接 → 字幕/语音识别 → 逐字稿 → Claude 笔记 → Markdown 文件。"""

from __future__ import annotations

import datetime as dt
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .bilibili import BiliClient, BiliError, Segment, VideoInfo, parse_ref
from .summarize import DEFAULT_MODEL, SYSTEM_PROMPT, format_timestamp, summarize

TIMESTAMP_RE = re.compile(r"\[((?:\d{1,2}:)?\d{1,2}:\d{2})\](?!\()")


@dataclass
class Config:
    out_dir: Path = Path("notes")
    cookie: str | None = None
    model: str = DEFAULT_MODEL
    effort: str = "high"
    whisper_model: str = "large-v3-turbo"
    prompt_file: Path | None = None
    force: bool = False  # 忽略缓存的逐字稿，重新获取


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


def linkify_timestamps(markdown: str, info: VideoInfo) -> str:
    """把笔记里的 [12:34] 变成可点击、直接跳到视频该时间点的链接。"""
    return TIMESTAMP_RE.sub(
        lambda m: f"[{m.group(1)}]({info.url_at(parse_timestamp(m.group(1)))})", markdown
    )


def safe_filename(name: str, limit: int = 60) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", name).strip()
    return name[:limit].rstrip() or "untitled"


def render_note(info: VideoInfo, body: str, source: str, model: str) -> str:
    title = info.title + (f" · {info.part_title}" if info.part_title else "")
    front = "\n".join([
        "---",
        f'title: "{title.replace(chr(34), chr(39))}"',
        f"url: {info.url}",
        f'up: "{info.owner.replace(chr(34), chr(39))}"',
        f"duration: {format_timestamp(info.duration)}",
        f"source: {source}",
        f"model: {model}",
        f"created: {dt.date.today().isoformat()}",
        "---",
    ])
    header = f"# {title}\n\n> UP 主：{info.owner} ｜ 时长：{format_timestamp(info.duration)} ｜ [原视频]({info.url})"
    return f"{front}\n\n{header}\n\n{linkify_timestamps(body, info)}\n"


def get_transcript(bili: BiliClient, info: VideoInfo, cfg: Config, log) -> tuple[list[str], str]:
    cache = cfg.out_dir / ".transcripts" / f"{info.bvid}_p{info.page}.txt"
    if cache.exists() and not cfg.force:
        lines = cache.read_text(encoding="utf-8").splitlines()
        if len(lines) > 1:
            log(f"使用缓存的逐字稿：{cache}")
            return lines[1:], lines[0].removeprefix("# source: ")

    result = bili.subtitles(info)
    if result:
        segments, lang = result
        source = f"字幕（{lang}）"
        log(f"找到字幕：{lang}，共 {len(segments)} 行")
    else:
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
            segments = transcribe(audio, cfg.whisper_model, hint=info.title, log=log)
        source = f"语音识别（whisper {cfg.whisper_model}）"

    lines = merge_segments(segments)
    if not lines:
        raise BiliError("没有识别出任何语音内容，无法生成笔记")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(f"# source: {source}\n" + "\n".join(lines), encoding="utf-8")
    return lines, source


def process(ref_text: str, cfg: Config, bili: BiliClient | None = None, log=print) -> Path:
    own_client = bili is None
    bili = bili or BiliClient(cfg.cookie)
    try:
        info = bili.video_info(parse_ref(ref_text, bili.http))
        log(f"《{info.title}》 UP：{info.owner} 时长 {format_timestamp(info.duration)}")
        if info.upower_exclusive and not bili.logged_in:
            log("⚠️ 这是充电专属视频，未登录很可能拿不到完整内容。")

        lines, source = get_transcript(bili, info, cfg, log)

        system = cfg.prompt_file.read_text(encoding="utf-8") if cfg.prompt_file else SYSTEM_PROMPT
        log(f"逐字稿 {sum(map(len, lines))} 字，交给 {cfg.model} 整理…")
        body = summarize(info, "\n".join(lines), cfg.model, cfg.effort, system)

        suffix = f" P{info.page}" if info.page > 1 else ""
        path = cfg.out_dir / f"{safe_filename(info.title)}{suffix} [{info.bvid}].md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_note(info, body, source, cfg.model), encoding="utf-8")
        log(f"✅ 笔记已保存：{path}")
        return path
    finally:
        if own_client:
            bili.close()
