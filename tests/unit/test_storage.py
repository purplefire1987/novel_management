"""文件层与配置兼容层：这些是其余命令的地基。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from novel_management import config as cfg_mod
from novel_management.domain.errors import ConflictError
from novel_management.infrastructure.filesystem.storage import dump_yaml, iter_files, sha256_bytes, sha256_file, write_new


def test_dump_yaml_roundtrip_with_pyyaml() -> None:
    data = {
        "task_id": "TASK-000001",
        "project_id": "novel-001",
        "write_mode": "staging",
        "input_files": ["a.md", "b.md"],
        "notes": "带 中文 与 \"引号\"",
    }
    parsed = cfg_mod.parse_yaml(dump_yaml(data))
    assert parsed["task_id"] == "TASK-000001"
    assert parsed["input_files"] == ["a.md", "b.md"]
    assert parsed["notes"] == data["notes"]


def test_dump_yaml_readable_without_pyyaml(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 PyYAML 时必须退回内置解析器——任务记录不能因为缺依赖就读不出来。"""
    text = dump_yaml(
        {
            "task_id": "TASK-000002",
            "project_id": "novel-001",
            "status": "in_progress",
            "input_files": ["x.md"],
            "note": "",
        }
    )
    monkeypatch.setitem(sys.modules, "yaml", None)
    parsed = cfg_mod.parse_yaml(text)
    assert parsed["task_id"] == "TASK-000002"
    assert parsed["status"] == "in_progress"
    assert parsed["input_files"] == ["x.md"]


def test_write_new_refuses_existing(tmp_path: Path) -> None:
    target = tmp_path / "a.md"
    write_new(target, b"first")
    with pytest.raises(ConflictError):
        write_new(target, b"second")
    assert target.read_bytes() == b"first"


def test_sha256_stable(tmp_path: Path) -> None:
    target = tmp_path / "a.bin"
    target.write_bytes(b"abc")
    assert sha256_file(target) == sha256_bytes(b"abc")


def test_iter_files_skips_hidden_and_git(tmp_path: Path) -> None:
    (tmp_path / "keep.md").write_text("x", encoding="utf-8")
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_text("z", encoding="utf-8")

    rels = [p.relative_to(tmp_path).as_posix() for p in iter_files(tmp_path)]
    assert rels == ["keep.md"]


def test_iter_files_can_include_git(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    rels = {p.relative_to(tmp_path).as_posix() for p in iter_files(tmp_path, include_git=True)}
    assert ".git/HEAD" in rels


def test_vault_config_layouts_alias(tmp_path: Path) -> None:
    """V4.0 交付的 Vault_Config 用 layouts.* / runtime_root，必须认得。"""
    vault = tmp_path / "vault"
    (vault / "00_System").mkdir(parents=True)
    (vault / "00_System" / "Vault_Config.yaml").write_text(
        "\n".join(
            [
                "schema_version: '4.0.0'",
                "layouts:",
                "  system: 00_System",
                "  projects: 10_Projects",
                "  shared_assets: 90_Shared_Assets",
                "  archive: 99_Archive",
                f"runtime_root: {tmp_path / 'runtime'}",
            ]
        ),
        encoding="utf-8",
    )
    cfg = cfg_mod.load(vault=str(vault))
    assert cfg.layout["projects_dir"] == "10_Projects"
    assert cfg.runtime == (tmp_path / "runtime").resolve()


def test_vault_config_layouts_can_be_overridden(tmp_path: Path) -> None:
    """显式 layout.* 优先于 layouts.* 折算值。"""
    vault = tmp_path / "vault"
    (vault / "00_System").mkdir(parents=True)
    (vault / "00_System" / "Vault_Config.yaml").write_text(
        "\n".join(
            [
                "layouts:",
                "  projects: 10_Projects",
                "layout:",
                "  projects_dir: 20_Novels",
            ]
        ),
        encoding="utf-8",
    )
    cfg = cfg_mod.load(vault=str(vault))
    assert cfg.layout["projects_dir"] == "20_Novels"


def test_resolve_project_id_prefers_explicit(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    base = vault / "10_Projects"
    for name, pid in (("a_one", "novel-a"), ("b_two", "novel-b")):
        (base / name / "00_Project").mkdir(parents=True)
        (base / name / "00_Project" / "project.yaml").write_text(f'project_id: "{pid}"\n', encoding="utf-8")
    cfg = cfg_mod.load(vault=str(vault))
    assert cfg.resolve_project_id("novel-b") == "novel-b"
    with pytest.raises(cfg_mod.ConfigError, match="未指定项目"):
        cfg.resolve_project_id()


def test_resolve_project_id_never_guesses_single_project(tmp_path: Path) -> None:
    """单项目时也不许推断——等第二个项目建起来，同一条命令的语义就变了。"""
    vault = tmp_path / "vault"
    base = vault / "10_Projects" / "only_one" / "00_Project"
    base.mkdir(parents=True)
    (base / "project.yaml").write_text('project_id: "novel-only"\n', encoding="utf-8")
    cfg = cfg_mod.load(vault=str(vault))
    with pytest.raises(cfg_mod.ConfigError, match="未指定项目"):
        cfg.resolve_project_id()
    assert cfg.resolve_project_id("novel-only") == "novel-only"


def test_active_project_in_config_is_honored(tmp_path: Path) -> None:
    """日常不带 --project 靠的是配置里写死 active_project，不是靠猜。"""
    vault = tmp_path / "vault"
    base = vault / "10_Projects" / "only_one" / "00_Project"
    base.mkdir(parents=True)
    (base / "project.yaml").write_text('project_id: "novel-only"\n', encoding="utf-8")
    (vault / "00_System").mkdir(parents=True)
    (vault / "00_System" / "Vault_Config.yaml").write_text(
        'active_project:\n  project_id: "novel-only"\n', encoding="utf-8"
    )
    cfg = cfg_mod.load(vault=str(vault))
    assert cfg.resolve_project_id() == "novel-only"
