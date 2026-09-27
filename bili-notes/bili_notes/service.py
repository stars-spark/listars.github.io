"""后台服务：定时同步收藏夹 / 稍后再看，逐个处理队列，完成后推送通知。"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time

from . import notify
from .bilibili import BiliClient
from .config import LLMConfig, Settings
from .pipeline import one_line_summary, process_video
from .store import Store

log = logging.getLogger("bili_notes")

SYNC_ERROR_NOTIFY_HOURS = 12  # 同步出错（常见原因：Cookie 过期）时，最多每 12 小时提醒一次


class Service:
    def __init__(self, settings: Settings, llm: LLMConfig, store: Store | None = None):
        self.settings = settings
        self.llm = llm
        self.store = store or Store(settings.db_path)
        self.wake_worker = threading.Event()
        self.wake_sync = threading.Event()
        self.stop = threading.Event()
        self.current: str = ""  # 正在处理的视频，给网页显示
        self.logs: list[str] = []  # 最近的日志，给网页显示

    # ---- 日志 --------------------------------------------------------------

    def log(self, msg: str) -> None:
        log.info(msg)
        stamp = dt.datetime.now().strftime("%m-%d %H:%M:%S")
        self.logs.append(f"{stamp} {msg}")
        del self.logs[:-200]

    # ---- 线程 --------------------------------------------------------------

    def start(self) -> None:
        self.store.reset_processing()
        threading.Thread(target=self._worker_loop, name="worker", daemon=True).start()
        if self.settings.fav_folder or self.settings.sync_watchlater:
            threading.Thread(target=self._sync_loop, name="sync", daemon=True).start()
        else:
            self.log("未配置 BILI_FAV_FOLDER / BILI_SYNC_WATCHLATER，不自动同步，只处理手动添加的视频")

    def _sync_loop(self) -> None:
        while not self.stop.is_set():
            self.sync_once()
            self.wake_sync.wait(self.settings.sync_interval * 60)
            self.wake_sync.clear()

    def _worker_loop(self) -> None:
        while not self.stop.is_set():
            if not self.process_next():
                self.wake_worker.wait(60)
                self.wake_worker.clear()

    # ---- 同步 --------------------------------------------------------------

    def sync_once(self) -> int:
        """把新收藏的视频加入队列，返回新加入的数量。"""
        added = 0
        bili = BiliClient(self.settings.bili_cookie)
        try:
            if self.settings.fav_folder:
                media_id = bili.resolve_folder(self.settings.fav_folder)
                added += self._sync_source(f"fav:{media_id}", "fav", lambda since, limit: bili.folder_items(media_id, since, limit))
            if self.settings.sync_watchlater:
                added += self._sync_source("watchlater", "watchlater", lambda since, limit: bili.watch_later(since)[:limit])
            self.store.kv_set("last_sync", dt.datetime.now().isoformat(timespec="seconds"))
            self.store.kv_set("last_sync_error", "")
        except Exception as e:
            self.log(f"同步失败：{e}")
            self.store.kv_set("last_sync_error", str(e))
            self._notify_sync_error(str(e))
        finally:
            bili.close()
        if added:
            self.log(f"同步到 {added} 个新视频")
            self.wake_worker.set()
        return added

    def _sync_source(self, key: str, source: str, fetch) -> int:
        since_key = f"since:{key}"
        since = int(self.store.kv_get(since_key, "0"))
        # 第一次同步只取最近的几个，避免把一个几百个视频的老收藏夹全部跑一遍
        limit = 200 if since else self.settings.initial_sync_limit
        items = fetch(since, limit)
        added = 0
        for item in reversed(items):  # 先收藏的先处理
            _, new = self.store.add(item.bvid, 1, source=source, title=item.title)
            added += new
        if items:
            self.store.kv_set(since_key, str(max(i.added_at for i in items)))
        elif not since:
            self.store.kv_set(since_key, str(int(time.time())))
        return added

    def _notify_sync_error(self, error: str) -> None:
        last = float(self.store.kv_get("last_sync_error_notified", "0"))
        if time.time() - last > SYNC_ERROR_NOTIFY_HOURS * 3600:
            self.store.kv_set("last_sync_error_notified", str(time.time()))
            notify.send(self.settings, "B站笔记：同步收藏夹失败", f"{error}\n\n如果是登录失效，请更新 BILI_COOKIE。", log=self.log)

    # ---- 处理队列 ----------------------------------------------------------

    def process_next(self) -> bool:
        video = self.store.claim_next()
        if not video:
            return False
        self.current = video.title or video.bvid
        try:
            done = process_video(self.store, video, self.settings, self.llm, log=self.log)
            notify.send(
                self.settings,
                f"笔记已生成：{done.display_title}"[:60],
                one_line_summary(done.note) or done.display_title,
                self.settings.note_url(done.id),
                log=self.log,
            )
        except Exception as e:
            self.log(f"❌ {video.title or video.bvid} 处理失败：{e}")
            self.store.fail(video.id, str(e))
            failed = self.store.get(video.id)
            notify.send(
                self.settings,
                f"笔记生成失败：{failed.display_title}"[:60],
                str(e)[:300],
                self.settings.note_url(video.id),
                log=self.log,
            )
        finally:
            self.current = ""
        time.sleep(3)  # 两个视频之间歇一下，降低触发 B 站风控的概率
        return True
