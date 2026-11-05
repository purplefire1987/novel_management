"""索引重建：清空并重建 index.sqlite（PRD 8.3 / AC-12）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Config
from .. import vault
from .indexer import index_vault
from .sqlite import Index


def rebuild(cfg: Config, *, full: bool = True) -> dict[str, Any]:
    """重建索引。`full=False` 只重读改动过的文件（mtime/size 判定）。"""
    cfg.ensure_dirs()
    db = cfg.index_db
    cleaned = vault.cleanup_stale_temp(cfg.vault)
    index = Index.open(db, create=True)
    try:
        # 增量模式绝不能 reset：`files` 表就是比较基线，清掉等于把全库重新读一遍
        if full:
            index.reset()
        result = index_vault(cfg, index, full=full)
    finally:
        index.close()
    return {"db": str(db), "cleaned_temp": len(cleaned), **result}


def open_index(cfg: Config) -> Index:
    cfg.ensure_dirs()
    return Index.open(cfg.index_db, create=True)


def ensure_index(cfg: Config, refresh: bool = True) -> Index:
    """索引缺失或为空时自动重建；随后按 mtime/size 增量补上改动过的文件。

    为什么要「自动补」：正文与档案会从 Obsidian、编辑器、git 直接改，CLI 未必知道。
    以前索引一旦建成就是死快照，搜到的可能是昨天的内容。现在每次打开索引先做一次
    增量刷新——`os.stat` 在本地近乎免费，800 个文件约 10 毫秒，只有真改过的才重读。
    """
    index = open_index(cfg)
    if index.stats()["entities"] == 0:
        index.close()
        rebuild(cfg)
        index = open_index(cfg)
    elif refresh:
        try:
            index_vault(cfg, index, full=False)
        except Exception:  # noqa: BLE001 - 刷新失败不该让整条命令打不开
            index.conn.rollback()
    return index
