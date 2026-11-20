"""多项目隔离（ADR-004）。

一个 Vault 管多部作品，但默认不许串味。这里钉住四件事：
未指定项目不许写、A 项目的任务不许写 B 项目的路径、跨项目双链必须被报出来、
索引按项目可分辨。
"""

from __future__ import annotations

import json
from pathlib import Path

from novel_system.cli import main

DRAFT = "---\nid: CH-0001\ntype: chapter\ntitle: 试稿\n---\n\n虚构正文。\n"


def run(*argv: str) -> int:
    return main(list(argv))


def two_projects(tmp_path: Path) -> tuple[Path, Path]:
    vault, runtime = tmp_path / "vault", tmp_path / "runtime"
    assert run("init-author", "--vault", str(vault), "--runtime", str(runtime), "--confirm") == 0
    for pid, name in (("novel-001", "First"), ("novel-002", "Second")):
        assert (
            run(
                "init-project", "--vault", str(vault), "--runtime", str(runtime),
                "--id", pid, "--name", name, "--confirm",
            )
            == 0
        )
    return vault, runtime


def project_of(vault: Path, project_id: str) -> Path:
    matches = sorted((vault / "10_Projects").glob(f"{project_id}*"))
    assert len(matches) == 1
    return matches[0]


def make_task(vault: Path, runtime: Path, project_id: str | None) -> str:
    argv = [
        "task", "create", "--vault", str(vault), "--runtime", str(runtime),
        "--type", "draft", "--assignee", "writer",
    ]
    if project_id:
        argv += ["--project", project_id]
    assert run(*argv) == 0
    return sorted((runtime / "tasks").glob("TASK-*.yaml"))[-1].stem


def test_two_projects_are_separate_dirs(tmp_path: Path) -> None:
    vault, _ = two_projects(tmp_path)
    dirs = sorted(p.name for p in (vault / "10_Projects").iterdir() if p.is_dir())
    assert len(dirs) == 2
    assert dirs[0].startswith("novel-001") and dirs[1].startswith("novel-002")


def clear_active_project(vault: Path) -> None:
    """把 active_project 清空，模拟「配置里没指定当前项目」。"""
    import re

    path = vault / "00_System" / "Vault_Config.yaml"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'(active_project:\s*\r?\n\s*project_id:\s*)"[^"]*"', r'\1""', text)
    path.write_text(text, encoding="utf-8")


def test_task_without_project_is_rejected(tmp_path: Path) -> None:
    """多项目并存又没配 active_project 时，不许猜是哪一个。"""
    vault, runtime = two_projects(tmp_path)
    clear_active_project(vault)
    assert run(
        "task", "create", "--vault", str(vault), "--runtime", str(runtime),
        "--type", "draft", "--assignee", "writer",
    ) == 2


def test_cannot_stage_into_other_project_by_absolute_path(tmp_path: Path) -> None:
    vault, runtime = two_projects(tmp_path)
    task = make_task(vault, runtime, "novel-001")
    draft = tmp_path / "d.md"
    draft.write_text(DRAFT, encoding="utf-8")
    other = project_of(vault, "novel-002") / "03_Manuscripts" / "Drafts" / "CH-0001.md"
    assert run(
        "stage", "--vault", str(vault), "--runtime", str(runtime),
        "--project", "novel-001", "--task", task,
        "--from", str(draft), "--to", str(other), "--as", "writer",
    ) == 3
    assert not other.exists()


def test_cannot_stage_into_other_project_by_relative_path(tmp_path: Path) -> None:
    vault, runtime = two_projects(tmp_path)
    task = make_task(vault, runtime, "novel-001")
    draft = tmp_path / "d.md"
    draft.write_text(DRAFT, encoding="utf-8")
    escape = f"../{project_of(vault, 'novel-002').name}/03_Manuscripts/Drafts/CH-0001.md"
    assert run(
        "stage", "--vault", str(vault), "--runtime", str(runtime),
        "--project", "novel-001", "--task", task,
        "--from", str(draft), "--to", escape, "--as", "writer",
    ) == 3


def test_stage_into_own_project_works(tmp_path: Path) -> None:
    vault, runtime = two_projects(tmp_path)
    task = make_task(vault, runtime, "novel-001")
    draft = tmp_path / "d.md"
    draft.write_text(DRAFT, encoding="utf-8")
    assert run(
        "stage", "--vault", str(vault), "--runtime", str(runtime),
        "--project", "novel-001", "--task", task,
        "--from", str(draft), "--to", "03_Manuscripts/Drafts/CH-0001.md", "--as", "writer",
    ) == 0
    assert (project_of(vault, "novel-001") / "07_Staging" / task).is_dir()
    assert not (project_of(vault, "novel-002") / "07_Staging" / task).exists()


def test_cross_project_link_is_reported(tmp_path: Path, capsys) -> None:
    vault, runtime = two_projects(tmp_path)
    other = project_of(vault, "novel-002")
    (other / "01_Canon" / "Characters").mkdir(parents=True, exist_ok=True)
    (other / "01_Canon" / "Characters" / "CHAR-0001.md").write_text(
        "---\nid: CHAR-0001\ntype: character\nname: 林砚\n---\n", encoding="utf-8"
    )

    mine = project_of(vault, "novel-001")
    (mine / "02_Planning" / "note.md").write_text(
        f"引用别的项目：[[10_Projects/{other.name}/01_Canon/Characters/CHAR-0001.md]]\n",
        encoding="utf-8",
    )
    code = run("validate", "--vault", str(vault), "--runtime", str(runtime), "--project", "novel-001")
    out = capsys.readouterr().out
    assert code == 1
    assert "跨项目引用" in out


def test_index_entries_carry_project(tmp_path: Path) -> None:
    vault, runtime = two_projects(tmp_path)
    for pid in ("novel-001", "novel-002"):
        target = project_of(vault, pid) / "01_Canon" / "Characters"
        target.mkdir(parents=True, exist_ok=True)
        (target / "CHAR-0001.md").write_text(
            "---\nid: CHAR-0001\ntype: character\nname: 林砚\n---\n", encoding="utf-8"
        )
    assert run("rebuild-index", "--vault", str(vault), "--runtime", str(runtime)) == 0
    index = json.loads((runtime / "index" / "index.json").read_text(encoding="utf-8"))
    projects = {e["project"] for e in index["entries"] if e.get("id") == "CHAR-0001"}
    assert len(projects) == 2, f"两个项目各自有一条，实得 {projects}"
