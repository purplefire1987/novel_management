"""CLI 入口。V1.0 §17 的命令集。

设计约束：
- 不硬编码任何私人路径，一律走 config.load()。
- 写入前校验目标落在 Vault / 项目内；越界直接拒绝。
- 已存在内容不覆盖；冲突即停。
- 作者专属动作（approve / publish / adopt / restore）只接受 --as author。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .. import config as cfg_mod
from .. import logs
from ..agents.budget import (
    DEFAULT_CONTEXT_LIMIT,
    DEFAULT_HARD_RESERVE,
    DEFAULT_OUTPUT_RESERVE,
)
from ..domain.errors import NovelError
from ..tasks import TASK_STATUSES, TASK_TYPES
from . import (
    agent_cmd,
    approval_cmd,
    backup_cmd,
    change_cmd,
    continuity_cmd,
    entity_cmd,
    flow,
    id_cmd,
    import_cmd,
    index_cmd,
    init,
    inspect_cmd,
    maps_cmd,
    note_cmd,
    project_cmd,
    release_cmd,
    schema_cmd,
    timeline_cmd,
    work_cmd,
    workflow_cmd,
)

from ..version import __version__ as __version__  # noqa: E402  版本只有一处定义


HANDLERS = {
    "doctor": inspect_cmd.cmd_doctor,
    "status": inspect_cmd.cmd_status,
    "validate": inspect_cmd.cmd_validate,
    "diff": inspect_cmd.cmd_diff,
    "init-author": init.cmd_init_author,
    "init-project": init.cmd_init_project,
    "rebuild-index": index_cmd.cmd_rebuild_index,
    "search": index_cmd.cmd_search,
    "state": timeline_cmd.cmd_state,
    "knowledge": timeline_cmd.cmd_knowledge,
    "reveal": timeline_cmd.cmd_reveal,
    "inspect": approval_cmd.cmd_inspect,
    "issue": change_cmd.cmd_issue,
    "change": change_cmd.cmd_change,
    "impact": change_cmd.cmd_impact,
    "task": flow.cmd_task,
    "stage": flow.cmd_stage,
    "approve": flow.cmd_approve,
    "adopt": flow.cmd_adopt,
    "publish": flow.cmd_publish,
    "release": release_cmd.cmd_release,
    "import": import_cmd.cmd_import,
    "backup": backup_cmd.cmd_backup,
    "restore": backup_cmd.cmd_restore,
    "maps": maps_cmd.cmd_maps,
    "schema": schema_cmd.cmd_schema,
    "allocate-id": id_cmd.cmd_allocate_id,
    "note": note_cmd.cmd_note,
    "package-hash": schema_cmd.cmd_package_hash,
    "preflight": agent_cmd.cmd_preflight,
    "agent": agent_cmd.cmd_agent,
    "plan": work_cmd.cmd_plan,
    "write": work_cmd.cmd_write,
    "review": work_cmd.cmd_review,
    "proposal": work_cmd.cmd_proposal,
    "deps": work_cmd.cmd_deps,
    "workflow": workflow_cmd.cmd_workflow,
    "project": project_cmd.cmd_project,
    "idea": project_cmd.cmd_idea,
    "entity": entity_cmd.cmd_entity,
    "relation": entity_cmd.cmd_relation,
    "event": entity_cmd.cmd_event,
    "continuity": continuity_cmd.cmd_continuity,
}


def _add_global_args(parent: argparse.ArgumentParser, *, quiet_default: bool) -> None:
    """把四个全局参数挂到 parent 上。

    `quiet_default=True` 给子命令用，默认值取 SUPPRESS。
    原因：argparse 解析子命令时会**新开一个 namespace**，解析完再把它的全部属性
    搬回主 namespace，于是子命令那份的默认值会盖掉主命令刚解析出来的值——
    `novel --vault X status` 里的 X 就是这样被 None 抹掉的。
    用 SUPPRESS 让子命令「没写就不产生属性」，主命令那份才留得住。
    """
    missing = argparse.SUPPRESS if quiet_default else None
    parent.add_argument(
        "--vault", default=missing, help="作者 Vault 路径（覆盖环境变量与配置文件）"
    )
    parent.add_argument("--runtime", default=missing, help="Runtime 路径（必须在 Vault 之外）")
    parent.add_argument("--project", default=missing, help="项目 ID（Vault 内多于一个项目时必需）")
    parent.add_argument(
        "--log-level",
        default=argparse.SUPPRESS if quiet_default else "INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "OFF"],
        help="写入 <runtime>/logs/novel.log 的级别；OFF 关闭",
    )


def build_parser() -> argparse.ArgumentParser:
    # 全局参数同时挂在主命令与子命令上，`novel --vault X status` 与
    # `novel status --vault X` 都成立——位置不同不该让命令失效。
    common = argparse.ArgumentParser(add_help=False)
    _add_global_args(common, quiet_default=False)

    # 子命令那份必须用 SUPPRESS 做默认值，否则会把主命令解析到的值盖掉。
    # 嵌套子命令（task create / maps paths / schema list|check）同理，共用这一份。
    sub_common = argparse.ArgumentParser(add_help=False)
    _add_global_args(sub_common, quiet_default=True)

    parser = argparse.ArgumentParser(
        prog="novel",
        description="长篇小说管理系统 CLI（V1.0 §17 命令集）",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"novel {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="检查配置、依赖、路径、隔离与隐私开关", parents=[sub_common])
    sub.add_parser("status", help="查看 Vault、项目、任务、备份与最近操作", parents=[sub_common])

    p_validate = sub.add_parser("validate", help="校验结构、实体 ID、双链与项目边界", parents=[sub_common])
    p_validate.add_argument("--strict", action="store_true", help="把断链等警告计为失败")

    p_author = sub.add_parser("init-author", help="初始化作者 Vault", parents=[sub_common])
    p_author.add_argument("--author-id", default="author-local")
    p_author.add_argument("--confirm", action="store_true", help="确认写入")

    p_proj = sub.add_parser("init-project", help="创建小说项目骨架", parents=[sub_common])
    p_proj.add_argument("--id", help="项目 ID（小写字母、数字、连字符）")
    p_proj.add_argument("--name", help="项目显示名")
    p_proj.add_argument("--author-id", default="author-local")
    p_proj.add_argument("--confirm", action="store_true", help="确认写入")

    p_rebuild = sub.add_parser(
        "rebuild-index", help="从 Markdown 重建 SQLite 索引（可绑定 Git 提交）", parents=[sub_common]
    )
    p_rebuild.add_argument(
        "--canon-revision",
        default=None,
        help="按指定 Git 提交建索引（不读工作树）；省略则按当前工作树",
    )

    p_search = sub.add_parser("search", help="在索引里检索（中文短词走子串，长词走 trigram）", parents=[sub_common])
    p_search.add_argument("query", help="查询词；像 ID 的会走精确匹配")
    p_search.add_argument("--limit", type=int, default=20, help="最多返回多少条（默认 20）")
    p_search.add_argument("--json", action="store_true", help="输出 JSON，便于脚本消费")
    p_search.add_argument("--show-index", action="store_true", help="附带索引的 revision 与 build id")

    # ---- state / knowledge / reveal（T04 时间与认知）----
    p_state = sub.add_parser(
        "state",
        help="查实体在某时点的客观字段值（只读断言与状态变化，不读人物认知）",
        parents=[sub_common],
    )
    p_state.add_argument("entity", help="实体 ID，如 CHAR-000001")
    p_state.add_argument("--field", default="", help="只看某个字段，如 life_status")
    anchor_state = p_state.add_mutually_exclusive_group()
    anchor_state.add_argument("--after", default=None, metavar="EVT", help="参照事件：查它发生之后")
    anchor_state.add_argument("--before", default=None, metavar="EVT", help="参照事件：查它发生之前")
    p_state.add_argument("--json", action="store_true", help="输出 JSON（含 status 与全部候选记录）")
    p_state.add_argument("--show-index", action="store_true", help="附带索引 revision 与 build id")

    p_kn = sub.add_parser(
        "knowledge",
        help="查角色对某命题的态度（查不到不等于「不知道」）",
        parents=[sub_common],
    )
    p_kn.add_argument("holder", help="持有认知的角色 ID，如 CHAR-000001")
    p_kn.add_argument("--subject", default="", help="命题主体 ID，如 CHAR-000002")
    p_kn.add_argument("--predicate", default="", help="命题谓词，如 life_status")
    anchor_kn = p_kn.add_mutually_exclusive_group()
    anchor_kn.add_argument("--after", default=None, metavar="EVT", help="参照事件：查它发生之后")
    anchor_kn.add_argument("--before", default=None, metavar="EVT", help="参照事件：查它发生之前")
    p_kn.add_argument("--json", action="store_true", help="输出 JSON（含 status 与全部候选记录）")

    p_rev = sub.add_parser(
        "reveal",
        help="查某命题在哪些叙述位置揭示给读者（不推断任何角色是否知道）",
        parents=[sub_common],
    )
    p_rev.add_argument("proposition", help="命题 ID，如 AST-000001")
    p_rev.add_argument("--json", action="store_true", help="输出 JSON")

    # ---- task ----
    p_task = sub.add_parser("task", help="创建或查看任务记录", parents=[sub_common])
    task_sub = p_task.add_subparsers(dest="task_command", required=True)

    p_tc = task_sub.add_parser("create", help="创建任务", parents=[sub_common])
    p_tc.add_argument(
        "--type",
        required=True,
        choices=TASK_TYPES,
        help="任务类型：" + "/".join(TASK_TYPES),
    )
    p_tc.add_argument("--assignee", default="", help="责任 Agent")
    p_tc.add_argument("--mode", default="staging", help="read_only / staging / draft / proposal")
    p_tc.add_argument("--scope", default="", help="允许写入的目录前缀（项目内相对路径）")
    p_tc.add_argument("--input", action="append", default=[], help="输入文件，可重复或逗号分隔")
    p_tc.add_argument("--note", default="")
    p_tc.add_argument("--as", dest="actor", default=None, help="操作者：author 或 agent 名")

    p_ti = task_sub.add_parser("inspect", help="查看任务详情", parents=[sub_common])
    p_ti.add_argument("task_id", help="形如 task_000001")
    p_ti.add_argument("--json", action="store_true", help="附带 JSON 输出")

    p_tl = task_sub.add_parser("list", help="列出任务及其状态（任务落在 Runtime）", parents=[sub_common])
    p_tl.add_argument(
        "--status",
        default="",
        choices=("",) + TASK_STATUSES,
        help="只看某个状态：" + "/".join(TASK_STATUSES),
    )
    p_tl.add_argument("--json", action="store_true")

    p_tt = task_sub.add_parser(
        "transition",
        help="推进任务状态（§10.4 状态机）",
        description=(
            "V1.0 §10.4 的状态机：queued → ready → running → waiting_review → "
            "approved / rejected → completed，异常态 blocked/paused/failed/cancelled。\n"
            "非顺流迁移（blocked/paused/failed/rejected/cancelled）必须写 --reason，"
            "否则三天后只有状态、没有「为什么」。"
        ),
        parents=[sub_common],
    )
    p_tt.add_argument("task_id", help="形如 task_000001")
    p_tt.add_argument("--to", required=True, choices=TASK_STATUSES, help="目标状态")
    p_tt.add_argument("--reason", default="", help="迁移原因（非顺流迁移必填）")
    p_tt.add_argument(
        "--related",
        action="append",
        default=[],
        metavar="TASK_ID",
        help="关联任务，可重复（§10.4 要求记录关联任务）",
    )
    p_tt.add_argument("--as", dest="actor", default=None, help="操作者：author 或 agent 名")


    # ---- stage ----
    p_stage = sub.add_parser("stage", help="把 Agent 产出写入 staging（唯一允许的提案落点）", parents=[sub_common])
    p_stage.add_argument("--task", required=True, help="任务 ID")
    p_stage.add_argument("--from", dest="source", required=True, help="源文件；`-` 表示从 stdin 读")
    p_stage.add_argument("--to", dest="target", required=True, help="目标正式文件的项目内相对路径")
    p_stage.add_argument("--note", default="")
    p_stage.add_argument("--replace", action="store_true", help="覆盖本任务已存在的同名产物")
    p_stage.add_argument("--as", dest="actor", default=None, help="操作者：author 或 agent 名")

    # ---- diff ----
    p_diff = sub.add_parser("diff", help="查看 staging 与正式文件的差异", parents=[sub_common])
    p_diff.add_argument("--task", required=True, help="任务 ID")
    p_diff.add_argument("--path", default=None, help="只看某个目标文件")
    p_diff.add_argument("--full", action="store_true", help="不截断差异行")

    # ---- approve ----
    p_approve = sub.add_parser("approve", help="作者批准 staging 内容（绑定内容指纹）", parents=[sub_common])
    p_approve.add_argument(
        "kind",
        nargs="?",
        choices=["artifact", "proposal", "release", "text"],
        help="按对象批准：artifact（staging 产物）／text（正文，要求先过审核）／"
        "release（发布计划）／proposal（记忆提案）。省略则走 --task",
    )
    p_approve.add_argument("candidate", nargs="?", help="候选 ID：task_000001 或 task_000001:<目标路径>")
    p_approve.add_argument("--hash", dest="hash", default="", help="当前内容哈希（非交互批准必填）")
    p_approve.add_argument("--task", default=None, help="任务 ID（旧用法：一次批准该任务全部 staging）")
    p_approve.add_argument("--chapter", default=None, help="章节 ID（approve text 用）")
    p_approve.add_argument("--note", default="")
    p_approve.add_argument("--reject", action="store_true", help="驳回而不是批准")
    p_approve.add_argument("--force", action="store_true", help="有冲突仍批准（承担覆盖作者手改的风险）")
    p_approve.add_argument("--as", dest="actor", default=None, help="只接受 author")

    # ---- inspect（T05）----
    p_inspect = sub.add_parser(
        "inspect",
        help="看清一个候选：内容哈希、与正式版差异、风险、阻断问题、批准范围",
        parents=[sub_common],
    )
    p_inspect.add_argument("candidate", help="task_000001 或 task_000001:<目标相对路径>")
    p_inspect.add_argument("--json", action="store_true", help="输出 JSON")

    # ---- issue（T05 问题等级）----
    p_issue = sub.add_parser("issue", help="登记与处理问题（BLOCKER 不能豁免）", parents=[sub_common])
    issue_sub = p_issue.add_subparsers(dest="issue_command", required=True)

    p_in = issue_sub.add_parser("new", help="登记一条问题", parents=[sub_common])
    p_in.add_argument("--severity", required=True, help="BLOCKER / NON_BLOCKING / SUGGESTION")
    p_in.add_argument("--category", required=True, help="KNOWLEDGE_BOUNDARY / CONTINUITY / TIMELINE / RULES / SCOPE / QUALITY / OTHER")
    p_in.add_argument("--target", required=True, help="问题指向的对象 ID（任务或产物）")
    p_in.add_argument("--block", default="", help="块 ID，如 p0031")
    p_in.add_argument("--description", required=True, help="问题描述")
    p_in.add_argument("--evidence", action="append", default=[], help="证据引用，可重复")
    p_in.add_argument("--suggest", action="append", default=[], help="建议动作，可重复")
    p_in.add_argument("--as", dest="actor", default=None, help="操作者：author 或 agent 名")

    p_il = issue_sub.add_parser("list", help="列出问题", parents=[sub_common])
    p_il.add_argument("--severity", default="", help="只看某个等级")
    p_il.add_argument("--status", default="", help="只看某个状态")
    p_il.add_argument("--json", action="store_true")

    p_ir = issue_sub.add_parser("resolve", help="关闭问题（BLOCKER 必须写核验依据）", parents=[sub_common])
    p_ir.add_argument("issue_id")
    p_ir.add_argument("--note", default="")
    p_ir.add_argument("--as", dest="actor", default=None)

    p_iw = issue_sub.add_parser("waive", help="豁免问题（BLOCKER 一律拒绝）", parents=[sub_common])
    p_iw.add_argument("issue_id")
    p_iw.add_argument("--note", default="")
    p_iw.add_argument("--as", dest="actor", default=None)

    # ---- change（T05 变更请求）----
    p_change = sub.add_parser("change", help="登记变更请求（RETCON 走独立审批）", parents=[sub_common])
    change_sub = p_change.add_subparsers(dest="change_command", required=True)

    p_cc = change_sub.add_parser("create", help="登记一笔变更", parents=[sub_common])
    p_cc.add_argument("--type", required=True, help="PLAN_CHANGE / STATE_CHANGE / CORRECTION / RETCON / EDITORIAL")
    p_cc.add_argument("--reason", required=True, help="为什么改（必填）")
    p_cc.add_argument("--target", action="append", default=[], help="受影响对象 ID，可重复")
    p_cc.add_argument("--scope", action="append", default=[], help="受影响路径前缀，可重复")
    p_cc.add_argument("--old", default="", help="原记录摘要")
    p_cc.add_argument("--new", dest="new", default="", help="新记录摘要")
    p_cc.add_argument("--migration", default="", help="迁移计划")
    p_cc.add_argument("--release-strategy", default="", help="发布策略")
    p_cc.add_argument("--as", dest="actor", default=None, help="只接受 author")

    p_cl = change_sub.add_parser("list", help="列出变更", parents=[sub_common])
    p_cl.add_argument("--json", action="store_true")

    p_cs = change_sub.add_parser("show", help="查看一笔变更与它的影响报告", parents=[sub_common])
    p_cs.add_argument("change_id")
    p_cs.add_argument("--json", action="store_true")

    p_ca = change_sub.add_parser(
        "approve", help="独立审批（RETCON 必须：先 impact，再绑报告哈希批）", parents=[sub_common]
    )
    p_ca.add_argument("change_id")
    p_ca.add_argument("--hash", required=True, help="影响报告的哈希")
    p_ca.add_argument("--rewrite-chapters", action="store_true", help="承诺改写受影响正文")
    p_ca.add_argument("--narrow-scope", action="store_true", help="明确缩小变更范围（不处理正文）")
    p_ca.add_argument("--note", default="")
    p_ca.add_argument("--force", action="store_true", help="非 RETCON 也走独立审批")
    p_ca.add_argument("--as", dest="actor", default=None, help="只接受 author")

    p_cr = change_sub.add_parser("reject", help="驳回变更", parents=[sub_common])
    p_cr.add_argument("change_id")
    p_cr.add_argument("--note", default="")
    p_cr.add_argument("--as", dest="actor", default=None)

    # ---- impact（T05 影响分析）----
    p_impact = sub.add_parser("impact", help="生成变更的影响报告（按索引反查）", parents=[sub_common])
    p_impact.add_argument("change_id")
    p_impact.add_argument("--dry-run", action="store_true", help="只算不写，不绑定哈希")
    p_impact.add_argument("--json", action="store_true")

    # ---- adopt ----
    p_adopt = sub.add_parser("adopt", help="登记作者直接编辑后的文件状态", parents=[sub_common])
    p_adopt.add_argument("--note", default="")
    p_adopt.add_argument("--dry-run", action="store_true", help="只报告变化，不推进基线")
    p_adopt.add_argument("--as", dest="actor", default=None, help="只接受 author")

    # ---- publish ----
    p_pub = sub.add_parser("publish", help="把已批准内容转为正式状态", parents=[sub_common])
    p_pub.add_argument("--task", required=True, help="任务 ID")
    p_pub.add_argument("--note", default="")
    p_pub.add_argument("--confirm", action="store_true", help="确认发布")
    p_pub.add_argument("--confirm-canon", action="store_true", help="写入 01_Canon 时的二次确认")
    p_pub.add_argument("--release", default="", help="指定已准备的发布号（默认取该任务最新一份计划）")
    p_pub.add_argument("--as", dest="actor", default=None, help="只接受 author")

    # ---- release（T06 发布协议）----
    p_release = sub.add_parser("release", help="发布计划、执行、恢复与回退", parents=[sub_common])
    release_sub = p_release.add_subparsers(dest="release_command", required=True)

    p_rprep = release_sub.add_parser(
        "prepare", help="按已批准的 staging 生成 ReleasePlan（不动正式区）", parents=[sub_common]
    )
    p_rprep.add_argument("--task", required=True, help="任务 ID")
    p_rprep.add_argument("--confirm-canon", action="store_true", help="计划里含 Canon 写入时预先确认")
    p_rprep.add_argument(
        "--skip-closure",
        action="store_true",
        help="跳过「正文与记忆同批」检查（会让记忆滞后于正文；只在明确知情时用）",
    )
    p_rprep.add_argument("--as", dest="actor", default=None, help="操作者")

    p_rshow = release_sub.add_parser("show", help="查看计划、批准、事务日志与清单", parents=[sub_common])
    p_rshow.add_argument("release_id")
    p_rshow.add_argument("--json", action="store_true")

    p_rexec = release_sub.add_parser(
        "execute", help="执行发布（22 步；可从中断处续）", parents=[sub_common]
    )
    p_rexec.add_argument("release_id")
    p_rexec.add_argument("--confirm", action="store_true", help="确认写正式区")
    p_rexec.add_argument("--confirm-canon", action="store_true", help="写入 01_Canon 的二次确认")
    p_rexec.add_argument("--as", dest="actor", default=None, help="只接受 author")

    p_rres = release_sub.add_parser("resume", help="从中断处继续一次发布", parents=[sub_common])
    p_rres.add_argument("release_id")
    p_rres.add_argument("--confirm-canon", action="store_true")
    p_rres.add_argument("--as", dest="actor", default=None, help="只接受 author")

    p_rabort = release_sub.add_parser("abort", help="中止未切换的发布（已切换请用 rollback）", parents=[sub_common])
    p_rabort.add_argument("release_id")
    p_rabort.add_argument("--note", default="")
    p_rabort.add_argument("--as", dest="actor", default=None, help="只接受 author")

    p_rstat = release_sub.add_parser("status", help="交叉验证指针/清单/索引/日志/回执", parents=[sub_common])
    p_rstat.add_argument("--deep", action="store_true", help="逐个文件核对候选区与清单")
    p_rstat.add_argument("--json", action="store_true")

    p_rrep = release_sub.add_parser("repair", help="补写缺失的回执（仅在确实切换成功时）", parents=[sub_common])
    p_rrep.add_argument("release_id")
    p_rrep.add_argument("--as", dest="actor", default=None, help="只接受 author")

    p_rrb = release_sub.add_parser("rollback", help="生成回退包（新 release_id，复用旧版内容与索引）", parents=[sub_common])
    p_rrb.add_argument("--to", required=True, help="目标发布号")
    p_rrb.add_argument("--reason", default="", help="为什么回退（必填项建议写清）")
    p_rrb.add_argument("--as", dest="actor", default=None, help="只接受 author")

    p_rlist = release_sub.add_parser("list", help="列出发布与状态", parents=[sub_common])
    p_rlist.add_argument("--json", action="store_true")

    p_runlock = release_sub.add_parser("unlock", help="检查发布锁是否被持有（不按年龄猜）", parents=[sub_common])
    p_runlock.add_argument("--confirm", action="store_true", help="执行检查（获取并立即释放）")
    p_runlock.add_argument("--as", dest="actor", default=None, help="只接受 author")

    # ---- import（T07 原稿导入）----
    p_import = sub.add_parser("import", help="原稿导入：扫描、报告、分批入 staging", parents=[sub_common])
    import_sub = p_import.add_subparsers(dest="import_command", required=True)

    p_iscan = import_sub.add_parser(
        "scan", help="扫描原稿：保全原始字节、解析章节、提取候选、抽样（不写正式区）", parents=[sub_common]
    )
    p_iscan.add_argument("paths", nargs="+", help="一个或多个 .md/.markdown/.txt（也可以是目录）")
    p_iscan.add_argument("--level", type=int, default=0, help="按第几级 Markdown 标题切章（0=自动取最浅）")
    p_iscan.add_argument("--seed", default="", help="抽样种子；同种子同结果")
    p_iscan.add_argument("--sample", type=int, default=0, help="抽样条数，默认 100")
    p_iscan.add_argument("--batch-size", type=int, default=0, help="每批章数（只用于提示，不切分）")
    p_iscan.add_argument("--json", action="store_true")
    p_iscan.add_argument("--as", dest="actor", default=None, help="操作者")

    p_irep = import_sub.add_parser("report", help="查看某次导入的全部维度", parents=[sub_common])
    p_irep.add_argument("import_id")
    p_irep.add_argument("--chapters", action="store_true", help="列出每章的行区间")
    p_irep.add_argument("--mentions", action="store_true", help="列出候选提及")
    p_irep.add_argument("--json", action="store_true")

    p_istage = import_sub.add_parser(
        "stage", help="把某一批章节写进 staging（不写正式区）", parents=[sub_common]
    )
    p_istage.add_argument("import_id")
    p_istage.add_argument("--batch", type=int, default=1, help="第几批（1 起）")
    p_istage.add_argument("--batch-size", type=int, default=0, help="每批章数；0 表示全放一批")
    p_istage.add_argument("--as", dest="actor", default=None, help="操作者")

    p_iwhere = import_sub.add_parser(
        "where", help="按来源定位符回读原文（验证来源是否可解析）", parents=[sub_common]
    )
    p_iwhere.add_argument("import_id")
    p_iwhere.add_argument("locator", help="形如 文件名#L12-L18")

    p_ilist = import_sub.add_parser("list", help="列出历次导入", parents=[sub_common])
    p_ilist.add_argument("--json", action="store_true")

    # ---- backup / restore ----
    p_backup = sub.add_parser("backup", help="创建本地备份（含清单与回读校验）", parents=[sub_common])
    p_backup.add_argument("--out", default=None, help="备份目录，默认 <runtime>/backups")
    p_backup.add_argument("--label", default="", help="备注标签")
    p_backup.add_argument("--no-git", action="store_true", help="不备份 .git")
    p_backup.add_argument("--list", action="store_true", help="只列出现有备份")
    p_backup.add_argument(
        "--prune",
        action="store_true",
        help="清理旧备份：只处理 BK-*，人工建的备份不动（默认只列不删）",
    )
    p_backup.add_argument("--keep", type=int, default=10, help="保留最近 N 份（配合 --prune），默认 10")
    p_backup.add_argument("--confirm", action="store_true", help="真正删除（不加则只预演）")
    p_backup.add_argument("--as", dest="actor", default=None, help="操作者：author 或 agent 名")

    p_restore = sub.add_parser("restore", help="从备份恢复（默认预演，不覆盖现存文件）", parents=[sub_common])
    p_restore.add_argument("--backup-id", required=True, help="形如 BK-20261120-101500")
    p_restore.add_argument("--dry-run", action="store_true", help="只预演，不写盘")
    p_restore.add_argument("--confirm", action="store_true", help="执行恢复")
    p_restore.add_argument("--overwrite", action="store_true", help="覆盖冲突文件（覆盖前另存一份）")
    p_restore.add_argument("--out", default=None, help="备份目录，默认 <runtime>/backups")
    p_restore.add_argument("--as", dest="actor", default=None, help="只接受 author")

    # ---- maps ----
    p_maps = sub.add_parser("maps", help="从 Canon 数据重画地图页与 canvas（默认预演）", parents=[sub_common])
    maps_sub = p_maps.add_subparsers(dest="maps_command", required=True)

    p_mpaths = maps_sub.add_parser("paths", help="打印地图生成用到的全部路径", parents=[sub_common])
    p_mpaths.add_argument("--world-subdir", default=None)

    p_madm = maps_sub.add_parser(
        "import-admin", help="把资产库「地图库」重排为 WORLD-0040 行政区划表", parents=[sub_common]
    )
    p_madm.add_argument("--world-subdir", default=None)
    p_madm.add_argument("--xlsx", default=None, help="资产表路径，默认项目内 90_Archive 下那份")
    p_madm.add_argument("--confirm", action="store_true", help="执行写入")

    p_mbuild = maps_sub.add_parser("build", help="重画地图页与两张 canvas", parents=[sub_common])
    p_mbuild.add_argument("--world-subdir", default=None)
    p_mbuild.add_argument("--xlsx", default=None, help="资产表路径（build 不读，仅为参数一致）")
    p_mbuild.add_argument("--maps-only", action="store_true", help="只重画图，不碰州域／府／城邑档案")
    p_mbuild.add_argument("--confirm", action="store_true", help="执行写入")

    # ---- schema（T02 契约）----
    p_schema = sub.add_parser("schema", help="查看与校验数据契约（JSON Schema）", parents=[sub_common])
    schema_sub = p_schema.add_subparsers(dest="schema_command", required=True)

    p_slist = schema_sub.add_parser("list", help="列出可校验类型", parents=[sub_common])
    p_slist.add_argument("--schema-dir", default=None, help="schema 目录，默认自动定位")

    p_scheck = schema_sub.add_parser("check", help="按类型校验 JSON 文件（- 读 stdin）", parents=[sub_common])
    p_scheck.add_argument("type", help="类型名，如 entity／assertion／approval")
    p_scheck.add_argument("files", nargs="*", help="JSON 文件；`-` 表示 stdin")
    p_scheck.add_argument("--schema-dir", default=None)

    # ---- allocate-id ----
    p_alloc = sub.add_parser("allocate-id", help="分配稳定 ID（默认预演，--confirm 落盘）", parents=[sub_common])
    p_alloc.add_argument(
        "--type",
        required=True,
        help="V1.0 §9.2 的类别名或前缀：ent（实体）/ ch（正文）/ scene / evt / plan / idea / prop / fs …"
        "；旧形态 CHARACTER／LOCATION 仍认，但新号只发小写下划线形态",
    )
    p_alloc.add_argument("--count", type=int, default=1, help="一次分配几个（默认 1）")
    p_alloc.add_argument("--reason", default="", help="分配原因，写进审计")
    p_alloc.add_argument("--confirm", action="store_true", help="真正发号并写审计；缺省只预演")
    p_alloc.add_argument("--allow-dirty", action="store_true", help="本前缀已有重号仍发号（不推荐）")
    p_alloc.add_argument("--json", action="store_true", help="附带 JSON 输出")
    p_alloc.add_argument("--as", dest="actor", default=None, help="操作者：author 或 agent 名")

    # ---- package-hash ----
    p_ph = sub.add_parser("package-hash", help="算规范化 JSON 的 sha256（审批与发布绑这个值）", parents=[sub_common])
    p_ph.add_argument("path", help="JSON 文件；`-` 读 stdin")
    p_ph.add_argument("--check", default=None, help="比对期望哈希，不一致退出 1")
    p_ph.add_argument("--canonical", action="store_true", help="同时打印规范化 JSON 原文")

    # ---- preflight（T08 写前资料包）----
    p_pf = sub.add_parser(
        "preflight",
        help="产出写前资料包：八级检索、预算账、PASS／BLOCKED（PRD §13）",
        parents=[sub_common],
    )
    p_pf.add_argument("chapter", help="章节 ID，如 CH-000102")
    p_pf.add_argument("--base-release", default="", help="基线发布；省略则取当前正式指针")
    p_pf.add_argument(
        "--budget", type=int, default=DEFAULT_CONTEXT_LIMIT, help=f"上下文预算上限（默认 {DEFAULT_CONTEXT_LIMIT}）"
    )
    p_pf.add_argument(
        "--output-reserve",
        type=int,
        default=DEFAULT_OUTPUT_RESERVE,
        help="给输出预留的空间",
    )
    p_pf.add_argument(
        "--hard-reserve",
        type=int,
        default=DEFAULT_HARD_RESERVE,
        help="给硬约束预留的空间；硬约束超过它就直接 BLOCKED，不压缩",
    )
    p_pf.add_argument("--search-limit", type=int, default=6, help="每个关键词最多取几条（Top-K）")
    p_pf.add_argument("--role", default="researcher", help="执行角色，默认资料官")
    p_pf.add_argument("--no-write", action="store_true", help="只算不落盘")
    p_pf.add_argument("--json", action="store_true", help="输出 JSON")

    # ---- agent（T08 九角色）----
    p_agent = sub.add_parser("agent", help="九角色：权限矩阵、提示词、调用计量", parents=[sub_common])
    agent_sub = p_agent.add_subparsers(dest="agent_command", required=True)

    p_al = agent_sub.add_parser("list", help="列出九角色的权限、可用工具与角色卡是否在位", parents=[sub_common])
    p_al.add_argument("--json", action="store_true")

    p_ap = agent_sub.add_parser("prompt", help="渲染某角色的提示词（默认只看结构）", parents=[sub_common])
    p_ap.add_argument("--role", required=True, help="角色 ID，如 writer")
    p_ap.add_argument("--task", default="", help="任务 ID；给了就把授权写进提示词")
    p_ap.add_argument("--base-release", default="", help="基线发布")
    p_ap.add_argument("--schema", default="", help="输出 schema 名")
    p_ap.add_argument("--extra", default="", help="补充要求")
    p_ap.add_argument("--show", action="store_true", help="打印全文")
    p_ap.add_argument("--json", action="store_true")

    p_au = agent_sub.add_parser("usage", help="看调用计量台账", parents=[sub_common])
    p_au.add_argument("--day", default="", help="日期 YYYYMMDD；省略为今天")
    p_au.add_argument("--json", action="store_true")

    # ---- plan（T09 规划契约）----
    p_plan = sub.add_parser(
        "plan",
        help="规划契约：章卡够不够开写、能从散文里派生哪些结构化约束（PRD §10）",
        parents=[sub_common],
    )
    plan_sub = p_plan.add_subparsers(dest="plan_command", required=True)

    p_ps = plan_sub.add_parser("show", help="逐项看 §10.2 的字段有没有、出处在哪", parents=[sub_common])
    p_ps.add_argument("chapter", help="章节 ID（ch_XXXXXX）或规划 ID（plan_XXXXXX）")
    p_ps.add_argument("--json", action="store_true")

    p_pc = plan_sub.add_parser("check", help="只判够不够开写（缺必需字段返回非零）", parents=[sub_common])
    p_pc.add_argument("chapter", help="章节 ID（ch_XXXXXX）或规划 ID（plan_XXXXXX）")
    p_pc.add_argument("--json", action="store_true")

    p_pd = plan_sub.add_parser(
        "derive",
        help="从「必须发生／禁止发生」派生结构化字段（只派生确定性可读的部分）",
        parents=[sub_common],
    )
    p_pd.add_argument("chapter")
    p_pd.add_argument("--write", action="store_true", help="写回章卡 frontmatter（只增不覆盖，正文不动）")
    p_pd.add_argument("--as", dest="actor", default=None, help="写回只接受 author")
    p_pd.add_argument("--json", action="store_true")

    p_pcr = plan_sub.add_parser(
        "create",
        help="建一份章纲骨架：只放小节标题，不预填内容（预填会被 derive 当成作者原话）",
        parents=[sub_common],
    )
    p_pcr.add_argument("--chapter", required=True, help="章节 ID，如 ch_000123")
    p_pcr.add_argument("--title", required=True, help="章标题：用于文件名与 frontmatter 的 name/title")
    p_pcr.add_argument("--volume", default="", help="所属卷的规划 ID，如 plan_000008")
    p_pcr.add_argument("--confirm", action="store_true", help="落盘（默认只预演）")
    p_pcr.add_argument("--as", dest="actor", default=None, help="执行角色，默认 author-local")

    # ---- write（T09 主笔写稿）----
    p_write = sub.add_parser(
        "write",
        help="主笔写草稿：前置规划与资料包，产物只进 staging（永不写 01_Canon）",
        parents=[sub_common],
    )
    p_write.add_argument("--task", required=True, help="写作任务 ID")
    p_write.add_argument("--chapter", required=True, help="章节 ID")
    p_write.add_argument("--mock-response", default="", help="演练：从文件读一段模型输出（本版未接真实适配器）")
    p_write.add_argument("--model", default="", help="模型名（给真适配器用）")
    p_write.add_argument("--base-release", default="")
    p_write.add_argument("--preflight", default="", help="指定资料包 ID；省略则复用最新 PASS，没有就现跑一次")
    p_write.add_argument("--note", default="")
    p_write.add_argument("--replace", action="store_true", help="覆盖本任务已有的同名草稿（返修时用）")
    p_write.add_argument("--as", dest="actor", default=None, help="执行角色，默认 writer")

    # ---- review（T09 审核与风险路由）----
    p_review = sub.add_parser(
        "review",
        help="审核草稿：风险路由、返修预算、高风险不许自审（PRD §14.2）",
        parents=[sub_common],
    )
    review_sub = p_review.add_subparsers(dest="review_command", required=True)

    p_rv = review_sub.add_parser("submit", help="登记一次审核结论", parents=[sub_common])
    p_rv.add_argument("--task", required=True)
    p_rv.add_argument("--chapter", required=True)
    p_rv.add_argument("--role", default="editor", help="审核角色（九角色之一），默认 editor")
    p_rv.add_argument("--verdict", default="approve", help="approve 或 revise")
    p_rv.add_argument("--risk", default="", help="人工调整风险：LOW/MEDIUM/HIGH（只能升不能降）")
    p_rv.add_argument("--max-rounds", type=int, default=3, help="返修预算（默认 3 轮）")
    p_rv.add_argument("--note", default="")
    p_rv.add_argument(
        "--dimension",
        action="append",
        default=[],
        metavar="维度=结论[:说明]",
        help="十维审核结论，可重复。结论取 pass/issue/not_applicable/not_checked；"
        "如 --dimension structure=pass --dimension causality=issue:动机缺失",
    )
    p_rv.add_argument(
        "--allow-unchecked",
        action="store_true",
        help="十维有未查项也下 APPROVE（作者专用；其他角色会被拒）",
    )
    p_rv.add_argument("--as", dest="actor", default=None, help="审核人")

    p_rd = review_sub.add_parser(
        "dimensions", help="列出 V1.0 §11 的十项检查与机器能判的范围", parents=[sub_common]
    )
    p_rd.add_argument("--json", action="store_true")

    p_rs = review_sub.add_parser("status", help="看这一章现在能不能过", parents=[sub_common])
    p_rs.add_argument("--task", required=True)
    p_rs.add_argument("--chapter", required=True)
    p_rs.add_argument("--json", action="store_true")

    # ---- proposal（T10 记忆提案）----
    p_prop = sub.add_parser(
        "proposal",
        help="记忆提案：从已批准正文提取、校验源哈希、作者批准（PRD §15）",
        parents=[sub_common],
    )
    prop_sub = p_prop.add_subparsers(dest="proposal_command", required=True)

    p_pe = prop_sub.add_parser("extract", help="从已批准正文提取记忆候选（不要求正文已发布）", parents=[sub_common])
    p_pe.add_argument("--task", required=True)
    p_pe.add_argument("--chapter", required=True)
    p_pe.add_argument("--mock-response", default="", help="演练：从文件读模型输出；不给则走确定性骨架")
    p_pe.add_argument("--model", default="")
    p_pe.add_argument("--base-release", default="")
    p_pe.add_argument("--note", default="")

    p_pl = prop_sub.add_parser("list", help="列出记忆提案", parents=[sub_common])
    p_pl.add_argument("--task", default="")
    p_pl.add_argument("--chapter", default="")

    p_psh = prop_sub.add_parser("show", help="看一份提案的条目", parents=[sub_common])
    p_psh.add_argument("proposal_id")
    p_psh.add_argument("--json", action="store_true")

    p_pv = prop_sub.add_parser("verify", help="源哈希变了没有（STALE 检查）", parents=[sub_common])
    p_pv.add_argument("proposal_id")

    p_pa = prop_sub.add_parser("approve", help="作者批准一份提案", parents=[sub_common])
    p_pa.add_argument("proposal_id")
    p_pa.add_argument("--note", default="")
    p_pa.add_argument("--as", dest="actor", default=None, help="只接受 author")

    p_pr = prop_sub.add_parser("reject", help="作者驳回一份提案", parents=[sub_common])
    p_pr.add_argument("proposal_id")
    p_pr.add_argument("--reason", default="")
    p_pr.add_argument("--as", dest="actor", default=None, help="只接受 author")

    p_pn = prop_sub.add_parser(
        "declare-none", help="显式声明本章不产记忆（发布闸门据此放行）", parents=[sub_common]
    )
    p_pn.add_argument("--task", default="")
    p_pn.add_argument("--chapter", required=True)
    p_pn.add_argument("--reason", required=True, help="必须写理由：没有理由的声明等于跳过检查")
    p_pn.add_argument("--as", dest="actor", default=None, help="只接受 author")

    # ---- deps（T10 依赖失效与共同发布）----
    p_deps = sub.add_parser("deps", help="依赖失效与共同发布检查（PRD §10.5／§15）", parents=[sub_common])
    deps_sub = p_deps.add_subparsers(dest="deps_command", required=True)

    p_dc = deps_sub.add_parser("check", help="逐条判依赖是否还成立（只有相关变化才失效）", parents=[sub_common])
    p_dc.add_argument("--json", action="store_true")

    p_ds = deps_sub.add_parser("show", help="看某份产物的依赖与状态", parents=[sub_common])
    p_ds.add_argument("artifact", help="产物 ID，如 PLAN-CH-0001")

    p_dl = deps_sub.add_parser(
        "closure", help="发布前问：每条正文的记忆都说清楚了吗", parents=[sub_common]
    )
    p_dl.add_argument("--task", default="", help="按任务的 staging 推出正文清单")
    p_dl.add_argument("--chapter", action="append", default=[], help="直接指定章节，可重复")
    p_dl.add_argument("--json", action="store_true")

    # ---- note（08_Reviews 轻量写通道）----
    p_note = sub.add_parser(
        "note",
        help="把审阅/审计意见写进 08_Reviews（不走 publish，不算签字基线）",
        parents=[sub_common],
    )
    note_sub = p_note.add_subparsers(dest="note_command", required=True)

    p_na = note_sub.add_parser("add", help="写入一份审阅记录（同名即拒，绝不覆盖）", parents=[sub_common])
    p_na.add_argument("--kind", default="review", help="review / audit / change-log（默认 review）")
    p_na.add_argument("--title", required=True, help="记录标题，同时决定文件名")
    p_na.add_argument("--body", default="", help="正文；不给则读 --body-file 或 stdin")
    p_na.add_argument("--body-file", default="", help="从文件读正文")
    p_na.add_argument("--scope", default="", help="这份意见管到哪里；默认同标题")
    p_na.add_argument("--task-id", default="", help="关联任务，如 task_000001")
    p_na.add_argument("--chapter-id", default="", help="关联章节，如 CH-0001")
    p_na.add_argument("--verdict", default="", help="pass / revise / reject / partial（只是意见，不是批准）")
    p_na.add_argument("--source-sha256", default="", help="被审对象的内容指纹，正文一改即视为过期")
    p_na.add_argument("--created-at", default="", help="日期，默认今天")
    p_na.add_argument("--dry-run", action="store_true", help="只看目标路径与 frontmatter")
    p_na.add_argument("--as", dest="actor", default=None, help="记录人（九角色之一或 author）")

    p_nl = note_sub.add_parser("list", help="列出已有审阅记录", parents=[sub_common])
    p_nl.add_argument("--kind", default="", help="只看某一类")
    p_nl.add_argument("--json", action="store_true")

    # ---- workflow（V1.0 §10 声明式工作流）----
    p_wf = sub.add_parser(
        "workflow",
        help="工作流目录：列出、校验、开实例、暂停与恢复（V1.0 §10）",
        parents=[sub_common],
    )
    wf_sub = p_wf.add_subparsers(dest="workflow_command", required=True)

    p_wl = wf_sub.add_parser("list", help="列出 20 条工作流", parents=[sub_common])
    p_wl.add_argument("--stage", default="", help="只看某个阶段，如 planning / review")
    p_wl.add_argument(
        "--agent",
        default="",
        help="只看某个角色的工作流（editor_in_chief/world_architect/editor/dice_mistress/researcher/writer/memory_manager/intake_clerk/version_steward）",
    )
    p_wl.add_argument("--json", action="store_true")

    p_ws = wf_sub.add_parser("show", help="看一条工作流的完整定义", parents=[sub_common])
    p_ws.add_argument("id", help="工作流编号，如 WF-14")
    p_ws.add_argument("--json", action="store_true")

    wf_sub.add_parser(
        "validate", help="逐字段校验目录：编号齐不齐、必填缺不缺、引用指没指空", parents=[sub_common]
    )

    p_wr = wf_sub.add_parser("run", help="开一个实例（只记账，不执行步骤）", parents=[sub_common])
    p_wr.add_argument("id", help="工作流编号，如 WF-13")
    p_wr.add_argument("--note", default="", help="这次开跑的原因")
    p_wr.add_argument("--force", action="store_true", help="已有运行中实例时先取消它")
    p_wr.add_argument("--as", dest="actor", default=None, help="操作者（九角色之一或 author）")
    p_wr.add_argument("--json", action="store_true")

    p_wp = wf_sub.add_parser("pause", help="暂停一个实例", parents=[sub_common])
    p_wp.add_argument("instance", help="实例 ID，如 WF-13-20261122-173000-01")
    p_wp.add_argument("--reason", default="", help="为什么停")
    p_wp.add_argument("--as", dest="actor", default=None)

    p_wres = wf_sub.add_parser("resume", help="恢复一个实例", parents=[sub_common])
    p_wres.add_argument("instance")
    p_wres.add_argument("--reason", default="")
    p_wres.add_argument("--as", dest="actor", default=None)

    # ---- project（V1.0 §17）----
    p_proj2 = sub.add_parser("project", help="项目列表与档案（V1.0 §17）", parents=[sub_common])
    proj_sub = p_proj2.add_subparsers(dest="project_command", required=True)
    p_pl = proj_sub.add_parser("list", help="列出 Vault 里所有项目", parents=[sub_common])
    p_pl.add_argument("--json", action="store_true")
    p_ps = proj_sub.add_parser("show", help="看一个项目的结构与知识库分布", parents=[sub_common])
    p_ps.add_argument("--json", action="store_true")

    # ---- idea（WF-01 灵感录入）----
    p_idea = sub.add_parser("idea", help="灵感录入：原文照录，不做取舍（WF-01）", parents=[sub_common])
    idea_sub = p_idea.add_subparsers(dest="idea_command", required=True)
    p_ic = idea_sub.add_parser("create", help="登记一条灵感（默认预演）", parents=[sub_common])
    p_ic.add_argument("--text", default="", help="灵感原文；与 --text-file 二选一")
    p_ic.add_argument("--text-file", default="", help="从文件读原文")
    p_ic.add_argument("--title", default="", help="短标题，决定文件名；省略取原文首行")
    p_ic.add_argument("--tag", action="append", default=[], help="初始标签，可重复")
    p_ic.add_argument("--source", action="append", default=[], help="来源，可重复")
    p_ic.add_argument("--note", default="", help="发号审计里的原因")
    p_ic.add_argument("--as", dest="actor", default=None)
    p_ic.add_argument("--confirm", action="store_true", help="确认写盘")
    p_ic.add_argument("--dry-run", action="store_true", help="强制预演（默认即预演）")
    p_ic.add_argument("--json", action="store_true")

    # ---- entity（V1.0 §5.2、WF-05）----
    p_ent = sub.add_parser("entity", help="实体建档、查看与契约校验（V1.0 §5.2）", parents=[sub_common])
    ent_sub = p_ent.add_subparsers(dest="entity_command", required=True)

    p_ec = ent_sub.add_parser("create", help="新建实体（默认预演）", parents=[sub_common])
    p_ec.add_argument("--name", required=True, help="实体名称")
    p_ec.add_argument("--type", required=True, help="类型，如 character/location/item/skill/rule/world")
    p_ec.add_argument("--subtype", default="", help="中文子类，如 主角／府／神器")
    p_ec.add_argument("--alias", action="append", default=[], help="别名，可重复")
    p_ec.add_argument("--summary", default="", help="一句话摘要")
    p_ec.add_argument("--tag", action="append", default=[], help="标签，可重复")
    p_ec.add_argument("--status", default="active", help="生命周期状态（默认 active）")
    p_ec.add_argument(
        "--canon-status",
        default="proposal",
        choices=["idea", "proposal", "pending_review", "canon", "deprecated", "rejected"],
        help="Canon 状态（V1.1 §4.2 六值，默认 proposal；Agent 不得自置 canon）",
    )
    p_ec.add_argument("--source", action="append", default=[], help="来源引用，可重复")
    p_ec.add_argument("--confidence", type=float, default=None, help="可信度 0..1（与 canon_status 不是一回事）")
    p_ec.add_argument("--prefix", default="ent", choices=["ent", "asset"], help="ID 前缀，素材用 asset")
    p_ec.add_argument("--allow-duplicate", action="store_true", help="允许同名（同名不同人／物时用）")
    p_ec.add_argument("--note", default="", help="发号审计里的原因")
    p_ec.add_argument("--as", dest="actor", default=None)
    p_ec.add_argument("--confirm", action="store_true", help="确认写盘")
    p_ec.add_argument("--dry-run", action="store_true")
    p_ec.add_argument("--json", action="store_true")

    p_es = ent_sub.add_parser("show", help="按 ID 看一份档案", parents=[sub_common])
    p_es.add_argument("id", help="实体 ID，如 ent_000086")
    p_es.add_argument("--json", action="store_true")

    p_ev = ent_sub.add_parser("validate", help="校验契约（单份或全库知识库）", parents=[sub_common])
    p_ev.add_argument("--id", default="", help="只校验这一份；省略则校验全部带 id 的档案")

    # ---- relation（V1.0 §5.3、WF-06）----
    p_rel = sub.add_parser("relation", help="登记关系（追加，不覆盖历史，V1.0 §5.3）", parents=[sub_common])
    rel_sub = p_rel.add_subparsers(dest="relation_command", required=True)
    p_rc = rel_sub.add_parser("create", help="登记一条关系（默认预演）", parents=[sub_common])
    p_rc.add_argument("--from", dest="from_id", required=True, help="起点实体 ID")
    p_rc.add_argument("--to", dest="to_id", required=True, help="终点实体 ID")
    p_rc.add_argument("--relation-type", required=True, help="关系类型，如 师徒／敌对／效忠")
    p_rc.add_argument("--valid-from", default="", help="生效时间（世界时间）")
    p_rc.add_argument("--valid-to", default="", help="失效时间；给了就把状态记成 ended")
    p_rc.add_argument("--summary", default="")
    p_rc.add_argument("--source", action="append", default=[])
    p_rc.add_argument("--confidence", type=float, default=None)
    p_rc.add_argument("--note", default="")
    p_rc.add_argument("--as", dest="actor", default=None)
    p_rc.add_argument("--confirm", action="store_true")
    p_rc.add_argument("--dry-run", action="store_true")

    # ---- event（V1.0 §5.10、WF-06）----
    p_evt = sub.add_parser("event", help="登记事件（时间不确定就写不确定，V1.0 §5.10）", parents=[sub_common])
    evt_sub = p_evt.add_subparsers(dest="event_command", required=True)
    p_etc = evt_sub.add_parser("create", help="登记一条事件（默认预演）", parents=[sub_common])
    p_etc.add_argument("--name", required=True)
    p_etc.add_argument("--summary", default="")
    p_etc.add_argument("--world-time", default="", help="世界时间；不知道就留空，别猜")
    p_etc.add_argument(
        "--precision",
        default="unknown",
        choices=["exact", "day", "month", "season", "year", "era", "unknown"],
        help="时间精度（默认 unknown：宁可承认不知道）。season 是「那年冬天」这一档，长篇里最常用",
    )
    p_etc.add_argument("--narrative", default="", help="叙事位置，如「第 3 章前」")
    p_etc.add_argument("--participant", action="append", default=[], help="参与者实体 ID，可重复")
    p_etc.add_argument("--location", default="", help="地点实体 ID")
    p_etc.add_argument("--source", action="append", default=[])
    p_etc.add_argument("--status", default="active")
    p_etc.add_argument(
        "--canon-status",
        default="proposal",
        choices=["idea", "proposal", "pending_review", "canon", "deprecated", "rejected"],
    )
    p_etc.add_argument(
        "--fact-status",
        default="unknown",
        choices=["confirmed", "claimed", "rumored", "planned", "unknown"],
        help="V1.0 §5.10 要求区分「作者确认／角色声称／传闻／计划」。默认 unknown，不替作者认账",
    )
    p_etc.add_argument("--confidence", type=float, default=None)
    p_etc.add_argument("--note", default="")
    p_etc.add_argument("--as", dest="actor", default=None)
    p_etc.add_argument("--confirm", action="store_true")
    p_etc.add_argument("--dry-run", action="store_true")

    # ---- continuity（V1.0 §11 时间／信息／伏笔、WF-17）----
    p_cont = sub.add_parser(
        "continuity", help="连续性检查：账面缺口，以及这次没查什么（WF-17）", parents=[sub_common]
    )
    cont_sub = p_cont.add_subparsers(dest="continuity_command", required=True)
    p_cc = cont_sub.add_parser("check", help="跑一遍连续性检查", parents=[sub_common])
    p_cc.add_argument(
        "--scope",
        default="all",
        choices=["all", "chapter", "volume"],
        help="检查范围；chapter/volume 目前只影响「未查项」的说明，不影响已实现的三项",
    )
    p_cc.add_argument("--json", action="store_true")

    return parser


def _abs(raw: str | None) -> str | None:
    """命令行给的相对路径按 cwd 解析。

    配置文件里的相对路径仍然要求显式基准（那里相对谁说不清，宁可报错），
    但人在终端敲 `novel --vault vault status` 时，cwd 就是明确基准。
    """
    if not raw:
        return None
    return str(Path(raw).expanduser().resolve())


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        cfg = cfg_mod.load(
            vault=_abs(args.vault),
            runtime=_abs(args.runtime),
            project=args.project,
        )
    except cfg_mod.ConfigError as exc:
        print(f"配置错误：{exc}")
        return 2

    # 日志在这里装配：此前的失败（参数、配置）还没 Runtime，写不进文件。
    # 写不进也照跑——日志是旁路，不许反过来让命令失败。
    logs.setup_logging(
        cfg.runtime,
        getattr(args, "log_level", "INFO"),
        console=os.environ.get("NOVEL_LOG_CONSOLE") == "1",
    )
    log = logs.get_logger("cli")

    handler = HANDLERS.get(args.command)
    if handler is None:
        print(f"未知命令：{args.command}")
        return 2

    log.info(
        "start cmd=%s project=%s vault=%s argv=%d",
        args.command,
        args.project or "-",
        cfg.author_vault or "-",
        len(argv) if argv is not None else max(len(sys.argv) - 1, 0),
    )
    started = time.monotonic()
    rc = 0
    try:
        rc = handler(cfg, args)
    except NovelError as exc:
        log.warning("%s: %s", type(exc).__name__, exc)
        print(str(exc))
        rc = exc.code
    except cfg_mod.ConfigError as exc:
        log.warning("ConfigError: %s", exc)
        print(f"配置错误：{exc}")
        rc = 2
    except KeyboardInterrupt:
        log.info("interrupted cmd=%s", args.command)
        print("\n已中断")
        rc = 130
    except Exception:
        # 未预期异常原样抛出，但先在日志里留全栈——终端只给作者看消息，栈进日志。
        log.exception("crash cmd=%s", args.command)
        raise
    log.info(
        "done cmd=%s rc=%s elapsed=%.3fs", args.command, rc, time.monotonic() - started
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
