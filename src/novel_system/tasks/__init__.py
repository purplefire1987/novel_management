"""任务记录：Agent 交接的最小单元（schemas/task.schema.json）。

落在 Runtime，不进 Vault——任务是运行态，不是作品内容。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ConflictError, UsageError
from ..storage import dump_yaml, write_atomic

TASK_TYPES = (
    "outline",
    "preflight",
    "draft",
    "revision",
    "review",
    "memory_update",
    "ingest",
    "simulation",
    "release",
)

TASK_STATUSES = ("pending", "in_progress", "blocked", "awaiting_author", "done", "cancelled")

WRITE_MODES = ("read_only", "staging", "draft", "proposal")

APPROVAL_STATES = ("not_required", "pending_author", "approved", "rejected")

# 允许的状态流转。跳步一律拒绝，避免「未批准就完成」。
TRANSITIONS = {
    "pending": {"in_progress", "cancelled", "blocked"},
    "in_progress": {"blocked", "awaiting_author", "done", "cancelled"},
    "blocked": {"in_progress", "cancelled"},
    "awaiting_author": {"in_progress", "done", "cancelled"},
    "done": set(),
    "cancelled": set(),
}


@dataclass
class Task:
    task_id: str
    project_id: str
    task_type: str
    author_id: str = ""
    status: str = "pending"
    assigned_to: str = ""
    input_files: list[str] = field(default_factory=list)
    output_files: list[str] = field(default_factory=list)
    output_scope: str = ""
    write_mode: str = "staging"
    approval_state: str = "pending_author"
    created_at: str = ""
    closed_at: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        data: dict = {
            "task_id": self.task_id,
            "project_id": self.project_id,
            "author_id": self.author_id,
            "task_type": self.task_type,
            "status": self.status,
        }
        if self.assigned_to:
            data["assigned_to"] = self.assigned_to
        if self.input_files:
            data["input_files"] = list(self.input_files)
        if self.output_files:
            data["output_files"] = list(self.output_files)
        if self.output_scope:
            data["output_scope"] = self.output_scope
        data["write_mode"] = self.write_mode
        data["approval_state"] = self.approval_state
        data["created_at"] = self.created_at
        if self.closed_at:
            data["closed_at"] = self.closed_at
        if self.notes:
            data["notes"] = self.notes
        return data

    def to_yaml(self) -> str:
        header = f"# 任务记录 {self.task_id}（Runtime 内运行态，不是作品内容）\n"
        return header + dump_yaml(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        def as_list(value: object) -> list[str]:
            if isinstance(value, list):
                return [str(v) for v in value]
            return [str(value)] if value else []

        return cls(
            task_id=str(data.get("task_id") or ""),
            project_id=str(data.get("project_id") or ""),
            task_type=str(data.get("task_type") or ""),
            author_id=str(data.get("author_id") or ""),
            status=str(data.get("status") or "pending"),
            assigned_to=str(data.get("assigned_to") or ""),
            input_files=as_list(data.get("input_files")),
            output_files=as_list(data.get("output_files")),
            output_scope=str(data.get("output_scope") or ""),
            write_mode=str(data.get("write_mode") or "staging"),
            approval_state=str(data.get("approval_state") or "pending_author"),
            created_at=str(data.get("created_at") or ""),
            closed_at=str(data.get("closed_at") or ""),
            notes=str(data.get("notes") or ""),
        )


class TaskStore:
    def __init__(self, runtime: Path):
        self.dir = runtime / "tasks"

    def _path(self, task_id: str) -> Path:
        return self.dir / f"{task_id}.yaml"

    def next_id(self) -> str:
        highest = 0
        if self.dir.is_dir():
            for path in self.dir.glob("TASK-*.yaml"):
                try:
                    highest = max(highest, int(path.stem.split("-")[1]))
                except (IndexError, ValueError):
                    continue
        return f"TASK-{highest + 1:06d}"

    def create(
        self,
        *,
        project_id: str,
        task_type: str,
        author_id: str = "author-local",
        assigned_to: str = "",
        write_mode: str = "staging",
        output_scope: str = "",
        input_files: list[str] | None = None,
        notes: str = "",
        created_at: str = "",
    ) -> Task:
        if task_type not in TASK_TYPES:
            raise UsageError(f"未知任务类型：{task_type}（可选 {', '.join(TASK_TYPES)}）")
        if write_mode not in WRITE_MODES:
            raise UsageError(f"未知写入模式：{write_mode}（可选 {', '.join(WRITE_MODES)}）")
        if not created_at:
            from ..audit import now_iso

            created_at = now_iso()

        task = Task(
            task_id=self.next_id(),
            project_id=project_id,
            task_type=task_type,
            author_id=author_id,
            status="pending",
            assigned_to=assigned_to,
            input_files=list(input_files or []),
            output_scope=output_scope,
            write_mode=write_mode,
            approval_state="not_required" if write_mode == "read_only" else "pending_author",
            created_at=created_at,
            notes=notes,
        )
        self.save(task)
        return task

    def save(self, task: Task) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(task.task_id)
        write_atomic(path, task.to_yaml().encode("utf-8"))
        return path

    def load(self, task_id: str) -> Task:
        from .. import config as cfg_mod

        path = self._path(task_id)
        if not path.exists():
            raise UsageError(f"任务不存在：{task_id}（先运行 novel task create）")
        return Task.from_dict(cfg_mod.read_yaml(path))

    def exists(self, task_id: str) -> bool:
        return self._path(task_id).exists()

    def list(self, project_id: str = "", status: str = "") -> list[Task]:
        from .. import config as cfg_mod

        out: list[Task] = []
        if not self.dir.is_dir():
            return out
        for path in sorted(self.dir.glob("TASK-*.yaml")):
            task = Task.from_dict(cfg_mod.read_yaml(path))
            if project_id and task.project_id != project_id:
                continue
            if status and task.status != status:
                continue
            out.append(task)
        return out

    def transition(self, task: Task, status: str) -> Task:
        if status not in TASK_STATUSES:
            raise UsageError(f"未知任务状态：{status}（可选 {', '.join(TASK_STATUSES)}）")
        allowed = TRANSITIONS.get(task.status, set())
        if status not in allowed:
            raise ConflictError(
                f"任务 {task.task_id} 不能从 {task.status} 直接转到 {status}"
                f"（允许：{', '.join(sorted(allowed)) or '无'}）"
            )
        from ..audit import now_iso

        task.status = status
        if status in ("done", "cancelled"):
            task.closed_at = now_iso()
        self.save(task)
        return task

    def describe(self, task: Task) -> list[str]:
        lines = [
            f"task_id      : {task.task_id}",
            f"project_id   : {task.project_id}",
            f"task_type    : {task.task_type}",
            f"status       : {task.status}",
            f"assigned_to  : {task.assigned_to or '(未指定)'}",
            f"write_mode   : {task.write_mode}",
            f"approval     : {task.approval_state}",
            f"created_at   : {task.created_at}",
        ]
        if task.closed_at:
            lines.append(f"closed_at    : {task.closed_at}")
        if task.output_scope:
            lines.append(f"output_scope : {task.output_scope}")
        if task.input_files:
            lines.append("input_files  :")
            lines.extend(f"  - {p}" for p in task.input_files)
        if task.output_files:
            lines.append("output_files :")
            lines.extend(f"  - {p}" for p in task.output_files)
        if task.notes:
            lines.append(f"notes        : {task.notes}")
        return lines


def task_json(task: Task) -> str:
    return json.dumps(task.to_dict(), ensure_ascii=False, indent=2)
