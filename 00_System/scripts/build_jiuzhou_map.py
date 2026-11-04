#!/usr/bin/env python3
"""把《九州表》（WORLD-0029）落成地图：州级地点档案 + 地图页 + Canvas 视图。

背景：`01_Core_Memory/Maps/` 建库时只有 `.gitkeep`，九州表只落成一篇文本，
「关联：地图库」那一行从没被解析成层级，所以地图一直是空的。

本脚本做三件事，全部幂等，可反复运行：

1. 为九州表里每个州/大区生成 location 档案（LOC-0073 起），带下辖城的双链；
2. 生成 `01_Core_Memory/Maps/九州地图.md`：方位、地貌、口语、主城一页看全；
3. 生成 `01_Core_Memory/Maps/九州地图.canvas`：中州居中、八州环列的方位图。

城名到档案编号按 `01_Core_Memory/Locations/LOC-*.md` 的文件名反查，
查不到的城会打印出来（不静默吞掉）。
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

VAULT = Path(__file__).resolve().parents[2]
WORLD_TABLE = VAULT / "01_Core_Memory/World/九州/总表/WORLD-0029_九州表.md"
LOC_DIR = VAULT / "01_Core_Memory/Locations"
MAP_DIR = VAULT / "01_Core_Memory/Maps"
TODAY = "2026-11-04"

# 九州表里的大区名 → 方位（用于 Canvas 布局与地图页排序）
REGIONS: dict[str, tuple[str, float, float]] = {
    "中州": ("中部", 0.0, 0.0),
    "凉州": ("北部", 0.0, -1.0),
    "海州": ("东北", 0.7071, -0.7071),
    "扬州": ("东部", 1.0, 0.0),
    "越州": ("南部", 0.0, 1.0),
    "湖州": ("东南", 0.7071, 0.7071),
    "陵州": ("西北", -0.7071, -0.7071),
    "戎州": ("西南", -0.7071, 0.7071),
    "云州": ("西部", -1.0, 0.0),
    "海外十五州": ("海外", 0.0, 0.0),
    "三十六洞天，七十二奇地": ("界外", 0.0, 0.0),
}

OFF_MAINLAND = {"海外十五州", "三十六洞天，七十二奇地"}


@dataclass
class Region:
    name: str
    terrain: str = ""
    intro: str = ""
    slang: str = ""
    cities: list[str] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)   # 九州表未列、依档案「州属」补入

    @property
    def direction(self) -> str:
        return REGIONS.get(self.name, ("", 0.0, 0.0))[0]

    @property
    def slug(self) -> str:
        return self.name.replace("，", "·").replace(" ", "")


def parse_table() -> list[Region]:
    text = WORLD_TABLE.read_text(encoding="utf-8")
    body = text.split("---", 2)[-1]
    regions: list[Region] = []
    current: Region | None = None
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            current = Region(name=line[3:].strip())
            regions.append(current)
            continue
        if current is None or not line.startswith("- **"):
            continue
        m = re.match(r"- \*\*(.+?)\*\*：(.+)$", line)
        if not m:
            continue
        key, value = m.group(1).strip(), m.group(2).strip()
        if key == "主要地貌":
            current.terrain = value
        elif key == "简介":
            current.intro = value
        elif key == "特色口语":
            current.slang = value
        elif key == "关联：地图库":
            current.cities = [c.strip() for c in value.split(",") if c.strip()]
    return [r for r in regions if r.name in REGIONS]


def city_index() -> dict[str, str]:
    """城名 → 档案文件名（不含 .md）。"""
    idx: dict[str, str] = {}
    for p in LOC_DIR.glob("LOC-*.md"):
        stem = p.stem
        name = stem.split("_", 1)[1] if "_" in stem else stem
        idx[name] = stem
    return idx


def region_index() -> dict[str, str]:
    """已有的州级档案：州名 → LOC-编号。重跑时复用编号，不再往后排。"""
    out: dict[str, str] = {}
    for p in LOC_DIR.glob("LOC-*.md"):
        s = p.read_text(encoding="utf-8")
        if "subtype: 州域" not in s:
            continue
        m = re.match(r"(LOC-\d{4})", p.stem)
        name = p.stem.split("_", 1)[1] if "_" in p.stem else p.stem
        if m and not name.endswith("州域"):
            out[name] = m.group(1)
    return out


def city_region() -> dict[str, str]:
    """城名 → 档案里写的「州属」。用来反向校验九州表有没有漏城。"""
    out: dict[str, str] = {}
    for p in LOC_DIR.glob("LOC-*.md"):
        s = p.read_text(encoding="utf-8")
        m = re.search(r"- \*\*州属\*\*：(.+)", s)
        if not m:
            continue
        name = p.stem.split("_", 1)[1] if "_" in p.stem else p.stem
        out[name] = m.group(1).strip()
    return out


def reconcile(regions: list[Region], declared: dict[str, str]) -> list[str]:
    """九州表没列、但档案「州属」指向某州的城，补挂上去；顺带报冲突。"""
    listed = {c for r in regions for c in r.cities}
    by_name = {r.name: r for r in regions}
    problems: list[str] = []
    for city, state in sorted(declared.items()):
        if city in listed:
            owner = next((r.name for r in regions if city in r.cities), "")
            if state and state != owner:
                problems.append(f"{city}：九州表记在「{owner}」，档案「州属」写「{state}」")
            continue
        if state in by_name:
            by_name[state].cities.append(city)
            by_name[state].extra.append(city)
        elif state:
            problems.append(f"{city}：档案「州属」写「{state}」，九州表里没有这个大区")
    return problems


def fc_region(region: Region, loc_id: str, index: dict[str, str]) -> str:
    """生成一个州级 location 档案。"""
    missing = [c for c in region.cities if c not in index]
    city_lines = []
    for c in region.cities:
        tag = "（依档案「州属」补入）" if c in region.extra else ""
        if c in index:
            city_lines.append(f"- [[{index[c]}|{c}]]{tag}")
        else:
            city_lines.append(f"- {c}（待建档）")
    city_block = "\n".join(city_lines) if city_lines else "- （九州表未列城名）"

    upper = "九州" if region.name not in OFF_MAINLAND else "九州之外"
    notes = ""
    if missing:
        notes = (
            "\n> 待建档：" + "、".join(missing)
            + "　（九州表列名，但地点库里没有对应档案）\n"
        )

    return f"""---
