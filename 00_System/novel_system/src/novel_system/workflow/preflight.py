"""写前 Preflight：检索 → 硬规则 → 总编裁决 → 资料包（PRD 11 / 16.1）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import vault
from ..agents.chief_editor import ChiefEditorAgent
from ..agents.lore_keeper import LoreKeeperAgent
from ..agents.story_editor import StoryEditorAgent
from ..audit.logger import AuditLog
from ..audit.reports import issues_table, write_report
from ..config import Config
from ..database.sqlite import Index
from ..memory import context_builder, retrieval
from ..memory.entity_resolver import Resolved, resolve_chapter
from ..transaction import Transaction, commit_artifacts
from ..validators import canon, continuity, foreshadowing
from ..validators.schema import Issue
from .state_machine import WorkflowError, with_status


@dataclass
class PreflightResult:
    chapter_id: str
    verdict: str
    issues: list[Issue] = field(default_factory=list)
    author_decisions: list[str] = field(default_factory=list)
    pack_path: str = ""
    report_path: str = ""
    editor_report: str = ""
    lore_report: str = ""
    modes: dict[str, str] = field(default_factory=dict)
    status_changed: str = ""

    @property
    def blocked(self) -> bool:
        return self.verdict in ("BLOCKED", "AUTHOR_APPROVAL_REQUIRED", "REVISION_REQUIRED")

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "verdict": self.verdict,
            "issues": [i.to_dict() for i in self.issues],
            "author_decisions": self.author_decisions,
            "pack": self.pack_path,
            "report": self.report_path,
            "modes": self.modes,
            "status_changed": self.status_changed,
        }


def _issues_from_agent(data: dict[str, Any]) -> list[Issue]:
    out: list[Issue] = []
    for raw in data.get("issues") or []:
        if not isinstance(raw, dict):
            continue
        out.append(
            Issue(
                str(raw.get("severity") or "MINOR").upper(),
                str(raw.get("category") or "agent"),
                str(raw.get("detail") or raw.get("message") or ""),
                refs=[str(r) for r in (raw.get("refs") or [])],
                blocks_write=bool(raw.get("blocks_write", False)),
            )
        )
    return out


def collect_hard_checks(cfg: Config, index: Index, resolved: Resolved) -> list[Issue]:
    """纯规则检查：不调用 LLM，可单测。"""
    issues: list[Issue] = []
    unknown_blocking = bool(cfg.guardrails.get("block_on_unknown_entity", False))

    for label, ids in (
        ("出场人物", resolved.cast_ids),
        ("场景", resolved.scene_ids),
        ("伏笔", resolved.foreshadow_ids),
        ("地点", resolved.location_ids),
    ):
        issues.extend(
            canon.check_refs_exist(
                ids,
                index.exists,
                category="entity_ref",
                blocking=unknown_blocking,
                context=f"大纲中的{label}",
            )
        )

    issues.extend(
        continuity.check_cast(cfg, resolved.cast_ids, resolved.states_by_character, index.exists)
    )
    issues.extend(continuity.check_locations(resolved.location_ids, index.exists))

    # 伏笔：本章点名的 + 全账本中已超期的
    for row in index.foreshadowing_all():
        meta = {
            "id": row["id"],
            "status": row["status"],
            "planted_in": row.get("planted_in"),
            "target_reveal_window": {
                "start": row.get("target_start"),
                "end": row.get("target_end"),
            },
            "trigger_conditions": [],
        }
        relevant = row["id"] in resolved.foreshadow_ids
        if relevant:
            issues.extend(foreshadowing.check_ledger_entry(meta, resolved.chapter_id))
        else:
            start, end = foreshadowing.window(meta)
            try:
                cur = vault.chapter_num(resolved.chapter_id)
            except Exception:  # noqa: BLE001
                cur = 0
            if row["status"] not in foreshadowing.TERMINAL and cur > end:
                issues.append(
                    Issue(
                        "MAJOR",
                        "foreshadowing",
                        f"{row['id']} 已超期未回收（最晚 CH-{end:04d}，当前 {resolved.chapter_id}）",
                        refs=[row["id"]],
                    )
                )

    return issues


def run(cfg: Config, index: Index, chapter_id: str) -> PreflightResult:
    outline = retrieval.outline_doc(cfg, index, chapter_id)
    if not outline:
        raise WorkflowError(f"找不到章节大纲：{chapter_id}（应在 04_Outline/Chapters/）")

    issues: list[Issue] = []
    author_decisions: list[str] = []

    approved = vault.checkbox_checked(outline.body, "已批准写作")
    if cfg.workflow.get("require_outline_approved", True) and not approved:
        issues.append(
            Issue(
                "MAJOR",
                "outline",
                "大纲尚未获得作者批准（`## 作者批准 - [ ] 已批准写作`）",
                refs=[chapter_id],
                blocks_write=True,
            )
        )
        author_decisions.append("请在章节大纲勾选「已批准写作」，或先修改大纲。")

    resolved = resolve_chapter(cfg, index, outline)
    hard_issues = collect_hard_checks(cfg, index, resolved)

    # 编辑检查结构完整性
    editor = StoryEditorAgent(cfg, index)
    editor_result = editor.review_outline(
        chapter_id=chapter_id, outline=outline, scenes=resolved.scenes
    )
    hard_issues.extend(_issues_from_agent(editor_result.data))

    issues.extend(hard_issues)
    for issue in hard_issues:
        if issue.category == "foreshadowing" and "超期" in issue.detail:
            author_decisions.append(f"{issue.detail}：请确认是放弃、延窗还是尽快回收。")

    # 组装资料包
    pack = context_builder.build_pack(cfg, index, resolved, hard_issues)
    pack_rel = context_builder.save_pack(cfg, pack)
    pack_text = pack.render()

    lore = LoreKeeperAgent(cfg, index)
    lore_result = lore.build(
        chapter_id=chapter_id, pack_text=pack_text, resolved=resolved, hard_issues=hard_issues
    )

    material = "\n\n".join(
        [
            "## 章节大纲",
            outline.body.strip(),
            "## 编辑结构检查",
            editor_result.content,
            "## 资料官资料包",
            lore_result.content,
            "## Preflight Pack（节选）",
            pack_text[: int(cfg.budget_chars * 0.6)],
        ]
    )

    chief = ChiefEditorAgent(cfg, index)
    review = chief.review(
        stage="preflight",
        chapter_id=chapter_id,
        material=material,
        hard_issues=hard_issues,
        author_decisions=author_decisions,
    )
    verdict = review.status
    # 总编在离线模式下会把硬规则问题复述一遍，这里去重
    seen = {(i.severity, i.category, i.detail) for i in issues}
    for extra in _issues_from_agent(review.data):
        if (extra.severity, extra.category, extra.detail) not in seen:
            issues.append(extra)
            seen.add((extra.severity, extra.category, extra.detail))

    # 报告
    report_body = "\n".join(
        [
            f"# Preflight｜{chapter_id}",
            "",
            "## 结论",
            f"**{verdict}**",
            "",
            "## 问题清单",
            issues_table(issues),
            "",
            "## 待作者确认",
            *([f"- {d}" for d in author_decisions] or ["- 无"]),
            "",
            "## 上下文预算实际用量",
            "| 区块 | 字符数 | 预算 | 占比 |",
            "|---|---:|---:|---:|",
            *[
                f"| {key} | {stat['chars']} | {stat['budget']} | {stat['ratio']:.0%} |"
                for key, stat in pack.stats.items()
            ],
            "",
            "## 生成物",
            f"- 资料包：`{pack_rel}`",
            f"- 资料官/总编运行模式：lore={lore_result.mode}, chief={review.mode}, editor={editor_result.mode}",
            "",
            "## 引用来源",
            *[f"- {path}" for path in review.sources + editor_result.sources + lore_result.sources],
        ]
    )
    report_rel = write_report(
        cfg,
        "preflight",
        f"PREFLIGHT-{chapter_id}",
        report_type="Preflight",
        target=chapter_id,
        verdict=verdict,
        body=report_body,
        generated_by=f"chief_editor/{review.mode}",
        extra_meta={"pack": pack_rel, "issue_count": len(issues)},
    )

    result = PreflightResult(
        chapter_id=chapter_id,
        verdict=verdict,
        issues=issues,
        author_decisions=author_decisions,
        pack_path=pack_rel,
        report_path=report_rel,
        editor_report=editor_result.content,
        lore_report=lore_result.content,
        modes={"editor": editor_result.mode, "lore": lore_result.mode, "chief": review.mode},
    )

    AuditLog(cfg, index).record(
        operation="preflight",
        target=chapter_id,
        agent="chief_editor",
        actor="system",
        status=verdict,
        detail=f"{report_rel}｜issues={len(issues)}",
    )

    # 只有 ACCEPTED 才推进大纲状态
    if verdict == "ACCEPTED":
        tx = Transaction(
            cfg,
            index,
            actor="system_executor",
            agent="system",
            operation="preflight_pass",
            target=chapter_id,
        )
        new_meta = with_status(outline.meta, "preflight_passed")
        tx.stage_doc(cfg.rel(outline.path), new_meta, outline.body)
        commit = tx.commit(f"preflight passed {chapter_id}")
        result.status_changed = commit["operation_id"]

    commit_artifacts(cfg, f"preflight report {chapter_id}")
    return result
