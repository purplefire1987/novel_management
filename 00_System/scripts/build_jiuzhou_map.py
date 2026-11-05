#!/usr/bin/env python3
"""把《九州表》+《行政区划表》落成三层地理库：州 › 府 › 郡（郡治即城邑）。

两条前情：

1. 建库时 `01_Core_Memory/Maps/` 只有 `.gitkeep`，九州表里「关联：地图库」那一行
   从没被解析成层级，地图一直是空的；上一版脚本只做到「州 → 城」两层。
2. 资产库「地图库」表本来就有「府属 / 郡属」两列，但「府」从来没成为实体，
   只被压成城档案里的一个字段。三层一压扁，写作时就分不清「一府辖几县」
   「府城与州城是否同城」，也查不到「江陵郡在哪」。

本脚本做五件事，全部幂等：

1. 州域档案（LOC-0073~0083）：改成「府 → 郡 → 治所」三层表；
2. 府级档案（新建）：一府一页，上下双链；
3. 城邑档案（LOC-0001~0071）：补「层级」行，州属／府属改为双链；
4. `Maps/九州地图.md`：按州 → 府 → 郡三级铺开；
5. `Maps/九州地图.canvas`（方位图）+ `Maps/九州行政层级.canvas`（层级树）。

数据源：州的地貌/口语/简介取 WORLD-0029，府郡归属取 WORLD-0040。
两页都是生成的，不要手改；改源头再重跑。
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

VAULT = Path(__file__).resolve().parents[2]
WORLD_TABLE = VAULT / "01_Core_Memory/World/九州/总表/WORLD-0029_九州表.md"
ADMIN_TABLE = VAULT / "01_Core_Memory/World/九州/总表/WORLD-0040_行政区划表.md"
LOC_DIR = VAULT / "01_Core_Memory/Locations"
MAP_DIR = VAULT / "01_Core_Memory/Maps"
LAYOUT_FILE = VAULT / "00_System/Config/jiuzhou_layout.yaml"
TODAY = date.today().isoformat()


def load_layout() -> dict:
    """读「版式」配置：大区顺序、方位、Canvas 偏移、是否在大陆上。

    这些都是**排版**，不是设定；写死在本文件里时，加一个州要改代码、
    改方位要改代码，而且和 WORLD-0029/0040 的顺序很容易对不上。
    """
    if not LAYOUT_FILE.exists():
        raise SystemExit(f"✗ 缺少版式配置：{LAYOUT_FILE.relative_to(VAULT)}")
    return yaml.safe_load(LAYOUT_FILE.read_text(encoding="utf-8")) or {}


LAYOUT = load_layout()
_REGION_CFG: dict[str, dict] = LAYOUT.get("regions") or {}

# 大区名 → (方位, dx, dy)；dx/dy 用于 Canvas 方位图
REGIONS: dict[str, tuple[str, float, float]] = {
    name: (cfg.get("direction", ""), float(cfg.get("dx", 0.0)), float(cfg.get("dy", 0.0)))
    for name, cfg in _REGION_CFG.items()
}
ORDER: list[str] = list(LAYOUT.get("order") or REGIONS.keys())
OFF_MAINLAND = {name for name, cfg in _REGION_CFG.items() if not cfg.get("mainland", True)}
NO_FU = LAYOUT.get("no_fu", "（未分府）")


@dataclass
class Jun:
    name: str
    seat: str
    intro: str = ""
    note: str = ""
    assoc: str = ""
    loc_id: str = ""       # 治所城邑档案编号
    missing: bool = False  # 治所尚无档案


@dataclass
class Fu:
    name: str
    state: str
    jun: list["Jun"] = field(default_factory=list)
    loc_id: str = ""


@dataclass
class Region:
    name: str
    terrain: str = ""
    intro: str = ""
    slang: str = ""
    fu: list["Fu"] = field(default_factory=list)
    direct: list["Jun"] = field(default_factory=list)   # 不分府的大区：郡／治所直属
    loc_id: str = ""

    @property
    def direction(self) -> str:
        return REGIONS.get(self.name, ("", 0.0, 0.0))[0]

    @property
    def slug(self) -> str:
        return self.name.replace("，", "·").replace(" ", "")

    @property
    def jun_count(self) -> int:
        return sum(len(f.jun) for f in self.fu) + len(self.direct)


# ------------------------------------------------------------------ 解析
def parse_world_table() -> dict[str, Region]:
    """WORLD-0029：州的地貌／简介／口语。"""
    text = WORLD_TABLE.read_text(encoding="utf-8")
    body = text.split("---", 2)[-1]
    out: dict[str, Region] = {}
    current: Region | None = None
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            name = line[3:].strip()
            current = out.setdefault(name, Region(name=name))
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
    return {k: v for k, v in out.items() if k in REGIONS}


def parse_admin_table() -> dict[str, Region]:
    """WORLD-0040：州 › 府 › 郡（郡治即城邑）。"""
    text = ADMIN_TABLE.read_text(encoding="utf-8")
    body = text.split("---", 2)[-1]
    out: dict[str, Region] = {}
    state: Region | None = None
    fu: Fu | None = None
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            state = out.setdefault(line[3:].strip(), Region(name=line[3:].strip()))
            fu = None
            continue
        if line.startswith("### "):
            if state is None:
                continue
            name = line[4:].strip()
            fu = Fu(name="" if name == NO_FU else name, state=state.name)
            state.fu.append(fu)
            continue
        if not line.startswith("|") or state is None or fu is None:
            continue
        cells = [c.strip().replace("\\|", "|") for c in line.strip("|").split("|")]
        if len(cells) < 5 or cells[0] in ("郡", "---"):
            continue
        jun = Jun(name=cells[0], seat=cells[1], intro=cells[2], note=cells[3], assoc=cells[4])
        if jun.name.startswith("（原表未填") or jun.name in ("无", "—", "-"):
            jun.name = ""
        fu.jun.append(jun)
    regions = {k: v for k, v in out.items() if k in REGIONS}
    apply_fixups(regions)
    return regions


def apply_fixups(regions: dict[str, Region]) -> None:
    """原表口径冲突的修正：不丢数据、不静默改写，降级为「州直属」并保留说明。

    已知一处：旧「地图库」把平都山（三十六洞天）的府属填成「巴川府」，
    与湖州的巴川府同名。若照抄，两页会争同一个编号、互相覆盖，
    还会让读者以为平都山在湖州。故把它从府里摘出来，挂成三十六洞天直属。
    """
    for region in regions.values():
        for fu in list(region.fu):
            if not fu.name:
                region.fu.remove(fu)
                region.direct.extend(fu.jun)

    owner: dict[str, str] = {}
    for state, region in regions.items():
        for fu in region.fu:
            owner.setdefault(fu.name, state)
    for state, region in list(regions.items()):
        for fu in list(region.fu):
            if owner.get(fu.name) != state:
                region.fu.remove(fu)
                for jun in fu.jun:
                    jun.note = (jun.note + "｜" if jun.note and jun.note != "—" else "") + (
                        f"旧表府属记「{fu.name}」，与「{owner[fu.name]}·{fu.name}」重名，已降为州直属，待作者裁定"
                    )
                    region.direct.append(jun)


def loc_files() -> dict[str, Path]:
    return {p.stem: p for p in LOC_DIR.glob("LOC-*.md")}


def name_to_stem(stems: dict[str, Path]) -> dict[str, str]:
    out: dict[str, str] = {}
    for stem in stems:
        out[stem.split("_", 1)[1] if "_" in stem else stem] = stem
    return out


def subtype_of(path: Path) -> str:
    m = re.search(r"^subtype:\s*(.+)$", path.read_text(encoding="utf-8"), re.M)
    return m.group(1).strip() if m else ""


def allocate_fu(wanted: list[str], stems: dict[str, Path]) -> dict[str, str]:
    """给府分配编号：已有府档案复用编号，其余按现有最大号往后排。"""
    existing: dict[str, str] = {}
    serials: list[int] = []
    for stem, path in stems.items():
        m = re.match(r"LOC-(\d{4})", stem)
        if m:
            serials.append(int(m.group(1)))
        if sub := subtype_of(path):
            name = path.stem.split("_", 1)[1]
            if sub == "府" or (sub == "地点" and name.endswith("府")):
                existing[name] = m.group(0) if m else ""

    next_id = (max(serials) if serials else 0) + 1
    for name in wanted:
        if name not in existing:
            existing[name] = f"LOC-{next_id:04d}"
            next_id += 1
    return existing


def link(loc_id: str, name: str) -> str:
    return f"[[{loc_id}_{name}|{name}]]" if loc_id else name


# ------------------------------------------------------------------ 生成：州
def render_region(region: Region) -> str:
    upper = "九州" if region.name not in OFF_MAINLAND else "九州之外"
    rows = []
    for fu in region.fu:
        first = True
        for jun in fu.jun:
            fu_cell = link(fu.loc_id, fu.name) if first else ""
            seat = link(jun.loc_id, jun.seat) if jun.loc_id else f"{jun.seat}（待建档）"
            rows.append(f"| {fu_cell} | {jun.name or '—'} | {seat} |")
            first = False
        if not fu.jun:
            rows.append(f"| {link(fu.loc_id, fu.name)} | — | — |")
    for jun in region.direct:
        seat = link(jun.loc_id, jun.seat) if jun.loc_id else f"{jun.seat}（待建档）"
        rows.append(f"| （直属·不分府） | {jun.name or '—'} | {seat} |")
    table = "\n".join(rows) if rows else "| — | — | — |"

    fu_list = [
        f"- {link(f.loc_id, f.name)}（{len(f.jun)} 郡：{'、'.join(j.name or j.seat for j in f.jun) or '待补'}）"
        for f in region.fu
    ]
    if region.direct:
        fu_list.append(
            "- **州直属（不分府）**：" + "、".join(
                (link(j.loc_id, j.seat) if j.loc_id else j.seat) + (f"（{j.name}）" if j.name else "")
                for j in region.direct
            )
        )
    fu_block = "\n".join(fu_list) if fu_list else "- （本大区不分府：海外／界外自理）"

    return f"""---
