"""CLI 闭环测试：初始化 → 建项目 → 校验 → 建索引。全部使用 tmp_path 虚构数据。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_management.cli import main


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


def _tree_digest(root: Path) -> dict[str, str]:
    import hashlib

    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def test_repeated_init_leaves_every_byte_untouched(paths: tuple[Path, Path]) -> None:
    """T01 验收「重复初始化不破坏数据」，用逐文件 sha256 作判据。

    只看「退出码非 0」不够——命令可以报了错又顺手改了一个文件。
    这里比的是整棵树的内容指纹。
    """
    vault, runtime = paths
    assert _init_author(vault, runtime) == 0
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
                "试验作品",
                "--confirm",
            ]
        )
        == 0
    )
    # 项目目录名是 `<id>_<slug>`，不是裸 id。
    matches = sorted((vault / "10_Projects").glob("novel-001*"))
    assert len(matches) == 1, f"项目目录应唯一：{matches}"
    project = matches[0]
    (project / "01_Canon" / "Characters" / "CHAR-0001.md").write_text(
        "---\nid: CHAR-0001\ntype: character\n---\n\n作者写的正文。\n", encoding="utf-8"
    )

    before = _tree_digest(vault)
    assert _init_author(vault, runtime) != 0
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
                "试验作品",
                "--confirm",
            ]
        )
        != 0
    )
    assert _tree_digest(vault) == before


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


def test_cli_accepts_relative_vault_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """人在终端敲 `novel --vault vault` 是常事，相对路径按 cwd 解析。"""
    monkeypatch.chdir(tmp_path)
    assert main(["init-author", "--vault", "vault", "--runtime", "runtime", "--confirm"]) == 0
    assert (tmp_path / "vault" / "00_System" / "Vault_Config.yaml").exists()
    assert (tmp_path / "runtime" / "index").is_dir()


# --------------------------------------------------------------------------
# 素材库查重（R-04 的回归测试）
#
# 素材按分类分目录存放，ID 却是「全库连续编号，不按分类分段」，
# 所以查重必须跨子目录一起看。只扫 Canon 六类会让重号永远隐形。
# --------------------------------------------------------------------------

def _new_project(vault: Path, runtime: Path) -> Path:
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
    return vault / "10_Projects" / "novel-001_sample-novel"


def _asset(root: Path, sub: str, filename: str, raw_id: str) -> None:
    directory = root / "04_Story_Assets" / sub
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(
        f"---\nid: {raw_id}\ntype: story_asset\n---\n\n# 示例素材\n", encoding="utf-8"
    )


def test_validate_detects_asset_id_collision_across_subdirs(paths: tuple[Path, Path]) -> None:
    vault, runtime = paths
    root = _new_project(vault, runtime)
    _asset(root, "Battles", "ASSET-0001_示例甲.md", "ASSET-0001")
    _asset(root, "Settings", "ASSET-0001_示例乙.md", "ASSET-0001")
    assert main(["validate", "--vault", str(vault), "--runtime", str(runtime)]) == 1


def test_validate_ignores_asset_id_placeholder(paths: tuple[Path, Path]) -> None:
    """模板里的 `id: {{id}}` 是占位符，不是身份，不能算重号。"""
    vault, runtime = paths
    root = _new_project(vault, runtime)
    _asset(root, "Templates", "素材卡模板.md", '"{{id}}"')
    _asset(root, "Templates", "剧情卡模板.md", '"{{id}}"')
    assert main(["validate", "--vault", str(vault), "--runtime", str(runtime)]) == 0


def test_validate_passes_when_asset_ids_are_unique(paths: tuple[Path, Path]) -> None:
    vault, runtime = paths
    root = _new_project(vault, runtime)
    _asset(root, "Battles", "ASSET-0001_示例甲.md", "ASSET-0001")
    _asset(root, "Settings", "ASSET-0002_示例乙.md", "ASSET-0002")
    assert main(["validate", "--vault", str(vault), "--runtime", str(runtime)]) == 0
