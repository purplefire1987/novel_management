"""审核与发布：总编审稿 → 作者确认 → 正文锁定（PRD 12.1 第 1-4 步）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .. import vault
from ..agents.chief_editor import ChiefEditorAgent
from ..audit.logger import AuditLog
from ..audit.reports import issues_table, write_report
from ..config import Config
from ..database.sqlite import Index, now_iso
from ..memory import context_builder, retrieval
from ..transaction import Transaction, commit_artifacts
from ..validators import continuity
from ..validators.schema import Issue
from .state_machine import WorkflowError, chapter_status, ensure_transition, with_status
from .writing import body_word_count, draft_path, load_draft


@dataclass
class ReviewResult:
    chapter_id: str
    verdict: str
    issues: list[Issue] = field(default_factory=list)
    report_path: str = ""
    word_count: int = 0
    mode: str = "offline"
    status_after: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "verdict": self.verdict,
            "issues": [i.to_dict() for i in self.issues],
            "report": self.report_path,
            "word_count": self.word_count,
            "mode": self.mode,
            "status_after": self.status_after,
        }


def _hard_checks(cfg: Config, draft_body: str, outline_body: str) -> list[Issue]:
    issues: list[Issue] = []
    text = vault.extract_section(draft_body, "正文")
    words = body_word_count(draft_body)

    if "【离线占位草稿】" in draft_body:
        issues.append(
            Issue(
                "MAJOR",
                "draft",
                "草稿是离线占位骨架（未配置 LLM 且未经作者改写），不能直接作为正文发布",
                blocks_write=True,
            )
        )
    if words == 0:
        issues.append(Issue("BLOCKER", "draft", "正文为空", blocks_write=True))
    elif words < 800:
        issues.append(
            Issue("MINOR", "draft", f"正文仅 {words} 字，短于常规章节（示例参考值 2000+）", blocks_write=False)
        )

    issues.extend(continuity.check_world_rule_text(text, retrieval.world_rules(cfg)))

    # 大纲「必须发生的剧情事实」：只对带引号的封闭表述做关键词级核对，
    # 其余自然语言断言交给 LLM 审核，避免规则误报。
    must = vault.bullets(vault.extract_section(outline_body, "必须发生的剧情事实"))
    quoted_re = re.compile(r"[「“\"]([^」”\"]{2,30})[」”\"]")
    missing = []
    for item in must:
        quotes = quoted_re.findall(item)
        if quotes and not any(q in text for q in quotes):
            missing.append(f"{item[:28]}（未见：{quotes[0]}）")
    if missing:
        issues.append(
            Issue(
                "MINOR",
                "outline_alignment",
                "大纲中的引号原句未在正文出现，请人工确认是否等价改写：" + "；".join(missing[:3]),
            )
        )
    return issues


def run(cfg: Config, index: Index, chapter_id: str) -> ReviewResult:
    draft = load_draft(cfg, chapter_id)
    if not draft:
        raise WorkflowError(f"找不到草稿：{chapter_id}。先运行 `novel write {chapter_id}`。")

    outline = retrieval.outline_doc(cfg, index, chapter_id)
    if not outline:
        raise WorkflowError(f"找不到大纲：{chapter_id}")

    issues = _hard_checks(cfg, draft.body, outline.body)
    pack_text = context_builder.load_pack(cfg, chapter_id) or ""
    material = "\n\n".join(
        [
            "## 待审正文",
            draft.body.strip()[:12000],
            "## 章节大纲",
            outline.body.strip(),
            "## 参考资料包（节选）",
            pack_text[:4000],
        ]
    )

    chief = ChiefEditorAgent(cfg, index)
    review = chief.review(
        stage="chapter_review",
        chapter_id=chapter_id,
        material=material,
        hard_issues=issues,
        author_decisions=[],
    )
    verdict = review.status

    report_body = "\n".join(
        [
            f"# 章节审核｜{chapter_id}",
            "",
            "## 结论",
            f"**{verdict}**",
            "",
            "## 问题清单",
            issues_table(issues),
            "",
            "## 总编意见",
            review.content,
            "",
            "## 引用来源",
            *([f"- {s}" for s in review.sources] or ["- 无"]),
        ]
    )
    report_rel = write_report(
        cfg,
        "review",
        f"REVIEW-{chapter_id}",
        report_type="Chapter_Review",
        target=chapter_id,
        verdict=verdict,
        body=report_body,
        generated_by=f"chief_editor/{review.mode}",
        extra_meta={"word_count": body_word_count(draft.body)},
    )

    status_after = chapter_status(cfg, index, chapter_id)
    if verdict == "ACCEPTED":
        new_meta = with_status(draft.meta, "editor_approved")
        vault.write_atomic(draft_path(cfg, chapter_id), vault.render_doc(new_meta, draft.body))
        status_after = "editor_approved"
        AuditLog(cfg, index).record(
            operation="review_pass",
            target=chapter_id,
            agent="chief_editor",
            actor="system",
            status="editor_approved",
            detail=report_rel,
        )
    else:
        AuditLog(cfg, index).record(
            operation="review",
            target=chapter_id,
            agent="chief_editor",
            actor="system",
            status=verdict,
            detail=report_rel,
        )

    commit_artifacts(cfg, f"review report {chapter_id}")
    return ReviewResult(
        chapter_id=chapter_id,
        verdict=verdict,
        issues=issues,
        report_path=report_rel,
        word_count=body_word_count(draft.body),
        mode=review.mode,
        status_after=status_after,
    )


def publish(cfg: Config, index: Index, chapter_id: str, confirmed: bool = False) -> dict[str, Any]:
    """作者确认后，把 staging 草稿锁成正式正文。"""
    if not confirmed:
        raise WorkflowError(
            "发布正式正文需要作者明确确认（CLI 传 --yes）。这是人为闸门，Agent 不能代劳。"
        )
    draft = load_draft(cfg, chapter_id)
    if not draft:
        raise WorkflowError(f"找不到草稿：{chapter_id}")

    status = str(draft.meta.get("status") or "draft")
    if status == "draft":
        reviewer_report = cfg.abs(f"09_Audit_Reports/Chapter_Review/REVIEW-{chapter_id}.md")
        if not reviewer_report.exists():
            raise WorkflowError(f"{chapter_id} 尚未经过总编审核，先运行 `novel review {chapter_id}`。")
        raise WorkflowError(
            f"{chapter_id} 总编审核未通过（状态 {status}），需先修订并重新 review。"
        )
    if status == "editor_approved":
        # 作者确认即同时完成 author_approved → published 两步
        ensure_transition(cfg, status, "author_approved")
        ensure_transition(cfg, "author_approved", "published")
    elif status == "author_approved":
        ensure_transition(cfg, status, "published")
    else:
        raise WorkflowError(f"{chapter_id} 当前状态 {status}，不能发布")

    outline = retrieval.outline_doc(cfg, index, chapter_id)
    volume_id = (outline.meta.get("volume_id") if outline else None) or "VOL-0001"
    title = str(draft.meta.get("title") or chapter_id)
    words = body_word_count(draft.body)

    meta = dict(draft.meta)
    meta.update(
        {
            "type": "manuscript",
            "status": "published",
            "word_count": words,
            "volume_id": volume_id,
            "outline_ref": chapter_id,
            "preflight_ref": f"PREFLIGHT-{chapter_id}",
            "updated_at": now_iso()[:10],
        }
    )

    rel = f"08_Manuscripts/{volume_id.replace('VOL-', 'Volume_')}/{chapter_id}.md"
    if volume_id == "VOL-0001":
        rel = f"08_Manuscripts/Volume_01/{chapter_id}.md"

    tx = Transaction(
        cfg,
        index,
        actor="author",
        agent="author",
        operation="publish",
        target=chapter_id,
    )
    tx.stage_doc(rel, meta, draft.body)
    result = tx.commit(f"publish {chapter_id}")
    result["path"] = rel
    result["word_count"] = words
    commit_artifacts(cfg, f"publish {chapter_id}")

    # 正文一落盘，人物/势力/物品的出场章节就变了：立刻重算地图投影，
    # 免得地图页要等到下次 rebuild-index 才跟上。失败不影响发布本身。
    try:
        from . import map_sync, relation_sync

        result["map_sync"] = map_sync.run(cfg)
        # 同一时刻，人物页的「出场章节」也会影响关系索引里的活跃度排序
        result["relation_sync"] = relation_sync.run(cfg)
    except Exception as exc:  # noqa: BLE001 - 派生页失败不该回滚已经发布的正文
        result["map_sync_error"] = str(exc)
    return result
