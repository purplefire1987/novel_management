"""命令共享件：输出、上下文装配、前端简单解析。"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from .. import config as cfg_mod
from .. import frontmatter as fm
from ..approvals import ApprovalStore
from ..audit import OperationLog
from ..backup import BackupStore
from ..baseline import AuthorBaseline
from ..errors import SecurityError, UsageError
from ..paths import ProjectPaths
from ..tasks import TaskStore


def ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def info(msg: str) -> None:
    print(f"  {msg}")


def heading(msg: str) -> None:
    print(f"\n{msg}")


def slugify(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_-]+", "-", name.strip().lower()).strip("-")
    return s or "project"


def read_frontmatter(path: Path) -> dict:
    """转发到 frontmatter 模块——解析只有那一份实现。"""
    return fm.read(path)


def project(cfg: cfg_mod.Config, args: argparse.Namespace) -> ProjectPaths:
    project_id = getattr(args, "project", None) or cfg.project_id or None
    return ProjectPaths.resolve(cfg, project_id)


def need_runtime(cfg: cfg_mod.Config) -> Path:
    return cfg.require_runtime()


def task_store(cfg: cfg_mod.Config) -> TaskStore:
    return TaskStore(need_runtime(cfg))


def approval_store(cfg: cfg_mod.Config) -> ApprovalStore:
    return ApprovalStore(need_runtime(cfg))


def baseline(cfg: cfg_mod.Config, pp: ProjectPaths) -> AuthorBaseline:
    return AuthorBaseline(need_runtime(cfg), pp.project_id)


def oplog(cfg: cfg_mod.Config) -> OperationLog:
    return OperationLog(cfg.runtime)


def backup_store(cfg: cfg_mod.Config, out: str | None) -> BackupStore:
    out_dir = cfg_mod.resolve_path(out) if out else None
    if out_dir is None and cfg.runtime is None:
        raise UsageError("未配置 Runtime，请用 --out 指定备份目录")
    return BackupStore(cfg.runtime, out_dir)


def require_write(args: argparse.Namespace, flag: str = "confirm", hint: str = "") -> None:
    if not getattr(args, flag, False):
        raise UsageError(hint or f"该命令会写盘，确认请加 --{flag}")


def ensure_inside_vault(path: Path, vault: Path, what: str) -> Path:
    try:
        return cfg_mod.ensure_inside(path, vault)
    except cfg_mod.ConfigError as exc:
        raise SecurityError(f"{what} 越界：{exc}") from exc
