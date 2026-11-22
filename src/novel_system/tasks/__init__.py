"""任务记录：Agent 交接的最小单元（schemas/task.schema.json）。

落在 Runtime，不进 Vault——任务是运行态，不是作品内容。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import ConflictError, UsageError
from ..storage import dump_yaml, write_atomic

# V1.0 §9.2 的前缀是 `task_`（小写、下划线、六位）。
#
# 2026-11-22 全库重排时漏了这一支：重编号脚本只扫 Vault，而任务落在 Runtime。
# 于是 Vault 里 1046 份文档是 `ent_000001`，runtime 里新发的任务却是
# `TASK-000001`——两套方言并存，而 §9.2 明列 `task_`。当时真库 `tasks/` 是空的，
# 所以换形态没有数据迁移成本，只改代码面。
#
# **旧形态仍然读得懂**（`_LEGACY_TASK_RE`）：旧的 runtime 目录不该因为这次改名
# 突然变成「任务不存在」。认得出旧号、不再发旧号，这条与 `ids.LEGACY_PREFIXES` 同一思路。
_TASK_RE = re.compile(r"^task_(\d{6})$")
_LEGACY_TASK_RE = re.compile(r"^TASK-(\d{6})$")


def normalize_task_id(raw: str) -> str:
    """把用户/旧代码给的 `TASK-000001` 归一成 `task_000001`；认不出的原样返回。"""
    text = (raw or "").strip()
    m = _LEGACY_TASK_RE.match(text)
    return f"task_{m.group(1)}" if m else text


def is_task_id(raw: str) -> bool:
    text = (raw or "").strip()
    return bool(_TASK_RE.match(text) or _LEGACY_TASK_RE.match(text))


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

# V1.0 §10.4 的任务状态机：
#
#   queued → ready → running → waiting_review → approved / rejected → completed
#   异常：blocked、paused、failed、cancelled
#
# 与 V4.0 的六态（pending/in_progress/awaiting_author/done/blocked/cancelled）不是同一套。
# 换语汇的理由不是「新版本看着顺眼」，而是旧六态缺了三样 §10 明确要求的东西：
#   - 没有 ready：pending 与 in_progress 之间没有「材料齐了、可以动手」这个位置，
#     于是「分配给人」和「人真开始写」只能靠口头同步，队列里看不出差别。
#   - 没有 approved/rejected：审核结论只能塞进 approval_state 字段，而那是「作者批准」
#     不是「编辑审核通过」——§10 里这是两道不同的关。
#   - 没有 paused/failed：中断与失败只能记成 blocked，而 blocked 的语义是「等外部条件」；
#     把「机器挂了」记成「等作者拍板」会让阻塞统计彻底失真，也让重试策略无从下手。
#
# **旧态仍然读得懂**（LEGACY_STATUS），与 `TASK-000001` 旧号同一思路：认得出、不再写。
LEGACY_STATUS = {
    "pending": "queued",
    "in_progress": "running",
    "awaiting_author": "waiting_review",
    "done": "completed",
}

# 顺序即 §10.4 的正流顺序；CLI 的 --status 选项与状态表都按它排。
TASK_STATUSES = (
    "queued",
    "ready",
    "running",
    "waiting_review",
    "approved",
    "rejected",
    "completed",
    "blocked",
    "paused",
    "failed",
    "cancelled",
)

TERMINAL_STATUSES = ("completed", "cancelled")

WRITE_MODES = ("read_only", "staging", "draft", "proposal")

APPROVAL_STATES = ("not_required", "pending_author", "approved", "rejected")

# 非顺流迁移：这些方向意味着「事情没按计划走」，必须写理由。
# 顺流（ready→running→approved→completed）不强制，否则每次正常推进都要编一句话，
# 理由字段很快就全是「继续」——那等于没有理由。
NEEDS_REASON = ("blocked", "paused", "failed", "rejected", "cancelled")

# 允许的状态流转。跳步一律拒绝，避免「未批准就完成」。
#
# approved 与 completed 之间刻意分成两步：approved 是「人点了头」，completed 是
# 「产物已经落地」。批准之后发布失败的任务停在 approved 才是诚实的；
# 直接给 completed 会让人以为正文已经进库了。
TRANSITIONS = {
    "queued": {"ready", "cancelled"},
    "ready": {"running", "blocked", "cancelled"},
    "running": {"waiting_review", "blocked", "paused", "failed", "cancelled"},
    "waiting_review": {"approved", "rejected", "paused", "failed", "cancelled"},
    "approved": {"completed", "cancelled"},
    # 打回不是取消：重做要回到可执行位置，而不是重新排队。
    "rejected": {"ready", "cancelled"},
    # 阻塞解除后可能直接续跑，也可能要重新准备材料——两个入口都留。
    "blocked": {"ready", "running", "cancelled"},
    # 暂停解除时回到被打断的那一步。
    "paused": {"running", "waiting_review", "cancelled"},
    # 失败可以重试（回 ready），也可以放弃。
    "failed": {"ready", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


def normalize_status(raw: str) -> str:
    """把旧态归一成 §10.4 的新态；认不出的原样返回（由调用方报错，不在这里猜）。"""
    text = (raw or "").strip()
    return LEGACY_STATUS.get(text, text)


@dataclass
class Task:
    task_id: str
    project_id: str
    task_type: str
    author_id: str = ""
    status: str = "queued"
    assigned_to: str = ""
    input_files: list[str] = field(default_factory=list)
    output_files: list[str] = field(default_factory=list)
    output_scope: str = ""
    write_mode: str = "staging"
    approval_state: str = "pending_author"
    created_at: str = ""
    closed_at: str = ""
    notes: str = ""
    # V1.0 §10.4：「每次状态迁移必须记录操作者、时间、原因和关联任务。」
    # 只存一个当前 status 的话，「谁把 running 改成 completed 的」永远查不到——
    # 而任务一旦跨 Agent 交接，这正是唯一能追责的线索。
    status_history: list[dict] = field(default_factory=list)

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
        if self.status_history:
            data["status_history"] = [dict(h) for h in self.status_history]
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

        raw_history = data.get("status_history")
        history = [dict(h) for h in raw_history if isinstance(h, dict)] if isinstance(raw_history, list) else []

        return cls(
            task_id=str(data.get("task_id") or ""),
            project_id=str(data.get("project_id") or ""),
            task_type=str(data.get("task_type") or ""),
            author_id=str(data.get("author_id") or ""),
            # 读盘时归一旧态：旧 runtime 里的 pending/done 一读出来就是 queued/completed，
            # 这样后续写入不会把两套方言混在同一个文件里。
            status=normalize_status(str(data.get("status") or "queued")),
            assigned_to=str(data.get("assigned_to") or ""),
            input_files=as_list(data.get("input_files")),
            output_files=as_list(data.get("output_files")),
            output_scope=str(data.get("output_scope") or ""),
            write_mode=str(data.get("write_mode") or "staging"),
            approval_state=str(data.get("approval_state") or "pending_author"),
            created_at=str(data.get("created_at") or ""),
            closed_at=str(data.get("closed_at") or ""),
            notes=str(data.get("notes") or ""),
            status_history=history,
        )


class TaskStore:
    def __init__(self, runtime: Path):
        self.dir = runtime / "tasks"

    def _path(self, task_id: str) -> Path:
        return self.dir / f"{task_id}.yaml"

    def _paths(self, task_id: str) -> list[Path]:
        """新号路径，兼容旧 `TASK-` 号的路径（旧号不重命名，只额外认一份）。"""
        wanted = normalize_task_id(task_id)
        out = [self.dir / f"{wanted}.yaml"]
        legacy = "TASK-" + wanted.split("_")[-1] if wanted.startswith("task_") else ""
        if legacy:
            out.append(self.dir / f"{legacy}.yaml")
        return out

    def next_id(self) -> str:
        highest = 0
        if self.dir.is_dir():
            for path in list(self.dir.glob("task_*.yaml")) + list(self.dir.glob("TASK-*.yaml")):
                m = _TASK_RE.match(path.stem) or _LEGACY_TASK_RE.match(path.stem)
                if not m:
                    continue
                try:
                    highest = max(highest, int(m.group(1)))
                except ValueError:
                    continue
        return f"task_{highest + 1:06d}"

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
            status="queued",
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

        for path in self._paths(task_id):
            if path.exists():
                return Task.from_dict(cfg_mod.read_yaml(path))
        raise UsageError(f"任务不存在：{task_id}（先运行 novel task create）")

    def exists(self, task_id: str) -> bool:
        return any(path.exists() for path in self._paths(task_id))

    def list(self, project_id: str = "", status: str = "") -> list[Task]:
        from .. import config as cfg_mod

        out: list[Task] = []
        if not self.dir.is_dir():
            return out
        for path in sorted(list(self.dir.glob("task_*.yaml")) + list(self.dir.glob("TASK-*.yaml"))):
            task = Task.from_dict(cfg_mod.read_yaml(path))
            if project_id and task.project_id != project_id:
                continue
            if status and task.status != status:
                continue
            out.append(task)
        return out

    def transition(
        self,
        task: Task,
        status: str,
        actor: str = "",
        reason: str = "",
        related_task_ids: list[str] | None = None,
    ) -> Task:
        """推进任务状态，并把这次迁移记进 `status_history`。

        §10.4 要求记录四样：操作者、时间、原因、关联任务。这里四样都写。
        `reason` 只在非顺流迁移上强制（`NEEDS_REASON`）——正常推进也要编理由的话，
        理由字段很快会全是「继续」，那与不记没有区别。
        """
        target = normalize_status(status)
        if target not in TASK_STATUSES:
            raise UsageError(f"未知任务状态：{status}（可选 {', '.join(TASK_STATUSES)}）")
        if target in NEEDS_REASON and not reason.strip():
            raise UsageError(
                f"{task.task_id} → {target} 是一次非顺流迁移，必须写理由（--reason）。\n"
                "「为什么停了」比「停了」值钱：三天后回来看，只有理由能判断该重试还是该放弃。"
            )
        allowed = TRANSITIONS.get(task.status, set())
        if target not in allowed:
            raise ConflictError(
                f"任务 {task.task_id} 不能从 {task.status} 直接转到 {target}"
                f"（允许：{', '.join(sorted(allowed)) or '无'}）\n"
                "跳步会让「批准」和「完成」之间的关卡失去意义。"
            )
        from ..audit import now_iso

        at = now_iso()
        entry: dict = {"from": task.status, "to": target, "at": at}
        if actor:
            entry["by"] = actor
        if reason.strip():
            entry["reason"] = reason.strip()
        if related_task_ids:
            entry["related_task_ids"] = [str(t) for t in related_task_ids if str(t).strip()]

        task.status = target
        task.status_history.append(entry)
        if target in TERMINAL_STATUSES:
            task.closed_at = at
        self.save(task)
        return task

    def mark_published(self, task: Task, actor: str = "", release_id: str = "") -> Task:
        """发布引擎报 ACTIVE 后的收尾：无条件把任务落到 `completed`。

        这里**刻意绕过** `TRANSITIONS`。发布引擎返回 ACTIVE 时，内容已经在正式区了，
        这是既成事实；此时若因为「任务还停在 queued」而拒绝记录完成，
        状态文件就会和磁盘互相矛盾——而磁盘才是事实源。
        状态机的职责是拦住「没做就说做完了」，不是拦住「已经做了却说没记」。

        但不绕过记账：这次迁移照样进 `status_history`，理由写成「发布 ACTIVE」。
        """
        from ..audit import now_iso

        at = now_iso()
        entry: dict = {
            "from": task.status,
            "to": "completed",
            "at": at,
            "reason": f"发布 {release_id or '(未记 release_id)'} 已 ACTIVE",
        }
        if actor:
            entry["by"] = actor
        task.status = "completed"
        task.status_history.append(entry)
        task.closed_at = at
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
        if task.status_history:
            lines.append("")
            lines.append("状态轨迹：")
            for h in task.status_history:
                bits = [f"{h.get('from', '?')} → {h.get('to', '?')}"]
                if h.get("at"):
                    bits.append(str(h["at"]))
                if h.get("by"):
                    bits.append(f"by {h['by']}")
                if h.get("reason"):
                    bits.append(f"理由：{h['reason']}")
                if h.get("related_task_ids"):
                    bits.append("关联：" + ", ".join(str(x) for x in h["related_task_ids"]))
                lines.append("  - " + "  ".join(bits))
        return lines


def task_json(task: Task) -> str:
    return json.dumps(task.to_dict(), ensure_ascii=False, indent=2)
