"""工作流命令：list / validate / run / pause / resume / show（V1.0 §10、§17）。

五条子命令分两组：

- `list` / `show` / `validate` 是**只读**，随便跑。
- `run` / `pause` / `resume` 只写 `<runtime>/workflows/`，不写 Vault，
  所以不需要 `--as author`——它们记的是「谁把哪条流程推到哪一步」，
  不是「作品内容变了」。

`run` 不执行步骤。这一点是**故意的**：真执行器要能中断、续跑、回滚，
那需要事务，而发布协议已经有一整套了；再写平行的第二套，
「到底以谁的状态为准」会变成新的谜。这里只负责记账与拦重复。
"""

from __future__ import annotations

import argparse
import json

from .. import config as cfg_mod
from ..config import ConfigError
from ..errors import UsageError
from ..workflows import registry
from ._common import fail, heading, info, ok, warn


def cmd_workflow(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    action = args.workflow_command
    if action == "list":
        return _list(args)
    if action == "show":
        return _show(args)
    if action == "validate":
        return _validate(args)
    if action == "run":
        return _run(cfg, args)
    if action in ("pause", "resume"):
        return _transition(cfg, args, "paused" if action == "pause" else "running")
    fail(f"未知子命令 {action}")
    return 2


# ---------------------------------------------------------------------------
# 只读
# ---------------------------------------------------------------------------


def _pad(text: str, width: int) -> str:
    """按**显示宽度**补空格。中文一字占两列，`f"{x:<12s}"` 按码点数算，
    九条工作流排下来就会歪成锯齿——这种歪不是审美问题，是「哪一列是哪列」读不出来。
    """
    shown = sum(2 if "\u4e00" <= ch <= "\u9fff" else 1 for ch in text)
    return text + " " * max(0, width - shown)


def _list(args: argparse.Namespace) -> int:
    try:
        entries = registry.workflows(getattr(args, "catalog", None))
    except ConfigError as exc:
        fail(str(exc))
        return 1

    stage_filter = (getattr(args, "stage", None) or "").strip()
    agent_filter = (getattr(args, "agent", None) or "").strip()
    shown = []
    for wf in entries:
        if stage_filter and wf.get("stage") != stage_filter:
            continue
        # 按角色过滤时必须看 agents 字段，不能看 agents 名字出现在描述里就收——
        # 那会把「提到编辑」的流程算成「编辑负责」的流程。
        if agent_filter and agent_filter not in (wf.get("agents") or []):
            continue
        shown.append(wf)

    scope = ""
    if stage_filter:
        scope += f" · 阶段 {stage_filter}"
    if agent_filter:
        scope += f" · 角色 {agent_filter}"
    heading(f"工作流 {len(shown)}/{len(entries)} 条（V1.0 §10{scope}）")
    if agent_filter and not shown:
        info(
            f"{agent_filter} 没有负责的工作流。"
            "角色名以 agent_registry 为准（editor_in_chief/world_architect/editor/"
            "dice_mistress/researcher/writer/memory_manager/intake_clerk/version_steward）。"
        )
    for wf in shown:
        wid = str(wf.get("id"))
        steps = len(wf.get("steps") or [])
        detail = wf.get("detail_ref")
        marker = f"  细则：workflows/{detail}/" if detail else ""
        print(
            f"  {wid}  {_pad(str(wf.get('name')), 16)}  {_pad(str(wf.get('stage') or ''), 14)}"
            f"{steps} 步{marker}"
        )
        info(str(wf.get("description") or ""))

    stages: dict[str, int] = {}
    for wf in entries:
        stages[str(wf.get("stage"))] = stages.get(str(wf.get("stage")), 0) + 1
    info("按阶段：" + "、".join(f"{k} {v}" for k, v in sorted(stages.items())))

    if getattr(args, "json", False):
        print(json.dumps({"workflows": shown}, ensure_ascii=False, indent=2))
    return 0


def _show(args: argparse.Namespace) -> int:
    try:
        wf = registry.find(args.id, getattr(args, "catalog", None))
    except (ConfigError, UsageError) as exc:
        fail(str(exc))
        return 1

    heading(f"{wf.get('id')} {wf.get('name')}（v{wf.get('version')}）")
    info(str(wf.get("description") or ""))

    print("\n触发条件：")
    for item in wf.get("trigger") or []:
        print(f"  · {item}")
    print("前置条件：")
    for item in wf.get("preconditions") or ["（无）"]:
        print(f"  · {item}")

    print("\n步骤：")
    for step in wf.get("steps") or []:
        dep = step.get("depends_on") or []
        tail = f"  ← {', '.join(dep)}" if dep else ""
        print(f"  {str(step.get('id')):<18s} {str(step.get('owner')):<18s} {step.get('action')}{tail}")
        if step.get("gate"):
            info(f"门槛：{step['gate']}")

    perms = wf.get("tool_permissions") or {}
    print("\n工具权限：")
    for key in ("allow", "propose", "deny"):
        values = perms.get(key) or []
        print(f"  {key:<8s} {', '.join(values) if values else '（空）'}")

    print("\n质量门：")
    for item in wf.get("quality_gates") or []:
        print(f"  · {item}")

    detail = registry.detail_file(wf, getattr(args, "catalog", None))
    print()
    if detail:
        ok(f"分步细则：{detail}")
    else:
        warn("没有分步细则（catalog 里只声明了有哪些步）")

    if getattr(args, "json", False):
        print(json.dumps(wf, ensure_ascii=False, indent=2))
    return 0


def _validate(args: argparse.Namespace) -> int:
    try:
        problems = registry.validate(getattr(args, "catalog", None))
    except ConfigError as exc:
        fail(str(exc))
        return 1

    if not problems:
        ok(f"工作流目录校验通过：{len(registry.workflows(getattr(args, "catalog", None)))} 条定义，字段面齐全")
        return 0
    heading(f"发现 {len(problems)} 个问题")
    for item in problems:
        fail(item)
    return 1


# ---------------------------------------------------------------------------
# 记账
# ---------------------------------------------------------------------------


def _store(cfg: cfg_mod.Config) -> registry.InstanceStore:
    return registry.InstanceStore(cfg.require_runtime())


def _run(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    try:
        wf = registry.find(args.id, getattr(args, "catalog", None))
        store = _store(cfg)
        if getattr(args, "force", False):
            current = store.active(str(wf["id"]))
            if current is not None:
                store.set_state(
                    str(current["instance_id"]), "cancelled", args.actor or "author-local", "被 --force 取代"
                )
                warn(f"已取消旧实例 {current['instance_id']}（--force）")
        inst = store.open(wf, actor=args.actor or "author-local", note=args.note or "")
    except (ConfigError, UsageError) as exc:
        fail(str(exc))
        return 1

    ok(f"已开实例 {inst['instance_id']}（{wf['id']}，当前步 {inst['current_step']}）")
    info("这是**记账**，不是执行：步骤由 Agent 按 catalog 的 gate 逐条做，做完用 status 查。")
    if getattr(args, "json", False):
        print(json.dumps(inst, ensure_ascii=False, indent=2))
    return 0


def _transition(cfg: cfg_mod.Config, args: argparse.Namespace, state: str) -> int:
    try:
        store = _store(cfg)
        inst = store.set_state(args.instance, state, args.actor or "author-local", args.reason or "")
    except UsageError as exc:
        fail(str(exc))
        return 1
    ok(f"{inst['instance_id']} → {state}" + (f"（{args.reason}）" if getattr(args, "reason", None) else ""))
    return 0