id: {region.loc_id}
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
- 01_Core_Memory/World/九州/总表/WORLD-0040_行政区划表.md
import_batch: MAP-20261105
generated_by: 00_System/scripts/build_jiuzhou_map.py
tags:
- 九州
- 地图库
- 州域
- 方位{region.direction}
---

# {region.name}

> 来源：`九州-世界观资产.xlsx` · 工作表「九州表」「地图库」｜由 `00_System/scripts/build_jiuzhou_map.py` 生成，**请勿手改**。
> 改设定请改 [[WORLD-0029_九州表|九州·州域志]] 与 [[WORLD-0040_行政区划表|九州·行政区划表]]，再重跑脚本。

## 档案

- **层级**：{upper} › {region.name}
- **方位**：{region.direction}
- **主要地貌**：{region.terrain or '（九州表未记）'}
- **特色口语**：{region.slang or '（九州表未记）'}
- **下辖**：{len(region.fu)} 府 / {region.jun_count} 郡

## 简介

{region.intro or '（九州表未记）'}

## 下辖府郡

| 府 | 郡 | 治所（城邑） |
| --- | --- | --- |
{table}

## 下辖府

{fu_block}

## 关联

- **上层**：[[WORLD-0029_九州表|九州·州域志]]、[[WORLD-0040_行政区划表|九州·行政区划表]]
- **地图**：[[九州地图|九州地图]]、[[九州行政层级.canvas|九州行政层级]]
"""


# ------------------------------------------------------------------ 生成：府
def render_fu(fu: Fu, region: Region) -> str:
    rows = []
    for jun in fu.jun:
        seat = link(jun.loc_id, jun.seat) if jun.loc_id else f"{jun.seat}（待建档）"
        intro = jun.intro or "—"
        note = jun.note or "—"
        rows.append(
            f"| {jun.name or '—'} | {seat} | {intro[:120]}{'…' if len(intro) > 120 else ''} "
            f"| {note[:80]}{'…' if len(note) > 80 else ''} |"
        )
    table = "\n".join(rows) if rows else "| — | — | — | — |"
    seats = "、".join(j.seat for j in fu.jun) or "待补"

    notes: list[str] = []
    if fu.name == "巴川府":
        notes.append(
            "> **重名说明**：旧「地图库」表另把 [[LOC-0033_平都山|平都山]]（三十六洞天）的府属填作「巴川府」、\n"
            "> 郡属填作「河内郡」，与湖州巴川府、河津城河内郡重名。本页只纳湖州三郡；\n"
            "> 平都山是否属本府**待作者裁定**，未擅自挂入。"
        )
    if fu.name == "怀柔府":
        notes.append(
            "> **补记**：本页 2026-11-03 先以「府级·地名待定」建页——原表这一行只写了「戎州 / 怀柔府」，\n"
            "> 没有地名，导入器按规则跳过，作者指示「先进行记录」，故以府为单位单建一页，避免这一层行政信息丢失。\n"
            "> 2026-11-05 归入州 › 府 › 郡 三层体系，郡属补为「苍梧郡」。\n"
            "> **仍未定**：怀柔府是否为「府」这一级的正式地名、府治设在哪座城。\n"
            "> 补全时**不要改名本页**，另建正式地名条目并在本页「下辖郡县」追加即可。"
        )
    note_block = ("\n".join(notes) + "\n\n") if notes else ""

    return f"""---
