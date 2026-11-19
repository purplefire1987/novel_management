"""路径越界：符号链接、`..`、绝对路径逃逸都必须被拒绝。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from novel_system import config as cfg_mod


def test_dotdot_in_any_position_rejected() -> None:
    for raw in ("../x", "a/../../b", "/tmp/../../etc/passwd"):
        with pytest.raises(cfg_mod.ConfigError):
            cfg_mod.resolve_path(raw)


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = vault / "link"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("当前平台不支持创建符号链接")

    with pytest.raises(cfg_mod.ConfigError, match="越界"):
        cfg_mod.ensure_inside(link / "file.md", vault)


def test_sibling_prefix_not_confused(tmp_path: Path) -> None:
    """`/vault-other` 不能被当成 `/vault` 的子目录。"""
    vault = tmp_path / "vault"
    sibling = tmp_path / "vault-other"
    vault.mkdir()
    sibling.mkdir()
    with pytest.raises(cfg_mod.ConfigError, match="越界"):
        cfg_mod.ensure_inside(sibling, vault)


def test_empty_path_rejected() -> None:
    with pytest.raises(cfg_mod.ConfigError, match="为空"):
        cfg_mod.resolve_path("")
