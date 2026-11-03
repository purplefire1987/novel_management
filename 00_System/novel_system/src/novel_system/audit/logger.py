"""审计：操作记录与变更日志（PRD 15.1）。"""

from __future__ import annotations

import json
from typing import Any

from ..config import Config
from ..database.sqlite import Index, now_iso
from ..transaction import new_operation_id


class AuditLog:
    """非文件事务类操作（写报告、审核、预检）也必须有审计记录。"""

    def __init__(self, cfg: Config, index: Index):
        self.cfg = cfg
        self.index = index

    def record(
        self,
        *,
        operation: str,
        target: str,
        actor: str = "system",
        agent: str = "system",
        status: str = "success",
        detail: str = "",
        before_hash: str = "",
        after_hash: str = "",
        operation_id: str | None = None,
    ) -> str:
        op_id = operation_id or new_operation_id()
        row = {
            "operation_id": op_id,
            "timestamp": now_iso(),
            "actor": actor,
            "agent": agent,
            "operation": operation,
            "target": target,
            "target_type": "report" if operation.startswith("report") else "workflow",
            "before_hash": before_hash,
            "after_hash": after_hash,
            "status": status,
            "detail": detail,
            "backup_path": "",
        }
        self.index.log_audit(row)
        self.index.commit()
        self.cfg.logs_dir.mkdir(parents=True, exist_ok=True)
        with (self.cfg.logs_dir / "audit.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return op_id

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.index.recent_audits(limit)

    def find(self, op_id: str) -> dict[str, Any] | None:
        return self.index.get_audit(op_id)
