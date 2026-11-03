"""录入与版本控制相关验收：AC-15 ~ AC-18。"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_system.workflow import ingest
from novel_system.workflow.state_machine import WorkflowError

RAW = """《测试卷》旧稿
作者：某人

第一章 灯市

沈砚到的时候，天还没黑透。街两边的灯架子已经支起来了。

第二章 断指

老陆在城西的破庙里等他。庙里的神像早就没了头。

第三章 河心

船到河心的时候，雾起来了。苏未晞站在船头，手里的灯符只剩一线红。
"""


def _raw_file(tmp_path: Path, name: str = "old_draft.txt") -> Path:
    path = tmp_path / name
    path.write_text(RAW, encoding="utf-8")
    return path


# ---------------------------------------------------------------- AC-15


def test_ac15_ingest_splits_chapters_and_marks_published(orch):
    """AC-15 原始稿件录入：按「第X章」切章，正文入库即为 published。"""
    raw = _raw_file(Path(orch.cfg.vault).parent)
    result = orch.ingest(str(raw))

    assert [c["title"] for c in result.chapters] == ["灯市", "断指", "河心"]
    assert result.chapters[0]["chapter_id"] == "CH-0003"
    assert result.operation_id, "录入必须留下可回滚的操作号"

    manuscript = orch.cfg.abs(result.chapters[0]["manuscript"])
    assert manuscript.is_file()
    assert orch.cfg.abs(result.chapters[0]["outline"]).is_file()
    assert orch.cfg.abs(result.log_path).is_file()

    row = orch.index.get_chapter("CH-0003")
    assert row["status"] == "published"
    assert row["word_count"] > 0


def test_ac16_ingest_dry_run_writes_nothing(orch):
    """AC-16 预演模式只算不写。"""
    raw = _raw_file(Path(orch.cfg.vault).parent)
    log_dir = orch.cfg.abs("10_Change_Log/Ingest")
    # 库里可能已有历史批次日志，只能比对「预演前后有没有新增」
    before = {p.name for p in log_dir.glob("*.md")} if log_dir.is_dir() else set()

    result = orch.ingest(str(raw), dry_run=True)

    assert result.chapters, "预演也要给出切章结果"
    assert not orch.cfg.abs(result.chapters[0]["manuscript"]).exists()
    after = {p.name for p in log_dir.glob("*.md")} if log_dir.is_dir() else set()
    assert after == before, "预演不得写入批次日志"


def test_ac17_duplicate_source_is_refused(orch):
    """AC-17 同一文件重复录入：默认拒绝，--force 才放行。"""
    raw = _raw_file(Path(orch.cfg.vault).parent)
    orch.ingest(str(raw))

    with pytest.raises(WorkflowError) as exc:
        orch.ingest(str(raw))
    assert "已录入过" in str(exc.value)

    forced = orch.ingest(str(raw), force=True)
    assert forced.chapters[0]["chapter_id"] == "CH-0006", "强制重录应续接章号，不覆盖旧章"


# ---------------------------------------------------------------- AC-18


def test_ac18_annotate_enables_offline_memory_extraction(orch):
    """AC-18 录入后的正文补标注 → 离线记忆抽取可用 → 事件落库。"""
    raw = _raw_file(Path(orch.cfg.vault).parent)
    result = orch.ingest(str(raw))
    chapter_id = result.chapters[0]["chapter_id"]

    annotated = orch.annotate(
        chapter_id,
        [
            "- 摘要: 沈砚在灯市看到只挂白灯的灯架。",
            "- 事件: 白灯现世 | 沈砚在灯市见到只画眼睛的白灯 | 参与:CHAR-0001 | 地点:LOC-0001",
            "- 状态: CHAR-0001 | 位置=渡厄城灯市 | 生死=存活",
        ],
    )
    assert annotated.total == 3

    memory = orch.commit_memory(chapter_id)
    kinds = {p["kind"] for p in memory.proposals}
    assert "event" in kinds, "标注里的「事件」必须能抽出提案"
    assert "summary" in kinds

    # 标注正文必须仍在，且标注行不与正文混在一起
    doc = orch.cfg.abs(result.chapters[0]["manuscript"])
    body = doc.read_text(encoding="utf-8")
    assert "## 作者备注（不进入正文）" in body
    assert body.count("## 正文") == 1


def test_ac19_ingest_rollback_removes_whole_batch(orch):
    """AC-19 录入批次可整体回滚：大纲/正文/批次日志一并删除。"""
    raw = _raw_file(Path(orch.cfg.vault).parent)
    result = orch.ingest(str(raw))
    manuscript = orch.cfg.abs(result.chapters[0]["manuscript"])
    log = orch.cfg.abs(result.log_path)
    assert manuscript.is_file() and log.is_file()

    report = orch.rollback(result.operation_id)

    assert str(result.chapters[0]["manuscript"]) in report["removed"]
    assert not manuscript.exists()
    assert not log.exists()
    assert not orch.cfg.abs(result.chapters[1]["manuscript"]).exists()


def test_ac20_ingest_unknown_title_format_falls_back_to_single(orch):
    """AC-20 没有章节标题的稿子：整篇作为一章，不硬切碎。"""
    path = Path(orch.cfg.vault).parent / "no_heading.txt"
    path.write_text("这是一段没有章节标题的短稿。\n第二段继续。\n", encoding="utf-8")

    result = orch.ingest(str(path), title="无名短稿")

    assert len(result.chapters) == 1
    assert result.chapters[0]["title"] == "无名短稿"
    assert result.mode == "single"


def test_ingest_rejects_unknown_split_mode(orch):
    raw = _raw_file(Path(orch.cfg.vault).parent)
    with pytest.raises(WorkflowError):
        orch.ingest(str(raw), mode="whatever")


def test_normalize_annotations_rejects_unknown_kind():
    with pytest.raises(WorkflowError):
        ingest.normalize_annotations(["- 随便: 内容"])
    assert ingest.normalize_annotations(["事件: 某事 | 参与:CHAR-0001"]) == ["- 事件: 某事 | 参与:CHAR-0001"]
