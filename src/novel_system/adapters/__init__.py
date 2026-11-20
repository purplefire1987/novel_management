"""模型适配层。

T01 只交付 Mock，真模型接入属 T08。对外只暴露契约与 Mock，
避免上层代码直接依赖某个 SDK。
"""

from __future__ import annotations

from .mock import LLMCall, LLMClient, LLMResponse, MockLLM

__all__ = ["LLMCall", "LLMClient", "LLMResponse", "MockLLM"]
