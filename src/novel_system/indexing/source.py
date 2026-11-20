"""索引的内容来源：工作树，或某个 Git revision。

为什么要两种源：PRD §16.4 要求「输入明确的 00_Canon revision」，验收也要求
「指定 commit 可独立构建索引」。只读工作树做不到——工作树里混着未提交的改动，
建出来的索引说不清对应哪个版本。

git 源直接从 `git archive` 的 tar 流里读，**不落临时目录**：
解开再读要多一遍磁盘往返，还要负责清理，而 tar 里的路径本来就已经是
Vault 相对路径，正好是构建器需要的形态。
"""

from __future__ import annotations

import io
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Protocol

from ..errors import UsageError

# 项目内不索引的三个目录：Agent 提案区、审阅意见、已废弃旧版。
# 它们都不是「当前事实」，进索引会让 `search` 返回一堆不该被当作依据的内容。
_PROJECT_SKIP_DIRS = ("07_Staging", "08_Reviews", "90_Archive")

# Vault 根归档目录名。**这里是兜底默认值，真实调用方应从配置取**（见 skip_dirs_from_layout）。
# 教训：早期硬编码成 `90_Archive`，而 V4.0 的 Vault 归档叫 `99_Archive`，
# 于是整个归档（旧 PRD 2611 行、旧实体索引 75KB）被静默索引进库——
# 「跳过归档」这条注释看起来在生效，实际一份没跳过。
DEFAULT_ARCHIVE_DIR = "99_Archive"

MARKDOWN_SUFFIXES = (".md", ".markdown")


def skip_dirs_from_layout(layout: dict) -> tuple[str, ...]:
    """从 Vault 配置推「哪些顶层目录不索引」。

    目录名不许写死在代码里（`code_layout.md` 三条约定之一）。配置里归档叫
    `99_Archive` 还是 `90_Archive`、以后改成什么，都由配置说了算。
    """
    names = set(_PROJECT_SKIP_DIRS)
    archive = str(layout.get("vault_archive_dir") or "").strip()
    names.add(archive or DEFAULT_ARCHIVE_DIR)
    return tuple(sorted(names))


@dataclass(frozen=True)
class SourceFile:
    """一份待索引的文件。`rel` 是 Vault 相对路径（POSIX 风格）。"""

    rel: str
    data: bytes


class ContentSource(Protocol):
    kind: str
    revision: str

    def files(self) -> Iterator[SourceFile]: ...


def _skip(rel: str, skip_dirs: Iterable[str]) -> bool:
    parts = rel.split("/")
    if any(part.startswith(".") for part in parts):
        return True
    return any(part in skip_dirs for part in parts)


def _git(vault: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(vault), *args],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise UsageError("找不到 git 命令；按 revision 构建索引需要 Git CLI") from exc


def _own_repo(vault: Path) -> bool:
    """Vault 自己是不是一个 Git 仓库的**根**。

    只看 `rev-parse HEAD` 是不够的：Vault 若被摆在别的仓库里（测试临时目录、
    示例库、作者的草稿目录），`git -C` 会一路向上找到外层仓库，把外层仓库的
    HEAD 当成 Vault 的 revision。索引于是绑定了一个跟内容毫无关系的提交，
    而且看起来完全正常——这种错最难查。
    """
    top = _git(vault, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return False
    root = top.stdout.decode("utf-8", "replace").strip()
    return bool(root) and Path(root).resolve() == Path(vault).resolve()


def resolve_revision(vault: Path, revision: str) -> str:
    """把 `HEAD` / 短 sha / 分支名解析成完整提交 sha。解析不出就报错。"""
    if not _own_repo(vault):
        raise UsageError(
            f"Vault 不是独立的 Git 仓库根，无法按 revision 建索引：{vault}"
        )
    proc = _git(vault, "rev-parse", "--verify", f"{revision}^{{commit}}")
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip() or "未知错误"
        raise UsageError(f"不是有效的 Git 提交：{revision}（{detail}）")
    return proc.stdout.decode("utf-8").strip()


def head_revision(vault: Path) -> str:
    """Vault 当前对应的 commit；不是独立 Git 仓库或还没有提交时返回空串。"""
    if not _own_repo(vault):
        return ""
    proc = _git(vault, "rev-parse", "--verify", "HEAD^{commit}")
    if proc.returncode != 0:
        return ""
    return proc.stdout.decode("utf-8").strip()


def is_dirty(vault: Path) -> bool:
    if not _own_repo(vault):
        return False
    proc = _git(vault, "status", "--porcelain")
    if proc.returncode != 0:
        return False
    return bool(proc.stdout.strip())


class WorktreeSource:
    """读磁盘上的 Markdown。"""

    kind = "worktree"

    def __init__(
        self, vault: Path, revision: str = "", skip_dirs: Iterable[str] | None = None
    ) -> None:
        self.vault = vault
        self.revision = revision
        self.skip_dirs = tuple(skip_dirs) if skip_dirs is not None else (
            *_PROJECT_SKIP_DIRS,
            DEFAULT_ARCHIVE_DIR,
        )
        self.skipped = 0

    def files(self) -> Iterator[SourceFile]:
        for path in sorted(self.vault.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in MARKDOWN_SUFFIXES:
                continue
            rel = path.relative_to(self.vault).as_posix()
            if _skip(rel, self.skip_dirs):
                self.skipped += 1
                continue
            yield SourceFile(rel=rel, data=path.read_bytes())


class GitRevisionSource:
    """读某个 commit 里的 Markdown。不碰工作树，也不写任何临时目录。"""

    kind = "git"

    def __init__(
        self, vault: Path, revision: str, skip_dirs: Iterable[str] | None = None
    ) -> None:
        self.vault = vault
        self.commit = resolve_revision(vault, revision)
        self.revision = self.commit
        self.skip_dirs = tuple(skip_dirs) if skip_dirs is not None else (
            *_PROJECT_SKIP_DIRS,
            DEFAULT_ARCHIVE_DIR,
        )
        self.skipped = 0

    def files(self) -> Iterator[SourceFile]:
        proc = _git(self.vault, "archive", "--format=tar", self.commit)
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", "replace").strip() or "未知错误"
            raise UsageError(f"git archive 失败：{self.commit}（{detail}）")

        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                if not member.name.lower().endswith(MARKDOWN_SUFFIXES):
                    continue
                if _skip(member.name, self.skip_dirs):
                    self.skipped += 1
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                yield SourceFile(rel=member.name, data=handle.read())
