"""Git 版本控制辅助：历史查看、快照标签、仓库状态。

设计取向：
- 只做「读 + 打标签」，正式提交仍由 transaction 统一完成，避免两条写入路径。
- 所有命令强制校验 Vault 自身就是仓库根（git_guard），防止误操作父仓库。
"""

from __future__ import annotations

import re
import subprocess
from typing import Any

from .config import Config
from .transaction import git_guard

TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,80}$")


class GitError(RuntimeError):
    """Vault 不是 Git 仓库，或 Git 命令执行失败。"""


def _run(cfg: Config, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cfg.vault), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def ensure_repo(cfg: Config) -> None:
    ok, reason = git_guard(cfg.vault)
    if not ok:
        raise GitError(f"{reason}（{cfg.vault}）")


def repo_state(cfg: Config) -> dict[str, Any]:
    """分支、HEAD、脏工作区数量、与上游的差距。"""
    ensure_repo(cfg)
    branch = _run(cfg, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    head = _run(cfg, ["rev-parse", "--short", "HEAD"]).stdout.strip()
    dirty = [line for line in _run(cfg, ["status", "--porcelain"]).stdout.splitlines() if line.strip()]
    ahead = behind = 0
    upstream = _run(cfg, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    has_upstream = upstream.returncode == 0
    if has_upstream:
        counts = _run(cfg, ["rev-list", "--left-right", "--count", "@{u}...HEAD"])
        if counts.returncode == 0 and counts.stdout.strip():
            parts = counts.stdout.split()
            if len(parts) == 2:
                behind, ahead = int(parts[0]), int(parts[1])
    return {
        "branch": branch,
        "head": head,
        "dirty": len(dirty),
        "dirty_files": dirty[:10],
        "upstream": upstream.stdout.strip() if has_upstream else "",
        "ahead": ahead,
        "behind": behind,
    }


def history(cfg: Config, limit: int = 15, path: str | None = None) -> list[dict[str, Any]]:
    """最近提交：哈希 / 时间 / 作者 / 标题。"""
    ensure_repo(cfg)
    args = [
        "log",
        f"-{max(1, limit)}",
        "--pretty=format:%h\x1f%ad\x1f%an\x1f%s",
        "--date=format:%Y-%m-%d %H:%M",
    ]
    if path:
        args += ["--", path]
    proc = _run(cfg, args)
    if proc.returncode != 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        rows.append(
            {
                "hash": parts[0],
                "time": parts[1],
                "author": parts[2],
                "subject": parts[3],
            }
        )
    return rows


def list_tags(cfg: Config, pattern: str = "") -> list[dict[str, str]]:
    ensure_repo(cfg)
    args = ["tag", "--sort=-creatordate", "--format=%(refname:short)\x1f%(creatordate:short)\x1f%(subject)"]
    if pattern:
        args.append(f"{pattern}*")
    proc = _run(cfg, args)
    if proc.returncode != 0:
        return []
    out: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f")
        if not parts or not parts[0]:
            continue
        out.append(
            {
                "tag": parts[0],
                "date": parts[1] if len(parts) > 1 else "",
                "subject": parts[2] if len(parts) > 2 else "",
            }
        )
    return out


def create_tag(cfg: Config, tag: str, message: str = "") -> dict[str, Any]:
    """给当前 HEAD 打一个附注标签（批次快照）。已存在则报错。"""
    ensure_repo(cfg)
    name = tag.strip()
    if not TAG_RE.match(name):
        raise GitError(f"标签名非法：{name}（只允许字母数字与 . _ - /）")
    exists = _run(cfg, ["rev-parse", "--verify", "--quiet", f"refs/tags/{name}"])
    if exists.returncode == 0:
        raise GitError(f"标签已存在：{name}")
    proc = _run(
        cfg,
        [
            "-c",
            "user.name=novel-system",
            "-c",
            "user.email=novel-system@local",
            "tag",
            "-a",
            name,
            "-m",
            message or f"snapshot {name}",
        ],
        timeout=30,
    )
    if proc.returncode != 0:
        raise GitError(f"打标签失败：{(proc.stdout + proc.stderr).strip()[:300]}")
    head = _run(cfg, ["rev-parse", "--short", "HEAD"]).stdout.strip()
    return {"tag": name, "head": head, "message": message or f"snapshot {name}"}


def files_in_commit(cfg: Config, rev: str = "HEAD") -> list[str]:
    ensure_repo(cfg)
    proc = _run(cfg, ["show", "--pretty=format:", "--name-only", rev])
    return [line for line in proc.stdout.splitlines() if line.strip()]
