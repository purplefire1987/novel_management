"""本地备份与恢复。

V4.0 §11.2 要求「复制成功 + 回读校验」才算备份完成，所以这里不是复制完就报成功：
写盘后全量重算哈希，与源逐条比对，任何一条对不上都判失败。

§11.3 要求恢复先 dry-run、不覆盖现存文件、冲突列清单——默认跳过冲突，
`--overwrite` 是显式选择，且覆盖前先把被覆盖的文件另存一份。
"""

from __future__ import annotations

import csv
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ...audit import now_iso
from ...domain.errors import ConflictError, SecurityError, UsageError
from ...infrastructure.filesystem.storage import copy_verified, human_size, iter_files, sha256_file, write_atomic

BACKUP_PREFIX = "BK-"
FILES_DIR = "files"
MANIFEST_NAME = "manifest.csv"
META_NAME = "meta.json"

#: `--prune` 的默认保留份数。取 10 是因为一次写作会话通常产生个位数备份，
#: 10 份足够覆盖「今天早上还好好的」这种回溯深度。
DEFAULT_KEEP = 10


@dataclass
class BackupEntry:
    rel: str
    sha256: str
    bytes: int


class BackupStore:
    def __init__(self, runtime: Path | None, out_dir: Path | None = None):
        self.runtime = runtime
        self.out_dir = out_dir or ((runtime / "backups") if runtime else None)

    # ---- 位置 ----
    def require_root(self) -> Path:
        if self.out_dir is None:
            raise UsageError("未配置 Runtime，也没有 --out：备份无处落盘")
        return self.out_dir

    def _new_id(self) -> str:
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        return f"{BACKUP_PREFIX}{stamp}"

    def _path(self, backup_id: str) -> Path:
        return self.require_root() / backup_id

    # ---- 创建 ----
    def create(
        self,
        vault: Path,
        *,
        label: str = "",
        include_git: bool = True,
        project_id: str = "",
    ) -> dict:
        vault = vault.resolve()
        root = self.require_root()
        backup_id = self._new_id()
        dest = root / backup_id
        if dest.exists():
            raise ConflictError(f"备份目录已存在：{dest}")

        files_root = dest / FILES_DIR
        files_root.mkdir(parents=True, exist_ok=True)
        skip = set() if include_git else {".git"}

        entries: list[BackupEntry] = []
        for source in iter_files(vault, skip_dirs=skip, include_git=include_git):
            rel = source.relative_to(vault).as_posix()
            digest, size = copy_verified(source, files_root / rel)
            entries.append(BackupEntry(rel=rel, sha256=digest, bytes=size))

        # 校验：全量重算，逐条比对
        mismatched = []
        for entry in entries:
            copy_path = files_root / entry.rel
            if not copy_path.exists() or sha256_file(copy_path) != entry.sha256:
                mismatched.append(entry.rel)
        copied_bytes = sum(entry.bytes for entry in entries)

        verified = not mismatched
        git_saved = (files_root / ".git").exists()
        meta = {
            "backup_id": backup_id,
            "created_at": now_iso(),
            "vault": str(vault),
            "project_id": project_id,
            "label": label,
            "include_git": include_git,
            "git_saved": git_saved,
            "files": len(entries),
            "bytes": copied_bytes,
            "verified": verified,
            "mismatched": mismatched[:20],
            "schema_version": 1,
        }
        write_atomic(dest / META_NAME, json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))
        with (dest / MANIFEST_NAME).open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["path", "sha256", "bytes"])
            for entry in entries:
                writer.writerow([entry.rel, entry.sha256, entry.bytes])

        if not verified:
            raise ConflictError(
                f"备份校验未通过：{len(mismatched)} 个文件哈希不一致（前几个：{mismatched[:3]}）"
                f"备份保留在 {dest}，请勿删除，用于排查"
            )
        meta["path"] = str(dest)
        return meta

    # ---- 读取 ----
    def list(self) -> list[dict]:
        root = self.out_dir
        if root is None or not root.is_dir():
            return []
        out: list[dict] = []
        for path in sorted(root.glob(f"{BACKUP_PREFIX}*")):
            meta_path = path / META_NAME
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            meta["path"] = str(path)
            out.append(meta)
        return out

    # ---- 保留策略 ----
    def prune(self, *, keep: int = DEFAULT_KEEP, dry_run: bool = True) -> dict:
        """按创建时间保留最近 `keep` 份，其余列为可删。

        **只碰 `BK-*`。** 人工建的备份（比如 `maps-prov-39-20261123-010652`）
        命名不带这个前缀，因此永远不在清理范围内——自动删掉人亲手建的备份，
        是这套工具最不该做的事。

        默认 `dry_run`：备份的唯一价值是「还在」，清理必须是一次显式决定。
        """
        if keep < 1:
            raise UsageError("--keep 至少为 1：一份都不留，restore 就没有对象了")
        root = self.require_root()

        items: list[dict] = []
        for path in root.glob(f"{BACKUP_PREFIX}*"):
            if not path.is_dir():
                continue
            created, size, files = "", 0, 0
            meta_path = path / META_NAME
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    created = str(meta.get("created_at") or "")
                    size = int(meta.get("bytes") or 0)
                    files = int(meta.get("files") or 0)
                except (json.JSONDecodeError, OSError, ValueError):
                    pass
            # 排序一律用目录 mtime：meta.created_at 是带时区的字符串，
            # 混了时区做字典序会排错，而「哪份更新」不能排错。
            mtime = path.stat().st_mtime
            items.append(
                {
                    "backup_id": path.name,
                    "path": str(path),
                    "created_at": created,
                    "mtime": mtime,
                    "bytes": size,
                    "files": files,
                }
            )

        items.sort(key=lambda item: item["mtime"], reverse=True)
        kept, removable = items[:keep], items[keep:]
        freed = sum(int(item["bytes"]) for item in removable)

        if not dry_run:
            for item in removable:
                shutil.rmtree(item["path"])

        return {
            "root": str(root),
            "total": len(items),
            "kept": kept,
            "removable": removable,
            "freed_bytes": freed,
            "dry_run": dry_run,
        }

    def load(self, backup_id: str) -> dict:
        dest = self._path(backup_id)
        meta_path = dest / META_NAME
        if not meta_path.exists():
            raise UsageError(f"备份不存在：{backup_id}（用 novel backup --list 查看）")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["path"] = str(dest)
        return meta

    def manifest(self, backup_id: str) -> list[BackupEntry]:
        path = self._path(backup_id) / MANIFEST_NAME
        if not path.exists():
            raise UsageError(f"备份缺清单：{path}")
        out: list[BackupEntry] = []
        with path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                out.append(
                    BackupEntry(rel=row["path"], sha256=row["sha256"], bytes=int(row.get("bytes") or 0))
                )
        return out

    # ---- 恢复 ----
    def plan_restore(self, backup_id: str, vault: Path, *, verify_hashes: bool = True) -> dict:
        vault = vault.resolve()
        entries = self.manifest(backup_id)
        files_root = self._path(backup_id) / FILES_DIR
        new: list[BackupEntry] = []
        same: list[BackupEntry] = []
        conflict: list[BackupEntry] = []
        broken: list[BackupEntry] = []

        for entry in entries:
            rel = Path(entry.rel)
            if rel.is_absolute() or ".." in rel.parts:
                raise SecurityError(f"备份清单含越界路径，拒绝恢复：{entry.rel}")
            target = (vault / rel).resolve()
            if vault not in target.parents and target != vault:
                raise SecurityError(f"恢复目标越界：{target}")
            source = files_root / entry.rel
            if verify_hashes:
                if not source.exists() or sha256_file(source) != entry.sha256:
                    broken.append(entry)
                    continue
            if not target.exists():
                new.append(entry)
            elif sha256_file(target) == entry.sha256:
                same.append(entry)
            else:
                conflict.append(entry)

        return {
            "backup_id": backup_id,
            "vault": str(vault),
            "total": len(entries),
            "new": new,
            "same": same,
            "conflict": conflict,
            "broken": broken,
        }

    def restore(self, backup_id: str, vault: Path, *, overwrite: bool = False) -> dict:
        plan = self.plan_restore(backup_id, vault)
        if plan["broken"]:
            raise ConflictError(
                f"备份内 {len(plan['broken'])} 个文件校验不通过，拒绝恢复："
                f"{[e.rel for e in plan['broken'][:3]]}"
            )
        vault = vault.resolve()
        files_root = self._path(backup_id) / FILES_DIR
        undo_dir = (self.runtime / "restore-undo" / f"{backup_id}-{datetime.now().strftime('%H%M%S')}") if self.runtime else None

        written: list[str] = []
        saved: list[str] = []
        skipped: list[str] = []

        for entry in plan["new"]:
            target = vault / entry.rel
            target.parent.mkdir(parents=True, exist_ok=True)
            copy_verified(files_root / entry.rel, target)
            written.append(entry.rel)

        for entry in plan["conflict"]:
            if not overwrite:
                skipped.append(entry.rel)
                continue
            target = vault / entry.rel
            if undo_dir is not None and target.exists():
                copy_verified(target, undo_dir / entry.rel)
                saved.append(entry.rel)
            copy_verified(files_root / entry.rel, target)
            written.append(entry.rel)

        return {
            "backup_id": backup_id,
            "written": written,
            "skipped": skipped,
            "saved_before_overwrite": saved,
            "undo_dir": str(undo_dir) if saved and undo_dir else "",
            "unchanged": len(plan["same"]),
        }

    def describe(self, meta: dict) -> list[str]:
        lines = [
            f"backup_id : {meta.get('backup_id', '')}",
            f"created_at: {meta.get('created_at', '')}",
            f"vault     : {meta.get('vault', '')}",
            f"files     : {meta.get('files', 0)}",
            f"size      : {human_size(int(meta.get('bytes') or 0))}",
            f"verified  : {meta.get('verified')}",
        ]
        if meta.get("label"):
            lines.append(f"label     : {meta['label']}")
        lines.append(f"path      : {meta.get('path', '')}")
        return lines