id: {loc_id}
type: location
name: {region.name}
title: {region.name}
aliases: []
subtype: 州域
world: 九州
canon_status: confirmed
created_at: '{TODAY}'
updated_at: '{TODAY}'
source_refs:
- 01_Core_Memory/World/九州/总表/WORLD-0029_九州表.md
import_batch: MAP-{TODAY.replace('-', '')}
tags:
- 九州
- 地图库
- 州域
- 方位{region.direction}
---

# {region.name}

> 来源：`九州-世界观资产.xlsx` · 工作表「九州表」｜由 `00_System/scripts/build_jiuzhou_map.py` 生成，**请勿手改**。
> 改设定请改 [[WORLD-0029_九州表|九州·州域志]]，再重跑脚本。

## 档案

- **层级**：{upper} › {region.name}
- **方位**：{region.direction}
- **主要地貌**：{region.terrain or '（九州表未记）'}
- **特色口语**：{region.slang or '（九州表未记）'}

## 简介

{region.intro or '（九州表未记）'}

## 下辖城邑

{city_block}
{notes}
## 关联

- **上层**：[[WORLD-0029_九州表|九州·州域志]]
- **地图**：[[九州地图|九州地图]]
"""


def build_md(regions: list[Region], index: dict[str, str]) -> str:
    order = list(REGIONS.keys())
    regions = sorted(regions, key=lambda r: order.index(r.name))

    rows = []
    for r in regions:
        cities = "、".join(r.cities[:3]) + ("…" if len(r.cities) > 3 else "")
        rows.append(f"| {r.name} | {r.direction} | {r.terrain or '—'} | {cities or '—'} | {len(r.cities)} |")
    table = "\n".join(rows)

    sections = []
    for r in regions:
        city_lines = []
        for c in r.cities:
            tag = "（依档案「州属」补入）" if c in r.extra else ""
            if c in index:
                city_lines.append(f"- [[{index[c]}|{c}]]{tag}")
            else:
                city_lines.append(f"- {c}（待建档）")
        city_block = "\n".join(city_lines) if city_lines else "- （九州表未列城名）"
        intro = r.intro or "（九州表未记）"
        sections.append(
            f"""### {r.name}（{r.direction}）

