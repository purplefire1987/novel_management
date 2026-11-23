"""调用计量：每一次模型调用、每一次工具调用，都要留下可对账的一行。

为什么不能只记在内存里：T08 的验收之外还有个现实问题——**成本看不见就没法管**。
一次 preflight 用几次模型、每个角色花多少、工具被调了几次，这三件事必须落成文件，
否则「这个月为什么这么贵」永远答不上来。

落点：`<runtime>/usage/YYYYMMDD.jsonl`，一行一次调用。只追加，不改写。
另一个落点：`usage` 摘要跟着资料包一起走（PRD §6.3 信封里有 `usage` 字段），
这样「这一包是怎么算出来的」能就地回答，不用去翻日志。

**不记录提示词正文与模型输出正文。** 日志里存正文等于把作品抄进 runtime，
与 `audit` 层「只记元数据」的口径一致（V4.0 §5.4）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..infrastructure.filesystem.storage import write_text_atomic

OP_MODEL = "model"
OP_TOOL = "tool"

# 账本按天切。一天一个文件，出问题只影响一天的账。
USAGE_DIR = "usage"


def usage_dir(runtime: Path) -> Path:
    return Path(runtime) / USAGE_DIR


def usage_path(runtime: Path, day: str = "") -> Path:
    stamp = day or datetime.now().astimezone().strftime("%Y%m%d")
    return usage_dir(runtime) / f"{stamp}.jsonl"


@dataclass(frozen=True)
class Entry:
    op: str
    role: str = ""
    name: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ok: bool = True
    note: str = ""
    at: str = ""

    def as_dict(self) -> dict:
        return {
            "op": self.op,
            "role": self.role,
            "name": self.name,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "ok": self.ok,
            "note": self.note,
            "at": self.at or _now(),
        }


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Meter:
    """一次任务内的账本。`flush()` 之前只在内存，flush 之后才落盘。"""

    runtime: Path | None = None
    entries: list[Entry] = field(default_factory=list)

    def record(
        self,
        op: str,
        *,
        role: str = "",
        name: str = "",
        model: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        ok: bool = True,
        note: str = "",
    ) -> Entry:
        entry = Entry(
            op=op,
            role=role,
            name=name,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            ok=ok,
            note=note,
            at=_now(),
        )
        self.entries.append(entry)
        return entry

    def model_call(self, *, role: str, model: str, prompt_tokens: int, completion_tokens: int, ok: bool = True, note: str = "") -> Entry:
        return self.record(
            OP_MODEL,
            role=role,
            name=model,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            ok=ok,
            note=note,
        )

    def tool_call(self, *, role: str, name: str, ok: bool = True, tokens: int = 0, note: str = "") -> Entry:
        return self.record(
            OP_TOOL,
            role=role,
            name=name,
            prompt_tokens=tokens,
            ok=ok,
            note=note,
        )

    @property
    def model_calls(self) -> int:
        return sum(1 for e in self.entries if e.op == OP_MODEL)

    @property
    def tool_calls(self) -> int:
        return sum(1 for e in self.entries if e.op == OP_TOOL)

    def summary(self) -> dict:
        """给资料包与 CLI 用的 `usage` 摘要。"""
        by_role: dict[str, dict[str, int]] = {}
        by_tool: dict[str, int] = {}
        for entry in self.entries:
            if entry.role:
                slot = by_role.setdefault(entry.role, {"model_calls": 0, "tool_calls": 0})
                if entry.op == OP_MODEL:
                    slot["model_calls"] += 1
                else:
                    slot["tool_calls"] += 1
            if entry.op == OP_TOOL:
                by_tool[entry.name] = by_tool.get(entry.name, 0) + 1
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "prompt_tokens": sum(e.prompt_tokens for e in self.entries if e.op == OP_MODEL),
            "completion_tokens": sum(
                e.completion_tokens for e in self.entries if e.op == OP_MODEL
            ),
            "by_role": by_role,
            "by_tool": by_tool,
            "failed": sum(1 for e in self.entries if not e.ok),
        }

    def flush(self, runtime: Path | None = None) -> Path | None:
        """把账本追加到当天文件。没有 runtime 就不落盘（纯内存用法，测试用）。"""
        root = runtime or self.runtime
        if root is None or not self.entries:
            return None
        path = usage_path(root)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        lines = [json.dumps(entry.as_dict(), ensure_ascii=False, sort_keys=True) for entry in self.entries]
        write_text_atomic(path, existing + "\n".join(lines) + "\n")
        return path


def read_usage(runtime: Path, day: str = "") -> list[dict]:
    """读某天的账。没有就返回空表——「没有调用」和「读不出来」不一样，
    后者要报错，前者是正常状态。
    """
    path = usage_path(runtime, day)
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def aggregate(rows: list[dict]) -> dict:
    """把若干天的账合成一份摘要。"""
    meter = Meter()
    for row in rows:
        meter.record(
            str(row.get("op") or ""),
            role=str(row.get("role") or ""),
            name=str(row.get("name") or ""),
            model=str(row.get("model") or ""),
            prompt_tokens=int(row.get("prompt_tokens") or 0),
            completion_tokens=int(row.get("completion_tokens") or 0),
            ok=bool(row.get("ok", True)),
            note=str(row.get("note") or ""),
        )
    return meter.summary()
