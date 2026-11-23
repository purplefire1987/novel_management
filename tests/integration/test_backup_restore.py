"""备份与恢复：复制要校验，恢复要不覆盖。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from novel_management.cli import main


def _env(tmp_path: Path) -> tuple[Path, Path, Path]:
    vault, runtime = tmp_path / "vault", tmp_path / "runtime"
    assert main(["init-author", "--vault", str(vault), "--runtime", str(runtime), "--confirm"]) == 0
    assert (
        main(
            [
                "init-project",
                "--vault",
                str(vault),
                "--runtime",
                str(runtime),
                "--id",
                "novel-001",
                "--name",
                "Test Novel",
                "--confirm",
            ]
        )
        == 0
    )
    return vault, runtime, vault / "10_Projects" / "novel-001_test-novel"


def _backup_id(runtime: Path) -> str:
    ids = sorted(p.name for p in (runtime / "backups").glob("BK-*"))
    assert ids
    return ids[-1]


def test_backup_writes_manifest_and_verifies(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime, _ = _env(tmp_path)
    assert main(["backup", "--vault", str(vault), "--runtime", str(runtime), "--as", "author", "--label", "first"]) == 0
    backup_id = _backup_id(runtime)
    root = runtime / "backups" / backup_id
    assert (root / "manifest.csv").exists() and (root / "meta.json").exists()

    meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    assert meta["verified"] is True
    assert meta["files"] > 0

    with (root / "manifest.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == meta["files"]
    assert all(len(row["sha256"]) == 64 for row in rows)
    capsys.readouterr()


def test_backup_list_shows_entries(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime, _ = _env(tmp_path)
    main(["backup", "--vault", str(vault), "--runtime", str(runtime), "--as", "author"])
    capsys.readouterr()
    assert main(["backup", "--vault", str(vault), "--runtime", str(runtime), "--list"]) == 0
    out = capsys.readouterr().out
    assert "BK-" in out and "verified=True" in out


def _restore_target(tmp_path: Path) -> tuple[Path, Path]:
    """另建一个空 Vault 当恢复目标。

    不靠删除/改名原文件来制造「缺失」——受限环境里这些操作可能被拦，
    而「恢复到一个没有这份文件的 Vault」既等价又不依赖删除权限。
    """
    other = tmp_path / "vault2"
    runtime2 = tmp_path / "runtime2"
    assert main(["init-author", "--vault", str(other), "--runtime", str(runtime2), "--confirm"]) == 0
    return other, other / "10_Projects" / "novel-001_test-novel" / "03_Manuscripts" / "Drafts" / "CH-0001.md"


def test_restore_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime, project = _env(tmp_path)
    target = project / "03_Manuscripts" / "Drafts" / "CH-0001.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("正文\n", encoding="utf-8")
    main(["backup", "--vault", str(vault), "--runtime", str(runtime), "--as", "author"])
    backup_id = _backup_id(runtime)

    other, other_target = _restore_target(tmp_path)
    assert (
        main(["restore", "--vault", str(other), "--runtime", str(runtime), "--backup-id", backup_id, "--dry-run"]) == 0
    )
    assert not other_target.exists(), "dry-run 不得写盘"
    capsys.readouterr()


def test_restore_confirm_writes_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime, project = _env(tmp_path)
    target = project / "03_Manuscripts" / "Drafts" / "CH-0001.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("正文\n", encoding="utf-8")
    main(["backup", "--vault", str(vault), "--runtime", str(runtime), "--as", "author"])
    backup_id = _backup_id(runtime)

    other, other_target = _restore_target(tmp_path)
    code = main(
        [
            "restore",
            "--vault",
            str(other),
            "--runtime",
            str(runtime),
            "--backup-id",
            backup_id,
            "--confirm",
            "--as",
            "author",
        ]
    )
    assert code == 0
    assert other_target.read_text(encoding="utf-8") == "正文\n"
    capsys.readouterr()


def test_restore_keeps_conflicting_file_by_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime, project = _env(tmp_path)
    target = project / "03_Manuscripts" / "Drafts" / "CH-0001.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("备份时的版本\n", encoding="utf-8")
    main(["backup", "--vault", str(vault), "--runtime", str(runtime), "--as", "author"])
    backup_id = _backup_id(runtime)

    target.write_text("备份之后作者又改了\n", encoding="utf-8")
    code = main(
        [
            "restore",
            "--vault",
            str(vault),
            "--runtime",
            str(runtime),
            "--backup-id",
            backup_id,
            "--confirm",
            "--as",
            "author",
        ]
    )
    assert code == 0
    assert target.read_text(encoding="utf-8") == "备份之后作者又改了\n", "默认不得覆盖现存文件"
    capsys.readouterr()


def test_restore_overwrite_keeps_undo_copy(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime, project = _env(tmp_path)
    target = project / "03_Manuscripts" / "Drafts" / "CH-0001.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("备份时的版本\n", encoding="utf-8")
    main(["backup", "--vault", str(vault), "--runtime", str(runtime), "--as", "author"])
    backup_id = _backup_id(runtime)
    target.write_text("后来的版本\n", encoding="utf-8")

    code = main(
        [
            "restore",
            "--vault",
            str(vault),
            "--runtime",
            str(runtime),
            "--backup-id",
            backup_id,
            "--confirm",
            "--overwrite",
            "--as",
            "author",
        ]
    )
    assert code == 0
    assert target.read_text(encoding="utf-8") == "备份时的版本\n"
    undo = list((runtime / "restore-undo").rglob("CH-0001.md"))
    assert undo and undo[0].read_text(encoding="utf-8") == "后来的版本\n"
    capsys.readouterr()


def test_restore_requires_confirm(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime, _ = _env(tmp_path)
    main(["backup", "--vault", str(vault), "--runtime", str(runtime), "--as", "author"])
    backup_id = _backup_id(runtime)
    assert main(["restore", "--vault", str(vault), "--runtime", str(runtime), "--backup-id", backup_id]) == 0
    out = capsys.readouterr().out
    assert "预演结束" in out
