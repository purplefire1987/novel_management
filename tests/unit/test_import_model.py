"""T07 数据结构与定位符。

这一层测三件事：

1. **定位符能往返**：写出去的 `文件名#L起-L止` 必须能读回同一段原文；
   读不回就等于「来源引用不可解析」，PRD §12.4 的硬要求。
2. **ImportManifest 说话不许含糊**：十一个维度各自给结论，没有 `success` 字段，
   加一个就违反 schema（`additionalProperties: false`）。
3. **抽样是确定性的**：同种子同规模出同样样本，否则「可复现」是空话。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_system.errors import UsageError
from novel_system.importing.model import (
    ImportManifest,
    make_locator,
    parse_locator,
    read_locator,
)
from novel_system.importing.sampling import normalise, sample_indices, verify_sample
from novel_system.schema import check as check_schema

RAW = "第一行\n第二行\n第三行\n第四行\n"


@pytest.fixture()
def raw_dir(tmp_path: Path) -> Path:
    path = tmp_path / "raw"
    path.mkdir()
    (path / "旧稿.md").write_text(RAW, encoding="utf-8")
    return path


# ---- 定位符 --------------------------------------------------------------

def test_locator_round_trip(raw_dir: Path) -> None:
    locator = make_locator("旧稿.md", 2, 3)
    assert locator == "旧稿.md#L2-L3"
    assert parse_locator(locator) == ("旧稿.md", 2, 3)
    assert read_locator(raw_dir, locator) == "第二行\n第三行"


def test_single_line_locator_has_no_dash() -> None:
    assert make_locator("a.md", 7) == "a.md#L7"
    assert parse_locator("a.md#L7") == ("a.md", 7, 7)


def test_locator_rejects_bad_shapes() -> None:
    for bad in ("旧稿.md", "旧稿.md#L0", "旧稿.md#12", "#L1", "旧稿.md#L3-L1"):
        with pytest.raises(UsageError):
            parse_locator(bad)


def test_locator_without_chapter_number() -> None:
    """定位符里不能有章号：章号是解析结果，会随切分规则变。"""
    locator = make_locator("旧稿.md", 1, 2)
    assert "章" not in locator and "CH" not in locator
    assert parse_locator(locator)[0] == "旧稿.md"


def test_read_locator_refuses_missing_file(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="不存在"):
        read_locator(tmp_path, "没有这份.md#L1")


def test_read_locator_refuses_out_of_range(raw_dir: Path) -> None:
    with pytest.raises(UsageError, match="越界"):
        read_locator(raw_dir, "旧稿.md#L99")


def test_read_locator_clamps_tail(raw_dir: Path) -> None:
    """末行之后多要几行不该报错——原文只有 4 行，取到最后一行即可。"""
    assert read_locator(raw_dir, "旧稿.md#L3-L9") == "第三行\n第四行"


# ---- Manifest ------------------------------------------------------------

def _manifest(**over: object) -> ImportManifest:
    base = dict(
        import_id="IMP-000001",
        created_at="2026-11-21T00:00:00Z",
        project_id="novel-001",
        files=[{"path": "旧稿.md", "sha256": "a" * 64, "bytes": 10, "format": "markdown",
                "encoding": "utf-8", "lines": 4, "rejected": ""}],
        raw_bytes_preserved=True,
        chapters_parsed=2,
        paragraphs_mapped=4,
        refs_valid=True,
        blocking_conflicts=0,
        sample_result={"sample_size": 4, "seed": "s", "accuracy": 1.0, "note": "n"},
    )
    base.update(over)
    return ImportManifest(**base)  # type: ignore[arg-type]


def test_manifest_has_no_success_field() -> None:
    """不许有「总之成功了」的字段：那会把十一个维度压成一句话。"""
    payload = _manifest().as_dict()
    assert "success" not in payload
    assert "ok" not in payload
    assert "status" not in payload


def test_manifest_satisfies_schema() -> None:
    """记录的字段集必须过 `schemas/import_manifest.schema.json`。"""
    errors = check_schema("import_manifest", _manifest().as_dict())
    assert errors == [], errors


def test_manifest_rejects_extra_field() -> None:
    """`additionalProperties: false` 得真的拦得住「随手加个字段」。"""
    payload = _manifest().as_dict()
    payload["整批成功"] = True
    errors = check_schema("import_manifest", payload)
    assert errors, "schema 应当拒绝未声明的字段"


def test_verdicts_are_per_dimension() -> None:
    rows = _manifest(chapters_pending=["0003 空白"]).verdicts()
    labels = {label: passed for label, passed, _ in rows}
    assert labels["提取覆盖完整（每章都提到候选）"] is False
    assert labels["原始字节 100% 保全"] is True
    # 提取覆盖不完整不等于不能发布——两件事分开
    assert _manifest(chapters_pending=["0003 空白"]).blocking() == []


def test_blocking_names_each_problem() -> None:
    problems = _manifest(raw_bytes_preserved=False, refs_valid=False).blocking()
    assert len(problems) == 2
    assert any("原始字节" in p for p in problems)
    assert any("来源引用" in p for p in problems)


def test_unmapped_paragraphs_do_not_block() -> None:
    """PRD §12.4：映射到「显式未映射区」也算合规，只是没拿满分。"""
    manifest = _manifest(paragraphs_unmapped=["旧稿.md#L1-L1"])
    assert manifest.blocking() == []
    rows = {label: passed for label, passed, _ in manifest.verdicts()}
    assert rows["非空段落全部有归属"] is False


def test_round_trip_through_json() -> None:
    original = _manifest(chapters_extracted=["0001 渡口"], known_limits=["限制"])
    again = ImportManifest.from_dict(json.loads(json.dumps(original.as_dict())))
    assert again.as_dict() == original.as_dict()


def test_detail_is_not_persisted_in_manifest() -> None:
    """明细另存 `detail.json`：塞进 manifest 会让 schema 变成一坨放行字段。"""
    manifest = _manifest()
    manifest.detail = {"chapters": [{"index": 1}]}
    assert "detail" not in manifest.as_dict()
    assert "chapters" not in manifest.as_dict()


# ---- 抽样 ----------------------------------------------------------------

def test_sample_is_deterministic() -> None:
    first = sample_indices(50, size=10, seed="novel-import-v1")
    second = sample_indices(50, size=10, seed="novel-import-v1")
    assert first == second
    assert len(first) == 10


def test_sample_changes_with_seed() -> None:
    a = sample_indices(50, size=10, seed="seed-a")
    b = sample_indices(50, size=10, seed="seed-b")
    assert a != b, "换种子还抽同一批，说明种子没生效"


def test_sample_caps_at_population() -> None:
    assert sample_indices(3, size=10, seed="s") == [0, 1, 2]
    assert sample_indices(0, size=10, seed="s") == []


def test_verify_sample_counts_locator_only(raw_dir: Path) -> None:
    items = [("旧稿.md#L1", "第一行"), ("旧稿.md#L3-L4", "第三行\n第四行")]
    result = verify_sample(raw_dir, items, size=10, seed="s")
    assert result["sample_size"] == 2
    assert result["accuracy"] == 1.0
    assert "来源定位准确率" in result["note"]
    assert "不是语义提取准确率" in result["note"]


def test_verify_sample_reports_mismatch(raw_dir: Path) -> None:
    items = [("旧稿.md#L1", "第一行"), ("旧稿.md#L2", "这一行不存在")]
    result = verify_sample(raw_dir, items, size=10, seed="s")
    assert result["sample_size"] == 2
    assert result["accuracy"] == 0.5
    assert "不通过" in result["note"]


def test_verify_sample_of_nothing_is_not_100_percent(tmp_path: Path) -> None:
    """没有样本时的通过率不是通过率——记 0 并说明，不写 1.0。"""
    result = verify_sample(tmp_path, [], size=10, seed="s")
    assert result["sample_size"] == 0
    assert result["accuracy"] == 0.0
    assert "不适用" in result["note"]


def test_normalise_only_collapses_whitespace() -> None:
    assert normalise("a  b\n c") == "a b c"
    assert normalise("全角，标点") == "全角，标点"
