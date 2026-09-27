"""命令行入口：

    bili-notes serve                 # 启动网页 + 后台同步（部署到服务器用这个）
    bili-notes add BV1xx411c7mD ...  # 立即整理指定视频
    bili-notes sync                  # 同步一次收藏夹并处理完队列
    bili-notes folders               # 列出自己的收藏夹，方便填 BILI_FAV_FOLDER
    bili-notes search 张居正          # 在知识库里搜索
"""

from __future__ import annotations

import argparse
import sys

from .config import LLMConfig, Settings, load_dotenv


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(prog="bili-notes", description="把 B 站视频整理成个人知识库")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="启动网页和后台同步")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)

    a = sub.add_parser("add", help="立即整理指定视频")
    a.add_argument("videos", nargs="+", help="BV 号、链接、b23.tv 短链或 B 站分享文字")

    sub.add_parser("sync", help="同步一次收藏夹，并处理完队列")
    sub.add_parser("folders", help="列出自己的收藏夹")

    q = sub.add_parser("search", help="搜索知识库")
    q.add_argument("query", nargs="+")

    args = p.parse_args(argv)

    if args.cmd == "serve":
        from .web import run

        run(args.host, args.port)
        return 0

    settings = Settings.from_env()

    if args.cmd == "folders":
        from .bilibili import BiliClient

        bili = BiliClient(settings.bili_cookie)
        try:
            for f in bili.my_folders():
                print(f"{f['id']:>12}  {f['title']}（{f['media_count']} 个）")
        finally:
            bili.close()
        return 0

    from .service import Service

    service = Service(settings, LLMConfig.from_env())
    service.log = print  # 命令行下直接打印

    if args.cmd == "search":
        for hit in service.store.search(" ".join(args.query)):
            print(f"\n#{hit.video.id} {hit.video.display_title}（{hit.video.owner}）")
            for ts, text in hit.snippets:
                print(f"   [{ts or '笔记'}] {text}")
        return 0

    if args.cmd == "add":
        from .web import resolve_bvid

        for text in args.videos:
            try:
                bvid, page = resolve_bvid(text, settings.bili_cookie)
            except Exception as e:
                print(f"❌ {text}：{e}", file=sys.stderr)
                continue
            vid, new = service.store.add(bvid, page, source="manual")
            if not new and (v := service.store.get(vid)) and v.status in ("done", "failed"):
                service.store.requeue(vid)  # 命令行里明确要求整理，已有的就重新生成
    elif args.cmd == "sync":
        service.sync_once()

    failed_before = service.store.counts().get("failed", 0)
    while service.process_next():
        pass
    return 1 if service.store.counts().get("failed", 0) > failed_before else 0


if __name__ == "__main__":
    sys.exit(main())
