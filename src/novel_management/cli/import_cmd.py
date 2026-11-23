"""`novel import`：扫原稿、看账、按批入 staging、反查来源。

命令分工对应 PRD §12.2 的十步：

| 步骤 | 命令 |
|---|---|
| 1–3 验证、复制、算哈希、生成 ImportManifest | `import scan` |
| 4–9 解析、定位符、异常、提取、别名、冲突报告 | `import scan`（一次算完，分开看） |
| 看账 | `import report` / `import list` |
| 反查某条来源 | `import where` |
| 10 经批准后分批发布 | `import stage` + 既有的 approve / release |

`import scan` 全程**不写正式区**：原始字节进归档区，报告进 runtime。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import config as cfg_mod
from ..audit import ensure_actor
from ..domain.errors import UsageError
from ..importing import ImportStore, run_scan, stage_batch
from ..importing.model import read_locator
from ..importing.report import plan_batches
from ..infrastructure.filesystem.paths import ProjectPaths
from ._common import fail, heading, info, ok, oplog, project, task_store, warn


def cmd_import(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    action = getattr(args, "import_command", None)
    if action == "scan":
        return _scan(cfg, args)
    if action == "report":
        return _report(cfg, args)
    if action == "stage":
        return _stage(cfg, args)
    if action == "where":
        return _where(cfg, args)
    if action == "list":
        return _list(cfg, args)
    raise UsageError("用法：novel import scan|report|stage|where|list")


# --------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------

def _scan(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    actor = ensure_actor(getattr(args, "actor", None), "stage")
    pp = project(cfg, args)
    outcome = run_scan(
        pp,
        cfg.require_runtime(),
        [Path(p) for p in args.paths],
        level=getattr(args, "level", 0) or 0,
        seed=getattr(args, "seed", "") or "novel-import-v1",
        sample_size=getattr(args, "sample", 0) or 100,
        actor=actor,
    )
    manifest = outcome.manifest

    if getattr(args, "json", False):
        print(json.dumps({"manifest": manifest.as_dict(), "detail": manifest.detail}, ensure_ascii=False, indent=2))
        return 0 if not manifest.blocking() else 1

    heading(f"导入 {manifest.import_id}")
    info(f"项目      : {manifest.project_id}")
    info(f"时间      : {manifest.created_at}")

    print("  文件：")
    for item in manifest.files:
        if item.get("rejected"):
            warn(f"{item['path']}：{item['rejected']}")
        else:
            print(
                f"    ✓ {item['path']}  {item['sha256'][:12]}…  {item['bytes']} 字节"
                f"  {item['lines']} 行  {item['format']}/{item['encoding']}"
            )

    print()
    info("逐维度验收：")
    failed = 0
    for label, passed, detail in manifest.verdicts():
        mark = "[OK]  " if passed else "[未达标]"
        if not passed:
            failed += 1
        print(f"    {mark} {label}：{detail}")

    if manifest.chapters_pending:
        print()
        info(f"待提取章节（{len(manifest.chapters_pending)}）——正文已保全，可查询，只是没提到候选：")
        for item in manifest.chapters_pending[:20]:
            print(f"    · {item}")
        if len(manifest.chapters_pending) > 20:
            print(f"    …… 另有 {len(manifest.chapters_pending) - 20} 章")

    if manifest.paragraphs_unmapped:
        print()
        warn(f"未映射段落（{len(manifest.paragraphs_unmapped)}）——已在报告里列明，不阻断发布：")
        for item in manifest.paragraphs_unmapped[:10]:
            print(f"    · {item}")

    if manifest.parse_errors:
        print()
        warn(f"解析异常（{len(manifest.parse_errors)}）：")
        for item in manifest.parse_errors:
            print(f"    · {item}")

    if manifest.known_limits:
        print()
        info(f"已知限制（{len(manifest.known_limits)}）：")
        for item in manifest.known_limits:
            print(f"    · {item}")

    print()
    problems = manifest.blocking()
    if problems:
        warn("不能作为发布候选：" + "；".join(problems))
    else:
        ok("可以作为发布候选进 staging")
    batches = plan_batches(manifest.chapters_parsed, getattr(args, "batch_size", 0) or 0)
    if batches:
        info(f"分批：共 {len(batches)} 批")
        info(f"下一步：novel import stage {manifest.import_id} --batch 1 --as writer")

    oplog(cfg).record(
        actor=actor,
        command="import scan",
        project_id=pp.project_id,
        outputs=[{"import_id": manifest.import_id, "chapters": manifest.chapters_parsed}],
    )
    # 部分失败不伪报全量成功：有维度没达标就是非 0
    return 0 if failed == 0 else 1


# --------------------------------------------------------------------------
# report / list / where
# --------------------------------------------------------------------------

def _report(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    store = ImportStore(cfg.require_runtime())
    manifest = store.load(args.import_id)
    if getattr(args, "json", False):
        print(json.dumps({"manifest": manifest.as_dict(), "detail": manifest.detail}, ensure_ascii=False, indent=2))
        return 0 if not manifest.blocking() else 1

    heading(f"导入报告 {manifest.import_id}")
    info(f"时间      : {manifest.created_at}")
    info(f"文件      : {len(manifest.files)} 份（接收 {sum(1 for f in manifest.files if not f.get('rejected'))}）")
    info(f"章节      : {manifest.chapters_parsed} 章，{manifest.paragraphs_mapped} 段已映射")
    info(f"原始字节  : {'100% 保全' if manifest.raw_bytes_preserved else '未保全'}")
    info(f"来源引用  : {'可解析' if manifest.refs_valid else '有不可解析项'}")
    info(f"阻断冲突  : {manifest.blocking_conflicts}")

    sample = manifest.sample_result or {}
    if sample:
        info(
            f"抽样      : {sample.get('sample_size')} 条，定位准确率 "
            f"{float(sample.get('accuracy') or 0):.1%}，种子 {sample.get('seed')}"
        )

    for label, passed, detail in manifest.verdicts():
        mark = "[OK]  " if passed else "[未达标]"
        print(f"    {mark} {label}：{detail}")

    if args.chapters:
        print()
        info("章节：")
        for item in (manifest.detail or {}).get("chapters", []):
            print(
                f"    {item['index']:04d}  {item['title']}  "
                f"`{item['locator']}`  {item['paragraphs']} 段 / {item['chars']} 字"
            )
    if args.mentions:
        print()
        info("候选提及（未批准，不作为事实）：")
        for chapter in (manifest.detail or {}).get("extractions", []):
            for mention in chapter.get("mentions", []):
                print(
                    f"    {chapter['index']:04d}  {mention['kind']:5s} {mention['value']} "
                    f"→ {mention['entity_id'] or '—'}  `{mention['locator']}`"
                )
    problems = manifest.blocking()
    if problems:
        warn("不能作为发布候选：" + "；".join(problems))
    return 0 if not problems else 1


def _list(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    store = ImportStore(cfg.require_runtime())
    ids = store.list()
    if not ids:
        info("还没有任何导入")
        return 0
    if getattr(args, "json", False):
        rows = []
        for import_id in ids:
            manifest = store.load(import_id)
            rows.append(
                {
                    "import_id": import_id,
                    "created_at": manifest.created_at,
                    "chapters": manifest.chapters_parsed,
                    "pending": len(manifest.chapters_pending),
                    "blocking": manifest.blocking(),
                }
            )
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    for import_id in ids:
        manifest = store.load(import_id)
        state = "可发布" if not manifest.blocking() else "不可发布"
        print(
            f"  {import_id}  {manifest.created_at}  {manifest.chapters_parsed:3d} 章  "
            f"待提取 {len(manifest.chapters_pending):3d}  {state}"
        )
    return 0


def _where(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    """反查一条来源定位符。这是「来源定位有效」的人可验证入口。"""
    pp = project(cfg, args)
    store = ImportStore(cfg.require_runtime())
    raw_dir = store.raw_dir(pp, args.import_id)
    try:
        text = read_locator(raw_dir, args.locator)
    except UsageError as exc:
        fail(str(exc))
        return 2
    heading(f"{args.import_id} · {args.locator}")
    print(text)
    return 0


# --------------------------------------------------------------------------
# stage
# --------------------------------------------------------------------------

def _stage(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    actor = ensure_actor(getattr(args, "actor", None), "stage")
    pp: ProjectPaths = project(cfg, args)
    result = stage_batch(
        pp,
        cfg.require_runtime(),
        args.import_id,
        batch=getattr(args, "batch", 1) or 1,
        batch_size=getattr(args, "batch_size", 0) or 0,
        actor=actor,
        task_store=task_store(cfg),
    )
    heading(f"{result.import_id} 第 {result.batch}/{result.batches} 批已入 staging")
    info(f"任务      : {result.task_id}")
    info(f"章节      : {len(result.chapters)}")
    if result.reused:
        info("发号      : 复用该批次此前已发出的号（重跑不再吃新号）")
    for item in result.chapters:
        print(f"    · {item}")
    if result.skipped:
        warn(f"跳过 {len(result.skipped)} 章（只有标题没有正文，未生成提案）：")
        for item in result.skipped:
            print(f"    · {item}")
    print("  产物：")
    for item in result.files:
        print(f"    + {item}")
    print()
    info(f"审阅：novel diff --task {result.task_id}")
    info(f"批准：novel approve artifact {result.task_id} --hash <inspect 报的哈希> --as author")
    info(f"发布：novel release prepare --task {result.task_id} --as author")
    oplog(cfg).record(
        actor=actor,
        command="import stage",
        project_id=pp.project_id,
        outputs=[{"import_id": result.import_id, "task_id": result.task_id, "files": result.files}],
        task_id=result.task_id,
    )
    return 0
