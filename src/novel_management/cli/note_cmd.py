"""`novel note`：把审阅/审计意见写进 `08_Reviews` 的轻量通道。

这条命令**不**走 `stage` → `approve` → `publish`：`08_Reviews` 不在正式区，
审阅意见也不是作品内容（见 `reviews/__init__.py` 的完整理由）。它保留三样、
去掉两样：保留 schema 校验、原子写、审计留痕；去掉批准链与签字基线。

默认**直写**而不是先预演。理由：这条通道存在的意义就是「轻」——如果它也要
先预演再确认，编辑写一条意见要三步，人就会绕回去手写文件，而手写才是真风险。
保护落在别处：同名即拒（绝不覆盖）、frontmatter 先过 schema、每次写入进 oplog。

`--dry-run` 仍保留，用于先看标题会变成什么文件名。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import config as cfg_mod
from ..errors import ConflictError, UsageError
from ..reviews import KIND_DIRS, KIND_TYPES, Note, list_notes, target_path, write_note
from ._common import fail, heading, info, ok, oplog, project, warn


def _read_body(args: argparse.Namespace) -> str:
    if args.body and args.body_file:
        raise UsageError("`--body` 与 `--body-file` 只能给一个")
    if args.body:
        return args.body
    if args.body_file:
        path = Path(args.body_file)
        if not path.is_file():
            raise UsageError(f"正文文件不存在：{path}")
        return path.read_text(encoding="utf-8")
    if sys.stdin is None or sys.stdin.isatty():
        raise UsageError("没有正文：用 `--body`、`--body-file`，或从管道喂入")
    return sys.stdin.read()


def cmd_note(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    pp = project(cfg, args)

    if args.note_command == "list":
        rows = list_notes(pp, kind=args.kind or None)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if not rows:
            info("08_Reviews 里还没有记录")
            return 0
        heading(f"审阅记录 {len(rows)} 份")
        for row in rows:
            verdict = f"  [{row['verdict']}]" if row["verdict"] else ""
            print(f"  {row['created_at'] or '----------'}  {row['type'] or '?':<14} {row['name']}{verdict}")
            print(f"      {row['path']}")
        return 0

    if args.note_command != "add":
        raise UsageError(f"未知子命令：{args.note_command}")

    kind = args.kind
    if kind not in KIND_DIRS:
        raise UsageError(f"--kind 必须是 {', '.join(KIND_DIRS)} 之一")

    body = _read_body(args)
    if not body.strip():
        raise UsageError("正文是空的。空意见不该占一份记录")

    note = Note(
        kind=kind,
        name=args.title,
        body=body,
        scope=args.scope or args.title,
        author=args.actor or "editor",
        task_id=args.task_id or "",
        chapter_id=args.chapter_id or "",
        verdict=args.verdict or "",
        source_sha256=args.source_sha256 or "",
        created_at=args.created_at or "",
    )

    if args.dry_run:
        heading("预演（未写入）")
        info(f"kind：{kind}（type: {KIND_TYPES[kind]}）")
        info(f"目标：{target_path(pp, note).relative_to(pp.vault).as_posix()}")
        print("\n--- frontmatter ---")
        print(note.render().split("---\n\n", 1)[0])
        return 0

    try:
        path = write_note(pp, note)
    except (ConflictError, UsageError) as exc:
        fail(str(exc))
        return 1

    op = oplog(cfg).record(
        actor=note.author,
        command="note",
        project_id=pp.project_id,
        outputs=[{"path": path.relative_to(pp.vault).as_posix(), "kind": kind, "bytes": len(note.render())}],
        note=note.name,
    )
    ok(f"已写入 {path.relative_to(pp.vault).as_posix()}")
    info("08_Reviews 不进正式区，不需要 approve/publish，也不算签字基线")
    if note.verdict:
        info(f"审阅结论「{note.verdict}」只是意见；正文要成为正式事实仍须 approve + publish")
    print(f"  op: {op.op_id}")
    return 0


__all__ = ["cmd_note"]