id: {fu.loc_id}
type: location
name: {fu.name}
title: {fu.name}
aliases:
- {region.name}·{fu.name}
subtype: 府
world: 九州
canon_status: confirmed
created_at: '{TODAY}'
updated_at: '{TODAY}'
source_refs:
- 01_Core_Memory/World/九州/总表/WORLD-0040_行政区划表.md
import_batch: MAP-20261105
generated_by: 00_System/scripts/build_jiuzhou_map.py
tags:
- 九州
- 地图库
- 府级
- {region.name}
---

# {fu.name}

> 来源：`九州-世界观资产.xlsx` · 工作表「地图库」｜由 `00_System/scripts/build_jiuzhou_map.py` 生成，**请勿手改**。
> 改设定请改 [[WORLD-0040_行政区划表|九州·行政区划表]]，再重跑脚本。

## 档案

- **层级**：九州 › {link(region.loc_id, region.name)} › {fu.name}
- **州属**：{link(region.loc_id, region.name)}（{region.direction}）
- **下辖**：{len(fu.jun)} 郡（{seats}）
- **府治**：{fu_seat_note(fu, region)}

{note_block}## 下辖郡县

| 郡 | 治所（城邑） | 简介 | 备注 |
| --- | --- | --- | --- |
{table}

## 关联

