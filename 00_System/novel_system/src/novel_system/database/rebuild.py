"""索引重建：清空并重建 index.sqlite（PRD 8.3 / AC-12）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Config
from .. import vault
from .indexer import index_vault
from .sqlite import Index


def rebuild(cfg: Config) -> dict[str, Any]:
    cfg.ensure_dirs()
    db = cfg.index_db
    cleaned = vault.cleanup_stale_temp(cfg.vault)
    index = Index.open(db, create=True)
    try:
        index.reset()
        result = index_vault(cfg, index)
    finally:
        index.close()
    return {"db": str(db), "cleaned_temp": len(cleaned), **result}


def open_index(cfg: Config) -> Index:
    cfg.ensure_dirs()
    return Index.open(cfg.index_db, create=True)


def ensure_index(cfg: Config) -> Index:
    """索引缺失或为空时自动重建，避免 CLI 直接报错。"""
    index = open_index(cfg)
    if index.stats()["entities"] == 0:
        index.close()
        rebuild(cfg)
        index = open_index(cfg)
    return index
