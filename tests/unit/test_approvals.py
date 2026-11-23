"""批准记录的内容绑定（T05）。

PRD §14.5：批准绑的是「文件 + 内容指纹」。**批准后改一个字节就失效**，
这里不做「非实质修改」的识别——MVP 不实现含义判断。

这些用例直接打 `ApprovalStore.verify`，不走 CLI。原因是 CLI 那条路上
还有一道包哈希校验会顺手兜住同样的错误（`_approval_state` 里
`target_hash != expected` 分支），集成测试红了也未必说明逐文件复核还在。
两道闸门各有各的用例，谁被删掉谁自己红。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_management.approvals import ApprovalFile, ApprovalStore
from novel_management.errors import ApprovalError


def _store(tmp_path: Path) -> ApprovalStore:
    return ApprovalStore(tmp_path / "runtime")


def _files() -> list[ApprovalFile]:
    return [
        ApprovalFile(target="01_Canon/Characters/CHAR-0001.md", staged="07_Staging/T1/a.md", sha256="a" * 64),
        ApprovalFile(target="01_Canon/Characters/CHAR-0002.md", staged="07_Staging/T1/b.md", sha256="b" * 64),
    ]


def test_verify_passes_when_every_byte_matches(tmp_path: Path) -> None:
    store = _store(tmp_path)
    approval = store.record(task_id="TASK-000001", project_id="novel-001", files=_files())
    current = {f.target: f.sha256 for f in approval.files}

    status, stale = store.verify(approval, current)

    assert status == "VALID"
    assert stale == []


def test_verify_flags_the_one_file_that_changed(tmp_path: Path) -> None:
    """改一个字节 → 只有那一个文件失效，且报得出是哪个。"""
    store = _store(tmp_path)
    approval = store.record(task_id="TASK-000001", project_id="novel-001", files=_files())
    current = {f.target: f.sha256 for f in approval.files}
    current["01_Canon/Characters/CHAR-0002.md"] = "c" * 64

    status, stale = store.verify(approval, current)

    assert status == "STALE"
    assert stale == ["01_Canon/Characters/CHAR-0002.md"]
    assert "01_Canon/Characters/CHAR-0001.md" not in stale


def test_verify_treats_missing_file_as_stale(tmp_path: Path) -> None:
    """文件被删了不能算「没改动」。`None` 与任何哈希都不相等，必须进 stale。"""
    store = _store(tmp_path)
    approval = store.record(task_id="TASK-000001", project_id="novel-001", files=_files())
    current = {"01_Canon/Characters/CHAR-0001.md": "a" * 64}

    status, stale = store.verify(approval, current)

    assert status == "STALE"
    assert stale == ["01_Canon/Characters/CHAR-0002.md"]


def test_verify_checks_release_level_hash(tmp_path: Path) -> None:
    """发布/变更批准绑的是计划或影响报告的哈希，那条也要复核。"""
    store = _store(tmp_path)
    approval = store.record(
        task_id="TASK-000001",
        project_id="novel-001",
        files=[],
        target_type="release",
        target_id="REL-000001",
        target_hash="d" * 64,
    )

    assert store.verify(approval, {"REL-000001": "d" * 64})[0] == "VALID"
    assert store.verify(approval, {"REL-000001": "e" * 64})[0] == "STALE"


def test_record_rejects_non_author(tmp_path: Path) -> None:
    """批准主体只能是作者。编辑点过头不是批准。"""
    store = _store(tmp_path)
    with pytest.raises(ApprovalError):
        store.record(task_id="TASK-000001", project_id="novel-001", files=_files(), approver="editor")


def test_record_requires_bound_files_for_artifact(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(Exception):
        store.record(task_id="TASK-000001", project_id="novel-001", files=[], target_type="artifact")


def test_for_target_ignores_other_targets_and_rejections(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record(task_id="TASK-000001", project_id="novel-001", files=_files(), target_type="artifact", target_id="TASK-000001")
    store.record(
        task_id="TASK-000002",
        project_id="novel-001",
        files=_files(),
        target_type="artifact",
        target_id="TASK-000002",
        decision="rejected",
    )

    found = store.for_target("artifact", "TASK-000001")

    assert len(found) == 1
    assert found[0].target_id == "TASK-000001"
    assert store.for_target("artifact", "TASK-000002") == []
