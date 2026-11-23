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
from ..timeline import EventGraph
from ..timeline import extract as extract_semantics
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
# 正文有两个 type 值：`chapter`（novel write 产出）与 `manuscript`（2026-11-04
# 批量导入的 28 份旧稿）。只认 chapter 的后果不是报错，是那 28 份不进叙事索引——
# 时间线、场景、揭示三个视图对它们全空，而空视图和「没有正文」在界面上长得一样。
NARRATIVE_TYPES = frozenset({"chapter", "manuscript", "scene"})

# 伏笔档案。T08 的 Preflight 要查「未回收承诺」，必须先让它进索引——
# 否则 `foreshadowing` 表永远是空的，「检查过伏笔」就成了一句空话
# （空表和「没有伏笔」在查询里长得一模一样）。
FORESHADOW_TYPES = frozenset({"foreshadowing", "foreshadow", "伏笔"})
FORESHADOW_ID_PREFIXES = ("FORE-", "FSH-", "FORESHADOW-")

# 「打算怎么写」不是「已经发生」。规划类产物里的语义记录一律不入事实表。
#
# 这一条不是靠约定，靠两道判定同时生效：类型在名单里，或路径落在 02_Planning 下。
# 只判类型不够——规划文件常常没写 type；只判路径也不够——规划类档案可能被
# 建在别处。两道都不中才允许入库。
PLANNING_TYPES = frozenset(
    {
        "chapter_plan",
        "chapter_outline",
        "scene_beat",
        "volume",
        "volume_design",
        "outline",
        "series_outline",
        "template",
    }
)
PLANNING_DIR = "/02_Planning/"

_DIGITS_RE = re.compile(r"(\d+)(?!.*\d)")


def is_planning(rel: str, artifact_type: str) -> bool:
    """这份产物是不是规划件。规划件里的「事件」是计划，不是事实。"""
    if (artifact_type or "").strip().lower() in PLANNING_TYPES:
        return True
    return PLANNING_DIR in f"/{rel}"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ordinal(raw_id: str) -> int:
    """取 id 里最后一串数字当序号；取不到返回 0。"""
    found = _DIGITS_RE.search(raw_id or "")
    return int(found.group(1)) if found else 0


def _project_of(rel: str) -> str:
    parts = rel.split("/")
    if len(parts) >= 2 and parts[0] == "01_小说项目":
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


def is_foreshadowing(meta: dict, artifact_type: str) -> bool:
    """是不是伏笔档案。类型优先，其次看 ID 前缀——老档案常常只写 id 不写 type。"""
    if (artifact_type or "").strip().lower() in FORESHADOW_TYPES:
        return True
    raw_id = str(meta.get("id") or "").strip().upper()
    if "{{" in raw_id:  # 模板占位符不是身份
        return False
    return raw_id.startswith(FORESHADOW_ID_PREFIXES)


def _nested_str(meta: dict, key: str, inner: tuple[str, ...]) -> str:
    value = meta.get(key)
    if isinstance(value, dict):
        for name in inner:
            text = str(value.get(name) or "").strip()
            if text:
                return text
    return ""


def foreshadow_row(meta: dict, *, artifact_id: str, project: str, title: str, status: str) -> tuple:
    """把一份伏笔档案压成索引行。

    `window_policy` 与窗口终点是两个关键字段：ADVISORY 超期只告警，
    HARD 超期阻断（PRD §10.4）。压丢了就没法在 Preflight 里判「该不该拦」。
    所以字段缺失时留空串而不是猜一个默认值——空串在查询里能看出「没写」，
    猜成 ADVISORY 就把「该拦」变成「放行」。
    """
    planted = _nested_str(meta, "plant_source", ("chapter_id",)) or str(
        meta.get("planted_in") or ""
    ).strip()
    payoff = str(meta.get("payoff_in") or meta.get("paid_off_in") or "").strip()
    window_end = _nested_str(meta, "target_window", ("to_chapter", "end_chapter", "to")) or str(
        meta.get("window_end") or ""
    ).strip()
    reveal_scope = ",".join(_as_list(meta.get("reveal_scope")))
    # 生命周期字段在 schema 里叫 `state`（PRD §10.4），不是通用元数据的 `status`。
    # 只看 `status` 会把所有伏笔都读成「状态未知」，而「未知」既不算已回收、
    # 也不算超期——于是该拦的一条都拦不住。
    state = str(meta.get("state") or "").strip() or status
    return (
        artifact_id,
        project,
        title,
        state,
        planted,
        payoff,
        str(meta.get("window_policy") or "").strip().upper(),
        window_end,
        reveal_scope,
    )


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
    # ---- T04：时间线与语义记录 ----
    semantic: dict[str, int] = field(default_factory=dict)
    planning_skipped: int = 0
    event_cycle: tuple[str, ...] = ()
    semantic_warnings: list[str] = field(default_factory=list)
    dangling_edges: tuple[str, ...] = ()



