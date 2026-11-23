"""Mock LLM：把「模型调用」这一层先钉死成可测试的形状。

T01 要求「测试不需要 API Key」，但真接模型是 T08 的事。这里只做三件事：

1. 定出调用形状（`LLMClient.complete`），后面接真模型时替换实现即可；
2. 给出确定性实现，同样的输入永远同样的输出——测试才能断言；
3. 记录每次调用，测试可以检查「发了什么、发了几次」。

**它绝不联网，也绝不读任何 API Key 环境变量。** 这不是惰性，是硬要求：
测试一旦依赖真实网络或密钥，就不再是测试，而是「碰运气」。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

Message = Mapping[str, str]


@dataclass(frozen=True)
class LLMResponse:
    """一次补全的结果。字段只留后面真用得上的，不预先堆参数。"""

    text: str
    model: str = "mock-1"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"


@dataclass(frozen=True)
class LLMCall:
    """一次调用的留痕：发出去的、以及拿回来的。"""

    messages: tuple[dict[str, str], ...]
    model: str
    response: LLMResponse


@runtime_checkable
class LLMClient(Protocol):
    """模型客户端契约。接真模型时实现这一个方法即可。"""

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str = "",
        **options: Any,
    ) -> LLMResponse: ...


def _freeze(messages: Sequence[Message]) -> tuple[dict[str, str], ...]:
    return tuple(
        {"role": str(m.get("role", "user")), "content": str(m.get("content", ""))}
        for m in messages
    )


@dataclass
class MockLLM:
    """脚本化的假模型。

    - `script`：按顺序返回的文本队列，用完后回落到 `default`；
    - `responder`：需要按输入生成时用，签名 `(messages) -> str`；
    - `calls`：每次调用的留痕，供断言。
    """

    script: list[str] = field(default_factory=list)
    default: str = ""
    model: str = "mock-1"
    responder: Callable[[Sequence[Message]], str] | None = None
    calls: list[LLMCall] = field(default_factory=list)

    def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str = "",
        **options: Any,
    ) -> LLMResponse:
        used_model = model or self.model
        if self.script:
            text = self.script.pop(0)
        elif self.responder is not None:
            text = self.responder(messages)
        else:
            text = self.default

        response = LLMResponse(
            text=text,
            model=used_model,
            prompt_tokens=sum(len(m.get("content", "")) for m in messages),
            completion_tokens=len(text),
        )
        self.calls.append(
            LLMCall(messages=_freeze(messages), model=used_model, response=response)
        )
        return response

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def last_prompt(self) -> str:
        """最后一次调用的 user 内容，断言用。"""
        if not self.calls:
            return ""
        for message in reversed(self.calls[-1].messages):
            if message.get("role") == "user":
                return message["content"]
        return self.calls[-1].messages[-1]["content"] if self.calls[-1].messages else ""
