#!/usr/bin/env python3
"""把势力之间的关系汇成一张表。

读什么
------
`01_Core_Memory/Factions/` 下每一张卡片的声明字段，经 `relation_sync` 算成有向边：

    上级 / 上级势力 / 所属势力 / 势力所属（值为真实势力时）  →  上级
    下级 / 下属势力 / 关联：军团库                          →  下级
    盟友 / 盟好 / 结盟                                      →  盟友（对称）
    敌对 / 敌对势力 / 仇敌                                  →  敌对（对称）

写什么
------
`01_Core_Memory/World/九州/总表/WORLD-0042_势力关系表.md`

为什么是生成而不是手写
----------------------
手写过一张关系表，改一张卡就得记得回来改表。这里让表始终等于卡片：
改关系 → 重跑本脚本（或直接 `novel rebuild-index`，它也会重算关系块）。
孤立势力清单也是算出来的，补完关系自然消失。
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

VAULT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(VAULT / "00_System/novel_system/src"))

from novel_system.config import Config  # noqa: E402
from novel_system.workflow import relation_sync as rs  # noqa: E402

OUT = VAULT / "01_Core_Memory/World/九州/总表/WORLD-0042_势力关系表.md"
TODAY = date.today().isoformat()

LABEL_ORDER = ("上级", "下级", "盟友", "敌对")


def collect() -> tuple[dict, dict, dict]:
    cfg = Config.load(str(VAULT))
    nodes, by_id = rs.load_nodes(cfg)
    factions = [n for n in nodes if n.kind == "势力"]

    edges = [
        e for e in rs.collect_declarations(nodes, by_id)
        if e.src_kind == "势力" and e.dst_kind == "势力"
    ]

    parents: dict[str, set[str]] = defaultdict(set)
    children: dict[str, set[str]] = defaultdict(set)
    ally: dict[str, set[str]] = defaultdict(set)
    foe: dict[str, set[str]] = defaultdict(set)

    for e in edges:
        if e.label == "上级":
            parents[e.src].add(e.dst)
            children[e.dst].add(e.src)
        elif e.label == "下级":
            children[e.src].add(e.dst)
            parents[e.dst].add(e.src)
        elif e.label == "盟友":
            ally[e.src].add(e.dst)
            ally[e.dst].add(e.src)
        elif e.label == "敌对":
            foe[e.src].add(e.dst)
            foe[e.dst].add(e.src)

    linked = set(parents) | set(children) | set(ally) | set(foe)
    isolated = [n for n in factions if n.id not in linked]
    return by_id, {
        "factions": factions,
        "edges": edges,
        "parents": parents,
        "children": children,
        "ally": ally,
        "foe": foe,
        "isolated": isolated,
    }, {}


def link(by_id: dict, ident: str) -> str:
    node = by_id.get(ident)
    return node.label if node else ident


def subtree(by_id: dict, children: dict, root: str, depth: int = 0, seen: set | None = None) -> list[str]:
    """渲染一个人的下属树。多上级会导致同一个节点落进两棵树，这里按棵去重。"""
    seen = seen if seen is not None else set()
    lines: list[str] = []
    for kid in sorted(children.get(root, set())):
        if kid in seen:
            continue
        seen.add(kid)
        node = by_id.get(kid)
        if node is None:
            continue
        lines.append(f"{'  ' * depth}- {node.label}")
        lines += subtree(by_id, children, kid, depth + 1, seen)
    return lines


def render(by_id: dict, data: dict) -> str:
    factions: list = data["factions"]
    edges: list = data["edges"]
    parents: dict = data["parents"]
    children: dict = data["children"]
    ally: dict = data["ally"]
    foe: dict = data["foe"]
    isolated: list = data["isolated"]

    tiers = defaultdict(int)
    for e in edges:
        tiers[e.label] += 1

    out: list[str] = []
    out.append("---")
    out.append("id: WORLD-0042")
    out.append("type: world")
    out.append("name: 九州·势力关系表")
    out.append("title: 九州·势力关系表")
    out.append("aliases: []")
    out.append("subtype: 势力关系表")
    out.append("world: 九州")
    out.append("canon_status: proposed")
    out.append(f"created_at: '{TODAY}'")
    out.append(f"updated_at: '{TODAY}'")
    out.append("source_refs:")
    out.append("- 01_Core_Memory/Factions/（全部势力档案的声明字段）")
    out.append("tags:")
    out.append("- 九州")
    out.append("- world-asset")
    out.append("- 总表")
    out.append("- 势力")
    out.append("---")
    out.append("")
    out.append("# 九州·势力关系表")
    out.append("")
    out.append("> **性质**：派生视图，不是独立设定源。每一行都读自 `01_Core_Memory/Factions/` 下")
    out.append("> 势力卡的声明字段，**要改关系就改卡片，再重跑**")
    out.append("> `00_System/scripts/gen_faction_relations.py`；直接改本表会在下次重跑时被覆盖。")
    out.append(">")
    out.append("> 写关系的四个字段：`上级`／`下级`（层级）、`盟友`／`敌对`（横向，写一头即可，")
    out.append("> 反向自动补全）。字段白名单见 `novel_system/workflow/relation_sync.py`。")
    out.append("")

    out.append("## 总览")
    out.append("")
    out.append(f"- **势力档案**：{len(factions)}")
    out.append(f"- **势力间关系边**：{len(edges)}")
    detail = "／".join(f"{k} {tiers[k]}" for k in LABEL_ORDER if tiers.get(k))
    out.append(f"- **按标签**：{detail or '（无）'}")
    out.append(f"- **有对外关系的势力**：{len(factions) - len(isolated)}")
    out.append(f"- **一条关系都没有的势力**：{len(isolated)}")
    out.append("")

    # ---------------- 层级
    out.append("## 层级：谁归谁管")
    out.append("")
    roots = sorted(
        (n for n in factions if not parents.get(n.id) and children.get(n.id)),
        key=lambda n: (-len(children[n.id]), n.id),
    )
    for root in roots:
        out.append(f"### {root.name}（下辖 {len(children[root.id])} 个）")
        out.append("")
        out.extend(subtree(by_id, children, root.id))
        out.append("")
    if not roots:
        out.append("（无层级关系）")
        out.append("")

    # 多上级 = 共管
    multi = {k: v for k, v in parents.items() if len(v) > 1}
    if multi:
        out.append("### 多重归属（共管）")
        out.append("")
        out.append("同一支力量同时归两个势力，通常是「朝廷的名分 + 世家的实权」，不是记错。")
        out.append("")
        out.append("| 势力 | 上级 | 说明 |")
        out.append("| --- | --- | --- |")
        for k in sorted(multi):
            ups = "、".join(sorted(link(by_id, u) for u in multi[k]))
            out.append(f"| {link(by_id, k)} | {ups} | 共管 |")
        out.append("")

    # ---------------- 横向
    out.append("## 横向：盟友与敌对")
    out.append("")
    pairs: dict[tuple[str, str], str] = {}
    for src, dsts in ally.items():
        for dst in dsts:
            pairs[tuple(sorted((src, dst)))] = "盟友"
    for src, dsts in foe.items():
        for dst in dsts:
            pairs[tuple(sorted((src, dst)))] = "敌对"

    if pairs:
        out.append("| 甲方 | 关系 | 乙方 |")
        out.append("| --- | --- | --- |")
        for (a, b), label in sorted(pairs.items(), key=lambda kv: (kv[1], kv[0])):
            out.append(f"| {link(by_id, a)} | {label} | {link(by_id, b)} |")
        out.append("")
    else:
        out.append("**零条**。全库还没有任何一对势力写过 `盟友` 或 `敌对`。")
        out.append("")

    # ---------------- 孤立
    out.append("## 关系待建")
    out.append("")
    out.append("这些势力在库里没有任何一条对外关系——查它的时候，看不到它和谁是一伙的。")
    out.append("")
    if isolated:
        by_kind = defaultdict(list)
        for n in isolated:
            by_kind["军团" if "军团" in n.path.name or _is_legion(n) else "势力"].append(n)
        for kind in ("军团", "势力"):
            group = by_kind.get(kind) or []
            if not group:
                continue
            out.append(f"**{kind}**（{len(group)}）")
            out.append("")
            out.append("、".join(n.label for n in sorted(group, key=lambda n: n.id)))
            out.append("")
    else:
        out.append("（无）")
        out.append("")

    out.append("## 关联")
    out.append("")
    out.append("- 势力分类口径：`00_System/Config/faction_categories.yaml`")
    out.append("- 军团兵力与驻地：[[WORLD-0041_各州驻军表|九州·各州驻军表]]")
    out.append("- 官职与爵位门槛：[[WORLD-0027_帝国官职表|九州·帝国官职表]]、[[WORLD-0028_帝国爵位表|九州·帝国爵位表]]")
    out.append("- 总表入口：[[WORLD-0029_九州表|九州·九州表]]")
    out.append("")
    return "\n".join(out)


def _is_legion(node) -> bool:
    """军团卡与势力卡同目录同类型，只能看 frontmatter 的 subtype。"""
    try:
        text = node.path.read_text(encoding="utf-8")[:600]
    except Exception:  # noqa: BLE001
        return False
    return "subtype: 军团" in text or "subtype: '军团'" in text


def main() -> int:
    by_id, data, _ = collect()
    OUT.write_text(render(by_id, data), encoding="utf-8")
    print(
        f"✓ 势力关系表 {len(data['edges'])} 条边 / "
        f"{len(data['factions'])} 个势力 / 孤立 {len(data['isolated'])} → {OUT.relative_to(VAULT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
