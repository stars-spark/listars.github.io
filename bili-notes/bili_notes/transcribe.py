"""没有字幕时，用本地 faster-whisper 把音频转成文字。"""

from __future__ import annotations

from pathlib import Path

from .bilibili import Segment


def transcribe(
    audio: Path,
    model_size: str = "small",
    hint: str = "",
    log=print,
) -> list[Segment]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:  # pragma: no cover - 依赖提示
        raise RuntimeError(
            "该视频没有字幕，需要语音识别。请先安装：pip install 'bili-notes[asr]'"
        ) from e

    log(f"加载 Whisper 模型 {model_size}（首次运行会自动下载）…")
    # auto：有 NVIDIA 显卡用显卡，否则在 CPU 上用 int8，内存占用和速度都更友好
    model = WhisperModel(model_size, device="auto", compute_type="auto")
    # initial_prompt 能让输出偏向简体中文，并帮助识别标题里的专有名词
    prompt = "以下是普通话的讲解，使用简体中文和标点符号。" + (f"主题：{hint}" if hint else "")
    segments, info = model.transcribe(
        str(audio),
        language="zh",
        initial_prompt=prompt,
        vad_filter=True,
        beam_size=5,
    )
    out: list[Segment] = []
    for seg in segments:
        out.append(Segment(seg.start, seg.end, seg.text.strip()))
        if len(out) % 50 == 0:
            log(f"  已识别到 {seg.end / 60:.1f} / {info.duration / 60:.1f} 分钟")
    return out
