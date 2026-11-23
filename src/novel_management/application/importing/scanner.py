"""输入扫描：格式、编码、哈希。

三件事上不做「尽量试试」：

1. **格式**：只收 UTF-8 Markdown / TXT（PRD §12.1 的 P0）。docx/epub/pdf 直接拒绝
   并说明属 P1，不是「解析失败」——前者是没做，后者是做了没成，混在一起会让人以为
   解析器坏了。
2. **编码**：`bytes.decode("utf-8")` 失败就拒绝。不猜 GBK、不猜 latin-1：
   猜错的表现是整章乱码却写得出来，而乱码会被当成作者的原文保存下来。
3. **哈希**：读进来就算，之后每处比对都用它，不再重算原文件（原文件可能已经被作者改过）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from novel_management.domain.errors import UsageError
from novel_management.application.importing.model import MARKDOWN_SUFFIXES, REJECTED_SUFFIXES, TEXT_SUFFIXES, ImportFile


def _fmt_of(suffix: str) -> str:
    lowered = suffix.lower()
    if lowered in MARKDOWN_SUFFIXES:
        return "markdown"
    if lowered in TEXT_SUFFIXES:
        return "text"
    return ""


def scan_one(path: Path, *, base: Path | None = None) -> tuple[ImportFile, bytes]:
    """扫一份文件。返回 (记录, 原始字节)。**不写盘。**

    被拒的文件也返回记录（带 rejected 原因）与空字节，好让报告里能看到它。
    """
    rel = path.name if base is None else _rel(path, base)
    record = ImportFile(path=rel)

    if not path.exists():
        record.rejected = "文件不存在"
        return record, b""
    if path.is_dir():
        record.rejected = "这是一个目录，不是文件"
        return record, b""

    suffix = path.suffix
    fmt = _fmt_of(suffix)
    if not fmt:
        record.rejected = REJECTED_SUFFIXES.get(
            suffix.lower(),
            f"不支持的扩展名 {suffix or '（无）'}；本版只收 .md / .markdown / .txt",
        )
        return record, b""

    data = path.read_bytes()
    record.bytes = len(data)
    record.sha256 = hashlib.sha256(data).hexdigest()
    record.fmt = fmt

    if data.startswith(b"\xef\xbb\xbf"):
        # BOM 不拦，但要记：它会让「第一行是不是标题」判断出错，报告里得能看出来
        record.encoding = "utf-8-bom"
        data = data[3:]
    else:
        record.encoding = "utf-8"

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        record.rejected = (
            f"不是合法 UTF-8（第 {exc.start} 字节起）："
            "请先转成 UTF-8 再导入；本系统不猜 GBK/GB18030 等编码"
        )
        return record, b""

    record.lines = len(text.splitlines())
    if not text.strip():
        record.rejected = "文件为空（只有空白字符）"
        return record, b""

    return record, data


def _rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.name


def scan_inputs(paths: list[Path], *, base: Path | None = None) -> list[tuple[ImportFile, bytes]]:
    """扫一批。目录会展开成其中的 md/txt；一个都收不到就报用法错误。

    「一个都收不到」和「收到但全被拒」是两回事：前者是路径写错了，后者要看报告。
    """
    expanded: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            found = sorted(
                p
                for p in path.rglob("*")
                if p.is_file() and _fmt_of(p.suffix)
            )
            if not found:
                raise UsageError(f"目录里没有 .md/.markdown/.txt：{path}")
            expanded.extend(found)
        else:
            expanded.append(path)

    if not expanded:
        raise UsageError("没有要导入的文件")

    rows: list[tuple[ImportFile, bytes]] = []
    seen: dict[str, str] = {}
    for path in expanded:
        record, data = scan_one(path, base=base)
        if record.accepted:
            # 同名不同内容会让定位符（靠文件名）指向错误的东西
            if record.path in seen and seen[record.path] != record.sha256:
                record.rejected = (
                    f"同一次导入里有两份 {record.path} 且内容不同；"
                    "定位符靠文件名定位，不能重名"
                )
            else:
                seen[record.path] = record.sha256
        rows.append((record, data))
    return rows
