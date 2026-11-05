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
    indexed_at TEXT,
    mtime      INTEGER,
    size       INTEGER
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
        self._migrate_files()
        self.conn.commit()

    def _migrate_files(self) -> None:
        """老库补列：`files` 早期只有 path/hash，增量索引要 mtime 与 size。

        直接用**整秒**的 mtime，不用纳秒：仓库要能整目录复制（测试夹具、备份、
        换机），而 macOS 自带的 rsync 只把时间还原到秒。若拿纳秒做判据，
        每复制一次就会判定「全库都改过」，增量索引直接退化成全量。
        代价是「同一秒内、长度不变」的改动看不出来——这种改动下一次
        `rebuild-index`（全量）会修正，日常编辑几乎碰不到。
        """
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(files)")}
        if "mtime" not in cols:
            self.conn.execute("ALTER TABLE files ADD COLUMN mtime INTEGER")
        if "size" not in cols:
            self.conn.execute("ALTER TABLE files ADD COLUMN size INTEGER")

    def reset(self) -> None:
        """清空派生数据，保留表结构与审计流水。

        重建索引不再删除 index.sqlite 文件：删文件在 Windows 上会被占用、
        在受限沙箱里会被安全策略拦截，而索引本来就是可丢弃的派生层。

        `audit_log` 有意不清空：它没有对应的 Markdown 事实源，
        重建后仍要能 `novel log` 查到历史操作（同时留有 logs/audit.jsonl）。
        """
        for table in (
            "entities",
            "relationships",
            "events",
            "foreshadowing",
            "chapter_index",
            "files",
        ):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.execute("DELETE FROM fts_chapters")
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

    def search_entities(self, keyword: str, limit: int = 30) -> list[dict[str, Any]]:
        """按名称 / 别名 / 概要做子串检索，按「名称命中 > 别名命中 > 概要命中」排序。

        录入员判「库里有没有这个实体」必须走这里：只比名称会漏掉简称（环教 vs 环之教派）、
        职务称呼（老坊主）与亲属指称（老坊主孙女）。
        """
        needle = keyword.strip()
        if not needle:
            return []
        like = f"%{needle}%"
        cur = self.conn.execute(
            """
            SELECT *,
                   CASE
                       WHEN name = ? THEN 0
                       WHEN name LIKE ? THEN 1
                       WHEN aliases LIKE ? THEN 2
                       WHEN summary LIKE ? THEN 3
                       ELSE 4
                   END AS rank
            FROM entities
            WHERE name LIKE ? OR aliases LIKE ? OR summary LIKE ?
            ORDER BY rank, type, id
            LIMIT ?
            """,
            (needle, like, like, like, like, like, like, limit),
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

    def set_file_hash(
        self,
        rel_path: str,
        digest: str,
        mtime: int | None = None,
        size: int | None = None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO files (path, hash, indexed_at, mtime, size) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET hash=excluded.hash, indexed_at=excluded.indexed_at, "
            "mtime=excluded.mtime, size=excluded.size",
            (rel_path, digest, now_iso(), mtime, size),
        )

    def file_fingerprints(self) -> dict[str, tuple[int, int]]:
        """`路径 -> (mtime, size)`。增量索引据此判断「文件没动过，跳过」。"""
        rows = self.conn.execute(
            "SELECT path, mtime, size FROM files WHERE mtime IS NOT NULL"
        )
        return {r["path"]: (int(r["mtime"]), int(r["size"] or 0)) for r in rows}

    def prune_missing(self, keep: set[str]) -> int:
        """删掉事实源已消失的派生记录，返回清理条数。

        以前只增不减：改名或删档后，索引里会留下指向不存在文件的实体，
        搜索能搜到、点开却是空。索引既然是派生的，就该跟着事实源收缩。
        """
        rows = [r["path"] for r in self.conn.execute("SELECT path FROM files")]
        stale = [p for p in rows if p not in keep]
        if not stale:
            return 0
        for path in stale:
            self.conn.execute("DELETE FROM files WHERE path=?", (path,))
            self.conn.execute("DELETE FROM entities WHERE file_path=?", (path,))
            self.conn.execute("DELETE FROM relationships WHERE file_path=?", (path,))
            self.conn.execute("DELETE FROM events WHERE file_path=?", (path,))
            self.conn.execute("DELETE FROM foreshadowing WHERE file_path=?", (path,))
            # 章节行只在「大纲和正文都没了」时删；否则会误删还有一方存活的章节
            cursor = self.conn.execute(
                "DELETE FROM chapter_index WHERE outline_path=? AND (manuscript_path IS NULL OR manuscript_path='')",
                (path,),
            )
            if cursor.rowcount == 0:
                self.conn.execute(
                    "DELETE FROM chapter_index WHERE manuscript_path=? AND (outline_path IS NULL OR outline_path='')",
                    (path,),
                )
        return len(stale)

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
