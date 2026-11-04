"""Vault 文件层：frontmatter 读写、哈希、原子替换、备份。

原则：Markdown/YAML 是唯一事实源；本模块是唯一的「碰盘」入口。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import yaml

FM_RE_BYTES = b"---"


class VaultError(RuntimeError):
    pass


@dataclass
class Doc:
    """一份 Markdown 文档：frontmatter + 正文。"""

    path: Path
    meta: dict[str, Any]
    body: str
    raw: str

    @property
    def id(self) -> str | None:
        return self.meta.get("id")

    @property
    def type(self) -> str | None:
        return self.meta.get("type")

    def section(self, heading: str) -> str:
        """取 `## heading` 到下一个同级标题之间的内容。"""
        return extract_section(self.body, heading)


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() in ("---", "..."):
            end = idx
            break
    if end is None:
        return {}, text
    block = "".join(lines[1:end])
    try:
        meta = yaml.safe_load(block) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - 报错信息更友好
        raise VaultError(f"frontmatter YAML 解析失败：{exc}") from exc
    if not isinstance(meta, dict):
        meta = {}
    return meta, "".join(lines[end + 1 :])


def render_doc(meta: dict[str, Any], body: str) -> str:
    yaml_text = yaml.safe_dump(
        meta, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).strip()
    body = body.lstrip("\n")
    return f"---\n{yaml_text}\n---\n\n{body}"


def read_doc(path: Path) -> Doc:
    raw = path.read_text(encoding="utf-8")
    meta, body = split_frontmatter(raw)
    return Doc(path=path, meta=meta, body=body, raw=raw)


def read_optional(path: Path) -> Doc | None:
    return read_doc(path) if path.exists() else None


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return sha256_text(path.read_text(encoding="utf-8"))


def _log_atomic_fallback(path: Path) -> None:
    """把「降级直写」记进 `.novel_system/logs/atomic_fallback.log`，便于事后排查。"""
    for parent in path.parents:
        logs = parent / ".novel_system" / "logs"
        if logs.parent.is_dir():
            try:
                logs.mkdir(parents=True, exist_ok=True)
                with (logs / "atomic_fallback.log").open("a", encoding="utf-8") as fh:
                    fh.write(f"{datetime.now().isoformat(timespec='seconds')} {path}\n")
            except OSError:
                pass
            return


def write_atomic(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace，避免半截文件。

    受限环境（容器/沙箱/某些挂载）会禁止 rename，此时退化为直接写入并记一条
    `logs/atomic_fallback.log`：宁可少一层原子性，也不要整套工作流不可用。
    清理临时文件失败同样不能掩盖原始错误——否则会留下 `.tmp_*` 半成品。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.replace(tmp, path)
        except PermissionError:
            # rename 被环境拒绝：直接覆盖写入，并留下可追溯的降级记录
            with path.open("w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            _log_atomic_fallback(path)
    finally:
        with contextlib.suppress(OSError):
            if os.path.exists(tmp):
                os.unlink(tmp)


def backup_file(path: Path, backup_dir: Path, op_id: str) -> Path | None:
    """把文件复制到 backups/<op_id>/<相对目录>/<文件名>。"""
    if not path.exists():
        return None
    target_dir = backup_dir / op_id / path.parent.name
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    shutil.copy2(path, target)
    return target


def restore_file(backup_path: Path, target: Path) -> None:
    if not backup_path.exists():
        raise VaultError(f"备份不存在：{backup_path}")
    write_atomic(target, backup_path.read_text(encoding="utf-8"))


def iter_markdown(root: Path, skip_dirs: tuple[str, ...] = (".obsidian", ".git", ".novel_system")) -> Iterator[Path]:
    """遍历 Markdown。跳过隐藏文件：`.tmp_*` 是原子写入的半成品，不是事实源。"""
    for dirpath, dirnames, filenames in os.walk(root):
        # 隐藏目录（.trash、.workbuddy…）与 .git/.obsidian 一样不是事实源：
        # 把它们排除在外，否则归档/回收站里的旧档案会以同名 ID 覆盖真档案
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
        for name in filenames:
            if name.endswith(".md") and not name.startswith("."):
                yield Path(dirpath) / name


def cleanup_stale_temp(root: Path, min_age_seconds: int = 300) -> list[Path]:
    """删除遗留的 `.tmp_*` 半成品（写入降级、进程中断留下的残骸）。

    只在索引重建这类维护动作里调用，并且只删「超过 min_age_seconds 没被修改」的文件，
    避免和正在进行的写入抢同一个临时文件。删除失败不报错：受限环境可能禁止 unlink，
    但残留文件已被索引器和 .gitignore 双重隔离，不影响事实源正确性。
    """
    removed: list[Path] = []
    now = time.time()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in (".git", ".obsidian", ".novel_system") and not d.startswith(".")
        ]
        for name in filenames:
            if not name.startswith(".tmp_"):
                continue
            target = Path(dirpath) / name
            try:
                if now - target.stat().st_mtime < min_age_seconds:
                    continue
                target.unlink()
                removed.append(target)
            except OSError:
                continue
    return removed


def extract_section(body: str, heading: str) -> str:
    """提取 `## heading` 段落内容（到下一个 ## 为止），忽略大小写与前后空格。"""
    target = heading.strip().lower()
    out: list[str] = []
    capturing = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped.lstrip("#").strip().lower()
            if capturing and level <= 2:
                break
            if level == 2 and title == target:
                capturing = True
                continue
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def bullets(section_text: str) -> list[str]:
    """把 `- 内容` 形式的段落实体化成列表（去掉空项与占位符）。"""
    items: list[str] = []
    for line in section_text.splitlines():
        s = line.strip()
        if not s.startswith(("-", "*")):
            continue
        value = s.lstrip("-*").strip()
        if not value or value in ("-", "…", "..."):
            continue
        items.append(value)
    return items


def key_values(section_text: str) -> dict[str, str]:
    """解析 `- 键：值` 形式，中英文冒号都支持。"""
    result: dict[str, str] = {}
    for line in section_text.splitlines():
        s = line.strip().lstrip("-*").strip()
        for sep in ("：", ":"):
            if sep in s:
                key, _, value = s.partition(sep)
                result[key.strip()] = value.strip()
                break
    return result


def checkbox_checked(text: str, label: str) -> bool:
    """判断 `- [x] label` 是否勾选。"""
    for line in text.splitlines():
        s = line.strip()
        if label in s and s.startswith(("- [x]", "- [X]")):
            return True
    return False


def chapter_num(chapter_id: str) -> int:
    """CH-0010 -> 10（数值比较，禁止字符串排序）。"""
    parts = chapter_id.split("-")
    for part in reversed(parts):
        if part.isdigit():
            return int(part)
    raise VaultError(f"无法解析章节序号：{chapter_id}")


def count_words(text: str) -> int:
    """中文按字计，英文按空格分词计。"""
    import re

    zh = len(re.findall(r"[\u4e00-\u9fff]", text))
    en = len(re.findall(r"[A-Za-z]+", text))
    return zh + en
