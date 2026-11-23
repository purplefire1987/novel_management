"""Markdown 文档解析：frontmatter、正文、块。

与 `frontmatter.py` 的分工：
- `frontmatter.py` 只认**顶层** `key: value`，够查重、发号、`status` 用，零依赖。
- 本模块要的是**完整结构**（`source_refs` 是映射列表、`aliases` 是标量列表），
  必须真 YAML。装了 PyYAML 就用它；没装时只能用子集解析器读平铺键值，
  **遇到嵌套直接报错**。

**为什么不许静默退化**：子集解析器会把 `- kind: CANON` 读成字符串
`"kind: CANON"`，看上去有值、其实结构没了。索引拿这种数据去回答
「这句话出自哪个版本」，会给出一个错得很自信的答案。宁可失败。

块 ID 形如 `p0001`（PRD §9.1 的 `p0042`），同一产物内按出现顺序编号。
它是**位置编号**，不绑定内容：段落被改写时 ID 不变，段落被增删时后面的会顺移。
PRD 允许「修改段落可保留块 ID」，但也要求来源仍绑定版本或哈希——所以
真正的定位要用 `artifact_sha256 + block_id`，单看 block_id 不够。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import config as cfg_mod
from . import frontmatter as fm
from .errors import ParseError

BLOCK_ID_PREFIX = "p"
BLOCK_ID_WIDTH = 4

# 块的种类。够区分「是不是正文段落」即可，不追求 Markdown 全覆盖。
BLOCK_PARAGRAPH = "paragraph"
BLOCK_HEADING = "heading"
BLOCK_LIST = "list"
BLOCK_QUOTE = "quote"
BLOCK_CODE = "code"
BLOCK_TABLE = "table"

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
_LIST_RE = re.compile(r"^\s{0,3}(?:[-*+]|\d{1,9}[.)])\s+\S")
_QUOTE_RE = re.compile(r"^\s{0,3}>")
_TABLE_RE = re.compile(r"^\s{0,3}\|")


@dataclass(frozen=True)
class Block:
    """正文的一个块。`line_start` / `line_end` 都是 1-based，指原文行号。"""

    block_id: str
    ordinal: int
    kind: str
    text: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class Document:
    meta: dict
    body: str
    body_line_offset: int
    blocks: tuple[Block, ...]

    @property
    def raw_meta(self) -> dict:
        return self.meta


def block_id(ordinal: int) -> str:
    """第 ordinal 个块（1 起）的块 ID。超过 9999 个块时自然变宽，不截断。"""
    if ordinal < 1:
        raise ValueError(f"块序号从 1 开始，收到 {ordinal}")
    return f"{BLOCK_ID_PREFIX}{ordinal:0{BLOCK_ID_WIDTH}d}"


def _has_nested_lines(raw: str) -> bool:
    """frontmatter 里是否有缩进行（映射/列表的嵌套内容）。"""
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            return True
    return False


def load_meta(raw: str, where: str = "<string>") -> dict:
    """解析 frontmatter 文本。

    有 PyYAML 用 PyYAML。没有时能用子集解析器读平铺的键值，但**遇到嵌套直接报错**：
    子集会把它读成字符串，看上去有值、结构却没了；索引拿这种数据去回答
    「这句话出自哪个版本」，会给出一个错得很自信的答案。宁可失败并让人装上 PyYAML。
    """
    if not raw.strip():
        return {}
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None  # type: ignore[assignment]

    if yaml is not None:
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:  # type: ignore[attr-defined]
            raise ParseError(f"{where}: frontmatter 不是合法 YAML：{exc}") from exc
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ParseError(f"{where}: frontmatter 顶层必须是映射，实得 {type(data).__name__}")
        return data

    if _has_nested_lines(raw):
        raise ParseError(
            f"{where}: frontmatter 有嵌套结构，但没装 PyYAML，无法可靠解析"
            '（pip install -e ".[yaml]"）'
        )
    try:
        data = cfg_mod.parse_yaml(raw)
    except cfg_mod.ConfigError as exc:
        raise ParseError(f"{where}: frontmatter 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ParseError(f"{where}: frontmatter 顶层必须是映射")
    return data


def decode(data: bytes) -> str:
    """按 UTF-8 解码并统一换行、去 BOM。非 UTF-8 直接报错，不猜编码。"""
    from .canonical import normalize_text

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ParseError(f"文件不是 UTF-8：{exc}") from exc
    return normalize_text(text)


def _classify(line: str) -> str:
    if _FENCE_RE.match(line):
        return BLOCK_CODE
    if _HEADING_RE.match(line):
        return BLOCK_HEADING
    if _QUOTE_RE.match(line):
        return BLOCK_QUOTE
    if _TABLE_RE.match(line):
        return BLOCK_TABLE
    if _LIST_RE.match(line):
        return BLOCK_LIST
    return BLOCK_PARAGRAPH


def split_blocks(body: str, body_line_offset: int = 0) -> tuple[Block, ...]:
    """把正文切成块。

    规则：空行是块边界；围栏代码块整段算一个；连续的列表/引用/表格行各算一个；
    其余连续行算一个段落。行号按 body 内的相对位置算，再加 `body_line_offset`
    得到原文行号。
    """
    lines = body.split("\n")
    blocks: list[Block] = []
    ordinal = 0
    i = 0
    total = len(lines)

    def emit(kind: str, start: int, end: int) -> None:
        nonlocal ordinal
        text = "\n".join(lines[start:end + 1]).strip("\n")
        if not text.strip():
            return
        ordinal += 1
        blocks.append(
            Block(
                block_id=block_id(ordinal),
                ordinal=ordinal,
                kind=kind,
                text=text,
                line_start=start + 1 + body_line_offset,
                line_end=end + 1 + body_line_offset,
            )
        )

    while i < total:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        kind = _classify(line)

        if kind == BLOCK_CODE:
            fence = line.strip()[:3]
            start = i
            i += 1
            while i < total and not lines[i].strip().startswith(fence):
                i += 1
            if i < total:
                i += 1  # 收尾围栏
            emit(BLOCK_CODE, start, min(i, total) - 1)
            continue

        if kind in (BLOCK_HEADING, BLOCK_PARAGRAPH):
            start = i
            i += 1
            if kind == BLOCK_PARAGRAPH:
                # 段落持续到空行或下一个结构行
                while i < total:
                    nxt = lines[i]
                    if not nxt.strip():
                        break
                    if _classify(nxt) is not BLOCK_PARAGRAPH:
                        break
                    i += 1
            emit(kind, start, i - 1)
            continue

        # 列表 / 引用 / 表格：连续同类行算一块
        start = i
        i += 1
        while i < total:
            nxt = lines[i]
            if not nxt.strip() or _classify(nxt) is not kind:
                break
            i += 1
        emit(kind, start, i - 1)

    return tuple(blocks)


def parse(text: str, where: str = "<string>") -> Document:
    """解析一篇 Markdown。"""
    raw_meta, body = fm.split(text)
    meta = load_meta(raw_meta, where)
    offset = text[: len(text) - len(body)].count("\n") if raw_meta else 0
    blocks = split_blocks(body, body_line_offset=offset)
    return Document(meta=meta, body=body, body_line_offset=offset, blocks=blocks)


def parse_bytes(data: bytes, where: str = "<bytes>") -> Document:
    return parse(decode(data), where)
