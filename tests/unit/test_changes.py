"""问题等级与变更请求的语义。

三条验收里两条落在这一层（BLOCKER 不能豁免、RETCON 独立审批），
所以这里不测 CLI 输出，只测「能不能绕过去」——绕不过去才算数。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_management.application.changes import (
    BLOCKER,
    NON_BLOCKING,
    APPROVED,
    ChangeStore,
    IssueStore,
    RETCON,
)
from novel_management.domain.errors import ApprovalError, UsageError


def _issues(tmp_path: Path) -> IssueStore:
    return IssueStore(tmp_path / "runtime")


def _changes(tmp_path: Path) -> ChangeStore:
    return ChangeStore(tmp_path / "runtime")


# ------------------------------------------------------------------ 问题等级


def test_blocker_cannot_be_waived(tmp_path: Path):
    """验收 2 的底层：BLOCKER 一律拒绝豁免，报错里要给两条合法路径。"""
    store = _issues(tmp_path)
    issue = store.create(severity=BLOCKER, category="CONTINUITY", target_id="CH-0001", description="人物死了又出现")
    with pytest.raises(ApprovalError) as exc:
        store.waive(issue.issue_id, note="这次先放过", actor="author")
    message = str(exc.value)
    assert "BLOCKER" in message
    assert "resolve" in message, "拒绝时要指路，不能只说不行"
    assert store.load(issue.issue_id).status == "OPEN"


def test_non_blocking_can_be_waived(tmp_path: Path):
    store = _issues(tmp_path)
    issue = store.create(severity=NON_BLOCKING, category="QUALITY", target_id="CH-0001", description="句子重复")
    waived = store.waive(issue.issue_id, note="风格如此", actor="author")
    assert waived.status == "WAIVED"
    assert "风格如此" in waived.closure_note


def test_blocker_resolve_requires_note(tmp_path: Path):
    store = _issues(tmp_path)
    issue = store.create(severity=BLOCKER, category="RULES", target_id="CHAR-0001", description="违规则")
    with pytest.raises(UsageError):
        store.resolve(issue.issue_id, note="  ", actor="author")
    resolved = store.resolve(issue.issue_id, note="已按 RULE-0001 改写", actor="author")
    assert resolved.status == "RESOLVED"


def test_open_blockers_only_returns_open_blockers(tmp_path: Path):
    store = _issues(tmp_path)
    open_blocker = store.create(severity=BLOCKER, category="CONTINUITY", target_id="CH-0001", description="a")
    store.create(severity=NON_BLOCKING, category="QUALITY", target_id="CH-0001", description="b")
    closed = store.create(severity=BLOCKER, category="CONTINUITY", target_id="CH-0001", description="c")
    store.resolve(closed.issue_id, note="已修", actor="author")
    store.create(severity=BLOCKER, category="CONTINUITY", target_id="CH-0002", description="d")

    found = store.open_blockers({"CH-0001"})
    assert [i.issue_id for i in found] == [open_blocker.issue_id]


def test_unknown_severity_rejected(tmp_path: Path):
    store = _issues(tmp_path)
    with pytest.raises(UsageError):
        store.create(severity="MAJOR", category="QUALITY", target_id="CH-0001", description="x")


# ------------------------------------------------------------------ 变更请求


def test_retcon_marked_independent(tmp_path: Path):
    store = _changes(tmp_path)
    retcon = store.create(type=RETCON, reason="此前写错了血统", targets=["CHAR-0001"])
    correction = store.create(type="CORRECTION", reason="录入错字", targets=["CHAR-0002"])
    assert retcon.independent is True
    assert correction.independent is False
    assert retcon.required_approvals == ["AUTHOR_INDEPENDENT"]


def test_retcon_cannot_be_approved_before_impact(tmp_path: Path):
    store = _changes(tmp_path)
    change = store.create(type=RETCON, reason="改设定", targets=["CHAR-0001"])
    with pytest.raises(ApprovalError) as exc:
        store.approve(change, impact_hash="x", actor="author")
    assert "impact" in str(exc.value)


def test_approval_hash_must_match_impact_report(tmp_path: Path):
    store = _changes(tmp_path)
    change = store.create(type=RETCON, reason="改设定", targets=["CHAR-0001"])
    store.attach_impact(change, {"affected_chapters": [], "counts": {}})
    with pytest.raises(ApprovalError) as exc:
        store.approve(change, impact_hash="deadbeef", actor="author")
    assert "哈希不符" in str(exc.value)


def test_retcon_with_affected_chapters_needs_decision(tmp_path: Path):
    """PRD §19.3：只改档案不处理正文，不能宣称 RETCON 完成。"""
    store = _changes(tmp_path)
    change = store.create(type=RETCON, reason="改设定", targets=["CHAR-0001"])
    store.attach_impact(change, {"affected_chapters": ["CH-0001", "CH-0007"], "counts": {}})
    with pytest.raises(ApprovalError) as exc:
        store.approve(change, impact_hash=change.impact_hash, actor="author")
    assert "CH-0001" in str(exc.value)

    approved = store.approve(
        change, impact_hash=change.impact_hash, actor="author", narrow_scope=True
    )
    assert approved.status == APPROVED
    assert approved.scope_narrowed is True


def test_retcon_rewrite_commitment_is_enough(tmp_path: Path):
    store = _changes(tmp_path)
    change = store.create(type=RETCON, reason="改设定", targets=["CHAR-0001"])
    store.attach_impact(change, {"affected_chapters": ["CH-0001"], "counts": {}})
    approved = store.approve(
        change, impact_hash=change.impact_hash, actor="author", allow_rewrite=True
    )
    assert approved.status == APPROVED
    assert approved.scope_narrowed is False


def test_blocking_scope_lists_unapproved_retcon(tmp_path: Path):
    store = _changes(tmp_path)
    change = store.create(
        type=RETCON, reason="改设定", targets=["CHAR-0001"], scope=["01_Canon/Characters/CHAR-0001.md"]
    )
    assert [c.change_id for c in store.blocking_scope(["01_Canon/Characters/CHAR-0001.md"])] == [change.change_id]
    # 不相干的路径不受牵连
    assert store.blocking_scope(["03_Manuscripts/Drafts/CH-0001.md"]) == []

    store.attach_impact(change, {"affected_chapters": [], "counts": {}})
    store.approve(change, impact_hash=change.impact_hash, actor="author")
    assert store.blocking_scope(["01_Canon/Characters/CHAR-0001.md"]) == []


def test_change_requires_reason_and_target(tmp_path: Path):
    store = _changes(tmp_path)
    with pytest.raises(UsageError):
        store.create(type=RETCON, reason="   ", targets=["CHAR-0001"])
    with pytest.raises(UsageError):
        store.create(type=RETCON, reason="有原因但没说改什么")


# ------------------------------------------------------------------ 影响分析


def _index_conn():
    """建一份只有表结构的索引库，用来测影响分析怎么找「受影响的正文」。

    真库的正文里写的是人名，不是 `CHAR-0001`。所以「只扫 ID」的实现会在真库上
    永远报 0 章受影响，RETCON 那道闸门就成了常开。这几个用例守的就是这件事。
    """
    import sqlite3

    from novel_management.infrastructure.database.indexing.schema import create_schema

    conn = sqlite3.connect(":memory:")
    create_schema(conn)
    return conn


def _chapter(
    conn, artifact_id: str, path: str, text: str, block_id: str = "p0001", artifact_type: str = "chapter"
) -> None:
    conn.execute(
        "insert into artifacts (id, project, path, artifact_type, sha256)"
        " values (?, 'p1', ?, ?, 'x')",
        (artifact_id, path, artifact_type),
    )
    conn.execute(
        "insert into blocks (project, artifact_id, path, block_id, ordinal, kind, text)"
        " values ('p1', ?, ?, ?, 1, 'para', ?)",
        (artifact_id, path, block_id, text),
    )


def _entity(conn, entity_id: str, name: str, aliases: list[str]) -> None:
    conn.execute(
        "insert into entities (id, project, entity_type, name, path) values (?, 'p1', 'character', ?, '')",
        (entity_id, name),
    )
    for alias in aliases:
        conn.execute(
            "insert into aliases (project, entity_id, alias) values ('p1', ?, ?)", (entity_id, alias)
        )


def test_impact_finds_chapter_by_character_name():
    """正文只写人名、不写 ID 时，仍然要认出这一章受影响。"""
    from novel_management.application.changes.impact import analyse

    conn = _index_conn()
    _entity(conn, "CHAR-0001", "张三", ["老张"])
    _chapter(conn, "CH-0001", "03_Manuscripts/Drafts/CH-0001.md", "张三推开门，屋里没有人。")

    report = analyse(conn, change_id="CR-000001", targets=["CHAR-0001"])

    assert report.affected_chapters == ("CH-0001",)
    assert report.counts["blocks"] == 1
    assert report.mentions[0]["matched"][0]["via"] == "name"


def test_impact_matches_alias_too():
    from novel_management.application.changes.impact import analyse

    conn = _index_conn()
    _entity(conn, "CHAR-0001", "张三", ["老张"])
    _chapter(conn, "CH-0002", "03_Manuscripts/Drafts/CH-0002.md", "老张在城门口等了三天。")

    report = analyse(conn, change_id="CR-000001", targets=["CHAR-0001"])

    assert report.affected_chapters == ("CH-0002",)
    assert report.mentions[0]["matched"][0]["via"] == "alias"


def test_impact_ignores_single_char_names():
    """单字名会命中一切，宁可不匹配也不能把全书报成受影响。"""
    from novel_management.application.changes.impact import analyse

    conn = _index_conn()
    _entity(conn, "CHAR-0009", "砚", [])
    _chapter(conn, "CH-0003", "03_Manuscripts/Drafts/CH-0003.md", "无关的一章。")

    report = analyse(conn, change_id="CR-000001", targets=["CHAR-0009"])

    assert report.affected_chapters == ()
    assert any("名字不足 2 字" in note for note in report.notes), "词表为空时必须明说"


def test_impact_treats_like_wildcards_as_literal():
    """名字里带 `%` 时不能当成通配符——那会把整库都算成受影响。"""
    from novel_management.application.changes.impact import analyse

    conn = _index_conn()
    _entity(conn, "CHAR-0010", "百%胜", [])
    _chapter(conn, "CH-0004", "03_Manuscripts/Drafts/CH-0004.md", "与这个名字无关的正文。")

    report = analyse(conn, change_id="CR-000001", targets=["CHAR-0010"])

    assert report.affected_chapters == ()


def test_impact_splits_planning_from_chapters():
    from novel_management.application.changes.impact import analyse

    conn = _index_conn()
    _entity(conn, "CHAR-0001", "张三", [])
    _chapter(conn, "CH-0001", "03_Manuscripts/Drafts/CH-0001.md", "张三出场。")
    # 规划文档的 frontmatter 常写 `type: chapter`（分卷章卡），路径才是判据。
    _chapter(
        conn,
        "OUTLINE-0001",
        "02_Planning/Outlines/OUTLINE-0001.md",
        "张三在第 3 卷死。",
        artifact_type="chapter",
    )

    report = analyse(conn, change_id="CR-000001", targets=["CHAR-0001"])

    assert report.affected_chapters == ("CH-0001",)
    assert report.affected_planning == ("OUTLINE-0001",)
