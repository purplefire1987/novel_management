"""T04 命令行闭环与三条验收。

验收对照（PRD §23 T04）：
- 倒叙查询正确 → `test_flashback_after_anchor_returns_earlier_value`
- 无法排序返回 UNORDERED → `test_unordered_exit_code_and_json_status`
- 角色误判不污染客观状态 → `test_belief_does_not_change_objective_answer`
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_system.cli import main


def _write(path: Path, front: str, body: str = "占位。") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{front}---\n\n{body}\n", encoding="utf-8")


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
                "p1",
                "--name",
                "测试",
                "--confirm",
            ]
        )
        == 0
    )
    root = sorted((vault / "10_Projects").glob("p1*"))[0]

    # 故事顺序 E1 → E2 → E3；叙述顺序把 E3 放第 1 章（倒叙）。
    _write(root / "01_Canon/Events/EVT-0001.md", "id: EVT-0001\ntype: event\nnarrated_in: CH-0002\n")
    _write(
        root / "01_Canon/Events/EVT-0002.md",
        "id: EVT-0002\ntype: event\npredecessors: [EVT-0001]\nnarrated_in: CH-0001\n",
    )
    _write(
        root / "01_Canon/Events/EVT-0003.md",
        "id: EVT-0003\ntype: event\npredecessors: [EVT-0002]\nnarrated_in: CH-0001\n",
    )
    # 支线两条互不可比
    _write(root / "01_Canon/Events/EVT-0004.md", "id: EVT-0004\ntype: event\n")
    _write(root / "01_Canon/Events/EVT-0005.md", "id: EVT-0005\ntype: event\n")

    _write(
        root / "01_Canon/Assertions/AST-000001.md",
        "id: AST-000001\ntype: assertion\nsubject_id: CHAR-000001\npredicate: location\n"
        "value: LOC-A\nvalid_from_event: EVT-0002\n",
    )
    _write(
        root / "01_Canon/StateChanges/CHANGE-000001.md",
        "id: CHANGE-000001\ntype: state_change\nentity_id: CHAR-000001\nattribute: location\n"
        "from_value: LOC-A\nto_value: LOC-B\nevent_id: EVT-0003\n",
    )
    _write(
        root / "01_Canon/Assertions/AST-000002.md",
        "id: AST-000002\ntype: assertion\nsubject_id: CHAR-000002\npredicate: location\n"
        "value: LOC-C\nvalid_from_event: EVT-0004\n",
    )
    _write(
        root / "01_Canon/Assertions/AST-000003.md",
        "id: AST-000003\ntype: assertion\nsubject_id: CHAR-000002\npredicate: location\n"
        "value: LOC-D\nvalid_from_event: EVT-0005\n",
    )
    # 认知：CHAR-000001 相信 CHAR-000002 已经死了；客观记录并不支持这条。
    _write(
        root / "01_Canon/Knowledge/KNOW-000001.md",
        "id: KNOW-000001\ntype: knowledge\nholder_id: CHAR-000001\nattitude: BELIEVES\n"
        "proposition:\n  subject_id: CHAR-000002\n  predicate: life_status\n  value: DEAD\n",
    )
    _write(
        root / "01_Canon/Assertions/AST-000004.md",
        "id: AST-000004\ntype: assertion\nsubject_id: CHAR-000002\npredicate: life_status\nvalue: ALIVE\n",
    )

    assert main(["rebuild-index", "--vault", str(vault), "--runtime", str(runtime)]) == 0
    return vault, runtime, root


def _run(vault: Path, runtime: Path, *args: str) -> int:
    return main([*args, "--vault", str(vault), "--runtime", str(runtime)])


def test_flashback_after_anchor_returns_earlier_value(env, capsys):
    vault, runtime, _ = env
    capsys.readouterr()
    rc = _run(vault, runtime, "state", "CHAR-000001", "--field", "location", "--after", "EVT-0002")
    out = capsys.readouterr().out
    assert rc == 0
    # E3 在叙述上更早被读到（第 1 章），故事时间上更晚；按故事时间应是 LOC-A。
    assert "LOC-A" in out


def test_unordered_exit_code_and_json_status(env, capsys):
    vault, runtime, _ = env
    capsys.readouterr()
    rc = _run(vault, runtime, "state", "CHAR-000002", "--field", "location", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "UNORDERED"
    assert rc == 1, "没拿到确定答案必须非零退出"
    assert len(payload["records"]) == 2


def test_belief_does_not_change_objective_answer(env, capsys):
    vault, runtime, _ = env
    capsys.readouterr()
    rc = _run(vault, runtime, "state", "CHAR-000002", "--field", "life_status", "--json")
    state_payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert state_payload["value"] == "ALIVE"

    rc = _run(vault, runtime, "knowledge", "CHAR-000001", "--subject", "CHAR-000002", "--json")
    know_payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert know_payload["value"] == "BELIEVES:DEAD"


def test_not_recorded_is_nonzero_and_explained(env, capsys):
    vault, runtime, _ = env
    capsys.readouterr()
    rc = _run(vault, runtime, "state", "CHAR-999999", "--json")
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["status"] == "NOT_RECORDED"
    assert "不等于" in payload["note"]


def test_state_without_index_fails_loudly(tmp_path: Path, capsys):
    vault, runtime = tmp_path / "vault", tmp_path / "runtime"
    assert main(["init-author", "--vault", str(vault), "--runtime", str(runtime), "--confirm"]) == 0
    capsys.readouterr()
    rc = _run(vault, runtime, "state", "CHAR-000001")
    err = capsys.readouterr()
    assert rc == 2
    assert "rebuild-index" in (err.out + err.err)
