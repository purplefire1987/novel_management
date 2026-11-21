"""T05 命令行闭环与三条验收。

验收对照（PRD §23 T05）：
- 批准后改一个字节即失效 → `test_approval_goes_stale_after_one_byte_change`
- BLOCKER 不能豁免绕过 → `test_blocker_blocks_approval_and_waiver`
- RETCON 有独立审批路径 → `test_retcon_takes_its_own_path`
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_system.cli import main

TARGET = "01_Canon/Characters/CHAR-0001.md"
CHAPTER_TARGET = "03_Manuscripts/Drafts/CH-0001.md"


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path, Path]:
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
    root = sorted((vault / "10_Projects").glob("novel-001*"))[0]
    return vault, runtime, root


def _run(vault: Path, runtime: Path, *args: str) -> int:
    return main([*args, "--vault", str(vault), "--runtime", str(runtime)])


def _task(vault: Path, runtime: Path, task_type: str = "draft") -> str:
    assert _run(vault, runtime, "task", "create", "--type", task_type, "--assignee", "writer", "--as", "writer") == 0
    return sorted((runtime / "tasks").glob("TASK-*.yaml"))[-1].stem


def _stage(vault: Path, runtime: Path, tmp_path: Path, task: str, target: str, text: str) -> None:
    draft = tmp_path / "draft.md"
    draft.write_text(text, encoding="utf-8")
    assert _run(vault, runtime, "stage", "--task", task, "--from", str(draft), "--to", target, "--as", "writer") == 0


def _inspect(vault: Path, runtime: Path, capsys, candidate: str) -> dict:
    capsys.readouterr()
    assert _run(vault, runtime, "inspect", candidate, "--json") == 0
    return json.loads(capsys.readouterr().out)


def test_inspect_reports_hash_diff_risk_scope(env, tmp_path, capsys):
    vault, runtime, root = env
    task = _task(vault, runtime)
    _stage(vault, runtime, tmp_path, task, TARGET, "---\nid: CHAR-0001\ntype: character\n---\n\n新版本\n")
    payload = _inspect(vault, runtime, capsys, task)

    assert payload["candidate"] == task
    assert len(payload["hash"]) == 64
    assert payload["files"][0]["diff"] == "added"
    assert payload["risk"] == "HIGH"          # Canon 写入
    assert payload["scope"] == ["CANON_FILE"]
    assert payload["blocking_issues"] == []
    assert payload["approval"]["status"] == "NONE"
    assert payload["source"]["task_type"] == "draft"


def test_approval_goes_stale_after_one_byte_change(env, tmp_path, capsys):
    """验收 1：批准后改一个字节即失效——不是「提示一下」，是发布直接拒绝。"""
    vault, runtime, root = env
    task = _task(vault, runtime)
    _stage(vault, runtime, tmp_path, task, TARGET, "---\nid: CHAR-0001\ntype: character\n---\n\n甲\n")
    payload = _inspect(vault, runtime, capsys, task)
    digest = payload["hash"]

    assert _run(vault, runtime, "approve", "artifact", task, "--hash", digest, "--as", "author") == 0
    capsys.readouterr()

    assert _inspect(vault, runtime, capsys, task)["approval"]["status"] == "VALID"

    staged = root / "07_Staging" / task / "drafts" / TARGET
    staged.write_bytes(staged.read_bytes().replace("甲".encode(), "乙".encode()))

    after = _inspect(vault, runtime, capsys, task)
    assert after["approval"]["status"] == "STALE"
    assert after["hash"] != digest

    code = _run(
        vault, runtime, "publish", "--task", task, "--as", "author", "--confirm", "--confirm-canon"
    )
    assert code == 4, "批准已失效时发布必须被拒（码 4 = 审批失败）"


def test_approval_requires_matching_hash(env, tmp_path, capsys):
    vault, runtime, root = env
    task = _task(vault, runtime)
    _stage(vault, runtime, tmp_path, task, TARGET, "正文\n")
    capsys.readouterr()
    assert _run(vault, runtime, "approve", "artifact", task, "--hash", "0" * 64, "--as", "author") == 4
    capsys.readouterr()


def test_blocker_blocks_approval_and_waiver(env, tmp_path, capsys):
    """验收 2：BLOCKER 不能靠 waive 绕过去，批准与发布都被挡住。"""
    vault, runtime, root = env
    task = _task(vault, runtime)
    _stage(vault, runtime, tmp_path, task, TARGET, "正文\n")
    digest = _inspect(vault, runtime, capsys, task)["hash"]
    capsys.readouterr()

    assert (
        _run(
            vault,
            runtime,
            "issue",
            "new",
            "--severity",
            "BLOCKER",
            "--category",
            "CONTINUITY",
            "--target",
            task,
            "--description",
            "人物已死却又出场",
            "--as",
            "editor",
        )
        == 0
    )
    capsys.readouterr()

    assert _run(vault, runtime, "approve", "artifact", task, "--hash", digest, "--as", "author") == 4
    capsys.readouterr()

    assert _run(vault, runtime, "issue", "waive", "ISSUE-000001", "--note", "先放着", "--as", "author") == 4
    capsys.readouterr()

    # 合法路径：修掉后 resolve，再批准
    assert _run(vault, runtime, "issue", "resolve", "ISSUE-000001", "--note", "已改写", "--as", "author") == 0
    capsys.readouterr()
    assert _run(vault, runtime, "approve", "artifact", task, "--hash", digest, "--as", "author") == 0
    capsys.readouterr()


def test_retcon_takes_its_own_path(env, tmp_path, capsys):
    """验收 3：RETCON 不走普通文件审批，必须先生成影响报告再独立批。"""
    vault, runtime, root = env
    # 一章正文，正文里提到 CHAR-0001，影响分析才找得到它
    chapter = root / CHAPTER_TARGET
    chapter.parent.mkdir(parents=True, exist_ok=True)
    chapter.write_text(
        "---\nid: CH-0001\ntype: chapter\ntitle: 第一章\n---\n\nCHAR-0001 推门进来。\n",
        encoding="utf-8",
    )
    assert _run(vault, runtime, "rebuild-index") == 0
    capsys.readouterr()

    task = _task(vault, runtime)
    _stage(vault, runtime, tmp_path, task, TARGET, "---\nid: CHAR-0001\ntype: character\n---\n\n新设定\n")
    digest = _inspect(vault, runtime, capsys, task)["hash"]
    capsys.readouterr()

    assert (
        _run(
            vault,
            runtime,
            "change",
            "create",
            "--type",
            "RETCON",
            "--reason",
            "推翻此前血统设定",
            "--target",
            "CHAR-0001",
            "--scope",
            TARGET,
            "--as",
            "author",
        )
        == 0
    )
    capsys.readouterr()

    # 未批准 RETCON 覆盖该路径 → 普通审批被拦
    assert _run(vault, runtime, "approve", "artifact", task, "--hash", digest, "--as", "author") == 4
    capsys.readouterr()

    # 没有影响报告就批变更 → 拒
    assert (
        _run(vault, runtime, "change", "approve", "CR-000001", "--hash", "x", "--as", "author") == 4
    )
    capsys.readouterr()

    assert _run(vault, runtime, "impact", "CR-000001", "--json") == 0
    impact = json.loads(capsys.readouterr().out)
    assert impact["impact"]["counts"]["chapters"] == 1
    assert impact["impact"]["affected_chapters"] == ["CH-0001"]
    impact_hash = impact["impact_hash"]

    # 有受影响正文却没做决定 → 拒（PRD §19.3）
    assert (
        _run(vault, runtime, "change", "approve", "CR-000001", "--hash", impact_hash, "--as", "author") == 4
    )
    capsys.readouterr()

    # 明确缩小范围 → 通过
    assert (
        _run(
            vault,
            runtime,
            "change",
            "approve",
            "CR-000001",
            "--hash",
            impact_hash,
            "--narrow-scope",
            "--as",
            "author",
        )
        == 0
    )
    capsys.readouterr()

    # RETCON 批准后，普通审批才放行
    assert _run(vault, runtime, "approve", "artifact", task, "--hash", digest, "--as", "author") == 0
    capsys.readouterr()

    show = _run(vault, runtime, "change", "show", "CR-000001", "--json")
    assert show == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "APPROVED"
    assert payload["scope_narrowed"] is True


def test_issue_list_filters(env, capsys):
    vault, runtime, root = env
    capsys.readouterr()
    _run(vault, runtime, "issue", "new", "--severity", "SUGGESTION", "--category", "QUALITY", "--target", "CH-0001", "--description", "可再压缩", "--as", "editor")
    _run(vault, runtime, "issue", "new", "--severity", "BLOCKER", "--category", "RULES", "--target", "CH-0002", "--description", "违规则", "--as", "editor")
    capsys.readouterr()
    assert _run(vault, runtime, "issue", "list", "--severity", "BLOCKER", "--json") == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1 and rows[0]["severity"] == "BLOCKER"
