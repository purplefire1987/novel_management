"""ID 发号命令（PRD §8.1 / §20.2 `novel allocate-id --type CHARACTER`）。

默认只预演。理由：发号会写审计行，而「这次到底发没发」必须一眼能看出；
预演与落盘用同一套取号逻辑，预演看到的号就是落盘拿到的号（除非这期间
有别人也发了号——那时锁会拦住）。
"""

from __future__ import annotations

import argparse

from .. import config as cfg_mod
from ..errors import ConflictError
from ..ids import IdAllocator, IdRegistry, resolve_prefix
from ._common import fail, heading, info, ok, warn


def cmd_allocate_id(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    vault = cfg.require_vault()
    runtime = cfg.require_runtime()

    prefix = resolve_prefix(args.type)
    alloc = IdAllocator(vault, runtime, actor=getattr(args, "actor", None) or "author-local")

    registry = IdRegistry(vault)
    current_max = registry.max_number(prefix)
    stray = registry.stray_compound(prefix)

    heading(f"分配 {prefix}（当前最大号 {current_max:04d}）")
    if stray:
        warn(f"另有 {len(stray)} 个复合型 ID 以 {prefix}- 开头，不计入号段：")
        for raw, source in stray[:5]:
            info(f"{raw} ← {source}")
        info("PRD §8.1 禁止复合型；迁移前它们不参与取号")

    others = [k for k in registry.collisions() if not k.startswith(prefix + "-")]
    if others:
        warn(f"库内另有 {len(others)} 个其它前缀的重复 ID（与本前缀无关，validate 会报）")

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
    info(f"下一个号将是 {prefix}-{_next_of(issued):04d}")

    if args.json:
        import json

        print(json.dumps({"prefix": prefix, "ids": issued, "committed": bool(args.confirm)}, ensure_ascii=False))
    return 0


def _next_of(issued: list[str]) -> int:
    from ..ids import split_id

    last = split_id(issued[-1]) if issued else None
    return (last[1] + 1) if last else 1
