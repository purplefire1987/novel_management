"""发布协议四类记录：哈希稳定性、规范形式、日志状态机。

这里刻意不碰文件系统——记录本身的错（哈希不稳、自引用、顺序抖动）在集成测试里
只会表现为「发布莫名其妙被拒」，很难定位。先在小处钉死。
"""

from __future__ import annotations

import json

import pytest

from novel_system.canonical import package_hash
from novel_system.errors import UsageError
from novel_system.release.model import (
    STEP_NAMES,
    ActivePointer,
    Change,
    Journal,
    ManifestFile,
    Receipt,
    ReleaseManifest,
    ReleasePlan,
)


def _plan(**kw) -> ReleasePlan:
    base = dict(
        release_id="REL-000001",
        base_release=None,
        changes=[Change(path="03_Manuscripts/Drafts/CH-0001.md", op="add", content_hash="a" * 64)],
        schema_version="0.9.0",
        index_config_version="1/1",
        prepared_at="2026-01-01T00:00:00+00:00",
    )
    base.update(kw)
    return ReleasePlan(**base)


# --------------------------------------------------------------------------
# 步骤表
# --------------------------------------------------------------------------

def test_step_table_covers_1_to_22_without_gaps():
    """PRD §17.4 是 22 步。缺号或多号都会让日志的「已完成 20/22」变成假话。"""
    assert sorted(STEP_NAMES) == list(range(1, 23))
    assert all(STEP_NAMES[i].strip() for i in range(1, 23))


def test_every_step_name_is_distinct():
    assert len(set(STEP_NAMES.values())) == 22


# --------------------------------------------------------------------------
# 计划哈希
# --------------------------------------------------------------------------

def test_plan_hash_is_stable_across_instances():
    assert _plan().hash == _plan().hash


def test_plan_hash_changes_with_content_hash():
    """批准绑的是内容字节的哈希：字节变了，计划哈希必须变。"""
    other = _plan(changes=[Change(path="03_Manuscripts/Drafts/CH-0001.md", op="add", content_hash="b" * 64)])
    assert other.hash != _plan().hash


def test_plan_hash_changes_with_op():
    other = _plan(changes=[Change(path="03_Manuscripts/Drafts/CH-0001.md", op="modify", content_hash="a" * 64)])
    assert other.hash != _plan().hash


def test_plan_hash_ignores_dict_key_order():
    """规范形式按键排序：同一个对象的哈希不能随构造顺序抖动。"""
    payload = _plan().as_dict()
    shuffled = dict(reversed(list(payload.items())))
    assert package_hash(payload) == package_hash(shuffled)


def test_plan_hash_covers_approval_refs():
    a = _plan(text_approvals=["APR-000001"])
    b = _plan(text_approvals=["APR-000002"])
    assert a.hash != b.hash


def test_plan_bytes_are_compact_utf8_without_escaped_chinese():
    """中文不转义：落盘的 JSON 要能直接读，且字节哈希 == package_hash。"""
    plan = _plan(release_id="REL-000002", removals=["05_Ledgers/旧台账.md"])
    data = plan.bytes()
    assert "旧台账".encode() in data
    assert "\\u" not in data.decode("utf-8")
    assert b" " not in data.replace('"'.encode(), b""), "紧凑分隔符，不许有多余空白"
    assert json.loads(data.decode("utf-8"))["removals"] == ["05_Ledgers/旧台账.md"]


def test_plan_roundtrip_preserves_hash():
    plan = _plan(text_approvals=["APR-000001"])
    again = ReleasePlan.from_dict(json.loads(plan.bytes().decode("utf-8")))
    assert again.hash == plan.hash


def test_first_release_has_null_base():
    assert _plan().as_dict()["base_release"] is None
    assert _plan(base_release="REL-000009").as_dict()["base_release"] == "REL-000009"


# --------------------------------------------------------------------------
# 清单
# --------------------------------------------------------------------------

