"""事件有向无环图。

PRD §9.3 三条硬要求，这个模块逐条落：

1. **事件图必须无环**。`cycle()` 用 Kahn 拓扑排序判定，有环时返回具体回路
   （不是「图有环」四个字——作者要能顺着回路去改哪条边）。
2. **有效区间是 `[start_event, end_event)`**。语义在 `query.py` 里，图只提供
   「谁在谁之前」。
3. **无法比较的事件返回 `UNORDERED`，不得强行生成先后顺序**。

第 3 条是验收项，也是最容易写错的一条：两个事件在图上互不可达时，直觉做法是
「按 ID 大小排」「按章节顺序排」「取最新一条」——**全都是编造**。ID 后缀不决定
故事顺序（PRD §8.1 明写），章节顺序是叙述顺序不是故事顺序（倒叙就靠这个差别）。

可达性用惰性 BFS + 每节点缓存。事件上万时也不用暴力闭包——单次查询 O(V+E)，
重复查询命中缓存。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable

COMPARE_BEFORE = "BEFORE"
COMPARE_AFTER = "AFTER"
COMPARE_SAME = "SAME"
COMPARE_UNORDERED = "UNORDERED"


@dataclass
class EventGraph:
    """有向图：边 `from_id → to_id` 读作「from 先发生」。"""

    nodes: tuple[str, ...] = ()
    edges: tuple[tuple[str, str], ...] = ()
    _succ: dict[str, set[str]] = field(default_factory=dict, repr=False)
    _pred: dict[str, set[str]] = field(default_factory=dict, repr=False)
    _descendants: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)
    dangling: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for node in self.nodes:
            self._succ.setdefault(node, set())
            self._pred.setdefault(node, set())
        for src, dst in self.edges:
            self._succ.setdefault(src, set()).add(dst)
            self._pred.setdefault(dst, set()).add(src)

    # ---- 构造 ----
    @classmethod
    def from_edges(
        cls,
        event_ids: Iterable[str],
        edges: Iterable[tuple[str, str]],
    ) -> EventGraph:
        """只保留两端都是已知事件的边。

        指向不存在事件的边记进 `dangling` 并丢弃，不造「幽灵节点」：
        幽灵节点会让 `compare()` 声称某事件存在且可比，而它其实连档案都没有。
        """
        known = tuple(dict.fromkeys(event_ids))
        known_set = set(known)
        kept: list[tuple[str, str]] = []
        dangling: list[str] = []
        for src, dst in edges:
            if src in known_set and dst in known_set:
                kept.append((src, dst))
            else:
                missing = src if src not in known_set else dst
                dangling.append(f"{src}->{dst}（缺 {missing}）")
        return cls(nodes=known, edges=tuple(dict.fromkeys(kept)), dangling=tuple(dangling))

    # ---- 环 ----
    def cycle(self) -> tuple[str, ...]:
        """返回一条回路；无环返回空元组。

        返回回路而不是布尔值：作者改档案时要知道是哪几个事件绕成了圈。
        """
        indegree = {node: len(self._pred[node]) for node in self._succ}
        queue = deque(node for node, deg in indegree.items() if deg == 0)
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for nxt in self._succ[node]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)
        if visited == len(indegree):
            return ()

        # 有环：从剩下入度非零的节点里任取一个，顺着 succ 走回自己
        stuck = [node for node, deg in indegree.items() if deg > 0]
        return self._walk_back_to_self(stuck[0]) if stuck else ()

    def _walk_back_to_self(self, start: str) -> tuple[str, ...]:
        """从 `start` 沿出边一直走，直到踩回走过的节点，截出那一段环。

        `start` 的入度非零，所以它必然在某个环上（或能从环到达不了的地方？
        不会——Kahn 剩下的节点每个都至少有一条来自剩余节点的入边，
        顺着入边总能回到环里）。这里顺着出边走，同样能闭合成环。
        """
        seen: list[str] = [start]
        index: dict[str, int] = {start: 0}
        node = start
        while True:
            succ = sorted(self._succ.get(node, ()))
            if not succ:  # 理论上不会发生；真发生了就如实返回走到的路径
                return tuple(seen)
            node = succ[0]
            if node in index:
                return tuple(seen[index[node]:] + [node])
            index[node] = len(seen)
            seen.append(node)

    # ---- 可比性 ----
    def _reachable(self, src: str) -> frozenset[str]:
        """从 src 出发能到达的全部节点（不含 src 自己，除非真的成环回到它）。"""
        cached = self._descendants.get(src)
        if cached is not None:
            return cached
        seen: set[str] = set()
        queue = deque(self._succ.get(src, ()))
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            queue.extend(self._succ.get(node, ()))
        frozen = frozenset(seen)
        self._descendants[src] = frozen
        return frozen

    def is_before(self, a: str, b: str) -> bool | None:
        """a 是否严格早于 b。不可比返回 `None`——**不是 False**。

        三值而不是布尔：`False` 会被调用方当成「a 晚于 b」，
        而真实情况可能是「压根没法比」。这两个结论完全不同。
        """
        if a == b:
            return False
        if b in self._reachable(a):
            return True
        if a in self._reachable(b):
            return False
        return None

    def compare(self, a: str, b: str) -> str:
        """返回 BEFORE / AFTER / SAME / UNORDERED。"""
        if a == b:
            return COMPARE_SAME
        before = self.is_before(a, b)
        if before is None:
            return COMPARE_UNORDERED
        return COMPARE_BEFORE if before else COMPARE_AFTER

    # ---- 排序 ----
    def topological_order(self) -> tuple[str, ...]:
        """拓扑序。有环时返回已能排出的那部分，不假装成功。"""
        indegree = {node: len(self._pred[node]) for node in self._succ}
        queue = deque(sorted(node for node, deg in indegree.items() if deg == 0))
        out: list[str] = []
        while queue:
            node = queue.popleft()
            out.append(node)
            for nxt in sorted(self._succ[node]):
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)
        return tuple(out)

    def latest_among(self, ids: Iterable[str]) -> tuple[str, ...]:
        """给一组事件，返回「最晚」的那批。

        成对比较：某事件若严格早于组内任一其它事件，就不是最晚的。
        互不可比的事件会**全部留下**——调用方据此报 UNORDERED，
        而不是从里面随便挑一个当答案。
        """
        candidates = list(dict.fromkeys(ids))
        if not candidates:
            return ()
        latest: list[str] = []
        for candidate in candidates:
            dominated = False
            for other in candidates:
                if other == candidate:
                    continue
                if self.is_before(candidate, other):
                    dominated = True
                    break
            if not dominated:
                latest.append(candidate)
        return tuple(latest)
