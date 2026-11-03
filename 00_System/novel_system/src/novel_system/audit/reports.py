"""报告输出：09_Audit_Reports 下的各类报告（PRD 16 / 4.1）。"""

from __future__ import annotations

from typing import Any, Iterable

from .. import vault
from ..config import Config
from ..database.sqlite import now_iso
from ..validators.schema import Issue

REPORT_DIRS = {
    "preflight": "Preflight",
    "review": "Chapter_Review",
    "continuity": "Continuity",
    "foreshadow": "Foreshadowing",
    "memory": "Memory_Update",
}

SEVERITY_ORDER = {"BLOCKER": 0, "MAJOR": 1, "MINOR": 2}


def sort_issues(issues: Iterable[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda i: (SEVERITY_ORDER.get(i.severity, 9), i.category))


def issues_table(issues: Iterable[Issue]) -> str:
    issues = sort_issues(list(issues))
    if not issues:
        return "无"
    lines = ["| 严重度 | 类别 | 描述 | 引用 | 阻断 |", "|---|---|---|---|---|"]
    for issue in issues:
        refs = ", ".join(issue.refs) if issue.refs else "-"
        lines.append(
            f"| {issue.severity} | {issue.category} | {issue.detail.replace('|', '/')} | {refs} | "
            f"{'是' if issue.blocks_write else '否'} |"
        )
    return "\n".join(lines)


def write_report(
    cfg: Config,
    kind: str,
    name: str,
    *,
    report_type: str,
    target: str,
    verdict: str,
    body: str,
    generated_by: str = "system",
    extra_meta: dict[str, Any] | None = None,
) -> str:
    """写一份 Markdown 报告，返回相对路径。"""
    sub = REPORT_DIRS.get(kind)
    if not sub:
        raise ValueError(f"未知报告类型：{kind}")
    rel = f"09_Audit_Reports/{sub}/{name}.md"
    meta = {
        "id": name,
        "type": "audit_report",
        "report_type": report_type,
        "target": target,
        "verdict": verdict,
        "generated_by": generated_by,
        "created_at": now_iso(),
    }
    if extra_meta:
        meta.update(extra_meta)
    vault.write_atomic(cfg.abs(rel), vault.render_doc(meta, body.strip() + "\n"))
    return rel


def read_report(cfg: Config, rel_path: str) -> str | None:
    path = cfg.abs(rel_path)
    return path.read_text(encoding="utf-8") if path.exists() else None