- **地貌**：{r.terrain or '—'}　**口语**：{r.slang or '—'}
- **简介**：{intro}

**城邑**

{city_block}
"""
        )

    total_cities = sum(len(r.cities) for r in regions)
    linked = sum(1 for r in regions for c in r.cities if c in index)

    return f"""---
type: map
name: 九州地图
title: 九州地图
generated_by: 00_System/scripts/build_jiuzhou_map.py
canon_status: confirmed
created_at: '{TODAY}'
updated_at: '{TODAY}'
tags:
- 九州
- 地图库
- 总图
---

# 九州地图

> 本页由 `00_System/scripts/build_jiuzhou_map.py` 从 [[WORLD-0029_九州表|九州·州域志]] 生成，**请勿手改**。
> 改设定改九州表，再重跑脚本。可视化版本见 [[九州地图.canvas|九州地图.canvas]]。

## 格局

九州大陆居中，八州环列：北凉州、东北海州、东扬州、南越州、东南湖州、西北陵州、西南戎州、西云州。
九州之外是海外十五州与三十六洞天、七十二奇地。

| 州 | 方位 | 主要地貌 | 代表城邑 | 城数 |
|---|---|---|---|---|
{table}

合计 **{len(regions)}** 个大区、**{total_cities}** 座城邑，已挂双链 **{linked}** 座。

## 分州

{chr(10).join(sections)}

## 用法

- 写场景先查本页「地貌 + 口语」，再进具体的城档案取细节。
- 想知道某地在哪，看 [[九州地图.canvas|Canvas 图]]：中州居中，其余八州按真实方位环列。
- 新州、新城先补进九州表 → 重跑脚本 → 档案与地图一起更新。

## 关联

