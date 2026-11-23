"""`issue` / `change` / `impact` 三条命令。

`impact` 是顶层命令（PRD §20.2 写的是 `novel impact <change-request-id>`），
因为它同时服务 RETCON 之外的场景：任何变更都可以先问「会碰到什么」。

## 为什么 RETCON 不能走 `approve artifact`

`approve artifact` 批的是「这份文件替换那份文件」。RETCON 批的是
「承认此前的事实作废」——对象不是文件，是**影响报告**。所以它有自己的入口：

    novel change create --type RETCON --reason ... --target CHAR-000001
    novel impact CR-000001          # 生成报告并绑定哈希
    novel change approve CR-000001 --hash <影响报告哈希> [--rewrite-chapters|--narrow-scope]

有未批准 RETCON 覆盖的路径，普通文件审批会被拦（见 approval_cmd）。
这条链子是 T05 验收第 3 条的全部含义：不是「多一个命令」，是「两条路不能互相替代」。
"""

from __future__ import annotations

import argparse
import json

from .. import config as cfg_mod
from ..audit import ensure_actor
from ..changes import (
    BLOCKER,
    SEVERITIES,
    ChangeStore,
    IssueStore,
    analyse,
)
from ..errors import UsageError
from ..indexing import connect
from ..indexing.locate import active_db
from ._common import heading, info, ok, oplog, project, warn


# --------------------------------------------------------------------------
# issue
# --------------------------------------------------------------------------

