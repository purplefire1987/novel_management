"""备份与恢复：backup / restore。"""

from __future__ import annotations

import argparse

from .. import config as cfg_mod
from ..audit import ensure_actor
from ..backup import DEFAULT_KEEP, BackupStore
from ..errors import UsageError
from ..storage import human_size
from ._common import backup_store, fail, info, ok, oplog, warn


def cmd_backup(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    store = backup_store(cfg, getattr(args, "out", None))

    if getattr(args, "list", False):
        items = store.list()
        if not items:
            print(f"尚无备份（目录：{store.out_dir or '(未配置)'}）")
            return 0
        print(f"备份 {len(items)} 份（{store.out_dir}）")
        for meta in items:
            print(
                f"  {meta.get('backup_id'):<22} {meta.get('files'):>6} 文件 "
                f"{human_size(int(meta.get('bytes') or 0)):>9}  "
                f"verified={meta.get('verified')}  {meta.get('label') or ''}"
            )
        return 0

    if getattr(args, "prune", False):
        # 不能用 `keep or DEFAULT_KEEP`：`--keep 0` 是 0（falsy），会被静默改成 10，
        # 于是 prune() 里那条 `keep < 1` 的拒绝永远收不到 0。默认值由 argparse 给。
        keep = int(getattr(args, "keep", DEFAULT_KEEP))
        result = store.prune(keep=keep, dry_run=not getattr(args, "confirm", False))
        if not result["total"]:
            print(f"没有备份可清理（目录：{result['root']}）")
            return 0
        if not result["removable"]:
            ok(f"{result['total']} 份备份，未超过保留数 {keep}，无需清理")
            return 0

        if result["dry_run"]:
            print(f"将删除 {len(result['removable'])} 份，释放 {human_size(result['freed_bytes'])}")
        else:
            ok(f"已删除 {len(result['removable'])} 份，释放 {human_size(result['freed_bytes'])}")
        for item in result["removable"]:
            print(
                f"  - {item['backup_id']:<24} {human_size(item['bytes']):>9}  "
                f"{item['files']:>6} 文件  {item['created_at']}"
            )
        if result["dry_run"]:
            print()
            print("预演不删。确认清理请加 --confirm")
            print("只处理 BK-*：人工建的备份（例如 maps-prov-39-*）不受影响")
        return 0

    vault = cfg.require_vault()
    actor = ensure_actor(getattr(args, "actor", None), "backup")
    meta = store.create(
        vault,
        label=getattr(args, "label", "") or "",
        include_git=not getattr(args, "no_git", False),
        project_id=cfg.project_id,
    )
    op = oplog(cfg).record(
        actor=actor,
        command="backup",
        project_id=cfg.project_id,
        inputs=[{"vault": str(vault)}],
        outputs=[{"backup_id": meta["backup_id"], "files": meta["files"], "bytes": meta["bytes"]}],
        note=meta.get("label", ""),
    )
    ok(f"备份完成并校验通过：{meta['backup_id']}")
    info(f"位置 {meta['path']}")
    git_note = "已含 .git" if meta.get("git_saved") else ("要求含 .git 但 Vault 无 .git" if meta["include_git"] else "按参数排除 .git")
    info(f"{meta['files']} 个文件，{human_size(meta['bytes'])}，{git_note}")
    info(f"清单 {meta['path']}/manifest.csv，元数据 meta.json")
    print(f"  op: {op.op_id}")
    return 0


def cmd_restore(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    store = backup_store(cfg, getattr(args, "out", None))
    vault = cfg.require_vault()
    backup_id = args.backup_id
    if not backup_id:
        raise UsageError("缺少 --backup-id（用 novel backup --list 查看）")

    meta = store.load(backup_id)
    if not meta.get("verified", False):
        warn(f"备份 {backup_id} 的 verified 不为真，恢复有风险")

    plan = store.plan_restore(backup_id, vault)
    print(f"备份 {backup_id} → {vault}")
    print(f"  清单 {plan['total']} 个文件")
    print(f"  新增 {len(plan['new'])}，内容相同 {len(plan['same'])}，冲突 {len(plan['conflict'])}，损坏 {len(plan['broken'])}")

    for label, key in (("新增", "new"), ("冲突", "conflict"), ("损坏", "broken")):
        rows = plan.get(key) or []
        if not rows:
            continue
        print(f"  {label}:")
        for entry in rows[:10]:
            print(f"    - {entry.rel}")
        if len(rows) > 10:
            print(f"    ……另 {len(rows) - 10} 个")

    if plan["broken"]:
        fail("备份内文件哈希与清单不符，拒绝恢复")
        return 1

    if getattr(args, "dry_run", False) or not getattr(args, "confirm", False):
        print("\nrestore: 预演结束，未写入任何文件")
        print("确认恢复请加 --confirm；覆盖冲突文件再加 --overwrite（覆盖前会另存一份）")
        return 0

    actor = ensure_actor(getattr(args, "actor", None), "restore")
    result = store.restore(backup_id, vault, overwrite=getattr(args, "overwrite", False))
    op = oplog(cfg).record(
        actor=actor,
        command="restore",
        project_id=cfg.project_id,
        inputs=[{"backup_id": backup_id}],
        outputs=[{"path": rel} for rel in result["written"]],
        note=f"skipped={len(result['skipped'])}",
    )
    ok(f"恢复完成：写入 {len(result['written'])} 个文件，跳过冲突 {len(result['skipped'])} 个")
    if result["saved_before_overwrite"]:
        info(f"覆盖前已另存 {len(result['saved_before_overwrite'])} 个文件到 {result['undo_dir']}")
    print(f"  op: {op.op_id}")
    print("  建议接着运行：novel doctor、novel validate、novel rebuild-index")
    return 0
