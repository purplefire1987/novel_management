"""T02 字段契约（PRD §8.4）。

三条验收在这里闭环：

- 缺字段被拒绝 —— 从每个 valid 样本里删掉一个 required 键。
- 非法枚举被拒绝 —— 每类的 invalid 样本各挑一种错误。
- 未知字段被拒绝 —— 给每个 valid 样本加一个 `__unknown__`。

覆盖方式是「对每一类都做」，而不是抽查几类。schema 加了新类型但忘了写样本，
第一条用例会直接失败。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from novel_management.schemas.store import SchemaStore

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "schemas" / "samples.json"
_TYPES = sorted(k for k in json.loads(FIXTURE.read_text(encoding="utf-8")) if not k.startswith("$"))


@pytest.fixture(scope="module")
def store() -> SchemaStore:
    return SchemaStore()


@pytest.fixture(scope="module")
def samples() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_every_schema_has_a_sample(store: SchemaStore, samples: dict) -> None:
    """schema 与样本一一对应。新增类型不写样本，这里会红。"""
    declared = set(store.names())
    sampled = {k for k in samples if not k.startswith("$")}
    assert declared == sampled, (
        f"缺样本：{sorted(declared - sampled)}；多余样本：{sorted(sampled - declared)}"
    )


@pytest.mark.parametrize("name", _TYPES)
def test_valid_fixture_passes(store: SchemaStore, samples: dict, name: str) -> None:
    if name.startswith("$"):
        pytest.skip("$comment 不是类型")
    assert store.check(name, samples[name]["valid"]) == []


@pytest.mark.parametrize("name", _TYPES)
def test_invalid_fixture_is_rejected(store: SchemaStore, samples: dict, name: str) -> None:
    if name.startswith("$"):
        pytest.skip("$comment 不是类型")
    errors = store.check(name, samples[name]["invalid"])
    assert errors, f"{name} 的 invalid 样本居然通过了——校验器漏了这个错误"


@pytest.mark.parametrize("name", _TYPES)
def test_missing_required_key_is_rejected(store: SchemaStore, samples: dict, name: str) -> None:
    """缺字段必须被拒绝。逐类做，不是抽查。"""
    if name.startswith("$"):
        pytest.skip("$comment 不是类型")
    doc = store.load(name)
    required = doc.get("required") or []
    if not required:
        pytest.skip(f"{name} 顶层没有 required（必填在 $ref 分支里，已由 invalid 样本覆盖）")
    for key in required:
        broken = copy.deepcopy(samples[name]["valid"])
        broken.pop(key, None)
        errors = store.check(name, broken)
        assert errors, f"{name} 缺 {key} 却通过了"


@pytest.mark.parametrize("name", _TYPES)
def test_unknown_field_is_rejected(store: SchemaStore, samples: dict, name: str) -> None:
    """未知字段必须被拒绝（PRD §8.4「未知字段默认拒绝」）。"""
    if name.startswith("$"):
        pytest.skip("$comment 不是类型")
    polluted = copy.deepcopy(samples[name]["valid"])
    polluted["__unknown_field__"] = "x"
    errors = store.check(name, polluted)
    # oneOf 类型的根（如 source_ref）会报「不满足分支」而不是逐字段报「未知字段」，
    # 但同样必须被拒绝——所以判据是「有错」，不是「错误文本里必须出现某四个字」。
    assert errors, f"{name} 放过了未知字段"


def test_extensions_is_the_only_escape_hatch(store: SchemaStore) -> None:
    """扩展只能进 extensions，不能就地加字段。"""
    base = {"id": "ent_000001", "type": "character", "name": "示例"}
    assert store.check("entity", {**base, "extensions": {"我方扩展": 1}}) == []
    assert store.check("entity", {**base, "我方扩展": 1}) != []


def test_shared_metadata_fields_are_consistent(store: SchemaStore) -> None:
    """带 artifact_type 的 schema 必须都有同一组通用元数据字段。

    这些字段故意没有抽成 $defs（`additionalProperties: false` 不继承父级
    properties），所以只能靠这条测试防它们各写各的。
    """
    expected = {
        "artifact_type",
        "artifact_status",
        "schema_version",
        "created_by_task",
        "derived_from",
        "supersedes",
        "tags",
        "extensions",
    }
    missing: dict[str, list[str]] = {}
    for name in store.names():
        doc = store.load(name)
        props = set((doc.get("properties") or {}))
        if "artifact_type" not in props:
            continue  # 控制面记录（task/approval/issue……）不套产物头
        gap = expected - props
        if gap:
            missing[name] = sorted(gap)
    assert not missing, f"通用元数据字段缺失：{missing}"


def test_all_schemas_are_valid_json_and_declare_draft(store: SchemaStore) -> None:
    for name in store.names():
        doc = store.load(name)
        assert doc.get("$schema", "").endswith("2020-12/schema"), name
        assert doc.get("title"), f"{name} 没有 title"


def test_id_pattern_accepts_current_form_only(store: SchemaStore) -> None:
    """当前形态是 `ent_000001`（下划线 + 六位 + 单前缀）。

    这条测试原先守的是「四位数和六位数都必须通过」，理由是当时库内实为
    `CHAR-0001`、PRD 写六位，两种并存。2026-11-22 作者裁定按 V1.0 §9.2 全库
    重排，1046 份统一成 `ent_000001`，四位数形态已不存在——继续接受它等于
    允许旧形态重新长回来。所以判据反过来：当前形态必须通过，V4.0 连字符形态
    必须被拒。守卫的意图没变，仍是「不让存量数据被判非法」。
    """
    valid = {"id": "ent_000001", "type": "character", "name": "当前形态"}
    prefixed = {"id": "asset_000001", "type": "character", "name": "同 schema 的另一前缀"}
    assert store.check("entity", valid) == []
    assert store.check("entity", prefixed) == []

    for legacy in ("CHAR-0001", "CHAR-000001", "LOC-0001"):
        bad = {"id": legacy, "type": "character", "name": "V4.0 旧形态"}
        assert store.check("entity", bad) != [], f"{legacy} 是旧形态，不该通过"


def test_compound_id_is_rejected(store: SchemaStore) -> None:
    """`EVT-CH-0001-001` 这类复合 ID 必须不合法（PRD §8.1）。"""
    bad = {"event_id": "EVT-CH-0001-001", "summary": "复合型"}
    assert store.check("event", bad) != []
