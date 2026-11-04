"""实体索引页：全量导出、表格转义、写入位置。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from novel_system.database.sqlite import Index
from novel_system.workflow import index_md


def _row(doc_id: str, doc_type: str, name: str, *, aliases=None, summary="") -> dict[str, object]:
    return {
        "id": doc_id,
        "type": doc_type,
        "name": name,
        "aliases": json.dumps(aliases or [], ensure_ascii=False),
        "canon_status": "confirmed",
        "file_path": f"01_Core_Memory/{doc_id}_{name}.md",
        "updated_at": "2026-11-04",
        "tags": "[]",
        "summary": summary,
        "content_hash": "",
    }


def test_render_lists_every_entity_grouped_by_type(tmp_path: Path):
    index = Index.open(tmp_path / "index.sqlite", create=True)
    index.upsert_entity(_row("CHAR-0002", "character", "项月溪", aliases=["夏月溪", "公主"]))
    index.upsert_entity(_row("CHAR-0001", "character", "陈默"))
    index.upsert_entity(_row("FAC-0025", "faction", "环之教派", aliases=["环教"]))

    text, counts = index_md.render(index)

    assert counts == {"character": 2, "faction": 1}
    assert text.startswith("---\ntype: index\n")
    assert "entity_count: 3" in text
    # 类型章节按约定顺序：人物在势力之前
    assert text.index("## 人物") < text.index("## 势力")
    # 组内按编号升序
    assert text.index("CHAR-0001") < text.index("CHAR-0002")
    assert "夏月溪、公主" in text


def test_render_escapes_table_pipes(tmp_path: Path):
    """名称或别名里带竖线时，必须转义，否则整行表格错列。"""
    index = Index.open(tmp_path / "index.sqlite", create=True)
    row = _row("ITEM-0001", "item", "甲|乙", aliases=["丙|丁"])
    row["file_path"] = "01_Core_Memory/Items/ITEM-0001.md"
    index.upsert_entity(row)

    text, _ = index_md.render(index)

    assert "甲\\|乙" in text
    assert "丙\\|丁" in text
    line = [ln for ln in text.splitlines() if ln.startswith("| ITEM-0001")][0]
    # 先把转义竖线替换掉，再按真分隔符切列：5 列表格就该切出 5 段
    cells = line.replace("\\|", "\x00").strip("|").split("|")
    assert len(cells) == 5, cells


def test_write_entity_index_creates_page_at_expected_path(tmp_path: Path):
    index = Index.open(tmp_path / "index.sqlite", create=True)
    index.upsert_entity(_row("CHAR-0001", "character", "陈默"))

    cfg = SimpleNamespace(vault=tmp_path / "vault")
    result = index_md.write_entity_index(cfg, index)

    assert result["path"] == "07_Entity_Graph/Entity_Index.md"
    assert result["entities"] == 1
    written = cfg.vault / "07_Entity_Graph/Entity_Index.md"
    assert written.exists()
    assert "[[CHAR-0001_陈默\\|陈默]]" in written.read_text(encoding="utf-8")
