"""SQLite 知识库：视频、逐字稿、笔记，以及全文搜索。

一个文件就是全部数据，备份只要复制 data/bili-notes.db。
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY,
    bvid TEXT NOT NULL,
    page INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL DEFAULT '',
    part_title TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    duration INTEGER NOT NULL DEFAULT 0,
    tags TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',   -- manual / fav / watchlater / parts
    status TEXT NOT NULL DEFAULT 'pending',  -- pending / processing / done / failed / deleted
    error TEXT NOT NULL DEFAULT '',
    transcript_source TEXT NOT NULL DEFAULT '',
    transcript TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    done_at TEXT,
    UNIQUE (bvid, page)
);
CREATE INDEX IF NOT EXISTS videos_status ON videos (status, id);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# trigram 分词器能直接搜中文子串（需要 SQLite 3.34+），不支持时退回 LIKE
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS videos_fts USING fts5(
    title, owner, note, transcript, content='videos', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS videos_ai AFTER INSERT ON videos BEGIN
    INSERT INTO videos_fts (rowid, title, owner, note, transcript)
    VALUES (new.id, new.title, new.owner, new.note, new.transcript);
END;
CREATE TRIGGER IF NOT EXISTS videos_ad AFTER DELETE ON videos BEGIN
    INSERT INTO videos_fts (videos_fts, rowid, title, owner, note, transcript)
    VALUES ('delete', old.id, old.title, old.owner, old.note, old.transcript);
END;
CREATE TRIGGER IF NOT EXISTS videos_au AFTER UPDATE OF title, owner, note, transcript ON videos BEGIN
    INSERT INTO videos_fts (videos_fts, rowid, title, owner, note, transcript)
    VALUES ('delete', old.id, old.title, old.owner, old.note, old.transcript);
    INSERT INTO videos_fts (rowid, title, owner, note, transcript)
    VALUES (new.id, new.title, new.owner, new.note, new.transcript);
END;
"""

TS_LINE_RE = re.compile(r"^\[((?:\d{1,2}:)?\d{1,2}:\d{2})\]\s*(.*)$")


@dataclass
class Video:
    id: int
    bvid: str
    page: int
    title: str
    part_title: str
    owner: str
    duration: int
    tags: str
    source: str
    status: str
    error: str
    transcript_source: str
    transcript: str
    note: str
    model: str
    added_at: str
    done_at: str | None

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}" + (f"?p={self.page}" if self.page > 1 else "")

    @property
    def display_title(self) -> str:
        title = self.title or self.bvid
        return f"{title} · P{self.page} {self.part_title}".rstrip() if self.page > 1 else title


