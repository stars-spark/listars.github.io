"""命令行入口：

    bili-notes BV1xx411c7mD https://www.bilibili.com/video/BV...?p=2
    bili-notes --watchlater          # 处理「稍后再看」列表
    bili-notes serve                 # 启动本地网页服务
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .bilibili import BiliClient
from .pipeline import Config, process
from .summarize import DEFAULT_MODEL


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def config_from_env(**overrides) -> Config:
    cfg = Config(
        out_dir=Path(os.environ.get("BILI_NOTES_DIR", "notes")),
        cookie=os.environ.get("BILI_COOKIE") or None,
        model=os.environ.get("BILI_NOTES_MODEL", DEFAULT_MODEL),
        effort=os.environ.get("BILI_NOTES_EFFORT", "high"),
        whisper_model=os.environ.get("BILI_NOTES_WHISPER", "large-v3-turbo"),
    )
    for k, v in overrides.items():
        if v is not None:
            setattr(cfg, k, v)
    return cfg


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    argv = sys.argv[1:] if argv is None else argv

    if argv[:1] == ["serve"]:
        p = argparse.ArgumentParser(prog="bili-notes serve")
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=8765)
        a = p.parse_args(argv[1:])
        from .web import run

        run(a.host, a.port)
        return 0

    p = argparse.ArgumentParser(prog="bili-notes", description="把 B 站视频整理成知识笔记")
    p.add_argument("videos", nargs="*", help="BV 号、av 号、视频链接或 b23.tv 短链")
    p.add_argument("--watchlater", action="store_true", help="处理「稍后再看」里的全部视频（需要 Cookie）")
    p.add_argument("-o", "--out-dir", type=Path)
    p.add_argument("--model", help=f"Claude 模型，默认 {DEFAULT_MODEL}")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--whisper-model", help="无字幕时使用的 Whisper 模型，如 small / medium / large-v3-turbo")
    p.add_argument("--prompt-file", type=Path, help="自定义整理要求（替换默认系统提示词）")
    p.add_argument("--force", action="store_true", help="忽略缓存，重新获取逐字稿")
    a = p.parse_args(argv)

    cfg = config_from_env(
        out_dir=a.out_dir, model=a.model, effort=a.effort,
        whisper_model=a.whisper_model, prompt_file=a.prompt_file, force=a.force or None,
    )
    bili = BiliClient(cfg.cookie)
    try:
        videos = list(a.videos)
        if a.watchlater:
            videos += bili.watch_later()
        if not videos:
            p.error("请提供至少一个视频，或使用 --watchlater")

        failed = 0
        for i, v in enumerate(videos, 1):
            print(f"\n[{i}/{len(videos)}] {v}")
            try:
                process(v, cfg, bili)
            except Exception as e:  # 批量处理时单个失败不影响其余
                failed += 1
                print(f"❌ 失败：{e}", file=sys.stderr)
        return 1 if failed else 0
    finally:
        bili.close()


if __name__ == "__main__":
    sys.exit(main())
