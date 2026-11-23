"""章节切分与段落映射。

输入的标题层级、章节标记都不可信——别人的稿子可能用 `# 第一章`、可能用 `第一章`、
也可能整篇没有一处标记。所以：

- **切不出章就如实说切不出**：整篇当一章，并在 `known_limits` 里写明；
- **每个非空段落必须落到某处**：要么在某章里，要么进未映射清单（带定位符与前 40 字），
  不许静默丢；
- **不合并、不改写**：段落文本逐字保留，行号指回原文件。解析器不负责「整理稿件」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from novel_management.application.importing.model import Chapter, ImportFile, Paragraph, make_locator

# Markdown 标题。`#` 后必须有空白，否则 `#tag` 会被当成标题。
MD_HEADING = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<title>.*\S)\s*$")

# 中日韩小说常见的章节标记。收得比「聪明」更宽一点：误切一两个假章，
# 作者一眼能看出来；漏切则会让整本变成一章。
CJK_CHAPTER = re.compile(
    r"^\s*(?:第\s*[0-9零一二三四五六七八九十百千万两]+\s*[章節节回卷篇]"
    r"|序章|序言|楔子|尾声|尾聲|后记|後記|终章|終章)"
    r"[\s:：、.．\-—]*(?P<title>.*\S)?\s*$"
)
EN_CHAPTER = re.compile(r"^\s*(?:chapter|part|prologue|epilogue)\b[^\n]*$", re.IGNORECASE)

# 行首的「装饰」：全角空格、`#` 号。别人从编辑器导出的 TXT 常把 `## 第一章`
# 原样带出来；不认这层，整本就只剩一章。`#` 的个数照旧当层级用——
# 同一份文字存成 .md 还是 .txt，切出来的章应当一样。
LINE_MARKER = re.compile(r"^[\s\u3000]*(?P<hashes>#{1,6}[ \t]*)?")


@dataclass
class ParseResult:
    file: str
    chapters: list[Chapter]
    unmapped: list[Paragraph]
    errors: list[str]
    limits: list[str]


def _heading_of(line: str, fmt: str) -> tuple[int, str] | None:
    """这一行是不是标题。返回 (层级, 标题)；不是则 None。

    两种格式走同一套判据，只有一点不同：Markdown 的 `# 标题` 一定是标题；
    TXT 里 `# 标题` 只是「带了井号的文字」，还得看出后面是不是章名——
    否则一篇用 `#` 当项目符号的 TXT 会被切得粉碎。

    Markdown 认不出 `#` 时也退回文字标记（`第N章`）：有些 .md 是纯文本
    改名来的，通篇没有 `#`，只认 `#` 就会把整本当一章。
    """
    if fmt == "markdown":
        match = MD_HEADING.match(line)
        if match:
            return len(match.group("hashes")), match.group("title").strip()
    marker = LINE_MARKER.match(line.rstrip())
    hashes = 0
    stripped = line.strip()
    if marker:
        group = marker.group("hashes") or ""
        hashes = group.count("#")
        stripped = line.rstrip()[marker.end() :].strip()
    if not stripped:
        return None
    if CJK_CHAPTER.match(stripped) or EN_CHAPTER.match(stripped):
        return (hashes or 1), stripped
    return None


def _paragraph_spans(lines: list[str], start: int, end: int) -> list[tuple[int, int, str]]:
    """把 [start, end) 行区间切成段落。空行分段，纯空白行不算段。"""
    spans: list[tuple[int, int, str]] = []
    buffer: list[str] = []
    buffer_start = 0
    for line_no in range(start, end):
        text = lines[line_no]
        if text.strip():
            if not buffer:
                buffer_start = line_no + 1  # 1 起
            buffer.append(text)
        elif buffer:
            spans.append((buffer_start, line_no, "\n".join(buffer)))
            buffer = []
    if buffer:
        spans.append((buffer_start, end, "\n".join(buffer)))
    return spans


def split_chapters(
    text: str, *, source_file: str, fmt: str, level: int = 0
) -> ParseResult:
    """切章。`level=0` 表示自动选最浅的标题层级。"""
    lines = text.splitlines()
    errors: list[str] = []
    limits: list[str] = []

    heads: list[tuple[int, int, str]] = []  # (行下标, 层级, 标题)
    for idx, line in enumerate(lines):
        found = _heading_of(line, fmt)
        if found:
            heads.append((idx, found[0], found[1]))

    if heads:
        wanted = level or min(h[1] for h in heads)
        chosen = [h for h in heads if h[1] == wanted]
        if level and not chosen:
            limits.append(
                f"{source_file}：没有 {level} 级标题，本次连同其它层级一起按最浅的 {wanted} 级切分"
            )
            chosen = [h for h in heads if h[1] == min(x[1] for x in heads)]
        # 比它更深的标题留在章体里，不单独成章——不然 `### 场景一` 会被切成一章
        heads = chosen
    else:
        limits.append(
            f"{source_file}：没有识别到章节标记（标题/「第N章」），整篇按 1 章处理；"
            "章号与会话结构需要作者手工整理"
        )

    chapters: list[Chapter] = []
    unmapped: list[Paragraph] = []

    if not heads:
        chapter = Chapter(
            index=1,
            title=_default_title(source_file),
            source_file=source_file,
            line_start=1,
            line_end=len(lines),
            heading_level=0,
        )
        for line_start, line_end, body in _paragraph_spans(lines, 0, len(lines)):
            chapter.paragraphs.append(
                Paragraph(
                    locator=make_locator(source_file, line_start, line_end),
                    text=body,
                    line_start=line_start,
                    line_end=line_end,
                    chapter_index=1,
                )
            )
        return ParseResult(file=source_file, chapters=[chapter], unmapped=[], errors=[], limits=limits)

    # 第一个标题之前的内容：不丢，进未映射清单
    first_heading_line = heads[0][0]
    if first_heading_line > 0:
        for line_start, line_end, body in _paragraph_spans(lines, 0, first_heading_line):
            unmapped.append(
                Paragraph(
                    locator=make_locator(source_file, line_start, line_end),
                    text=body,
                    line_start=line_start,
                    line_end=line_end,
                    chapter_index=-1,
                    mapped=False,
                    reason="位于第一个章节标记之前（前言/书名页），未归入任何章",
                )
            )

    for order, (line_idx, heading_level, title) in enumerate(heads, start=1):
        body_start = line_idx + 1
        body_end = heads[order][0] if order < len(heads) else len(lines)
        chapter = Chapter(
            index=order,
            title=title or _default_title(source_file),
            source_file=source_file,
            line_start=line_idx + 1,
            line_end=body_end,
            heading_level=heading_level,
        )
        for line_start, line_end, body in _paragraph_spans(lines, body_start, body_end):
            chapter.paragraphs.append(
                Paragraph(
                    locator=make_locator(source_file, line_start, line_end),
                    text=body,
                    line_start=line_start,
                    line_end=line_end,
                    chapter_index=order,
                )
            )
        if chapter.empty:
            limits.append(
                f"{source_file} 第 {order} 章「{title}」没有任何正文段落"
                "（只有标题）；该章仍会出现在报告里，但不会生成正文提案"
            )
        chapters.append(chapter)

    # 空行数量对不上说明有内容被吃了——宁可报出来，也不悄悄少几段。
    # 分母要把标题行排除：标题是章的坐标，不是它的段落；算进去会永远差 N 段。
    body_ranges: list[tuple[int, int]] = [(0, heads[0][0] if heads else len(lines))]
    for order, (line_idx, _, _title) in enumerate(heads):
        start = line_idx + 1
        end = heads[order + 1][0] if order + 1 < len(heads) else len(lines)
        body_ranges.append((start, end))
    total = sum(len(_paragraph_spans(lines, start, end)) for start, end in body_ranges)
    consumed = sum(len(c.paragraphs) for c in chapters) + len(unmapped)
    if consumed != total:
        errors.append(
            f"{source_file}：总段落 {total}，已归属 {consumed}，差 {total - consumed} 段"
            "（解析缺陷，请报给维护者）"
        )

    return ParseResult(
        file=source_file, chapters=chapters, unmapped=unmapped, errors=errors, limits=limits
    )


def _default_title(source_file: str) -> str:
    stem = source_file.rsplit("/", 1)[-1]
    return stem.rsplit(".", 1)[0] or source_file


def parse_document(
    record: ImportFile, data: bytes, *, level: int = 0
) -> ParseResult:
    """按格式解析一份已接收的文件。只有被接收的文件才该走到这里。"""
    text = data.decode("utf-8-sig", errors="strict")
    return split_chapters(text, source_file=record.path, fmt=record.fmt, level=level)
