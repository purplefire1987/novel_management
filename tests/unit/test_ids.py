"""稳定 ID 分配（PRD §8.1）。

钉住四条：不复用空号、带病不发号、并发有锁、每次分配留审计。
「不复用空号」最容易实现错——很多人会顺手从洞里补号，那会让历史
引用在删档后指向另一个对象。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_system.errors import ConflictError, UsageError
from novel_system.ids import IdAllocator, IdRegistry, is_compound_id, resolve_prefix, split_id


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _card(eid: str, name: str = "示例") -> str:
    return f"---\nid: {eid}\nname: {name}\n---\n\n正文。\n"


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    project = root / "10_Projects" / "p1"
    _write(project / "01_Canon" / "Characters" / "CHAR-0001_甲.md", _card("CHAR-0001", "甲"))
    _write(project / "01_Canon" / "Characters" / "CHAR-0002_乙.md", _card("CHAR-0002", "乙"))
    _write(project / "01_Canon" / "Locations" / "LOC-0001_丙.md", _card("LOC-0001", "丙"))
    _write(project / "Templates" / "素材卡模板.md", _card("{{id}}", "模板"))
    # 复合型：前缀正确但形态不合法，不应计入 EVT 号段
    _write(project / "05_Ledgers" / "Events" / "EVT-CH-0001-001_丁.md", _card("EVT-CH-0001-001", "丁"))
    return root


@pytest.fixture()
def runtime(tmp_path: Path) -> Path:
    return tmp_path / "runtime"


# ---- 解析 ----------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("CHAR-0001", ("CHAR", 1)),
        ("PLAN-CH-000102", ("PLAN-CH", 102)),
        ("char-0001", None),
        ("CHAR-1", None),
        ("CHAR-0001-002", None),
        ("随机名", None),
    ],
)
def test_split_id(raw: str, expected) -> None:
    assert split_id(raw) == expected


def test_compound_detection() -> None:
    assert is_compound_id("EVT-CH-0001-001")
    assert is_compound_id("LORE-20261104-000000")
    assert not is_compound_id("CHAR-0001")
    assert not is_compound_id("随便")


@pytest.mark.parametrize(
    "kind,prefix",
    [
        ("CHARACTER", "CHAR"),
        ("character", "CHAR"),
        ("char", "CHAR"),
        ("LOCATION", "LOC"),
        ("CHANGE_REQUEST", "CR"),
        ("PLAN-CH", "PLAN-CH"),
        ("plan-ch", "PLAN-CH"),
        ("FORESHADOWING", "FORESHADOW"),
    ],
)
def test_resolve_prefix(kind: str, prefix: str) -> None:
    assert resolve_prefix(kind) == prefix


def test_unknown_prefix_raises() -> None:
    with pytest.raises(UsageError):
        resolve_prefix("DRAGON")
    with pytest.raises(UsageError):
        resolve_prefix("")


# ---- 扫描 ----------------------------------------------------------------

def test_registry_finds_ids_and_skips_placeholder(vault: Path) -> None:
    registry = IdRegistry(vault)
    ids = {r.id for r in registry.records}
    assert {"CHAR-0001", "CHAR-0002", "LOC-0001"} <= ids
    assert not any("{{" in i for i in ids), "模板占位符不是身份"


def test_registry_ignores_compound_for_sequence(vault: Path) -> None:
    registry = IdRegistry(vault)
    assert registry.max_number("EVT") == 0, "复合型不该把 EVT 号段抬到 0001 之外"
    assert [c[0] for c in registry.compounds] == ["EVT-CH-0001-001"]


def test_max_number(vault: Path) -> None:
    registry = IdRegistry(vault)
    assert registry.max_number("CHAR") == 2
    assert registry.max_number("LOC") == 1
    assert registry.max_number("ITEM") == 0


def test_collisions_detected(tmp_path: Path) -> None:
    root = tmp_path / "v"
    _write(root / "A" / "ASSET-0001_x.md", _card("ASSET-0001"))
    _write(root / "B" / "ASSET-0001_y.md", _card("ASSET-0001"))
    collisions = IdRegistry(root).collisions()
    assert "ASSET-0001" in collisions
    assert len({r.source for r in collisions["ASSET-0001"]}) == 2


def test_frontmatter_wins_over_filename(tmp_path: Path) -> None:
    """文件名写 CHAR-0001、frontmatter 写 CHAR-0009：身份是 0009。

    早期实现两个号都占，结果 `VOL-0001_示例细纲.md`（id 为 VOL-0001-S1-DETAIL）
    与另外四份规划文档被判成同一个 ID——假重复比漏报更能误导人。
    """
    root = tmp_path / "v"
    _write(root / "CHAR-0001_甲.md", _card("CHAR-0009"))
    registry = IdRegistry(root)
    assert {r.id for r in registry.records} == {"CHAR-0009"}
    assert registry.mismatched == [("CHAR-0001_甲.md", "CHAR-0009", "CHAR-0001")]


def test_filename_used_only_without_frontmatter_id(tmp_path: Path) -> None:
    root = tmp_path / "v"
    _write(root / "CHAR-0001_无 id.md", "---\nname: 无 id\n---\n正文\n")
    assert {r.id for r in IdRegistry(root).records} == {"CHAR-0001"}


def test_same_prefixed_filenames_are_not_collisions(tmp_path: Path) -> None:
    """一个卷的多份文档各用不同 id，不该因为文件名同前缀被判重复。"""
    root = tmp_path / "v"
    for name, eid in (
        ("VOL-0001_示例细纲.md", "VOL-0001-S1-DETAIL"),
        ("VOL-0001_示例章节表.md", "VOL-0001-PLAN"),
        ("VOL-0001_示例设计.md", "VOL-0001-DESIGN"),
    ):
        _write(root / name, _card(eid))
    registry = IdRegistry(root)
    assert registry.collisions() == {}
    assert {c[0] for c in registry.compounds} == {"VOL-0001-S1-DETAIL", "VOL-0001-PLAN", "VOL-0001-DESIGN"}


# ---- 分级查重（T07 发现：发布过一次就发不出号） ----------------------------

def _areas(root: Path) -> dict[str, list[Path]]:
    return {
        "formal": [root / "03_Manuscripts"],
        "pending": [root / "07_Staging"],
        "archive": [root / "90_Archive"],
    }


def test_pending_copy_is_not_a_conflict(tmp_path: Path) -> None:
    """正式区一份 + staging 一份 = 同一对象在途，不算撞号。

    这是 T07 的真实现场：第一批发布后，正式区与 staging 各有一份 CH-0001，
    旧实现把它判成重号，第二批连号都发不出来。
    """
    root = tmp_path / "v"
    _write(root / "03_Manuscripts" / "CH-0001.md", _card("CH-0001"))
    _write(root / "07_Staging" / "TASK-1" / "CH-0001.md", _card("CH-0001"))

    registry = IdRegistry(root, areas=_areas(root))
    assert registry.collisions(), "物理上确实是两处，得看得见"
    assert registry.conflicts() == {}, "但正式区只有一份，不是撞号"
    assert set(registry.pending_copies()) == {"CH-0001"}
    assert [r.area for r in registry.records if r.id == "CH-0001"] == ["formal", "pending"]


def test_two_formal_copies_are_a_conflict(tmp_path: Path) -> None:
    root = tmp_path / "v"
    _write(root / "03_Manuscripts" / "CH-0001.md", _card("CH-0001"))
    _write(root / "03_Manuscripts" / "CH-0001_copy.md", _card("CH-0001"))
    registry = IdRegistry(root, areas=_areas(root))
    assert set(registry.conflicts()) == {"CH-0001"}


def test_out_of_area_copy_is_tainted_not_conflict(tmp_path: Path) -> None:
    """区划外那份（Vault 根的旧归档、`99_Archive/legacy_v1` 之类）不算撞号。

    算成撞号的结果是「整库快照里有过的号，今天一个都发不出来」。
    但它也不能隐身：进 tainted，报告里看得见。
    """
    root = tmp_path / "v"
    _write(root / "03_Manuscripts" / "CH-0001.md", _card("CH-0001"))
    _write(root / "99_Archive" / "legacy" / "CH-0001.md", _card("CH-0001"))
    registry = IdRegistry(root, areas=_areas(root))
    assert registry.conflicts() == {}
    assert set(registry.tainted()) == {"CH-0001"}
    assert {r.area for r in registry.collisions()["CH-0001"]} == {"formal", "other"}


def test_archive_copy_is_tainted_not_conflict(tmp_path: Path) -> None:
    root = tmp_path / "v"
    _write(root / "03_Manuscripts" / "CH-0001.md", _card("CH-0001"))
    _write(root / "90_Archive" / "CH-0001.md", _card("CH-0001"))
    registry = IdRegistry(root, areas=_areas(root))
    assert registry.conflicts() == {}
    assert set(registry.tainted()) == {"CH-0001"}


def test_without_areas_everything_is_formal(tmp_path: Path) -> None:
    """忘了传区划时按「全库皆正式区」处理：宁可从严，不能悄悄放松。"""
    root = tmp_path / "v"
    _write(root / "03_Manuscripts" / "CH-0001.md", _card("CH-0001"))
    _write(root / "07_Staging" / "CH-0001.md", _card("CH-0001"))
    assert set(IdRegistry(root).conflicts()) == {"CH-0001"}


def test_unknown_area_name_rejected(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="未知 ID 区划"):
        IdRegistry(tmp_path, areas={"formal": [], "trash": [tmp_path]})


# ---- 分配 ----------------------------------------------------------------

def test_allocate_continues_sequence(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="test")
    assert alloc.allocate("CHAR") == ["CHAR-000003"]


def test_allocate_count(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="test")
    assert alloc.allocate("CHAR", count=3) == ["CHAR-000003", "CHAR-000004", "CHAR-000005"]


def test_freed_numbers_are_not_reused(vault: Path, runtime: Path) -> None:
    """发过 0003 之后删掉 0002，下一个必须是 0004。

    只按现存最大号取号会补到 0002——那等于让「CHAR-0002」这个历史引用
    指向另一个对象。所以取号要同时看审计记录。
    """
    alloc = IdAllocator(vault, runtime, actor="test", lock_timeout=0.2)
    assert alloc.allocate("CHAR") == ["CHAR-000003"]
    (vault / "10_Projects" / "p1" / "01_Canon" / "Characters" / "CHAR-0002_乙.md").unlink()
    assert alloc.allocate("CHAR") == ["CHAR-000004"]


def test_history_survives_file_deletion(vault: Path, runtime: Path) -> None:
    """连最大的那份也删掉，号段仍不回退。"""
    alloc = IdAllocator(vault, runtime, actor="test", lock_timeout=0.2)
    alloc.allocate("CHAR", count=2)  # 0003, 0004
    for name in ("CHAR-0001_甲.md", "CHAR-0002_乙.md"):
        (vault / "10_Projects" / "p1" / "01_Canon" / "Characters" / name).unlink()
    assert alloc.allocate("CHAR") == ["CHAR-000005"]


def test_allocate_records_audit_line(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="writer", lock_timeout=0.2)
    alloc.allocate("CHAR", count=2, reason="新建人物")
    lines = (runtime / "id-allocations.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["ids"] == ["CHAR-000003", "CHAR-000004"]
    assert row["actor"] == "writer"
    assert row["reason"] == "新建人物"
    assert row["previous_max"] == 2


def test_dry_run_does_not_write(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="test")
    assert alloc.allocate("CHAR", dry_run=True) == ["CHAR-000003"]
    assert not (runtime / "id-allocations.jsonl").exists()


def test_history_reads_back(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="test", lock_timeout=0.2)
    alloc.allocate("CHAR")
    alloc.allocate("LOC")
    assert [h["prefix"] for h in alloc.history()] == ["CHAR", "LOC"]


def test_dirty_same_prefix_is_refused(tmp_path: Path, runtime: Path) -> None:
    root = tmp_path / "v"
    _write(root / "A" / "ASSET-0001_x.md", _card("ASSET-0001"))
    _write(root / "B" / "ASSET-0001_y.md", _card("ASSET-0001"))
    alloc = IdAllocator(root, runtime, actor="test")
    with pytest.raises(ConflictError, match="ASSET"):
        alloc.allocate("ASSET")


def test_dirty_other_prefix_does_not_block(tmp_path: Path, runtime: Path) -> None:
    """ASSET 撞号不该让 CHAR 也发不出号——两件事。"""
    root = tmp_path / "v"
    _write(root / "A" / "ASSET-0001_x.md", _card("ASSET-0001"))
    _write(root / "B" / "ASSET-0001_y.md", _card("ASSET-0001"))
    _write(root / "C" / "CHAR-0001_甲.md", _card("CHAR-0001"))
    alloc = IdAllocator(root, runtime, actor="test")
    assert alloc.allocate("CHAR") == ["CHAR-000002"]


def test_allow_dirty_flag(vault: Path, runtime: Path, tmp_path: Path) -> None:
    root = tmp_path / "v2"
    _write(root / "A" / "ASSET-0001_x.md", _card("ASSET-0001"))
    _write(root / "B" / "ASSET-0001_y.md", _card("ASSET-0001"))
    alloc = IdAllocator(root, runtime, actor="test")
    assert alloc.allocate("ASSET", allow_dirty=True) == ["ASSET-000002"]


def test_allocate_survives_pending_copy(tmp_path: Path, runtime: Path) -> None:
    """发布过一次之后，staging 里那份副本不该把后续发号卡死（T07 回归）。"""
    root = tmp_path / "v"
    _write(root / "03_Manuscripts" / "CH-0001.md", _card("CH-0001"))
    _write(root / "07_Staging" / "TASK-1" / "CH-0001.md", _card("CH-0001"))
    alloc = IdAllocator(root, runtime, actor="test", areas=_areas(root))
    assert alloc.allocate("chapter", count=2) == ["CH-000002", "CH-000003"]


def test_allocate_still_refuses_two_formal_copies(tmp_path: Path, runtime: Path) -> None:
    """区划不能把「真撞号」也一起放过去。"""
    root = tmp_path / "v"
    _write(root / "03_Manuscripts" / "CH-0001.md", _card("CH-0001"))
    _write(root / "03_Manuscripts" / "CH-0001_copy.md", _card("CH-0001"))
    alloc = IdAllocator(root, runtime, actor="test", areas=_areas(root))
    with pytest.raises(ConflictError, match="正式区"):
        alloc.allocate("chapter")


def test_lock_is_honored(vault: Path, runtime: Path) -> None:
    """锁被占时不该硬闯，也不该静默降级。"""
    lock = runtime / "locks" / "id-allocate.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("pid=999 at=0", encoding="utf-8")
    alloc = IdAllocator(vault, runtime, actor="test", lock_timeout=0.1)
    with pytest.raises(ConflictError, match="另一个进程"):
        alloc.allocate("CHAR")


def test_missing_runtime_refused(vault: Path) -> None:
    alloc = IdAllocator(vault, None, actor="test")
    with pytest.raises(UsageError, match="Runtime"):
        alloc.allocate("CHAR")


def test_count_must_be_positive(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="test")
    with pytest.raises(UsageError):
        alloc.allocate("CHAR", count=0)


# ---- 号段上限（CH 的旧稿存档段）--------------------------------------------
#
# 2026-11-22 全库重编号曾把旧稿 28 份压进 CH-000003..030，与新正文同号段。
# 恢复独立段后（CH-001001..001028），`allocate` 若只取「最大号 + 1」，
# 新正文第一章会拿到 1029——直接落进存档段。下面三条钉住这个边界。


def _ch_vault(tmp_path: Path, ids: list[str]) -> Path:
    root = tmp_path / "vault"
    for i, eid in enumerate(ids):
        band = "90_Archive" if int(eid.split("-")[1]) > 999 else "03_Manuscripts"
        _write(root / "10_Projects" / "p1" / band / f"{eid}_第{i}.md", _card(eid, f"第{i}"))
    return root / "10_Projects" / "p1"


def test_ch_band_ignores_archive_segment(tmp_path: Path, runtime: Path) -> None:
    """旧稿占 1001+ 时，新正文仍从 000001 起，不能拿到 1002。"""
    root = _ch_vault(tmp_path, ["CH-001001", "CH-001002"])
    alloc = IdAllocator(root, runtime, actor="test", areas=_areas(root))
    assert alloc.allocate("chapter") == ["CH-000001"]


def test_ch_band_counts_only_in_band_numbers(tmp_path: Path, runtime: Path) -> None:
    """段内已有 000007 时下一个是 000008，段外的 001028 不参与取号。"""
    root = _ch_vault(tmp_path, ["CH-000007", "CH-001028"])
    alloc = IdAllocator(root, runtime, actor="test", areas=_areas(root))
    assert alloc.allocate("chapter") == ["CH-000008"]


def test_ch_band_exhausted_refuses(tmp_path: Path, runtime: Path) -> None:
    """段满时拒绝发号。悄悄越过会把新正文写进存档段，那是不归路。"""
    root = _ch_vault(tmp_path, ["CH-000999"])
    alloc = IdAllocator(root, runtime, actor="test", areas=_areas(root))
    with pytest.raises(UsageError, match="活文档号段已用尽"):
        alloc.allocate("chapter")


def test_band_limit_does_not_affect_other_prefixes(tmp_path: Path, runtime: Path) -> None:
    """CHAR 没有号段上限，1000 以上照发——上限是按前缀配的，不是全局。"""
    root = _ch_vault(tmp_path, ["CH-000001"])
    _write(root / "03_Manuscripts" / "CHAR-001204_甲.md", _card("CHAR-001204", "甲"))
    alloc = IdAllocator(root, runtime, actor="test", areas=_areas(root))
    assert alloc.allocate("CHAR") == ["CHAR-001205"]


def test_max_number_upper_filters_out_band(tmp_path: Path) -> None:
    """`upper` 只影响统计口径，不改登记表。"""
    root = _ch_vault(tmp_path, ["CH-000005", "CH-001028"])
    reg = IdRegistry(root, areas=_areas(root))
    assert reg.max_number("CH") == 1028
    assert reg.max_number("CH", upper=999) == 5
