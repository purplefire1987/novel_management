"""测试共用夹具。

原则：所有数据都在 tmp_path 里现造，且全部虚构。不读取任何真实 Vault，
也不把真实作品内容写进测试——测试文件本身也是会进公仓的。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_system.cli import main

# 完全虚构的正文样本。人名、地名均不指向任何真实作品。
FICTIONAL_DRAFT = """\
---
id: CH-0001
type: chapter
title: 灰石渡的雨
status: draft
word_count: 24
---

雨落在灰石渡的石阶上，抄书人把最后一页纸压进书匣。

他没有回头。船在等，而书比船重。
"""


def init_env(root: Path, project_id: str = "novel-001", name: str = "Test Novel") -> tuple[Path, Path]:
    """建一个最小可用环境：作者 Vault + Runtime + 一个项目。"""
    vault, runtime = root / "vault", root / "runtime"
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
                project_id,
                "--name",
                name,
                "--confirm",
            ]
        )
        == 0
    )
    return vault, runtime


def project_root(vault: Path, project_id: str = "novel-001") -> Path:
    matches = sorted((vault / "10_Projects").glob(f"{project_id}*"))
    assert len(matches) == 1, f"项目目录应唯一：{matches}"
    return matches[0]


def new_task(vault: Path, runtime: Path, *extra: str, project: str | None = None) -> str:
    argv = ["task", "create", "--vault", str(vault), "--runtime", str(runtime), *extra]
    if project:
        argv += ["--project", project]
    assert main(argv) == 0
    tasks = sorted((runtime / "tasks").glob("TASK-*.yaml"))
    return tasks[-1].stem


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把用户级配置指到临时目录。

    否则测试会读到本机 `~/.config/novel-system/config.yaml`，
    在里面配了真实 Vault 的机器上，测试就会去动作者的作品。这条是硬隔离。
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    for name in ("NOVEL_VAULT", "NOVEL_RUNTIME", "NOVEL_PUBLIC_REPO"):
        monkeypatch.delenv(name, raising=False)
    # 测试 Vault 建在 tmp_path 里，而仓库根会被当成默认公仓——
    # 那会让每个用例都触发「Vault 位于公仓内」的隔离告警。给公仓也指到临时目录，
    # 让两者成为兄弟目录，才是在测系统而不是在测测试环境的摆放。
    public = tmp_path / "public-repo"
    public.mkdir(exist_ok=True)
    monkeypatch.setenv("NOVEL_PUBLIC_REPO", str(public))
    return tmp_path / "xdg-config"


@pytest.fixture
def vault_env(tmp_path: Path) -> tuple[Path, Path]:
    return init_env(tmp_path)


@pytest.fixture
def draft_md(tmp_path: Path) -> Path:
    path = tmp_path / "draft.md"
    path.write_text(FICTIONAL_DRAFT, encoding="utf-8")
    return path


@pytest.fixture
def task_factory(vault_env):
    vault, runtime = vault_env

    def _make(*extra: str, project: str | None = None) -> str:
        return new_task(vault, runtime, *extra, project=project)

    return _make


@pytest.fixture
def cli():
    """直接拿到命令入口，省得每个测试都 import。"""
    return main
