"""索引子系统：解析后的产物，全部可丢弃重来。

对外的入口只有四组：
- `build_index` 建库（`builder.py`）
- `search` 查询（`search.py`）
- `WorktreeSource` / `GitRevisionSource` 选内容来源（`source.py`）
- `create_schema` / `detect_fts` 建表与能力探测（`schema.py`）
"""

from __future__ import annotations

from .builder import BuildResult, build_index
from .schema import (
    ALL_TABLES,
    EMPTY_TABLES,
    FTS_MODE_LIKE,
    FTS_MODE_TRIGRAM,
    POPULATED_TABLES,
    create_schema,
    detect_fts,
)
from .search import Hit, SearchResult, connect, index_metadata, search
from .source import (
    GitRevisionSource,
    WorktreeSource,
    head_revision,
    is_dirty,
    resolve_revision,
)

__all__ = [
    "ALL_TABLES",
    "BuildResult",
    "EMPTY_TABLES",
    "FTS_MODE_LIKE",
    "FTS_MODE_TRIGRAM",
    "GitRevisionSource",
    "Hit",
    "POPULATED_TABLES",
    "SearchResult",
    "WorktreeSource",
    "build_index",
    "connect",
    "create_schema",
    "detect_fts",
    "head_revision",
    "index_metadata",
    "is_dirty",
    "resolve_revision",
    "search",
]
