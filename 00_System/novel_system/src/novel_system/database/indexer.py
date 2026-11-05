"""索引器：从 Markdown/YAML 事实源构建 SQLite 索引（PRD 8.2）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import vault
from ..config import Config
from .sqlite import Index, now_iso

ENTITY_TYPES = {
    "character",
    "location",
    "faction",
    "item",
    "enemy",
    "world",
    "mission",
    "story_asset",
    "character_state",
    "volume",
    "skill",
    "destiny",
}


def summarize(body: str, limit: int = 180) -> str:
    lines = []
    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", "---", "|", ">")):
            continue
        lines.append(s.lstrip("-* ").strip())
        if sum(len(x) for x in lines) > limit:
            break
    text = " ".join(lines)
    return text[:limit]


def _json(value: Any) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def stamp_of(path: Path) -> tuple[int, int]:
    """文件指纹 `(mtime 整秒, size)`。

    为什么只到秒：仓库要能整目录复制（测试夹具、备份、换机），而 macOS 自带的
    rsync 只把修改时间还原到秒，纳秒/微秒位会丢。用更细的精度做判据，
    每次复制都会判定「全库改过」，增量索引退化成全量——比不做还慢。
    """
    st = path.stat()
    return (int(st.st_mtime), st.st_size)


def index_document(
    cfg: Config,
    index: Index,
    path: Path,
    doc: vault.Doc | None = None,
    stamp: tuple[int, int] | None = None,
) -> str | None:
    """索引单个文件；返回文档类型，未识别则返回 None。

    `stamp` 是 `(mtime, size)`，写进 `files` 表供增量索引用。
    """
    doc = doc or vault.read_doc(path)
    if stamp is None:
        stamp = stamp_of(path)
    meta = doc.meta
    doc_type = meta.get("type")
    doc_id = meta.get("id")
    rel_path = cfg.rel(path)
    digest = vault.sha256_text(doc.raw)

    if not doc_id or doc_type not in ENTITY_TYPES | {
        "foreshadowing",
        "event",
        "chapter_outline",
        "manuscript",
        "scene_beat",
        "relationship",
    }:
        index.set_file_hash(rel_path, digest, *stamp)
        return None

    # 先清理旧的派生记录，避免改名/改类型后残留
    index.clear_entities_for_path(rel_path)
    index.conn.execute("DELETE FROM relationships WHERE file_path=?", (rel_path,))
    index.conn.execute("DELETE FROM events WHERE file_path=?", (rel_path,))

    name = str(meta.get("name") or meta.get("title") or "")
    index.upsert_entity(
        {
            "id": str(doc_id),
            "type": str(doc_type),
            "name": name,
            "aliases": _json(meta.get("aliases")),
            "canon_status": str(meta.get("canon_status") or "confirmed"),
            "file_path": rel_path,
            "updated_at": str(meta.get("updated_at") or ""),
            "tags": _json(meta.get("tags")),
            "summary": summarize(doc.body),
            "content_hash": digest,
        }
    )

    if doc_type == "relationship":
        index.upsert_relationship(
            {
                "id": str(doc_id),
                "source_id": str(meta.get("source_id") or ""),
                "target_id": str(meta.get("target_id") or ""),
                "relation_type": str(meta.get("relation_type") or "unknown"),
                "strength": int(meta.get("strength") or 0),
                "valid_from": str(meta.get("valid_from") or ""),
                "valid_to": str(meta.get("valid_to") or ""),
                "source_event": str(meta.get("source_event") or ""),
                "file_path": rel_path,
            }
        )

    elif doc_type == "event":
        index.upsert_event(
            {
                "event_id": str(doc_id),
                "chapter_id": str(meta.get("chapter_id") or ""),
                "sequence": int(meta.get("sequence") or 1),
                "name": name,
                "location_id": str(meta.get("location_id") or ""),
                "participants": _json(meta.get("participants")),
                "source_refs": _json(meta.get("source_refs")),
                "file_path": rel_path,
            }
        )

    elif doc_type == "foreshadowing":
        win = meta.get("target_reveal_window") or {}
        index.upsert_foreshadowing(
            {
                "id": str(doc_id),
                "name": name,
                "status": str(meta.get("status") or "planted"),
                "planted_in": str(meta.get("planted_in") or ""),
                "target_start": str(win.get("start") or ""),
                "target_end": str(win.get("end") or ""),
                "importance": str(meta.get("importance") or ""),
                "trigger_conditions": _json(meta.get("trigger_conditions")),
                "file_path": rel_path,
            }
        )

    elif doc_type == "chapter_outline":
        index.upsert_chapter(
            str(doc_id),
            title=name,
            outline_path=rel_path,
            status=str(meta.get("status") or "outline"),
            updated_at=str(meta.get("updated_at") or now_iso()),
        )

    elif doc_type == "manuscript":
        body_digest = vault.sha256_text(doc.body)
        index.upsert_chapter(
            str(doc_id),
            title=name,
            manuscript_path=rel_path,
            status=str(meta.get("status") or "draft"),
            word_count=int(meta.get("word_count") or 0),
            content_hash=body_digest,
            updated_at=str(meta.get("updated_at") or now_iso()),
        )
        index.index_chapter_text(str(doc_id), name, doc.body)

    index.set_file_hash(rel_path, digest, *stamp)
    return str(doc_type)


def index_vault(cfg: Config, index: Index, full: bool = True) -> dict[str, Any]:
    """索引全库。

    `full=False` 时走增量：`files` 表里 `(mtime_ns, size)` 未变的文件直接跳过。
    这不是微优化——受限宿主里单次读盘约 18ms，全量读 800 个档案要 15 秒，
    而一次正常写作只改动几个文件。默认仍为全量，保证 `rebuild-index` 语义不变。
    """
    cfg.ensure_dirs()
    counts: dict[str, int] = {}
    errors: list[str] = []
    total = 0
    skipped = 0
    seen: set[str] = set()
    fingerprints = {} if full else index.file_fingerprints()

    # 模板、配置、归档不是事实源，不参与索引
    excluded = {
        cfg.rel(cfg.dir("templates")),
        cfg.rel(cfg.dir("archive")),
        cfg.rel(cfg.vault / "00_System"),
    }

    # 归档整棵不索引，只有一个例外：旧稿正文存档。作者的上一版全稿要「入库」——
    # 可检索、可引用、能进上下文，所以它留在 99_Archive 下（视图里排除），但参与索引。
    archive_exceptions = {"99_Archive/Original_Draft"}

    for path in vault.iter_markdown(cfg.vault):
        rel = cfg.rel(path)
        if any(rel.startswith(prefix + "/") for prefix in excluded if prefix) and not any(
            rel.startswith(prefix + "/") for prefix in archive_exceptions
        ):
            continue
        total += 1
        seen.add(rel)
        try:
            stamp = stamp_of(path)
            if fingerprints.get(rel) == stamp:
                skipped += 1
                continue
            doc_type = index_document(cfg, index, path, stamp=stamp)
        except Exception as exc:  # noqa: BLE001 - 单个文件坏掉不应中断全量索引
            errors.append(f"{rel}: {exc}")
            continue
        if doc_type:
            counts[doc_type] = counts.get(doc_type, 0) + 1

    pruned = index.prune_missing(seen)
    index.commit()
    return {
        "files": total,
        "indexed": counts,
        "skipped": skipped,
        "pruned": pruned,
        "errors": errors,
        "stats": index.stats(),
    }
