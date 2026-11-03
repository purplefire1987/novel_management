"""Agent 基类与 LLM 适配层。

设计约束：
- 只从这里调 LLM，方便统一替换模型与服务商；
- 没有 API Key 时显式降级为 offline 模式，绝不假装是模型输出；
- 所有 Agent 产出都是提案/报告，落盘由 Python 执行器负责。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from ..database.sqlite import Index

JSON_BLOCK_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


class LLMError(RuntimeError):
    pass


@dataclass
class AgentResult:
    agent: str
    status: str
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "offline"
    note: str = ""

    @property
    def is_llm(self) -> bool:
        return self.mode == "llm"


class LLMClient:
    def __init__(self, cfg: Config):
        conf = cfg.llm
        self.base_url = (os.environ.get("NOVEL_LLM_BASE_URL") or conf.get("base_url") or "").rstrip("/")
        self.model = os.environ.get("NOVEL_LLM_MODEL") or conf.get("model") or ""
        self.api_key = os.environ.get(conf.get("api_key_env", "NOVEL_LLM_API_KEY"), "").strip()
        self.temperature = float(conf.get("temperature", 0.7))
        self.max_tokens = int(conf.get("max_tokens", 4096))
        self.timeout = int(conf.get("timeout_seconds", 120))
        self.offline_fallback = bool(conf.get("offline_fallback", True))

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def complete(self, system: str, user: str, temperature: float | None = None) -> str:
        if not self.available:
            raise LLMError("未配置 LLM（缺少 API Key / base_url / model）")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - 依赖外网
            body = exc.read().decode("utf-8", errors="ignore")[:500]
            raise LLMError(f"LLM HTTP {exc.code}: {body}") from exc
        except Exception as exc:  # pragma: no cover - 依赖外网
            raise LLMError(f"LLM 调用失败：{exc}") from exc

        try:
            data = json.loads(raw)
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"LLM 返回格式异常：{raw[:300]}") from exc


class BaseAgent:
    """所有 Agent 的共同外壳：读 Prompt、调模型、解析结构化块。"""

    key: str = "base"
    display: str = "Agent"

    def __init__(self, cfg: Config, index: Index | None = None):
        self.cfg = cfg
        self.index = index
        self.llm = LLMClient(cfg)

    # ------------------------------------------------------------------ Prompt
    def prompt_path(self):
        rel = self.cfg.agent_def(self.key).get("prompt")
        return self.cfg.abs(rel) if rel else None

    def system_prompt(self) -> str:
        path = self.prompt_path()
        if path and path.exists():
            from .. import vault

            doc = vault.read_doc(path)
            return (doc.body or doc.raw).strip()
        return f"你是{self.display}。遵守项目规则，不得擅自修改 Canon。"

    # ------------------------------------------------------------------ 调用
    def run_llm(self, user_prompt: str, temperature: float | None = None) -> str:
        return self.llm.complete(self.system_prompt(), user_prompt, temperature=temperature)

    def make_result(
        self,
        *,
        status: str,
        content: str,
        mode: str,
        data: dict[str, Any] | None = None,
        sources: list[str] | None = None,
        issues: list[dict[str, Any]] | None = None,
        note: str = "",
    ) -> AgentResult:
        return AgentResult(
            agent=self.key,
            status=status,
            content=content.strip(),
            data=data or {},
            sources=sources or [],
            issues=issues or [],
            mode=mode,
            note=note,
        )

    @staticmethod
    def extract_json(text: str) -> dict[str, Any] | None:
        blocks = JSON_BLOCK_RE.findall(text or "")
        if not blocks:
            start = (text or "").find("{")
            end = (text or "").rfind("}")
            if start >= 0 and end > start:
                blocks = [text[start : end + 1]]
            else:
                return None
        for block in reversed(blocks):
            try:
                data = json.loads(block)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
        return None


def offline_note(cfg: Config, reason: str) -> str:
    return (
        f"[OFFLINE] {reason}。当前为确定性离线模式，输出仅用于流程联调，"
        "不代表模型创作结果；配置 NOVEL_LLM_API_KEY 后即为真实 AI 输出。"
    )
