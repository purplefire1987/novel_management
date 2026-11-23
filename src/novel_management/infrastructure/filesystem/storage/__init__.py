"""文件层：哈希、原子写、不覆盖写、可验证复制、最小 YAML 输出。

只做机制，不做策略。策略在命令层。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Iterable, Iterator

from ....domain.errors import ConflictError

CHUNK = 1 << 20

# 备份与遍历时跳过的目录：缓存与可再生内容，不属于事实源。
SKIP_DIR_NAMES = {
    ".obsidian",
    ".trash",
    ".git-rewrite",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ipynb_checkpoints",
    ".tmp",
}

SKIP_FILE_SUFFIXES = {".tmp", ".swp", ".pyc", ".pyo", ".novel-tmp"}


# --------------------------------------------------------------------------
# 哈希
# --------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


# --------------------------------------------------------------------------
# 写
# --------------------------------------------------------------------------

# 有环境禁止 rename（安全软件、只读挂载、某些容器）。探测一次就记住，
# 之后同目录直接写——避免每个文件都留一个删不掉的临时文件。
_NO_RENAME_DIRS: set[str] = set()


def write_atomic(path: Path, data: bytes) -> None:
    """同目录临时文件 + 原子替换。

    rename 不可用时降级为直接写入：内容仍完整，只失去原子性。
    这类降级按目录记忆一次，不会反复制造临时文件。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (path.stat().st_mode & 0o777) if path.exists() else 0o644
    dir_key = str(path.parent)

    if dir_key in _NO_RENAME_DIRS:
        path.write_bytes(data)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
        return

    tmp = path.parent / f".{path.name}.novel-tmp"
    try:
        with tmp.open("wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        try:
            os.replace(tmp, path)
            return
        except OSError:
            _NO_RENAME_DIRS.add(dir_key)
            path.write_bytes(data)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def write_new(path: Path, data: bytes) -> None:
    """目标不存在才写。存在即冲突，绝不静默覆盖。"""
    if path.exists():
        raise ConflictError(f"目标已存在，拒绝覆盖：{path}")
    write_atomic(path, data)


def write_text_atomic(path: Path, text: str) -> None:
    write_atomic(path, text.encode("utf-8"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------
# 遍历与复制
# --------------------------------------------------------------------------

def iter_files(
    root: Path,
    skip_dirs: Iterable[str] | None = None,
    skip_hidden: bool = True,
    include_git: bool = False,
) -> Iterator[Path]:
    """稳定顺序遍历文件。默认跳过隐藏目录与缓存目录。"""
    skip = set(skip_dirs or ()) | SKIP_DIR_NAMES
    if include_git:
        skip.discard(".git")
    else:
        skip.add(".git")
    root = root.resolve()
    for current, dirnames, filenames in os.walk(root):
        current_path = Path(current)
        kept = []
        for name in sorted(dirnames):
            if name in skip:
                continue
            if skip_hidden and name.startswith(".") and name != ".git":
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            if name in SKIP_FILE_SUFFIXES:
                continue
            if skip_hidden and name.startswith("."):
                continue
            yield current_path / name


def copy_verified(src: Path, dst: Path) -> tuple[str, int]:
    """复制并回读校验。返回 (sha256, 字节数)。哈希不一致直接抛错。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    want = sha256_file(src)
    got = sha256_file(dst)
    if want != got:
        raise ConflictError(f"复制校验失败：{src} → {dst}（{want} != {got}）")
    return got, dst.stat().st_size


def tree_size(root: Path) -> tuple[int, int]:
    count = 0
    total = 0
    for path in iter_files(root, skip_hidden=False):
        count += 1
        total += path.stat().st_size
    return count, total


def human_size(num: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{num}B"


# --------------------------------------------------------------------------
# 最小 YAML 输出：只覆盖记录类数据（标量、列表、一层对象列表）
# --------------------------------------------------------------------------

def _yaml_scalar(value: object) -> str:
    if value is None:
        # 空值写 `null` 而不是 `""`：YAML 读回来是 None，与写进去的一致。
        # 写成空字符串会让「没有值」和「值就是空串」混成一件事，
        # 而这两件事在 `base_release` 这种字段上含义完全不同。
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    return json.dumps(text, ensure_ascii=False)


def dump_yaml(data: dict, indent: int = 0) -> str:
    """把记录类数据写成 YAML。

    递归到任意深度。早先只支持「标量、列表、一层对象列表」，够任务记录用；
    T08 的资料包有三层（列表里套对象，对象里再套列表，比如
    `issues[].evidence[]`），一层就写坏了——`evidence: "[]"` 看着像空表，
    读回来却是个字符串。宁可把 dumper 写全，也不要让派生文件静默变成错的数据。
    """
    return "\n".join(_dump_map(data, indent)) + "\n"


def _dump_map(data: dict, indent: int) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    for key, value in data.items():
        head = f"{pad}{key}:"
        if isinstance(value, dict):
            if not value:
                lines.append(f"{head} {{}}")
                continue
            lines.append(head)
            lines.extend(_dump_map(value, indent + 2))
        elif isinstance(value, (list, tuple)):
            if not value:
                lines.append(f"{head} []")
                continue
            lines.append(head)
            lines.extend(_dump_seq(value, indent + 2))
        else:
            lines.append(f"{head} {_yaml_scalar(value)}")
    return lines


def _dump_seq(items, indent: int) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            if not item:
                lines.append(f"{pad}- {{}}")
                continue
            body = _dump_map(item, indent + 2)
            # 第一行挂到 `- ` 后面，其余行保持缩进——这是 YAML 的块序列写法。
            lines.append(f"{pad}- {body[0].lstrip()}")
            lines.extend(body[1:])
        elif isinstance(item, (list, tuple)):
            if not item:
                lines.append(f"{pad}- []")
                continue
            lines.append(f"{pad}-")
            lines.extend(_dump_seq(item, indent + 2))
        else:
            lines.append(f"{pad}- {_yaml_scalar(item)}")
    return lines
