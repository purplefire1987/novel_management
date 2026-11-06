"""横向立场（盟友／中立／敌对）的投影规则。

横向关系是**对称**的：甲写一头，乙页上就该出现甲。这条规则以前只覆盖盟友与敌对，
中立加进来之后必须一起走 `SYMMETRIC` 那条路，否则「A 说与 B 中立」在 B 页上会消失
——查 B 的时候看不出它其实已经表过态。

这里故意不碰磁盘：`build_index` 与 `inverse_of` 都是纯函数，一份不依赖 Vault 的守卫
跑得比复制整棵档案树快得多。
"""

from __future__ import annotations

from pathlib import Path

from novel_system.workflow.relation_sync import (
    ALLOWED,
    DECLARATIONS,
    Edge,
    Node,
    SYMMETRIC,
    _render,
    build_index,
    inverse_of,
)

STANCE = ("盟友", "中立", "敌对")


def _stance_edge(label: str) -> Edge:
    return Edge("FAC-A", "FAC-B", label, "势力", "势力")


def test_all_three_stances_are_symmetric() -> None:
    assert set(STANCE) <= SYMMETRIC
    for label in STANCE:
        assert inverse_of(label) == label, f"{label} 的逆关系必须是自己"


def test_neutral_is_projected_to_both_ends() -> None:
    index = build_index([_stance_edge("中立")])
    assert index["FAC-A"] == [("中立", "FAC-B")]
    assert index["FAC-B"] == [("中立", "FAC-A")]


def test_hierarchy_still_has_direction() -> None:
    """层级不能被顺带对称化：A 的上级是 B，B 页上应写「下级 A」而不是「上级 A」。"""
    index = build_index([Edge("FAC-A", "FAC-B", "上级", "势力", "势力")])
    assert index["FAC-A"] == [("上级", "FAC-B")]
    assert index["FAC-B"] == [("下级", "FAC-A")]


def test_neutral_field_is_declared_and_allowed() -> None:
    """字段要同时出现在两张表里才算接通——只加一边 = 静默丢弃，最难查的那种错。"""
    for field in ("中立", "中立势力"):
        assert DECLARATIONS[field] == ("势力", "中立")
        assert field in ALLOWED["势力"]


def test_render_orders_ally_neutral_foe(tmp_path: Path) -> None:
    """渲染顺序是盟友 → 中立 → 敌对。顺序错了不报错，但「谁没表态」就看不出来。"""
    self_node = Node("FAC-A", "甲方", "势力", tmp_path / "a.md")
    peers = {
        f"FAC-{suffix}": Node(f"FAC-{suffix}", name, "势力", tmp_path / f"{suffix}.md")
        for suffix, name in (("B", "乙方"), ("C", "丙方"), ("D", "丁方"))
    }
    block = _render(
        self_node,
        [("盟友", "FAC-B"), ("中立", "FAC-C"), ("敌对", "FAC-D")],
        {self_node.id: self_node, **peers},
    )
    assert block.index("**盟友**") < block.index("**中立**") < block.index("**敌对**")
    # 链接用文件名（stem）当目标，与库里的 `[[FAC-xxxx_名称|名称]]` 同一口径
    assert "[[C|丙方]]" in block
