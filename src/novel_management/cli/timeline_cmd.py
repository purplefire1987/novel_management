"""时间线与认知查询命令：state / knowledge / reveal。

三条命令对应 PRD §20.2 的 `novel state` 与 `novel knowledge`，另加 `reveal`
（PRD 没有这条命令，但 T04 的交付项「读者揭示」得有个查询入口，否则只是存着）。

**退出码只有两种含义**：`0` 表示拿到确定答案（`RESOLVED`），`1` 表示没拿到。
没拿到分五种情况，靠 `--json` 的 `status` 字段区分——把它们编码进不同退出码
会让脚本必须记住一张表，而那张表很容易记错。索引不存在返回 `2`（沿用 UsageError）。
"""

from __future__ import annotations

import argparse
import json

from .. import config as cfg_mod
from ..indexing import connect, index_metadata
from ..indexing.locate import active_db
from ..timeline import (
    MODE_AFTER,
    MODE_BEFORE,
    knowledge_answer,
    revelation_answer,
    state_answer,
)
from ._common import heading, info, ok, warn


def _open(cfg: cfg_mod.Config):
    runtime = cfg.require_runtime()
    db_path = active_db(runtime)
    conn = connect(db_path)
    return conn, db_path


def _project(args: argparse.Namespace) -> str:
    return getattr(args, "project", None) or ""


def _anchor(args: argparse.Namespace) -> tuple[str, str]:
    if getattr(args, "after", None):
        return args.after, MODE_AFTER
    if getattr(args, "before", None):
        return args.before, MODE_BEFORE
    return "", MODE_AFTER


def _emit(answer, args: argparse.Namespace, *, subject_label: str) -> int:
    """统一输出。非 RESOLVED 一律返回 1，具体状态在 status 字段。"""
    if getattr(args, "json", False):
        payload = {
            "status": answer.status,
            "subject": answer.subject,
            "predicate": answer.predicate,
            "value": answer.value,
            "note": answer.note,
            "event": answer.extra.get("event", ""),
            "path": answer.extra.get("path", ""),
            "record": answer.extra.get("record", ""),
            "fields": answer.extra.get("fields", []),
            "records": [
                {
                    "id": r.id,
                    "kind": r.kind,
                    "value": r.value,
                    "event": r.event,
                    "path": r.path,
                    "review_status": r.review_status,
                }
                for r in answer.records
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0 if answer.ok else 1

    heading(f"{subject_label}")
    if answer.predicate:
        info(f"字段：{answer.predicate}")
    fields = answer.extra.get("fields") or []
    if answer.ok:
        ok(f"{answer.status}：{answer.value}")
        if len(fields) > 1:
            for item in fields:
                where = f"  @{item['event']}" if item["event"] else "  （无生效事件，视为恒成立）"
                print(f"     {item['predicate']} = {item['value']}{where}")
        if answer.extra.get("event"):
            info(f"依据事件：{answer.extra['event']}（{answer.extra.get('record', '?')}）")
        if answer.extra.get("path"):
            info(f"记录位置：{answer.extra['path']}")
    else:
        warn(f"{answer.status}")
        if len(fields) > 1:
            for item in fields:
                print(f"     {item['predicate']}: {item['status']} {item['value']}".rstrip())
    if answer.note:
        info(answer.note)

    if answer.records and not answer.ok:
        info(f"候选记录 {len(answer.records)} 条：")
        for record in answer.records[:8]:
            where = f"{record.id}  {record.value}"
            if record.event:
                where += f"  @{record.event}"
            print(f"     {where}")
            if record.path:
                print(f"       {record.path}")
    return 0 if answer.ok else 1


def cmd_state(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    """查实体在某时点的客观字段值。**不读人物认知表。**"""
    conn, _db = _open(cfg)
    anchor, mode = _anchor(args)
    try:
        answer = state_answer(
            conn,
            entity_id=args.entity,
            attribute=getattr(args, "field", "") or "",
            anchor=anchor,
            mode=mode,
            project=_project(args),
        )
        meta = index_metadata(conn) if getattr(args, "show_index", False) else {}
    finally:
        conn.close()
    label = f"状态 {args.entity}"
    if anchor:
        label += f"（{'之后' if mode == MODE_AFTER else '之前'} {anchor}）"
    code = _emit(answer, args, subject_label=label)
    if meta and not getattr(args, "json", False):
        info(f"索引依据 {meta.get('canon_revision', '?')[:12]}，build {meta.get('build_id', '?')}")
    return code


def cmd_knowledge(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    """查角色对命题的态度。查不到不等于「不知道」（PRD §9.5）。"""
    conn, _db = _open(cfg)
    anchor, mode = _anchor(args)
    try:
        answer = knowledge_answer(
            conn,
            holder_id=args.holder,
            subject_id=getattr(args, "subject", "") or "",
            predicate=getattr(args, "predicate", "") or "",
            anchor=anchor,
            mode=mode,
            project=_project(args),
        )
    finally:
        conn.close()
    return _emit(answer, args, subject_label=f"认知 {args.holder}")


def cmd_reveal(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    """查某命题在哪些叙述位置被揭示给读者。不推断任何角色是否知道。"""
    conn, _db = _open(cfg)
    try:
        answer = revelation_answer(
            conn,
            proposition_ref=args.proposition,
            project=_project(args),
        )
    finally:
        conn.close()
    return _emit(answer, args, subject_label=f"读者揭示 {args.proposition}")
