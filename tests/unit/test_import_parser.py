"""T07 章节切分与段落归属。

解析器的三条硬约束，逐条对应下面的用例：

1. 切不出章就如实说切不出（整篇一章 + `known_limits`），不硬猜；
2. **每个非空段落都要有归属**——要么在某章里，要么进显式未映射区，不许静默丢；
3. 段落文本逐字保留，定位符指回原文件的真实行号。

第 2 条靠「段落总数核对」兜底：分母排掉标题行，差一段就报解析缺陷。
"""

from __future__ import annotations

from novel_management.importing.parser import _heading_of, split_chapters


def _split(text: str, *, fmt: str = "markdown", level: int = 0):
    return split_chapters(text, source_file="稿.md", fmt=fmt, level=level)


def _counted(result) -> int:
    return sum(len(c.paragraphs) for c in result.chapters) + len(result.unmapped)


# ---- 标题识别 ------------------------------------------------------------

def test_markdown_heading_needs_space() -> None:
    """`#tag` 不是标题——`#` 后面必须有空白。"""
    assert _heading_of("# 第一章 渡口", "markdown") == (1, "第一章 渡口")
    assert _heading_of("#tag", "markdown") is None


def test_txt_accepts_numbered_chapter() -> None:
    assert _heading_of("第十二章 归途", "text") == (1, "第十二章 归途")
    assert _heading_of("序章", "text") == (1, "序章")


def test_txt_keeps_hash_depth() -> None:
    """TXT 里的 `## ` 照旧当层级用：同一份文字存成 .md 还是 .txt 应当切得一样。"""
    assert _heading_of("## 第一章 旧事", "text") == (2, "第一章 旧事")
    assert _heading_of("# 第一卷 起", "text") == (1, "第一卷 起")


def test_txt_body_line_is_not_heading() -> None:
    assert _heading_of("他想起第一章里写过的事。", "text") is None
    assert _heading_of("", "text") is None


def test_markdown_falls_back_to_text_marker() -> None:
    """纯文本改名来的 .md 通篇没有 `#`，只认 `#` 会把整本当一章。"""
    assert _heading_of("第一章 渡口", "markdown") == (1, "第一章 渡口")


# ---- 切章 ----------------------------------------------------------------

def test_splits_on_shallowest_level() -> None:
    text = "# 第一章 甲\n\n正文一。\n\n## 场景\n\n正文二。\n\n# 第二章 乙\n\n正文三。\n"
    result = _split(text)
    assert [c.title for c in result.chapters] == ["第一章 甲", "第二章 乙"]
    # 更深的标题留在章体里，不单独成章——否则 `### 场景一` 会被切成一章。
    # 它作为一行正文留在章里（不改写、不删行），所以第一章有 3 段。
    assert [len(c.paragraphs) for c in result.chapters] == [3, 1]
    assert "## 场景" in result.chapters[0].paragraphs[1].text
    assert result.errors == []


def test_explicit_level_overrides_depth() -> None:
    text = "# 第一卷\n\n## 第一章\n\n甲。\n\n## 第二章\n\n乙。\n"
    result = _split(text, level=2)
    assert [c.title for c in result.chapters] == ["第一章", "第二章"]
    assert result.errors == []


def test_missing_requested_level_is_reported() -> None:
    text = "# 第一章\n\n甲。\n"
    result = _split(text, level=3)
    assert len(result.chapters) == 1
    assert any("3 级标题" in item for item in result.limits)


def test_whole_document_becomes_one_chapter() -> None:
    """一处标记都没有：整篇一章，并且**明说**这是降级处理。"""
    text = "第一段。\n\n第二段。\n"
    result = _split(text, fmt="text")
    assert len(result.chapters) == 1
    assert result.chapters[0].index == 1
    assert len(result.chapters[0].paragraphs) == 2
    assert any("没有识别到章节标记" in item for item in result.limits)


def test_preamble_before_first_heading_is_unmapped_not_dropped() -> None:
    text = "书名页\n\n作者\n\n# 第一章\n\n甲。\n"
    result = _split(text)
    assert len(result.unmapped) == 2, "首章之前的两段各成一段，都要显式列出"
    assert all("第一个章节标记之前" in p.reason for p in result.unmapped)
    assert all(p.mapped is False for p in result.unmapped)
    assert _counted(result) == 3


def test_empty_chapter_is_kept_and_explained() -> None:
    """只有标题的空章要留在报告里（它有坐标），但不该悄悄算作有内容。"""
    text = "# 第一章\n\n# 第二章\n\n乙。\n"
    result = _split(text)
    assert [c.empty for c in result.chapters] == [True, False]
    assert any("没有任何正文段落" in item for item in result.limits)


def test_paragraph_locator_points_at_real_lines() -> None:
    text = "线一\n线二\n\n线四\n"
    result = _split(text, fmt="text")
    paragraph = result.chapters[0].paragraphs[0]
    assert paragraph.line_start == 1
    assert paragraph.line_end == 2
    assert paragraph.locator == "稿.md#L1-L2"
    assert paragraph.text == "线一\n线二"


def test_paragraph_text_is_verbatim() -> None:
    """不改写、不合并、不整理：解析器只管坐标，不管文风。"""
    body = "  缩进两个空格的一行  \n第二行带着全角标点，和「引号」"
    result = _split(f"# 第一章\n\n{body}\n", fmt="text")
    assert result.chapters[0].paragraphs[0].text == body


def test_paragraph_count_is_audited() -> None:
    """段落总数核对：分母排掉标题行，少一段就必须报解析缺陷。"""
    text = "# 第一章\n\n甲。\n\n乙。\n\n# 第二章\n\n丙。\n"
    result = _split(text)
    assert _counted(result) == 3
    assert result.errors == []


def test_blank_lines_do_not_make_paragraphs() -> None:
    text = "# 第一章\n\n\n\n甲。\n\n\n"
    result = _split(text)
    assert len(result.chapters[0].paragraphs) == 1


def test_english_chapter_markers() -> None:
    text = "Chapter 1\n\nBody.\n\nChapter 2\n\nMore.\n"
    result = _split(text, fmt="text")
    assert len(result.chapters) == 2
    assert result.errors == []


def test_chapter_locator_covers_heading_through_body() -> None:
    text = "# 第一章\n\n甲。\n\n# 第二章\n\n乙。\n"
    result = _split(text)
    first = result.chapters[0]
    assert first.line_start == 1
    assert first.line_end == 4, "第一章覆盖标题行到下一条标题之前"
    assert first.locator == "稿.md#L1-L4"