- **上层**：{link(region.loc_id, region.name)}（州）
- **同级府**：{'、'.join(link(f.loc_id, f.name) for f in region.fu if f.name != fu.name) or '—'}
- **行政区划总表**：[[WORLD-0040_行政区划表|九州·行政区划表]]
- **地图**：[[九州地图|九州地图]]、[[九州行政层级.canvas|九州行政层级]]
"""


def fu_seat_note(fu: Fu, region: Region) -> str:
    """府治：州城所在府则与州城同城，其余原表未记。"""
    # 只有湖州这一处有正文依据（「武安即是江陵府的府城，也是湖州的州城」），其余不猜
    state_city = {"湖州": "武安都"}.get(region.name)
    seats = [j.seat for j in fu.jun]
    if state_city and state_city in seats:
        return f"与州城同城（{state_city}）"
    return "（原表未记，待作者定）"


# ------------------------------------------------------------------ 生成：城
def add_alias(text: str, alias: str) -> str:
    """把郡名写进 aliases，让「查江陵郡」能落到郡治那一页。已有则不动。"""
    if re.search(rf"^- {re.escape(alias)}$", text, re.M):
        return text
    m = re.search(r"^aliases:[ \t]*(\[\])?[ \t]*$", text, re.M)
    if not m:
        return text
    if m.group(1):                      # aliases: []
        return text[: m.start()] + f"aliases:\n- {alias}" + text[m.end():]
    lines = text.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("aliases:"))
    insert_at = start + 1
    while insert_at < len(lines) and lines[insert_at].startswith("- "):
        insert_at += 1
    lines.insert(insert_at, f"- {alias}")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def update_city(path: Path, region: Region, fu: Fu | None, jun: Jun, alias: str = "") -> bool:
    """给城邑档案补「层级」行、把州属／府属改成双链、郡名入别名。返回是否改动。"""
    text = path.read_text(encoding="utf-8")
    if "## 档案" not in text:
        return False
    original = text
    if alias:
        text = add_alias(text, alias)

    chain = ["九州" if region.name not in OFF_MAINLAND else "九州之外", link(region.loc_id, region.name)]
    if fu is not None:
        chain.append(link(fu.loc_id, fu.name))
    if jun.name:
        chain.append(jun.name)

    new_lines = [f"- **层级**：{' › '.join(chain)}", f"- **州属**：{link(region.loc_id, region.name)}"]
    if fu is not None:
        new_lines.append(f"- **府属**：{link(fu.loc_id, fu.name)}")
    if jun.name:
        new_lines.append(f"- **郡属**：{jun.name}（郡治即本城）")

    text = re.sub(r"^- \*\*(层级|州属|府属|郡属)\*\*：.*\n?", "", text, flags=re.M)
    if "## 档案\n\n" in text:
        text = text.replace("## 档案\n\n", "## 档案\n\n" + "\n".join(new_lines) + "\n", 1)
    else:
        text = text.replace("## 档案\n", "## 档案\n\n" + "\n".join(new_lines) + "\n", 1)

    if fu is not None:
        upper = f"- **上级**：{link(fu.loc_id, fu.name)}（府）／{link(region.loc_id, region.name)}（州）"
    else:
        upper = f"- **上级**：{link(region.loc_id, region.name)}（州域）"

    if "- **上级**：" in text:
        text = re.sub(r"^- \*\*上级\*\*：.*$", upper, text, count=1, flags=re.M)
    elif "## 关联" in text:
        text = text.replace("## 关联\n\n", f"## 关联\n\n{upper}\n", 1)
    else:
        text = text.rstrip() + f"\n\n## 关联\n\n{upper}\n"

    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


# ------------------------------------------------------------------ 生成：地图页
def build_md(regions: list[Region]) -> str:
    order = ORDER
    regions = sorted(regions, key=lambda r: order.index(r.name))

    rows = []
    for r in regions:
        fu_names = "、".join(f.name for f in r.fu) or "—"
        rows.append(f"| {link(r.loc_id, r.name)} | {r.direction} | {fu_names} | {len(r.fu)} | {r.jun_count} |")
    table = "\n".join(rows)

    sections = []
    for r in regions:
        blocks = []
        for fu in r.fu:
            jun_lines = []
            for jun in fu.jun:
                seat = link(jun.loc_id, jun.seat) if jun.loc_id else f"{jun.seat}（待建档）"
                jun_lines.append(f"  - {jun.name or '—'} → {seat}")
            blocks.append(f"- {link(fu.loc_id, fu.name)}\n" + ("\n".join(jun_lines) if jun_lines else "  - （郡待补）"))
        for jun in r.direct:
            seat = link(jun.loc_id, jun.seat) if jun.loc_id else f"{jun.seat}（待建档）"
            blocks.append(f"- （直属·不分府）{jun.name or '—'} → {seat}")
        block = "\n".join(blocks) if blocks else "- （不分府：海外／界外自理）"
        sections.append(
            f"""### {r.name}（{r.direction}）

