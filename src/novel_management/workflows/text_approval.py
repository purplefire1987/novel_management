"""正文批准：作者批的是「这一份字节」，不是「这一章我同意了」。

PRD §14.4 要求批精确对象：`target_type=ARTIFACT`、`target_id=CH-xxxx`、
`target_hash=<sha256>`、`approved_scope=[CHAPTER_TEXT]`。本模块把三件事串起来：

1. 拿 staging 里**当前这一份**草稿算哈希；
2. 先过审核闸（`workflows.review.status`）——没审、没审全、有 BLOCKER 都不批；
3. 写批准记录，哈希绑到字节上。

## 为什么批准前要查审核，而不是「作者想批就批」

作者当然有权拍板。但「作者拍板」与「系统分不清拍板与随手点一下」是两件事。
没有审核前置时，`approve text` 会变成发布流程里的一个确认弹窗——
点过去就完了，风险路由与返修预算全部形同虚设。

所以这里要求：结论是 APPROVE、按风险等级该过的角色都过过、没有 BLOCKER。
不满足就拒绝，并说清缺什么。作者要强行推进，走的是「改规划 / 换角色重审」，
不是加一个 `--force`。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..approvals import Approval, ApprovalFile, ApprovalStore
from ..audit import AUTHOR
from ..domain.errors import ApprovalError, SecurityError
from ..infrastructure.filesystem.paths import ProjectPaths
from ..infrastructure.filesystem.storage import sha256_file
from . import review as review_mod

SCOPE_CHAPTER_TEXT = "CHAPTER_TEXT"


@dataclass
class TextApprovalResult:
    approval: Approval
    chapter_id: str
    target: str
    sha256: str
    risk_level: str
    reviewers: list[str]
    note: str = ""


def approve_text(
    pp: ProjectPaths,
    runtime: Path,
    *,
    task_id: str,
    chapter_id: str,
    store: ApprovalStore,
    actor: str = AUTHOR,
    note: str = "",
    issue_store=None,
) -> TextApprovalResult:
    """给这一章的当前草稿下作者批准。"""
    if actor != AUTHOR:
        raise SecurityError(f"正文批准只能由作者做（收到 {actor!r}）")

    path, target, _ = review_mod.draft_of(pp, runtime, task_id, chapter_id)
    digest = sha256_file(path)

    # ---- 闸一：审核 ----
    state = review_mod.status(runtime, task_id=task_id, chapter_id=chapter_id)
    if state.verdict == "NOT_REVIEWED":
        raise ApprovalError(
            f"{chapter_id} 还没有审核记录。先 `novel review --task {task_id} --chapter {chapter_id} --as editor --verdict approve`"
        )
    if state.verdict != "APPROVE" or not state.passed:
        detail = []
        if state.verdict in ("REVISE", "BLOCKED"):
            detail.append(f"审核结论 {state.verdict}")
        if state.missing_reviewers:
            detail.append("还缺角色：" + "、".join(state.missing_reviewers))
        if state.blockers:
            detail.append(f"{len(state.blockers)} 条 BLOCKER")
        raise ApprovalError(
            f"{chapter_id} 还没过审核（{state.risk_level or '风险未定'}）："
            + "；".join(detail or ["审核未形成通过结论"])
            + "。正文批准要求先审通过——这一步不是橡皮图章"
        )

    # ---- 闸二：登记的 BLOCKER ----
    if issue_store is not None:
        blockers = issue_store.open_blockers({chapter_id, task_id}, project_id=pp.project_id)
        if blockers:
            names = "、".join(getattr(item, "issue_id", str(item)) for item in blockers)
            raise ApprovalError(f"存在未解决的 BLOCKER（{names}），不能批准正文")

    approval = store.record(
        task_id=task_id,
        project_id=pp.project_id,
        files=[ApprovalFile(target=target, staged=_vault_rel(pp, path), sha256=digest, bytes=path.stat().st_size)],
        note=note or f"{chapter_id} 正文批准",
        approver=actor,
        target_type="artifact",
        target_id=chapter_id,
        target_hash=digest,
        approved_scope=[SCOPE_CHAPTER_TEXT],
    )
    return TextApprovalResult(
        approval=approval,
        chapter_id=chapter_id,
        target=target,
        sha256=digest,
        risk_level=state.risk_level,
        reviewers=list(state.approved_by),
        note=note,
    )


def current(store: ApprovalStore, chapter_id: str) -> Approval | None:
    """这一章最近一次正文批准。"""
    records = [item for item in store.for_target("artifact", chapter_id) if SCOPE_CHAPTER_TEXT in item.approved_scope]
    return records[-1] if records else None


def is_valid(approval: Approval | None, current_sha: str) -> bool:
    """批准还有效吗。哈希变了就失效——这是设计，不是故障。"""
    if approval is None:
        return False
    return approval.target_hash == current_sha and approval.decision == "approved"


def _vault_rel(pp: ProjectPaths, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(pp.vault.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


__all__ = ["SCOPE_CHAPTER_TEXT", "TextApprovalResult", "approve_text", "current", "is_valid"]
