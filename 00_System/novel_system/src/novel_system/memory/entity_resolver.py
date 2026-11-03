"""实体解析：从章节大纲解析出本章涉及的全部实体（PRD 11.2 第 4 步）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .. import vault
from ..config import Config
from ..database.sqlite import Index

COMPOSITE_RE = re.compile(
    r"\b(?:SCENE|EVT)-CH-\d{4}-\d{3}\b|\bSTATE-CHAR-\d{4}\b"
)
SIMPLE_RE = re.compile(
    r"(?<![\w-])(?:FORESHADOW|CHAR|LOC|FAC|ITEM|ENEMY|WORLD|VOL|REL|MISSION|ASSET|CH)"
    r"-\d{4}(?!-\d)\b"
)


def ids_in_text(text: str) -> list[str]:
    """抽取实体 ID。先取复合 ID（SCENE-CH-0002-001），再取简单 ID 并排除重叠。"""
    text = text or ""
    found: list[str] = []
    spans: list[tuple[int, int]] = []
    for match in COMPOSITE_RE.finditer(text):
        found.append(match.group(0))
        spans.append(match.span())
    for match in SIMPLE_RE.finditer(text):
        if any(start <= match.start() < end for start, end in spans):
            continue
        found.append(match.group(0))
    return list(dict.fromkeys(found))


def load_entity_doc(cfg: Config, row: dict[str, Any]) -> vault.Doc | None:
    path = row.get("file_path")
    if not path:
        return None
    abs_path = cfg.abs(str(path))
    return vault.read_doc(abs_path) if abs_path.exists() else None


def name_mentions(cfg: Config, index: Index, text: str, limit: int = 40) -> dict[str, str]:
    """在文本中按 name/aliases 做简单包含匹配，返回 id -> 命中的名字。"""
    hits: dict[str, str] = {}
    if not text:
        return hits
    for row in index.all_entities():
        if row["type"] not in {"character", "location", "faction", "item", "enemy", "world"}:
            continue
        names = [row.get("name") or ""] + [
            a for a in _loads(row.get("aliases")) if isinstance(a, str)
        ]
        for name in names:
            name = name.strip()
            if len(name) >= 2 and name in text:
                hits[str(row["id"])] = name
                break
        if len(hits) >= limit:
            break
    return hits


def _loads(value: Any) -> list[Any]:
    import json

    if not value:
        return []
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


@dataclass
class Resolved:
    chapter_id: str
    outline: vault.Doc
    cast_ids: list[str] = field(default_factory=list)
    foreshadow_ids: list[str] = field(default_factory=list)
    scene_ids: list[str] = field(default_factory=list)
    location_ids: list[str] = field(default_factory=list)
    mentioned_ids: list[str] = field(default_factory=list)
    all_ids: list[str] = field(default_factory=list)
    missing_ids: list[str] = field(default_factory=list)
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    docs: dict[str, vault.Doc] = field(default_factory=dict)
    states_by_character: dict[str, dict[str, Any]] = field(default_factory=dict)
    scenes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def meta(self) -> dict[str, Any]:
        return self.outline.meta


def _section_ids(doc: vault.Doc, heading: str) -> list[str]:
    return ids_in_text(vault.extract_section(doc.body, heading))


def resolve_chapter(cfg: Config, index: Index, outline: vault.Doc) -> Resolved:
    chapter_id = str(outline.meta.get("id") or "")
    res = Resolved(chapter_id=chapter_id, outline=outline)

    cast = _section_ids(outline, "角色出场")
    fs = _section_ids(outline, "关联伏笔")
    scenes = _section_ids(outline, "场景列表")
    location_text = vault.extract_section(outline.body, "时间与地点")
    locations = ids_in_text(location_text)

    # 正文里提到的实体也算（大纲常直接写名字）
    body_ids = ids_in_text(outline.body)
    name_hits = name_mentions(cfg, index, outline.body)

    res.cast_ids = list(dict.fromkeys(cast))
    res.foreshadow_ids = list(dict.fromkeys(fs))
    res.scene_ids = list(dict.fromkeys(scenes))
    res.location_ids = list(dict.fromkeys(locations))
    res.mentioned_ids = list(dict.fromkeys(list(name_hits) + body_ids))
    res.all_ids = list(
        dict.fromkeys(res.cast_ids + res.foreshadow_ids + res.scene_ids + res.location_ids + res.mentioned_ids)
    )

    for doc_id in res.all_ids:
        row = index.get_entity(doc_id)
        if not row:
            res.missing_ids.append(doc_id)
            continue
        res.entities[doc_id] = row
        doc = load_entity_doc(cfg, row)
        if doc:
            res.docs[doc_id] = doc

    # 人物当前状态
    for state in index.entities_by_type("character_state"):
        doc = load_entity_doc(cfg, state)
        if not doc:
            continue
        character_id = str(doc.meta.get("character_id") or "")
        if character_id:
            merged = dict(doc.meta)
            merged.update(vault.key_values(doc.section("客观状态")))
            merged.update(
                vault.key_values(doc.section("认知边界（角色以为的世界，不等于事实）"))
            )
            merged["character_id"] = character_id
            res.states_by_character[character_id] = merged

    # 场景细纲
    for scene_id in res.scene_ids:
        doc = res.docs.get(scene_id)
        if doc:
            res.scenes.append({"id": scene_id, "meta": doc.meta, "body": doc.body})
    return res


def resolve_by_ids(cfg: Config, index: Index, ids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for doc_id in ids:
        row = index.get_entity(doc_id)
        if row:
            out[doc_id] = row
    return out
