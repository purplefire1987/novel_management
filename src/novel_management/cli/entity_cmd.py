"""实体与关系命令：entity create / show / validate、relation create、event create。

（V1.0 §17、§5.2–§5.4、WF-05/06）

**为什么必须走命令而不是「让 Agent 直接写文件」**：建档最容易出的错不是写错字，
是「同一件事建了两份档」「ID 发重了」「类型填了个不存在的」。前两个要查全库
（`IdRegistry` + 索引），第三个要查注册表（`TYPE_TO_CANON_KIND`）。
这些校验放在命令里，Agent 就绕不过去；放在 Prompt 里，绕过去没人知道。

**新建默认预演**。和 `allocate-id` 一致：`create` 不给 `--confirm` 就只打印将要
写什么。发号会写审计行，文件会占住一个 ID——两件事都不该在「先看看」的时候发生。

`entity show` 与 `entity validate` 是只读的，`show` 回答「这个 ID 是谁」，
`validate` 回答「这份档案合不合契约」。后者与全局 `novel validate` 的区别是范围：
全局查结构、查重、双链，单份只查 schema。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import config as cfg_mod
from ..audit import ensure_actor, now_iso
from ..domain.errors import ConflictError, UsageError
from novel_management.domain.ids import IdAllocator, IdRegistry, resolve_prefix
from ..infrastructure.filesystem.paths import ProjectPaths
from novel_management.schemas.store import SchemaStore, schema_name_for
from ..infrastructure.filesystem.storage import dump_yaml, write_text_atomic
from ._common import fail, heading, info, name_slug, ok, project, read_meta, warn


# ---------------------------------------------------------------------------
# entity
# ---------------------------------------------------------------------------


def _compact(front: dict) -> dict:
    """把「没记」的字段**留空**，而不是写成空串。

    `summary: ""` 与「没有 summary」在语义上是两件事，但没人会照这个区别维护库；
    更实际的问题是空串会撞上约束——`minLength: 1` 会判空 summary 不合法，
    `location: ""` 会撞 `^ent_[0-9]{6}$` 的 ID 模式，一份本来没问题的档案因此校验不过。

    规则只有一条：空字符串、空列表不写进 frontmatter。
    **显式取值一律保留**，包括 `unknown` / `false` / `0` / `null`——那是作者说的话，
    不是缺失。`time_precision: unknown` 与「没有 time_precision」必须能区分开：
    前者是「查过，确实不知道」，后者是「没查」。
    """
    return {k: v for k, v in front.items() if not (isinstance(v, (str, list)) and len(v) == 0)}


def cmd_entity(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    action = args.entity_command
    if action == "create":
        return _entity_create(cfg, args)
    if action == "show":
        return _entity_show(cfg, args)
    if action == "validate":
        return _entity_validate(cfg, args)
    fail(f"未知子命令 {action}")
    return 2


def _entity_create(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    actor = ensure_actor(getattr(args, "actor", None), "entity")
    pp = project(cfg, args)
    runtime = cfg.require_runtime()
    # 默认预演：只有显式 --confirm 才写盘。--dry-run 用来在脚本里把
    # 「这次一定是预演」写死，免得哪天默认值改了，脚本开始偷偷建档。
    dry = bool(args.dry_run) or not bool(args.confirm)

    target_dir = pp.dir_for_type(args.type)
    name = (args.name or "").strip()
    if not name:
        raise UsageError("实体必须有名字（--name）")

    # 先查重再发号：发号写审计，查重没过的稿子不该留下一个号。
    registry = IdRegistry(pp.vault, areas=pp.area_roots())
    same = _same_name(registry, pp, name)
    if same and not args.allow_duplicate:
        detail = "；".join(f"{eid}（{path}）" for eid, path in same[:5])
        raise ConflictError(
            f"已经有同名档案：{detail}\n"
            "同名建第二份会让「按名称取对象」永远有歧义。"
            "确认是不同对象（同名不同人／同名不同物）请加 --allow-duplicate。"
        )

    prefix = resolve_prefix("entity" if args.prefix != "asset" else "asset")
    alloc = IdAllocator(pp.vault, runtime, actor=actor, areas=pp.area_roots())
    entity_id = alloc.allocate(prefix, reason=args.note or f"新建实体 {name}", dry_run=dry)[0]

    front = {
        "id": entity_id,
        "novel_id": pp.project_id,
        "type": args.type,
        "name": name,
        "aliases": [a.strip() for a in (args.alias or []) if a.strip()],
        "summary": args.summary or "",
        "tags": [t.strip() for t in (args.tag or []) if t.strip()],
        "status": args.status,
        "canon_status": args.canon_status,
        "schema_version": "1.0",
        "content_version": 1,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "created_by": actor,
        "updated_by": actor,
        "source_refs": [s.strip() for s in (args.source or []) if s.strip()],
        "confidence": args.confidence if args.confidence is not None else None,
    }
    if args.subtype:
        front["subtype"] = args.subtype

    errors = SchemaStore().check("entity", front)
    if errors:
        heading("契约校验未过，不写盘")
        for item in errors[:10]:
            fail(item)
        return 1

    path = target_dir / f"{entity_id}_{name_slug(name)}.md"
    if path.exists():
        raise ConflictError(f"目标已存在，不覆盖：{pp.rel(path)}")

    body = (
        f"---\n{dump_yaml(front)}---\n\n"
        f"# {name}\n\n"
        f"{args.summary or '（待补）'}\n"
    )

    if args.json:
        print(
            json.dumps(
                {"id": entity_id, "target": pp.rel(path), "dry_run": dry},
                ensure_ascii=False,
                indent=2,
            )
        )
        if dry:
            return 0

    if dry:
        heading(f"{entity_id} → {pp.rel(path)}")
        info(f"类型 {args.type} 归入 {target_dir.name}")
        info("预演：没有写盘。加 --confirm 落盘。")
        return 0

    target_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, body)
    ok(f"{entity_id} → {pp.rel(path)}")
    info(f"类型 {args.type} → {target_dir.name}；契约校验通过")
    return 0


def _same_name(registry: IdRegistry, pp: ProjectPaths, name: str) -> list[tuple[str, str]]:
    """按 name 精确匹配已有档案。只比名称与别名，不做模糊匹配——
    模糊匹配会把「安怀舟」和「安怀舟之母」判成同一个。"""
    hits: list[tuple[str, str]] = []
    for path in pp.canon.rglob("*.md") if pp.canon.is_dir() else []:
        meta = read_meta(path)
        names = [str(meta.get("name") or "")] + [str(x) for x in meta.get("aliases") or []]
        if name in {n.strip() for n in names if n.strip()}:
            hits.append((str(meta.get("id") or "?"), pp.rel(path)))
    return hits


def _entity_show(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    pp = project(cfg, args)
    found = _find_by_id(pp, args.id)
    if not found:
        fail(f"找不到 {args.id}（在 02_世界知识库／06_素材与研究 里查过）")
        return 1
    path, meta = found

    if args.json:
        print(json.dumps({"path": pp.rel(path), "meta": meta}, ensure_ascii=False, indent=2))
        return 0

    heading(f"{meta.get('id')} · {meta.get('name')}")
    info(f"路径：{pp.rel(path)}")
    info(f"类型：{meta.get('type') or meta.get('entity_type') or '未记'}"
         f"   子类：{meta.get('subtype') or '—'}")
    info(f"状态：{meta.get('status') or '—'}   Canon：{meta.get('canon_status') or '—'}")
    aliases = [str(x) for x in meta.get("aliases") or []]
    if aliases:
        info("别名：" + "、".join(aliases))
    if meta.get("summary"):
        print()
        info(str(meta["summary"]))
    refs = meta.get("source_refs") or []
    if refs:
        print()
        info(f"来源 {len(refs)} 条：")
        for ref in refs[:8]:
            info(f"  {ref}")
    return 0


def _entity_validate(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    pp = project(cfg, args)
    store = SchemaStore()

    targets: list[tuple[Path, dict]] = []
    if args.id:
        found = _find_by_id(pp, args.id)
        if not found:
            fail(f"找不到 {args.id}")
            return 1
        targets = [found]
    else:
        root = pp.canon if pp.canon.is_dir() else pp.root
        for path in sorted(root.rglob("*.md")):
            meta = read_meta(path)
            if meta.get("id"):
                targets.append((path, meta))
        if not targets:
            warn("没有带 id 的档案可校验")
            return 0

    bad = 0
    for path, meta in targets:
        # 按 frontmatter 的 type 选契约。以前这里写死了 if-elif，关系档会被当实体判，
        # 于是 `rel_` 先撞 entity 的 ID 模式，字段面的问题全被挡在后面。
        name = schema_name_for(str(meta.get("type")))
        errors = store.check(name, meta)
        if errors:
            bad += 1
            fail(f"{pp.rel(path)}：{errors[0]}" + (f"（另有 {len(errors)-1} 处）" if len(errors) > 1 else ""))

    if bad:
        heading(f"{bad}/{len(targets)} 份不合契约")
        return 1
    ok(f"{len(targets)} 份全部通过 {args.id or '知识库'} 契约校验")
    return 0


def _find_by_id(pp: ProjectPaths, entity_id: str) -> tuple[Path, dict] | None:
    wanted = (entity_id or "").strip()
    if not wanted:
        return None
    roots = [p for p in (pp.canon, pp.assets) if p.is_dir()]
    for root in roots:
        for path in root.rglob("*.md"):
            meta = read_meta(path)
            if str(meta.get("id") or "").strip() == wanted:
                return path, meta
        # 文件名兜底：frontmatter 是权威，但没有 id 时文件名仍是线索。
        for path in root.rglob(f"{wanted}_*.md"):
            return path, read_meta(path)
    return None


# ---------------------------------------------------------------------------
# relation
# ---------------------------------------------------------------------------


def cmd_relation(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    actor = ensure_actor(getattr(args, "actor", None), "relation")
    pp = project(cfg, args)
    runtime = cfg.require_runtime()
    dry = bool(args.dry_run) or not bool(args.confirm)

    src = _find_by_id(pp, args.from_id)
    dst = _find_by_id(pp, args.to_id)
    for label, found, raw in (("起点", src, args.from_id), ("终点", dst, args.to_id)):
        if not found:
            fail(f"{label}实体不存在：{raw}。关系两端必须都是已建档的实体。")
            return 1

    alloc = IdAllocator(pp.vault, runtime, actor=actor, areas=pp.area_roots())
    rel_id = alloc.allocate("rel", reason=args.note or "登记关系", dry_run=dry)[0]

    # 关系是「追加」不是「覆盖」：人物关系变了，旧关系要留着并写上 valid_to。
    # 覆盖历史关系，等于让「他曾经是她的师父」这件事消失。
    front = {
        "id": rel_id,
        "novel_id": pp.project_id,
        "type": "relationship",
        "name": f"{src[1].get('name')} → {dst[1].get('name')}",
        "from_entity_id": str(src[1].get("id")),
        "to_entity_id": str(dst[1].get("id")),
        "relationship_type": args.relation_type,
        "valid_from": args.valid_from or "",
        "valid_to": args.valid_to or "",
        "status": "active" if not args.valid_to else "ended",
        "source_refs": [s.strip() for s in (args.source or []) if s.strip()],
        "confidence": args.confidence if args.confidence is not None else None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "created_by": actor,
        "updated_by": actor,
    }

    target_dir = pp.canon_dir("关系与状态")
    front = _compact(front)
    path = target_dir / f"{rel_id}_{name_slug(str(front['name']))}.md"
    body = f"---\n{dump_yaml(front)}---\n\n# {front['name']}\n\n{args.summary or '（待补）'}\n"

    # 契约校验放在写盘之前、也放在预演里：关系档此前没有任何契约，写出什么样没人管。
    # 预演阶段就把错误报出来，比落盘之后再让 validate 抓更省事。
    errors = SchemaStore().check("relationship", front)
    if errors:
        for err in errors:
            fail(f"{rel_id}：{err}")
        return 1

    if dry:
        heading(f"{rel_id} → {pp.rel(path)}")
        info(f"{front['from_entity_id']} --{args.relation_type}--> {front['to_entity_id']}")
        info("预演：没有写盘。加 --confirm 落盘。")
        return 0

    if path.exists():
        raise ConflictError(f"目标已存在，不覆盖：{pp.rel(path)}")
    target_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, body)
    ok(f"{rel_id} → {pp.rel(path)}")
    info("旧关系不会被覆盖：关系变化请新登记一条并把上一条的 valid_to 填上。")
    return 0


# ---------------------------------------------------------------------------
# event
# ---------------------------------------------------------------------------


def cmd_event(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    actor = ensure_actor(getattr(args, "actor", None), "event")
    pp = project(cfg, args)
    runtime = cfg.require_runtime()
    dry = bool(args.dry_run) or not bool(args.confirm)

    name = (args.name or "").strip()
    if not name:
        raise UsageError("事件必须有名字（--name）")

    alloc = IdAllocator(pp.vault, runtime, actor=actor, areas=pp.area_roots())
    event_id = alloc.allocate("evt", reason=args.note or f"登记事件 {name}", dry_run=dry)[0]

    front = {
        "id": event_id,
        "novel_id": pp.project_id,
        "type": "event",
        "name": name,
        "summary": args.summary or "",
        "status": args.status,
        "canon_status": args.canon_status,
        # 时间三件套：世界时间、精度、叙事位置。**不确定就写不确定**，
        # 这里给 `precision` 而不是给一个猜出来的日期。
        "world_time": args.world_time or "",
        "time_precision": args.precision,
        "narrative_time": args.narrative or "",
        "participants": [p.strip() for p in (args.participant or []) if p.strip()],
        "location": args.location or "",
        "source_refs": [s.strip() for s in (args.source or []) if s.strip()],
        "confidence": args.confidence if args.confidence is not None else None,
        # 事实状态默认 unknown 而不是 confirmed：作者把「传闻 X 杀了 Y」登记进来时，
        # 默认成 confirmed 会把传闻变成事实，而这是这类库里最难查出来的污染。
        # 默认 unknown 是一个**看得见的缺口**，continuity check 能报出来。
        "fact_status": args.fact_status,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "created_by": actor,
        "updated_by": actor,
    }

    target_dir = pp.canon_dir("事件与历史")
    front = _compact(front)
    path = target_dir / f"{event_id}_{name_slug(name)}.md"
    body = f"---\n{dump_yaml(front)}---\n\n# {name}\n\n{args.summary or '（待补）'}\n"

    errors = SchemaStore().check("event", front)
    if errors:
        for err in errors:
            fail(f"{event_id}：{err}")
        return 1

    if dry:
        heading(f"{event_id} → {pp.rel(path)}")
        info(f"时间：{args.world_time or '（未记）'}   精度：{args.precision}   事实状态：{args.fact_status}")
        if args.fact_status == "unknown":
            info("事实状态未定：确认发生过用 --fact-status confirmed，传闻用 rumored。")
        info("预演：没有写盘。加 --confirm 落盘。")
        return 0

    if path.exists():
        raise ConflictError(f"目标已存在，不覆盖：{pp.rel(path)}")
    target_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, body)
    ok(f"{event_id} → {pp.rel(path)}")
    # 只在真的没记时间时才提这句。无条件的提醒会变成噪声，而噪声读久了
    # 就没人读了——「提示语说的事」必须和「这次实际发生的事」对得上。
    if not args.world_time:
        info("未记时间的事件在时间线查询里会报 UNORDERED——这比硬排一个顺序诚实。")
    if args.fact_status == "unknown":
        info("事实状态记的是 unknown（查过，未定）。要区分传闻与事实请补 --fact-status。")
    return 0
