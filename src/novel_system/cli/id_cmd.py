"""ID 发号命令（PRD §8.1 / §20.2 `novel allocate-id --type CHARACTER`）。

默认只预演。理由：发号会写审计行，而「这次到底发没发」必须一眼能看出；
预演与落盘用同一套取号逻辑，预演看到的号就是落盘拿到的号（除非这期间
有别人也发了号——那时锁会拦住）。
"""

from __future__ import annotations

import argparse

from .. import config as cfg_mod
from ..errors import ConflictError, SecurityError, UsageError
from ..ids import BAND_LIMITS, MIN_WIDTH, IdAllocator, IdRegistry, resolve_prefix
from ..paths import ProjectPaths
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
    # 有号段上限时，"当前最大号"必须报段内的号。报段外最大号（CH 的 1028
    # 来自旧稿存档）会让人以为下一个要发 1029，与实际的 000003 对不上。
    limit = BAND_LIMITS.get(prefix)
    current_max = registry.max_number(prefix, upper=limit)
    stray = registry.stray_compound(prefix)

    band_note = f"，活文档段 1..{limit}" if limit is not None else ""
    heading(f"分配 {prefix}（段内最大号 {current_max:0{MIN_WIDTH}d}{band_note}）")
    if scope_note:
        info(scope_note)
    if stray:
        warn(f"另有 {len(stray)} 个复合型 ID 以 {prefix}- 开头，不计入号段：")
        for raw, source in stray[:5]:
            info(f"{raw} ← {source}")
        info("PRD §8.1 禁止复合型；迁移前它们不参与取号")

    others = [k for k in registry.conflicts() if not k.startswith(prefix + "-")]
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
        ok(f"预演：将分配 {'、'.join(issued)}（加 --confirm 落盘）")
    info(f"下一个号将是 {prefix}-{_next_of(issued):0{MIN_WIDTH}d}")

    if args.json:
        import json

        print(json.dumps({"prefix": prefix, "ids": issued, "committed": bool(args.confirm)}, ensure_ascii=False))
    return 0


def _next_of(issued: list[str]) -> int:
    from ..ids import split_id

    last = split_id(issued[-1]) if issued else None
    return (last[1] + 1) if last else 1
