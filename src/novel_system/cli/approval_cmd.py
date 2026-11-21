"""`inspect` 与「按对象批准」：批准绑定精确字节，不是一句同意。

PRD §20.3 要求 `inspect` 显示七样东西，少一样作者就没法判断自己在批什么：

1. 对象 ID；
2. 内容 hash；
3. 与正式版差异；
4. 风险；
5. 阻断问题；
6. 来源；
7. 所需批准范围。

## 候选（candidate）怎么指

本版没有单独的候选 ID 命名空间（PRD §8.1 也没给候选留前缀），候选就是
**任务 staging 里的一份或一包产物**：

- `TASK-000001` —— 该任务 staging 的全部文件，批准绑「包哈希」；
- `TASK-000001:01_Canon/Characters/CHAR-0001.md` —— 只批这一份，绑文件哈希。

包哈希用 `canonical.package_hash` 算 `[{target, sha256}]`，跨进程稳定。

## 三条硬线

- **哈希必须显式给**，且与当前字节一致；不给 `--hash` 直接拒（PRD §20.3）。
- **有 OPEN 的 BLOCKER 指向这批对象时拒绝批准**，豁免路径不存在（PRD §14.3）。
- **未批准的 RETCON 覆盖了目标路径时拒绝普通审批**，必须走 `change approve`
  （T05 验收第 3 条：RETCON 有独立审批路径）。
"""

from __future__ import annotations

import argparse
import json

from .. import config as cfg_mod
from ..approvals import ApprovalFile
from ..audit import ensure_actor
from ..canonical import package_hash
from ..changes import IssueStore, ChangeStore
from ..errors import ApprovalError, UsageError
from ..staging import StagedFile, StagingArea, artifact_id_of
from ..storage import sha256_file
from ._common import approval_store, heading, info, ok, oplog, project, task_store, warn

# 路径前缀 → 批准范围（PRD §14.4 的 approved_scope）
SCOPES = (
    ("03_Manuscripts", "CHAPTER_TEXT"),
    ("01_Canon", "CANON_FILE"),
    ("02_Planning", "PLANNING"),
    ("06_Memory", "MEMORY"),
    ("04_Story_Assets", "STORY_ASSET"),
    ("05_Ledgers", "LEDGER"),
)
# 风险：正文与 Canon 是高危，规划次之，其余低。
RISK = {
    "CHAPTER_TEXT": "HIGH",
    "CANON_FILE": "HIGH",
    "PLANNING": "MEDIUM",
    "MEMORY": "MEDIUM",
    "STORY_ASSET": "LOW",
    "LEDGER": "LOW",
}


def scope_of(target: str) -> str:
    for prefix, scope in SCOPES:
        if target.startswith(prefix + "/"):
            return scope
    return "FILE"


def _split_candidate(candidate: str) -> tuple[str, str]:
    task_id, _, target = candidate.partition(":")
    if not task_id.startswith("TASK-"):
        raise UsageError(f"候选 ID 形如 TASK-000001 或 TASK-000001:<目标相对路径>，收到：{candidate}")
    return task_id, target


def resolve_candidate(cfg: cfg_mod.Config, pp, candidate: str) -> tuple:
    """返回 (task, [StagedFile], 当前哈希, {target: 磁盘哈希})。

    **哈希按磁盘现算，不读 manifest。** manifest 是 stage 当时的记录，之后
    有人直接动了 staging 里的文件它就过期了；拿过期哈希去批，批的是一份
    此刻已经不存在的字节。manifest 与磁盘的差额另作 drift 报给作者看。
    """
    task_id, target = _split_candidate(candidate)
    task = task_store(cfg).load(task_id)
    if task.project_id != pp.project_id:
        raise UsageError(f"任务 {task_id} 属于 {task.project_id}，不是当前项目 {pp.project_id}")
    staging = StagingArea(pp, task)
    files = staging.files()
    if not files:
        raise UsageError(f"任务 {task_id} 的 staging 为空，没有可批的对象")
    if target:
        files = [item for item in files if item.target == target]
        if not files:
            raise UsageError(f"{task_id} 的 staging 里没有 {target}")

    current = _current_hashes(pp, files)
    missing = [t for t, digest in current.items() if not digest]
    if missing:
        raise UsageError(f"staging 文件已丢失：{', '.join(missing)}；这件候选不能批")

    if target:
        return task, files, current[target], current
    package = package_hash(
        [{"target": f.target, "sha256": current[f.target]} for f in sorted(files, key=lambda x: x.target)]
    )
    return task, files, package, current


