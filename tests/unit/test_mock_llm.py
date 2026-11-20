"""Mock LLM 的测试。

T01 的验收之一是「测试不需要 API Key」。这条测试要能证明两件更强的事：
它不联网，也不读密钥——否则「不需要 Key」只是个巧合。
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from novel_system.adapters import LLMClient, MockLLM


def test_returns_scripted_text_in_order() -> None:
    llm = MockLLM(script=["第一段", "第二段"])
    assert llm.complete([{"role": "user", "content": "a"}]).text == "第一段"
    assert llm.complete([{"role": "user", "content": "b"}]).text == "第二段"


def test_falls_back_to_default_when_script_exhausted() -> None:
    llm = MockLLM(script=["只有一次"], default="兜底")
    llm.complete([{"role": "user", "content": "a"}])
    assert llm.complete([{"role": "user", "content": "b"}]).text == "兜底"


def test_responder_is_deterministic() -> None:
    """同样输入必须同样输出，否则断言无从谈起。"""
    llm = MockLLM(responder=lambda msgs: f"字数{sum(len(m['content']) for m in msgs)}")
    first = llm.complete([{"role": "user", "content": "青石镇"}])
    second = llm.complete([{"role": "user", "content": "青石镇"}])
    assert first.text == second.text == "字数3"


def test_records_every_call() -> None:
    llm = MockLLM(default="ok")
    llm.complete([{"role": "system", "content": "你是编辑"}, {"role": "user", "content": "改这段"}])
    assert llm.call_count == 1
    call = llm.calls[0]
    assert call.messages[0]["role"] == "system"
    assert call.response.text == "ok"
    assert llm.last_prompt() == "改这段"


def test_no_network_access(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 socket 打死，Mock 仍须正常工作。"""

    def _boom(*args, **kwargs):
        raise AssertionError("Mock LLM 不该发起任何网络连接")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    llm = MockLLM(default="离线也能跑")
    assert llm.complete([{"role": "user", "content": "hi"}]).text == "离线也能跑"


def test_works_without_api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "NOVEL_LLM_KEY"):
        monkeypatch.delenv(name, raising=False)
    llm = MockLLM(default="无需密钥")
    assert llm.complete([{"role": "user", "content": "hi"}]).text == "无需密钥"


def test_satisfies_client_protocol() -> None:
    assert isinstance(MockLLM(), LLMClient)


def test_does_not_write_anything(tmp_path: Path) -> None:
    """Mock 只回文本，不落盘——写盘是上层的事，别混进适配层。

    用「调用前后目录快照一致」判定，而不是「目录为空」：
    tmp_path 里本来就有 conftest 建的隔离目录，断言为空会误报。
    """
    before = sorted(p.name for p in tmp_path.iterdir())
    llm = MockLLM(default="x")
    llm.complete([{"role": "user", "content": "hi"}])
    assert sorted(p.name for p in tmp_path.iterdir()) == before
