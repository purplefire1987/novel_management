"""文件状态机：章节只能在既定顺序里前进（PRD 10.2 / 16）。"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..config import Config
from ..database.sqlite import Index


class WorkflowError(RuntimeError):
    """工作流不允许的操作。"""


def transitions(cfg: Config) -> dict[str, list[str]]:
    raw = cfg.workflow.get("transitions") or {}
    return {str(k): list(v or []) for k, v in raw.items()}


def states(cfg: Config) -> list[str]:
    return list(cfg.workflow.get("states") or [])


def can_transition(cfg: Config, current: str, new: str) -> bool:
    return new in transitions(cfg).get(current, [])


def ensure_transition(cfg: Config, current: str, new: str) -> None:
    if current == new:
        return
    if new not in states(cfg):
        raise WorkflowError(f"未知状态：{new}")
    if not can_transition(cfg, current, new):
        allowed = transitions(cfg).get(current, [])
        raise WorkflowError(
            f"状态迁移非法：{current} → {new}（允许：{allowed or '无，终态'}）"
        )


def status_of(meta: dict[str, Any]) -> str:
    return str(meta.get("status") or "outline")


def chapter_status(cfg: Config, index: Index, chapter_id: str) -> str:
    """正文状态优先；没有正文时用大纲状态。"""
    row = index.get_chapter(chapter_id)
    if not row:
        return "outline"
    manuscript_path = row.get("manuscript_path")
    if manuscript_path:
        return str(row.get("status") or "draft")
    return str(row.get("status") or "outline")


def with_status(meta: dict[str, Any], status: str) -> dict[str, Any]:
    updated = dict(meta)
    updated["status"] = status
    updated["updated_at"] = date.today().isoformat()
    return updated


def assert_state(cfg: Config, current: str, expected: list[str], action: str) -> None:
    if current not in expected:
        raise WorkflowError(
            f"当前状态 {current} 不允许执行 {action}（需要：{', '.join(expected)}）"
        )


def next_state_hint(cfg: Config, current: str) -> str:
    nxt = transitions(cfg).get(current) or []
    return nxt[0] if nxt else "（终态）"
