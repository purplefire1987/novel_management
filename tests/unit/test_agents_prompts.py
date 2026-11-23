"""提示词装配与信封校验。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_management.adapters.mock import MockLLM
from novel_management.agents import prompts, registry, runner
from novel_management.domain.errors import UsageError


def test_nine_required_sections_present() -> None:
    """PRD §6.4 的公共前缀九项，一项都不能少。"""
    for role_id in registry.ROLE_IDS:
        prompt = prompts.build(role_id)
        keys = prompts.section_keys(prompt)
        missing = [key for key in prompts.REQUIRED_KEYS if key not in keys]
        assert not missing, f"{role_id} 缺公共约束：{missing}"


def test_common_texts_are_shared_across_roles() -> None:
    """同一段约束在九个角色里必须一字不差，否则等于九套规矩。"""
    texts = {}
    for role_id in registry.ROLE_IDS:
        prompt = prompts.build(role_id)
        texts[role_id] = {section.key: section.body for section in prompt.sections}
    for key in ("uncertainty", "citation", "injection", "no_claim", "scope"):
        bodies = {texts[role_id][key] for role_id in registry.ROLE_IDS}
        assert len(bodies) == 1, f"{key} 在各角色间不一致"


def test_prompt_carries_tools_and_role_card() -> None:
    prompt = prompts.build("writer")
    assert "writer（主笔）" in prompt.system
    assert "## 职责与边界（角色卡）" in prompt.system
    assert "search" in prompt.system
    assert prompt.tokens > 0
    # 「不得宣称未执行的动作」这一条得真的出现在文本里
    assert "不得宣称完成了未执行的文件写入、审批或发布动作" in prompt.system


def test_missing_task_says_read_only() -> None:
    """没有任务授权时，提示词必须把话说死：只读。"""
    section = next(s for s in prompts.build("writer").sections if s.key == "task")
    assert "不得写 staging" in section.body
    assert section.hard is True


def test_release_section_states_absence() -> None:
    empty = next(s for s in prompts.build("writer").sections if s.key == "release")
    assert "尚未发布过" in empty.body
    filled = next(
        s for s in prompts.build("writer", release="REL-01M85DMB0000000000000031NM").sections if s.key == "release"
    )
    assert "REL-01M85DMB0000000000000031NM" in filled.body


def test_missing_card_raises(tmp_path: Path) -> None:
    with pytest.raises(UsageError) as exc:
        prompts.build("writer", card_path=tmp_path / "nope.md")
    assert "角色卡缺失" in str(exc.value)


def test_extract_json_handles_fences_and_noise() -> None:
    payload = {"a": 1}
    assert runner.extract_json(json.dumps(payload)) == payload
    assert runner.extract_json("```json\n" + json.dumps(payload) + "\n```") == payload
    assert runner.extract_json("说明文字\n" + json.dumps(payload) + "\n结束") == payload
    assert runner.extract_json("完全不是 JSON") is None
    assert runner.extract_json("") is None
    assert runner.extract_json("[1, 2]") is None


def _envelope(role: str = "researcher") -> dict:
    return {
        "task_id": "TSK-01M85DMB000000000000000001",
        "role": role,
        "result": {"key_constraints": []},
    }


def test_runner_accepts_valid_envelope() -> None:
    llm = MockLLM(responder=lambda messages: json.dumps(_envelope(), ensure_ascii=False))
    run = runner.run(llm, "researcher", instructions="给我约束")
    assert run.ok
    assert run.envelope["role"] == "researcher"
    assert llm.call_count == 1
    # 系统消息里带的是装配好的提示词，不是随手一句话
    assert "九项" not in llm.calls[0].messages[0]["content"]
    assert "不确定性处理" in llm.calls[0].messages[0]["content"]


def test_runner_rejects_garbage_without_guessing() -> None:
    llm = MockLLM(default="我觉得可以这么写。")
    run = runner.run(llm, "researcher", instructions="给我约束")
    assert not run.ok
    assert run.envelope == {}
    assert "不是可解析的 JSON" in run.describe_errors()
    assert run.result() == {}


def test_runner_rejects_role_mismatch() -> None:
    llm = MockLLM(responder=lambda messages: json.dumps(_envelope("writer"), ensure_ascii=False))
    run = runner.run(llm, "researcher", instructions="给我约束")
    assert not run.ok
    assert "role" in run.describe_errors()


def test_runner_rejects_schema_violation() -> None:
    bad = {"task_id": "T-1", "role": "researcher", "result": {}}
    llm = MockLLM(responder=lambda messages: json.dumps(bad, ensure_ascii=False))
    run = runner.run(llm, "researcher", instructions="x")
    assert not run.ok
    assert any("task_id" in error for error in run.errors)


def test_envelope_schema_rejects_extra_field() -> None:
    data = _envelope()
    data["ok"] = True
    errors = runner.envelope_ok(data)
    assert errors, "additionalProperties=false 该挡住随手加的字段"
