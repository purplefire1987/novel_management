"""`novel preflight`：产出一份写前资料包。

§20.2 的命令形状是 `novel preflight CH-000102 --base-release REL-000020`，照抄。
多出来的只有三个参数，都为了「先看再落盘」与「预算可调」：
`--budget`／`--search-limit`／`--no-write`。

默认**写盘**。理由：资料包是给下一步 `write` 按号引用的产物，不落盘就没法引用；
而它写的是 `<runtime>/preflight/`，不是正式区，写坏了删掉重跑即可，
不存在覆盖作者内容的风险。这与 `publish`／`restore` 那类「默认预演」的动作
是两种东西，不该套用同一条规矩。
"""

from __future__ import annotations

import argparse

from ..agents import preflight as pf
from ..agents.budget import Budget
from ..agents.metering import Meter, usage_path
from ..errors import UsageError
from . import _common as common


def cmd_preflight(cfg, args: argparse.Namespace) -> int:
    pp = common.project(cfg, args)
    runtime = common.need_runtime(cfg)

    budget = Budget(
        context_limit=args.budget,
        output_reserve=args.output_reserve,
        hard_reserve=args.hard_reserve,
    )
    meter = Meter(runtime=runtime)

    result = pf.build(
        pp,
        runtime,
        chapter_id=args.chapter,
        base_release=args.base_release or "",
        budget=budget,
        role=args.role,
        search_limit=args.search_limit,
        meter=meter,
        write=not args.no_write,
    )
    doc = result.doc

    if args.json:
        print(pf.to_json(doc))
    else:
        _print_report(doc, result)

    meter.flush(runtime)
    return 0 if result.passed else 1


def _print_report(doc: dict, result: pf.PreflightResult) -> None:
    common.heading(f"资料包 {doc['preflight_id']}／{doc['chapter_id']}")
    common.info(f"gate_result : {doc['gate_result']}")
    common.info(f"基线发布    : {doc.get('base_release') or '（无正式发布，按工作树计算）'}")
    anchor = doc.get("story_anchor") or {}
    common.info(f"故事锚点    : {anchor.get('story_time') or '（未给）'}")
    if anchor.get("note"):
        common.warn(anchor["note"])

    coverage = doc.get("coverage") or {}
    common.info(
        "检索覆盖    : 实体 {n} 个／主题 {t} 个".format(
            n=len(coverage.get("checked_entities") or []),
            t=len(coverage.get("checked_topics") or []),
        )
    )
    common.info(
        "关键约束    : {c} 条（硬约束，未参与截断）".format(c=len(doc.get("key_constraints") or []))
    )
    common.info(
        "未回收伏笔  : {f} 条".format(f=len(doc.get("foreshadowing") or []))
    )

    truncations = coverage.get("truncated_results") or []
    if truncations:
        # 这一句必须出现。PRD §13.5 要的是「明确记录截断」，
        # 只把截断写进 YAML 而终端上不提示，等于让作者以为资料很全。
        common.warn(f"有 {len(truncations)} 处结果被截断（看 truncated_results，别当成「就这么多」）：")
        for item in truncations:
            common.info(
                "  - {section}：{total} 条里收了 {kept} 条，丢 {dropped} 条（{reason}）".format(**item)
            )
    else:
        common.info("截断        : 无（本次没有丢任何条目）")

    gaps = coverage.get("unresolved_gaps") or []
    if gaps:
        common.warn(f"未解决的缺口 {len(gaps)} 条：")
        for gap in gaps[:8]:
            common.info(f"  - {gap['topic']}：{gap['detail']}")
        if len(gaps) > 8:
            common.info(f"  ……其余 {len(gaps) - 8} 条见文件")

    blockers = [item for item in doc.get("issues") or [] if item.get("severity") == "BLOCKER"]
    others = [item for item in doc.get("issues") or [] if item.get("severity") != "BLOCKER"]
    for item in others[:6]:
        common.warn(f"{item['severity']}／{item['category']}：{item['message']}")
    if blockers:
        common.heading("阻断项（gate_result=BLOCKED）")
        for item in blockers:
            common.fail(f"{item['category']}：{item['message']}")
    else:
        common.ok("没有阻断项")

    usage = doc.get("usage") or {}
    common.info(
        "调用计量    : 模型 {m} 次／工具 {t} 次／prompt {p} tokens".format(
            m=usage.get("model_calls", 0),
            t=usage.get("tool_calls", 0),
            p=usage.get("prompt_tokens", 0),
        )
    )
    budget = doc.get("budget") or {}
    common.info(
        "预算        : 上限 {limit}／输出预留 {out}／硬约束预留 {hard}／实used {used}".format(
            limit=budget.get("context_limit", 0),
            out=budget.get("output_reserve", 0),
            hard=budget.get("hard_reserve", 0),
            used=budget.get("used", 0),
        )
    )
    if result.path:
        common.heading(f"已写入 {result.path}")
    else:
        common.heading("未写盘（--no-write）：这个号没被占用，下次还可能发到同一个号")


