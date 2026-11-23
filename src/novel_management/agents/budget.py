"""上下文预算：截断必须看得见，硬约束一条都不许丢。

PRD §13.5 四条要求，逐条对应到本模块的一个机制：

| 要求 | 机制 |
|---|---|
| 为硬约束预留空间 | `Budget.hard_reserve` 先扣下来，软资料碰不到 |
| 为输出预留空间 | `Budget.output_reserve` 同理，写正文时模型得有地方写字 |
| 明确记录截断 | 每次装不下都产一条 `Truncation`，**带条数**，不是一句「已截断」 |
| 不静默丢弃硬限制 | `place(..., hard=True)` 永不丢条目；装不下就记 `hard_overflow`，由 gate 判 BLOCKED |

两条设计上的取舍，先说清楚。

**一、估的是字符上界，不是真 token。** 中文一个字大致等于一个 token（多数分词器
在 0.6–1.5 之间浮动）。用一个偏大的估计做预算，结果是「宁可早截断」——
反过来估小了会超窗，超窗的后果是模型静默丢开头，那正好是本模块要防的事。
接真模型时可以把 `measure` 换成真的分词器，接口不用动。

**二、硬约束装不下时不压缩它，而是让任务停下。** 看起来「压缩一下继续」更省事，
但压缩硬约束等于替作者改写约束——PRD §13.5 给的三条出路里，压缩只针对
「非关键资料」，硬约束放不下就只剩「减少写作范围」和「阻断任务」两条。
前一条要作者决定范围，所以这一层只能选第三条：阻断，并把超出多少写清楚。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..errors import UsageError

# 默认值按「一部中文长篇的一章」量级取：一章 6000–9000 字，
# 加资料包与输出，32k 的窗口是当前的保守假设。改这里等于改预算策略。
DEFAULT_CONTEXT_LIMIT = 32000
DEFAULT_OUTPUT_RESERVE = 4000
DEFAULT_HARD_RESERVE = 6000

Measure = Callable[[Any], int]


def estimate_tokens(text: str) -> int:
    """保守上界估计：非空白字符数。

    空白（含换行、全角空格）不占多少 token，但正文里的字一个都跑不掉。
    这个估计对英文偏大（一个词被数成五六个），对中文基本准；
    预算宁大勿小，所以不为此再做区分。
    """
    if not text:
        return 0
    return sum(1 for ch in text if not ch.isspace())


def measure_item(item: Any) -> int:
    """默认量法：字符串按字数，其余按规范化 JSON 的字数。"""
    if isinstance(item, str):
        return estimate_tokens(item)
    try:
        text = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(item)
    return estimate_tokens(text)


@dataclass(frozen=True)
class Truncation:
    """一次装不下。`dropped` 是条数，不是字符数——只看字符数会让人以为「少了一点」。"""

    section: str
    kept: int
    dropped: int
    reason: str = "预算不足"

    @property
    def total(self) -> int:
        return self.kept + self.dropped

    def as_dict(self) -> dict:
        return {
            "section": self.section,
            "kept": self.kept,
            "dropped": self.dropped,
            "total": self.total,
            "reason": self.reason,
        }

    def describe(self) -> str:
        return f"{self.section}：{self.total} 条里收了 {self.kept} 条，丢 {self.dropped} 条（{self.reason}）"


@dataclass(frozen=True)
class Overflow:
    """硬约束放不下。与 `Truncation` 分开，因为它不是「少看了一点」，而是「不能开工」。"""

    section: str
    items: int
    over_by: int

    def as_dict(self) -> dict:
        return {"section": self.section, "items": self.items, "over_by": self.over_by}

    def describe(self) -> str:
        return f"{self.section}：{self.items} 条硬约束超出预留 {self.over_by} 字，无法在不丢约束的前提下开工"


@dataclass(frozen=True)
class Fitted:
    """一次放置的结果。`items` 里的东西**已经全部在预算内**（硬约束除外，见 `overflow`）。"""

    section: str
    items: tuple[Any, ...]
    hard: bool
    used: int
    overflow: Overflow | None = None
    truncation: Truncation | None = None

    @property
    def ok(self) -> bool:
        return self.overflow is None


@dataclass
class Budget:
    """一次任务内的预算账本。就地累加，`report()` 出账。"""

    context_limit: int = DEFAULT_CONTEXT_LIMIT
    output_reserve: int = DEFAULT_OUTPUT_RESERVE
    hard_reserve: int = DEFAULT_HARD_RESERVE
    measure: Measure = measure_item
    _hard_used: int = field(default=0, init=False, repr=False)
    _hard_count: int = field(default=0, init=False, repr=False)
    _soft_used: int = field(default=0, init=False, repr=False)
    _truncations: list[Truncation] = field(default_factory=list, init=False, repr=False)
    _overflows: list[Overflow] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.context_limit < 1:
            raise UsageError(f"上下文预算必须为正数，收到 {self.context_limit}")
        for name in ("output_reserve", "hard_reserve"):
            value = getattr(self, name)
            if value < 0:
                raise UsageError(f"{name} 不能为负，收到 {value}")

    # ---- 账目 ----
    @property
    def soft_allowance(self) -> int:
        """软资料还能用多少（**剩余**额度，不是总额）。

        预留只按**硬约束实际用量**扣，不按 `hard_reserve` 全额扣：预留是按最坏
        情况留的，实际只用了 10 字还扣着 6000，等于白扔一大块窗口。
        硬约束总在软资料之前放置（`preflight` 里按这个次序排），所以「实际用量」
        在软资料开始装的时候已经确定。

        已经用掉的软额度必须扣掉。早先没扣，于是每个小节都拿到一份完整配额：
        八个检索级各花 6000，预算 32000 却能塞进 48000——预算还在，上限没了。
        这种错不会报错，只会让「预算」这个字段变成摆设。
        """
        remaining = self.context_limit - self.output_reserve - self._hard_used - self._soft_used
        return max(remaining, 0)

    @property
    def used(self) -> int:
        return self._hard_used + self._soft_used

    @property
    def truncations(self) -> tuple[Truncation, ...]:
        return tuple(self._truncations)

    @property
    def overflows(self) -> tuple[Overflow, ...]:
        return tuple(self._overflows)

    @property
    def hard_overflow(self) -> bool:
        return bool(self._overflows)

    # ---- 放置 ----
    def place(
        self,
        section: str,
        items: Iterable[Any],
        *,
        hard: bool = False,
        reason: str = "预算不足",
    ) -> Fitted:
        """把一组条目装进预算。

        硬约束：一条都不丢。超了就记 `Overflow` 并**照旧全部返回**——
        返回被砍过的硬约束，调用方会以为约束就这么多。
        软资料：按顺序装到装不下为止，剩下的记一条 `Truncation`。
        """
        pool = list(items)
        if hard:
            return self._place_hard(section, pool)
        return self._place_soft(section, pool, reason)

    def _place_hard(self, section: str, pool: Sequence[Any]) -> Fitted:
        sizes = [self.measure(item) for item in pool]
        total = sum(sizes)
        self._hard_used += total
        self._hard_count += len(pool)

        overflow = None
        if total > self.hard_reserve:
            overflow = Overflow(section=section, items=len(pool), over_by=total - self.hard_reserve)
            self._overflows.append(overflow)
        return Fitted(
            section=section,
            items=tuple(pool),
            hard=True,
            used=total,
            overflow=overflow,
        )

    def _place_soft(self, section: str, pool: Sequence[Any], reason: str) -> Fitted:
        allowance = self.soft_allowance
        kept: list[Any] = []
        used = 0
        for item in pool:
            size = self.measure(item)
            if used + size > allowance:
                break
            kept.append(item)
            used += size
        dropped = len(pool) - len(kept)
        self._soft_used += used

        truncation = None
        if dropped:
            truncation = Truncation(section=section, kept=len(kept), dropped=dropped, reason=reason)
            self._truncations.append(truncation)
        return Fitted(
            section=section,
            items=tuple(kept),
            hard=False,
            used=used,
            truncation=truncation,
        )

    # ---- 出账 ----
    def report(self) -> dict:
        return {
            "context_limit": self.context_limit,
            "output_reserve": self.output_reserve,
            "hard_reserve": self.hard_reserve,
            "soft_allowance_initial": max(
                self.context_limit - self.output_reserve - self._hard_used,
                0,
            ),
            "soft_used": self._soft_used,
            "soft_remaining": self.soft_allowance,
            "used": self.used,
            "hard_used": self._hard_used,
            "hard_items": self._hard_count,
            "truncated": [item.as_dict() for item in self._truncations],
            "hard_overflow": [item.as_dict() for item in self._overflows],
        }

    def truncation_notes(self) -> list[str]:
        """给人看的截断说明。空列表表示「一条都没丢」。"""
        return [item.describe() for item in self._truncations]

    def overflow_notes(self) -> list[str]:
        return [item.describe() for item in self._overflows]
