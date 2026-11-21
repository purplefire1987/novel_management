"""发布：把已批准内容转成正式状态。

四道闸，缺一不可：
1. 有作者批准记录；
2. staging 内容与批准时登记的哈希逐个一致（批准后调包不算批准）；
3. staging 里没有未纳入批准的新增文件；
4. 目标落在正式区，且 Canon 目标要作者二次确认。

旧版本先归档再覆盖——发布可以重来，被覆盖的内容不能凭空消失。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..approvals import Approval, ApprovalStore
from ..audit import now_iso
from ..baseline import AuthorBaseline
from ..errors import ApprovalError, ConflictError, SecurityError, UsageError
from ..paths import ProjectPaths
from ..staging import StagingArea, artifact_id_of
from ..storage import copy_verified, sha256_file, write_atomic
from ..tasks import Task, TaskStore

CANON_PREFIX = "01_Canon"


@dataclass
class PublishResult:
    task_id: str
    approval_id: str
    written: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    archive_dir: str = ""
    skipped: list[str] = field(default_factory=list)
    canon_touched: bool = False


def _archive_target(pp: ProjectPaths, task_id: str, target_rel: str, stamp: str) -> Path:
    return pp.archive / "publish_archive" / task_id / stamp / target_rel


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
    """发布前的两道内容闸：未解决的 BLOCKER、未批准的 RETCON。

    这两道**不能靠「记得别按那个键」守**：BLOCKER 被豁免一次，正式版就带着
    已知硬伤出门；RETCON 绕过独立审批一次，「独立」二字就没有意义了。
    所以它们放进 publish 主路径，而不是做成可选的检查命令。
    """
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


def publish_task(
    pp: ProjectPaths,
    task_store: TaskStore,
    approval_store: ApprovalStore,
    task_id: str,
    *,
    actor: str,
    note: str = "",
    confirm_canon: bool = False,
    issue_store=None,
    change_store=None,
) -> PublishResult:
    task = task_store.load(task_id)
    if task.write_mode == "read_only":
        raise SecurityError(f"任务 {task.task_id} 是 read_only，没有可发布内容")
    if task.status == "cancelled":
        raise ConflictError(f"任务 {task.task_id} 已取消，不能发布")
    if task.status == "done":
        raise ConflictError(
            f"任务 {task.task_id} 已经发布过；内容有更新请新建任务（novel task create）再走一遍 stage/approve"
        )

    approval = approval_store.latest_for(task_id)
    staging = StagingArea(pp, task)
    check_gates(pp, task, staging, issue_store=issue_store, change_store=change_store)
    targets = check_approval(pp, task, approval, staging)
    assert approval is not None  # check_approval 已保证

    # 目标合法性
    canon_targets = []
    for rel in targets:
        target = pp.at(rel)
        if not pp.is_control_path(target):
            raise SecurityError(f"发布目标不在正式区：{rel}")
        if rel.startswith(CANON_PREFIX + "/"):
            canon_targets.append(rel)
    if canon_targets and not confirm_canon:
        raise SecurityError(
            "本次发布包含 Canon 写入："
            f"{canon_targets}；需要作者额外确认 --confirm-canon"
        )

    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    result = PublishResult(task_id=task_id, approval_id=approval.approval_id, canon_touched=bool(canon_targets))

    staged_map = {item.target: item for item in staging.files()}
    for rel in targets:
        item = staged_map[rel]
        source = pp.vault / item.staged
        target = pp.at(rel)
        if target.exists():
            archived = _archive_target(pp, task_id, rel, stamp)
            copy_verified(target, archived)
            result.archived.append(pp.rel_to_project(archived))
        write_atomic(target, source.read_bytes())
        result.written.append(rel)

    if result.archived:
        result.archive_dir = str(pp.archive / "publish_archive" / task_id / stamp)

    # 任务收尾
    task.output_files = sorted(set(task.output_files) | set(result.written))
    task.approval_state = "approved"
    task.status = "done"
    task.closed_at = now_iso()
    task_store.save(task)

    # 基线与历史
    baseline = AuthorBaseline(task_store.dir.parent, pp.project_id)
    baseline.refresh_paths(pp, result.written)
    baseline.record_publish(task_id=task_id, paths=result.written, note=note)
    return result


def describe_result(result: PublishResult) -> list[str]:
    lines = [f"task      : {result.task_id}", f"approval  : {result.approval_id}"]
    lines.append(f"写入      : {len(result.written)} 个文件")
    lines.extend(f"  + {rel}" for rel in result.written)
    if result.archived:
        lines.append(f"旧版归档  : {result.archive_dir}")
        lines.extend(f"  ~ {rel}" for rel in result.archived)
    if result.canon_touched:
        lines.append("注意      : 本次写入了 01_Canon，请重跑 novel validate 与 novel rebuild-index")
    return lines
