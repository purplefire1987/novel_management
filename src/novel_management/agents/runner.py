"""把「调一次模型」变成一件可校验的事。

三步，一步都不许省：

1. 按角色装配提示词（`prompts.build`）；
2. 调模型，拿到文本；
3. **校验**：文本必须能被解析成 PRD §6.3 的信封，并且过 schema。

第三步是关键。模型很擅长输出「看起来像 JSON」的东西——多一个逗号、外面裹一层
```json 围栏、把 `role` 写成中文名。直接 `json.loads` 会在第一种情况下崩、
第二种情况下崩、第三种情况下悄悄通过然后把错的角色记进档案。所以：
去围栏 → 解析 → schema 校验 → 再核对信封里的 `role` 与本次角色是否一致。

**任何一步失败都不猜。** `AgentRun.errors` 非空就是失败，调用方要么重试、
要么降级到确定性路径并在产物里写明「模型输出不可用」。这一层不提供
「尽力而为地提取字段」——那种提取出来的东西，字段名对了、语义错了，
比直接失败更难发现。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..adapters.mock import LLMClient
from novel_management.schemas.store import check as schema_check
from . import prompts, registry
from .metering import Meter

ENVELOPE_SCHEMA = "agent_envelope"

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def extract_json(text: str) -> dict | None:
    """从模型输出里取出 JSON 对象。取不到返回 None——不抛，交给调用方记 issue。"""
    raw = (text or "").strip()
    if not raw:
        return None
    fenced = _FENCE.match(raw)
    if fenced:
        raw = fenced.group(1).strip()
    if not raw.startswith("{"):
        # 允许前面有一段解释文字：从第一个 `{` 到最后一个 `}`。
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


@dataclass
class AgentRun:
    role: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    envelope: dict = field(default_factory=dict)
    raw: str = ""
    errors: tuple[str, ...] = ()
    usage: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.envelope)

    def result(self) -> dict:
        """信封里的 `result`。失败时返回空字典，**不回退到部分字段**。"""
        value = self.envelope.get("result")
        return value if isinstance(value, dict) else {}

    def describe_errors(self) -> str:
        return "；".join(self.errors) if self.errors else ""


def run(
    llm: LLMClient,
    role_id: str,
    *,
    instructions: str,
    task: dict | None = None,
    release: str = "",
    anchors: dict | None = None,
    output_schema: str = ENVELOPE_SCHEMA,
    card_path: Path | None = None,
    meter: Meter | None = None,
    model: str = "",
) -> AgentRun:
    """跑一次角色调用，返回可校验的结果。"""
    role = registry.role(role_id)
    prompt = prompts.build(
        role_id,
        task=task,
        release=release,
        anchors=anchors,
        output_schema=output_schema,
        card_path=card_path,
    )
    messages = (
        {"role": "system", "content": prompt.system},
        {"role": "user", "content": instructions or "按上面的契约完成任务，只输出 JSON。"},
    )
    response = llm.complete(messages, model=model)

    run_result = AgentRun(
        role=role.id,
        model=response.model,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        raw=response.text,
    )

    if meter is not None:
        meter.model_call(
            role=role.id,
            model=response.model,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
        )

    data = extract_json(response.text)
    if data is None:
        run_result.errors = (
            "模型输出不是可解析的 JSON 对象："
            + (response.text[:120].replace("\n", " ") or "（空）"),
        )
        return run_result

    errors = list(schema_check(ENVELOPE_SCHEMA, data))
    declared = str(data.get("role") or "").strip()
    if declared and declared != role.id:
        errors.append(
            f"信封里的 role={declared!r} 与本次角色 {role.id!r} 不一致："
            "角色写错会让审核记录归错人，不能放行"
        )
    if errors:
        run_result.envelope = data
        run_result.errors = tuple(errors)
        return run_result

    run_result.envelope = data
    run_result.usage = dict(data.get("usage") or {})
    return run_result


def envelope_ok(data: Any, role_id: str = "") -> list[str]:
    """单独校验一份信封，CLI 与测试共用。"""
    errors = list(schema_check(ENVELOPE_SCHEMA, data))
    if role_id and isinstance(data, dict):
        declared = str(data.get("role") or "").strip()
        if declared and declared != role_id:
            errors.append(f"role 不一致：{declared!r} ≠ {role_id!r}")
    return errors
