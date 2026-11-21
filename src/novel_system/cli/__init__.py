"""CLI 入口。V4.0 §5.3 的命令集，按 T01–T05 逐步落地。

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
from ..errors import NovelError
from . import (
    approval_cmd,
    backup_cmd,
    change_cmd,
    flow,
    id_cmd,
    index_cmd,
    init,
    maps_cmd,
    read,
    schema_cmd,
    timeline_cmd,
)

__version__ = "0.8.0"  # 包版本，与 pyproject.toml 同步；V4.0 是规范版本号，两者不是一回事

HANDLERS = {
    "doctor": read.cmd_doctor,
    "status": read.cmd_status,
    "validate": read.cmd_validate,
    "diff": read.cmd_diff,
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
    "backup": backup_cmd.cmd_backup,
    "restore": backup_cmd.cmd_restore,
    "maps": maps_cmd.cmd_maps,
    "schema": schema_cmd.cmd_schema,
    "allocate-id": id_cmd.cmd_allocate_id,
    "package-hash": schema_cmd.cmd_package_hash,
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
        description="长篇小说管理系统 CLI（V4.0 §5.3 命令集）",
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
    p_tc.add_argument("--type", required=True, help="任务类型（outline/preflight/draft/revision/review/memory_update/ingest/simulation/release）")
    p_tc.add_argument("--assignee", default="", help="责任 Agent")
    p_tc.add_argument("--mode", default="staging", help="read_only / staging / draft / proposal")
    p_tc.add_argument("--scope", default="", help="允许写入的目录前缀（项目内相对路径）")
    p_tc.add_argument("--input", action="append", default=[], help="输入文件，可重复或逗号分隔")
    p_tc.add_argument("--note", default="")
    p_tc.add_argument("--as", dest="actor", default=None, help="操作者：author 或 agent 名")

    p_ti = task_sub.add_parser("inspect", help="查看任务详情", parents=[sub_common])
    p_ti.add_argument("task_id", help="形如 TASK-000001")
    p_ti.add_argument("--json", action="store_true", help="附带 JSON 输出")

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
        choices=["artifact", "proposal"],
        help="按对象批准：artifact（本版可用）／proposal（T07 才有）。省略则走 --task",
    )
    p_approve.add_argument("candidate", nargs="?", help="候选 ID：TASK-000001 或 TASK-000001:<目标路径>")
    p_approve.add_argument("--hash", dest="hash", default="", help="当前内容哈希（非交互批准必填）")
    p_approve.add_argument("--task", default=None, help="任务 ID（旧用法：一次批准该任务全部 staging）")
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
    p_inspect.add_argument("candidate", help="TASK-000001 或 TASK-000001:<目标相对路径>")
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
    p_pub.add_argument("--as", dest="actor", default=None, help="只接受 author")

    # ---- backup / restore ----
    p_backup = sub.add_parser("backup", help="创建本地备份（含清单与回读校验）", parents=[sub_common])
    p_backup.add_argument("--out", default=None, help="备份目录，默认 <runtime>/backups")
    p_backup.add_argument("--label", default="", help="备注标签")
    p_backup.add_argument("--no-git", action="store_true", help="不备份 .git")
    p_backup.add_argument("--list", action="store_true", help="只列出现有备份")
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
    p_alloc.add_argument("--type", required=True, help="CHARACTER／LOCATION／EVENT……或直接给前缀 CHAR")
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
