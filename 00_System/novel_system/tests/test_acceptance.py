"""PRD 17.1 验收标准 AC-01 ~ AC-14 的可执行版本。"""

from __future__ import annotations

import pytest

from conftest import compose_draft, make_chapter, write_file
from novel_system import vault
from novel_system.agents.memory_manager import MemoryManagerAgent
from novel_system.database.rebuild import rebuild
from novel_system.database.sqlite import Index, _chapter_sort_key
from novel_system.transaction import Transaction, TransactionError, rollback
from novel_system.validators import canon, continuity, foreshadowing, permissions
from novel_system.validators.schema import Issue
from novel_system.workflow.state_machine import WorkflowError

ALIVE_STATE = {
    "character_id": "CHAR-0001",
    "objective_status": "存活",
    "已知信息": "西岸有灯",
}


# ---------------------------------------------------------------- AC-01 ~ AC-14


def test_ac01_duplicate_entity_id_rejected(cfg, vault_dir):
    """AC-01 创建重复实体 ID：系统拒绝。"""
    src = vault_dir / "01_Core_Memory/Characters/CHAR-0001_沈砚.md"
    dup = vault_dir / "01_Core_Memory/Characters/CHAR-0001_沈砚_副本.md"
    dup.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    docs = [{"meta": vault.read_doc(p).meta, "path": str(p)} for p in (src, dup)]
    issues = canon.check_duplicate_ids(cfg, docs)

    assert any(i.blocks_write and "重复实体 ID" in i.detail for i in issues), issues


def test_ac02_injury_heals_without_event(cfg):
    """AC-02 受伤后下一章无故恢复：提示状态冲突。"""
    old = {"character_id": "CHAR-0001", "伤势": "左肩刀伤，未处理"}
    new = {"injury": ""}

    issues = continuity.check_state_regression(old, new, [], "CH-0009")

    assert any(i.blocks_write and "伤势" in i.detail for i in issues), issues


def test_ac02_injury_heal_with_event_passes(cfg):
    old = {"character_id": "CHAR-0001", "伤势": "左肩刀伤，未处理"}
    issues = continuity.check_state_regression(old, {"injury": "已愈"}, ["CH-0009 正文"], "CH-0009")
    assert not issues


def test_ac03_dead_character_on_stage(cfg):
    """AC-03 人物死亡后仍出场：阻断。"""
    dead = {"character_id": "CHAR-0001", "objective_status": "死亡", "已知信息": "无"}
    issues = continuity.check_cast(cfg, ["CHAR-0001"], {"CHAR-0001": dead}, lambda _id: True)

    assert any(i.severity == "BLOCKER" and "死亡" in i.detail for i in issues), issues


def test_ac04_character_belief_is_not_objective_fact(cfg, orch):
    """AC-04 角色误认敌人死亡：只进认知边界，不改客观生死。"""
    body = (
        "## 正文\n\n"
        "沈砚看着水面，认定邢咎已经死了。\n\n"
        "---\n\n"
        "## 作者备注（不进入正文）\n"
        "- 状态: CHAR-0001 | 位置=白骨渡河心 | 生死=未确认 | 认知=沈砚认为邢咎已死 | 未知=邢咎真实生死\n"
    )
    doc = vault.Doc(path=cfg.vault, meta={"id": "CH-0002", "type": "manuscript"}, body=body, raw=body)

    agent = MemoryManagerAgent(cfg, orch.index)
    result = agent.extract(
        chapter_id="CH-0002", manuscript=doc, canon_context="", existing_events=[], event_sequence=1
    )
    state_proposals = [p for p in result.data["proposals"] if p["kind"] == "character_state"]

    assert state_proposals, result.data
    payload = state_proposals[0]["payload"]
    assert payload["objective_status"] == "未确认"
    assert any("已死" in a for a in payload["awareness"])
    assert "已死" not in str(payload["objective_status"])


