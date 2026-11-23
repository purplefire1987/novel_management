"""流转命令：task / stage / approve / adopt / publish。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import config as cfg_mod
from ..approvals import ApprovalFile
from ..changes import ChangeStore, IssueStore
from ..audit import ensure_actor, file_stamp
from ..errors import SecurityError, UsageError
from ..indexing.source import skip_dirs_from_layout
from ..release import describe_result, publish_task
from ..staging import StagingArea
from ..storage import sha256_file
from ..tasks import TASK_TYPES, WRITE_MODES, Task
from ._common import (
    approval_store,
    baseline,
    heading,
    info,
    ok,
    oplog,
    project,
    require_write,
    task_store,
    warn,
)
from .approval_cmd import approve_object


# --------------------------------------------------------------------------
# task
# --------------------------------------------------------------------------

def cmd_task(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    store = task_store(cfg)
    action = getattr(args, "task_command", None)

    if action == "create":
        pp = project(cfg, args)
        inputs = []
        for raw in getattr(args, "input", None) or []:
            inputs.extend(part.strip() for part in str(raw).split(",") if part.strip())
        scope = getattr(args, "scope", "") or ""
        if scope:
            pp.at(scope)  # 只做合法性校验，不要求存在
        task = store.create(
            project_id=pp.project_id,
            task_type=args.type,
            author_id=cfg.author_id,
            assigned_to=getattr(args, "assignee", "") or "",
            write_mode=getattr(args, "mode", None) or "staging",
            output_scope=scope,
            input_files=inputs,
            notes=getattr(args, "note", "") or "",
        )
        actor = ensure_actor(getattr(args, "actor", None), "task")
        op = oplog(cfg).record(
            actor=actor,
            command="task create",
            project_id=pp.project_id,
            outputs=[{"task_id": task.task_id}],
            note=task.notes,
        )
        print(task.task_id)
        print(f"任务已创建：{task.task_id}（{task.task_type}/{task.write_mode}，op={op.op_id}）")
        print(f"  下一步：novel stage --task {task.task_id} --from <文件> --to <项目内正式路径>")
        return 0

    if action == "inspect":
        task = store.load(args.task_id)
        for line in store.describe(task):
            print(f"  {line}")
        staging = None
        try:
            pp = project(cfg, args)
            staging = StagingArea(pp, task)
        except Exception:  # 项目缺失时仍能看任务本体
            staging = None
        if staging is not None:
            files = staging.files()
            print(f"\n  staging: {len(files)} 个产物（{staging.root}）")
            for item in files:
                print(f"    - {item.target}  {item.sha256[:12]}  {item.staged_at}")
            approval = approval_store(cfg).latest_for(task.task_id)
            if approval:
                print(f"\n  批准: {approval.approval_id} by {approval.approver} at {approval.approved_at}")
            else:
                print("\n  批准: 尚无")
        if getattr(args, "json", False):
            from ..tasks import task_json

            print(task_json(task))
        return 0

    if action == "list":
        # `TaskStore.list()` 一直有实现却没有命令入口，于是「我手上有哪些任务、
        # 卡在哪个状态」只能靠翻 `<runtime>/tasks/*.yaml`。任务状态机没有查询
        # 命令，等于状态机对作者不可见。
        items = store.list(
            project_id=getattr(args, "project", "") or "",
            status=getattr(args, "status", "") or "",
        )
        if getattr(args, "json", False):
            from ..tasks import task_json

            print(json.dumps([json.loads(task_json(t)) for t in items], ensure_ascii=False, indent=2))
            return 0
        if not items:
            print("  没有任务。novel task create --type <类型> 可以建一个。")
            return 0
        heading(f"{len(items)} 个任务")
        for task in items:
            print(
                f"  {task.task_id}  {task.status:<16s} {task.task_type:<12s} "
                f"{task.write_mode:<9s} {task.assigned_to or '—'}"
            )
        return 0

    if action == "transition":
        # `TaskStore.transition()` 一直有实现、有迁移表、有跳步拒绝，却从没有命令入口。
        # 于是 §10.4 的状态机在代码里成立、在操作上不存在——作者只能手改 yaml。
        from ..tasks import TRANSITIONS

        task = store.load(args.task_id)
        before = task.status
        target = (getattr(args, "to", "") or "").strip()
        actor = ensure_actor(getattr(args, "actor", None), "task")
        task = store.transition(
            task,
            target,
            actor=actor,
            reason=getattr(args, "reason", "") or "",
            related_task_ids=getattr(args, "related", None) or [],
        )
        op = oplog(cfg).record(
            actor=actor,
            command="task transition",
            project_id=task.project_id,
            outputs=[{"task_id": task.task_id, "from": before, "to": task.status}],
            note=getattr(args, "reason", "") or "",
        )
        ok(f"{task.task_id}  {before} → {task.status}（op={op.op_id}）")
        nxt = sorted(TRANSITIONS.get(task.status, set()))
        if nxt:
            info("可以继续转到：" + "、".join(nxt))
        else:
            info("这是终态，不能再转。")
        return 0

    raise UsageError("用法：novel task create | list | inspect <task_id> | transition <task_id> --to <状态>")


# --------------------------------------------------------------------------
# stage
# --------------------------------------------------------------------------

def _read_source(raw: str) -> bytes:
    if raw == "-":
        return sys.stdin.buffer.read()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        raise UsageError(f"源文件不存在：{path}")
    return path.read_bytes()


def cmd_stage(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    actor = ensure_actor(getattr(args, "actor", None), "stage")
    pp = project(cfg, args)
    store = task_store(cfg)
    task = store.load(args.task)
    if task.project_id != pp.project_id:
        raise SecurityError(f"任务 {task.task_id} 属于 {task.project_id}，不是 {pp.project_id}")

    data = _read_source(args.source)
    staging = StagingArea(pp, task)
    record = staging.stage(
        data=data,
        target_rel=args.target,
        actor=actor,
        note=getattr(args, "note", "") or "",
        replace=getattr(args, "replace", False),
    )

    op = oplog(cfg).record(
        actor=actor,
        command="stage",
        project_id=pp.project_id,
        inputs=[{"source": args.source}],
        outputs=[{"target": record.target, "staged": record.staged, "sha256": record.sha256}],
        note=record.note,
        task_id=task.task_id,
    )
    ok(f"已写入 staging：{record.staged}")
    info(f"目标正式文件：{record.target}")
    info(f"sha256 {record.sha256[:16]}…  基于正式版本 {record.base_sha256[:12] or '（不存在）'}")
    print(f"  op: {op.op_id}")
    print(f"下一步：novel diff --task {task.task_id}")
    return 0


# --------------------------------------------------------------------------
# approve
# --------------------------------------------------------------------------

def cmd_approve(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    """两条入口：`approve <kind> <candidate> --hash`（T05）与旧的 `approve --task`。

    旧入口保留是因为它已经绑了文件指纹、publish 也照着它校验；新入口只是把
    「批哪个对象」说清楚，并把哈希变成必填。两条路最后都写同一种批准记录。
    """
    kind = getattr(args, "kind", None)
    if kind:
        # `approve text` 指对象的方式是 `--task` + `--chapter`，没有候选 ID 这个说法。
        if kind != "text" and not getattr(args, "candidate", None):
            raise UsageError(f"approve {kind} 需要候选 ID：novel approve {kind} task_000001 --hash <sha256>")
        return approve_object(cfg, args, kind)

    if not getattr(args, "task", None):
        raise UsageError("approve 需要 --task task_000001，或用 approve artifact <candidate> --hash <sha256>")

    actor = ensure_actor(getattr(args, "actor", None), "approve")
    pp = project(cfg, args)
    store = task_store(cfg)
    task = store.load(args.task)
    if task.project_id != pp.project_id:
        raise SecurityError(f"任务 {task.task_id} 属于 {task.project_id}，不是 {pp.project_id}")

    staging = StagingArea(pp, task)
    files = staging.files()
    if not files:
        raise UsageError(f"任务 {task.task_id} 的 staging 为空，没有可批准的内容")

    # 批准前先看冲突：作者手改过的正式文件不能被一份陈旧提案盖掉。
    conflicts = [row for row in staging.diff() if row["kind"] == "conflict"]
    if conflicts and not getattr(args, "force", False):
        for row in conflicts:
            warn(f"{row['target']}：{row['detail']}")
        raise UsageError(
            "存在冲突，拒绝批准。请先处理冲突（作者确认以哪一版为准），或加 --force 显式承担覆盖风险"
        )

    reject = getattr(args, "reject", False)
    store_approvals = approval_store(cfg)
    if reject:
        approval = store_approvals.record(
            task_id=task.task_id,
            project_id=pp.project_id,
            files=[],
            note=getattr(args, "note", "") or "作者驳回",
            approver=actor,
            decision="rejected",
        )
        task.approval_state = "rejected"
        store.save(task)
        oplog(cfg).record(
            actor=actor,
            command="approve",
            project_id=pp.project_id,
            status="rejected",
            outputs=[{"approval_id": approval.approval_id}],
            note=approval.note,
            task_id=task.task_id,
        )
        print(f"已驳回：{approval.approval_id}")
        return 0

    records = [
        ApprovalFile(
            target=item.target,
            staged=item.staged,
            sha256=sha256_file(pp.vault / item.staged),
            bytes=item.bytes,
        )
        for item in files
    ]
    approval = store_approvals.record(
        task_id=task.task_id,
        project_id=pp.project_id,
        files=records,
        note=getattr(args, "note", "") or "",
        approver=actor,
    )
    task.approval_state = "approved"
    store.save(task)
    op = oplog(cfg).record(
        actor=actor,
        command="approve",
        project_id=pp.project_id,
        outputs=[{"approval_id": approval.approval_id, "files": [r.as_dict() for r in records]}],
        note=approval.note,
        task_id=task.task_id,
    )

    ok(f"批准记录：{approval.approval_id}（绑定 {len(records)} 个文件的内容指纹）")
    for record in records:
        info(f"{record.target}  {record.sha256[:16]}…")
    print(f"  op: {op.op_id}")
    print(f"下一步：novel publish --task {task.task_id} --as author --confirm")
    return 0


# --------------------------------------------------------------------------
# adopt
# --------------------------------------------------------------------------

def cmd_adopt(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    actor = ensure_actor(getattr(args, "actor", None), "adopt")
    pp = project(cfg, args)
    base = baseline(cfg, pp)

    if getattr(args, "dry_run", False):
        report = base.compare(pp)
        print(f"项目 {pp.project_id} · 扫描 {report['scanned']} 个受管文件")
        print(f"  新增 {len(report['added'])}，修改 {len(report['modified'])}，删除 {len(report['removed'])}，未变 {report['unchanged']}")
        _print_paths(report)
        print("\nadopt --dry-run：未写入任何状态")
        return 0

    report = base.adopt(pp, actor=actor, note=getattr(args, "note", "") or "")
    op = oplog(cfg).record(
        actor=actor,
        command="adopt",
        project_id=pp.project_id,
        outputs=[
            {
                "added": len(report["added"]),
                "modified": len(report["modified"]),
                "removed": len(report["removed"]),
            }
        ],
        note=getattr(args, "note", "") or "",
    )
    if report["first_time"]:
        print(f"已建立作者基线：{report['scanned']} 个受管文件（首次登记，不产生「改动」记录）")
    else:
        ok(f"已登记作者编辑：新增 {len(report['added'])}，修改 {len(report['modified'])}，删除 {len(report['removed'])}")
        _print_paths(report)
        if not (report["added"] or report["modified"] or report["removed"]):
            info("磁盘与基线一致，没有作者手改")
    print(f"  op: {op.op_id}")
    return 0


def _print_paths(report: dict) -> None:
    for label, key in (("新增", "added"), ("修改", "modified"), ("删除", "removed")):
        paths = report.get(key) or []
        if not paths:
            continue
        print(f"  {label}:")
        for rel in paths[:15]:
            print(f"    - {rel}")
        if len(paths) > 15:
            print(f"    ……另 {len(paths) - 15} 个")


# --------------------------------------------------------------------------
# publish
# --------------------------------------------------------------------------

def cmd_publish(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    actor = ensure_actor(getattr(args, "actor", None), "publish")
    pp = project(cfg, args)
    require_write(args, "confirm", "发布会写正式区，确认请加 --confirm（Canon 还需 --confirm-canon）")

    result = publish_task(
        pp,
        cfg.require_runtime(),
        task_store(cfg),
        approval_store(cfg),
        args.task,
        actor=actor,
        note=getattr(args, "note", "") or "",
        confirm_canon=getattr(args, "confirm_canon", False),
        issue_store=IssueStore(cfg.require_runtime()),
        change_store=ChangeStore(cfg.require_runtime()),
        release_id=getattr(args, "release", "") or "",
        skip_dirs=skip_dirs_from_layout(cfg.layout),
        index_dir=cfg.require_runtime() / "index",
    )
    op = oplog(cfg).record(
        actor=actor,
        command="publish",
        project_id=pp.project_id,
        inputs=[{"approval_id": result.approval_id}],
        outputs=[{"path": rel} for rel in result.written],
        note=getattr(args, "note", "") or "",
        task_id=result.task_id,
        canon_touched=result.canon_touched,
    )
    for line in describe_result(result):
        print(f"  {line}")
    print(f"  op: {op.op_id}")
    return 0
