"""按名称 / 别名 / 概要查实体档案：录入判「库里有没有」的唯一依据。"""

from __future__ import annotations

import json
from pathlib import Path

from novel_system.database.sqlite import Index


def _row(doc_id: str, doc_type: str, name: str, *, aliases: list[str] | None = None,
         summary: str = "", path: str = "") -> dict[str, object]:
    return {
        "id": doc_id,
        "type": doc_type,
        "name": name,
        "aliases": json.dumps(aliases or [], ensure_ascii=False),
        "canon_status": "confirmed",
        "file_path": path or f"01_Core_Memory/{doc_id}.md",
        "updated_at": "2026-11-04",
        "tags": "[]",
        "summary": summary,
        "content_hash": "",
    }


def test_search_entities_hits_alias_and_ranks_name_first(tmp_path: Path):
    index = Index.open(tmp_path / "index.sqlite", create=True)
    index.upsert_entity(_row("FAC-0025", "faction", "环之教派", aliases=["环教"]))
    index.upsert_entity(_row("CHAR-0034", "character", "刘平", summary="环教教众，家破人亡后入教"))
    index.upsert_entity(_row("CHAR-0002", "character", "项月溪", aliases=["夏月溪", "公主"]))

    rows = index.search_entities("环教")
    ids = [r["id"] for r in rows]
    assert ids[0] == "FAC-0025", "别名命中的正主排在概要命中之前"
    assert "CHAR-0034" in ids, "概要里提到的也要能被搜到"

    assert [r["id"] for r in index.search_entities("夏月溪")] == ["CHAR-0002"]
    assert [r["id"] for r in index.search_entities("公主")] == ["CHAR-0002"]
    assert index.search_entities("环教")[0]["aliases"].count("环教") == 1


def test_search_entities_returns_empty_for_unknown(tmp_path: Path):
    index = Index.open(tmp_path / "index.sqlite", create=True)
    index.upsert_entity(_row("CHAR-0001", "character", "陈默"))
    assert index.search_entities("靖卫司") == []
    assert index.search_entities("   ") == []
