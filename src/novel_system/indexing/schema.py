"""索引库的建表语句与能力探测。

表清单照 PRD §16.1（17 张）。本轮**只有一部分会被填满**，其余建空表：

| 已填充 | 依据 |
|---|---|
| `index_metadata` `artifacts` `blocks` `fts_blocks` | T03 本体的产物 |
| `entities` `aliases` `source_refs` | frontmatter 里就有 |
| `narrative_order` | 章节/场景的编号可从 id 与目录推 |

| 建空表 | 谁来填 |
|---|---|
| `events` `event_edges` | T04 时间线 |
| `assertions` `state_changes` `knowledge` `revelations` | T05 认知 |
| `foreshadowing` `dependencies` `issues` | T07 之后 |

**建空表不算交付**。空表只保证「查询不会因为表不存在而报错」，不能当成
「这些语义已经支持」。`index_metadata.populated` 会把这件事写进库里。

为什么不担心改表结构：索引是**派生**的（ADR-003），每次重建都从 Markdown 重来，
没有迁移负担。要加列直接改这里，重跑 `rebuild-index`。

FTS5 能力：PRD §16.2 要求 trigram 做中文子串。探测失败时**不退回 unicode61**
——那对中文等于按整段切词，几乎检不出东西，却会让「检索功能已启用」看起来成立。
退化路径是「不用 FTS，全部走参数化 LIKE」，慢但结果对，并把 `fts_mode=none` 记进库。
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1
BUILDER_VERSION = "0.6.0"

FTS_MODE_TRIGRAM = "trigram"
FTS_MODE_LIKE = "none"  # 明确退化：没有 FTS，查询走 LIKE

# 本版本会真的写入数据的表
POPULATED_TABLES = (
    "index_metadata",
    "artifacts",
    "entities",
    "aliases",
    "source_refs",
    "narrative_order",
    "blocks",
    "fts_blocks",
)

# 建表但不填的表：留给后续任务
EMPTY_TABLES = (
    "events",
    "event_edges",
    "assertions",
    "state_changes",
    "knowledge",
    "revelations",
    "foreshadowing",
    "dependencies",
    "issues",
)

ALL_TABLES = POPULATED_TABLES + EMPTY_TABLES

_DDL = [
    # ---- 元数据 ----
    """create table index_metadata (
        key text primary key,
        value text not null
    )""",
    # ---- 产物 ----
    """create table artifacts (
        id text not null,
        project text not null,
        path text not null,
        artifact_type text not null default '',
        title text not null default '',
        status text not null default '',
        sha256 text not null,
        block_count integer not null default 0,
        char_count integer not null default 0,
        primary key (project, id, path)
    )""",
    # ---- 实体与别名 ----
    """create table entities (
        id text not null,
        project text not null,
        entity_type text not null default '',
        name text not null default '',
        subtype text not null default '',
        world text not null default '',
        path text not null default '',
        primary key (project, id, path)
    )""",
    """create table aliases (
        project text not null,
        entity_id text not null,
        alias text not null
    )""",
    # ---- 来源 ----
    """create table source_refs (
        project text not null,
        artifact_id text not null,
        block_id text not null default '',
        kind text not null default '',
        target_id text not null default '',
        target_hash text not null default '',
        locator text not null default '',
        quote text not null default ''
    )""",
    # ---- 正文块 ----
    """create table blocks (
        id integer primary key,
        project text not null,
        artifact_id text not null,
        path text not null,
        block_id text not null,
        ordinal integer not null,
        kind text not null,
        text text not null,
        line_start integer not null default 0,
        line_end integer not null default 0,
        unique (project, path, block_id)
    )""",
    # ---- 叙述顺序 ----
    """create table narrative_order (
        project text not null,
        artifact_id text not null,
        kind text not null default '',
        ordinal integer not null default 0
    )""",
    # ---- 以下本版建表不填 ----
    """create table events (
        id text not null, project text not null, title text not null default '',
        summary text not null default '', story_time text not null default ''
    )""",
    """create table event_edges (
        project text not null, from_id text not null, to_id text not null,
        relation text not null default ''
    )""",
    """create table assertions (
        id text not null, project text not null, subject_id text not null default '',
        predicate text not null default '', value text not null default '',
        epistemic_type text not null default '', review_status text not null default ''
    )""",
    """create table state_changes (
        id text not null, project text not null, entity_id text not null default '',
        attribute text not null default '', from_value text not null default '',
        to_value text not null default '', event_id text not null default ''
    )""",
    """create table knowledge (
        id text not null, project text not null, holder_id text not null default '',
        subject_id text not null default '', predicate text not null default '',
        value text not null default '', since_event text not null default ''
    )""",
    """create table revelations (
        id text not null, project text not null, subject_id text not null default '',
        chapter_id text not null default ''
    )""",
    """create table foreshadowing (
        id text not null, project text not null, title text not null default '',
        status text not null default '', planted_in text not null default '',
        payoff_in text not null default ''
    )""",
    """create table dependencies (
        project text not null, from_id text not null, to_id text not null,
        kind text not null default ''
    )""",
    """create table issues (
        id text not null, project text not null, severity text not null default '',
        status text not null default '', title text not null default ''
    )""",
    # ---- 普通索引：查询热路径 ----
    "create index idx_blocks_artifact on blocks (project, artifact_id)",
    "create index idx_blocks_lookup on blocks (project, block_id)",
    "create index idx_entities_name on entities (project, name)",
    "create index idx_aliases_alias on aliases (project, alias)",
    "create index idx_artifacts_path on artifacts (project, path)",
]


def detect_fts(conn: sqlite3.Connection) -> tuple[bool, str]:
    """能不能用 FTS5 trigram。返回 (可用, 不可用原因)。"""
    try:
        conn.execute("create virtual table __fts_probe using fts5(x, tokenize='trigram')")
    except sqlite3.OperationalError as exc:
        return False, str(exc)
    finally:
        try:
            conn.execute("drop table if exists __fts_probe")
        except sqlite3.OperationalError:
            pass
    return True, ""


def create_schema(conn: sqlite3.Connection) -> str:
    """建全部表，返回 fts_mode。调用方负责提交事务。"""
    for statement in _DDL:
        conn.execute(statement)

    available, reason = detect_fts(conn)
    if available:
        conn.execute(
            "create virtual table fts_blocks using fts5("
            "text, content='blocks', content_rowid='id', tokenize='trigram')"
        )
        # 外部内容表要自己维护同步；这里只在插入时写，重建索引一律整库重来，
        # 没有 UPDATE/DELETE 路径，所以只需要一个 after insert 触发器。
        conn.execute(
            "create trigger blocks_ai after insert on blocks begin "
            "insert into fts_blocks(rowid, text) values (new.id, new.text); end"
        )
        return FTS_MODE_TRIGRAM

    # 明确退化：不建 fts_blocks 虚表，另建一张 fts_blocks_unavailable 记下原因。
    # 查询层据 fts_mode 分叉走 LIKE，不需要在这张表上查任何东西。
    conn.execute("create table fts_blocks_unavailable (reason text)")
    conn.execute("insert into fts_blocks_unavailable values (?)", (reason,))
    return FTS_MODE_LIKE


def fts_unavailable_reason(conn: sqlite3.Connection) -> str:
    try:
        row = conn.execute("select reason from fts_blocks_unavailable limit 1").fetchone()
    except sqlite3.OperationalError:
        return ""
    return row[0] if row else ""
