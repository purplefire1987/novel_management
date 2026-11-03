"""SQLite 索引层：可随时从 Markdown 重建，不是第二套事实源（PRD 8）。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entities (
    id            TEXT NOT NULL,
    type          TEXT NOT NULL,
    name          TEXT,
    aliases       TEXT,
    canon_status  TEXT,
    file_path     TEXT,
    updated_at    TEXT,
    tags          TEXT,
    summary       TEXT,
    content_hash  TEXT,
    PRIMARY KEY (id, type)
);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

CREATE TABLE IF NOT EXISTS relationships (
    id            TEXT PRIMARY KEY,
    source_id     TEXT,
    target_id     TEXT,
    relation_type TEXT,
    strength      INTEGER DEFAULT 0,
    valid_from    TEXT,
    valid_to      TEXT,
    source_event  TEXT,
    file_path     TEXT
);
CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_id);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_id);

CREATE TABLE IF NOT EXISTS events (
    event_id      TEXT PRIMARY KEY,
    chapter_id    TEXT,
    sequence      INTEGER,
    name          TEXT,
    location_id   TEXT,
    participants  TEXT,
    source_refs   TEXT,
    file_path     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_chapter ON events(chapter_id);

CREATE TABLE IF NOT EXISTS foreshadowing (
    id                 TEXT PRIMARY KEY,
    name               TEXT,
    status             TEXT,
    planted_in         TEXT,
    target_start       TEXT,
    target_end         TEXT,
    importance         TEXT,
    trigger_conditions TEXT,
    file_path          TEXT
);
CREATE INDEX IF NOT EXISTS idx_fs_status ON foreshadowing(status);

CREATE TABLE IF NOT EXISTS chapter_index (
    chapter_id      TEXT PRIMARY KEY,
    title           TEXT,
    outline_path    TEXT,
    manuscript_path TEXT,
    status          TEXT,
    word_count      INTEGER DEFAULT 0,
    content_hash    TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    operation_id TEXT PRIMARY KEY,
    timestamp    TEXT,
    actor        TEXT,
    agent        TEXT,
    operation    TEXT,
    target       TEXT,
    target_type  TEXT,
    before_hash  TEXT,
    after_hash   TEXT,
    status       TEXT,
    detail       TEXT,
    backup_path  TEXT
);

CREATE TABLE IF NOT EXISTS files (
    path       TEXT PRIMARY KEY,
    hash       TEXT,
    indexed_at TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_chapters USING fts5(
    chapter_id UNINDEXED,
    title,
    body
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def bigrams(text: str) -> str:
    """中文没有空格，用二元字组建 FTS 索引，支持子串检索。"""
    clean = "".join(ch if not ch.isspace() else " " for ch in text)
    grams: list[str] = []
    for chunk in clean.split(" "):
        if not chunk:
            continue
        if len(chunk) == 1:
            grams.append(chunk)
            continue
        for i in range(len(chunk) - 1):
            grams.append(chunk[i : i + 2])
    return " ".join(grams)


def loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class Index:
    """索引库句柄。所有写入都来自 indexer，不被 Agent 直接调用。"""

    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    # ------------------------------------------------------------ 生命周期
    @classmethod
    def open(cls, db_path: Path, create: bool = False) -> "Index":
        if create:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        if not db_path.exists() and not create:
            idx = cls(db_path)
            idx.init_schema()
            return idx
        idx = cls(db_path)
        idx.init_schema()
        return idx

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Index":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------ 实体
    def clear_entities_for_path(self, rel_path: str) -> None:
        self.conn.execute("DELETE FROM entities WHERE file_path = ?", (rel_path,))

    def upsert_entity(self, row: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO entities (id, type, name, aliases, canon_status, file_path,
                                  updated_at, tags, summary, content_hash)
            VALUES (:id, :type, :name, :aliases, :canon_status, :file_path, :updated_at,
                    :tags, :summary, :content_hash)
            ON CONFLICT(id, type) DO UPDATE SET
                name=excluded.name, aliases=excluded.aliases, canon_status=excluded.canon_status,
                file_path=excluded.file_path, updated_at=excluded.updated_at,
                tags=excluded.tags, summary=excluded.summary, content_hash=excluded.content_hash
            """,
            row,
        )

    def get_entity(self, doc_id: str, doc_type: str | None = None) -> dict[str, Any] | None:
        if doc_type:
            cur = self.conn.execute(
                "SELECT * FROM entities WHERE id=? AND type=?", (doc_id, doc_type)
            )
        else:
            cur = self.conn.execute("SELECT * FROM entities WHERE id=?", (doc_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def entities_by_type(self, doc_type: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM entities WHERE type=? ORDER BY id", (doc_type,)
        )
        return [dict(r) for r in cur.fetchall()]

    def all_entities(self) -> list[dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM entities ORDER BY type, id")
        return [dict(r) for r in cur.fetchall()]

    def exists(self, doc_id: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM entities WHERE id=? LIMIT 1", (doc_id,))
        return cur.fetchone() is not None

    def find_by_name(self, name: str) -> list[dict[str, Any]]:
        needle = name.strip()
        if not needle:
            return []
        cur = self.conn.execute(
            "SELECT * FROM entities WHERE name=? OR aliases LIKE ?",
            (needle, f'%"{needle}"%'),
        )
        return [dict(r) for r in cur.fetchall()]

    # ------------------------------------------------------------ 关系 / 事件
    def upsert_relationship(self, row: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO relationships (id, source_id, target_id, relation_type, strength,
                                       valid_from, valid_to, source_event, file_path)
            VALUES (:id, :source_id, :target_id, :relation_type, :strength,
                    :valid_from, :valid_to, :source_event, :file_path)
            ON CONFLICT(id) DO UPDATE SET
                source_id=excluded.source_id, target_id=excluded.target_id,
                relation_type=excluded.relation_type, strength=excluded.strength,
                valid_from=excluded.valid_from, valid_to=excluded.valid_to,
                source_event=excluded.source_event, file_path=excluded.file_path
            """,
            row,
        )

    def relationships_of(self, doc_id: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM relationships WHERE source_id=? OR target_id=? ORDER BY id",
            (doc_id, doc_id),
        )
        return [dict(r) for r in cur.fetchall()]

    def upsert_event(self, row: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO events (event_id, chapter_id, sequence, name, location_id,
                                participants, source_refs, file_path)
            VALUES (:event_id, :chapter_id, :sequence, :name, :location_id,
                    :participants, :source_refs, :file_path)
            ON CONFLICT(event_id) DO UPDATE SET
                chapter_id=excluded.chapter_id, sequence=excluded.sequence, name=excluded.name,
                location_id=excluded.location_id, participants=excluded.participants,
                source_refs=excluded.source_refs, file_path=excluded.file_path
            """,
            row,
        )

    def events_of_chapter(self, chapter_id: str) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM events WHERE chapter_id=? ORDER BY sequence, event_id",
            (chapter_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    def event_exists(self, event_id: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,))
        return cur.fetchone() is not None

    # ------------------------------------------------------------ 伏笔
    def upsert_foreshadowing(self, row: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO foreshadowing (id, name, status, planted_in, target_start,
                                       target_end, importance, trigger_conditions, file_path)
            VALUES (:id, :name, :status, :planted_in, :target_start, :target_end,
                    :importance, :trigger_conditions, :file_path)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, status=excluded.status, planted_in=excluded.planted_in,
                target_start=excluded.target_start, target_end=excluded.target_end,
                importance=excluded.importance,
                trigger_conditions=excluded.trigger_conditions, file_path=excluded.file_path
            """,
            row,
        )

    def foreshadowing_all(self) -> list[dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM foreshadowing ORDER BY id")
        return [dict(r) for r in cur.fetchall()]

    def foreshadowing_get(self, fs_id: str) -> dict[str, Any] | None:
        cur = self.conn.execute("SELECT * FROM foreshadowing WHERE id=?", (fs_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------ 章节
    def upsert_chapter(self, chapter_id: str, **fields: Any) -> None:
        existing = self.get_chapter(chapter_id) or {"chapter_id": chapter_id}
        existing.update({k: v for k, v in fields.items() if v is not None})
        self.conn.execute(
            """
            INSERT INTO chapter_index (chapter_id, title, outline_path, manuscript_path,
                                       status, word_count, content_hash, updated_at)
            VALUES (:chapter_id, :title, :outline_path, :manuscript_path,
                    :status, :word_count, :content_hash, :updated_at)
            ON CONFLICT(chapter_id) DO UPDATE SET
                title=excluded.title, outline_path=excluded.outline_path,
                manuscript_path=excluded.manuscript_path, status=excluded.status,
                word_count=excluded.word_count, content_hash=excluded.content_hash,
                updated_at=excluded.updated_at
            """,
            {
                "chapter_id": chapter_id,
                "title": existing.get("title"),
                "outline_path": existing.get("outline_path"),
                "manuscript_path": existing.get("manuscript_path"),
                "status": existing.get("status") or "outline",
                "word_count": existing.get("word_count") or 0,
                "content_hash": existing.get("content_hash"),
                "updated_at": existing.get("updated_at") or now_iso(),
            },
        )

    def get_chapter(self, chapter_id: str) -> dict[str, Any] | None:
        cur = self.conn.execute(
            "SELECT * FROM chapter_index WHERE chapter_id=?", (chapter_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def all_chapters(self) -> list[dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM chapter_index")
        rows = [dict(r) for r in cur.fetchall()]
        return sorted(rows, key=lambda r: _chapter_sort_key(r["chapter_id"]))

    # ------------------------------------------------------------ 全文检索
    def index_chapter_text(self, chapter_id: str, title: str, body: str) -> None:
        self.conn.execute("DELETE FROM fts_chapters WHERE chapter_id=?", (chapter_id,))
        self.conn.execute(
            "INSERT INTO fts_chapters (chapter_id, title, body) VALUES (?, ?, ?)",
            (chapter_id, bigrams(title), bigrams(body)),
        )

    def search_chapters(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        grams = bigrams(query)
        if not grams:
            return []
        match = " OR ".join(f'"{g}"' for g in dict.fromkeys(grams.split()))
        cur = self.conn.execute(
            """
            SELECT chapter_id, bm25(fts_chapters) AS score
            FROM fts_chapters WHERE fts_chapters MATCH ?
            ORDER BY score LIMIT ?
            """,
            (match, limit),
        )
        out = []
        for row in cur.fetchall():
            chapter = self.get_chapter(row["chapter_id"]) or {}
            out.append(
                {
                    "chapter_id": row["chapter_id"],
                    "title": chapter.get("title"),
                    "score": round(row["score"], 4),
                }
            )
        return out

    # ------------------------------------------------------------ 审计 / 文件
    def log_audit(self, row: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO audit_log (operation_id, timestamp, actor, agent, operation,
                target, target_type, before_hash, after_hash, status, detail, backup_path)
            VALUES (:operation_id, :timestamp, :actor, :agent, :operation, :target,
                    :target_type, :before_hash, :after_hash, :status, :detail, :backup_path)
            """,
            row,
        )

    def get_audit(self, op_id: str) -> dict[str, Any] | None:
        cur = self.conn.execute("SELECT * FROM audit_log WHERE operation_id=?", (op_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def recent_audits(self, limit: int = 20) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC, rowid DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in cur.fetchall()]

    def set_file_hash(self, rel_path: str, digest: str) -> None:
        self.conn.execute(
            "INSERT INTO files (path, hash, indexed_at) VALUES (?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET hash=excluded.hash, indexed_at=excluded.indexed_at",
            (rel_path, digest, now_iso()),
        )

    def get_file_hash(self, rel_path: str) -> str | None:
        cur = self.conn.execute("SELECT hash FROM files WHERE path=?", (rel_path,))
        row = cur.fetchone()
        return row["hash"] if row else None

    def remove_file(self, rel_path: str) -> None:
        self.conn.execute("DELETE FROM files WHERE path=?", (rel_path,))
        self.conn.execute("DELETE FROM entities WHERE file_path=?", (rel_path,))

    def commit(self) -> None:
        self.conn.commit()

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in ("entities", "relationships", "events", "foreshadowing", "chapter_index", "audit_log"):
            cur = self.conn.execute(f"SELECT COUNT(*) AS c FROM {table}")  # noqa: S608 - 固定表名
            out[table] = int(cur.fetchone()["c"])
        return out


def _chapter_sort_key(chapter_id: str) -> tuple[int, str]:
    """数值排序：CH-10 必须排在 CH-2 之后（AC-14）。"""
    parts = str(chapter_id).split("-")
    for part in reversed(parts):
        if part.isdigit():
            return (int(part), chapter_id)
    return (10**9, str(chapter_id))


def rows_to_meta(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]