- **地貌**：{r.terrain or '—'}　**口语**：{r.slang or '—'}
- **府郡**：{len(r.fu)} 府 / {r.jun_count} 郡

{block}
"""
        )

    total_fu = sum(len(r.fu) for r in regions)
    total_jun = sum(r.jun_count for r in regions)
    missing = [(r.name, f.name, j.seat) for r in regions for f in r.fu for j in f.jun if not j.loc_id]

    miss_block = ""
    if missing:
        miss_block = "\n> 待建档治所：" + "、".join(f"{s}（{st}·{fu}）" for st, fu, s in missing) + "\n"

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

> 本页由 `00_System/scripts/build_jiuzhou_map.py` 从 [[WORLD-0029_九州表|九州·州域志]] 与
> [[WORLD-0040_行政区划表|九州·行政区划表]] 生成，**请勿手改**。改设定改两张表，再重跑脚本。
> 方位图见 [[九州地图.canvas|九州地图.canvas]]，层级树见 [[九州行政层级.canvas|九州行政层级.canvas]]。

## 格局

九州大陆居中，八州环列：北凉州、东北海州、东扬州、南越州、东南湖州、西北陵州、西南戎州、西云州。
九州之外是海外十五州与三十六洞天、七十二奇地。

| 州 | 方位 | 所辖府 | 府数 | 郡数 |
| --- | --- | --- | --- | --- |
{table}

合计 **{len(regions)}** 个大区、**{total_fu}** 个府、**{total_jun}** 个郡级条目。
层级：**九州 › 州 › 府 › 郡（郡治即城邑）**；一郡一城，故郡数＝城邑档案数。
{miss_block}
## 分州

{chr(10).join(sections)}

## 用法

- 写场景先查本页「地貌 + 口语」，再进具体的城档案取细节。
- 行政口径：**州 → 府 → 郡**。府设府尹、管刑名钱粮；郡是户籍与地理单位，郡治即城。
- 州城与府城常常同城（例：[[LOC-0057_武安都|武安都]] 既是江陵府府城，也是湖州州城），
  写「进城」「过府」「下郡」时不要当成三个地方。
- 方位看 [[九州地图.canvas|方位图]]；层级看 [[九州行政层级.canvas|行政层级图]]。
- 新州、新府、新城先补进 [[WORLD-0040_行政区划表|行政区划表]] → 重跑脚本 → 档案与地图一起更新。

## 关联

- **数据源**：[[WORLD-0029_九州表|九州·州域志]]、[[WORLD-0040_行政区划表|九州·行政区划表]]
- **卷一舞台**：[[LOC-0057_武安都|武安都]]（湖州 › 江陵府 › 江陵郡）
- **地理规则**：[[WORLD-0002_三质_物质灵力魂魄|三质]]、[[WORLD-0003_三重循环_灵力总纲|三重循环]]
"""


