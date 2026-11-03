"""伏笔硬规则引擎（PRD 13）。时间与状态归规则引擎，叙事质量归 LLM。"""

from __future__ import annotations

from typing import Any, Iterable

from ..vault import chapter_num
from .schema import Issue, FORESHADOW_STATUSES

# 合法状态迁移：from -> {to: 附加条件}
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "planned": {"planted", "abandoned"},
    "planted": {"hinted", "triggered", "paid_off", "abandoned", "overdue"},
    "hinted": {"triggered", "paid_off", "abandoned", "overdue"},
    "triggered": {"paid_off", "overdue"},
    "overdue": {"paid_off", "abandoned"},
    "paid_off": set(),
    "abandoned": set(),
}

TERMINAL = {"paid_off", "abandoned"}


def window(meta: dict[str, Any]) -> tuple[int, int]:
    win = meta.get("target_reveal_window") or {}
    try:
        start = chapter_num(str(win.get("start")))
        end = chapter_num(str(win.get("end")))
    except Exception:  # noqa: BLE001 - 非法窗口由 schema 层报错
        return (0, 10**6)
    if end < start:
        start, end = end, start
    return start, end


def check_ledger_entry(meta: dict[str, Any], current_chapter: str | None = None) -> list[Issue]:
    """单条伏笔的静态检查（可带当前章节上下文）。"""
    issues: list[Issue] = []
    fid = str(meta.get("id") or "?")
    status = str(meta.get("status") or "")
    if status not in FORESHADOW_STATUSES:
        issues.append(
            Issue("MAJOR", "foreshadowing", f"伏笔状态非法：{status}", refs=[fid], blocks_write=True)
        )
        return issues

    start, end = window(meta)
    planted = meta.get("planted_in")
    if planted:
        try:
            if chapter_num(str(planted)) > start:
                issues.append(
                    Issue(
                        "MINOR",
                        "foreshadowing",
                        f"{fid} 埋设章 {planted} 晚于回收窗口起点 {start}，窗口可能写反了",
                        refs=[fid],
                    )
                )
        except Exception:  # noqa: BLE001
            pass

    if current_chapter:
        cur = chapter_num(current_chapter)
        if status in ("paid_off", "triggered") and cur < start:
            issues.append(
                Issue(
                    "MAJOR",
                    "foreshadowing",
                    f"{fid} 在 {current_chapter} 之前不得进入 {status}（最早回收章为 CH-{start:04d}）",
                    refs=[fid],
                    blocks_write=True,
                )
            )
        if status not in TERMINAL and status != "overdue" and cur > end:
            issues.append(
                Issue(
                    "MAJOR",
                    "foreshadowing",
                    f"{fid} 已超过最晚回收章 CH-{end:04d}（当前 {current_chapter}），应标记 overdue",
                    refs=[fid],
                )
            )
    return issues


def check_transition(
    meta: dict[str, Any],
    new_status: str,
    current_chapter: str,
    evidence: Iterable[str] = (),
) -> list[Issue]:
    """校验一次伏笔状态变更是否合法。"""
    fid = str(meta.get("id") or "?")
    old = str(meta.get("status") or "planted")
    issues: list[Issue] = []
    evidence = [e for e in evidence if e and e.strip()]

    if new_status == old:
        return issues
    if new_status not in FORESHADOW_STATUSES:
        return [
            Issue("MAJOR", "foreshadowing", f"非法目标状态 {new_status}", refs=[fid], blocks_write=True)
        ]
    allowed = LEGAL_TRANSITIONS.get(old, set())
    if new_status not in allowed:
        issues.append(
            Issue(
                "MAJOR",
                "foreshadowing",
                f"{fid} 状态迁移非法：{old} → {new_status}（允许：{sorted(allowed) or '无'}）",
                refs=[fid],
                blocks_write=True,
            )
        )

    start, end = window(meta)
    cur = chapter_num(current_chapter)

    if new_status in ("paid_off", "triggered") and cur < start:
        issues.append(
            Issue(
                "MAJOR",
                "foreshadowing",
                f"{fid} 在 {current_chapter} 回收/触发过早（最早 CH-{start:04d}）",
                refs=[fid],
                blocks_write=True,
            )
        )
    if new_status == "paid_off" and cur > end:
        issues.append(
            Issue(
                "MINOR",
                "foreshadowing",
                f"{fid} 回收晚了（最晚 CH-{end:04d}，实际 {current_chapter}），需作者确认是否仍成立",
                refs=[fid],
            )
        )
    if new_status in ("paid_off", "triggered") and not evidence:
        issues.append(
            Issue(
                "MAJOR",
                "foreshadowing",
                f"{fid} 进入 {new_status} 必须提供事件或章节引用（当前无来源）",
                refs=[fid],
                blocks_write=True,
            )
        )

    conditions = meta.get("trigger_conditions") or []
    if new_status == "triggered" and conditions and not evidence:
        issues.append(
            Issue(
                "MAJOR",
                "foreshadowing",
                f"{fid} 触发条件未验证，不能自动进入 triggered",
                refs=[fid],
                blocks_write=True,
            )
        )
    return issues


def ledger_report(entries: list[dict[str, Any]], current_chapter: str | None = None) -> dict[str, Any]:
    """给报告用的伏笔概览。"""
    buckets: dict[str, list[str]] = {}
    issues: list[Issue] = []
    for meta in entries:
        buckets.setdefault(str(meta.get("status", "unknown")), []).append(str(meta.get("id")))
        issues.extend(check_ledger_entry(meta, current_chapter))
    return {
        "by_status": {k: sorted(v) for k, v in sorted(buckets.items())},
        "issues": issues,
        "total": len(entries),
    }


def relevant_entries(entries: list[dict[str, Any]], ids: Iterable[str]) -> list[dict[str, Any]]:
    wanted = set(ids)
    return [e for e in entries if str(e.get("id")) in wanted]


def is_within_window(meta: dict[str, Any], chapter_id: str) -> bool:
    start, end = window(meta)
    return start <= chapter_num(chapter_id) <= end
