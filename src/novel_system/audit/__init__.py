"""操作日志与操作者校验。

V4.0 §5.4：所有写入必须记录操作 ID、时间、操作者、输入、输出、结果。
日志只记元数据（路径、哈希、字节数），**不复制正文**。
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..errors import SecurityError, UsageError
from ..storage import sha256_file

AGENTS = (
    "editor_in_chief",
    "world_architect",
    "editor",
    "dice_mistress",
    "researcher",
    "writer",
    "memory_keeper",
    "ingestor",
    "version_manager",
)

AUTHOR = "author"
ACTORS = (AUTHOR,) + AGENTS

# 只有作者能做的事。CLI 执行动作，但不代替作者做决定。
AUTHOR_ONLY = {
    "approve": "批准变更",
    "publish": "发布正式内容",
    "adopt": "登记作者手改",
    "restore": "恢复备份",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_op_id() -> str:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return f"OP-{stamp}-{secrets.token_hex(2).upper()}"


def ensure_actor(raw: str | None, command: str) -> str:
    """校验操作者身份。

    Agent 专属命令必须显式声明 --as；作者专属命令只接受 author，不许代办。
    """
    actor = (raw or "").strip()
    if not actor:
        if command in AUTHOR_ONLY:
            raise SecurityError(
                f"`{command}` 只能由作者执行，请显式加 --as author"
                f"（该命令的语义是「{AUTHOR_ONLY[command]}」，Agent 不得代办）"
            )
        actor = AUTHOR
    if actor not in ACTORS:
        raise UsageError(f"未知操作者：{actor}（可选 {', '.join(ACTORS)}）")
    if command in AUTHOR_ONLY and actor != AUTHOR:
        raise SecurityError(
            f"`{command}` 只能由作者执行（{AUTHOR_ONLY[command]}），{actor} 无权代办"
        )
    return actor


@dataclass
class Operation:
    op_id: str
    at: str
    actor: str
    command: str
    project_id: str
    status: str
    inputs: list[dict] = field(default_factory=list)
    outputs: list[dict] = field(default_factory=list)
    note: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        payload = {
            "op_id": self.op_id,
            "at": self.at,
            "actor": self.actor,
            "command": self.command,
            "project_id": self.project_id,
            "status": self.status,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "note": self.note,
        }
        payload.update(self.extra)
        return payload


class OperationLog:
    """Runtime 内 JSONL 追加日志。

    Runtime 未配置时静默跳过：只读命令不该因为记不了日志而失败。
    """

    def __init__(self, runtime: Path | None):
        self.runtime = runtime
        self.path = (runtime / "logs" / "ops.jsonl") if runtime else None

    def record(
        self,
        *,
        actor: str,
        command: str,
        project_id: str = "",
        status: str = "ok",
        inputs: list[dict] | None = None,
        outputs: list[dict] | None = None,
        note: str = "",
        **extra: object,
    ) -> Operation:
        op = Operation(
            op_id=new_op_id(),
            at=now_iso(),
            actor=actor,
            command=command,
            project_id=project_id,
            status=status,
            inputs=inputs or [],
            outputs=outputs or [],
            note=note,
            extra=dict(extra),
        )
        self._append(op)
        return op

    def _append(self, op: Operation) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(op.as_dict(), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def tail(self, count: int = 20) -> list[dict]:
        if self.path is None or not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        out: list[dict] = []
        for line in lines[-count:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


def file_stamp(path: Path, *, root: Path | None = None) -> dict:
    """记录一个文件的元数据。root 给定时存相对路径，便于跨机器阅读。"""
    shown = str(path.relative_to(root).as_posix()) if root else str(path)
    if not path.exists():
        return {"path": shown, "exists": False}
    stat = path.stat()
    return {
        "path": shown,
        "exists": True,
        "sha256": sha256_file(path),
        "bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
    }
