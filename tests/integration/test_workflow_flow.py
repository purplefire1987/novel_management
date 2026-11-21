"""V4.0 §5.3 主链：task → stage → diff → approve → publish → adopt。

全部在 tmp_path 的虚构 Vault 上跑，不碰任何真实作品内容。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_system.cli import main
from novel_system.storage import sha256_file

TARGET = "03_Manuscripts/Drafts/CH-0001.md"


def _env(tmp_path: Path) -> tuple[Path, Path]:
    vault, runtime = tmp_path / "vault", tmp_path / "runtime"
    assert main(["init-author", "--vault", str(vault), "--runtime", str(runtime), "--confirm"]) == 0
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
            "Test Novel",
            "--confirm",
        ]
    )
    assert code == 0
    return vault, runtime


def _new_task(vault: Path, runtime: Path, task_type: str = "draft", assignee: str = "writer") -> str:
    code = main(
        [
            "task",
            "create",
            "--vault",
            str(vault),
            "--runtime",
            str(runtime),
            "--type",
            task_type,
            "--assignee",
            assignee,
            "--as",
            assignee,
        ]
    )
    assert code == 0
    tasks = sorted((runtime / "tasks").glob("TASK-*.yaml"))
    return tasks[-1].stem


def _stage(vault: Path, runtime: Path, tmp_path: Path, task: str, target: str, text: str = "第一章\n") -> int:
    draft = tmp_path / f"{task}-draft.md"
    draft.write_text(text, encoding="utf-8")
    return main(
        [
            "stage",
            "--vault",
            str(vault),
            "--runtime",
            str(runtime),
            "--task",
            task,
            "--from",
            str(draft),
            "--to",
            target,
            "--as",
            "writer",
        ]
    )


def _release_ids(runtime: Path) -> list[str]:
    return sorted(p.name for p in (runtime / "releases").glob("REL-*"))


def _prepare(vault: Path, runtime: Path, task: str, *, expect: int = 0) -> str:
    """准备发布计划。T06 之后 publish 必须基于「已准备且已批准」的计划。"""
    code = main(
        ["release", "prepare", "--vault", str(vault), "--runtime", str(runtime), "--task", task, "--as", "author"]
    )
    assert code == expect
    return _release_ids(runtime)[-1] if code == 0 else ""


def _approve_plan(vault: Path, runtime: Path, release_id: str) -> None:
    # plan.json 就是规范形式落盘的，文件哈希 == 计划哈希，不必再解析一遍
    digest = sha256_file(runtime / "releases" / release_id / "plan.json")
    code = main(
        [
            "approve",
            "release",
            release_id,
            "--hash",
            digest,
            "--as",
            "author",
            "--vault",
            str(vault),
            "--runtime",
            str(runtime),
        ]
    )
    assert code == 0


def _publish(vault: Path, runtime: Path, task: str, *extra: str) -> int:
    release_id = _prepare(vault, runtime, task)
    _approve_plan(vault, runtime, release_id)
    return main(
        [
            "publish",
            "--vault",
            str(vault),
            "--runtime",
            str(runtime),
            "--task",
            task,
            "--as",
            "author",
            "--confirm",
            *extra,
        ]
    )


def test_stage_writes_into_task_area(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime = _env(tmp_path)
    task = _new_task(vault, runtime)
    assert _stage(vault, runtime, tmp_path, task, TARGET) == 0
    staged = vault / "10_Projects" / "novel-001_test-novel" / "07_Staging" / task / "drafts" / TARGET
    assert staged.exists()
    # 正式区一个字都不该动
    assert not (vault / "10_Projects" / "novel-001_test-novel" / TARGET).exists()
    capsys.readouterr()


def test_stage_refuses_duplicate_without_replace(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime = _env(tmp_path)
    task = _new_task(vault, runtime)
    assert _stage(vault, runtime, tmp_path, task, TARGET) == 0
    assert _stage(vault, runtime, tmp_path, task, TARGET, "改过的正文\n") == 1
    capsys.readouterr()


def test_stage_rejects_target_outside_formal_areas(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime = _env(tmp_path)
    task = _new_task(vault, runtime)
    assert _stage(vault, runtime, tmp_path, task, "00_Project/project.yaml") == 3
    capsys.readouterr()


def test_stage_rejects_path_escape(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime = _env(tmp_path)
    task = _new_task(vault, runtime)
    assert _stage(vault, runtime, tmp_path, task, "../outside.md") == 3
    capsys.readouterr()


def test_publish_requires_approval(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """没有产物批准就准备不了计划；没有计划就发布不了。两道门都必须关着。"""
    vault, runtime = _env(tmp_path)
    task = _new_task(vault, runtime)
    _stage(vault, runtime, tmp_path, task, TARGET)
    # 产物未批准 → 准备计划被拒（4 = 审批失败）
    _prepare(vault, runtime, task, expect=4)
    # 没有计划 → publish 拒绝，并给出该走的三步
    code = main(
        ["publish", "--vault", str(vault), "--runtime", str(runtime), "--task", task, "--as", "author", "--confirm"]
    )
    assert code == 2
    assert "release prepare" in capsys.readouterr().out
    capsys.readouterr()


def test_full_chain_publishes_content(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime = _env(tmp_path)
    task = _new_task(vault, runtime)
    assert _stage(vault, runtime, tmp_path, task, TARGET, "第一章 正文\n") == 0
    assert main(["diff", "--vault", str(vault), "--runtime", str(runtime), "--task", task]) == 0
    assert main(["approve", "--vault", str(vault), "--runtime", str(runtime), "--task", task, "--as", "author"]) == 0
    assert _publish(vault, runtime, task) == 0
    published = vault / "10_Projects" / "novel-001_test-novel" / TARGET
    assert published.read_text(encoding="utf-8") == "第一章 正文\n"
    capsys.readouterr()


def test_publish_twice_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """任务已发布过 → 拒绝。发布本身可以幂等重试，但「再发一次新内容」必须走新任务。"""
    vault, runtime = _env(tmp_path)
    task = _new_task(vault, runtime)
    _stage(vault, runtime, tmp_path, task, TARGET)
    main(["approve", "--vault", str(vault), "--runtime", str(runtime), "--task", task, "--as", "author"])
    assert _publish(vault, runtime, task) == 0
    args = ["publish", "--vault", str(vault), "--runtime", str(runtime), "--task", task, "--as", "author", "--confirm"]
    assert main(args) == 1
    assert "已经发布过" in capsys.readouterr().out
    capsys.readouterr()


def test_publish_archives_previous_version(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime = _env(tmp_path)
    project = vault / "10_Projects" / "novel-001_test-novel"
    formal = project / TARGET
    formal.parent.mkdir(parents=True, exist_ok=True)
    formal.write_text("旧版本\n", encoding="utf-8")

    task = _new_task(vault, runtime)
    _stage(vault, runtime, tmp_path, task, TARGET, "新版本\n")
    main(["approve", "--vault", str(vault), "--runtime", str(runtime), "--task", task, "--as", "author"])
    assert _publish(vault, runtime, task) == 0

    assert formal.read_text(encoding="utf-8") == "新版本\n"
    # 归档位置从布局配置取，不写死目录名
    archived = list((project / "90_Archive" / "publish_archive").rglob("CH-0001.md"))
    assert archived, "旧版没有归档，回退就无据可依"
    assert any(path.read_text(encoding="utf-8") == "旧版本\n" for path in archived)
    capsys.readouterr()


def test_canon_publish_needs_second_confirmation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime = _env(tmp_path)
    task = _new_task(vault, runtime, "memory_update", "memory_keeper")
    canon = "01_Canon/Characters/CHAR-0001.md"
    assert _stage(vault, runtime, tmp_path, task, canon, "---\nid: CHAR-0001\n---\n档案\n") == 0
    main(["approve", "--vault", str(vault), "--runtime", str(runtime), "--task", task, "--as", "author"])
    release_id = _prepare(vault, runtime, task)
    _approve_plan(vault, runtime, release_id)
    base = ["publish", "--vault", str(vault), "--runtime", str(runtime), "--task", task, "--as", "author", "--confirm"]
    assert main(base) == 3
    assert main(base + ["--confirm-canon"]) == 0
    capsys.readouterr()


def test_tampered_staging_invalidates_approval(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime = _env(tmp_path)
    task = _new_task(vault, runtime)
    _stage(vault, runtime, tmp_path, task, TARGET)
    main(["approve", "--vault", str(vault), "--runtime", str(runtime), "--task", task, "--as", "author"])
    release_id = _prepare(vault, runtime, task)
    _approve_plan(vault, runtime, release_id)
    staged = vault / "10_Projects" / "novel-001_test-novel" / "07_Staging" / task / "drafts" / TARGET
    staged.write_text("批准后被换了\n", encoding="utf-8")
    code = main(
        ["publish", "--vault", str(vault), "--runtime", str(runtime), "--task", task, "--as", "author", "--confirm"]
    )
    assert code == 4
    capsys.readouterr()


def test_diff_reports_conflict_when_author_edited(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime = _env(tmp_path)
    project = vault / "10_Projects" / "novel-001_test-novel"
    formal = project / TARGET
    formal.parent.mkdir(parents=True, exist_ok=True)
    formal.write_text("旧版本\n", encoding="utf-8")

    task = _new_task(vault, runtime)
    _stage(vault, runtime, tmp_path, task, TARGET, "提案\n")
    # staging 生成后作者手改了正式文件
    formal.write_text("作者手改\n", encoding="utf-8")

    assert main(["diff", "--vault", str(vault), "--runtime", str(runtime), "--task", task]) == 1
    assert main(["approve", "--vault", str(vault), "--runtime", str(runtime), "--task", task, "--as", "author"]) == 2
    capsys.readouterr()


def test_task_inspect_shows_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime = _env(tmp_path)
    task = _new_task(vault, runtime)
    _stage(vault, runtime, tmp_path, task, TARGET)
    assert main(["task", "inspect", "--vault", str(vault), "--runtime", str(runtime), task]) == 0
    out = capsys.readouterr().out
    assert task in out and TARGET in out


def test_task_rejects_unknown_type(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime = _env(tmp_path)
    code = main(
        ["task", "create", "--vault", str(vault), "--runtime", str(runtime), "--type", "nonsense", "--as", "writer"]
    )
    assert code == 2
    capsys.readouterr()


def test_operation_log_records_writes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    vault, runtime = _env(tmp_path)
    task = _new_task(vault, runtime)
    _stage(vault, runtime, tmp_path, task, TARGET)
    log = runtime / "logs" / "ops.jsonl"
    assert log.exists()
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    commands = {entry["command"] for entry in entries}
    assert "stage" in commands and "task create" in commands
    assert all("op_id" in entry and "at" in entry and "actor" in entry for entry in entries)
    capsys.readouterr()