def cmd_agent(cfg, args: argparse.Namespace) -> int:
    """`agent` 的分发器。三个子命令各自一件小事，不合并成一个大命令。"""
    mapping = {
        "list": cmd_agent_list,
        "prompt": cmd_agent_prompt,
        "usage": cmd_agent_usage,
    }
    handler = mapping.get(getattr(args, "agent_command", "") or "")
    if handler is None:
        raise UsageError(f"未知 agent 子命令：{getattr(args, 'agent_command', '')!r}（可选 list／prompt／usage）")
    return handler(cfg, args)


def cmd_agent_list(cfg, args: argparse.Namespace) -> int:
    """列出九个角色：权限、可用工具、角色卡是否在位。

    这条命令存在的实际用处是「图与配置一致」：权限矩阵有三处副本
    （prdmd、`agents/common/permissions.md`、代码），出问题时得有一个
    地方能一眼看出**跑起来的到底是哪一行**。
    """
    from ..agents import registry, tools

    rows = []
    for role in registry.ROLES:
        try:
            card = registry.prompt_file(role.id)
            card_ok = True
            card_path = str(card)
        except UsageError as exc:
            card_ok = False
            card_path = str(exc)
        rows.append(
            {
                "role": role,
                "matrix": role.matrix_row(),
                "tools": tools.allowed_tool_names(role.id),
                "card_ok": card_ok,
                "card": card_path,
            }
        )

    if args.json:
        import json

        print(
            json.dumps(
                [
                    {
                        "id": row["role"].id,
                        "name": row["role"].name,
                        "duty": row["role"].duty,
                        "matrix": list(row["matrix"]),
                        "tools": list(row["tools"]),
                        "card_ok": row["card_ok"],
                    }
                    for row in rows
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        common.heading("九角色（权限列序：Canon读／Canon写／staging／正文／发布）")
        common.info(f"{'ID':<16}{'角色':<8}{'读':<12}{'Canon写':<8}{'staging':<8}{'正文':<6}{'发布':<6}工具")
        for row in rows:
            read, cw, stg, draft, rel = row["matrix"]
            common.info(
                f"{row['role'].id:<16}{row['role'].name:<8}{read:<12}{cw:<8}{stg:<8}"
                f"{draft:<6}{rel:<6}{', '.join(row['tools']) or '（无）'}"
            )
        missing = [row["role"].id for row in rows if not row["card_ok"]]
        if missing:
            common.fail(f"缺角色卡：{', '.join(missing)}（PRD §6.4 要求九份）")
            return 1
        common.ok("九份角色卡都在位；越权由工具网关拦，不靠提示词")
    return 0


def cmd_agent_prompt(cfg, args: argparse.Namespace) -> int:
    """渲染某个角色的提示词。`--show` 才打印全文，默认只给结构。"""
    from ..agents import prompts

    task = None
    if args.task:
        record = common.task_store(cfg).load(args.task)
        task = {
            "task_id": record.task_id,
            "type": record.task_type,
            "mode": record.write_mode,
            "scope": record.output_scope,
        }

    prompt = prompts.build(
        args.role,
        task=task,
        release=args.base_release or "",
        output_schema=args.schema or "",
        extra=args.extra or "",
    )

    if args.json:
        import json

        print(json.dumps(prompt.as_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.show:
        print(prompt.system)
        return 0

    common.heading(f"{prompt.role_id}（{prompt.role_name}）提示词")
    common.info(f"共 {prompt.tokens} tokens；工具：{', '.join(prompt.tool_names) or '（无）'}")
    for section in prompt.sections:
        mark = "硬" if section.hard else "  "
        common.info(f"  [{mark}] {section.title}")
    missing = [key for key in prompts.REQUIRED_KEYS if key not in prompts.section_keys(prompt)]
    if missing:
        common.fail(f"公共前缀缺项：{', '.join(missing)}")
        return 1
    common.ok("PRD §6.4 的九项公共约束齐全")
    common.info("加 --show 看全文，加 --json 看结构")
    return 0


def cmd_agent_usage(cfg, args: argparse.Namespace) -> int:
    """看调用计量台账。没有记录是正常状态，不是错误。"""
    from ..agents import metering

    runtime = common.need_runtime(cfg)
    rows = metering.read_usage(runtime, args.day)
    summary = metering.aggregate(rows)

    if args.json:
        import json

        print(json.dumps({"day": args.day or "today", "summary": summary, "entries": rows}, ensure_ascii=False, indent=2))
        return 0

    path = usage_path(runtime, args.day)
    common.heading(f"调用计量：{path.name}")
    if not rows:
        common.info("这一天没有记录。没有调用就是没有账，不是读不出来。")
        return 0
    common.info(
        "模型 {m} 次／工具 {t} 次／prompt {p} tokens／completion {c} tokens／失败 {f} 次".format(
            m=summary["model_calls"],
            t=summary["tool_calls"],
            p=summary["prompt_tokens"],
            c=summary["completion_tokens"],
            f=summary["failed"],
        )
    )
    for role_id, slot in sorted(summary["by_role"].items()):
        common.info(f"  {role_id}: 模型 {slot['model_calls']}／工具 {slot['tool_calls']}")
    for name, count in sorted(summary["by_tool"].items()):
        common.info(f"  工具 {name}: {count} 次")
    return 0
