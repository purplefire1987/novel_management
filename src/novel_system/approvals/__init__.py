"""批准记录。批准绑定「文件 + 内容指纹」，不是一句口头同意。

publish 时重新计算 staging 文件哈希并与批准记录比对：
批准后被换过的内容，一律不算批准过。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..audit import AUTHOR, now_iso
from ..errors import ApprovalError, UsageError
from ..storage import write_atomic


@dataclass
class ApprovalFile:
    target: str          # 项目内正式相对路径，如 01_Canon/Characters/CHAR-0001.md
    staged: str          # Vault 内相对路径，指向 staging 产物
    sha256: str
    bytes: int = 0

    def as_dict(self) -> dict:
        return {"target": self.target, "staged": self.staged, "sha256": self.sha256, "bytes": self.bytes}


@dataclass
class Approval:
    approval_id: str
    task_id: str
    project_id: str
    approver: str
    approved_at: str
    decision: str = "approved"
    note: str = ""
    files: list[ApprovalFile] = field(default_factory=list)
    # T05：批准「精确对象」而不只是「某个任务」。
    # target_type 取 artifact / proposal / release / change；target_hash 是批准时看到的那个哈希。
    target_type: str = ""
    target_id: str = ""
    target_hash: str = ""
    approved_scope: list[str] = field(default_factory=list)
    change_id: str = ""

    def as_dict(self) -> dict:
        return {
            "approval_id": self.approval_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "approver": self.approver,
            "approved_at": self.approved_at,
            "decision": self.decision,
            "note": self.note,
            "files": [f.as_dict() for f in self.files],
            "target_type": self.target_type,
            "target_id": self.target_id,
            "target_hash": self.target_hash,
            "approved_scope": list(self.approved_scope),
            "change_id": self.change_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Approval":
        raw_files = data.get("files") or []
        files = [
            ApprovalFile(
                target=str(item.get("target") or ""),
                staged=str(item.get("staged") or ""),
                sha256=str(item.get("sha256") or ""),
                bytes=int(item.get("bytes") or 0),
            )
            for item in raw_files
            if isinstance(item, dict)
        ]
        return cls(
            approval_id=str(data.get("approval_id") or ""),
            task_id=str(data.get("task_id") or ""),
            project_id=str(data.get("project_id") or ""),
            approver=str(data.get("approver") or ""),
            approved_at=str(data.get("approved_at") or ""),
            decision=str(data.get("decision") or "approved"),
            note=str(data.get("note") or ""),
            files=files,
            target_type=str(data.get("target_type") or ""),
            target_id=str(data.get("target_id") or ""),
            target_hash=str(data.get("target_hash") or ""),
            approved_scope=[str(x) for x in (data.get("approved_scope") or [])],
            change_id=str(data.get("change_id") or ""),
        )


class ApprovalStore:
    def __init__(self, runtime: Path):
        self.dir = runtime / "approvals"

    def _path(self, approval_id: str) -> Path:
        return self.dir / f"{approval_id}.json"

    def next_id(self) -> str:
        highest = 0
        if self.dir.is_dir():
            for path in self.dir.glob("APR-*.json"):
                try:
                    highest = max(highest, int(path.stem.split("-")[1]))
                except (IndexError, ValueError):
                    continue
        return f"APR-{highest + 1:06d}"

    def record(
        self,
        *,
        task_id: str,
        project_id: str,
        files: list[ApprovalFile],
        note: str = "",
        approver: str = AUTHOR,
        decision: str = "approved",
        target_type: str = "",
        target_id: str = "",
        target_hash: str = "",
        approved_scope: list[str] | None = None,
        change_id: str = "",
    ) -> Approval:
        if approver != AUTHOR:
            raise ApprovalError(f"批准主体只能是作者，收到：{approver}")
        if not files and decision == "approved" and target_type not in ("release", "change"):
            # 发布与变更批准绑的是「计划/影响报告的哈希」，不绑具体文件；
            # 其余批准必须落在文件上，否则「批准了什么」无从核验。
            raise UsageError("批准记录必须至少绑定一个文件")
        approval = Approval(
            approval_id=self.next_id(),
            task_id=task_id,
            project_id=project_id,
            approver=approver,
            approved_at=now_iso(),
            decision=decision,
            note=note,
            files=files,
            target_type=target_type,
            target_id=target_id,
            target_hash=target_hash,
            approved_scope=list(approved_scope or []),
            change_id=change_id,
        )
        self.dir.mkdir(parents=True, exist_ok=True)
        write_atomic(
            self._path(approval.approval_id),
            json.dumps(approval.as_dict(), ensure_ascii=False, indent=2).encode("utf-8"),
        )
        return approval

    def load(self, approval_id: str) -> Approval:
        path = self._path(approval_id)
        if not path.exists():
            raise UsageError(f"批准记录不存在：{approval_id}")
        return Approval.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def all(self) -> list[Approval]:
        if not self.dir.is_dir():
            return []
        out = []
        for path in sorted(self.dir.glob("APR-*.json")):
            try:
                out.append(Approval.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, OSError):
                continue
        return out

    def latest_for(self, task_id: str) -> Approval | None:
        """任务最近一次有效批准。驳回记录不返回。"""
        candidates = [a for a in self.all() if a.task_id == task_id and a.decision == "approved"]
        return candidates[-1] if candidates else None

    def status_for(self, task_id: str) -> str:
        approvals = [a for a in self.all() if a.task_id == task_id]
        if not approvals:
            return "pending_author"
        return approvals[-1].decision

    # ---- T05：按对象查、按当前字节复核 ----
    def for_target(self, target_type: str, target_id: str) -> list[Approval]:
        """某个对象历次批准（按时间正序）；最后一笔有效即当前批准。"""
        return [
            a
            for a in self.all()
            if a.target_type == target_type and a.target_id == target_id and a.decision == "approved"
        ]

    @staticmethod
    def verify(approval: Approval, current: dict[str, str]) -> tuple[str, list[str]]:
        """按**当前字节**复核批准是否还有效。

        `current` 是 {target: 现在的 sha256}。返回 (VALID|STALE, 失效的文件清单)。
        **批准后改一个字节就失效**（PRD §14.5）——这里不做「非实质修改」的判断，
        MVP 不实现含义识别，白名单留到 P1。
        """
        stale: list[str] = []
        for record in approval.files:
            now = current.get(record.target)
            if now is None or now != record.sha256:
                stale.append(record.target)
        if approval.target_hash and approval.target_type in ("release", "change"):
            now = current.get(approval.target_id)
            if now is not None and now != approval.target_hash:
                stale.append(approval.target_id)
        return ("VALID" if not stale else "STALE"), stale