- **数据源**：[[WORLD-0029_九州表|九州·州域志]]
- **卷一舞台**：[[LOC-0057_武安都|武安都]]（湖州）
- **地理规则**：[[WORLD-0002_三质_物质灵力魂魄|三质]]、[[WORLD-0003_三重循环_灵力总纲|三重循环]]
"""


def build_canvas(regions: list[Region], index: dict[str, str]) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []

    def text_node(nid: str, text: str, x: float, y: float, w: int, h: int, color: str) -> None:
        nodes.append(
            {
                "id": nid,
                "type": "text",
                "text": text,
                "x": x,
                "y": y,
                "width": w,
                "height": h,
                "color": color,
            }
        )

    def file_node(nid: str, rel: str, x: float, y: float, w: int = 210, h: int = 60,
                  color: str = "5") -> None:
        nodes.append(
            {
                "id": nid,
                "type": "file",
                "file": rel,
                "x": x,
                "y": y,
                "width": w,
                "height": h,
                "color": color,
            }
        )

    def edge(a: str, b: str, color: str = "2") -> None:
        edges.append(
            {
                "id": f"e-{a}-{b}",
                "fromNode": a,
                "fromSide": "bottom",
                "toNode": b,
                "toSide": "top",
                "color": color,
            }
        )

    # 中心：九州总图
    text_node("center", "# 九州\n大陆居中，八州环列", -150, -60, 300, 120, "1")

    radius = 1700
    inner = [r for r in regions if r.name not in OFF_MAINLAND and r.name != "中州"]
    for r in inner:
        _, dx, dy = REGIONS[r.name]
        cx, cy = dx * radius, dy * radius
        rid = f"region-{r.slug}"
        file_node(rid, f"01_Core_Memory/Locations/{r.loc_id}_{r.name}.md",
                  cx - 130, cy - 35, 260, 70, "4")
        edges.append(
            {
                "id": f"e-center-{rid}",
                "fromNode": "center",
                "fromSide": "bottom",
                "toNode": rid,
                "toSide": "top",
                "color": "3",
            }
        )

        # 城：沿该州向外的方向扇形铺开
        n = max(len(r.cities), 1)
        base = math.atan2(dy, dx) if (dx or dy) else 0.0
        span = min(1.5, 0.42 * n)
        for i, c in enumerate(r.cities):
            if c not in index:
                continue
            frac = 0.5 if n == 1 else i / (n - 1)
            ang = base - span / 2 + span * frac
            px = cx + math.cos(ang) * 620
            py = cy + math.sin(ang) * 620
            cid = f"city-{index[c]}"
            file_node(cid, f"01_Core_Memory/Locations/{index[c]}.md", px - 105, py - 30)
            edge(rid, cid)

    # 中州：放在中心正下方，城向下排
    mid = next((r for r in regions if r.name == "中州"), None)
    if mid:
        rid = "region-中州"
        file_node(rid, f"01_Core_Memory/Locations/{mid.loc_id}_中州.md", -130, 260, 260, 70, "4")
        edges.append(
            {"id": "e-center-region-中州", "fromNode": "center", "fromSide": "bottom",
             "toNode": rid, "toSide": "top", "color": "3"}
        )
        for i, c in enumerate(mid.cities):
            if c not in index:
                continue
            col, row = i % 3, i // 3
            px = -105 - 240 + col * 240
            py = 430 + row * 100
            cid = f"city-{index[c]}"
            file_node(cid, f"01_Core_Memory/Locations/{index[c]}.md", px, py)
            edge(rid, cid)

    # 海外与界外：右下角独立摆放
    off_y = radius + 700
    for i, r in enumerate([x for x in regions if x.name in OFF_MAINLAND]):
        rid = f"region-{r.slug}"
        bx = -400 + i * 900
        file_node(rid, f"01_Core_Memory/Locations/{r.loc_id}_{r.name}.md", bx, off_y, 300, 80, "6")
        edges.append(
            {"id": f"e-center-{rid}", "fromNode": "center", "fromSide": "right",
             "toNode": rid, "toSide": "left", "color": "6"}
        )
        for j, c in enumerate(r.cities):
            if c not in index:
                continue
            cid = f"city-{index[c]}"
            file_node(cid, f"01_Core_Memory/Locations/{index[c]}.md",
                      bx + (j % 3) * 230, off_y + 140 + (j // 3) * 90)
            edge(rid, cid)

    return {"nodes": nodes, "edges": edges}


def main() -> int:
    regions = parse_table()
    if not regions:
        print("✗ 九州表解析结果为空，检查标题格式", file=sys.stderr)
        return 1

    index = city_index()
    problems = reconcile(regions, city_region())
    MAP_DIR.mkdir(parents=True, exist_ok=True)

    # 编号：已存在的州域档案复用原编号，缺的才排新号
    known = region_index()
    existing = [int(m.group(1)) for p in LOC_DIR.glob("LOC-*.md")
                if (m := re.match(r"LOC-(\d{4})", p.name))]
    next_id = max(existing) + 1 if existing else 1

    created = []
    for r in regions:
        if r.name in known:
            r.loc_id = known[r.name]  # type: ignore[attr-defined]
        else:
            r.loc_id = f"LOC-{next_id:04d}"  # type: ignore[attr-defined]
            next_id += 1
        path = LOC_DIR / f"{r.loc_id}_{r.name}.md"  # type: ignore[attr-defined]
        path.write_text(fc_region(r, r.loc_id, index), encoding="utf-8")  # type: ignore[attr-defined]
        created.append(r.loc_id)

    (MAP_DIR / "九州地图.md").write_text(build_md(regions, index), encoding="utf-8")
    canvas = build_canvas(regions, index)
    (MAP_DIR / "九州地图.canvas").write_text(
        json.dumps(canvas, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    missing = [(r.name, c) for r in regions for c in r.cities if c not in index]
    print(f"✓ 州域档案 {len(created)} 个：{created[0]} ~ {created[-1]}")
    print(f"✓ 地图页 {MAP_DIR / '九州地图.md'}")
    print(f"✓ Canvas {len(canvas['nodes'])} 节点 / {len(canvas['edges'])} 连线")
    if missing:
        print(f"! 待建档城邑 {len(missing)} 处：{missing}")
    if problems:
        print(f"! 州属不一致 {len(problems)} 处：")
        for line in problems:
            print("   ", line)
    else:
        print("✓ 州属与九州表一致，无冲突")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
