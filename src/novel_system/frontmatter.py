"""frontmatter 的唯一解析实现。

此前只有 `cli/_common.py` 里一份简易解析，底层模块（如 ID 分配）要用就得反向
依赖 CLI 层。这里把它挪到底层，CLI 改为转发。

**只解析顶层 `key: value`**。嵌套映射与列表（`source_refs:` 下的 `- kind: ...`）
一律不进结果——那是 T03 Markdown/YAML 解析的活，用 YAML 库做，不要在这里
手写半个解析器。需要顶层 id / type / status 的调用方（索引、查重、ID 分配）
用这个就够。
"""

from __future__ import annotations

from pathlib import Path

DELIM = "---"


def split(text: str) -> tuple[str, str]:
    """拆成 (frontmatter 文本, 正文)。没有 frontmatter 时返回 ("", 全文)。"""
    if not text.startswith(DELIM):
        return "", text
    end = text.find("\n" + DELIM, len(DELIM))
    if end < 0:
        return "", text
    block = text[len(DELIM) : end]
    body_start = end + len("\n" + DELIM)
    # 结束行后面可能紧跟换行，跳过后再取正文
    if body_start < len(text) and text[body_start] == "\n":
        body_start += 1
    return block, text[body_start:]


def parse_block(block: str) -> dict[str, str]:
    """顶层键值。缩进行（嵌套）与列表项跳过，值去掉成对引号。"""
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            continue  # 嵌套内容不归这一层管
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        meta[key.strip()] = value
    return meta


def read_text(text: str) -> dict[str, str]:
    block, _ = split(text)
    if not block:
        return {}
    return parse_block(block)


def read(path: Path) -> dict[str, str]:
    """读文件取顶层 frontmatter。读不了就当没有——调用方据此判断「缺 id」。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return read_text(text)


# 旧名字：CLI 层沿用，避免一次性改多处调用点。
read_frontmatter = read
