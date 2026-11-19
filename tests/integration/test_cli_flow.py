"""CLI 闭环测试：初始化 → 建项目 → 校验 → 建索引。全部使用 tmp_path 虚构数据。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_system.cli import main


@pytest.fixture()
def paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "vault", tmp_path / "runtime"


def _init_author(vault: Path, runtime: Path) -> int:
    return main(["init-author", "--vault", str(vault), "--runtime", str(runtime), "--confirm"])


def test_init_author_creates_structure(paths: tuple[Path, Path]) -> None:
    vault, runtime = paths
    assert _init_author(vault, runtime) == 0
    assert (vault / "00_System" / "Vault_Config.yaml").exists()
    assert (vault / "10_Projects").is_dir()
    assert (vault / "90_Shared_Assets").is_dir()
    assert (runtime / "index").is_dir()
    assert (runtime / "staging").is_dir()


def test_init_author_requires_confirm(paths: tuple[Path, Path]) -> None:
    vault, runtime = paths
    assert main(["init-author", "--vault", str(vault), "--runtime", str(runtime)]) == 2
    assert not vault.exists(), "未确认时不得创建任何目录"


def test_init_author_refuses_non_empty_dir(paths: tuple[Path, Path]) -> None:
    vault, runtime = paths
    vault.mkdir(parents=True)
    (vault / "existing.md").write_text("已有内容", encoding="utf-8")
    assert _init_author(vault, runtime) == 2
    assert (vault / "existing.md").read_text(encoding="utf-8") == "已有内容"


def test_init_project_creates_layout(paths: tuple[Path, Path]) -> None:
    vault, runtime = paths
    _init_author(vault, runtime)
    code = main(
        [
            "init-project",
            "--vault",
            str(vault),
            "--runtime",
            str(runtime),
            "--id",
            "novel-001",
            "--name",
            "Sample Novel",
            "--confirm",
        ]
    )
    assert code == 0
    root = vault / "10_Projects" / "novel-001_sample-novel"
    assert (root / "00_Project" / "project.yaml").exists()
    for sub in ("01_Canon", "02_Planning", "03_Manuscripts", "07_Staging"):
        assert (root / sub).is_dir(), f"缺 {sub}/"
    assert (root / "01_Canon" / "Characters").is_dir()


def test_init_project_rejects_bad_id(paths: tuple[Path, Path]) -> None:
    vault, runtime = paths
    _init_author(vault, runtime)
    code = main(
        ["init-project", "--vault", str(vault), "--runtime", str(runtime), "--id", "Bad ID!", "--confirm"]
    )
    assert code == 2


def test_validate_passes_after_init(paths: tuple[Path, Path]) -> None:
    vault, runtime = paths
    _init_author(vault, runtime)
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
            "Sample Novel",
            "--confirm",
        ]
    )
    assert main(["validate", "--vault", str(vault), "--runtime", str(runtime)]) == 0


def test_validate_fails_without_vault_structure(paths: tuple[Path, Path]) -> None:
    vault, runtime = paths
    vault.mkdir(parents=True)
    assert main(["validate", "--vault", str(vault), "--runtime", str(runtime)]) == 1


def test_rebuild_index_counts_markdown(paths: tuple[Path, Path]) -> None:
    vault, runtime = paths
    _init_author(vault, runtime)
    code = main(["rebuild-index", "--vault", str(vault), "--runtime", str(runtime)])
    assert code == 0
    index = json.loads((runtime / "index" / "index.json").read_text(encoding="utf-8"))
    assert index["count"] >= 2
    assert all(e["path"].endswith(".md") for e in index["entries"])


def test_rebuild_index_ignores_hidden_dirs(paths: tuple[Path, Path]) -> None:
    vault, runtime = paths
    _init_author(vault, runtime)
    hidden = vault / ".trash"
    hidden.mkdir()
    (hidden / "secret.md").write_text("---\nid: X\n---\n", encoding="utf-8")
    main(["rebuild-index", "--vault", str(vault), "--runtime", str(runtime)])
    index = json.loads((runtime / "index" / "index.json").read_text(encoding="utf-8"))
    assert not any(".trash" in e["path"] for e in index["entries"])


def test_doctor_fails_when_runtime_inside_vault(paths: tuple[Path, Path]) -> None:
    vault, _ = paths
    _init_author(vault, paths[1])
    inside = vault / "runtime"
    inside.mkdir()
    assert main(["doctor", "--vault", str(vault), "--runtime", str(inside)]) == 1
