"""ID 服务：唯一生成入口，Agent 不得自行造 ID（PRD 7.2）。"""

from __future__ import annotations

import re

from .config import Config
from .database.sqlite import Index

SERIAL_RE = re.compile(r"(\d{3,4})(?!.*\d)")


def _existing_ids(index: Index, doc_type: str) -> list[str]:
    return [str(row["id"]) for row in index.entities_by_type(doc_type)]


def next_serial(numbers: list[int]) -> int:
    return (max(numbers) if numbers else 0) + 1


def _numbers(ids: list[str], pattern_tail: str | None = None) -> list[int]:
    out: list[int] = []
    for doc_id in ids:
        if pattern_tail and not doc_id.startswith(pattern_tail):
            continue
        matches = SERIAL_RE.findall(doc_id)
        if matches:
            out.append(int(matches[-1]))
    return out


def next_id(cfg: Config, index: Index, doc_type: str) -> str:
    """生成下一个实体 ID，例如 CHAR-0004。"""
    prefix = (cfg.id_rules.get("prefixes") or {}).get(doc_type)
    if not prefix:
        raise ValueError(f"未定义 ID 前缀：{doc_type}")
    width = int(cfg.id_rules.get("serial_width", 4))

    if doc_type in ("character_state", "event", "scene"):
        raise ValueError(f"{doc_type} 需要父级 ID，请用 next_child_id")

    serial = next_serial(_numbers(_existing_ids(index, doc_type), str(prefix)))
    return f"{prefix}-{serial:0{width}d}"


def next_child_id(cfg: Config, index: Index, doc_type: str, parent_id: str) -> str:
    """生成子编号 ID：EVT-CH-0002-001 / SCENE-CH-0002-001 / STATE-CHAR-0004。"""
    if doc_type == "character_state":
        char_serial = _numbers([parent_id], "CHAR")
        if not char_serial:
            raise ValueError(f"无法从 {parent_id} 解析人物序号")
        return f"STATE-CHAR-{char_serial[-1]:04d}"

    if doc_type == "event":
        ids = [str(r["event_id"]) for r in index.events_of_chapter(parent_id)]
        width = int(((cfg.id_rules.get("composite") or {}).get("event") or {}).get("width", 3))
        serial = next_serial(_numbers(ids, f"EVT-{parent_id}-"))
        return f"EVT-{parent_id}-{serial:0{width}d}"

    if doc_type == "scene":
        ids = [
            str(r["id"])
            for r in index.entities_by_type("scene_beat")
            if str(r["id"]).startswith(f"SCENE-{parent_id}-")
        ]
        width = int(((cfg.id_rules.get("composite") or {}).get("scene") or {}).get("width", 3))
        serial = next_serial(_numbers(ids, f"SCENE-{parent_id}-"))
        return f"SCENE-{parent_id}-{serial:0{width}d}"

    raise ValueError(f"未知子编号类型：{doc_type}")