def test_ac05_foreshadowing_overdue(cfg):
    """AC-05 超过回收窗口：标记 overdue 并提示。"""
    meta = {
        "id": "FORESHADOW-9001",
        "status": "planted",
        "planted_in": "CH-0001",
        "target_reveal_window": {"start": "CH-0003", "end": "CH-0004"},
    }
    issues = foreshadowing.check_ledger_entry(meta, "CH-0008")

    assert any("超过最晚回收章" in i.detail for i in issues), issues


def test_ac06_no_payoff_without_evidence_or_window(cfg):
    """AC-06 触发条件/引用不满足：不允许自动回收。"""
    meta = {
        "id": "FORESHADOW-9002",
        "status": "planted",
        "planted_in": "CH-0001",
        "target_reveal_window": {"start": "CH-0005", "end": "CH-0008"},
        "trigger_conditions": ["沈砚见到师父遗物"],
    }

    early = foreshadowing.check_transition(meta, "paid_off", "CH-0003", ["CH-0003 正文"])
    no_evidence = foreshadowing.check_transition(meta, "paid_off", "CH-0006", [])
    illegal = foreshadowing.check_transition(
        {**meta, "status": "paid_off"}, "planted", "CH-0006", ["CH-0006 正文"]
    )

    assert any("过早" in i.detail for i in early)
    assert any("必须提供事件或章节引用" in i.detail for i in no_evidence)
    assert any("迁移非法" in i.detail for i in illegal)

    ok = foreshadowing.check_transition(meta, "paid_off", "CH-0006", ["EVT-CH-0006-001"])
    assert not ok, ok


def test_ac07_unknown_entity_reference(cfg):
    """AC-07 引用不存在的实体：校验失败。"""
    issues = canon.check_refs_exist(["CHAR-9999"], lambda _id: False, blocking=True)

    assert any(i.blocks_write for i in issues), issues


def test_ac08_agent_cannot_modify_core_memory(cfg):
    """AC-08 Agent 尝试改核心设定：权限系统拒绝。"""
    with pytest.raises(permissions.PermissionDenied):
        permissions.check_agent_write(
            cfg, "writer", "01_Core_Memory/Characters/CHAR-0001_沈砚.md"
        )


def test_ac13_agent_cannot_write_manuscript(cfg):
    """AC-13 Agent 直接写正式正文：执行层拒绝。"""
    with pytest.raises(permissions.PermissionDenied):
        permissions.check_agent_write(cfg, "memory_manager", "08_Manuscripts/Volume_01/CH-0001.md")
    with pytest.raises(permissions.PermissionDenied):
        permissions.check_agent_write(cfg, "lore_keeper", "02_Hot_Memory/Current_Story_State.md")
    # 允许写自己的 staging
    permissions.check_agent_write(cfg, "writer", ".novel_system/staging/draft/CH-0003.draft.md")


def test_ac09_write_failure_keeps_files_intact(cfg, orch, monkeypatch):
    """AC-09 正式写入失败：原文件保持不变并记录日志。"""
    existing = vault_dir_file(cfg, "01_Core_Memory/Characters/CHAR-0001_沈砚.md")
    before = existing.read_text(encoding="utf-8")

    tx = Transaction(
        cfg, orch.index, actor="author", agent="author", operation="test_failure", target="CHAR-9001"
    )
    tx.stage_doc(
        "01_Core_Memory/Characters/CHAR-9001_临时甲.md",
        {"id": "CHAR-9001", "type": "character", "name": "临时甲", "canon_status": "confirmed"},
        "# 临时甲\n",
    )
    tx.stage_doc(
        "01_Core_Memory/Characters/CHAR-9002_临时乙.md",
        {"id": "CHAR-9002", "type": "character", "name": "临时乙", "canon_status": "confirmed"},
        "# 临时乙\n",
    )

    original = vault.write_atomic

    def flaky(path, text):
        if path.name.startswith("CHAR-9002") and "01_Core_Memory" in str(path):
            raise OSError("模拟磁盘写入失败")
        return original(path, text)

    monkeypatch.setattr(vault, "write_atomic", flaky)
    with pytest.raises(TransactionError):
        tx.commit("触发失败")
    monkeypatch.setattr(vault, "write_atomic", original)

    assert existing.read_text(encoding="utf-8") == before
    assert not (cfg.vault / "01_Core_Memory/Characters/CHAR-9001_临时甲.md").exists()
    assert not (cfg.vault / "01_Core_Memory/Characters/CHAR-9002_临时乙.md").exists()
    assert orch.index.get_audit(tx.op_id)["status"] == "failed"