def _manifest(**kw) -> ReleaseManifest:
    base = dict(
        release_id="REL-000001",
        base_release=None,
        release_plan_hash="c" * 64,
        canon_revision="d" * 40,
        index_build_id="IDX-000001",
        index_bytes_hash="e" * 64,
        files=[ManifestFile(path="03_Manuscripts/Drafts/CH-0001.md", sha256="f" * 64, bytes=12)],
        approval_refs=["APR-000001"],
        tool_version="novel-system/0.9.0",
        schema_version="0.9.0",
        created_at="2026-01-01T00:00:00+00:00",
    )
    base.update(kw)
    return ReleaseManifest(**base)


def test_manifest_does_not_reference_itself():
    """写进自己的哈希就是自引用，永远算不出来（PRD §17.2 末段）。"""
    payload = _manifest().as_dict()
    assert "manifest_sha256" not in payload
    assert "hash" not in payload
    # 清单不带自己的父提交，免得「父提交 = 本清单产物」这类循环
    assert "parent_revision" not in payload


def test_manifest_hash_is_stable_and_order_independent_in_files():
    one = _manifest()
    assert one.hash == _manifest().hash
    # files 顺序不同 = 不同清单：清单是事实记录，不做集合化
    reordered = _manifest(
        files=[
            ManifestFile(path="01_Canon/x.md", sha256="1" * 64, bytes=1),
            ManifestFile(path="03_Manuscripts/Drafts/CH-0001.md", sha256="f" * 64, bytes=12),
        ]
    )
    assert reordered.hash != one.hash


def test_manifest_roundtrip_preserves_hash():
    manifest = _manifest()
    again = ReleaseManifest.from_dict(json.loads(manifest.bytes().decode("utf-8")))
    assert again.hash == manifest.hash
    assert again.files[0].bytes == 12


# --------------------------------------------------------------------------
# 事务日志
# --------------------------------------------------------------------------

def test_journal_marks_steps_and_reports_last_done():
    journal = Journal(txn_id="TXN-000001", release_id="REL-000001")
    for step in (1, 2, 3):
        journal.set_step(step, "done", at="2026-01-01T00:00:00+00:00")
    assert journal.done_steps == [1, 2, 3]
    assert journal.last_done() == 3


def test_journal_is_sorted_even_when_marked_out_of_order():
    """恢复流程会跳着标记（例如 finally 里补第 20 步），日志顺序不能跟着乱。"""
    journal = Journal(txn_id="TXN-000001", release_id="REL-000001")
    journal.set_step(20, "done")
    journal.set_step(4, "done")
    journal.set_step(2, "done")
    assert [s.step for s in journal.steps] == [2, 4, 20]


def test_journal_remark_overwrites_not_duplicates():
    journal = Journal(txn_id="TXN-000001", release_id="REL-000001")
    journal.set_step(5, "failed", detail="第一次")
    journal.set_step(5, "done", detail="重试成功")
    assert len(journal.steps) == 1
    assert journal.steps[0].status == "done"
    assert journal.steps[0].detail == "重试成功"


def test_journal_rejects_unknown_step_and_status():
    journal = Journal(txn_id="TXN-000001", release_id="REL-000001")
    with pytest.raises(UsageError):
        journal.set_step(23, "done")
    with pytest.raises(UsageError):
        journal.set_step(1, "finished")


def test_failed_steps_are_reported_separately():
    journal = Journal(txn_id="TXN-000001", release_id="REL-000001")
    journal.set_step(1, "done")
    journal.set_step(2, "failed", detail="基线不符")
    assert journal.done_steps == [1]
    assert journal.failed_steps == [2]


def test_journal_roundtrip_keeps_step_names():
    journal = Journal(txn_id="TXN-000001", release_id="REL-000001", state="READY_TO_ACTIVATE")
    journal.set_step(18, "done", at="2026-01-01T00:00:00+00:00", detail="active=REL-000001")
    again = Journal.from_dict(json.loads(journal.bytes().decode("utf-8")))
    assert again.state == "READY_TO_ACTIVATE"
    assert again.steps[0].name == STEP_NAMES[18]
    assert again.last_done() == 18


