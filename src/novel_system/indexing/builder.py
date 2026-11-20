"""把 Markdown 建成 SQLite 索引。

两条硬约束：

1. **不原地重建**（PRD §16.4）。做法是**每次新建一份 build**：
   `<index_dir>/builds/<build_id>.sqlite`，写完再把 `active.json` 指针指过去。
   全程不覆盖任何已存在的库文件，所以正在读旧索引的进程握着旧 inode，
   不会看到半成品。比「临时文件 + 原子替换」更彻底——替换毕竟要动那个名字。
2. **不修改 00_Canon**。本模块只有读，没有任何写回路径；集成测试用整棵树的
   sha256 摘要前后比对来守这一条。

`journal_mode=MEMORY`：索引整库可重建，不需要崩溃回滚；顺带避开沙箱对
`.sqlite-journal` 的拦截。丢了就重跑 `rebuild-index`，没有数据损失。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .. import markdown as md
from ..errors import ParseError
from ..storage import sha256_text, write_text_atomic
from . import schema

# 进 `entities` 表的类型。章节/场景不是实体，它们去 `narrative_order`。
ENTITY_TYPES = frozenset(
    {
        "world",
        "location",
        "organization",
        "faction",
        "character",
        "creature",
        "item",
        "system",
        "rule",
        "skill",
        "destiny",
    }
)
NARRATIVE_TYPES = frozenset({"chapter", "scene"})

_DIGITS_RE = re.compile(r"(\d+)(?!.*\d)")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ordinal(raw_id: str) -> int:
    """取 id 里最后一串数字当序号；取不到返回 0。"""
    found = _DIGITS_RE.search(raw_id or "")
    return int(found.group(1)) if found else 0


def _project_of(rel: str) -> str:
    parts = rel.split("/")
    if len(parts) >= 2 and parts[0] == "10_Projects":
        return parts[1]
    return ""


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return []


@dataclass
class BuildResult:
    build_id: str
    db_path: Path
    canon_revision: str
    source_kind: str
    fts_mode: str
    fts_note: str
    built_at: str
    artifact_count: int = 0
    id_count: int = 0
    block_count: int = 0
    skipped: int = 0
    duplicate_ids: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    tables: dict[str, int] = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)


def _write_metadata(conn: sqlite3.Connection, rows: dict[str, object]) -> None:
    conn.executemany(
        "insert into index_metadata(key, value) values (?, ?)",
        [(key, str(value)) for key, value in rows.items()],
    )


def build_index(
    source,
    index_dir: Path,
    *,
    built_at: str | None = None,
    manifest_path: Path | None = None,
    pointer_path: Path | None = None,
) -> BuildResult:
    """从 source 建一份新索引到 `index_dir/builds/`，并（可选）切换指针。

    返回统计。**不覆盖任何已存在的库文件**；旧 build 原样留着，
    要清理是另一件事（见 known_risks R-18）。
    """
    index_dir = Path(index_dir)
    builds = index_dir / "builds"
    builds.mkdir(parents=True, exist_ok=True)
    stamp = built_at or _now()
    build_id = f"IDX-{stamp.replace('-', '').replace(':', '')}-{os.getpid()}"
    target = builds / f"{build_id}.sqlite"
    if target.exists():
        # 同一秒同一进程连建两次时不能覆盖：编号往后顺延，build_id 跟着改，
        # 否则指针说 build 是 A、文件名却是 A-2，两边对不上。
        suffix = 2
        while (builds / f"{build_id}-{suffix}.sqlite").exists():
            suffix += 1
        build_id = f"{build_id}-{suffix}"
        target = builds / f"{build_id}.sqlite"

    conn = sqlite3.connect(target)
    try:
        conn.execute("pragma journal_mode = MEMORY")
        conn.execute("pragma synchronous = OFF")
        fts_mode = schema.create_schema(conn)
        fts_note = "" if fts_mode == schema.FTS_MODE_TRIGRAM else schema.fts_unavailable_reason(conn)

        ids: list[str] = []
        entries: list[dict] = []
        failed: list[tuple[str, str]] = []
        block_count = 0

        for item in source.files():
            try:
                doc = md.parse_bytes(item.data, item.rel)
            except ParseError as exc:  # 坏文件不该让整次重建失败
                failed.append((item.rel, str(exc)))
                continue

            project = _project_of(item.rel)
            artifact_id = str(doc.meta.get("id") or Path(item.rel).stem).strip()
            if "{{" in artifact_id:  # 模板占位符不是身份
                artifact_id = Path(item.rel).stem
            artifact_type = str(doc.meta.get("type") or "").strip()
            title = str(doc.meta.get("title") or doc.meta.get("name") or Path(item.rel).stem)
            status = str(doc.meta.get("status") or doc.meta.get("canon_status") or "").strip()
            text_sha = sha256_text(md.decode(item.data))
            char_count = len(doc.body)

            # 模板里的 `id: {{id}}` 是占位符，不是身份。判据必须与 `validate` 一致
            # （read.py 里写的是 `if not eid or "{{" in eid: continue`），
            # 否则模板文件会被算成「重号」——真实 Vault 上会多报一个 `{{id}}`。
            raw_id = str(doc.meta.get("id") or "").strip()
            if raw_id and "{{" not in raw_id:
                ids.append(raw_id)

            conn.execute(
                "insert into artifacts"
                "(id, project, path, artifact_type, title, status, sha256, block_count, char_count)"
                " values (?,?,?,?,?,?,?,?,?)",
                (
                    artifact_id,
                    project,
                    item.rel,
                    artifact_type,
                    title,
                    status,
                    text_sha,
                    len(doc.blocks),
                    char_count,
                ),
            )

            if artifact_type in ENTITY_TYPES:
                conn.execute(
                    "insert into entities(id, project, entity_type, name, subtype, world, path)"
                    " values (?,?,?,?,?,?,?)",
                    (
                        artifact_id,
                        project,
                        artifact_type,
                        title,
                        str(doc.meta.get("subtype") or ""),
                        str(doc.meta.get("world") or ""),
                        item.rel,
                    ),
                )
                aliases = _as_list(doc.meta.get("aliases")) + _as_list(doc.meta.get("alias"))
                if aliases:
                    conn.executemany(
                        "insert into aliases(project, entity_id, alias) values (?,?,?)",
                        [(project, artifact_id, alias) for alias in aliases],
                    )

            if artifact_type in NARRATIVE_TYPES:
                conn.execute(
                    "insert into narrative_order(project, artifact_id, kind, ordinal) values (?,?,?,?)",
                    (project, artifact_id, artifact_type, _ordinal(artifact_id)),
                )

            for ref in doc.meta.get("source_refs") or []:
                if not isinstance(ref, dict):
                    continue
                conn.execute(
                    "insert into source_refs"
                    "(project, artifact_id, block_id, kind, target_id, target_hash, locator, quote)"
                    " values (?,?,?,?,?,?,?,?)",
                    (
                        project,
                        artifact_id,
                        str(ref.get("block_id") or ""),
                        str(ref.get("kind") or ""),
                        str(ref.get("artifact_id") or ref.get("import_id") or ref.get("decision_id") or ""),
                        str(ref.get("file_sha256") or ref.get("content_hash") or ""),
                        str(ref.get("locator") or ""),
                        str(ref.get("quote") or ""),
                    ),
                )

            if doc.blocks:
                conn.executemany(
                    "insert into blocks"
                    "(project, artifact_id, path, block_id, ordinal, kind, text, line_start, line_end)"
                    " values (?,?,?,?,?,?,?,?,?)",
                    [
                        (
                            project,
                            artifact_id,
                            item.rel,
                            block.block_id,
                            block.ordinal,
                            block.kind,
                            block.text,
                            block.line_start,
                            block.line_end,
                        )
                        for block in doc.blocks
                    ],
                )
                block_count += len(doc.blocks)

            entries.append(
                {
                    "path": item.rel,
                    "project": project,
                    "id": str(doc.meta.get("id") or ""),
                    "type": artifact_type,
                    "status": status,
                    "title": title,
                }
            )

        seen: dict[str, int] = {}
        for raw in ids:
            seen[raw] = seen.get(raw, 0) + 1
        duplicates = sorted(key for key, count in seen.items() if count > 1)

        tables = {
            name: conn.execute(f"select count(*) from {name}").fetchone()[0]
            for name in schema.POPULATED_TABLES
            if name != "index_metadata"
        }

        _write_metadata(
            conn,
            {
                "build_id": build_id,
                "canon_revision": source.revision,
                "source_kind": source.kind,
                "schema_version": schema.SCHEMA_VERSION,
                "builder_version": schema.BUILDER_VERSION,
                "built_at": stamp,
                "fts_mode": fts_mode,
                "fts_note": fts_note,
                "artifact_count": len(entries),
                "block_count": block_count,
                "failed_count": len(failed),
                "populated": ",".join(schema.POPULATED_TABLES),
                "empty": ",".join(schema.EMPTY_TABLES),
            },
        )
        conn.commit()
    except BaseException:
        conn.close()
        raise
    else:
        conn.close()

    result = BuildResult(
        build_id=build_id,
        db_path=target,
        canon_revision=source.revision,
        source_kind=source.kind,
        fts_mode=fts_mode,
        fts_note=fts_note,
        built_at=stamp,
        artifact_count=len(entries),
        id_count=len(ids),
        block_count=block_count,
        skipped=getattr(source, "skipped", 0),
        duplicate_ids=duplicates,
        failed=failed,
        tables=tables,
    )

    manifest = {
        "built_at": stamp,
        "build_id": build_id,
        "schema_version": schema.SCHEMA_VERSION,
        "builder_version": schema.BUILDER_VERSION,
        "canon_revision": source.revision,
        "source_kind": source.kind,
        "fts_mode": fts_mode,
        "count": len(entries),
        "ids": len(ids),
        "blocks": block_count,
        "skipped_runtime": result.skipped,
        "duplicate_ids": duplicates,
        "failed": [{"path": path, "reason": reason} for path, reason in failed],
        "tables": tables,
        "entries": entries,
    }
    result.manifest = manifest
    if pointer_path is not None:
        write_text_atomic(
            Path(pointer_path),
            json.dumps(
                {
                    "build_id": build_id,
                    "db": f"builds/{target.name}",
                    "canon_revision": source.revision,
                    "source_kind": source.kind,
                    "built_at": stamp,
                    "artifact_count": len(entries),
                    "block_count": block_count,
                    "fts_mode": fts_mode,
                },
                ensure_ascii=False,
                indent=1,
            ),
        )
    if manifest_path is not None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return result
