"""Markdown 解析与块 ID。"""

from __future__ import annotations

import sys

import pytest

from novel_system import markdown as md
from novel_system.errors import ParseError

DOC = """\
---
id: CHAR-0001
type: character
name: 林砚
aliases:
  - 林先生
  - 抄书人
source_refs:
  - kind: CANON
    artifact_id: CH-0001
    block_id: p0002
---

# 林砚

雨落在灰石渡的石阶上，抄书人把最后一页纸压进书匣。

他确信那个人已经死了。

- 第一条
- 第二条

> 引文

```python
print(1)
```

| a | b |
|---|---|
| 1 | 2 |
"""


def test_nested_frontmatter_is_parsed() -> None:
    doc = md.parse(DOC)
    assert doc.meta["id"] == "CHAR-0001"
    assert doc.meta["aliases"] == ["林先生", "抄书人"]
    assert doc.meta["source_refs"][0]["kind"] == "CANON"
    assert doc.meta["source_refs"][0]["block_id"] == "p0002"


def test_block_ids_are_sequential_and_unique() -> None:
    doc = md.parse(DOC)
    ids = [block.block_id for block in doc.blocks]
    assert ids == ["p0001", "p0002", "p0003", "p0004", "p0005", "p0006", "p0007"]
    assert len(set(ids)) == len(ids)


def test_block_kinds() -> None:
    kinds = [block.kind for block in md.parse(DOC).blocks]
    assert kinds == ["heading", "paragraph", "paragraph", "list", "quote", "code", "table"]


def test_block_line_numbers_point_into_original_text() -> None:
    text = DOC
    doc = md.parse(text)
    lines = text.split("\n")
    for block in doc.blocks:
        first = lines[block.line_start - 1]
        assert block.text.split("\n")[0].strip() == first.strip()


def test_code_fence_is_one_block_and_not_split_by_blank_lines() -> None:
    text = "```\na\n\nb\n```\n\n段落\n"
    doc = md.parse(text)
    assert [block.kind for block in doc.blocks] == ["code", "paragraph"]
    assert "a\n\nb" in doc.blocks[0].text


def test_document_without_frontmatter() -> None:
    doc = md.parse("只有正文。\n")
    assert doc.meta == {}
    assert doc.body_line_offset == 0
    assert doc.blocks[0].block_id == "p0001"


def test_crlf_and_bom_are_normalized() -> None:
    raw = "\ufeff---\r\nid: X\r\n---\r\n\r\n正文\r\n".encode("utf-8")
    doc = md.parse_bytes(raw)
    assert doc.meta["id"] == "X"
    assert "\r" not in doc.body


def test_non_utf8_is_rejected() -> None:
    with pytest.raises(ParseError):
        md.parse_bytes(b"---\nid: \xff\xfe\n---\n")


def test_illegal_yaml_is_rejected() -> None:
    with pytest.raises(ParseError):
        md.parse("---\nid: [unclosed\n---\n正文\n")


def test_non_mapping_frontmatter_is_rejected() -> None:
    with pytest.raises(ParseError):
        md.parse("---\n- 只有列表\n---\n正文\n")


def test_without_pyyaml_nested_structure_is_rejected(monkeypatch) -> None:
    """没装 PyYAML 时必须报错，不许悄悄把结构读成字符串。"""
    monkeypatch.setitem(sys.modules, "yaml", None)
    with pytest.raises(ParseError) as excinfo:
        md.parse(DOC)
    assert "PyYAML" in str(excinfo.value)


def test_without_pyyaml_flat_frontmatter_still_works(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "yaml", None)
    doc = md.parse("---\nid: CHAR-0001\ntype: character\n---\n正文\n")
    assert doc.meta["id"] == "CHAR-0001"


def test_block_id_width() -> None:
    assert md.block_id(1) == "p0001"
    assert md.block_id(42) == "p0042"
    assert md.block_id(9999) == "p9999"
    assert md.block_id(10000) == "p10000"  # 自然变宽，不截断
    with pytest.raises(ValueError):
        md.block_id(0)