def test_journal_skips_done_steps_property_ignores_skipped():
    journal = Journal(txn_id="TXN-000001", release_id="REL-000001")
    journal.set_step(1, "skipped", detail="指针已指向本版")
    journal.set_step(2, "done")
    assert journal.done_steps == [2]


# --------------------------------------------------------------------------
# 回执与指针
# --------------------------------------------------------------------------

def test_receipt_marks_repaired_and_omits_empty_fields():
    plain = Receipt(
        receipt_id="RCPT-000001",
        release_id="REL-000001",
        manifest_hash="a" * 64,
        canon_revision="b" * 40,
        index_build_id="IDX-000001",
        created_at="2026-01-01T00:00:00+00:00",
        base_release=None,
        active_since="",
        repaired=True,
    )
    payload = plain.as_dict()
    assert payload["repaired"] is True
    assert "base_release" not in payload, "首次发布不写 base_release，不写空串"
    assert "active_since" not in payload
    assert payload["warnings"] == []


def test_receipt_roundtrip_keeps_warnings():
    receipt = Receipt(
        receipt_id="RCPT-000002",
        release_id="REL-000002",
        manifest_hash="a" * 64,
        canon_revision="b" * 40,
        index_build_id="IDX-000002",
        created_at="2026-01-01T00:00:00+00:00",
        base_release="REL-000001",
        warnings=["对外平台内容未同步，需人工处理"],
    )
    again = Receipt.from_dict(json.loads(json.dumps(receipt.as_dict(), ensure_ascii=False)))
    assert again.warnings == ["对外平台内容未同步，需人工处理"]
    assert again.base_release == "REL-000001"


def test_pointer_roundtrip():
    pointer = ActivePointer(
        release_id="REL-000001",
        canon_revision="b" * 40,
        index_build_id="IDX-000001",
        manifest_sha256="a" * 64,
        activated_at="2026-01-01T00:00:00+00:00",
    )
    again = ActivePointer.from_dict(pointer.as_dict())
    assert again == pointer
    assert set(pointer.as_dict()) == {
        "release_id",
        "canon_revision",
        "index_build_id",
        "manifest_sha256",
        "activated_at",
    }


# --------------------------------------------------------------------------
# 与 T02 的 schema 契约对齐
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("name", "builder"),
    [
        ("release_plan", _plan),
        ("release_manifest", _manifest),
        (
            "transaction_journal",
            lambda: _journal_with_steps(),
        ),
        (
            "execution_receipt",
            lambda: Receipt(
                receipt_id="RCPT-000001",
                release_id="REL-000001",
                manifest_hash="a" * 64,
                canon_revision="b" * 40,
                index_build_id="IDX-000001",
                created_at="2026-01-01T00:00:00+00:00",
                active_since="2026-01-01T00:00:00+00:00",
                repaired=True,
            ),
        ),
    ],
)
def test_records_satisfy_their_schema(name: str, builder) -> None:
    """四类发布记录必须满足 T02 定的契约。

    T02 时这四份 schema 的字段是从 PRD §17.4 与 §18.2 反推的（known_risks R-16）；
    T06 真写记录时如果对不上，要么 schema 要改，要么实现要改——**不能两边都当没看见**。
    """
    from novel_system.schema import SchemaStore

    problems = SchemaStore().check(name, builder().as_dict())
    assert problems == [], f"{name} 不符合契约：{problems}"


def _journal_with_steps() -> Journal:
    journal = Journal(
        txn_id="TXN-000001",
        release_id="REL-000001",
        state="ACTIVE",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:05:00+00:00",
    )
    journal.set_step(1, "done", at="2026-01-01T00:00:01+00:00", detail="计划已建")
    journal.set_step(18, "done", at="2026-01-01T00:04:00+00:00", detail="active=REL-000001")
    return journal
