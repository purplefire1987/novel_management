#!/usr/bin/env python3
"""把势力之间的关系汇成一张表。

读什么
------
`01_Core_Memory/Factions/` 下每一张卡片的声明字段，经 `relation_sync` 算成有向边：

    上级 / 上级势力 / 所属势力 / 势力所属（值为真实势力时）  →  上级
    下级 / 下属势力 / 关联：军团库                          →  下级
    盟友 / 盟好 / 结盟                                      →  盟友（对称）
    中立 / 中立势力                                         →  中立（对称）
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

import re
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

LABEL_ORDER = ("上级", "下级", "盟友", "中立", "敌对")


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
    neutral: dict[str, set[str]] = defaultdict(set)
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
        elif e.label == "中立":
            neutral[e.src].add(e.dst)
            neutral[e.dst].add(e.src)
        elif e.label == "敌对":
            foe[e.src].add(e.dst)
            foe[e.dst].add(e.src)

    linked = set(parents) | set(children) | set(ally) | set(neutral) | set(foe)
    isolated = [n for n in factions if n.id not in linked]
    return by_id, {
        "factions": factions,
        "edges": edges,
        "parents": parents,
        "children": children,
        "ally": ally,
        "neutral": neutral,
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
    neutral: dict = data["neutral"]
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
    out.append("> 写关系的五个字段：`上级`／`下级`（层级）、`盟友`／`中立`／`敌对`（横向，写一头即可，")
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

    # 多上级：两种来源要分开说——「共管」是朝廷名分 + 世家实权，
    # 「加盟」是门阀结成的联盟。都写在上表里，但含义不同，混成一句「共管」会读歪。
    multi = {k: v for k, v in parents.items() if len(v) > 1}
    alliance_ids = _alliance_ids(factions)
    if multi:
        out.append("### 多重归属：共管与加盟")
        out.append("")
        out.append("同一支力量同时挂两个上级，来源有两种：**共管**（朝廷的名分 + 世家的实权）、")
        out.append("**加盟**（加入了门阀联盟，联盟不设盟主、不调兵）。两者都不是记错。")
        out.append("")
        out.append("| 势力 | 上级 | 归属方式 |")
        out.append("| --- | --- | --- |")
        for k in sorted(multi):
            ups = sorted(multi[k])
            names = "、".join(link(by_id, u) for u in ups)
            ways = []
            if any(u in alliance_ids for u in ups):
                ways.append("加盟")
            if any(u not in alliance_ids for u in ups):
                ways.append("共管")
            out.append(f"| {link(by_id, k)} | {names} | {' + '.join(ways)} |")
        out.append("")

    # ---------------- 横向
    out.append("## 横向：盟友、中立与敌对")
    out.append("")
    out.append("一对势力只取一个立场。三个标签互斥，靠 `novel integrity` 的「立场矛盾」兜底。")
    out.append("")
    pairs: dict[tuple[str, str], str] = {}
    for src, dsts in ally.items():
        for dst in dsts:
            pairs[tuple(sorted((src, dst)))] = "盟友"
    for src, dsts in neutral.items():
        for dst in dsts:
            pairs[tuple(sorted((src, dst)))] = "中立"
    for src, dsts in foe.items():
        for dst in dsts:
            pairs[tuple(sorted((src, dst)))] = "敌对"

    if pairs:
        out.append("| 甲方 | 关系 | 乙方 |")
        out.append("| --- | --- | --- |")
        rank = {"盟友": 0, "中立": 1, "敌对": 2}
        for (a, b), label in sorted(pairs.items(), key=lambda kv: (rank.get(kv[1], 9), kv[0])):
            out.append(f"| {link(by_id, a)} | {label} | {link(by_id, b)} |")
        out.append("")
    else:
        out.append("**零条**。全库还没有任何一对势力写过 `盟友`、`中立` 或 `敌对`。")
        out.append("")

    # ---------------- 自称中立
    out.append("## 自称中立（身份，不是关系）")
    out.append("")
    out.append("「中立」不是本轮新造的字段，它本来就在旧势力表 `势力所属` 那一栏里标着——")
    out.append("`平民`、`散人`、`百草`、`唐门`、`幽灵山庄`、`十巫山门`、`魇罗殿`、`凰天`、`赤水`")
    out.append("九张卡，从建卡那天起就写着「中立」。所以这一栏记的是**身份**，不是**关系**：")
    out.append("它不需要对端，也不产生边。")
    out.append("")
    out.append("与上表的区别：上表是「甲和乙之间表态了」（写出来的关系）；这里是「甲自己就不站队」")
    out.append("（设定上的身份）。两者可以并存——唐门标着中立，仍然可以跟谁结仇；但身份不会因为")
    out.append("结仇就消失，它只是说明这家门派不投靠谁、也不替谁打仗。")
    out.append("")
    out.append("身份不进「关系待建」的统计，也不该靠编一对「与某方中立」来把名单清空。")
    out.append("")
    neutral_ids = _neutral_ids(factions)
    neutrals = sorted((n for n in factions if n.id in neutral_ids), key=lambda n: n.id)
    if neutrals:
        out.append(f"**{len(neutrals)} 张**：")
        out.append("")
        out.append("| 势力 | 旧表类别 | 阵营 | 据点 |")
        out.append("| --- | --- | --- | --- |")
        for n in neutrals:
            fields = dict(rs._fields(rs._read_body(n.path)))
            tags = "／".join(sorted(_category_tokens(n))) or "—"
            camp = fields.get("阵营", "").strip() or "—"
            seat = fields.get("据点", "").strip() or "—"
            out.append(f"| {link(by_id, n.id)} | {tags} | {camp} | {seat} |")
        out.append("")
        out.append("> 「旧表类别」照抄 `势力所属`，不做归并——`中立` 与 `宗门,中立`、`羽族,中立`")
        out.append("> 的差别本身是设定（既属某个形态，又不站队），抹平会丢掉信息。")
    else:
        out.append("（无）")
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


def _category_tokens(node) -> set[str]:
    """卡上 `势力所属` 一栏的类别词。旧表这一栏混装类别与上级，这里只取词面。"""
    fields = dict(rs._fields(rs._read_body(node.path)))
    return {t.strip() for t in re.split(r"[,，、]", fields.get("势力所属", "")) if t.strip()}


def _alliance_ids(factions: list) -> set[str]:
    """哪些势力卡自称「联盟」。

    读的是 `势力所属` 里的类别词（白名单在 `faction_categories.yaml`），不按名字猜：
    叫「XX盟」的不一定是联盟，叫「长流」的恰恰是。
    """
    return {n.id for n in factions if "联盟" in _category_tokens(n)}


def _neutral_ids(factions: list) -> set[str]:
    """哪些势力卡自称「中立」。**身份**，不是关系——中立身份不产生任何边。"""
    return {n.id for n in factions if "中立" in _category_tokens(n)}


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