# ------------------------------------------------------------------ Canvas
def build_canvas(regions: list[Region]) -> dict:
    """方位图：州居中环列，府贴州向外，城贴府。"""
    nodes: list[dict] = []
    edges: list[dict] = []

    def node(nid: str, rel: str, x: float, y: float, w: int, h: int, color: str = "") -> None:
        item = {"id": nid, "type": "file", "file": rel, "x": x, "y": y, "width": w, "height": h}
        if color:
            item["color"] = color
        nodes.append(item)

    def text_node(nid: str, text: str, x: float, y: float, w: int, h: int, color: str) -> None:
        nodes.append({"id": nid, "type": "text", "text": text, "x": x, "y": y,
                      "width": w, "height": h, "color": color})

    def edge(a: str, b: str, color: str = "2") -> None:
        edges.append({"id": f"e-{a}-{b}", "fromNode": a, "fromSide": "bottom",
                      "toNode": b, "toSide": "top", "color": color})

    text_node("center", "# 九州\n大陆居中，八州环列", -150, -60, 300, 120, "1")

    radius = 2200
    inner = [r for r in regions if r.name not in OFF_MAINLAND and r.name != "中州"]
    for r in inner:
        _, dx, dy = REGIONS[r.name]
        cx, cy = dx * radius, dy * radius
        rid = f"region-{r.slug}"
        node(rid, f"01_Core_Memory/Locations/{r.loc_id}_{r.name}.md", cx - 130, cy - 35, 260, 70, "4")
        edges.append({"id": f"e-center-{rid}", "fromNode": "center", "fromSide": "bottom",
                      "toNode": rid, "toSide": "top", "color": "3"})

        base = math.atan2(dy, dx) if (dx or dy) else 0.0
        n = max(len(r.fu), 1)
        span = min(1.7, 0.5 * n)
        for i, fu in enumerate(r.fu):
            frac = 0.5 if n == 1 else i / (n - 1)
            ang = base - span / 2 + span * frac
            fx, fy = cx + math.cos(ang) * 700, cy + math.sin(ang) * 700
            fid = f"fu-{fu.loc_id}"
            node(fid, f"01_Core_Memory/Locations/{fu.loc_id}_{fu.name}.md", fx - 105, fy - 28, 210, 56, "5")
            edge(rid, fid)

            m = max(len(fu.jun), 1)
            for j, jun in enumerate(fu.jun):
                if not jun.loc_id:
                    continue
                jf = 0.5 if m == 1 else j / (m - 1)
                jspan = min(1.1, 0.36 * m)
                jang = ang - jspan / 2 + jspan * jf
                jx, jy = fx + math.cos(jang) * 380, fy + math.sin(jang) * 380
                cid = f"city-{jun.loc_id}"
                node(cid, f"01_Core_Memory/Locations/{jun.loc_id}.md", jx - 90, jy - 24, 180, 48)
                edge(fid, cid)

    mid = next((r for r in regions if r.name == "中州"), None)
    if mid:
        rid = "region-中州"
        node(rid, f"01_Core_Memory/Locations/{mid.loc_id}_中州.md", -130, 300, 260, 70, "4")
        edges.append({"id": "e-center-region-中州", "fromNode": "center", "fromSide": "bottom",
                      "toNode": rid, "toSide": "top", "color": "3"})
        for i, fu in enumerate(mid.fu):
            fx, fy = -520 + i * 420, 470
            fid = f"fu-{fu.loc_id}"
            node(fid, f"01_Core_Memory/Locations/{fu.loc_id}_{fu.name}.md", fx, fy, 210, 56, "5")
            edge(rid, fid)
            for j, jun in enumerate(fu.jun):
                if not jun.loc_id:
                    continue
                cid = f"city-{jun.loc_id}"
                node(cid, f"01_Core_Memory/Locations/{jun.loc_id}.md",
                     fx + (j % 2) * 200, fy + 100 + (j // 2) * 80, 180, 48)
                edge(fid, cid)

    off_y = radius + 900
    for i, r in enumerate([x for x in regions if x.name in OFF_MAINLAND]):
        rid = f"region-{r.slug}"
        bx = -700 + i * 1200
        node(rid, f"01_Core_Memory/Locations/{r.loc_id}_{r.name}.md", bx, off_y, 300, 80, "6")
        edges.append({"id": f"e-center-{rid}", "fromNode": "center", "fromSide": "right",
                      "toNode": rid, "toSide": "left", "color": "6"})
        for k, fu in enumerate(r.fu):
            fid = f"fu-{fu.loc_id}"
            node(fid, f"01_Core_Memory/Locations/{fu.loc_id}_{fu.name}.md",
                 bx, off_y + 130 + k * 80, 240, 56, "5")
            edge(rid, fid)
            for j, jun in enumerate(fu.jun):
                if not jun.loc_id:
                    continue
                cid = f"city-{jun.loc_id}"
                node(cid, f"01_Core_Memory/Locations/{jun.loc_id}.md",
                     bx + 320 + j * 200, off_y + 130 + k * 80 + 4, 180, 48)
                edge(fid, cid)
    return {"nodes": nodes, "edges": edges}


def build_tree_canvas(regions: list[Region]) -> dict:
    """层级树：左州、中府、右郡（治所）。纯行政树，不表方位。"""
    nodes: list[dict] = []
    edges: list[dict] = []

    def node(nid: str, rel: str, x: float, y: float, w: int, h: int, color: str = "") -> None:
        item = {"id": nid, "type": "file", "file": rel, "x": x, "y": y, "width": w, "height": h}
        if color:
            item["color"] = color
        nodes.append(item)

    def text_node(nid: str, text: str, x: float, y: float, w: int, h: int, color: str) -> None:
        nodes.append({"id": nid, "type": "text", "text": text, "x": x, "y": y,
                      "width": w, "height": h, "color": color})

    def edge(a: str, b: str, color: str) -> None:
        edges.append({"id": f"e-{a}-{b}", "fromNode": a, "fromSide": "right",
                      "toNode": b, "toSide": "left", "color": color})

    X_STATE, X_FU, X_JUN = 0, 560, 1120
    W_STATE, H_STATE = 240, 60
    W_FU, H_FU = 220, 56
    W_JUN, H_JUN = 210, 46
    GAP_FU, GAP_JUN, GAP_STATE = 90, 34, 140

    text_node("root", "# 九州\n州 › 府 › 郡", -600, 0, 320, 120, "1")

    y = 0
    for r in regions:
        blocks: list[tuple[object, list[Jun]]] = [(fu, fu.jun) for fu in r.fu]
        if r.direct:
            blocks.append((None, r.direct))
        total = sum(max(len(j), 1) * (H_JUN + GAP_JUN) + GAP_FU for _, j in blocks)
        total = max(total, H_STATE + 60)

        sid = f"state-{r.loc_id}"
        node(sid, f"01_Core_Memory/Locations/{r.loc_id}_{r.name}.md",
             X_STATE, y + total / 2 - H_STATE / 2, W_STATE, H_STATE, "4")
        edge("root", sid, "3")

        fy = y
        for fu, jun in blocks:
            bh = max(len(jun), 1) * (H_JUN + GAP_JUN)
            if fu is None:
                for i, j in enumerate(jun):
                    if not j.loc_id:
                        continue
                    node(f"city-{j.loc_id}", f"01_Core_Memory/Locations/{j.loc_id}.md",
                         X_FU + 60, fy + i * (H_JUN + GAP_JUN), W_JUN, H_JUN, "6")
                    edge(sid, f"city-{j.loc_id}", "6")
                fy += bh + GAP_FU
                continue
            fid = f"fu-{fu.loc_id}"
            node(fid, f"01_Core_Memory/Locations/{fu.loc_id}_{fu.name}.md",
                 X_FU, fy + bh / 2 - H_FU / 2, W_FU, H_FU, "5")
            edge(sid, fid, "2")
            for i, j in enumerate(jun):
                if not j.loc_id:
                    continue
                node(f"city-{j.loc_id}", f"01_Core_Memory/Locations/{j.loc_id}.md",
                     X_JUN, fy + i * (H_JUN + GAP_JUN), W_JUN, H_JUN)
                edge(fid, f"city-{j.loc_id}", "5")
            fy += bh + GAP_FU
        y += total + GAP_STATE

    return {"nodes": nodes, "edges": edges}


# ------------------------------------------------------------------ main
def normalize_aliases() -> int:
    """修掉早期生成留下的 `aliases: ` 尾空格，保证 frontmatter 干净。"""
    fixed = 0
    for path in LOC_DIR.glob("LOC-*.md"):
        text = path.read_text(encoding="utf-8")
        new = re.sub(r"^aliases:[ \t]+$", "aliases:", text, flags=re.M)
        if new != text:
            path.write_text(new, encoding="utf-8")
            fixed += 1
    return fixed


def main() -> int:
    regions_map = parse_world_table()
    admin = parse_admin_table()
    if not admin:
        print("✗ 行政区划表解析结果为空，检查 WORLD-0040 标题格式", file=sys.stderr)
        return 1

    stems = loc_files()
    by_name = name_to_stem(stems)

    state_ids: dict[str, str] = {}
    for stem, path in stems.items():
        if subtype_of(path) == "州域":
            m = re.match(r"LOC-(\d{4})", stem)
            if m:
                state_ids[path.stem.split("_", 1)[1]] = m.group(0)

    fu_names: list[str] = []
    for r in admin.values():
        for fu in r.fu:
            if fu.name and fu.name not in fu_names:
                fu_names.append(fu.name)
    fu_ids = allocate_fu(fu_names, stems)

    order = ORDER
    regions: list[Region] = []
    for name in sorted(admin.keys(), key=lambda n: order.index(n) if n in order else 99):
        r = admin[name]
        src = regions_map.get(name, Region(name))
        r.terrain, r.intro, r.slang = src.terrain, src.intro, src.slang
        r.loc_id = state_ids.get(name, "")
        def resolve(jun: Jun) -> None:
            if "（府级·地名待填）" in jun.seat:   # 原表缺地名的那一行，指代府本身
                jun.loc_id, jun.missing = "", False
                return
            stem = by_name.get(jun.seat, "")
            m = re.match(r"(LOC-\d{4})", stem)
            jun.loc_id = m.group(0) if m else ""
            jun.missing = not jun.loc_id

        for fu in r.fu:
            fu.loc_id = fu_ids.get(fu.name, "")
            for jun in fu.jun:
                resolve(jun)
        for jun in r.direct:
            resolve(jun)
        regions.append(r)

    missing_state = [r.name for r in regions if not r.loc_id]
    if missing_state:
        print(f"! 缺州域档案：{missing_state}")

    MAP_DIR.mkdir(parents=True, exist_ok=True)
    normalize_aliases()

    written: list[str] = []
    for r in regions:
        if r.loc_id:
            (LOC_DIR / f"{r.loc_id}_{r.name}.md").write_text(render_region(r), encoding="utf-8")
            written.append(f"{r.loc_id} {r.name}")

    fu_written: list[str] = []
    for r in regions:
        for fu in r.fu:
            if not fu.loc_id:
                continue
            (LOC_DIR / f"{fu.loc_id}_{fu.name}.md").write_text(render_fu(fu, r), encoding="utf-8")
            fu_written.append(f"{fu.loc_id} {fu.name}")

    # 郡名唯一才写进别名：重名的郡（河内郡）不写，避免一条名字挂两处
    dup: dict[str, int] = {}
    for r in regions:
        for fu in r.fu:
            for j in fu.jun:
                if j.name:
                    dup[j.name] = dup.get(j.name, 0) + 1
        for j in r.direct:
            if j.name:
                dup[j.name] = dup.get(j.name, 0) + 1

    def alias_for(j: Jun) -> str:
        return j.name if j.name and dup.get(j.name, 0) == 1 and j.name != j.seat else ""

    # 郡名唯一才写进别名：重名的郡（河内郡）不写，避免一条名字挂两处
    dup: dict[str, int] = {}
    for r in regions:
        for fu in r.fu:
            for j in fu.jun:
                if j.name:
                    dup[j.name] = dup.get(j.name, 0) + 1
        for j in r.direct:
            if j.name:
                dup[j.name] = dup.get(j.name, 0) + 1

    def alias_for(j: Jun) -> str:
        return j.name if j.name and dup.get(j.name, 0) == 1 and j.name != j.seat else ""

    touched = 0
    for r in regions:
        for fu in r.fu:
            for jun in fu.jun:
                if not jun.loc_id:
                    continue
                path = stems.get(by_name.get(jun.seat, ""))
                if path and update_city(path, r, fu, jun, alias_for(jun)):
                    touched += 1
        for jun in r.direct:
            if not jun.loc_id:
                continue
            path = stems.get(by_name.get(jun.seat, ""))
            if path and update_city(path, r, None, jun, alias_for(jun)):
                touched += 1

    (MAP_DIR / "九州地图.md").write_text(build_md(regions), encoding="utf-8")

    geo = build_canvas(regions)
    (MAP_DIR / "九州地图.canvas").write_text(
        json.dumps(geo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tree = build_tree_canvas(regions)
    (MAP_DIR / "九州行政层级.canvas").write_text(
        json.dumps(tree, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    missing = [f"{f.name}/{j.seat}" for r in regions for f in r.fu for j in f.jun if not j.loc_id]
    missing += [f"（直属）{j.seat}" for r in regions for j in r.direct if not j.loc_id]
    print(f"✓ 州域档案 {len(written)}：{written[0]} … {written[-1]}")
    print(f"✓ 府级档案 {len(fu_written)}：{fu_written[0]} … {fu_written[-1]}")
    print(f"✓ 城邑档案补层级 {touched} 个")
    print(f"✓ 地图页 {MAP_DIR / '九州地图.md'}")
    print(f"✓ 方位图 {len(geo['nodes'])} 节点 / {len(geo['edges'])} 连线")
    print(f"✓ 层级树 {len(tree['nodes'])} 节点 / {len(tree['edges'])} 连线")
    if missing:
        print(f"! 待建档治所 {len(missing)}：{missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