def test_ac10_rollback_restores_file_and_index(cfg, orch):
    """AC-10 回滚某次写入：文件与索引恢复到指定版本。"""
    rel = "01_Core_Memory/Characters/CHAR-9003_回滚测试.md"
    tx = Transaction(
        cfg, orch.index, actor="author", agent="author", operation="test_rollback", target="CHAR-9003"
    )
    tx.stage_doc(
        rel,
        {"id": "CHAR-9003", "type": "character", "name": "回滚测试", "canon_status": "confirmed"},
        "# 回滚测试\n",
    )
    commit = tx.commit("创建回滚测试实体")
    assert (cfg.vault / rel).exists()
    assert orch.index.exists("CHAR-9003")

    result = rollback(cfg, orch.index, commit["operation_id"])

    assert rel in result["removed"]
    assert not (cfg.vault / rel).exists()
    fresh = Index(cfg.index_db)
    try:
        assert not fresh.exists("CHAR-9003")
        assert fresh.get_audit(result["rollback_id"])["operation"] == "rollback"
    finally:
        fresh.close()


def test_ac11_repeated_memory_commit_is_idempotent(cfg, orch, vault_dir):
    """AC-11 重复执行同章记忆更新：不产生重复事件。"""
    prepare_published_chapter(orch, vault_dir)

    first = orch.commit_memory("CH-0003")
    applied_first = orch.apply_memory("CH-0003", approve="all")
    events_after_first = sorted(p.name for p in (cfg.vault / "07_Entity_Graph/Events").glob("EVT-CH-0003-*.md"))
    assert events_after_first == ["EVT-CH-0003-001.md"], applied_first

    orch.commit_memory("CH-0003")
    orch.apply_memory("CH-0003", approve="all")
    events_after_second = sorted(p.name for p in (cfg.vault / "07_Entity_Graph/Events").glob("EVT-CH-0003-*.md"))

    assert events_after_second == events_after_first
    assert len([p for p in first.proposals if p["kind"] == "event"]) == 1


def test_ac12_index_rebuild_from_markdown(cfg, orch):
    """AC-12 Markdown 与 SQLite 不一致：支持从 Markdown 重建索引。"""
    before = orch.index.stats()
    cfg.index_db.unlink()
    result = rebuild(cfg)

    assert result["stats"]["entities"] == before["entities"]
    assert result["stats"]["chapter_index"] == before["chapter_index"]
    assert all(rel for rel in result["indexed"])


def test_ac14_chapter_numeric_sort():
    """AC-14 章节 ID 排序：CH-0010 排在 CH-0002 之后。"""
    ids = ["CH-0010", "CH-0002", "CH-0001"]
    assert sorted(ids, key=_chapter_sort_key) == ["CH-0001", "CH-0002", "CH-0010"]
    assert vault.chapter_num("CH-0010") == 10


# ------------------------------------------------------------------ 闭环集成


def _prose() -> str:
    return (
        "船搁在滩上。沈砚先下去，脚陷进半尺深的沙。\n\n"
        "废祠在中庭后面，门是开的。里面没有灯，只有一股灯油的味道——没冷。\n\n"
        '"有人刚走。"苏未晞说。\n\n'
        "沈砚把手按在灯台上。铜是温的。\n\n"
        "他没有说话。他把刀从鞘里抽出半寸，又推回去。\n"
    )


def _annotations() -> list[str]:
    return [
        "- 事件: 废祠初见 | 沈砚与苏未晞登岸进入西岸废祠，发现灯台尚温 | 参与:CHAR-0001,CHAR-0002 | 地点:LOC-0002 | 结果:确认有人刚离开",
        "- 状态: CHAR-0001 | 位置=白骨渡西岸废祠 | 伤势=左肩旧伤未处理 | 生死=存活 | 认知=废祠里有人刚离开",
        "- 摘要: 沈砚在西岸废祠发现灯油未冷，确认有人刚离开。",
    ]