@dataclass
class Hit:
    video: Video
    snippets: list[tuple[str, str]]  # (时间戳, 文本)，时间戳为空表示来自笔记


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # 写操作串行，避免多线程下 database is locked
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            try:
                c.executescript(FTS_SCHEMA)
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _write(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock, self._conn() as c:
            return c.execute(sql, params)

    # ---- 队列 --------------------------------------------------------------

    def add(self, bvid: str, page: int = 1, source: str = "manual", title: str = "") -> tuple[int, bool]:
        """加入队列。返回 (id, 是否新加入)。已删除的视频手动添加时会恢复。"""
        with self._lock, self._conn() as c:
            row = c.execute("SELECT id, status FROM videos WHERE bvid=? AND page=?", (bvid, page)).fetchone()
            if row:
                if row["status"] == "deleted" and source == "manual":
                    c.execute("UPDATE videos SET status='pending', error='' WHERE id=?", (row["id"],))
                    return row["id"], True
                return row["id"], False
            cur = c.execute(
                "INSERT INTO videos (bvid, page, source, title, added_at) VALUES (?, ?, ?, ?, ?)",
                (bvid, page, source, title, now()),
            )
            return cur.lastrowid, True

    def claim_next(self) -> Video | None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT * FROM videos WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
            if not row:
                return None
            c.execute("UPDATE videos SET status='processing', error='' WHERE id=?", (row["id"],))
            return Video(**{**dict(row), "status": "processing"})

    def reset_processing(self) -> None:
        """服务重启时，把上次没做完的任务放回队列。"""
        self._write("UPDATE videos SET status='pending' WHERE status='processing'")

    def update_info(self, vid: int, *, title: str, part_title: str, owner: str, duration: int, tags: str) -> None:
        self._write(
            "UPDATE videos SET title=?, part_title=?, owner=?, duration=?, tags=? WHERE id=?",
            (title, part_title, owner, duration, tags, vid),
        )

    def save_transcript(self, vid: int, source: str, transcript: str) -> None:
        self._write("UPDATE videos SET transcript_source=?, transcript=? WHERE id=?", (source, transcript, vid))

    def finish(self, vid: int, note: str, model: str) -> None:
        self._write(
            "UPDATE videos SET status='done', note=?, model=?, error='', done_at=? WHERE id=?",
            (note, model, now(), vid),
        )

    def fail(self, vid: int, error: str) -> None:
        self._write("UPDATE videos SET status='failed', error=? WHERE id=?", (error[:1000], vid))

    def requeue(self, vid: int, keep_transcript: bool = True) -> None:
        extra = "" if keep_transcript else ", transcript='', transcript_source=''"
        self._write(f"UPDATE videos SET status='pending', error=''{extra} WHERE id=?", (vid,))

    def delete(self, vid: int) -> None:
        # 保留一条记录并标记删除，这样下次同步收藏夹时不会又被加回来
        self._write(
            "UPDATE videos SET status='deleted', note='', transcript='', error='' WHERE id=?", (vid,)
        )

    # ---- 查询 --------------------------------------------------------------

    def get(self, vid: int) -> Video | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM videos WHERE id=?", (vid,)).fetchone()
        return Video(**dict(row)) if row else None

    def recent(self, limit: int = 50, offset: int = 0) -> list[Video]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM videos WHERE status!='deleted' "
                "ORDER BY CASE status WHEN 'processing' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END, "
                "COALESCE(done_at, added_at) DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [Video(**dict(r)) for r in rows]

    def counts(self) -> dict[str, int]:
        with self._conn() as c:
            rows = c.execute("SELECT status, COUNT(*) n FROM videos GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def search(self, query: str, limit: int = 30) -> list[Hit]:
        terms = [t for t in query.split() if t]
        if not terms:
            return []
        with self._conn() as c:
            if self.fts and all(len(t) >= 3 for t in terms):
                match = " ".join('"' + t.replace('"', '""') + '"' for t in terms)
                rows = c.execute(
                    "SELECT v.* FROM videos_fts f JOIN videos v ON v.id = f.rowid "
                    "WHERE videos_fts MATCH ? AND v.status='done' ORDER BY bm25(videos_fts, 10, 5, 2, 1) LIMIT ?",
                    (match, limit),
                ).fetchall()
            else:
                # 两个字的词（如「明朝」）trigram 搜不到，用 LIKE，个人数据量下足够快
                cond = " AND ".join(["(title || owner || note || transcript) LIKE ?"] * len(terms))
                rows = c.execute(
                    f"SELECT * FROM videos WHERE status='done' AND {cond} ORDER BY done_at DESC LIMIT ?",
                    [f"%{t}%" for t in terms] + [limit],
                ).fetchall()
        return [Hit(v, find_snippets(v, terms)) for v in (Video(**dict(r)) for r in rows)]

    # ---- 键值状态 ----------------------------------------------------------

    def kv_get(self, key: str, default: str = "") -> str:
        with self._conn() as c:
            row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        self._write("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def find_snippets(video: Video, terms: list[str], limit: int = 3) -> list[tuple[str, str]]:
    """从逐字稿里找出命中的段落（带时间戳），不够再从笔记里补。"""
    out: list[tuple[str, str]] = []
    for line in video.transcript.splitlines():
        m = TS_LINE_RE.match(line)
        if m and any(t in m.group(2) for t in terms):
            out.append((m.group(1), _clip(m.group(2), terms)))
            if len(out) >= limit:
                return out
    for line in video.note.splitlines():
        text = re.sub(r"\]\([^)]*\)", "]", line).strip("#-*> ").strip()  # 去掉链接地址
        if text and any(t in text for t in terms):
            out.append(("", _clip(text, terms)))
            if len(out) >= limit:
                break
    return out


def _clip(text: str, terms: list[str], width: int = 60) -> str:
    pos = min((text.find(t) for t in terms if t in text), default=0)
    start = max(0, pos - width // 2)
    snippet = text[start : start + width + 20]
    return ("…" if start else "") + snippet + ("…" if start + width + 20 < len(text) else "")
