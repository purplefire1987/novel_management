"""事件 DAG：可比性、环、拓扑序。

验收第 2 条「无法排序返回 UNORDERED」的根在这里：`is_before` 必须是三值，
不可比返回 `None` 而不是 `False`。测试直接盯这一点，不通过查询层绕。
"""

from __future__ import annotations

from novel_management.domain.timeline.graph import (
    COMPARE_AFTER,
    COMPARE_BEFORE,
    COMPARE_SAME,
    COMPARE_UNORDERED,
    EventGraph,
)


def _graph(*edges: tuple[str, str], nodes: tuple[str, ...] = ()) -> EventGraph:
    ids = list(nodes) or [node for edge in edges for node in edge]
    return EventGraph.from_edges(ids, edges)


def test_compare_gives_four_distinct_results():
    graph = _graph(("E1", "E2"), ("E2", "E3"))
    assert graph.compare("E1", "E3") == COMPARE_BEFORE
    assert graph.compare("E3", "E1") == COMPARE_AFTER
    assert graph.compare("E2", "E2") == COMPARE_SAME
    # E1 与 E4 之间没有任何路径：不可比，不能当成「E4 更早」。
    graph = _graph(("E1", "E2"), nodes=("E1", "E2", "E4"))
    assert graph.compare("E1", "E4") == COMPARE_UNORDERED


def test_is_before_returns_none_not_false():
    graph = _graph(("E1", "E2"), nodes=("E1", "E2", "E9"))
    assert graph.is_before("E9", "E1") is None
    assert graph.is_before("E1", "E2") is True
    assert graph.is_before("E2", "E1") is False


def test_transitive_reachability():
    graph = _graph(("A", "B"), ("B", "C"), ("C", "D"))
    assert graph.compare("A", "D") == COMPARE_BEFORE


def test_cycle_returns_the_loop_not_a_boolean():
    graph = _graph(("A", "B"), ("B", "C"), ("C", "A"))
    loop = graph.cycle()
    assert loop, "有环必须报出来"
    assert loop[0] == loop[-1], f"回路应首尾相接：{loop}"
    assert set(loop) == {"A", "B", "C"}


def test_self_loop_is_a_cycle():
    graph = _graph(("A", "A"))
    assert graph.cycle() == ("A", "A")


def test_acyclic_graph_reports_no_cycle():
    assert _graph(("A", "B"), ("B", "C")).cycle() == ()


def test_latest_among_keeps_incomparable_candidates():
    """互不可比的全部留下——这是「不随便挑最新一条」的底层保证。"""
    graph = _graph(("E1", "E2"), nodes=("E1", "E2", "E3", "E4"))
    assert set(graph.latest_among(["E1", "E2"])) == {"E2"}
    # E3 与 E4 无路径：两个都是「最晚」，不能只留一个。
    assert set(graph.latest_among(["E3", "E4"])) == {"E3", "E4"}
    # E1 与 E3 之间没有路径，所以 E1 也是「极大元」。诚实的结果是三个都留下：
    # 这类不可比不是靠 ID 大小或直觉能补的，补出来就是编造故事顺序。
    assert set(graph.latest_among(["E1", "E3", "E4"])) == {"E1", "E3", "E4"}
    # 有路径时才有唯一最晚。
    assert set(graph.latest_among(["E1", "E1"])) == {"E1"}


def test_latest_among_empty_input():
    assert _graph(("A", "B")).latest_among([]) == ()


def test_dangling_edge_does_not_create_ghost_node():
    graph = _graph(("A", "B"), ("B", "GHOST"), nodes=("A", "B"))
    assert "GHOST" not in graph.nodes
    assert graph.dangling and "GHOST" in graph.dangling[0]
    # 幽灵节点若被造出来，compare 会声称可与它比较——那是编造事实。
    assert graph.compare("A", "GHOST") == COMPARE_UNORDERED


def test_topological_order_partial_when_cycle():
    graph = _graph(("A", "B"), ("B", "A"), ("C", "D"))
    order = graph.topological_order()
    assert set(order) == {"C", "D"}, f"有环的子图排不出来就不该假装排出来：{order}"