def dependency_rows(meta: dict, *, artifact_id: str, project: str) -> list[tuple]:
    """`depends_on` → dependencies 表的行。

    只认显式声明的依赖。`derived_from` 是「我从哪来」，不是「我依赖谁」——
    把两者混在一起，会让一份档案因为「它的来源文件改了个错别字」而失效。
    """
    raw = meta.get("depends_on")
    if raw is None:
        raw = meta.get("dependencies")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    rows: list[tuple] = []
    for item in raw:
        if isinstance(item, str):
            target, digest, kind = item.strip(), "", "HARD"
        elif isinstance(item, dict):
            target = str(item.get("artifact_id") or item.get("id") or "").strip()
            digest = str(item.get("content_hash") or item.get("sha256") or "").strip()
            kind = str(item.get("dependency_type") or item.get("type") or "HARD").strip().upper()
        else:
            continue
        if not target:
            continue
        rows.append((project, artifact_id, target, kind if kind in ("HARD", "SOFT") else "HARD", digest, ""))
    return rows

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
    build_id: str = "",
) -> BuildResult:
    """从 source 建一份新索引到 `index_dir/builds/`，并（可选）切换指针。

    返回统计。**不覆盖任何已存在的库文件**；旧 build 原样留着，
    要清理是另一件事（见 known_risks R-18）。

    `build_id` 给定时用它，否则按时间戳自动编号。发布流程必须显式给号：
    ReleaseManifest 里写的是 `IDX-000021` 这种稳定编号，发布清单要能指着它说
    「这次发布对应的就是这份索引」，而不是一个带 PID 的时间戳。
    """
    index_dir = Path(index_dir)
    builds = index_dir / "builds"
    builds.mkdir(parents=True, exist_ok=True)
    stamp = built_at or _now()
    explicit_id = bool(build_id)
    if not build_id:
        build_id = f"IDX-{stamp.replace('-', '').replace(':', '')}-{os.getpid()}"
    target = builds / f"{build_id}.sqlite"
    if target.exists() and explicit_id:
        # 显式编号是发布清单钉住的号，**不能顺延**：悄悄改成 `-2` 会让清单
        # 永远指向一份不存在的库。重复建同一个号由调用方决定复用还是报错。
        raise ParseError(f"索引编号已存在，拒绝覆盖：{target}")
    if target.exists():
        # 自动编号时不能覆盖：同一秒同一进程连建两次，编号往后顺延，
        # build_id 跟着改，否则指针说 A、文件名却是 A-2，两边对不上。
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
        # ---- T04：语义记录的累加器 ----
        all_events: list = []
        all_edges: list = []
        all_assertions: list = []
        all_changes: list = []
        all_knowledge: list = []
        all_reveals: list = []
        planning_skipped = 0
        foreshadow_count = 0
        dependency_count = 0
        semantic_warnings: list[str] = []

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

            if is_foreshadowing(doc.meta, artifact_type):
                conn.execute(
                    "insert into foreshadowing"
                    "(id, project, title, status, planted_in, payoff_in,"
                    " window_policy, window_end, reveal_scope)"
                    " values (?,?,?,?,?,?,?,?,?)",
                    foreshadow_row(
                        doc.meta,
                        artifact_id=artifact_id,
                        project=project,
                        title=title,
                        status=status,
                    ),
                )
                foreshadow_count += 1

            for row in dependency_rows(doc.meta, artifact_id=artifact_id, project=project):
                conn.execute(
                    "insert into dependencies"
                    "(project, from_id, to_id, kind, content_hash, validation_status)"
                    " values (?,?,?,?,?,?)",
                    row,
                )
                dependency_count += 1

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

            # ---- T04：时间线与认知记录 ----
            sem = extract_semantics(doc.meta, path=item.rel, project=project)
            semantic_warnings.extend(sem.warnings)
            if not sem.is_empty():
                if is_planning(item.rel, artifact_type):
                    # 规划里写的事件是「打算发生」，不是「已经发生」。不入事实表。
                    # 但要计数：静默丢弃会让人以为写进去了。
                    planning_skipped += (
                        len(sem.events)
                        + len(sem.edges)
                        + len(sem.assertions)
                        + len(sem.state_changes)
                        + len(sem.knowledge)
                        + len(sem.revelations)
                    )
                else:
                    all_events.extend(sem.events)
                    all_edges.extend(sem.edges)
                    all_assertions.extend(sem.assertions)
                    all_changes.extend(sem.state_changes)
                    all_knowledge.extend(sem.knowledge)
                    all_reveals.extend(sem.revelations)

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

        # ---- 写语义表（T04） ----
        conn.executemany(
            "insert into events"
            "(id, project, title, summary, story_time, precision, narrated_in, path)"
            " values (?,?,?,?,?,?,?,?)",
            [
                (e.id, e.project, e.title, e.summary, e.story_time, e.precision, e.narrated_in, e.path)
                for e in all_events
            ],
        )
        conn.executemany(
            "insert into event_edges(project, from_id, to_id, relation, path) values (?,?,?,?,?)",
            [(x.project, x.from_id, x.to_id, x.relation, x.path) for x in all_edges],
        )
        conn.executemany(
            "insert into assertions"
            "(id, project, subject_id, predicate, value, epistemic_type, review_status,"
            " valid_from_event, valid_until_event, path) values (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    a.id,
                    a.project,
                    a.subject_id,
                    a.predicate,
                    a.value,
                    a.epistemic_type,
                    a.review_status,
                    a.valid_from_event,
                    a.valid_until_event,
                    a.path,
                )
                for a in all_assertions
            ],
        )
        conn.executemany(
            "insert into state_changes"
            "(id, project, entity_id, attribute, from_value, to_value, event_id, path)"
            " values (?,?,?,?,?,?,?,?)",
            [
                (c.id, c.project, c.entity_id, c.attribute, c.from_value, c.to_value, c.event_id, c.path)
                for c in all_changes
            ],
        )
        conn.executemany(
            "insert into knowledge"
            "(id, project, holder_id, subject_id, predicate, value, attitude,"
            " valid_from_event, valid_until_event, path) values (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    k.id,
                    k.project,
                    k.holder_id,
                    k.subject_id,
                    k.predicate,
                    k.value,
                    k.attitude,
                    k.valid_from_event,
                    k.valid_until_event,
                    k.path,
                )
                for k in all_knowledge
            ],
        )
        conn.executemany(
            "insert into revelations"
            "(id, project, proposition_ref, chapter_id, scene_id, block_id, revelation_mode, path)"
            " values (?,?,?,?,?,?,?,?)",
            [
                (
                    r.id,
                    r.project,
                    r.proposition_ref,
                    r.chapter_id,
                    r.scene_id,
                    r.block_id,
                    r.revelation_mode,
                    r.path,
                )
                for r in all_reveals
            ],
        )

        # 建图只为两件事：查环、查悬空边。有环不阻止建库（索引是派生物，
        # 建了才有东西可查），但必须报出来——`state` 查询遇到环会拒绝排序。
        event_graph = EventGraph.from_edges(
            [e.id for e in all_events], [(x.from_id, x.to_id) for x in all_edges]
        )
        event_cycle = event_graph.cycle()
        dangling_edges = event_graph.dangling
        semantic = {
            "events": len(all_events),
            "edges": len(all_edges),
            "assertions": len(all_assertions),
            "state_changes": len(all_changes),
            "knowledge": len(all_knowledge),
            "revelations": len(all_reveals),
        }

        seen: dict[str, int] = {}
        for raw in ids:
            seen[raw] = seen.get(raw, 0) + 1
        duplicates = sorted(key for key, count in seen.items() if count > 1)

        # 依赖状态在全部行插完后统一判：逐行判的时候，被依赖的档案可能还没入库。
        #
        #   HARD 且哈希不符 → STALE
        #   SOFT 且哈希不符 → NEEDS_REVIEW
        #   没记 content_hash、或目标根本不在库里 → NEEDS_REVIEW（判不了不等于没问题）
        conn.execute(
            """
            update dependencies set validation_status = case
                when content_hash = '' then 'NEEDS_REVIEW'
                when not exists (
                    select 1 from artifacts a
                    where a.project = dependencies.project and a.id = dependencies.to_id
                ) then 'NEEDS_REVIEW'
                when not exists (
                    select 1 from artifacts a
                    where a.project = dependencies.project and a.id = dependencies.to_id
                      and a.sha256 = dependencies.content_hash
                ) then (case when kind = 'SOFT' then 'NEEDS_REVIEW' else 'STALE' end)
                else 'VALID'
            end
            """
        )

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
                "planning_skipped": planning_skipped,
                "foreshadowing": foreshadow_count,
                "dependencies": dependency_count,
                "event_cycle": " → ".join(event_cycle),
                "dangling_edges": len(dangling_edges),
                "semantic_warnings": len(semantic_warnings),
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
        semantic=semantic,
        planning_skipped=planning_skipped,
        event_cycle=event_cycle,
        semantic_warnings=semantic_warnings,
        dangling_edges=dangling_edges,
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
        "semantic": semantic,
        "planning_skipped": planning_skipped,
        "event_cycle": list(event_cycle),
        "dangling_edges": list(dangling_edges),
        "semantic_warnings": semantic_warnings,
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
