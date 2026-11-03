"""检索层：按 ID / 类型 / 标签 / 关键词 / 时间就近召回（PRD 4.1 资料检索）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .. import vault
from ..config import Config
from ..database.sqlite import Index, loads, _chapter_sort_key
from .entity_resolver import Resolved, load_entity_doc


def chapter_by_id(cfg: Config, index: Index, chapter_id: str) -> dict[str, Any] | None:
    return index.get_chapter(chapter_id)


def outline_doc(cfg: Config, index: Index, chapter_id: str) -> vault.Doc | None:
    row = index.get_chapter(chapter_id)
    if row and row.get("outline_path"):
        path = cfg.abs(str(row["outline_path"]))
        return vault.read_doc(path) if path.exists() else None
    fallback = cfg.dir("outline") / "Chapters" / f"{chapter_id}.md"
    return vault.read_doc(fallback) if fallback.exists() else None


def manuscript_doc(cfg: Config, index: Index, chapter_id: str) -> vault.Doc | None:
    row = index.get_chapter(chapter_id)
    if row and row.get("manuscript_path"):
        path = cfg.abs(str(row["manuscript_path"]))
        return vault.read_doc(path) if path.exists() else None
    for path in (cfg.dir("manuscripts")).rglob(f"{chapter_id}.md"):
        doc = vault.read_doc(path)
        if doc.meta.get("type") == "manuscript":
            return doc
    return None


def recent_chapters(
    cfg: Config, index: Index, chapter_id: str, count: int = 3
) -> list[dict[str, Any]]:
    """取当前章之前、最近的 N 章正式正文。"""
    from ..vault import chapter_num

    try:
        cur = chapter_num(chapter_id)
    except Exception:  # noqa: BLE001
        cur = 10**9
    rows = [
        r
        for r in index.all_chapters()
        if r.get("manuscript_path") and _chapter_sort_key(r["chapter_id"])[0] < cur
    ]
    rows.sort(key=lambda r: _chapter_sort_key(r["chapter_id"])[0], reverse=True)
    out: list[dict[str, Any]] = []
    for row in rows[:count]:
        doc = manuscript_doc(cfg, index, str(row["chapter_id"]))
        if doc:
            out.append(
                {
                    "chapter_id": row["chapter_id"],
                    "title": doc.meta.get("title") or doc.meta.get("name"),
                    "status": doc.meta.get("status"),
                    "body": doc.body,
                    "word_count": doc.meta.get("word_count") or 0,
                }
            )
    return out


def hot_memory(cfg: Config) -> dict[str, str]:
    files = {
        "current_volume": cfg.dir("hot_memory") / "Current_Volume.md",
        "current_story_state": cfg.dir("hot_memory") / "Current_Story_State.md",
        "active_context": cfg.dir("short_term_memory") / "Active_Context.md",
        "rolling_summary": cfg.dir("short_term_memory") / "Rolling_Summary.md",
    }
    out: dict[str, str] = {}
    for key, path in files.items():
        if path.exists():
            out[key] = vault.read_doc(path).body.strip()
    return out


def style_reference(cfg: Config, limit_chars: int = 4000) -> str:
    base = cfg.dir("short_term_memory") / "Style_Reference"
    chunks: list[str] = []
    if base.exists():
        for path in sorted(base.glob("*.md")):
            chunks.append(vault.read_doc(path).body.strip())
    return "\n\n".join(chunks)[:limit_chars]


def world_rules(cfg: Config) -> list[str]:
    world_dir = cfg.dir("core_memory") / "World"
    rules: list[str] = []
    if world_dir.exists():
        for path in sorted(world_dir.glob("*.md")):
            doc = vault.read_doc(path)
            rules.extend(vault.bullets(doc.section("世界规则（硬约束）")))
            rules.extend(vault.bullets(doc.section("禁忌与不可违反项")))
    return rules


def canon_docs(resolved: Resolved) -> list[dict[str, Any]]:
    out = []
    for doc_id, doc in resolved.docs.items():
        if doc.meta.get("type") in {
            "character",
            "location",
            "faction",
            "item",
            "enemy",
            "world",
            "mission",
        }:
            out.append({"id": doc_id, "meta": doc.meta, "body": doc.body})
    out.sort(key=lambda d: d["id"])
    return out


def related_events(
    cfg: Config, index: Index, entity_ids: Iterable[str], chapter_id: str, limit: int = 8
) -> list[dict[str, Any]]:
    wanted = set(entity_ids)
    from ..vault import chapter_num

    try:
        cur = chapter_num(chapter_id)
    except Exception:  # noqa: BLE001
        cur = 10**9
    out: list[dict[str, Any]] = []
    for row in index.conn.execute("SELECT * FROM events").fetchall():
        event = dict(row)
        participants = set(loads(event.get("participants"), []))
        if participants & wanted or event.get("location_id") in wanted:
            try:
                if chapter_num(str(event.get("chapter_id"))) > cur:
                    continue
            except Exception:  # noqa: BLE001
                pass
            doc = None
            if event.get("file_path"):
                path = cfg.abs(str(event["file_path"]))
                doc = vault.read_doc(path) if path.exists() else None
            out.append({"event": event, "body": doc.body if doc else ""})
    out.sort(key=lambda e: (chapter_num(str(e["event"].get("chapter_id"))), e["event"].get("sequence") or 0))
    return out[-limit:]


def foreshadow_entries(index: Index, ids: Iterable[str]) -> list[dict[str, Any]]:
    wanted = {i for i in ids if i}
    entries = []
    for row in index.foreshadowing_all():
        if not wanted or row["id"] in wanted:
            entries.append(row)
    return entries


def chapters_with_foreshadowing(
    index: Index, entity_ids: Iterable[str], chapter_id: str
) -> list[dict[str, Any]]:
    """与本章实体相关、但没写进「关联伏笔」的伏笔，用于提示遗漏。"""
    wanted = {i for i in entity_ids if i}
    out = []
    for row in index.foreshadowing_all():
        trigger_text = str(row.get("trigger_conditions") or "")
        if any(doc_id in trigger_text for doc_id in wanted) and row.get("planted_in") != chapter_id:
            out.append(row)
    return out


def search(cfg: Config, index: Index, query: str, limit: int = 10) -> list[dict[str, Any]]:
    return index.search_chapters(query, limit)


def tags(cfg: Config, index: Index, tag: str) -> list[dict[str, Any]]:
    out = []
    for row in index.all_entities():
        if tag in loads(row.get("tags"), []):
            out.append(row)
    return out


def read_file(cfg: Config, rel_path: str | Path) -> str:
    path = cfg.abs(str(rel_path))
    return path.read_text(encoding="utf-8") if path.exists() else ""
