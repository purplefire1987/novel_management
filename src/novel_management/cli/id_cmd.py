"""ID 发号命令（V1.3 §27.2 / §20.2 `novel allocate-id --type ENT`）。

默认只预演。理由：发号会写审计行，而「这次到底发没发」必须一眼能看出。

V1.3 起**预演与落盘不再保证同号**：ULID 每次调用都生成新值，预演看到的那串
不会被落盘复用（旧形态「序号从上一个加一」才需要这个保证）。所以预演输出
明确写成「样例」，不写「将分配」。把它写成「将分配」会让人拿着预演值去
建文件，然后文件和审计对不上。
"""

from __future__ import annotations

import argparse

from .. import config as cfg_mod
from ..domain.errors import ConflictError, SecurityError, UsageError
from ..domain.ids import IdAllocator, IdRegistry, resolve_prefix, split_id
from ..infrastructure.filesystem.paths import ProjectPaths
from ._common import fail, heading, info, ok, warn


def cmd_allocate_id(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    vault = cfg.require_vault()
    runtime = cfg.require_runtime()

    # 区划从项目布局取，不写死目录名：正式区 / 待发布区 / 归档区。
    # 没指定项目时**不做「只有一个项目就用它」的推断**（本系统一贯如此），
    # 退回「全库按正式区」的从严口径，并在输出里说明——两种口径数出来的重号数
    # 可以不一样，不说明就会让人以为库里的重号比实际多。
    try:
        pp = ProjectPaths.resolve(cfg, getattr(args, "project", None))
        areas = pp.area_roots()
        scope_note = ""
    except (UsageError, SecurityError, cfg_mod.ConfigError):
        areas = None
        scope_note = "未指定 --project，按「全库皆正式区」的从严口径判重（数可能偏多）"

    prefix = resolve_prefix(args.type)
    alloc = IdAllocator(
        vault, runtime, actor=getattr(args, "actor", None) or "author-local", areas=areas
    )

    registry = IdRegistry(vault, areas=areas)
    used = registry.count(prefix)
    last = registry.latest(prefix)
    stray = registry.stray_compound(prefix)
    legacy_count = len([1 for _id, _src, gen in registry.legacy if gen == "v1_seq"])

    heading(f"分配 {prefix}（已发放 {used} 个{'，最近 ' + last if last else ''}）")
    if scope_note:
        info(scope_note)
    if stray:
        warn(f"另有 {len(stray)} 个复合型旧 ID 以 {prefix}- 开头，不计入统计：")
        for raw, source in stray[:5]:
            info(f"{raw} ← {source}")
        info("V1.3 §27.2 禁止复合型；迁移前它们不参与查重")
    if legacy_count:
        warn(f"库内还有 {legacy_count} 份 V1.0 形态（`ent_000001` 式）未迁移，见 `novel id legacy`")

    others = [k for k in registry.conflicts() if (split_id(k) or ("", ""))[0] != prefix]
    if others:
        warn(f"正式区另有 {len(others)} 个其它前缀的重复 ID（与本前缀无关，validate 会报）")
    in_flight = registry.pending_copies()
    if in_flight:
        info(f"另有 {len(in_flight)} 个 ID 在待发布区有副本（同一对象在途，不算撞号）")

    try:
        issued = alloc.allocate(
            prefix,
            count=args.count,
            reason=args.reason,
            dry_run=not args.confirm,
            allow_dirty=args.allow_dirty,
        )
    except ConflictError as exc:
        fail(str(exc))
        return 1

    if args.confirm:
        ok(f"已分配并记录审计：{'、'.join(issued)}")
    else:
        ok(f"预演样例：{'、'.join(issued)}（加 --confirm 落盘；落盘会生成新的 ULID，不一定是这几串）")

    if args.json:
        import json

        print(json.dumps({"prefix": prefix, "ids": issued, "committed": bool(args.confirm)}, ensure_ascii=False))
    return 0


def cmd_legacy_ids(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    """列出库里还没迁到 V1.3 形态的旧号，按区划分组。**只读**。

    存在的理由：迁移完成后仍有旧号，而「还剩几份、在哪个区」必须能随时量出来。
    只靠 `validate` 不够——它在发布闸门里跑，而人想知道「现在整库什么状态」。

    **按区划分开报**是关键：活跃区还有旧号说明迁移漏了（该修），
    冻结区还有旧号是**故意的**（历史证据，不该动）。合成一个总数，
    就分不清「还差 8 份要迁」和「148 份历史文档按约定保留」——
    前者要动手，后者动手就是破坏证据。
    """
    vault = cfg.require_vault()
    try:
        pp = ProjectPaths.resolve(cfg, getattr(args, "project", None))
        areas = pp.area_roots()
    except (UsageError, SecurityError, cfg_mod.ConfigError):
        areas = None

    registry = IdRegistry(vault, areas=areas)
    rows = registry.legacy
    if not rows:
        ok("库里没有旧形态 ID（全部已是 {TYPE}-{ULID}）")
        return 0

    by_area: dict[str, list[tuple[str, str, str]]] = {}
    for raw, src, gen in rows:
        by_area.setdefault(registry.area_of(vault / src), []).append((raw, src, gen))

    order = {"formal": 0, "pending": 1, "archive": 2, "other": 3}
    total_active = 0
    for area in sorted(by_area, key=lambda a: order.get(a, 9)):
        items = by_area[area]
        gen_count: dict[str, int] = {}
        for _raw, _src, gen in items:
            gen_count[gen] = gen_count.get(gen, 0) + 1
        detail = "、".join(f"{k} {v}" for k, v in sorted(gen_count.items()))
        heading(f"{area}：{len(items)} 份（{detail}）")
        if area in ("formal", "pending"):
            total_active += len(items)
            warn("这些属于活跃区，按裁定应当迁移")
        else:
            info("冻结区／区划外——按裁定保留旧号作为历史证据，不要改")
        for raw, src, _gen in items[: args.limit]:
            info(f"{raw}  ← {src}")
        if len(items) > args.limit:
            info(f"…另有 {len(items) - args.limit} 份，用 --limit 调整")

    if total_active:
        fail(f"活跃区仍有 {total_active} 份旧形态 ID 未迁移")
        return 1
    ok("活跃区已全部为 V1.3 形态；旧号只存在于冻结区")
    return 0
