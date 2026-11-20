"""字节规范化与包哈希（PRD §14.5 / §17.3）。

验收项「中文包哈希重复运行一致」在这里钉死：同一对象算两次、跨进程算、
换键序算，都必须得到同一个值。浮点单独测——`1.0` 若被写成 `1`，跨语言
比对就会失真，而 Python 的 repr 恰好不这么干，这个行为要锁住。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from novel_system import canonical as c
from novel_system.errors import CanonicalError


# ---- 字节规范化 ----------------------------------------------------------

def test_strips_bom_and_unifies_newlines() -> None:
    raw = c.BOM_UTF8 + "第一行\r\n第二行\r\n".encode("utf-8")
    assert c.normalize_bytes(raw) == "第一行\n第二行\n".encode("utf-8")


def test_isolated_cr_becomes_lf() -> None:
    assert c.normalize_bytes(b"a\rb") == b"a\nb"


def test_normalize_is_idempotent() -> None:
    raw = c.BOM_UTF8 + "甲\r\n乙\r丙".encode("utf-8")
    once = c.normalize_bytes(raw)
    assert c.normalize_bytes(once) == once


def test_non_utf8_is_rejected() -> None:
    with pytest.raises(CanonicalError):
        c.normalize_bytes("中文".encode("gbk"))


def test_normalize_text_keeps_content() -> None:
    assert c.normalize_text("\ufeff甲乙\r\n丙") == "甲乙\n丙"


def test_is_normalized() -> None:
    assert c.is_normalized("甲\n乙".encode("utf-8"))
    assert not c.is_normalized(b"a\r\nb")
    assert not c.is_normalized(c.BOM_UTF8 + b"a")


def test_normalize_file_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "x.md"
    path.write_bytes(b"a\r\nb")
    before = path.read_bytes()
    raw, norm = c.normalize_file(path)
    assert raw == before and norm == b"a\nb"
    assert path.read_bytes() == before, "normalize_file 不该写盘"


# ---- 包哈希 --------------------------------------------------------------

def test_chinese_hash_is_stable_across_calls() -> None:
    """验收项：中文包哈希重复运行一致。"""
    obj = {"名称": "示例人物", "说明": "甲、乙、丙", "nested": {"引用": ["一", "二"]}}
    values = {c.package_hash(obj) for _ in range(20)}
    assert len(values) == 1


def test_chinese_hash_is_stable_across_processes(tmp_path: Path) -> None:
    """换一个进程算，结果必须一样——否则发布后对不上批准时的哈希。"""
    code = (
        "import json,sys;"
        "from novel_system.canonical import package_hash;"
        "print(package_hash(json.loads(sys.argv[1])))"
    )
    obj = {"名称": "示例人物", "n": [1, 2, 3]}
    here = c.package_hash(obj)
    out = subprocess.run(
        [sys.executable, "-c", code, json.dumps(obj, ensure_ascii=False)],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"), "PATH": "/usr/bin:/bin"},
    )
    assert out.stdout.strip() == here


def test_key_order_does_not_matter() -> None:
    assert c.package_hash({"a": 1, "b": 2}) == c.package_hash({"b": 2, "a": 1})


def test_unicode_is_not_escaped() -> None:
    text = c.canonical_json({"名称": "示例"})
    assert "示例" in text and "\\u" not in text


def test_no_extra_whitespace() -> None:
    assert c.canonical_json({"a": [1, 2], "b": None}) == '{"a":[1,2],"b":null}'


def test_float_one_stays_one_point_zero() -> None:
    """1.0 不能退化成 1：类型变了，跨语言比对就会误判为不同内容。"""
    assert c.canonical_json({"v": 1.0}) == '{"v":1.0}'
    assert c.canonical_json({"v": 1}) == '{"v":1}'


def test_nan_and_infinity_are_rejected() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(CanonicalError):
            c.canonical_json({"v": bad})


def test_nested_nan_is_rejected() -> None:
    with pytest.raises(CanonicalError):
        c.canonical_json({"a": [{"b": float("nan")}]})


def test_non_string_key_is_rejected() -> None:
    with pytest.raises(CanonicalError):
        c.canonical_json({1: "a"})


def test_non_json_type_is_rejected() -> None:
    with pytest.raises(CanonicalError):
        c.canonical_json({"v": {1, 2}})


def test_hash_file_matches_object_hash(tmp_path: Path) -> None:
    path = tmp_path / "pkg.json"
    path.write_text('{"b": 2, "a": "中文"}', encoding="utf-8")
    assert c.hash_file_package(path) == c.package_hash({"a": "中文", "b": 2})


def test_bad_json_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{不是 json", encoding="utf-8")
    with pytest.raises(CanonicalError):
        c.hash_file_package(path)


# ---- 时间 ---------------------------------------------------------------

def test_parse_iso_requires_timezone() -> None:
    with pytest.raises(CanonicalError):
        c.parse_iso("2026-11-20T10:00:00")


def test_parse_iso_accepts_z_suffix() -> None:
    assert c.parse_iso("2026-11-20T10:00:00Z").tzinfo is not None


def test_to_utc_iso_converts_offset() -> None:
    assert c.to_utc_iso("2026-11-20T10:00:00+08:00") == "2026-11-20T02:00:00Z"


def test_utc_now_has_z_suffix() -> None:
    assert c.utc_now().endswith("Z")


# ---- 差异 ---------------------------------------------------------------

def test_diff_paths_reports_changes() -> None:
    left = {"a": 1, "b": {"c": 2}, "d": [1, 2]}
    right = {"a": 1, "b": {"c": 3}, "d": [1, 2, 3]}
    diffs = c.diff_paths(left, right)
    assert any("$.b.c" in d for d in diffs)
    assert any("长度不同" in d for d in diffs)


def test_diff_paths_silent_when_equal() -> None:
    obj = {"a": {"b": [1, "中文"]}}
    assert c.diff_paths(obj, obj) == []
