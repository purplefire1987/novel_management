"""发布前的三道内容闸：批准、问题、变更。

这三道都不是「提醒」，是**拒绝**：

1. 没有作者批准、或批准后 staging 改过一个字节 → 拒绝；
2. 目标上有 OPEN 的 `BLOCKER` → 拒绝（豁免路径不存在）；
3. 目标路径上有未批准的 `RETCON` → 拒绝（否则「独立审批」是可选装饰）。

放在这里而不是散在命令层，是因为发布会从两个入口进来（`publish --task`、
`release execute`），闸门只要漏在一处就等于不存在。
"""

from __future__ import annotations

from novel_management.application.approvals import Approval
from novel_management.domain.errors import ApprovalError, ConflictError
from novel_management.infrastructure.filesystem.paths import ProjectPaths
from novel_management.application.staging import StagingArea, artifact_id_of
from novel_management.infrastructure.filesystem.storage import sha256_file
from novel_management.application.tasks import Task

CANON_PREFIX = "02_世界知识库"


def check_approval(
    pp: ProjectPaths,
    task: Task,
    approval: Approval | None,
    staging: StagingArea,
) -> list[str]:
    """批准与 staging 是否仍然对得上。返回将发布的 target 列表。"""
    if approval is None:
        raise ApprovalError(
            f"任务 {task.task_id} 没有作者批准记录。先运行 `novel diff` 查看，再由作者 `novel approve`"
        )
    if approval.decision != "approved":
        raise ApprovalError(f"任务 {task.task_id} 最近一次批准结论是 {approval.decision}，不能发布")

    staged = {item.target: item for item in staging.files()}
    if not staged:
        raise ConflictError(f"任务 {task.task_id} 的 staging 为空，没有可发布内容")

    approved_targets = [f.target for f in approval.files]
    extra = sorted(set(staged) - set(approved_targets))
    if extra:
        raise ApprovalError(
            f"staging 中有批准范围外的文件：{extra}（批准只覆盖 {len(approved_targets)} 个文件）"
        )

    for record in approval.files:
        item = staged.get(record.target)
        if item is None:
            raise ApprovalError(f"批准过的文件已不在 staging：{record.target}")
        staged_path = pp.vault / item.staged
        if not staged_path.exists():
            raise ConflictError(f"staging 文件丢失：{item.staged}")
        if sha256_file(staged_path) != record.sha256:
            raise ApprovalError(
                f"{record.target} 在批准后被改动过（哈希不符），批准已失效，需作者重新批准"
            )
    return approved_targets


def check_gates(
    pp: ProjectPaths,
    task: Task,
    staging: StagingArea,
    *,
    issue_store=None,
    change_store=None,
) -> None:
    """发布前的两道内容闸：未解决的 BLOCKER、未批准的 RETCON。"""
    targets = sorted({item.target for item in staging.files()})
    upstream = {task.task_id} | {artifact_id_of(t) for t in targets}

    if issue_store is not None:
        blockers = issue_store.open_blockers(upstream, project_id=pp.project_id)
        if blockers:
            raise ApprovalError(
                "存在未解决的 BLOCKER，拒绝发布："
                + "、".join(f"{i.issue_id}（{i.target_id}：{i.description}）" for i in blockers)
                + "。BLOCKER 只能修掉后 resolve，或先走变更审批缩小范围"
            )

    if change_store is not None:
        pending = change_store.blocking_scope(targets, project_id=pp.project_id)
        if pending:
            raise ApprovalError(
                "这些路径有未批准的 RETCON："
                + "、".join(c.change_id for c in pending)
                + "。先 `novel change approve <CR> --hash <影响报告哈希>`"
            )