def prepare_published_chapter(orch, vault_dir):
    make_chapter(vault_dir, "CH-0003")
    orch.rebuild_index()
    pre = orch.preflight("CH-0003")
    assert pre.verdict == "ACCEPTED", pre.issues
    orch.write("CH-0003")
    compose_draft(vault_dir, "CH-0003", _prose(), _annotations())
    review = orch.review("CH-0003")
    assert review.verdict == "ACCEPTED", review.issues
    return orch.publish("CH-0003", confirmed=True)


def test_full_loop_with_guards(cfg, orch, vault_dir):
    """完整闭环：大纲 → preflight → 草稿 → 审核 → 发布 → 记忆 → 审计 → 回滚。"""
    publish = prepare_published_chapter(orch, vault_dir)
    manuscript = cfg.vault / "08_Manuscripts/Volume_01/CH-0003.md"
    assert manuscript.exists()
    assert "status: published" in manuscript.read_text(encoding="utf-8")

    result = orch.commit_memory("CH-0003")
    assert result.proposals
    assert any(p["kind"] == "event" for p in result.proposals)

    applied = orch.apply_memory("CH-0003", approve="all")
    assert applied.operation_id
    assert applied.applied
    assert "status: memory_committed" in manuscript.read_text(encoding="utf-8")

    event = cfg.vault / "07_Entity_Graph/Events/EVT-CH-0003-001.md"
    assert event.exists()
    assert "废祠初见" in event.read_text(encoding="utf-8")

    audit = orch.audit_chapter("CH-0003")
    assert audit["verdict"] == "CLEAN", audit

    rolled = orch.rollback(applied.operation_id)
    assert not event.exists()
    assert "status: published" in manuscript.read_text(encoding="utf-8")
    assert rolled["restored"] or rolled["removed"]


def test_skeleton_draft_cannot_be_published(cfg, orch, vault_dir):
    """离线骨架草稿必须被审核挡下，不能发布。"""
    make_chapter(vault_dir, "CH-0003")
    orch.rebuild_index()
    orch.preflight("CH-0003")
    write_result = orch.write("CH-0003")
    assert write_result.skeleton

    review = orch.review("CH-0003")
    assert review.verdict == "REVISION_REQUIRED"
    with pytest.raises(WorkflowError):
        orch.publish("CH-0003", confirmed=True)


def test_cannot_write_without_preflight(cfg, orch, vault_dir):
    """未通过 Preflight 不允许写作。"""
    make_chapter(vault_dir, "CH-0003")
    orch.rebuild_index()
    with pytest.raises(WorkflowError):
        orch.write("CH-0003")


def test_unapproved_outline_blocks_preflight(cfg, orch, vault_dir):
    """大纲未获作者批准：preflight 不给 ACCEPTED。"""
    make_chapter(vault_dir, "CH-0003")
    path = vault_dir / "04_Outline/Chapters/CH-0003.md"
    text = path.read_text(encoding="utf-8").replace("- [x] 已批准写作", "- [ ] 已批准写作")
    path.write_text(text, encoding="utf-8")
    orch.rebuild_index()

    result = orch.preflight("CH-0003")
    assert result.verdict != "ACCEPTED"
    assert any("批准" in i.detail for i in result.issues)


def test_unknown_cast_entity_surfaces_in_preflight(cfg, orch, vault_dir):
    """大纲里写了不存在的实体：preflight 报出来。"""
    make_chapter(vault_dir, "CH-0003")
    path = vault_dir / "04_Outline/Chapters/CH-0003.md"
    text = path.read_text(encoding="utf-8").replace("- CHAR-0002", "- CHAR-9999")
    path.write_text(text, encoding="utf-8")
    orch.rebuild_index()

    result = orch.preflight("CH-0003")
    assert any("CHAR-9999" in i.detail for i in result.issues)
    assert result.verdict != "ACCEPTED"


def vault_dir_file(cfg, rel: str):
    return cfg.vault / rel
