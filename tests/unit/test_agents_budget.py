"""预算：截断看得见、硬约束不丢。

这一组里有一条是**回归测试**，对应 T08 期间的一个真缺陷：
`soft_allowance` 早先没有扣掉已经用掉的软额度，于是每个检索级都拿到一份完整配额，
预算 32000 能塞进 48000。这种错不报错、不警告，只会让「预算」这个字段变成摆设，
然后在真机上把窗口撑爆。
"""

from __future__ import annotations

import pytest

from novel_management.agents.budget import (
    Budget,
    estimate_tokens,
    measure_item,
)
from novel_management.errors import UsageError


def test_estimate_tokens_counts_non_space() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("灰石渡") == 3
    # 空白（含换行与全角空格）不计
    assert estimate_tokens("灰 石\n渡\u3000口") == 4


def test_measure_item_handles_dict() -> None:
    assert measure_item("abc") == 3
    assert measure_item({"a": "bc"}) > 0


def test_soft_truncation_records_counts() -> None:
    budget = Budget(context_limit=100, output_reserve=0, hard_reserve=0)
    fitted = budget.place("hits", ["字" * 30 for _ in range(5)])
    assert len(fitted.items) == 3
    assert fitted.truncation is not None
    assert fitted.truncation.kept == 3
    assert fitted.truncation.dropped == 2
    assert fitted.truncation.total == 5
    assert "丢 2 条" in fitted.truncation.describe()
    assert budget.report()["truncated"][0]["dropped"] == 2


def test_soft_allowance_is_not_double_spent() -> None:
    """回归：每个小节各拿一份完整配额 → 总量超预算。"""
    budget = Budget(context_limit=100, output_reserve=0, hard_reserve=0)
    first = budget.place("a", ["字" * 40])
    second = budget.place("b", ["字" * 40])
    third = budget.place("c", ["字" * 40])
    assert len(first.items) == 1
    assert len(second.items) == 1
    assert third.items == (), "前两节已经吃完 80，第三节不该再拿到 40"
    assert budget.used <= budget.context_limit
    assert third.truncation is not None and third.truncation.dropped == 1


def test_hard_constraints_are_never_dropped() -> None:
    """硬约束装不下时不砍条目，只报溢出。砍了就变成「约束只有这么多」。"""
    budget = Budget(context_limit=1000, output_reserve=100, hard_reserve=50)
    items = ["必须发生的一件事" * 3 for _ in range(5)]
    fitted = budget.place("key_constraints", items, hard=True)
    assert len(fitted.items) == 5, "硬约束一条都不许丢"
    assert fitted.overflow is not None
    assert fitted.overflow.items == 5
    assert fitted.overflow.over_by > 0
    assert budget.hard_overflow is True
    assert "无法在不丢约束的前提下开工" in fitted.overflow.describe()
    # 硬约束不走截断通道：把它记成「截断」会让下游以为只是少看了几眼
    assert budget.truncations == ()


def test_hard_within_reserve_is_not_an_overflow() -> None:
    budget = Budget(context_limit=1000, output_reserve=100, hard_reserve=500)
    fitted = budget.place("key_constraints", ["短约束"], hard=True)
    assert fitted.ok
    assert budget.hard_overflow is False


def test_leftover_hard_reserve_goes_back_to_soft() -> None:
    """硬约束只用了 10 字，剩下的预留该回收给软资料，而不是白扣着。"""
    budget = Budget(context_limit=1000, output_reserve=0, hard_reserve=500)
    budget.place("hard", ["字" * 10], hard=True)
    assert budget.soft_allowance == 990
    fitted = budget.place("soft", ["字" * 900])
    assert len(fitted.items) == 1


def test_report_shape() -> None:
    budget = Budget(context_limit=100, output_reserve=10, hard_reserve=20)
    budget.place("h", ["字" * 5], hard=True)
    budget.place("s", ["字" * 200])
    report = budget.report()
    for key in (
        "context_limit",
        "output_reserve",
        "hard_reserve",
        "used",
        "hard_used",
        "hard_items",
        "truncated",
        "hard_overflow",
    ):
        assert key in report
    assert report["used"] == report["hard_used"] + report["soft_used"]


def test_negative_budget_rejected() -> None:
    with pytest.raises(UsageError):
        Budget(context_limit=0)
    with pytest.raises(UsageError):
        Budget(output_reserve=-1)


def test_body_exceeding_allowance_truncates_whole_section() -> None:
    """第一条就装不下时，收 0 条并记 dropped=全部——不能悄悄返回空表。"""
    budget = Budget(context_limit=10, output_reserve=0, hard_reserve=0)
    fitted = budget.place("recent_text", ["字" * 50])
    assert fitted.items == ()
    assert fitted.truncation is not None
    assert fitted.truncation.kept == 0
    assert fitted.truncation.dropped == 1
