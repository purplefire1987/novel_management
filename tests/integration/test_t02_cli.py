"""T02 命令行闭环：schema / package-hash / allocate-id。

重点验三件事：预演不落盘、--confirm 才写审计、退出码能区分「通过」与「不合契约」。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_management.cli import main


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path]:
    vault, runtime = tmp_path / "vault", tmp_path / "runtime"
    assert main(["init-author", "--vault", str(vault), "--runtime", str(runtime), "--confirm"]) == 0
    project = vault / "10_Projects" / "p1"
    (project / "01_Canon" / "Characters").mkdir(parents=True)
    (project / "01_Canon" / "Characters" / "CHAR-0001_甲.md").write_text(
        "---\nid: CHAR-0001\nname: 甲\n---\n正文\n", encoding="utf-8"
    )
    return vault, runtime


def _run(vault: Path, runtime: Path, *argv: str) -> int:
    return main([*argv, "--vault", str(vault), "--runtime", str(runtime), "--project", "p1"])


# ---- schema --------------------------------------------------------------

def test_schema_list_counts_all_types(env, capsys) -> None:
    vault, runtime = env
    assert _run(vault, runtime, "schema", "list") == 0
    out = capsys.readouterr().out
    assert "可校验类型" in out
    for name in ("entity", "assertion", "approval", "release_manifest", "transaction_journal"):
        assert name in out


def test_schema_check_passes_valid_file(env, tmp_path, capsys) -> None:
    vault, runtime = env
    good = tmp_path / "c.json"
    good.write_text(json.dumps({"id": "CHAR-0001", "type": "character", "name": "甲"}), encoding="utf-8")
    assert _run(vault, runtime, "schema", "check", "entity", str(good)) == 0
    assert "[OK]" in capsys.readouterr().out


def test_schema_check_rejects_unknown_field(env, tmp_path, capsys) -> None:
    vault, runtime = env
    bad = tmp_path / "c.json"
    bad.write_text(json.dumps({"id": "CHAR-0001", "type": "character", "name": "甲", "野字段": 1}), encoding="utf-8")
    assert _run(vault, runtime, "schema", "check", "entity", str(bad)) == 1
    assert "未知字段" in capsys.readouterr().out


def test_schema_check_rejects_missing_required(env, tmp_path) -> None:
    vault, runtime = env
    bad = tmp_path / "c.json"
    bad.write_text(json.dumps({"id": "CHAR-0001", "type": "character"}), encoding="utf-8")
    assert _run(vault, runtime, "schema", "check", "entity", str(bad)) == 1


def test_schema_check_unknown_type_is_usage_error(env, tmp_path) -> None:
    vault, runtime = env
    f = tmp_path / "c.json"
    f.write_text("{}", encoding="utf-8")
    assert _run(vault, runtime, "schema", "check", "dragon", str(f)) == 2


def test_schema_check_bad_json_is_usage_error(env, tmp_path) -> None:
    vault, runtime = env
    f = tmp_path / "c.json"
    f.write_text("{不是 json", encoding="utf-8")
    assert _run(vault, runtime, "schema", "check", "entity", str(f)) == 2


# ---- package-hash --------------------------------------------------------

def test_package_hash_is_stable_and_key_order_free(env, tmp_path, capsys) -> None:
    vault, runtime = env
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text('{"名称":"示例","n":1}', encoding="utf-8")
    b.write_text('{"n":1,"名称":"示例"}', encoding="utf-8")
    assert _run(vault, runtime, "package-hash", str(a)) == 0
    first = capsys.readouterr().out.strip()
    assert len(first) == 64
    assert _run(vault, runtime, "package-hash", str(b)) == 0
    assert capsys.readouterr().out.strip() == first


def test_package_hash_check_mismatch_exits_1(env, tmp_path) -> None:
    vault, runtime = env
    f = tmp_path / "a.json"
    f.write_text('{"a":1}', encoding="utf-8")
    assert _run(vault, runtime, "package-hash", str(f), "--check", "0" * 64) == 1


def test_package_hash_check_match_exits_0(env, tmp_path, capsys) -> None:
    vault, runtime = env
    f = tmp_path / "a.json"
    f.write_text('{"a":1}', encoding="utf-8")
    assert _run(vault, runtime, "package-hash", str(f)) == 0
    digest = capsys.readouterr().out.strip()
    assert _run(vault, runtime, "package-hash", str(f), "--check", digest) == 0


def test_package_hash_rejects_nan(env, tmp_path) -> None:
    vault, runtime = env
    f = tmp_path / "a.json"
    f.write_text('{"a": NaN}', encoding="utf-8")
    assert _run(vault, runtime, "package-hash", str(f)) == 2


# ---- allocate-id ---------------------------------------------------------

def test_allocate_preview_does_not_write(env, capsys) -> None:
    vault, runtime = env
    assert _run(vault, runtime, "allocate-id", "--type", "CHARACTER") == 0
    out = capsys.readouterr().out
    assert "CHAR-0002" in out and "预演" in out
    assert not (runtime / "id-allocations.jsonl").exists()


def test_allocate_confirm_writes_audit(env, capsys) -> None:
    vault, runtime = env
    assert _run(vault, runtime, "allocate-id", "--type", "CHARACTER", "--confirm", "--reason", "测试") == 0
    lines = (runtime / "id-allocations.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["ids"] == ["CHAR-0002"]
    assert row["reason"] == "测试"


def test_allocate_unknown_type_exits_2(env) -> None:
    vault, runtime = env
    assert _run(vault, runtime, "allocate-id", "--type", "DRAGON") == 2


def test_allocate_refuses_dirty_prefix(env) -> None:
    vault, runtime = env
    project = vault / "10_Projects" / "p1"
    (project / "04_Story_Assets" / "A").mkdir(parents=True)
    (project / "04_Story_Assets" / "B").mkdir(parents=True)
    for sub in ("A", "B"):
        (project / "04_Story_Assets" / sub / "ASSET-0001_x.md").write_text(
            "---\nid: ASSET-0001\nname: x\n---\n", encoding="utf-8"
        )
    assert _run(vault, runtime, "allocate-id", "--type", "ASSET") == 1


def test_allocate_json_output(env, capsys) -> None:
    vault, runtime = env
    assert _run(vault, runtime, "allocate-id", "--type", "CHAR", "--json") == 0
    out = capsys.readouterr().out.strip().splitlines()[-1]
    payload = json.loads(out)
    assert payload["ids"] == ["CHAR-0002"]
    assert payload["committed"] is False
