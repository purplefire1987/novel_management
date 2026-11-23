"""发布：把已批准内容转成正式状态。

对外只暴露一件事：**一次发布是一次可恢复的受审计操作**，不是「把文件覆盖过去」。

```
prepare  → 生成 ReleasePlan（作者要批的就是它）
approve  → 作者批准计划哈希（`novel approve release <REL> --hash ...`）
execute  → 22 步：隔离快照 → 候选提交 → 建索引 → 清单 → 切换指针 → 回执 → 镜像
```

三条硬线：

1. 批准绑的是**计划哈希**，不是「这个任务我同意」；
2. 正式版本只由 `releases/active.json` 决定，写入走「临时文件 + fsync + 原子替换」；
3. 任何一步崩了都能 `release resume` / `release abort` / `release rollback`，
   恢复依据是指针、清单、日志、哈希的交叉验证。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..audit import now_iso
from ..baseline import AuthorBaseline
from ..domain.errors import ApprovalError, ConflictError, SecurityError, UsageError
from ..infrastructure.filesystem.paths import ProjectPaths
from ..staging import StagingArea
from ..tasks import TaskStore
from .engine import (
    ExecutionResult,
    InjectedFault,
    abort,
    execute,
    prepare_plan,
    prepare_rollback,
    repair_receipt,
    resume,
    status,
)
from .gates import CANON_PREFIX, check_approval, check_gates
from .lock import PublishLock, read_holder
from .model import ReleasePlan
from .store import PlanMeta, ReleaseStore

__all__ = [
    "CANON_PREFIX",
    "ExecutionResult",
    "InjectedFault",
    "PlanMeta",
    "PublishLock",
    "ReleasePlan",
    "ReleaseStore",
    "abort",
    "check_approval",
    "check_gates",
    "execute",
    "prepare_plan",
    "prepare_rollback",
    "publish_task",
    "read_holder",
    "repair_receipt",
    "resume",
    "status",
]


@dataclass
class PublishResult:
    """`publish` 的返回，保留旧字段以兼容调用方与测试。"""

    task_id: str
    approval_id: str
    release_id: str = ""
    receipt_id: str = ""
    canon_revision: str = ""
    index_build_id: str = ""
    written: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    archive_dir: str = ""
    skipped: list[str] = field(default_factory=list)
    canon_touched: bool = False
    warnings: list[str] = field(default_factory=list)


def publish_task(
    pp: ProjectPaths,
    runtime: Path,
    task_store: TaskStore,
    approval_store,
    task_id: str,
    *,
    actor: str,
    note: str = "",
    confirm_canon: bool = False,
    issue_store=None,
    change_store=None,
    release_id: str = "",
    skip_dirs: tuple[str, ...] | None = None,
    index_dir=None,
) -> PublishResult:
    """执行一次已批准的发布。

    找不到「已准备且已批准」的计划就拒绝——**不会替作者临时生成一份再当成批过**。
    那种便利的代价是：作者从没看过那份计划，批准就成了橡皮图章。
    """
    task = task_store.load(task_id)
    if task.write_mode == "read_only":
        raise SecurityError(f"任务 {task.task_id} 是 read_only，没有可发布内容")
    if task.status == "cancelled":
        raise ConflictError(f"任务 {task.task_id} 已取消，不能发布")
    if task.status == "completed":
        raise ConflictError(
            f"任务 {task.task_id} 已经发布过；内容有更新请新建任务（novel task create）再走一遍 stage/approve"
        )
    if not StagingArea(pp, task).files():
        raise ConflictError(f"任务 {task.task_id} 的 staging 为空，没有可发布内容")

    store = ReleaseStore(runtime)
    candidates = [release_id] if release_id else store.find_plan_for_task(task_id, pp.project_id)
    if not candidates:
        raise UsageError(
            f"任务 {task_id} 还没有准备发布计划。\n"
            f"  1) novel release prepare --task {task_id}\n"
            f"  2) novel approve release <REL-ID> --hash <计划哈希> --as author\n"
            f"  3) novel publish --task {task_id} --as author --confirm"
        )
    plan_id = candidates[0]
    plan = store.load_plan(plan_id)
    if not approval_store.for_target("release", plan_id):
        raise ApprovalError(
            f"发布计划 {plan_id} 尚未获作者批准。"
            f"运行：novel approve release {plan_id} --hash {plan.hash} --as author"
        )

    result = execute(
        pp,
        store,
        plan_id,
        actor=actor,
        approval_store=approval_store,
        issue_store=issue_store,
        change_store=change_store,
        confirm_canon=confirm_canon,
        skip_dirs=skip_dirs,
        index_dir=index_dir,
        task=task,
    )

    if result.state == "ACTIVE":
        task.output_files = sorted(set(task.output_files) | set(result.written))
        task.approval_state = "approved"
        task_store.mark_published(task, actor=actor, release_id=plan_id)

    canon_touched = any(c.path.startswith(CANON_PREFIX + "/") for c in plan.changes)
    baseline = AuthorBaseline(runtime, pp.project_id)
    baseline.refresh_paths(pp, result.written)
    baseline.record_publish(task_id=task_id, paths=result.written, note=note)

    return PublishResult(
        task_id=task_id,
        approval_id=plan.text_approvals[0] if plan.text_approvals else "",
        release_id=result.release_id,
        receipt_id=result.receipt_id,
        canon_revision=result.canon_revision,
        index_build_id=result.index_build_id,
        written=result.written,
        archived=result.archived,
        archive_dir="",
        canon_touched=canon_touched,
        warnings=result.warnings,
    )


def describe_result(result: PublishResult) -> list[str]:
    lines = [f"task      : {result.task_id}"]
    if result.release_id:
        lines.append(f"release   : {result.release_id}（{result.canon_revision[:12] or '无提交'}）")
    if result.index_build_id:
        lines.append(f"index     : {result.index_build_id}")
    if result.receipt_id:
        lines.append(f"receipt   : {result.receipt_id}")
    lines.append(f"写入      : {len(result.written)} 个文件")
    lines.extend(f"  + {rel}" for rel in result.written)
    if result.archived:
        lines.append(f"旧版归档  : {len(result.archived)} 个")
        lines.extend(f"  ~ {rel}" for rel in result.archived)
    if result.warnings:
        lines.append("告警      :")
        lines.extend(f"  ! {w}" for w in result.warnings)
    if result.canon_touched:
        lines.append("注意      : 本次写入了 01_Canon，请重跑 novel validate 与 novel rebuild-index")
    return lines
