"""项目与灵感命令：project list / project show / idea create（V1.0 §17、WF-01/02）。

这三条在 V1.0 §17 的命令表里，但此前的实现里一个都没有——`init-project`
建得出项目，却没有任何命令回答「现在有哪些项目」「这个项目长什么样」。
补它们不是为了凑命令表，是因为**没有 `project show` 就没法在动手前确认
「我要写的是哪个库」**，而这个系统最怕的错就是写错库。

`idea create` 的原则只有一条：**原文照录**。灵感阶段做取舍是后面 WF-02 的事，
这里改写一个字，作者三天后就认不出那是自己写的了。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import config as cfg_mod
from ..audit import ensure_actor, now_iso
from ..errors import ConflictError, UsageError
from ..ids import IdAllocator
from ..paths import ProjectPaths
from ..storage import dump_yaml, write_text_atomic
from ._common import fail, heading, info, name_slug, ok, project, read_meta, warn


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------


def _project_roots(cfg: cfg_mod.Config) -> list[Path]:
    vault = cfg.require_vault()
    base = vault / str(cfg.layout.get("projects_dir") or "01_小说项目")
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "10_项目配置" / "project.yaml").is_file())


def cmd_project(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    vault = cfg.require_vault()
    if args.project_command == "list":
        roots = _project_roots(cfg)
        if args.json:
            rows = []
            for root in roots:
                meta = cfg_mod.read_yaml(root / "10_项目配置" / "project.yaml") or {}
                rows.append(
                    {
                        "project_id": meta.get("project_id") or root.name,
                        "name": meta.get("project_name") or "",
                        "status": meta.get("project_status") or "",
                        "path": root.relative_to(vault).as_posix(),
                    }
                )
            print(json.dumps({"projects": rows}, ensure_ascii=False, indent=2))
            return 0

        heading(f"项目 {len(roots)} 个（{vault / str(cfg.layout.get('projects_dir') or '01_小说项目')}）")
        if not roots:
            warn("一个项目都没有。用 novel init-project 建一个。")
            return 0
        for root in roots:
            meta = cfg_mod.read_yaml(root / "10_项目配置" / "project.yaml") or {}
            flag = "  ← 当前" if str(meta.get("project_id")) == str(cfg.project_id or "") else ""
            print(
                f"  {str(meta.get('project_id') or '?'):<12s} "
                f"{str(meta.get('project_name') or ''):<10s} "
                f"{str(meta.get('project_status') or ''):<10s} {root.relative_to(vault).as_posix()}{flag}"
            )
        return 0

    # show
    pp = project(cfg, args)
    meta = cfg_mod.read_yaml(pp.project_meta / "project.yaml") or {}

    counts = _count_by_top_dir(pp)
    total = sum(counts.values())

    if args.json:
        print(
            json.dumps(
                {
                    "project_id": pp.project_id,
                    "root": pp.rel(pp.root),
                    "meta": meta,
                    "sections": counts,
                    "documents": total,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    heading(f"{meta.get('project_id') or pp.project_id} · {meta.get('project_name') or ''}")
    info(f"目录：{pp.rel(pp.root)}")
    info(f"状态：{meta.get('project_status') or '未记'}   时区：{meta.get('timezone') or '未记'}")
    info(f"schema_version：{meta.get('schema_version') or '未记'}")

    print()
    heading(f"结构（md {total} 份）")
    for name, count in counts.items():
        print(f"  {name:<24s} {count}")

    canon = pp.canon
    if canon.is_dir():
        print()
        heading("知识库十七类")
        empty: list[str] = []
        for kind in sorted(p.name for p in canon.iterdir() if p.is_dir()):
            n = sum(1 for _ in canon.joinpath(kind).rglob("*.md"))
            print(f"  {kind:<14s} {n}")
            if n == 0:
                empty.append(kind)
        if empty:
            info("空目录：" + "、".join(empty) + "（不是漏搬——按 type 归类后真的没有成员）")
    return 0


def _count_by_top_dir(pp: ProjectPaths) -> dict[str, int]:
    """按项目根下的**一级目录**数 md。只数一级：再细分就会变成
    「换个目录名就少一行」的脆弱报表，而这里要回答的只是「东西都在不在」。"""
    out: dict[str, int] = {}
    if not pp.root.is_dir():
        return out
    for child in sorted(pp.root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        out[child.name] = sum(1 for _ in child.rglob("*.md"))
    return out


# ---------------------------------------------------------------------------
# idea
# ---------------------------------------------------------------------------


def cmd_idea(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    if args.idea_command != "create":
        fail(f"未知子命令 {args.idea_command}")
        return 2

    actor = ensure_actor(getattr(args, "actor", None), "idea")
    pp = project(cfg, args)
    runtime = cfg.require_runtime()
    dry = bool(args.dry_run) or not bool(args.confirm)

    raw = _read_text(args.text, getattr(args, "text_file", ""))
    if not raw.strip():
        raise UsageError("灵感正文是空的。用 --text 或 --text-file 给一段原文。")

    alloc = IdAllocator(pp.vault, runtime, actor=actor, areas=pp.area_roots())
    idea_id = alloc.allocate("idea", reason=args.note or "灵感录入", dry_run=dry)[0]

    title = (args.title or _first_line(raw) or "未命名灵感").strip()
    target_dir = pp.in_dir("idea_root")
    path = target_dir / f"{idea_id}_{name_slug(title)}.md"

    front = {
        "id": idea_id,
        "type": "idea",
        "name": title,
        "status": "raw",
        "tags": [t.strip() for t in (args.tag or []) if t.strip()],
        "source_refs": [s.strip() for s in (args.source or []) if s.strip()],
        "created_at": now_iso(),
        "created_by": actor,
    }

    if args.json:
        print(
            json.dumps(
                {"id": idea_id, "target": pp.rel(path), "dry_run": dry},
                ensure_ascii=False,
                indent=2,
            )
        )
        if dry:
            return 0

    if path.exists():
        raise ConflictError(f"目标已存在，不覆盖：{pp.rel(path)}")

    body = (
        f"---\n{dump_yaml(front)}---\n\n"
        f"# {title}\n\n"
        f"<!-- 原文照录：以下内容与作者给的一字不差，标点也不改。 -->\n\n"
        f"{raw.rstrip()}\n"
    )

    if dry:
        heading(f"{idea_id} → {pp.rel(path)}")
        info("预演：没有写盘。加 --confirm 落盘。")
        return 0

    target_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, body)
    ok(f"{idea_id} → {pp.rel(path)}")
    info("灵感阶段不改写、不做取舍；评估与立项是 WF-02 的事。")
    return 0


def _read_text(text: str | None, text_file: str) -> str:
    if text_file:
        return Path(text_file).expanduser().read_text(encoding="utf-8")
    return text or ""


def _first_line(raw: str) -> str:
    for line in raw.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:40]
    return ""
