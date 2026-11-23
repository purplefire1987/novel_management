"""配置解析与路径安全的单元测试。只用虚构路径。"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_management import config as cfg_mod


def test_defaults_are_loaded() -> None:
    cfg = cfg_mod.load()
    assert cfg.layout["projects_dir"] == "10_Projects"
    assert cfg.layout["vault_system_dir"] == "00_System"
    assert "01_Canon" in cfg.layout["project_subdirs"]


def test_privacy_defaults_all_closed() -> None:
    cfg = cfg_mod.load()
    for key in (
        "private_remote_allowed",
        "public_repo_may_read_vault",
        "cross_project_access",
        "agent_canon_write",
    ):
        assert cfg.privacy[key] is False, f"{key} 默认必须关闭"


def test_no_absolute_path_in_defaults() -> None:
    """公共默认配置不得含任何绝对路径。"""
    text = cfg_mod.DEFAULTS_PATH.read_text(encoding="utf-8")
    assert "/Users/" not in text
    assert "C:\\" not in text


def test_resolve_path_expands_home() -> None:
    got = cfg_mod.resolve_path("~/novel-example")
    assert got == (Path.home() / "novel-example").resolve()


def test_resolve_path_rejects_parent_escape() -> None:
    with pytest.raises(cfg_mod.ConfigError, match="不得含"):
        cfg_mod.resolve_path("../outside")


def test_resolve_path_requires_base_for_relative() -> None:
    with pytest.raises(cfg_mod.ConfigError, match="相对路径"):
        cfg_mod.resolve_path("relative/path")


def test_ensure_inside_accepts_child(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    child = root / "10_Projects" / "p1"
    child.mkdir(parents=True)
    assert cfg_mod.ensure_inside(child, root) == child.resolve()


def test_ensure_inside_rejects_outside(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    other = tmp_path / "elsewhere"
    root.mkdir()
    other.mkdir()
    with pytest.raises(cfg_mod.ConfigError, match="越界"):
        cfg_mod.ensure_inside(other, root)


def test_missing_vault_raises_actionable_error() -> None:
    cfg = cfg_mod.Config(
        public_repo=Path("/tmp/repo"),
        author_vault=None,
        runtime=None,
        project_id="",
        layout=dict(cfg_mod.read_yaml(cfg_mod.DEFAULTS_PATH).get("layout") or {}),
        privacy={},
    )
    with pytest.raises(cfg_mod.ConfigError, match="NOVEL_VAULT"):
        cfg.require_vault()
