"""状态区间、人物认知、读者揭示的查询语义。

三条验收各有一个直接对应用例（名字里就写着），另外覆盖区间边界、
基线记录、环、规划隔离。全部数据在 tmp_path 现造，人名地名纯虚构。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_management.cli import main
from novel_management.infrastructure.database.indexing import connect
from novel_management.infrastructure.database.indexing.locate import active_db
from novel_management.domain.timeline import (
    AMBIGUOUS,
    DISPUTED,
    MODE_AFTER,
    MODE_BEFORE,
    NOT_RECORDED,
    RESOLVED,
    UNORDERED,
    knowledge_answer,
    revelation_answer,
    state_answer,
)


def _init_env(root: Path) -> tuple[Path, Path]:
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
                "novel-001",
                "--name",
                "测试",
                "--confirm",
            ]
        )
        == 0
    )
    return vault, runtime


def _write(path: Path, front: str, body: str = "占位正文。") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{front}---\n\n{body}\n", encoding="utf-8")


def _env(tmp_path: Path) -> tuple[Path, Path, Path]:
    vault, runtime = _init_env(tmp_path)
    root = sorted((vault / "10_Projects").glob("novel-001*"))[0]
    return vault, runtime, root


def _rebuild(vault: Path, runtime: Path) -> None:
    assert main(["rebuild-index", "--vault", str(vault), "--runtime", str(runtime)]) == 0


def _conn(vault: Path, runtime: Path):
    _rebuild(vault, runtime)
    return connect(active_db(runtime))


# ---------------------------------------------------------------- 验收 1：倒叙


def test_flashback_query_uses_story_order_not_narrative_order(tmp_path: Path):
    """验收 1：倒叙查询正确。

    故事顺序 E1 → E2 → E3；叙述顺序把 E3 放第 1 章、E1 放第 2 章（倒叙）。
    若查询按章节顺序排，问「E2 之后」会答成 E3 的值；按故事顺序应仍是 E2 的值。
    """
    vault, runtime, root = _env(tmp_path)
    _write(
        root / "01_Canon/Events/EVT-0001.md",
        "id: EVT-0001\ntype: event\ntitle: 前事\nnarrated_in: CH-0002\n",
    )
    _write(
        root / "01_Canon/Events/EVT-0002.md",
        "id: EVT-0002\ntype: event\ntitle: 中段\npredecessors: [EVT-0001]\nnarrated_in: CH-0001\n",
    )
    _write(
        root / "01_Canon/Events/EVT-0003.md",
        "id: EVT-0003\ntype: event\ntitle: 后事\npredecessors: [EVT-0002]\nnarrated_in: CH-0001\n",
    )
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

    conn = _conn(vault, runtime)
    try:
        after_two = state_answer(conn, entity_id="CHAR-000001", attribute="location", anchor="EVT-0002")
        assert after_two.status == RESOLVED
        assert after_two.value == "LOC-A", "E2 之后、E3 之前应是 LOC-A（章节顺序在这里是反的）"

        after_three = state_answer(conn, entity_id="CHAR-000001", attribute="location", anchor="EVT-0003")
        assert after_three.value == "LOC-B"
    finally:
        conn.close()


# ------------------------------------------------------- 验收 2：无法排序


def test_incomparable_events_return_unordered(tmp_path: Path):
    """验收 2：无法排序返回 UNORDERED（不是随便挑一条）。"""
    vault, runtime, root = _env(tmp_path)
    _write(root / "01_Canon/Events/EVT-0101.md", "id: EVT-0101\ntype: event\ntitle: 支线甲\n")
    _write(root / "01_Canon/Events/EVT-0102.md", "id: EVT-0102\ntype: event\ntitle: 支线乙\n")
    _write(
        root / "01_Canon/Assertions/AST-000101.md",
        "id: AST-000101\ntype: assertion\nsubject_id: CHAR-000009\npredicate: location\n"
        "value: LOC-A\nvalid_from_event: EVT-0101\n",
    )
    _write(
        root / "01_Canon/Assertions/AST-000102.md",
        "id: AST-000102\ntype: assertion\nsubject_id: CHAR-000009\npredicate: location\n"
        "value: LOC-B\nvalid_from_event: EVT-0102\n",
    )

    conn = _conn(vault, runtime)
    try:
        answer = state_answer(conn, entity_id="CHAR-000009", attribute="location")
        assert answer.status == UNORDERED
        assert {r.id for r in answer.records} == {"AST-000101", "AST-000102"}
        assert "最新一条" in answer.note
    finally:
        conn.close()


def test_same_event_conflicting_values_is_ambiguous_not_unordered(tmp_path: Path):
    """同一时点两条记录打架是 AMBIGUOUS；事件互不可比才是 UNORDERED。混了就没法修。"""
    vault, runtime, root = _env(tmp_path)
    _write(root / "01_Canon/Events/EVT-0111.md", "id: EVT-0111\ntype: event\ntitle: 某日\n")
    for aid, value in (("AST-000111", "LOC-A"), ("AST-000112", "LOC-B")):
        _write(
            root / f"01_Canon/Assertions/{aid}.md",
            f"id: {aid}\ntype: assertion\nsubject_id: CHAR-000010\npredicate: location\n"
            f"value: {value}\nvalid_from_event: EVT-0111\n",
        )

    conn = _conn(vault, runtime)
    try:
        answer = state_answer(conn, entity_id="CHAR-000010", attribute="location")
        assert answer.status == AMBIGUOUS
    finally:
        conn.close()


def test_cycle_refuses_to_order(tmp_path: Path):
    vault, runtime, root = _env(tmp_path)
    _write(root / "01_Canon/Events/EVT-0121.md", "id: EVT-0121\ntype: event\npredecessors: [EVT-0122]\n")
    _write(root / "01_Canon/Events/EVT-0122.md", "id: EVT-0122\ntype: event\npredecessors: [EVT-0121]\n")
    _write(
        root / "01_Canon/Assertions/AST-000121.md",
        "id: AST-000121\ntype: assertion\nsubject_id: CHAR-000011\npredicate: location\n"
        "value: LOC-A\nvalid_from_event: EVT-0121\n",
    )
    conn = _conn(vault, runtime)
    try:
        answer = state_answer(conn, entity_id="CHAR-000011", attribute="location")
        assert answer.status == UNORDERED
        assert "环" in answer.note
    finally:
        conn.close()


# ------------------------------------------------- 验收 3：认知不污染客观状态


def test_character_misbelief_does_not_pollute_objective_state(tmp_path: Path):
    """验收 3：角色误判不污染客观状态。

    CHAR-A 相信 CHAR-B 已死；客观记录说 B 活着。查 B 的状态必须是 ALIVE，
    查 A 的认知才是 BELIEVES:DEAD。两者走两张表，不是靠 where 过滤。
    """
    vault, runtime, root = _env(tmp_path)
    _write(
        root / "01_Canon/Assertions/AST-000201.md",
        "id: AST-000201\ntype: assertion\nsubject_id: CHAR-000020\npredicate: life_status\n"
        "value: ALIVE\nreview_status: CLEAR\n",
    )
    _write(
        root / "01_Canon/Knowledge/KNOW-000201.md",
        "id: KNOW-000201\ntype: knowledge\nholder_id: CHAR-000021\nattitude: BELIEVES\n"
        "proposition:\n  subject_id: CHAR-000020\n  predicate: life_status\n  value: DEAD\n",
    )

    conn = _conn(vault, runtime)
    try:
        objective = state_answer(conn, entity_id="CHAR-000020", attribute="life_status")
        assert objective.status == RESOLVED
        assert objective.value == "ALIVE"

        belief = knowledge_answer(conn, holder_id="CHAR-000021", subject_id="CHAR-000020")
        assert belief.status == RESOLVED
        assert belief.value == "BELIEVES:DEAD"

        # 反过来：拿认知当事实的实现会在这里返回 DEAD。
        assert "DEAD" not in objective.value
    finally:
        conn.close()


def test_knowledge_absent_is_not_recorded_not_unknown(tmp_path: Path):
    vault, runtime, root = _env(tmp_path)
    _write(
        root / "01_Canon/Events/EVT-000301.md",
        "id: EVT-000301\ntype: event\ntitle: 某日\n",
    )
    conn = _conn(vault, runtime)
    try:
        answer = knowledge_answer(conn, holder_id="CHAR-000030", subject_id="CHAR-000031")
        assert answer.status == NOT_RECORDED
        assert "不等于「不知道」" in answer.note
    finally:
        conn.close()


# --------------------------------------------------------------- 区间与基线


def test_interval_until_excludes_later_anchor(tmp_path: Path):
    """`[since, until)`：走到 until 之后，这条断言不再生效。"""
    vault, runtime, root = _env(tmp_path)
    _write(root / "01_Canon/Events/EVT-000401.md", "id: EVT-000401\ntype: event\n")
    _write(root / "01_Canon/Events/EVT-000402.md", "id: EVT-000402\ntype: event\npredecessors: [EVT-000401]\n")
    _write(
        root / "01_Canon/Assertions/AST-000401.md",
        "id: AST-000401\ntype: assertion\nsubject_id: CHAR-000040\npredicate: title\n"
        "value: 学徒\nvalid_from_event: EVT-000401\nvalid_until_event: EVT-000402\n",
    )
    conn = _conn(vault, runtime)
    try:
        before = state_answer(conn, entity_id="CHAR-000040", attribute="title", anchor="EVT-000401")
        assert before.status == RESOLVED and before.value == "学徒"
        after = state_answer(conn, entity_id="CHAR-000040", attribute="title", anchor="EVT-000402")
        assert after.status == NOT_RECORDED
    finally:
        conn.close()


def test_baseline_record_is_superseded_by_dated_change(tmp_path: Path):
    """没有生效事件的记录读作「从一开始就成立」，不该与事件记录比出 UNORDERED。"""
    vault, runtime, root = _env(tmp_path)
    _write(root / "01_Canon/Events/EVT-000501.md", "id: EVT-000501\ntype: event\n")
    _write(
        root / "01_Canon/Assertions/AST-000501.md",
        "id: AST-000501\ntype: assertion\nsubject_id: CHAR-000050\npredicate: life_status\nvalue: ALIVE\n",
    )
    _write(
        root / "01_Canon/StateChanges/CHANGE-000501.md",
        "id: CHANGE-000501\ntype: state_change\nentity_id: CHAR-000050\nattribute: life_status\n"
        "from_value: ALIVE\nto_value: DEAD\nevent_id: EVT-000501\n",
    )
    conn = _conn(vault, runtime)
    try:
        latest = state_answer(conn, entity_id="CHAR-000050", attribute="life_status")
        assert latest.status == RESOLVED and latest.value == "DEAD"
        # 变化之前问，应是基线值。
        earlier = state_answer(
            conn, entity_id="CHAR-000050", attribute="life_status", anchor="EVT-000501", mode=MODE_BEFORE
        )
        assert earlier.status == RESOLVED and earlier.value == "ALIVE"
    finally:
        conn.close()


def test_snapshot_resolves_each_field_separately(tmp_path: Path):
    """不给字段时按字段分别判定：一个字段 UNORDERED 不该把另一个字段也拖成 UNORDERED。"""
    vault, runtime, root = _env(tmp_path)
    _write(root / "01_Canon/Events/EVT-000601.md", "id: EVT-000601\ntype: event\n")
    _write(root / "01_Canon/Events/EVT-000602.md", "id: EVT-000602\ntype: event\n")
    _write(
        root / "01_Canon/Assertions/AST-000601.md",
        "id: AST-000601\ntype: assertion\nsubject_id: CHAR-000060\npredicate: life_status\nvalue: ALIVE\n",
    )
    for aid, event, value in (("AST-000602", "EVT-000601", "LOC-A"), ("AST-000603", "EVT-000602", "LOC-B")):
        _write(
            root / f"01_Canon/Assertions/{aid}.md",
            f"id: {aid}\ntype: assertion\nsubject_id: CHAR-000060\npredicate: location\n"
            f"value: {value}\nvalid_from_event: {event}\n",
        )

    conn = _conn(vault, runtime)
    try:
        answer = state_answer(conn, entity_id="CHAR-000060")
        assert answer.status == UNORDERED
        fields = {item["predicate"]: item for item in answer.extra["fields"]}
        assert fields["life_status"]["status"] == RESOLVED
        assert fields["life_status"]["value"] == "ALIVE"
        assert fields["location"]["status"] == UNORDERED
        assert "location" in answer.note
    finally:
        conn.close()


def test_disputed_record_is_flagged_not_trusted(tmp_path: Path):
    vault, runtime, root = _env(tmp_path)
    _write(
        root / "01_Canon/Assertions/AST-000701.md",
        "id: AST-000701\ntype: assertion\nsubject_id: CHAR-000070\npredicate: rank\n"
        "value: 三等\nreview_status: DISPUTED\n",
    )
    conn = _conn(vault, runtime)
    try:
        answer = state_answer(conn, entity_id="CHAR-000070", attribute="rank")
        assert answer.status == DISPUTED
        assert answer.value == "三等"
    finally:
        conn.close()


def test_state_not_recorded_for_unknown_entity(tmp_path: Path):
    vault, runtime, _root = _env(tmp_path)
    conn = _conn(vault, runtime)
    try:
        answer = state_answer(conn, entity_id="CHAR-999999")
        assert answer.status == NOT_RECORDED
        assert "不等于「不存在」" in answer.note
    finally:
        conn.close()


# ------------------------------------------------------------ 读者揭示


def test_revelation_is_separate_from_knowledge(tmp_path: Path):
    """读者知道 ≠ 人物知道：`reveal` 只读 revelations，不推断任何角色。"""
    vault, runtime, root = _env(tmp_path)
    _write(
        root / "01_Canon/Revelations/REV-000801.md",
        "id: REV-000801\ntype: revelation\nproposition_ref: AST-000801\n"
        "narrative_position:\n  chapter_id: CH-0010\n  block_id: p0032\nrevelation_mode: EXPLICIT\n",
    )
    conn = _conn(vault, runtime)
    try:
        answer = revelation_answer(conn, proposition_ref="AST-000801")
        assert answer.status == RESOLVED
        assert answer.value == "CH-0010 · p0032"
        empty = revelation_answer(conn, proposition_ref="AST-999999")
        assert empty.status == NOT_RECORDED
    finally:
        conn.close()


# ------------------------------------------------------------ 规划隔离


def test_planning_records_do_not_enter_fact_tables(tmp_path: Path):
    """`02_Planning` 里的事件是「打算这么写」，不是「已经发生」。"""
    vault, runtime, root = _env(tmp_path)
    _write(
        root / "02_Planning/Chapter_Outlines/EVT-000901.md",
        "id: EVT-000901\ntype: event\ntitle: 计划中的事\n",
        body="这是规划。",
    )
    _write(root / "01_Canon/Events/EVT-000902.md", "id: EVT-000902\ntype: event\ntitle: 已发生\n")
    conn = _conn(vault, runtime)
    try:
        ids = {row[0] for row in conn.execute("select id from events")}
        assert "EVT-000902" in ids
        assert "EVT-000901" not in ids
        meta = dict(conn.execute("select key, value from index_metadata").fetchall())
        assert int(meta.get("planning_skipped", "0")) >= 1
    finally:
        conn.close()


@pytest.mark.parametrize("mode", [MODE_AFTER, MODE_BEFORE])
def test_state_accepts_both_directions(tmp_path: Path, mode: str):
    vault, runtime, root = _env(tmp_path)
    _write(root / "01_Canon/Events/EVT-001001.md", "id: EVT-001001\ntype: event\n")
    _write(
        root / "01_Canon/Assertions/AST-001001.md",
        "id: AST-001001\ntype: assertion\nsubject_id: CHAR-000100\npredicate: rank\n"
        "value: 三等\nvalid_from_event: EVT-001001\n",
    )
    conn = _conn(vault, runtime)
    try:
        answer = state_answer(conn, entity_id="CHAR-000100", attribute="rank", anchor="EVT-001001", mode=mode)
        # AFTER 含锚点（[start, end) 左闭），BEFORE 不含。
        assert answer.status == (RESOLVED if mode == MODE_AFTER else NOT_RECORDED)
    finally:
        conn.close()
