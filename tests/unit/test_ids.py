"""稳定 ID 分配（PRD §8.1 / V1.0 §9.2）。

钉住四条：不复用空号、带病不发号、并发有锁、每次分配留审计。
「不复用空号」最容易实现错——很多人会顺手从洞里补号，那会让历史
引用在删档后指向另一个对象。

**形态说明（2026-11-22 裁定后）**：本文件原先全按 V4.0 的 `CHAR-0001` 写。
作者当日裁定按 V1.0 §9.2 全库重排，形态改为 `<前缀>_<六位数字>`，且世界知识
实体**共用一个 `ent_` 号段**（类型归 frontmatter 的 `type` 字段）。因此原先
「CHAR 号段」与「LOC 号段」互相独立的假设不再成立——人物和地点抢同一个 `ent`
号段，这正是下面 fixture 把三份实体编成 `ent_000001..000003` 的原因。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_management.errors import ConflictError, UsageError
from novel_management.ids import IdAllocator, IdRegistry, is_compound_id, resolve_prefix, split_id


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _card(eid: str, name: str = "示例") -> str:
    return f"---\nid: {eid}\nname: {name}\n---\n\n正文。\n"


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    project = root / "01_小说项目" / "novel-001_p1"
    canon = project / "02_世界知识库"
    # 三份实体共用一个 ent 号段：人物两人、地点一人。
    _write(canon / "人物与角色" / "ent_000001_甲.md", _card("ent_000001", "甲"))
    _write(canon / "人物与角色" / "ent_000002_乙.md", _card("ent_000002", "乙"))
    _write(canon / "地点与地理" / "ent_000003_丙.md", _card("ent_000003", "丙"))
    _write(root / "00_作者工作台" / "Templates" / "素材卡模板.md", _card("{{id}}", "模板"))
    # 复合型：看着像 ID 但不是规范形态，不应计入 evt 号段。
    _write(canon / "事件" / "EVT-CH-0001-001_丁.md", _card("EVT-CH-0001-001", "丁"))
    return root


@pytest.fixture()
def runtime(tmp_path: Path) -> Path:
    return tmp_path / "runtime"


# ---- 解析 ----------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ent_000001", ("ent", 1)),
        ("ch_001001", ("ch", 1001)),
        ("plan_000102", ("plan", 102)),
        ("ent_000001 ", ("ent", 1)),          # 两侧空白要容忍
        ("ENT_000001", None),                  # 大写不合法
        ("ent_1", None),                       # 位数不足
        ("ent_000001_002", None),              # 双段
        ("CHAR-0001", None),                   # V4.0 旧形态不是规范 ID
        ("随机名", None),
    ],
)
def test_split_id(raw: str, expected) -> None:
    assert split_id(raw) == expected


def test_compound_detection() -> None:
    """复合号识别的是 V4.0 旧形态，与「不是 ID」是两回事。

    后者会让人在查重时完全隐身，前者至少能被报告出来。
    """
    assert is_compound_id("EVT-CH-0001-001")
    assert is_compound_id("PLAN-VOL-000001")
    assert is_compound_id("VOL-0001-S1")
    assert not is_compound_id("CHAR-0001"), "只隔一段的是旧单号，不是复合号"
    assert not is_compound_id("ent_000001"), "规范形态当然不是复合号"
    assert not is_compound_id("随便")


@pytest.mark.parametrize(
    "kind,prefix",
    [
        # 世界知识实体一律 ent：类型信息归 frontmatter 的 type 字段。
        ("CHARACTER", "ent"),
        ("character", "ent"),
        ("char", "ent"),
        ("LOCATION", "ent"),
        ("item", "ent"),
        ("CREATURE", "ent"),
        # 其余按 V1.0 前缀。
        ("CHAPTER", "ch"),
        ("CHAPTER", "ch"),
        ("CHANGE_REQUEST", "prop"),
        ("FORESHADOWING", "fs"),
        ("ASSET", "asset"),
        ("chapter", "ch"),
    ],
)
def test_resolve_prefix(kind: str, prefix: str) -> None:
    assert resolve_prefix(kind) == prefix


def test_legacy_prefix_is_refused_with_hint() -> None:
    """V4.0 前缀必须报错而不是静默映射——否则同一库里会长出两种形态。"""
    for legacy in ("FAC", "LOC", "PLAN-VOL"):
        with pytest.raises(UsageError, match="V4.0 旧形态"):
            resolve_prefix(legacy)


def test_type_char_returns_ent_not_error() -> None:
    """`CHAR` 是活着的类型名（映射到 ent），不是废弃前缀。

    这条曾经被误判过：审计报告以为 `--type CHAR` 会走废弃分支，实际
    `TYPE_TO_PREFIX` 先命中。走废弃分支的是 `FAC` / `LOC` / `PLAN-VOL`。
    """
    assert resolve_prefix("CHAR") == "ent"


def test_unknown_prefix_raises() -> None:
    with pytest.raises(UsageError):
        resolve_prefix("DRAGON")
    with pytest.raises(UsageError):
        resolve_prefix("")


# ---- 扫描 ----------------------------------------------------------------

def test_registry_finds_ids_and_skips_placeholder(vault: Path) -> None:
    registry = IdRegistry(vault)
    ids = {r.id for r in registry.records}
    assert {"ent_000001", "ent_000002", "ent_000003"} <= ids
    assert not any("{{" in i for i in ids), "模板占位符不是身份"


def test_registry_ignores_compound_for_sequence(vault: Path) -> None:
    registry = IdRegistry(vault)
    assert registry.max_number("evt") == 0, "复合型不该把 evt 号段抬到 0001 之外"
    assert [c[0] for c in registry.compounds] == ["EVT-CH-0001-001"]


def test_max_number(vault: Path) -> None:
    registry = IdRegistry(vault)
    assert registry.max_number("ent") == 3, "人物与地点共用 ent 号段"
    assert registry.max_number("ch") == 0


def test_collisions_detected(tmp_path: Path) -> None:
    root = tmp_path / "v"
    _write(root / "A" / "asset_000001_x.md", _card("asset_000001"))
    _write(root / "B" / "asset_000001_y.md", _card("asset_000001"))
    collisions = IdRegistry(root).collisions()
    assert "asset_000001" in collisions
    assert len({r.source for r in collisions["asset_000001"]}) == 2


def test_frontmatter_wins_over_filename(tmp_path: Path) -> None:
    """文件名写 ent_000001、frontmatter 写 ent_000009：身份是 0009。

    早期实现两个号都占，结果五份 `VOL-0001_*.md` 被判成同一个 ID——
    假重复比漏报更能误导人。
    """
    root = tmp_path / "v"
    _write(root / "ent_000001_甲.md", _card("ent_000009"))
    registry = IdRegistry(root)
    assert {r.id for r in registry.records} == {"ent_000009"}
    assert registry.mismatched == [("ent_000001_甲.md", "ent_000009", "ent_000001")]


def test_filename_used_only_without_frontmatter_id(tmp_path: Path) -> None:
    root = tmp_path / "v"
    _write(root / "ent_000001_无 id.md", "---\nname: 无 id\n---\n正文\n")
    assert {r.id for r in IdRegistry(root).records} == {"ent_000001"}


def test_same_prefixed_filenames_are_not_collisions(tmp_path: Path) -> None:
    """文件名不以 ID 开头时，绝不从文件名猜身份。

    假重复的原始现场：一个卷的三份文档文件名同前缀，旧实现拿文件名兜底，
    三份被判成同一个 id。frontmatter 才是权威。
    """
    root = tmp_path / "v"
    for name, eid in (
        ("第一卷_细纲.md", "plan_000001"),
        ("第一卷_章节表.md", "plan_000002"),
        ("第一卷_设计.md", "plan_000003"),
    ):
        _write(root / name, _card(eid))
    registry = IdRegistry(root)
    assert registry.collisions() == {}
    assert {r.id for r in registry.records} == {"plan_000001", "plan_000002", "plan_000003"}


# ---- 分级查重（T07 发现：发布过一次就发不出号） ----------------------------

def _areas(root: Path) -> dict[str, list[Path]]:
    return {
        "formal": [root / "04_正文"],
        "pending": [root / "_staging"],
        "archive": [root / "90_Archive"],
    }


def test_pending_copy_is_not_a_conflict(tmp_path: Path) -> None:
    """正式区一份 + staging 一份 = 同一对象在途，不算撞号。

    这是 T07 的真实现场：第一批发布后，正式区与 staging 各有一份 ch_000001，
    旧实现把它判成重号，第二批连号都发不出来。
    """
    root = tmp_path / "v"
    _write(root / "04_正文" / "ch_000001.md", _card("ch_000001"))
    _write(root / "_staging" / "TASK-1" / "ch_000001.md", _card("ch_000001"))

    registry = IdRegistry(root, areas=_areas(root))
    assert registry.collisions(), "物理上确实是两处，得看得见"
    assert registry.conflicts() == {}, "但正式区只有一份，不是撞号"
    assert set(registry.pending_copies()) == {"ch_000001"}
    assert [r.area for r in registry.records if r.id == "ch_000001"] == ["formal", "pending"]


def test_two_formal_copies_are_a_conflict(tmp_path: Path) -> None:
    root = tmp_path / "v"
    _write(root / "04_正文" / "ch_000001.md", _card("ch_000001"))
    _write(root / "04_正文" / "ch_000001_copy.md", _card("ch_000001"))
    registry = IdRegistry(root, areas=_areas(root))
    assert set(registry.conflicts()) == {"ch_000001"}


def test_out_of_area_copy_is_tainted_not_conflict(tmp_path: Path) -> None:
    """区划外那份（Vault 根的旧归档、`99_Archive/legacy_v1` 之类）不算撞号。

    算成撞号的结果是「整库快照里有过的号，今天一个都发不出来」。
    但它也不能隐身：进 tainted，报告里看得见。
    """
    root = tmp_path / "v"
    _write(root / "04_正文" / "ch_000001.md", _card("ch_000001"))
    _write(root / "99_Archive" / "legacy" / "ch_000001.md", _card("ch_000001"))
    registry = IdRegistry(root, areas=_areas(root))
    assert registry.conflicts() == {}
    assert set(registry.tainted()) == {"ch_000001"}
    assert {r.area for r in registry.collisions()["ch_000001"]} == {"formal", "other"}


def test_archive_copy_is_tainted_not_conflict(tmp_path: Path) -> None:
    root = tmp_path / "v"
    _write(root / "04_正文" / "ch_000001.md", _card("ch_000001"))
    _write(root / "90_Archive" / "ch_000001.md", _card("ch_000001"))
    registry = IdRegistry(root, areas=_areas(root))
    assert registry.conflicts() == {}
    assert set(registry.tainted()) == {"ch_000001"}


def test_without_areas_everything_is_formal(tmp_path: Path) -> None:
    """忘了传区划时按「全库皆正式区」处理：宁可从严，不能悄悄放松。"""
    root = tmp_path / "v"
    _write(root / "04_正文" / "ch_000001.md", _card("ch_000001"))
    _write(root / "_staging" / "ch_000001.md", _card("ch_000001"))
    assert set(IdRegistry(root).conflicts()) == {"ch_000001"}


def test_unknown_area_name_rejected(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="未知 ID 区划"):
        IdRegistry(tmp_path, areas={"formal": [], "trash": [tmp_path]})


# ---- 分配 ----------------------------------------------------------------

def test_allocate_continues_sequence(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="test")
    assert alloc.allocate("CHARACTER") == ["ent_000004"]


def test_allocate_count(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="test")
    assert alloc.allocate("ent", count=3) == ["ent_000004", "ent_000005", "ent_000006"]


def test_freed_numbers_are_not_reused(vault: Path, runtime: Path) -> None:
    """发过 0004 之后删掉 0002，下一个必须是 0005。

    只按现存最大号取号会补到 0002——那等于让「ent_000002」这个历史引用
    指向另一个对象。所以取号要同时看审计记录。
    """
    alloc = IdAllocator(vault, runtime, actor="test", lock_timeout=0.2)
    assert alloc.allocate("ent") == ["ent_000004"]
    (vault / "01_小说项目" / "novel-001_p1" / "02_世界知识库" / "人物与角色" / "ent_000002_乙.md").unlink()
    assert alloc.allocate("ent") == ["ent_000005"]


def test_history_survives_file_deletion(vault: Path, runtime: Path) -> None:
    """连最大的那份也删掉，号段仍不回退。"""
    alloc = IdAllocator(vault, runtime, actor="test", lock_timeout=0.2)
    alloc.allocate("ent", count=2)  # 000004, 000005
    canon = vault / "01_小说项目" / "novel-001_p1" / "02_世界知识库" / "人物与角色"
    for name in ("ent_000001_甲.md", "ent_000002_乙.md"):
        (canon / name).unlink()
    assert alloc.allocate("ent") == ["ent_000006"]


def test_allocate_records_audit_line(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="writer", lock_timeout=0.2)
    alloc.allocate("ent", count=2, reason="新建人物")
    lines = (runtime / "id-allocations.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["ids"] == ["ent_000004", "ent_000005"]
    assert row["actor"] == "writer"
    assert row["reason"] == "新建人物"
    assert row["previous_max"] == 3


def test_dry_run_does_not_write(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="test")
    assert alloc.allocate("ent", dry_run=True) == ["ent_000004"]
    assert not (runtime / "id-allocations.jsonl").exists()


def test_history_reads_back(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="test", lock_timeout=0.2)
    alloc.allocate("ent")
    alloc.allocate("chapter")
    assert [h["prefix"] for h in alloc.history()] == ["ent", "ch"]


def test_dirty_same_prefix_is_refused(tmp_path: Path, runtime: Path) -> None:
    root = tmp_path / "v"
    _write(root / "A" / "asset_000001_x.md", _card("asset_000001"))
    _write(root / "B" / "asset_000001_y.md", _card("asset_000001"))
    alloc = IdAllocator(root, runtime, actor="test")
    with pytest.raises(ConflictError, match="asset"):
        alloc.allocate("asset")


def test_dirty_other_prefix_does_not_block(tmp_path: Path, runtime: Path) -> None:
    """asset 撞号不该让 ent 也发不出号——两件事。"""
    root = tmp_path / "v"
    _write(root / "A" / "asset_000001_x.md", _card("asset_000001"))
    _write(root / "B" / "asset_000001_y.md", _card("asset_000001"))
    _write(root / "C" / "ent_000001_甲.md", _card("ent_000001"))
    alloc = IdAllocator(root, runtime, actor="test")
    assert alloc.allocate("ent") == ["ent_000002"]


def test_allow_dirty_flag(vault: Path, runtime: Path, tmp_path: Path) -> None:
    root = tmp_path / "v2"
    _write(root / "A" / "asset_000001_x.md", _card("asset_000001"))
    _write(root / "B" / "asset_000001_y.md", _card("asset_000001"))
    alloc = IdAllocator(root, runtime, actor="test")
    assert alloc.allocate("asset", allow_dirty=True) == ["asset_000002"]


def test_allocate_survives_pending_copy(tmp_path: Path, runtime: Path) -> None:
    """发布过一次之后，staging 里那份副本不该把后续发号卡死（T07 回归）。"""
    root = tmp_path / "v"
    _write(root / "04_正文" / "ch_000001.md", _card("ch_000001"))
    _write(root / "_staging" / "TASK-1" / "ch_000001.md", _card("ch_000001"))
    alloc = IdAllocator(root, runtime, actor="test", areas=_areas(root))
    assert alloc.allocate("chapter", count=2) == ["ch_000002", "ch_000003"]


def test_allocate_still_refuses_two_formal_copies(tmp_path: Path, runtime: Path) -> None:
    """区划不能把「真撞号」也一起放过去。"""
    root = tmp_path / "v"
    _write(root / "04_正文" / "ch_000001.md", _card("ch_000001"))
    _write(root / "04_正文" / "ch_000001_copy.md", _card("ch_000001"))
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
        alloc.allocate("ent")


def test_missing_runtime_refused(vault: Path) -> None:
    alloc = IdAllocator(vault, None, actor="test")
    with pytest.raises(UsageError, match="Runtime"):
        alloc.allocate("ent")


def test_count_must_be_positive(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="test")
    with pytest.raises(UsageError):
        alloc.allocate("ent", count=0)


# ---- 号段上限（ch 的旧稿存档段）--------------------------------------------
#
# 2026-11-22 全库重编号曾把旧稿 28 份压进 ch_000003..030，与新正文同号段。
# 恢复独立段后（ch_001001..001028），`allocate` 若只取「最大号 + 1」，
# 新正文第一章会拿到 1029——直接落进存档段。下面四条钉住这个边界。


def _ch_vault(tmp_path: Path, ids: list[str]) -> Path:
    root = tmp_path / "vault"
    for i, eid in enumerate(ids):
        band = "90_Archive" if int(eid.split("_")[1]) > 999 else "04_正文"
        _write(root / "01_小说项目" / "novel-001_p1" / band / f"{eid}_第{i}.md", _card(eid, f"第{i}"))
    return root / "01_小说项目" / "novel-001_p1"


def test_ch_band_ignores_archive_segment(tmp_path: Path, runtime: Path) -> None:
    """旧稿占 1001+ 时，新正文仍从 000001 起，不能拿到 1002。"""
    root = _ch_vault(tmp_path, ["ch_001001", "ch_001002"])
    alloc = IdAllocator(root, runtime, actor="test", areas=_areas(root))
    assert alloc.allocate("chapter") == ["ch_000001"]


def test_ch_band_counts_only_in_band_numbers(tmp_path: Path, runtime: Path) -> None:
    """段内已有 000007 时下一个是 000008，段外的 001028 不参与取号。"""
    root = _ch_vault(tmp_path, ["ch_000007", "ch_001028"])
    alloc = IdAllocator(root, runtime, actor="test", areas=_areas(root))
    assert alloc.allocate("chapter") == ["ch_000008"]


def test_ch_band_exhausted_refuses(tmp_path: Path, runtime: Path) -> None:
    """段满时拒绝发号。悄悄越过会把新正文写进存档段，那是不归路。"""
    root = _ch_vault(tmp_path, ["ch_000999"])
    alloc = IdAllocator(root, runtime, actor="test", areas=_areas(root))
    with pytest.raises(UsageError, match="活文档号段已用尽"):
        alloc.allocate("chapter")


def test_band_limit_does_not_affect_other_prefixes(tmp_path: Path, runtime: Path) -> None:
    """ent 没有号段上限，1000 以上照发——上限是按前缀配的，不是全局。"""
    root = _ch_vault(tmp_path, ["ch_000001"])
    _write(root / "04_正文" / "ent_001204_甲.md", _card("ent_001204", "甲"))
    alloc = IdAllocator(root, runtime, actor="test", areas=_areas(root))
    assert alloc.allocate("ent") == ["ent_001205"]


def test_max_number_upper_filters_out_band(tmp_path: Path) -> None:
    """`upper` 只影响统计口径，不改登记表。"""
    root = _ch_vault(tmp_path, ["ch_000005", "ch_001028"])
    reg = IdRegistry(root, areas=_areas(root))
    assert reg.max_number("ch") == 1028
    assert reg.max_number("ch", upper=999) == 5
