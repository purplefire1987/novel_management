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
from ..errors import NovelError, SecurityError, UsageError
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


# 文件名里不能出现的字符。**不把中文排除在外**：库里的档案是
# `ent_000026_安怀舟.md`，中文名字直接进文件名是既定做法（可读性优先），
# 用 `slugify` 那种「只留 ASCII」会把「测试实体」压成 `project`——
# 一个文件名叫 `ent_000967_project.md` 的档案，半年后没人认得出是谁。
_FILENAME_BAD = re.compile(r'[\\/:*?"<>|\s]+')


def name_slug(name: str, fallback: str = "未命名") -> str:
    """把作品内名称变成可用的文件名字段：保留中文，只换掉真不能用的字符。"""
    s = _FILENAME_BAD.sub("-", (name or "").strip())
    s = re.sub(r"-{2,}", "-", s).strip("-._")
    return (s[:40] or fallback)


def read_frontmatter(path: Path) -> dict:
    """转发到 frontmatter 模块——解析只有那一份实现。

    **只解顶层 `key: value`**。要读 `aliases:` / `source_refs:` 这种嵌套块，
    用 `read_meta()`；用这个读到的是空字符串，那种「字段明明有、读出来是空」
    的假象会让校验器报一堆不存在的错。
    """
    return fm.read(path)


def read_meta(path: Path) -> dict:
    """完整 YAML frontmatter（含嵌套块），给需要判结构的命令用。

    和 `read_frontmatter` 的分工：那个是「快速看一眼 id 和 name」（索引、查重
    用得上，几千份要快），这个是「这一份合不合契约」（必须看到 aliases 到底是
    列表还是字符串）。两者混用会出两种错：拿快的那个去判结构 = 满屏假失败；
    拿全的那个去扫全库 = 慢到没人愿意跑。
    """
    from .. import markdown as md

    try:
        return md.parse_bytes(path.read_bytes()).meta
    except (NovelError, OSError, UnicodeDecodeError):
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
