"""稳定 ID 分配（V1.3 §27.2 形态 `{TYPE}-{ULID}`）。

钉住四件事：

1. **形态唯一**——只有当前形态算 ID，前两代只由 `legacy_form()` 识别并报告。
2. **不复用、不重复**——同样前缀两次发号不会给出同一个值。
3. **带病不发号**——正式区已有同前缀重号时拒绝，别的前缀不受牵连。
4. **每次发号留审计**，且锁被占时不硬闯。

## 这个文件跟着形态改过三次，判据每次都是「反过来」而不是「放宽」

- V4.0：`CHAR-0001`，一类一前缀，四→六位序号。
- V1.0（2026-11-22 裁定重排）：`ent_000001`，单前缀收实体、六位序号。
  当时最有价值的一组测试是「号段上限」（`ch` 999 挡住旧稿的 1001+），
  以及「删档后不回填空号」——两者都是序号隐含的坑。
- V1.3（2026-11-23）：`ENT-<ULID>`。**上面两组测试不是被删掉，而是失去了
  成立条件**：ULID 没有号段，删档也不可能让下一次生成的随机段撞上旧号。
  对应的守卫改成了 `test_no_band_limit_and_no_sequence_reuse`，用意不变——
  防止有人把序号那套悄悄装回来。

## 测试里的 ID 用 `compose()` 造

`compose(TS, n)` 是迁移工具专用的拼法（固定时间戳 + 负载放旧序号），
用它而不是随机 `new_ulid()`，是为了让断言能写死具体值。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_management.domain import ids as ids_mod
from novel_management.domain import ulid as ulid_mod
from novel_management.domain.errors import ConflictError, UsageError
from novel_management.domain.ids import (
    IdAllocator,
    IdRegistry,
    id_from_filename,
    is_compound_id,
    legacy_form,
    resolve_prefix,
    split_id,
)

TS = 1795478400000  # 与迁移工具同一常量，便于人肉对照


def uid(prefix: str, n: int) -> str:
    """造一个确定的 ID。`uid("ENT", 1)` == `ENT-01M85DMB000000000000000001`。"""
    return f"{prefix}-{ulid_mod.compose(TS, n)}"


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
    # 三份实体同一个 ENT 前缀：人物两人、地点一人。类型归 frontmatter 的 type。
    _write(canon / "人物与角色" / f"{uid('ENT', 1)}_甲.md", _card(uid("ENT", 1), "甲"))
    _write(canon / "人物与角色" / f"{uid('ENT', 2)}_乙.md", _card(uid("ENT", 2), "乙"))
    _write(canon / "地点与地理" / f"{uid('ENT', 3)}_丙.md", _card(uid("ENT", 3), "丙"))
    _write(root / "00_作者工作台" / "Templates" / "素材卡模板.md", _card("{{id}}", "模板"))
    # 复合型：看着像 ID 但属于 V4.0，不应计入 EVT 统计。
    _write(canon / "事件" / "EVT-CH-0001-001_丁.md", _card("EVT-CH-0001-001", "丁"))
    return root


@pytest.fixture()
def runtime(tmp_path: Path) -> Path:
    return tmp_path / "runtime"


# ---- 形态解析 ------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (uid("ENT", 1), ("ENT", ulid_mod.compose(TS, 1))),
        (uid("CH", 1001), ("CH", ulid_mod.compose(TS, 1001))),
        ("ENT-01M85DMB000000000000000001 ", ("ENT", "01M85DMB000000000000000001")),  # 容忍空白
        ("ent_000001", None),                       # V1.0 不是当前形态
        ("CHAR-0001", None),                        # V4.0 不是当前形态
        ("ent-01M85DMB000000000000000001", None),   # 混形：小写前缀 + ULID
        ("ENT-01M85DMB", None),                     # ULID 截断
        ("ENT-01M85DMB00000000000000000I", None),   # I 不在 Crockford 字母表
        ("ENT-01M85DMB0000000000000000OU", None),   # O / U 同样不在
        ("", None),
        ("随机名", None),
    ],
)
def test_split_id(raw: str, expected) -> None:
    assert split_id(raw) == expected


def test_legacy_form_tells_which_generation() -> None:
    """两代旧号要能分开——处置方式完全不同。

    `v1_seq`（上一轮迁移漏的）是 bug，该修；`v4_seq`（更早的历史）是要不要
    动的取舍。混成一个 `True`，报告里就分不出「还剩 8 份要迁」和「148 份按约定保留」。
    """
    assert legacy_form("ent_000001") == "v1_seq"
    assert legacy_form("ch_001001") == "v1_seq"
    assert legacy_form("CHAR-0001") == "v4_seq"
    assert legacy_form("VOL-0001-S1") == "v4_seq"
    assert legacy_form(uid("ENT", 1)) is None
    assert legacy_form("随便") is None
    assert legacy_form("") is None


def test_legacy_form_ignores_datestamp_filenames() -> None:
    """日期戳文件名与 V4.0 旧号形状相同，但不是 ID。

    库里 `09_版本与变更记录` 有 `LORE-20261103-223926` 这类日志。不校验前缀的话，
    它们会被报成「6 份旧 ID 未迁移」——作者要挨个打开确认「这个不用改」。
    **报假不符比漏报更消耗人**：前者花的是逐条复核的时间，后者只要一次全量扫描。
    """
    for name in (
        "ASSETS-20261103-224145",
        "INGEST-20261104-144536",
        "LORE-20261103-223926",
        "OUTLINE-20261104-0044",
        "RACE-20261108",
    ):
        assert legacy_form(name) is None, name


def test_compound_detection() -> None:
    """复合号识别的是 V4.0 旧形态，与「不是 ID」是两回事。

    后者会让人在查重时完全隐身，前者至少能被报告出来。
    """
    assert is_compound_id("EVT-CH-0001-001")
    assert is_compound_id("PLAN-VOL-000001")
    assert is_compound_id("VOL-0001-S1")
    assert not is_compound_id("CHAR-0001"), "只隔一段的是旧单号，不是复合号"
    assert not is_compound_id(uid("ENT", 1)), "当前形态当然不是复合号"
    assert not is_compound_id("随便")


@pytest.mark.parametrize(
    "stem,expected",
    [
        # 三代文件名都要认。这条逻辑只能有一份实现：曾经抄在 `IdRegistry` 与
        # `doctor` 两处，改形态时漏了一处，两个命令对同一批文件给出相反结论。
        (f"{uid('ENT', 1)}_怒涛府", uid("ENT", 1)),
        ("ent_000339_浑天-地动仪", "ent_000339"),   # V1.0：名字里带连字符，不能按 - 切
        ("VOL-0001-S1-DETAIL_细纲", "VOL-0001-S1-DETAIL"),
        ("第一卷_细纲", None),                       # 不以 ID 开头，绝不从文件名猜身份
        ("", None),
    ],
)
def test_id_from_filename_covers_all_generations(stem: str, expected) -> None:
    assert id_from_filename(stem) == expected


# ---- 前缀解析 ------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,prefix",
    [
        # 世界知识实体一律 ENT：类型信息归 frontmatter 的 type 字段。
        # V1.3 没有把 CHAR/LOC 恢复成独立前缀，§27.2.1 的 18 个前缀里只有一个 ENT。
        ("CHARACTER", "ENT"),
        ("character", "ENT"),
        ("char", "ENT"),
        ("LOCATION", "ENT"),
        ("item", "ENT"),
        ("CREATURE", "ENT"),
        ("entity", "ENT"),
        # 其余按 V1.3 前缀表。
        ("CHAPTER", "CH"),
        ("chapter", "CH"),
        ("CHANGE_REQUEST", "PRP"),
        ("FORESHADOWING", "FSP"),
        ("ASSET", "AST"),
        ("VOLUME", "VOL"),
        ("SCENE", "SCN"),
        ("STATE_CHANGE", "STC"),
        # 直接给前缀也认，大小写都行。
        ("ent", "ENT"),
        ("CH", "CH"),
        ("tsk", "TSK"),
    ],
)
def test_resolve_prefix(kind: str, prefix: str) -> None:
    assert resolve_prefix(kind) == prefix


def test_resolve_prefix_always_returns_uppercase() -> None:
    """返回小写会让某个分支发出 `ent-01J8…` 这种混形 ID。

    ID 里就是大写，让调用方自己 `.upper()` 一遍，迟早有一个分支忘掉。
    """
    for kind in ("ent", "Ent", "ENTITY", "chapter", "asset"):
        assert resolve_prefix(kind).isupper(), kind


def test_legacy_prefix_is_refused_with_hint() -> None:
    """只存在于旧形态的前缀必须报错而不是静默映射——否则同一库里会长出两种形态。

    只有**在 `TYPE_TO_PREFIX` 里找不到**的旧前缀才走这条分支。`CHAR` / `LOC`
    之外的 `SCENE` / `FORESHADOW` 都还是活着的类型名（映射到 SCN / FSP），
    它们先命中类型表，不算废弃——这条边界以前被弄混过。
    """
    for legacy in ("FAC", "LOC", "PLAN-VOL", "PLAN-CH"):
        with pytest.raises(UsageError, match="旧形态"):
            resolve_prefix(legacy)


def test_scene_and_foreshadow_are_still_live_type_names() -> None:
    """这两个词看着像 V4.0 前缀，实际是活着的类型名。"""
    assert resolve_prefix("SCENE") == "SCN"
    assert resolve_prefix("FORESHADOW") == "FSP"


def test_type_char_returns_ent_not_error() -> None:
    """`CHAR` 是活着的类型名（映射到 ENT），不是废弃前缀。

    这条曾经被误判过：审计报告以为 `--type CHAR` 会走废弃分支，实际
    `TYPE_TO_PREFIX` 先命中。走废弃分支的是 `FAC` / `LOC` / `PLAN-VOL`。
    """
    assert resolve_prefix("CHAR") == "ENT"


def test_unknown_prefix_raises() -> None:
    with pytest.raises(UsageError):
        resolve_prefix("DRAGON")
    with pytest.raises(UsageError):
        resolve_prefix("")


# ---- 扫描 ----------------------------------------------------------------


def test_registry_finds_ids_and_skips_placeholder(vault: Path) -> None:
    registry = IdRegistry(vault)
    got = {r.id for r in registry.records}
    assert {uid("ENT", 1), uid("ENT", 2), uid("ENT", 3)} <= got
    assert not any("{{" in i for i in got), "模板占位符不是身份"


def test_registry_ignores_compound_for_count(vault: Path) -> None:
    registry = IdRegistry(vault)
    assert registry.count("EVT") == 0, "复合型不该被算成一个 EVT 对象"
    assert [c[0] for c in registry.compounds] == ["EVT-CH-0001-001"]


def test_registry_reports_legacy_with_generation(vault: Path) -> None:
    registry = IdRegistry(vault)
    assert [(raw, gen) for raw, _src, gen in registry.legacy] == [
        ("EVT-CH-0001-001", "v4_seq")
    ]


def test_count_and_latest(vault: Path, ) -> None:
    registry = IdRegistry(vault)
    assert registry.count("ENT") == 3, "人物与地点共用 ENT 前缀"
    assert registry.count("CH") == 0
    assert registry.latest("ENT") == uid("ENT", 3), "latest 按 ULID 时间序取最大值"


def test_latest_does_not_confuse_prefixes(tmp_path: Path) -> None:
    """`CH` 与 `CHANGEREQ` 之类的前缀不能互相串。"""
    root = tmp_path / "v"
    _write(root / f"{uid('CH', 9)}_a.md", _card(uid("CH", 9)))
    _write(root / f"{uid('ENT', 1)}_b.md", _card(uid("ENT", 1)))
    registry = IdRegistry(root)
    assert registry.latest("ENT") == uid("ENT", 1)
    assert registry.latest("CH") == uid("CH", 9)


def test_collisions_detected(tmp_path: Path) -> None:
    root = tmp_path / "v"
    eid = uid("AST", 1)
    _write(root / "A" / f"{eid}_x.md", _card(eid))
    _write(root / "B" / f"{eid}_y.md", _card(eid))
    collisions = IdRegistry(root).collisions()
    assert eid in collisions
    assert len({r.source for r in collisions[eid]}) == 2


def test_frontmatter_wins_over_filename(tmp_path: Path) -> None:
    """文件名写 ENT-1、frontmatter 写 ENT-9：身份是 9。

    早期实现两个号都占，结果五份 `VOL-0001_*.md` 被判成同一个 ID——
    假重复比漏报更能误导人。
    """
    root = tmp_path / "v"
    _write(root / f"{uid('ENT', 1)}_甲.md", _card(uid("ENT", 9)))
    registry = IdRegistry(root)
    assert {r.id for r in registry.records} == {uid("ENT", 9)}
    assert registry.mismatched == [
        (f"{uid('ENT', 1)}_甲.md", uid("ENT", 9), uid("ENT", 1))
    ]


def test_filename_used_only_without_frontmatter_id(tmp_path: Path) -> None:
    root = tmp_path / "v"
    _write(root / f"{uid('ENT', 1)}_无 id.md", "---\nname: 无 id\n---\n正文\n")
    assert {r.id for r in IdRegistry(root).records} == {uid("ENT", 1)}


def test_same_prefixed_filenames_are_not_collisions(tmp_path: Path) -> None:
    """文件名不以 ID 开头时，绝不从文件名猜身份。

    假重复的原始现场：一个卷的三份文档文件名同前缀，旧实现拿文件名兜底，
    三份被判成同一个 id。frontmatter 才是权威。
    """
    root = tmp_path / "v"
    for name, eid in (
        ("第一卷_细纲.md", uid("PLAN", 1)),
        ("第一卷_章节表.md", uid("PLAN", 2)),
        ("第一卷_设计.md", uid("PLAN", 3)),
    ):
        _write(root / name, _card(eid))
    registry = IdRegistry(root)
    assert registry.collisions() == {}
    assert {r.id for r in registry.records} == {
        uid("PLAN", 1), uid("PLAN", 2), uid("PLAN", 3)
    }


# ---- 分级查重（T07 发现：发布过一次就发不出号） ----------------------------


def _areas(root: Path) -> dict[str, list[Path]]:
    return {
        "formal": [root / "04_正文"],
        "pending": [root / "_staging"],
        "archive": [root / "90_Archive"],
    }


def test_pending_copy_is_not_a_conflict(tmp_path: Path) -> None:
    """正式区一份 + staging 一份 = 同一对象在途，不算撞号。

    这是 T07 的真实现场：第一批发布后，正式区与 staging 各有一份同一章，
    旧实现把它判成重号，第二批连号都发不出来。
    """
    root = tmp_path / "v"
    eid = uid("CH", 1)
    _write(root / "04_正文" / f"{eid}.md", _card(eid))
    _write(root / "_staging" / "TASK-1" / f"{eid}.md", _card(eid))

    registry = IdRegistry(root, areas=_areas(root))
    assert registry.collisions(), "物理上确实是两处，得看得见"
    assert registry.conflicts() == {}, "但正式区只有一份，不是撞号"
    assert set(registry.pending_copies()) == {eid}
    assert [r.area for r in registry.records if r.id == eid] == ["formal", "pending"]


def test_two_formal_copies_are_a_conflict(tmp_path: Path) -> None:
    root = tmp_path / "v"
    eid = uid("CH", 1)
    _write(root / "04_正文" / f"{eid}.md", _card(eid))
    _write(root / "04_正文" / f"{eid}_copy.md", _card(eid))
    registry = IdRegistry(root, areas=_areas(root))
    assert set(registry.conflicts()) == {eid}


def test_out_of_area_copy_is_tainted_not_conflict(tmp_path: Path) -> None:
    """区划外那份（Vault 根的旧归档、`99_Archive/legacy_v1` 之类）不算撞号。

    算成撞号的结果是「整库快照里有过的号，今天一个都发不出来」。
    但它也不能隐身：进 tainted，报告里看得见。
    """
    root = tmp_path / "v"
    eid = uid("CH", 1)
    _write(root / "04_正文" / f"{eid}.md", _card(eid))
    _write(root / "99_Archive" / "legacy" / f"{eid}.md", _card(eid))
    registry = IdRegistry(root, areas=_areas(root))
    assert registry.conflicts() == {}
    assert set(registry.tainted()) == {eid}
    assert {r.area for r in registry.collisions()[eid]} == {"formal", "other"}


def test_archive_copy_is_tainted_not_conflict(tmp_path: Path) -> None:
    root = tmp_path / "v"
    eid = uid("CH", 1)
    _write(root / "04_正文" / f"{eid}.md", _card(eid))
    _write(root / "90_Archive" / f"{eid}.md", _card(eid))
    registry = IdRegistry(root, areas=_areas(root))
    assert registry.conflicts() == {}
    assert set(registry.tainted()) == {eid}


def test_without_areas_everything_is_formal(tmp_path: Path) -> None:
    """忘了传区划时按「全库皆正式区」处理：宁可从严，不能悄悄放松。"""
    root = tmp_path / "v"
    eid = uid("CH", 1)
    _write(root / "04_正文" / f"{eid}.md", _card(eid))
    _write(root / "_staging" / f"{eid}.md", _card(eid))
    assert set(IdRegistry(root).conflicts()) == {eid}


def test_unknown_area_name_rejected(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="未知 ID 区划"):
        IdRegistry(tmp_path, areas={"formal": [], "trash": [tmp_path]})


# ---- 分配 ----------------------------------------------------------------


def test_allocate_returns_current_form(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="test")
    got = alloc.allocate("CHARACTER")
    assert len(got) == 1
    assert got[0].startswith("ENT-")
    assert split_id(got[0]) is not None, "发出来的必须能过 split_id，否则它是个孤儿"


def test_allocate_count(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="test")
    got = alloc.allocate("ENT", count=3)
    assert len(got) == 3
    assert len(set(got)) == 3, "一批里不许重复"
    assert all(split_id(i) for i in got)
    assert all(i.startswith("ENT-") for i in got)


def test_two_allocations_never_repeat(vault: Path, runtime: Path) -> None:
    """同一前缀连发两次，第二次不能给出第一次的值。

    这是旧形态「删档后回填空号」那条测试在 V1.3 的对应物：序号会复用，
    ULID 不会——但**「不会」是要被测的**，不能靠「随机数应该足够大」的直觉。

    原来这条还会「删掉其中一份再发号」，靠删文件模拟删档。现在去掉了：
    ULID 的复用与否跟库里有没有那份文件无关（不读最大号），所以删不删都
    测不出新东西；而删文件会让这条测试在禁止删除的环境里直接报 PermissionError，
    那时看到的是「测试坏了」而不是「行为变了」——两种错误长得一样最费时间。
    """
    alloc = IdAllocator(vault, runtime, actor="test", lock_timeout=0.5)
    first = alloc.allocate("ENT", count=2)
    second = alloc.allocate("ENT", count=2)
    assert not set(first) & set(second)
    assert len(set(first) | set(second)) == 4


def test_allocate_records_audit_line(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="writer", lock_timeout=0.5)
    issued = alloc.allocate("ENT", count=2, reason="新建人物")
    lines = (runtime / "id-allocations.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["ids"] == issued
    assert row["prefix"] == "ENT"
    assert row["actor"] == "writer"
    assert row["reason"] == "新建人物"
    assert "previous_max" not in row, "V1.3 没有「上一个号」，别再往审计里写它"


def test_dry_run_does_not_write(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="test")
    assert len(alloc.allocate("ENT", dry_run=True)) == 1
    assert not (runtime / "id-allocations.jsonl").exists()


def test_history_reads_back(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="test", lock_timeout=0.5)
    alloc.allocate("ENT")
    alloc.allocate("CHAPTER")
    assert [h["prefix"] for h in alloc.history()] == ["ENT", "CH"]


def test_dirty_same_prefix_is_refused(tmp_path: Path, runtime: Path) -> None:
    root = tmp_path / "v"
    eid = uid("AST", 1)
    _write(root / "A" / f"{eid}_x.md", _card(eid))
    _write(root / "B" / f"{eid}_y.md", _card(eid))
    alloc = IdAllocator(root, runtime, actor="test")
    with pytest.raises(ConflictError, match="AST"):
        alloc.allocate("ASSET")


def test_dirty_other_prefix_does_not_block(tmp_path: Path, runtime: Path) -> None:
    """AST 撞号不该让 ENT 也发不出号——两件事。"""
    root = tmp_path / "v"
    bad = uid("AST", 1)
    _write(root / "A" / f"{bad}_x.md", _card(bad))
    _write(root / "B" / f"{bad}_y.md", _card(bad))
    _write(root / "C" / f"{uid('ENT', 1)}_甲.md", _card(uid("ENT", 1)))
    alloc = IdAllocator(root, runtime, actor="test")
    assert alloc.allocate("ENT")[0].startswith("ENT-")


def test_allow_dirty_flag(tmp_path: Path, runtime: Path) -> None:
    root = tmp_path / "v2"
    bad = uid("AST", 1)
    _write(root / "A" / f"{bad}_x.md", _card(bad))
    _write(root / "B" / f"{bad}_y.md", _card(bad))
    alloc = IdAllocator(root, runtime, actor="test")
    assert alloc.allocate("ASSET", allow_dirty=True)[0].startswith("AST-")


def test_allocate_survives_pending_copy(tmp_path: Path, runtime: Path) -> None:
    """发布过一次之后，staging 里那份副本不该把后续发号卡死（T07 回归）。"""
    root = tmp_path / "v"
    eid = uid("CH", 1)
    _write(root / "04_正文" / f"{eid}.md", _card(eid))
    _write(root / "_staging" / "TASK-1" / f"{eid}.md", _card(eid))
    alloc = IdAllocator(root, runtime, actor="test", areas=_areas(root))
    got = alloc.allocate("chapter", count=2)
    assert len(set(got)) == 2
    assert eid not in got


def test_allocate_still_refuses_two_formal_copies(tmp_path: Path, runtime: Path) -> None:
    """区划不能把「真撞号」也一起放过去。"""
    root = tmp_path / "v"
    eid = uid("CH", 1)
    _write(root / "04_正文" / f"{eid}.md", _card(eid))
    _write(root / "04_正文" / f"{eid}_copy.md", _card(eid))
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
        alloc.allocate("ENT")


def test_missing_runtime_refused(vault: Path) -> None:
    alloc = IdAllocator(vault, None, actor="test")
    with pytest.raises(UsageError, match="Runtime"):
        alloc.allocate("ENT")


def test_count_must_be_positive(vault: Path, runtime: Path) -> None:
    alloc = IdAllocator(vault, runtime, actor="test")
    with pytest.raises(UsageError):
        alloc.allocate("ENT", count=0)


# ---- 序号机制已作废（守卫，不是功能测试） ---------------------------------


def test_no_band_limit_and_no_sequence_reuse(vault: Path, runtime: Path) -> None:
    """序号那套东西不能在换形后被悄悄装回来。

    旧实现有三件**因为用序号才存在**的东西：`BAND_LIMITS`（`ch` 999 挡住
    旧稿的 1001+）、`max_number`（读全库最大号再 +1）、`_historical_max`
    （删档后不许回填空号）。ULID 换形后三者都没有成立条件，于是被移除。

    这条测试守的是「移除」这个决定本身。它看起来像在测「某属性不存在」——
    确实如此，因为**删掉一个机制和删掉一行业务逻辑不同**：前者会在下一次
    「让章节号可读一点」的改动里悄悄复活，且复活后测试全绿。
    """
    assert not hasattr(ids_mod, "BAND_LIMITS"), "号段上限随 ULID 作废，别再装回来"
    assert not hasattr(IdRegistry, "max_number"), "ULID 没有「最大号」，只有 count／latest"
    assert not hasattr(IdAllocator, "_historical_max"), "ULID 不会被复用，不需要历史最大号"

    # 更要紧的一条：库里已有 1000+ 个同前缀对象时，发号照样能发——
    # 序号体系下这里会撞号段上限。
    for n in (1001, 1002, 1028):
        eid = uid("CH", n)
        _write(vault / "01_小说项目" / "novel-001_p1" / "90_Archive" / f"{eid}.md", _card(eid))
    alloc = IdAllocator(vault, runtime, actor="test", lock_timeout=0.5)
    got = alloc.allocate("chapter")
    assert split_id(got[0]) is not None and got[0].startswith("CH-")


def test_migration_compose_preserves_order() -> None:
    """迁移拼法必须让字典序等于旧序号序。

    章节顺序是这个库的硬约束。如果迁移时负载纯随机，`ch_000001` 与
    `ch_000002` 换形后可能倒序——「按 ID 排序」就不再是「按章序」，
    而这种破坏不会报错，只会让某次导出悄悄错位。
    """
    ordered = [ulid_mod.compose(TS, n) for n in range(1, 12)]
    assert ordered == sorted(ordered)
    assert len(set(ordered)) == len(ordered)


def test_payload_roundtrip() -> None:
    """能从 ID 反查回旧序号——迁移出错时要靠它核对。"""
    for n in (0, 1, 1028, 99_999):
        assert ulid_mod.payload_of(ulid_mod.compose(TS, n)) == n


def test_ulid_rejects_alphabet_outside_crockford() -> None:
    """Crockford 去掉了 I L O U（防手抄混淆 + 防拼出脏词）。

    校验时直接按 A-Z 放行，会把 `…00I` 这种抄错一位的号当成合法 ID，
    而它永远查不到对象——比报错难查得多。
    """
    for ch in "ILOU":
        assert not ulid_mod.is_ulid("01M85DMB00000000000000000" + ch), ch
    for ch in "0123456789ABCDEFGHJKMNPQRSTVWXYZ":
        assert ulid_mod.is_ulid("01M85DMB00000000000000000" + ch), ch
