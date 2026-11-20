"""CLI 入口。V4.0 §5.3 的 16 条命令，加 3 条地图生成命令。

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
from . import backup_cmd, flow, init, maps_cmd, read

__version__ = "0.4.2"  # 包版本，与 pyproject.toml 同步；V4.0 是规范版本号，两者不是一回事

HANDLERS = {
    "doctor": read.cmd_doctor,
    "status": read.cmd_status,
    "validate": read.cmd_validate,
    "diff": read.cmd_diff,
    "init-author": init.cmd_init_author,
    "init-project": init.cmd_init_project,
    "rebuild-index": init.cmd_rebuild_index,
    "task": flow.cmd_task,
    "stage": flow.cmd_stage,
    "approve": flow.cmd_approve,
    "adopt": flow.cmd_adopt,
    "publish": flow.cmd_publish,
    "backup": backup_cmd.cmd_backup,
    "restore": backup_cmd.cmd_restore,
    "maps": maps_cmd.cmd_maps,
}


def build_parser() -> argparse.ArgumentParser:
    # 全局参数同时挂在主命令与子命令上，`novel --vault X status` 与
    # `novel status --vault X` 都成立——位置不同不该让命令失效。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vault", help="作者 Vault 路径（覆盖环境变量与配置文件）")
    common.add_argument("--runtime", help="Runtime 路径（必须在 Vault 之外）")
    common.add_argument("--project", help="项目 ID（Vault 内多于一个项目时必需）")
    common.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "OFF"],
        help="写入 <runtime>/logs/novel.log 的级别；OFF 关闭",
    )

    parser = argparse.ArgumentParser(
        prog="novel",
        description="长篇小说管理系统 CLI（V4.0 §5.3 命令集）",
        parents=[common],
    )
    parser.add_argument("--version", action="version", version=f"novel {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="检查配置、依赖、路径、隔离与隐私开关", parents=[common])
    sub.add_parser("status", help="查看 Vault、项目、任务、备份与最近操作", parents=[common])

    p_validate = sub.add_parser("validate", help="校验结构、实体 ID、双链与项目边界", parents=[common])
    p_validate.add_argument("--strict", action="store_true", help="把断链等警告计为失败")

    p_author = sub.add_parser("init-author", help="初始化作者 Vault", parents=[common])
    p_author.add_argument("--author-id", default="author-local")
    p_author.add_argument("--confirm", action="store_true", help="确认写入")

    p_proj = sub.add_parser("init-project", help="创建小说项目骨架", parents=[common])
    p_proj.add_argument("--id", help="项目 ID（小写字母、数字、连字符）")
    p_proj.add_argument("--name", help="项目显示名")
    p_proj.add_argument("--author-id", default="author-local")
    p_proj.add_argument("--confirm", action="store_true", help="确认写入")

    sub.add_parser("rebuild-index", help="从 Markdown 重建索引到 Runtime", parents=[common])

    # ---- task ----
    p_task = sub.add_parser("task", help="创建或查看任务记录", parents=[common])
    task_sub = p_task.add_subparsers(dest="task_command", required=True)

    p_tc = task_sub.add_parser("create", help="创建任务", parents=[common])
    p_tc.add_argument("--type", required=True, help="任务类型（outline/preflight/draft/revision/review/memory_update/ingest/simulation/release）")
    p_tc.add_argument("--assignee", default="", help="责任 Agent")
    p_tc.add_argument("--mode", default="staging", help="read_only / staging / draft / proposal")
    p_tc.add_argument("--scope", default="", help="允许写入的目录前缀（项目内相对路径）")
    p_tc.add_argument("--input", action="append", default=[], help="输入文件，可重复或逗号分隔")
    p_tc.add_argument("--note", default="")
    p_tc.add_argument("--as", dest="actor", default=None, help="操作者：author 或 agent 名")

    p_ti = task_sub.add_parser("inspect", help="查看任务详情", parents=[common])
    p_ti.add_argument("task_id", help="形如 TASK-000001")
    p_ti.add_argument("--json", action="store_true", help="附带 JSON 输出")

    # ---- stage ----
    p_stage = sub.add_parser("stage", help="把 Agent 产出写入 staging（唯一允许的提案落点）", parents=[common])
    p_stage.add_argument("--task", required=True, help="任务 ID")
    p_stage.add_argument("--from", dest="source", required=True, help="源文件；`-` 表示从 stdin 读")
    p_stage.add_argument("--to", dest="target", required=True, help="目标正式文件的项目内相对路径")
    p_stage.add_argument("--note", default="")
    p_stage.add_argument("--replace", action="store_true", help="覆盖本任务已存在的同名产物")
    p_stage.add_argument("--as", dest="actor", default=None, help="操作者：author 或 agent 名")

    # ---- diff ----
    p_diff = sub.add_parser("diff", help="查看 staging 与正式文件的差异", parents=[common])
    p_diff.add_argument("--task", required=True, help="任务 ID")
    p_diff.add_argument("--path", default=None, help="只看某个目标文件")
    p_diff.add_argument("--full", action="store_true", help="不截断差异行")

    # ---- approve ----
    p_approve = sub.add_parser("approve", help="作者批准 staging 内容（绑定内容指纹）", parents=[common])
    p_approve.add_argument("--task", required=True, help="任务 ID")
    p_approve.add_argument("--note", default="")
    p_approve.add_argument("--reject", action="store_true", help="驳回而不是批准")
    p_approve.add_argument("--force", action="store_true", help="有冲突仍批准（承担覆盖作者手改的风险）")
    p_approve.add_argument("--as", dest="actor", default=None, help="只接受 author")

    # ---- adopt ----
    p_adopt = sub.add_parser("adopt", help="登记作者直接编辑后的文件状态", parents=[common])
    p_adopt.add_argument("--note", default="")
    p_adopt.add_argument("--dry-run", action="store_true", help="只报告变化，不推进基线")
    p_adopt.add_argument("--as", dest="actor", default=None, help="只接受 author")

    # ---- publish ----
    p_pub = sub.add_parser("publish", help="把已批准内容转为正式状态", parents=[common])
    p_pub.add_argument("--task", required=True, help="任务 ID")
    p_pub.add_argument("--note", default="")
    p_pub.add_argument("--confirm", action="store_true", help="确认发布")
    p_pub.add_argument("--confirm-canon", action="store_true", help="写入 01_Canon 时的二次确认")
    p_pub.add_argument("--as", dest="actor", default=None, help="只接受 author")

    # ---- backup / restore ----
    p_backup = sub.add_parser("backup", help="创建本地备份（含清单与回读校验）", parents=[common])
    p_backup.add_argument("--out", default=None, help="备份目录，默认 <runtime>/backups")
    p_backup.add_argument("--label", default="", help="备注标签")
    p_backup.add_argument("--no-git", action="store_true", help="不备份 .git")
    p_backup.add_argument("--list", action="store_true", help="只列出现有备份")
    p_backup.add_argument("--as", dest="actor", default=None, help="操作者：author 或 agent 名")

    p_restore = sub.add_parser("restore", help="从备份恢复（默认预演，不覆盖现存文件）", parents=[common])
    p_restore.add_argument("--backup-id", required=True, help="形如 BK-20261120-101500")
    p_restore.add_argument("--dry-run", action="store_true", help="只预演，不写盘")
    p_restore.add_argument("--confirm", action="store_true", help="执行恢复")
    p_restore.add_argument("--overwrite", action="store_true", help="覆盖冲突文件（覆盖前另存一份）")
    p_restore.add_argument("--out", default=None, help="备份目录，默认 <runtime>/backups")
    p_restore.add_argument("--as", dest="actor", default=None, help="只接受 author")

    # ---- maps ----
    p_maps = sub.add_parser("maps", help="从 Canon 数据重画地图页与 canvas（默认预演）", parents=[common])
    maps_sub = p_maps.add_subparsers(dest="maps_command", required=True)

    p_mpaths = maps_sub.add_parser("paths", help="打印地图生成用到的全部路径", parents=[common])
    p_mpaths.add_argument("--world-subdir", default=None)

    p_madm = maps_sub.add_parser(
        "import-admin", help="把资产库「地图库」重排为 WORLD-0040 行政区划表", parents=[common]
    )
    p_madm.add_argument("--world-subdir", default=None)
    p_madm.add_argument("--xlsx", default=None, help="资产表路径，默认项目内 90_Archive 下那份")
    p_madm.add_argument("--confirm", action="store_true", help="执行写入")

    p_mbuild = maps_sub.add_parser("build", help="重画地图页与两张 canvas", parents=[common])
    p_mbuild.add_argument("--world-subdir", default=None)
    p_mbuild.add_argument("--xlsx", default=None, help="资产表路径（build 不读，仅为参数一致）")
    p_mbuild.add_argument("--maps-only", action="store_true", help="只重画图，不碰州域／府／城邑档案")
    p_mbuild.add_argument("--confirm", action="store_true", help="执行写入")

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
