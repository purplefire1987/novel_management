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


def index_document(cfg: Config, index: Index, path: Path, doc: vault.Doc | None = None) -> str | None:
    """索引单个文件；返回文档类型，未识别则返回 None。"""
    doc = doc or vault.read_doc(path)
    meta = doc.meta
    doc_type = meta.get("type")
    doc_id = meta.get("id")
    rel_path = cfg.rel(path)

    if not doc_id or doc_type not in ENTITY_TYPES | {
        "foreshadowing",
        "event",
        "chapter_outline",
        "manuscript",
        "scene_beat",
        "relationship",
    }:
        index.set_file_hash(rel_path, vault.sha256_text(doc.raw))
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
            "content_hash": vault.sha256_text(doc.raw),
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
        digest = vault.sha256_text(doc.body)
        index.upsert_chapter(
            str(doc_id),
            title=name,
            manuscript_path=rel_path,
            status=str(meta.get("status") or "draft"),
            word_count=int(meta.get("word_count") or 0),
            content_hash=digest,
            updated_at=str(meta.get("updated_at") or now_iso()),
        )
        index.index_chapter_text(str(doc_id), name, doc.body)

    index.set_file_hash(rel_path, vault.sha256_text(doc.raw))
    return str(doc_type)


def index_vault(cfg: Config, index: Index) -> dict[str, Any]:
    """全量索引。返回统计信息，便于 rebuild-index 输出。"""
    cfg.ensure_dirs()
    counts: dict[str, int] = {}
    errors: list[str] = []
    total = 0

    # 模板、配置、归档不是事实源，不参与索引
    excluded = {
        cfg.rel(cfg.dir("templates")),
        cfg.rel(cfg.dir("archive")),
        cfg.rel(cfg.vault / "00_System"),
    }

    for path in vault.iter_markdown(cfg.vault):
        rel = cfg.rel(path)
        if any(rel.startswith(prefix + "/") for prefix in excluded if prefix):
            continue
        total += 1
        try:
            doc_type = index_document(cfg, index, path)
        except Exception as exc:  # noqa: BLE001 - 单个文件坏掉不应中断全量索引
            errors.append(f"{rel}: {exc}")
            continue
        if doc_type:
            counts[doc_type] = counts.get(doc_type, 0) + 1

    index.commit()
    return {
        "files": total,
        "indexed": counts,
        "errors": errors,
        "stats": index.stats(),
    }
