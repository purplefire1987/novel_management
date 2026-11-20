"""索引命令：rebuild-index（T03 重写）与 search。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .. import config as cfg_mod
from ..errors import UsageError
from ..indexing.locate import active_db, index_dir, manifest_path, pointer_path
from ..indexing import (
    EMPTY_TABLES,
    GitRevisionSource,
    WorktreeSource,
    build_index,
    connect,
    head_revision,
    index_metadata,
    is_dirty,
    search,
)
from ..indexing.source import skip_dirs_from_layout
from ._common import fail, heading, info, ok, warn

def _pick_source(vault: Path, revision: str | None, skip_dirs: tuple[str, ...]):
    if revision:
        source = GitRevisionSource(vault, revision, skip_dirs=skip_dirs)
        info(f"从 Git 提交构建：{source.revision[:12]}")
        return source

    head = head_revision(vault)
    source = WorktreeSource(vault, revision=head or "worktree", skip_dirs=skip_dirs)
    if not head:
        info("按工作树构建：Vault 不是 Git 仓库或还没有提交，索引没有 revision 可绑定")
    elif is_dirty(vault):
        info(f"按工作树构建：绑定 {head[:12]}，但工作树有未提交改动（索引含未提交内容）")
    else:
        info(f"按工作树构建：绑定 {head[:12]}")
    return source


def cmd_rebuild_index(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    vault = cfg.require_vault()
    runtime = cfg.require_runtime()

    heading("索引来源")
    skip_dirs = skip_dirs_from_layout(cfg.layout)
    source = _pick_source(vault, getattr(args, "canon_revision", None), skip_dirs)

    directory = index_dir(runtime)
    result = build_index(
        source,
        directory,
        manifest_path=manifest_path(runtime),
        pointer_path=pointer_path(runtime),
    )
    db_path = result.db_path

    heading("索引")
    ok(f"{result.artifact_count} 份产物，{result.block_count} 块，{result.id_count} 个带 id 的文件")
    info(f"库：{db_path}")
    info(f"指针：{pointer_path(runtime)}")
    info(f"清单：{manifest_path(runtime)}")
    info(f"build：{result.build_id}")
    info(f"全文检索：{result.fts_mode}")
    if result.skipped:
        info(f"跳过运行态/归档 {result.skipped} 份（{'、'.join(skip_dirs)}）")

    heading("建了但本版不填（不是「已支持」）")
    info("、".join(EMPTY_TABLES))
    info("语义填充分别归 T04（时间线）、T05（认知）、T07 之后（伏笔/依赖/问题）")
    thin = [f"{name}={count}" for name, count in result.tables.items() if count == 0]
    if thin:
        info(f"本版会填、但这次没数据：{'、'.join(thin)}")

    if result.duplicate_ids:
        warn(f"{len(result.duplicate_ids)} 个 ID 重复：{result.duplicate_ids[:5]}")
    if result.failed:
        warn(f"{len(result.failed)} 份文件解析失败，未收入索引，前 5 条：")
        for path, reason in result.failed[:5]:
            info(f"{path}：{reason}")
    if result.fts_note:
        warn(f"全文索引不可用，检索退化为子串扫描：{result.fts_note}")
    return 0


def _print_hit(index: int, hit) -> None:
    where = f"{hit.path}"
    if hit.block_id:
        where += f"  {hit.block_id}"
    if hit.line_start:
        where += f"（第 {hit.line_start} 行）"
    print(f"  {index}. {where}")
    if hit.artifact_id:
        print(f"     {hit.artifact_id}  {hit.snippet}")
    else:
        print(f"     {hit.snippet}")


def cmd_search(cfg: cfg_mod.Config, args: argparse.Namespace) -> int:
    runtime = cfg.require_runtime()
    db_path = active_db(runtime)
    if not db_path.exists():
        raise UsageError(f"索引库不存在：{db_path}；重跑 `novel rebuild-index`")

    conn = connect(db_path)
    try:
        result = search(
            conn,
            args.query,
            limit=args.limit,
            project=getattr(args, "project", None) or None,
        )
        meta = index_metadata(conn) if getattr(args, "show_index", False) else {}
    finally:
        conn.close()

    if getattr(args, "json", False):
        payload = {
            "query": result.query,
            "mode": result.mode,
            "total": result.total,
            "note": result.note,
            "hits": [
                {
                    "path": hit.path,
                    "project": hit.project,
                    "artifact_id": hit.artifact_id,
                    "block_id": hit.block_id,
                    "kind": hit.kind,
                    "line_start": hit.line_start,
                    "snippet": hit.snippet,
                }
                for hit in result.hits
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0 if result.found else 1

    heading(f"检索「{result.query}」")
    info(f"路径：{result.mode}；命中 {result.total} 处" + ("（只列前若干条）" if result.total > len(result.hits) else ""))
    if result.note:
        warn(result.note)
    if meta:
        info(f"索引依据 {meta.get('canon_revision', '?')[:12]}，build {meta.get('build_id', '?')}")
    if not result.found:
        fail("没有命中")
        return 1
    for number, hit in enumerate(result.hits, start=1):
        _print_hit(number, hit)
    return 0
