"""`novel release`：准备、查看、执行、续做、中止、回退、体检。

命令的分工对应 PRD §17.4 的三个阶段：

| 阶段 | 命令 | 谁来做 |
|---|---|---|
| 准备 | `release prepare --task` | Agent 也可（只生成计划，不动正式区） |
| 批准 | `approve release <REL> --hash` | **只能作者** |
| 执行 | `release execute <REL>` / `publish --task` | **只能作者** |
| 恢复 | `release status` / `resume` / `abort` / `repair` | 查状态谁都可；改状态只能作者 |
| 回退 | `release rollback --to <REL>` | **只能作者** |
"""

from __future__ import annotations

import argparse
import json

from .. import config as cfg_mod
from ..audit import ensure_actor
from ..changes import ChangeStore, IssueStore
from ..errors import ConflictError, SecurityError, UsageError
from ..indexing.source import skip_dirs_from_layout
from ..release import (
    PublishLock,
    ReleasePlan,
    ReleaseStore,
    abort,
    check_approval,
    check_gates,
    execute,
    prepare_plan,
    prepare_rollback,
    read_holder,
    repair_receipt,
    resume,
    status,
)
from ..staging import StagingArea
from ._common import approval_store, heading, info, ok, oplog, project, task_store, warn


def _store(cfg: cfg_mod.Config) -> ReleaseStore:
    return ReleaseStore(cfg.require_runtime())


def _index_dir(cfg: cfg_mod.Config):
    return cfg.require_runtime() / "index"


def _skip_dirs(cfg: cfg_mod.Config):
    return skip_dirs_from_layout(cfg.layout)


def _plan_lines(plan: ReleasePlan) -> list[str]:
    lines = [
        f"计划      : {plan.release_id}",
        f"基线      : {plan.base_release or '（首次发布）'}",
        f"变更      : {len(plan.changes)} 项" + (f"，删除 {len(plan.removals)} 项" if plan.removals else ""),
        f"计划哈希  : {plan.hash}",
    ]
    return lines


def _closure_exemption_line(plan: ReleasePlan) -> None:
    """跳过共同发布闸的批次，在计划里留下可见的豁免痕迹。"""
    if plan.closure_skipped:
        warn("豁免      : 本批含正文但跳过了共同发布闸（--skip-closure），记忆可能落后于正文")