def cmd_issue(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    action = getattr(args, "issue_command", None)
    store = IssueStore(cfg.require_runtime())

    if action == "new":
        actor = ensure_actor(getattr(args, "actor", None), "issue new")
        pp = project(cfg, args)
        issue = store.create(
            severity=args.severity,
            category=args.category,
            target_id=args.target,
            description=args.description,
            project_id=pp.project_id,
            block_id=getattr(args, "block", "") or "",
            evidence_refs=getattr(args, "evidence", []) or [],
            suggested_actions=getattr(args, "suggest", []) or [],
            actor=actor,
        )
        ok(f"已登记 {issue.issue_id}（{issue.severity} / {issue.category}）")
        info(f"目标：{issue.target_id}{'#' + issue.block_id if issue.block_id else ''}")
        if issue.severity == BLOCKER:
            warn("BLOCKER 会阻断该对象的批准，直到 resolve（豁免不适用于 BLOCKER）")
        return 0

    if action == "list":
        project_id = getattr(args, "project", None) or ""
        rows = store.all(project_id=project_id or "")
        if getattr(args, "severity", ""):
            rows = [i for i in rows if i.severity == args.severity.upper()]
        if getattr(args, "status", ""):
            rows = [i for i in rows if i.status == args.status.upper()]
        if getattr(args, "json", False):
            print(json.dumps([i.as_dict() for i in rows], ensure_ascii=False, indent=2))
            return 0
        heading(f"问题 {len(rows)} 条")
        if not rows:
            info("没有问题记录。")
        for issue in rows:
            print(f"  {issue.issue_id}  {issue.severity:<12} {issue.status:<8} {issue.category:<20} {issue.target_id}")
            print(f"     {issue.description}")
        return 0

    if action in ("resolve", "waive"):
        actor = ensure_actor(getattr(args, "actor", None), f"issue {action}")
        note = getattr(args, "note", "") or ""
        if action == "resolve":
            issue = store.resolve(args.issue_id, note=note, actor=actor)
            ok(f"{issue.issue_id} 已 RESOLVED")
        else:
            issue = store.waive(args.issue_id, note=note, actor=actor)
            ok(f"{issue.issue_id} 已 WAIVED")
        if issue.closure_note:
            info(issue.closure_note)
        return 0

    raise UsageError("issue 需要子命令：new / list / resolve / waive")


# --------------------------------------------------------------------------
# change
# --------------------------------------------------------------------------

def cmd_change(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    action = getattr(args, "change_command", None)
    store = ChangeStore(cfg.require_runtime())

    if action == "create":
        actor = ensure_actor(getattr(args, "actor", None), "change create")
        pp = project(cfg, args)
        change = store.create(
            type=args.type,
            reason=args.reason,
            project_id=pp.project_id,
            targets=getattr(args, "target", []) or [],
            scope=getattr(args, "scope", []) or [],
            old_record=getattr(args, "old", "") or "",
            new_record=getattr(args, "new", "") or "",
            migration_plan=getattr(args, "migration", "") or "",
            release_strategy=getattr(args, "release_strategy", "") or "",
            actor=actor,
        )
        ok(f"已登记变更 {change.change_id}（{change.type}）")
        info(f"目标：{', '.join(change.targets) or '（仅按范围）'}")
        if change.independent:
            warn("RETCON 必须走独立审批：先 novel impact，再 novel change approve")
        print(f"下一步：novel impact {change.change_id}")
        return 0

    if action == "list":
        rows = store.all(project_id=getattr(args, "project", None) or "")
        if getattr(args, "json", False):
            print(json.dumps([c.as_dict() for c in rows], ensure_ascii=False, indent=2))
            return 0
        heading(f"变更 {len(rows)} 笔")
        if not rows:
            info("没有变更记录。")
        for change in rows:
            print(f"  {change.change_id}  {change.type:<12} {change.status:<9} {', '.join(change.targets) or '-'}")
            print(f"     {change.reason}")
        return 0

    if action == "show":
        change = store.load(args.change_id)
        if getattr(args, "json", False):
            print(json.dumps(change.as_dict(), ensure_ascii=False, indent=2))
            return 0
        heading(f"变更 {change.change_id}")
        info(f"类型：{change.type}   状态：{change.status}")
        info(f"原因：{change.reason}")
        info(f"目标：{', '.join(change.targets) or '-'}")
        info(f"范围：{', '.join(change.scope) or '-'}")
        if change.impact:
            info(f"影响报告哈希：{change.impact_hash}")
            _print_impact(change.impact)
        else:
            warn("还没有影响报告：novel impact " + change.change_id)
        return 0

    if action == "approve":
        actor = ensure_actor(getattr(args, "actor", None), "change approve")
        change = store.load(args.change_id)
        if not change.independent and not getattr(args, "force", False):
            # 非 RETCON 的变更不必走这条重路，但允许走——只要作者明说要走。
            info(f"{change.change_id} 是 {change.type}，普通变更本来可以随正文一起批；这里按独立变更批也可以")
        change = store.approve(
            change,
            impact_hash=getattr(args, "hash", "") or "",
            actor=actor,
            note=getattr(args, "note", "") or "",
            allow_rewrite=getattr(args, "rewrite_chapters", False),
            narrow_scope=getattr(args, "narrow_scope", False),
        )
        ok(f"{change.change_id} 已 APPROVED")
        if change.scope_narrowed:
            warn("已记录「缩小变更范围」：受影响正文不在本次变更内，另行处理")
        return 0

    if action == "reject":
        actor = ensure_actor(getattr(args, "actor", None), "change reject")
        change = store.reject(store.load(args.change_id), actor=actor, note=getattr(args, "note", "") or "")
        ok(f"{change.change_id} 已 REJECTED")
        return 0

    raise UsageError("change 需要子命令：create / list / show / approve / reject")


# --------------------------------------------------------------------------
# impact
# --------------------------------------------------------------------------

def _print_impact(report: dict) -> None:
    counts = report.get("counts") or {}
    print("  影响统计：")
    for key, label in (
        ("assertions", "客观断言"),
        ("state_changes", "状态变化"),
        ("knowledge", "人物认知"),
        ("revelations", "读者揭示"),
        ("events", "事件"),
        ("blocks", "文本块"),
        ("chapters", "受影响正文"),
        ("planning", "规划件"),
        ("dependencies", "依赖"),
    ):
        print(f"    {label:<8}{counts.get(key, 0)}")
    for key, label in (
        ("affected_chapters", "受影响正文"),
        ("affected_planning", "规划件"),
        ("affected_assertions", "断言"),
        ("affected_knowledge", "认知"),
        ("affected_revelations", "揭示"),
    ):
        items = report.get(key) or []
        if items:
            print(f"  {label}：{', '.join(items[:10])}{' 等' if len(items) > 10 else ''}")
    for note in report.get("notes") or []:
        print(f"  注意：{note}")


def cmd_impact(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    store = ChangeStore(cfg.require_runtime())
    change = store.load(args.change_id)
    runtime = cfg.require_runtime()

    if not change.targets:
        raise UsageError(
            f"{change.change_id} 没有目标 ID，无法按索引反查影响。"
            "用 `change create --target <ID>` 登记目标后再分析"
        )

    conn = connect(active_db(runtime))
    try:
        report = analyse(conn, change_id=change.change_id, targets=change.targets)
    finally:
        conn.close()

    payload = report.as_report()
    if not getattr(args, "dry_run", False):
        change = store.attach_impact(change, payload)
    op = oplog(cfg).record(
        actor="author",
        command="impact",
        project_id=change.project_id,
        outputs=[{"change_id": change.change_id, "impact_hash": change.impact_hash}],
        note="" if not getattr(args, "dry_run", False) else "dry-run，未写入变更",
    )

    if getattr(args, "json", False):
        print(json.dumps({"impact": payload, "impact_hash": change.impact_hash if not args.dry_run else ""}, ensure_ascii=False, indent=2))
        return 0

    heading(f"影响分析 {change.change_id}（{change.type}）")
    info(f"目标：{', '.join(change.targets)}")
    _print_impact(payload)
    if getattr(args, "dry_run", False):
        warn("dry-run：报告未写入变更记录")
    else:
        ok(f"影响报告已绑定：{change.impact_hash}")
        if change.independent:
            print(f"下一步：novel change approve {change.change_id} --hash {change.impact_hash} --as author")
    print(f"  op: {op.op_id}")
    return 0
