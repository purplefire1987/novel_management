"""事务层：所有正式写入的唯一通道（PRD 10.3 / 15）。

顺序：校验 -> 备份 -> 写 staging -> 原子替换 -> 更新索引 -> 审计 -> Git。
任何一步失败都会把已替换的文件还原，并把失败原因写进审计日志。
"""

from __future__ import annotations

import json
import secrets
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import vault
from .config import Config
from .database.indexer import index_document
from .database.sqlite import Index, now_iso
from .validators.schema import validate_meta


def new_operation_id(prefix: str = "OP") -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(2)}"


@dataclass
class StagedChange:
    rel_path: str
    new_text: str
    before_hash: str = ""
    after_hash: str = ""
    backup_path: str = ""
    kind: str = "write"
    meta: dict[str, Any] = field(default_factory=dict)


class TransactionError(RuntimeError):
    pass


class Transaction:
    """一次正式写入事务。调用方负责先做业务校验，再 stage，最后 commit。"""

    def __init__(
        self,
        cfg: Config,
        index: Index,
        *,
        actor: str,
        operation: str,
        target: str,
        agent: str = "system_executor",
        validate: Callable[[StagedChange], list[Any]] | None = None,
    ):
        self.cfg = cfg
        self.index = index
        self.actor = actor
        self.agent = agent
        self.operation = operation
        self.target = target
        self.op_id = new_operation_id()
        self.changes: list[StagedChange] = []
        self.validate = validate
        self.notes: list[str] = []

    # ------------------------------------------------------------------ stage
    def stage(self, rel_path: str, text: str, kind: str = "write") -> StagedChange:
        path = self.cfg.abs(rel_path)
        change = StagedChange(
            rel_path=rel_path,
            new_text=text,
            before_hash=vault.file_hash(path),
            after_hash=vault.sha256_text(text),
            kind=kind,
        )
        if text.startswith("---"):
            meta, _ = vault.split_frontmatter(text)
            change.meta = meta
        self.changes.append(change)
        return change

    def stage_doc(self, rel_path: str, meta: dict[str, Any], body: str) -> StagedChange:
        return self.stage(rel_path, vault.render_doc(meta, body))

    # ---------------------------------------------------------------- commit
    def commit(self, message: str) -> dict[str, Any]:
        if not self.changes:
            raise TransactionError("事务没有任何变更")

        # 1) 校验
        problems: list[str] = []
        for change in self.changes:
            # 只有带 ID 的正式实体文档才走 Schema 校验；
            # 热记忆/滚动摘要这类工作文档没有 ID，也允许没有。
            if change.meta and change.meta.get("id"):
                issues = validate_meta(change.meta, self.cfg, change.rel_path)
                problems.extend(str(i) for i in issues if i.severity in ("BLOCKER", "MAJOR"))
            if self.validate:
                problems.extend(str(i) for i in self.validate(change))
        if problems:
            self._log(status="rejected", detail="; ".join(problems[:10]))
            raise TransactionError("写入前校验失败：" + "；".join(problems[:5]))

        applied: list[StagedChange] = []
        try:
            # 2) 备份 + 3) 写 staging 副本
            txn_staging = self.cfg.staging_dir / "txn" / self.op_id
            for change in self.changes:
                target = self.cfg.abs(change.rel_path)
                if target.exists():
                    backup = vault.backup_file(target, self.cfg.backup_dir, self.op_id)
                    change.backup_path = str(backup) if backup else ""
                staged = txn_staging / change.rel_path.replace("/", "__")
                staged.parent.mkdir(parents=True, exist_ok=True)
                vault.write_atomic(staged, change.new_text)
            self._write_manifest()

            # 4) 原子替换
            for change in self.changes:
                vault.write_atomic(self.cfg.abs(change.rel_path), change.new_text)
                applied.append(change)

            # 5) 更新索引
            for change in self.changes:
                index_document(self.cfg, self.index, self.cfg.abs(change.rel_path))
            self.index.commit()

            # 6) 审计
            audit_row = self._log(status="success", detail=f"{len(self.changes)} 个文件")

            # 7) Git
            git_result = self._git_commit(message)
            self._append_git_note(git_result)

            return {
                "operation_id": self.op_id,
                "files": [c.rel_path for c in self.changes],
                "git": git_result,
                "audit": audit_row,
            }
        except Exception as exc:  # noqa: BLE001 - 失败必须回滚并记录
            self._rollback_applied(applied)
            self._log(status="failed", detail=f"{type(exc).__name__}: {exc}")
            raise TransactionError(f"事务 {self.op_id} 失败，已回滚：{exc}") from exc

    # ---------------------------------------------------------------- 内部
    def _write_manifest(self) -> None:
        """记录本次事务涉及的文件与备份位置，供 rollback 使用。"""
        manifest = {
            "operation_id": self.op_id,
            "operation": self.operation,
            "target": self.target,
            "actor": self.actor,
            "agent": self.agent,
            "created_at": now_iso(),
            "files": [
                {
                    "rel_path": c.rel_path,
                    "kind": c.kind,
                    "before_hash": c.before_hash,
                    "after_hash": c.after_hash,
                    "backup_path": c.backup_path,
                }
                for c in self.changes
            ],
        }
        target = self.cfg.backup_dir / self.op_id / "manifest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def _rollback_applied(self, applied: list[StagedChange]) -> None:
        for change in reversed(applied):
            target = self.cfg.abs(change.rel_path)
            if change.backup_path and Path(change.backup_path).exists():
                vault.restore_file(Path(change.backup_path), target)
            elif not change.before_hash and target.exists():
                # 事务之前文件不存在 → 回滚就是删掉它
                target.unlink()

    def _log(self, status: str, detail: str = "") -> dict[str, Any]:
        row = {
            "operation_id": self.op_id,
            "timestamp": now_iso(),
            "actor": self.actor,
            "agent": self.agent,
            "operation": self.operation,
            "target": self.target,
            "target_type": "transaction",
            "before_hash": ";".join(c.before_hash for c in self.changes)[:2000],
            "after_hash": ";".join(c.after_hash for c in self.changes)[:2000],
            "status": status,
            "detail": detail or f"{len(self.changes)} 个文件：" + ", ".join(c.rel_path for c in self.changes),
            "backup_path": str(self.cfg.backup_dir / self.op_id),
        }
        self.index.log_audit(row)
        self.index.commit()
        self._append_jsonl(row)
        return row

    def _append_jsonl(self, row: dict[str, Any]) -> None:
        self.cfg.logs_dir.mkdir(parents=True, exist_ok=True)
        path = self.cfg.logs_dir / "audit.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _git_commit(self, message: str) -> dict[str, Any]:
        if not self.cfg.system.get("transactions", {}).get("require_git", True):
            return {"status": "skipped", "reason": "配置关闭 Git"}
        try:
            inside = subprocess.run(
                ["git", "-C", str(self.cfg.vault), "rev-parse", "--is-inside-work-tree"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if inside.returncode != 0:
                return {"status": "skipped", "reason": "Vault 不是 Git 仓库"}
            subprocess.run(
                ["git", "-C", str(self.cfg.vault), "add", "-A"],
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            prefix = self.cfg.system.get("transactions", {}).get("git_commit_prefix", "novel-system")
            proc = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.cfg.vault),
                    "-c",
                    "user.name=novel-system",
                    "-c",
                    "user.email=novel-system@local",
                    "commit",
                    "-m",
                    f"{prefix}: {message} [{self.op_id}]",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode != 0:
                output = (proc.stdout + proc.stderr).strip()
                if "nothing to commit" in output:
                    return {"status": "no_changes"}
                return {"status": "failed", "detail": output[:500]}
            return {"status": "ok", "detail": (proc.stdout or "").splitlines()[0][:200]}
        except Exception as exc:  # noqa: BLE001
            return {"status": "failed", "detail": f"{type(exc).__name__}: {exc}"}

    def _append_git_note(self, result: dict[str, Any]) -> None:
        if result.get("status") in ("ok", "no_changes"):
            return
        self.notes.append(f"Git: {result}")
        path = self.cfg.logs_dir / "git_failures.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} {self.op_id} {result}\n")


def cleanup_staging(cfg: Config) -> None:
    """清理事务临时目录（不影响 staging 里的提案/草稿）。"""
    txn_dir = cfg.staging_dir / "txn"
    if txn_dir.exists():
        shutil.rmtree(txn_dir, ignore_errors=True)


def commit_artifacts(cfg: Config, message: str) -> dict[str, Any]:
    """把报告/日志这类事后生成的产物补提交，保持工作区干净。

    报告在事务之后生成，若不补提交，Vault 会一直有未提交改动。
    """
    if not cfg.system.get("transactions", {}).get("require_git", True):
        return {"status": "skipped", "reason": "配置关闭 Git"}
    try:
        inside = subprocess.run(
            ["git", "-C", str(cfg.vault), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if inside.returncode != 0:
            return {"status": "skipped", "reason": "Vault 不是 Git 仓库"}
        status = subprocess.run(
            ["git", "-C", str(cfg.vault), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if not (status.stdout or "").strip():
            return {"status": "no_changes"}
        subprocess.run(
            ["git", "-C", str(cfg.vault), "add", "-A"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(cfg.vault),
                "-c",
                "user.name=novel-system",
                "-c",
                "user.email=novel-system@local",
                "commit",
                "-m",
                f"{cfg.system.get('transactions', {}).get('git_commit_prefix', 'novel-system')}: {message}",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            return {"status": "failed", "detail": (proc.stdout + proc.stderr).strip()[:300]}
        return {"status": "ok", "detail": (proc.stdout or "").splitlines()[0][:200]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "detail": f"{type(exc).__name__}: {exc}"}


def rollback(
    cfg: Config,
    index: Index,
    op_id: str,
    *,
    force: bool = False,
    actor: str = "author",
) -> dict[str, Any]:
    """按 operation_id 回滚一次正式写入（PRD 15.2）。"""
    manifest_path = cfg.backup_dir / op_id / "manifest.json"
    if not manifest_path.exists():
        raise TransactionError(f"找不到 {op_id} 的备份清单：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = list(manifest.get("files") or [])

    conflicts: list[str] = []
    for item in files:
        target = cfg.abs(item["rel_path"])
        current = vault.file_hash(target)
        if current and item.get("after_hash") and current != item["after_hash"]:
            conflicts.append(item["rel_path"])
    if conflicts and not force:
        raise TransactionError(
            "以下文件在该操作之后又被修改，直接回滚会覆盖后续合法更新："
            + ", ".join(conflicts[:5])
            + "。确认要覆盖请加 --force。"
        )

    restored: list[str] = []
    removed: list[str] = []
    missing: list[str] = []
    for item in reversed(files):
        target = cfg.abs(item["rel_path"])
        backup = Path(item["backup_path"]) if item.get("backup_path") else None
        if backup and backup.exists():
            vault.restore_file(backup, target)
            restored.append(item["rel_path"])
        elif target.exists():
            target.unlink()
            removed.append(item["rel_path"])
        else:
            missing.append(item["rel_path"])

    from .database.rebuild import rebuild
    from .database.sqlite import Index as SqlIndex

    rebuild(cfg)

    rollback_id = new_operation_id("RB")
    detail = (
        f"rollback of {op_id}｜restored={len(restored)} removed={len(removed)}"
        f" missing={len(missing)}"
    )
    # rebuild 会重建数据库文件，这里必须用新连接写审计，否则写进已被删除的 inode
    fresh = SqlIndex(cfg.index_db)
    try:
        fresh.log_audit(
            {
                "operation_id": rollback_id,
                "timestamp": now_iso(),
                "actor": actor,
                "agent": "system",
                "operation": "rollback",
                "target": op_id,
                "target_type": "transaction",
                "before_hash": "",
                "after_hash": "",
                "status": "success",
                "detail": detail,
                "backup_path": str(cfg.backup_dir / op_id),
            }
        )
        fresh.commit()
    finally:
        fresh.close()

    log_rel = f"10_Change_Log/Rollbacks/{rollback_id}.md"
    log_meta = {
        "id": rollback_id,
        "type": "rollback_log",
        "operation_id": rollback_id,
        "rolled_back": op_id,
        "created_at": now_iso(),
    }
    log_body = "\n".join(
        [
            f"# 回滚记录｜{op_id}",
            "",
            f"- 回滚操作：`{rollback_id}`",
            f"- 原操作：`{op_id}`（{manifest.get('operation')} / {manifest.get('target')}）",
            f"- 强制：{force}",
            "",
            "## 已还原",
            *([f"- {p}" for p in restored] or ["- 无"]),
            "",
            "## 已删除（原为新建）",
            *([f"- {p}" for p in removed] or ["- 无"]),
            "",
            "## 未找到",
            *([f"- {p}" for p in missing] or ["- 无"]),
            "",
            "回滚后已执行 `rebuild-index`，SQLite 与 Markdown 重新对齐。",
        ]
    )
    vault.write_atomic(cfg.abs(log_rel), vault.render_doc(log_meta, log_body))

    return {
        "operation_id": op_id,
        "rollback_id": rollback_id,
        "restored": restored,
        "removed": removed,
        "missing": missing,
        "conflicts": conflicts,
        "log": log_rel,
    }