def _current_hashes(pp, files: list[StagedFile]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in files:
        path = pp.vault / item.staged
        if path.exists():
            out[item.target] = sha256_file(path)
        else:
            out[item.target] = ""
    return out


def _approval_state(cfg, pp, candidate: str, files: list[StagedFile], expected: str, current: dict) -> dict:
    """这笔候选当前有没有有效批准。批准后改一个字节 → STALE。"""
    store = approval_store(cfg)
    records = store.for_target("artifact", candidate)
    if not records:
        return {"status": "NONE", "approval_id": "", "stale": []}
    approval = records[-1]
    status, stale = store.verify(approval, current)
    if status == "VALID" and approval.target_hash and approval.target_hash != expected:
        # 候选内容变了（包哈希变了），即便单文件哈希没动也要重新批。
        status, stale = "STALE", stale + ["<package>"]
    return {"status": status, "approval_id": approval.approval_id, "stale": stale}


def cmd_inspect(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    pp = project(cfg, args)
    task, files, expected, current = resolve_candidate(cfg, pp, args.candidate)
    staging_diff = {row["target"]: row for row in StagingArea(pp, task).diff()}

    issues = IssueStore(cfg.require_runtime())
    ids = {task.task_id} | {artifact_id_of(item.target) for item in files}
    blockers = issues.open_blockers(ids, project_id=pp.project_id)
    pending_retcon = ChangeStore(cfg.require_runtime()).blocking_scope(
        [item.target for item in files], project_id=pp.project_id
    )

    scopes = sorted({scope_of(item.target) for item in files})
    risk = "HIGH" if any(RISK.get(s) == "HIGH" for s in scopes) else ("MEDIUM" if "PLANNING" in scopes or "MEMORY" in scopes else "LOW")
    if blockers or pending_retcon:
        risk = "HIGH"

    files_view = []
    for item in files:
        row = staging_diff.get(item.target, {})
        files_view.append(
            {
                "target": item.target,
                "sha256": item.sha256,
                "on_disk": current.get(item.target, ""),
                "manifest_sha256": item.sha256,
                "drift": current.get(item.target, "") != item.sha256,
                "diff": row.get("kind", "?"),
                "detail": row.get("detail", ""),
                "base_sha256": item.base_sha256,
                "staged_at": item.staged_at,
                "actor": item.actor,
                "scope": scope_of(item.target),
            }
        )

    state = _approval_state(cfg, pp, args.candidate, files, expected, current)
    payload = {
        "candidate": args.candidate,
        "task_id": task.task_id,
        "task_type": task.task_type,
        "project_id": pp.project_id,
        "package_hash": expected
        if len(files) > 1
        else package_hash([{"target": f.target, "sha256": current[f.target]} for f in files]),
        "hash": expected,
        "files": files_view,
        "scope": scopes,
        "risk": risk,
        "blocking_issues": [i.issue_id for i in blockers],
        "pending_retcon": [c.change_id for c in pending_retcon],
        "approval": state,
        "source": {
            "task_type": task.task_type,
            "assignee": task.assigned_to,
            "created_at": task.created_at,
            "write_mode": task.write_mode,
        },
    }

    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    heading(f"候选 {args.candidate}")
    info(f"任务：{task.task_id}（{task.task_type}，{task.write_mode}）")
    info(f"内容哈希：{expected}")
    info(f"批准范围：{', '.join(scopes)}")
    info(f"风险：{risk}")
    print("  与正式版差异：")
    for item in files_view:
        mark = {"added": "+", "modified": "~", "identical": "=", "conflict": "!"}.get(item["diff"], "?")
        print(f"    {mark} {item['target']}  {item['sha256'][:16]}…  {item['detail']}")
    if blockers:
        warn(f"阻断问题 {len(blockers)} 条：" + "、".join(i.issue_id for i in blockers))
        for issue in blockers:
            print(f"     {issue.issue_id} {issue.category}：{issue.description}")
    else:
        info("阻断问题：无")
    if pending_retcon:
        warn("有未批准的 RETCON 覆盖这些路径：" + "、".join(c.change_id for c in pending_retcon))
        info("这类改动不能走普通审批，先 `novel change approve <CR> --hash ...`")
    drifted = [item["target"] for item in files_view if item["drift"]]
    if drifted:
        warn("staging 清单与磁盘不一致（stage 之后文件被改过）：" + ", ".join(drifted))
    if state["status"] == "VALID":
        ok(f"批准状态：有效（{state['approval_id']}）")
    elif state["status"] == "STALE":
        warn(f"批准状态：已失效（{state['approval_id']}）——内容在批准后被改过：{', '.join(state['stale'])}")
    else:
        info("批准状态：尚未批准")
    print(f"\n批准：novel approve artifact {args.candidate} --hash {expected} --as author")
    return 0


def _approve_release(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    """批准一份发布计划（PRD §17.2：作者批准的是计划的规范 JSON 哈希）。

    与文件级批准的区别：这里批的是「这份计划」——基线与变更清单绑在一起。
    计划重新准备过（哪怕内容一样）哈希就会变，旧批准自动作废。
    """
    from ..release import ReleaseStore

    actor = ensure_actor(getattr(args, "actor", None), "approve")
    store = ReleaseStore(cfg.require_runtime())
    release_id = args.candidate
    plan = store.load_plan(release_id)
    meta = store.load_meta(release_id)

    given = (getattr(args, "hash", "") or "").strip()
    if not given:
        raise ApprovalError(
            f"必须显式给出 --hash（当前计划哈希是 {plan.hash}）。"
            "非交互批准不接受「大致同意」（PRD §20.3）"
        )
    if given != plan.hash:
        raise ApprovalError(
            f"计划哈希不符：给出 {given[:16]}…，当前 {plan.hash[:16]}…。"
            f"先 `novel release show {release_id}` 看清在批什么"
        )
    pp = project(cfg, args)
    if meta.project_id and meta.project_id != pp.project_id:
        raise ApprovalError(f"{release_id} 属于项目 {meta.project_id}，不是 {pp.project_id}")

    blockers_store = IssueStore(cfg.require_runtime())
    if meta.task_id:
        upstream = {meta.task_id} | {artifact_id_of(c.path) for c in plan.changes}
        blockers = blockers_store.open_blockers(upstream, project_id=pp.project_id)
        if blockers:
            raise ApprovalError(
                "存在未解决的 BLOCKER，拒绝批准发布计划："
                + "、".join(f"{i.issue_id}（{i.description}）" for i in blockers)
            )

    approval = approval_store(cfg).record(
        task_id=meta.task_id,
        project_id=pp.project_id,
        files=[],
        note=getattr(args, "note", "") or "",
        approver=actor,
        target_type="release",
        target_id=release_id,
        target_hash=plan.hash,
        approved_scope=sorted({scope_of(c.path) for c in plan.changes}),
    )
    op = oplog(cfg).record(
        actor=actor,
        command="approve",
        project_id=pp.project_id,
        outputs=[{"approval_id": approval.approval_id, "release_id": release_id, "hash": plan.hash}],
        note=approval.note,
        task_id=meta.task_id,
    )
    ok(f"已批准发布计划 {release_id}（{approval.approval_id}）")
    info(f"计划哈希 {plan.hash}")
    info(f"变更 {len(plan.changes)} 项，基线 {plan.base_release or '（首次发布）'}")
    print(f"  op: {op.op_id}")
    print(f"下一步：novel release execute {release_id} --as author --confirm")
    return 0


def approve_object(cfg: cfg_mod.Config, args: argparse.Namespace, kind: str) -> int:
    """`approve artifact|text|proposal|release` 的实际执行。"""
    if kind == "release":
        return _approve_release(cfg, args)
    if kind == "text":
        return _approve_text(cfg, args)

    actor = ensure_actor(getattr(args, "actor", None), "approve")
    pp = project(cfg, args)
    candidate = args.candidate
    task, files, expected, current = resolve_candidate(cfg, pp, candidate)

    if kind == "proposal":
        # 记忆提案的批准走 `novel proposal approve <PROP-ID>`：它要绑提案的
        # **源哈希**，而不是 staging 包哈希。两个哈希混用会让「批的是哪一版」说不清。
        raise UsageError(
            "记忆提案请用 `novel proposal approve PROP-0001 --as author`；"
            "它绑的是提案的源哈希，与 staging 包哈希不是同一个东西"
        )

    given = (getattr(args, "hash", "") or "").strip()
    if not given:
        raise ApprovalError(
            f"必须显式给出 --hash（当前内容哈希是 {expected}）。"
            "非交互批准不接受「大致同意」（PRD §20.3）"
        )
    if given != expected:
        raise ApprovalError(
            f"哈希不符：给出 {given[:16]}…，当前 {expected[:16]}…。"
            "先 `novel inspect` 看清在批什么"
        )

    runtime = cfg.require_runtime()
    ids = {task.task_id} | {artifact_id_of(item.target) for item in files}
    blockers = IssueStore(runtime).open_blockers(ids, project_id=pp.project_id)
    if blockers:
        raise ApprovalError(
            "存在未解决的 BLOCKER，拒绝批准："
            + "、".join(f"{i.issue_id}（{i.description}）" for i in blockers)
            + "。BLOCKER 只能修掉或经变更审批缩小范围，不能豁免"
        )

    pending = ChangeStore(runtime).blocking_scope([item.target for item in files], project_id=pp.project_id)
    if pending:
        raise ApprovalError(
            "这些路径有未批准的 RETCON："
            + "、".join(c.change_id for c in pending)
            + "。RETCON 必须走独立审批：novel change approve <CR> --hash <影响报告哈希>"
        )

    # 与正式区的冲突仍要挡：作者手改过的文件不能被一份陈旧提案盖掉。
    conflicts = [row for row in StagingArea(pp, task).diff() if row["kind"] == "conflict"]
    if conflicts and not getattr(args, "force", False):
        for row in conflicts:
            warn(f"{row['target']}：{row['detail']}")
        raise UsageError("存在冲突，拒绝批准；处理冲突或加 --force 显式承担覆盖风险")

    records = [
        ApprovalFile(
            target=item.target,
            staged=item.staged,
            sha256=sha256_file(pp.vault / item.staged),
            bytes=item.bytes,
        )
        for item in files
    ]
    approval = approval_store(cfg).record(
        task_id=task.task_id,
        project_id=pp.project_id,
        files=records,
        note=getattr(args, "note", "") or "",
        approver=actor,
        target_type="artifact",
        target_id=candidate,
        target_hash=expected,
        approved_scope=sorted({scope_of(item.target) for item in files}),
    )
    task.approval_state = "approved"
    task_store(cfg).save(task)
    op = oplog(cfg).record(
        actor=actor,
        command="approve",
        project_id=pp.project_id,
        outputs=[{"approval_id": approval.approval_id, "candidate": candidate, "hash": expected}],
        note=approval.note,
        task_id=task.task_id,
    )
    ok(f"批准记录：{approval.approval_id}（{len(records)} 个文件，哈希 {expected[:16]}…）")
    for record in records:
        info(f"{record.target}  {record.sha256[:16]}…")
    print(f"  op: {op.op_id}")
    print(f"下一步：novel publish --task {task.task_id} --as author --confirm")
    return 0

def _approve_text(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    """`approve text`：正文批准。要求先过审核，且不许有 OPEN BLOCKER。"""
    from ..workflows import text_approval

    actor = ensure_actor(getattr(args, "actor", None), "approve text")
    pp = project(cfg, args)
    task_id = (getattr(args, "task", "") or "").strip()
    chapter = (getattr(args, "chapter", "") or getattr(args, "candidate", "") or "").strip()
    if not task_id or not chapter:
        raise UsageError("approve text 需要 --task TASK-000001 与 --chapter CH-0001")

    runtime = cfg.require_runtime()
    result = text_approval.approve_text(
        pp,
        runtime,
        task_id=task_id,
        chapter_id=chapter,
        store=approval_store(cfg),
        actor=actor,
        note=getattr(args, "note", "") or "",
        issue_store=IssueStore(runtime),
    )
    heading(f"正文已批准 {chapter}")
    ok(f"批准号：{result.approval.approval_id}")
    info(f"哈希：{result.sha256[:16]}…（绑字节，改一个字节就失效）")
    info(f"范围：CHAPTER_TEXT   风险：{result.risk_level}")
    info(f"经手角色：{'、'.join(result.reviewers) or '（无）'}")
    print()
    print("批准后任何字节变化都要重新审、重新批（PRD §14.5）。")
    print(f"下一步：novel proposal extract --task {task_id} --chapter {chapter}")
    return 0