def cmd_release(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    action = getattr(args, "release_command", None)
    if action == "prepare":
        return _prepare(cfg, args)
    if action == "show":
        return _show(cfg, args)
    if action == "execute":
        return _execute(cfg, args)
    if action == "resume":
        return _resume(cfg, args)
    if action == "abort":
        return _abort(cfg, args)
    if action == "status":
        return _status(cfg, args)
    if action == "repair":
        return _repair(cfg, args)
    if action == "rollback":
        return _rollback(cfg, args)
    if action == "list":
        return _list(cfg, args)
    if action == "unlock":
        return _unlock(cfg, args)
    raise UsageError(
        "用法：novel release prepare|show|execute|resume|abort|status|repair|rollback|list|unlock"
    )


# --------------------------------------------------------------------------
# prepare
# --------------------------------------------------------------------------

def _prepare(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    actor = ensure_actor(getattr(args, "actor", None), "stage")
    pp = project(cfg, args)
    store = _store(cfg)
    tasks = task_store(cfg)
    task = tasks.load(args.task)
    if task.project_id != pp.project_id:
        raise ConflictError(f"任务 {task.task_id} 属于 {task.project_id}，不是 {pp.project_id}")

    staging = StagingArea(pp, task)
    approval = approval_store(cfg).latest_for(task.task_id)
    check_gates(
        pp,
        task,
        staging,
        issue_store=IssueStore(cfg.require_runtime()),
        change_store=ChangeStore(cfg.require_runtime()),
    )
    check_approval(pp, task, approval, staging)

    plan, meta = prepare_plan(
        pp,
        task,
        staging,
        approval,
        store,
        actor=actor,
        issue_store=IssueStore(cfg.require_runtime()),
        change_store=ChangeStore(cfg.require_runtime()),
        confirm_canon=getattr(args, "confirm_canon", False),
        skip_closure=getattr(args, "skip_closure", False),
    )
    oplog(cfg).record(
        actor=actor,
        command="release prepare",
        project_id=pp.project_id,
        outputs=[{"release_id": plan.release_id, "plan_hash": plan.hash}],
        task_id=task.task_id,
    )
    heading(f"已生成发布计划 {plan.release_id}")
    for line in _plan_lines(plan):
        info(line)
    _closure_exemption_line(plan)
    print("  变更清单：")
    for change in plan.changes:
        print(f"    {change.op:6} {change.path}  {change.content_hash[:12]}…")
    print()
    info(f"批准：novel approve release {plan.release_id} --hash {plan.hash} --as author")
    info(f"执行：novel release execute {plan.release_id} --as author --confirm")
    return 0


# --------------------------------------------------------------------------
# show
# --------------------------------------------------------------------------

def _show(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    store = _store(cfg)
    plan = store.load_plan(args.release_id)
    meta = store.load_meta(args.release_id)
    journal = store.load_journal(args.release_id)
    approvals = approval_store(cfg).for_target("release", args.release_id)
    payload = {
        "plan": plan.as_dict(),
        "plan_hash": plan.hash,
        "meta": meta.as_dict(),
        "approvals": [a.approval_id for a in approvals],
        "journal": journal.as_dict() if journal else None,
        "manifest": None,
        "receipt": None,
    }
    if store.manifest_path(args.release_id).exists():
        manifest = store.load_manifest(args.release_id)
        payload["manifest"] = manifest.as_dict()
        payload["manifest_hash"] = manifest.hash
    receipt = store.receipt_for_release(args.release_id)
    if receipt:
        payload["receipt"] = receipt.as_dict()

    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    heading(f"发布 {plan.release_id}")
    for line in _plan_lines(plan):
        info(line)
    _closure_exemption_line(plan)
    info(f"任务      : {meta.task_id or '（回退包）'}")
    info(f"批准      : {'、'.join(a.approval_id for a in approvals) or '尚无'}")
    current = approvals[-1].target_hash if approvals else ""
    if approvals and current != plan.hash:
        warn("已批准的是另一份计划（计划被重新准备过），当前批准无效")
    if journal:
        info(f"事务      : {journal.txn_id} / {journal.state}，已完成步骤 {journal.last_done()}/22")
        for step in journal.steps:
            mark = {"done": "✓", "failed": "✗", "skipped": "—"}.get(step.status, "·")
            print(f"    {mark} {step.step:2} {step.detail or step.name}")
    else:
        info("事务      : 尚未开始")
    if payload["manifest"]:
        info(f"清单哈希  : {payload['manifest_hash']}")
        info(f"候选提交  : {payload['manifest']['canon_revision']}")
        info(f"索引      : {payload['manifest']['index_build_id']}")
    if payload["receipt"]:
        info(f"回执      : {payload['receipt']['receipt_id']}")
        for warning in payload["receipt"].get("warnings") or []:
            warn(warning)
    return 0


# --------------------------------------------------------------------------
# execute / resume
# --------------------------------------------------------------------------

def _execute(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    actor = ensure_actor(getattr(args, "actor", None), "publish")
    pp = project(cfg, args)
    store = _store(cfg)
    plan = store.load_plan(args.release_id)
    meta = store.load_meta(args.release_id)
    require_canon = any(c.path.startswith("01_Canon/") for c in plan.changes)
    if require_canon and not (getattr(args, "confirm_canon", False) or meta.confirm_canon):
        # 与引擎里同一道闸门同一个错误类型：这条路径和 `novel publish` 必须给出同样的
        # 退出码，否则脚本按码分支时会以为「只是冲突，重试一下」。
        raise SecurityError(
            "本次发布包含 01_Canon 写入，需要 --confirm-canon（作者二次确认）"
        )
    user_confirm = getattr(args, "confirm", False)
    if not user_confirm:
        raise UsageError("发布会写正式区，确认请加 --confirm")

    result = execute(
        pp,
        store,
        plan.release_id,
        actor=actor,
        approval_store=approval_store(cfg),
        issue_store=IssueStore(cfg.require_runtime()),
        change_store=ChangeStore(cfg.require_runtime()),
        confirm_canon=getattr(args, "confirm_canon", False),
        skip_dirs=_skip_dirs(cfg),
        index_dir=_index_dir(cfg),
    )
    _report(cfg, pp, actor, result)
    return 0


def _resume(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    actor = ensure_actor(getattr(args, "actor", None), "publish")
    pp = project(cfg, args)
    store = _store(cfg)
    result = resume(
        pp,
        store,
        args.release_id,
        actor=actor,
        approval_store=approval_store(cfg),
        issue_store=IssueStore(cfg.require_runtime()),
        change_store=ChangeStore(cfg.require_runtime()),
        confirm_canon=getattr(args, "confirm_canon", False),
        skip_dirs=_skip_dirs(cfg),
        index_dir=_index_dir(cfg),
    )
    _report(cfg, pp, actor, result)
    return 0


def _report(cfg: cfg_mod.Config, pp, actor: str, result) -> None:
    heading(f"发布 {result.release_id} · {result.state}")
    if result.already_active:
        info("该版本已经是正式版：只补了回执与镜像，没有再次切换")
    info(f"候选提交  : {result.canon_revision}")
    info(f"索引      : {result.index_build_id}")
    info(f"回执      : {result.receipt_id}")
    info(f"镜像写入  : {len(result.written)} 个文件")
    for rel in result.written:
        print(f"    + {rel}")
    if result.archived:
        info(f"归档旧版  : {len(result.archived)} 个")
    for warning in result.warnings:
        warn(warning)
    info(f"已完成步骤: {result.steps_done}")
    oplog(cfg).record(
        actor=actor,
        command="release execute",
        project_id=pp.project_id,
        outputs=[
            {
                "release_id": result.release_id,
                "receipt_id": result.receipt_id,
                "canon_revision": result.canon_revision,
            }
        ],
        task_id=result.task_id,
    )


# --------------------------------------------------------------------------
# abort / repair / rollback
# --------------------------------------------------------------------------

def _abort(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    actor = ensure_actor(getattr(args, "actor", None), "publish")
    store = _store(cfg)
    journal = abort(store, args.release_id, actor=actor, note=getattr(args, "note", "") or "")
    oplog(cfg).record(
        actor=actor,
        command="release abort",
        outputs=[{"release_id": args.release_id, "state": journal.state}],
        note=getattr(args, "note", "") or "",
    )
    ok(f"{args.release_id} 已中止（正式版未改动）")
    return 0


def _repair(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    actor = ensure_actor(getattr(args, "actor", None), "publish")
    store = _store(cfg)
    receipt = repair_receipt(store, args.release_id, actor=actor)
    oplog(cfg).record(
        actor=actor,
        command="release repair",
        outputs=[{"release_id": args.release_id, "receipt_id": receipt.receipt_id}],
    )
    ok(f"回执已补写：{receipt.receipt_id}（repaired=true）")
    return 0


def _rollback(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    actor = ensure_actor(getattr(args, "actor", None), "publish")
    pp = project(cfg, args)
    store = _store(cfg)
    plan, meta = prepare_rollback(
        pp,
        store,
        args.to,
        actor=actor,
        reason=getattr(args, "reason", "") or "",
        confirm_canon=True,
    )
    oplog(cfg).record(
        actor=actor,
        command="release rollback",
        project_id=pp.project_id,
        outputs=[{"release_id": plan.release_id, "to": args.to, "plan_hash": plan.hash}],
        note=meta.reason,
    )
    heading(f"回退包 {plan.release_id}（目标 {args.to}）")
    for line in _plan_lines(plan):
        info(line)
    info(f"复用索引  : {store.index_id_for(args.to)}")
    info(f"受影响依赖: 待 T10 依赖表生成后才能枚举；本版在回执里写明需人工复核")
    print()
    info(f"批准：novel approve release {plan.release_id} --hash {plan.hash} --as author")
    info(f"执行：novel release execute {plan.release_id} --as author --confirm --confirm-canon")
    return 0


# --------------------------------------------------------------------------
# status / list / unlock
# --------------------------------------------------------------------------

def _status(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    store = _store(cfg)
    report = status(store, deep=getattr(args, "deep", False))
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["verdict"] in ("OK", "NO_RELEASE") else 1

    heading("发布状态")
    info(f"判词      : {report['verdict']}")
    info(f"说明      : {report['detail']}")
    if report["pointer"]:
        pointer = report["pointer"]
        info(f"正式版    : {pointer['release_id']}")
        info(f"候选提交  : {pointer['canon_revision']}")
        info(f"索引      : {pointer['index_build_id']}")
    if report["receipt"]:
        info(f"回执      : {report['receipt']['receipt_id']}")
    if report["checked_files"]:
        info(f"核对文件  : {report['checked_files']}")
    for problem in report["problems"]:
        warn(problem)
    if report["pending"]:
        info("未完成的事务：")
        for item in report["pending"]:
            print(
                f"    - {item['release_id']}  {item['state']}  已完成 {item['last_done']}/22"
                f"（novel release resume {item['release_id']}）"
            )
    if report["verdict"] == "RECEIPT_MISSING":
        info(f"补回执：novel release repair {report['pointer']['release_id']} --as author")
    return 0 if report["verdict"] in ("OK", "NO_RELEASE") else 1


def _list(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    store = _store(cfg)
    active = store.read_active()
    active_id = active.release_id if active else ""
    releases = store.list_releases()
    if not releases:
        info("还没有任何发布")
        return 0
    if getattr(args, "json", False):
        rows = []
        for release_id in releases:
            journal = store.load_journal(release_id)
            rows.append(
                {
                    "release_id": release_id,
                    "active": release_id == active_id,
                    "state": journal.state if journal else "PREPARED",
                    "last_done": journal.last_done() if journal else 0,
                }
            )
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    for release_id in releases:
        journal = store.load_journal(release_id)
        mark = "*" if release_id == active_id else " "
        state = journal.state if journal else "PREPARED"
        done = journal.last_done() if journal else 0
        print(f" {mark} {release_id}  {state:18} {done:2}/22")
    if active_id:
        info(f"* = 当前正式版（{active_id}）")
    return 0


def _unlock(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    """显式处理异常锁。**不按年龄、不按 PID 猜**，只做一次「能不能拿到」。"""
    actor = ensure_actor(getattr(args, "actor", None), "publish")
    store = _store(cfg)
    path = store.lock_path()
    holder = read_holder(path)
    if not getattr(args, "confirm", False):
        if holder:
            info(f"锁文件记录：{holder.describe()}")
        info("unlock 只验证锁当前是否被持有，不会删文件。确认请加 --confirm")
        return 0
    lock = PublishLock(path, task="release unlock", actor=actor)
    try:
        lock.acquire()
    except ConflictError as exc:
        raise ConflictError(
            f"锁仍被持有，拒绝强制解锁：{exc}。"
            "先确认那个进程确实已结束（不要按文件年龄判断），再重试"
        ) from exc
    finally:
        if lock._fh is not None:  # noqa: SLF001 - 只在这里需要「拿到又立刻还回去」
            lock.release()
    oplog(cfg).record(actor=actor, command="release unlock", outputs=[{"lock": str(path)}])
    ok("锁当前没有被持有（已获取并立即释放）")
    return 0
