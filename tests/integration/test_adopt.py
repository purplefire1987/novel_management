"""adopt：登记作者手改，不覆盖、不回滚。"""

from __future__ import annotations

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
    project = vault / "10_Projects" / "novel-001_test-novel"
    return vault, runtime, project


def test_first_adopt_builds_baseline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime, project = _env(tmp_path)
    (project / "01_Canon" / "Characters" / "CHAR-0001.md").write_text("---\nid: CHAR-0001\n---\n", encoding="utf-8")
    assert main(["adopt", "--vault", str(vault), "--runtime", str(runtime), "--as", "author"]) == 0
    baseline = runtime / "authorship" / "novel-001.json"
    assert baseline.exists()
    data = json.loads(baseline.read_text(encoding="utf-8"))
    assert "01_Canon/Characters/CHAR-0001.md" in data["files"]
    capsys.readouterr()


def test_adopt_reports_and_keeps_author_edit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime, project = _env(tmp_path)
    target = project / "03_Manuscripts" / "Drafts" / "CH-0001.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("原稿\n", encoding="utf-8")
    main(["adopt", "--vault", str(vault), "--runtime", str(runtime), "--as", "author"])

    target.write_text("作者改的\n", encoding="utf-8")
    assert main(["adopt", "--vault", str(vault), "--runtime", str(runtime), "--as", "author", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "修改" in out and "CH-0001.md" in out

    assert main(["adopt", "--vault", str(vault), "--runtime", str(runtime), "--as", "author"]) == 0
    # 作者的内容必须原样保留——adopt 只登记，不回滚
    assert target.read_text(encoding="utf-8") == "作者改的\n"
    capsys.readouterr()


def test_dry_run_does_not_touch_baseline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime, project = _env(tmp_path)
    (project / "README.md").write_text("改过的说明\n", encoding="utf-8")
    main(["adopt", "--vault", str(vault), "--runtime", str(runtime), "--as", "author"])
    before = (runtime / "authorship" / "novel-001.json").read_text(encoding="utf-8")

    (project / "README.md").write_text("再改一次\n", encoding="utf-8")
    assert main(["adopt", "--vault", str(vault), "--runtime", str(runtime), "--as", "author", "--dry-run"]) == 0
    after = (runtime / "authorship" / "novel-001.json").read_text(encoding="utf-8")
    assert before == after
    capsys.readouterr()


def test_only_author_can_adopt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime, _ = _env(tmp_path)
    assert main(["adopt", "--vault", str(vault), "--runtime", str(runtime), "--as", "writer"]) == 3
    capsys.readouterr()
