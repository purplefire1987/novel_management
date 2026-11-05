#!/usr/bin/env python3
"""把资产库「地图库」工作表落成文本总表：`WORLD-0040_行政区划表.md`。

背景：`地图库` 表本来就有「州属 / 府属 / 郡属」三列，但一直没有落成可检索的文本，
导致「府」这一层在知识库里根本不存在——州域档案直接列城，府被压扁成城档案里的一个字段。

本脚本只做一件事：把 72 行原始数据按 州 › 府 › 郡 归拢成一张 Markdown 总表，
供 `build_jiuzhou_map.py` 解析。幂等，可反复运行。

数据缺口不猜：原表空着的格子照原样留空，并在文末「待补」里点名。
"""

from __future__ import annotations

import sys
from collections import OrderedDict
from datetime import date
from pathlib import Path

import openpyxl
import yaml

VAULT = Path(__file__).resolve().parents[2]
XLSX = VAULT / "99_Archive/Raw_Sources/九州旧稿/九州-世界观资产.xlsx"
OUT = VAULT / "01_Core_Memory/World/九州/总表/WORLD-0040_行政区划表.md"
LAYOUT_FILE = VAULT / "00_System/Config/jiuzhou_layout.yaml"
TODAY = date.today().isoformat()

# 州（大区）的排列顺序：大陆八州按方位，中州居中，其余殿后。
# 顺序属「版式」，与地图脚本共用同一份配置，避免两处排得不一样。
STATE_ORDER = list(
    (yaml.safe_load(LAYOUT_FILE.read_text(encoding="utf-8")) or {}).get("order") or []
)
if not STATE_ORDER:
    raise SystemExit(f"✗ 版式配置里没有 order：{LAYOUT_FILE.relative_to(VAULT)}")

# 原表已知的口径问题：照录，但在表内与文末点名，不改数据
ANOMALIES = {
    "平都山": "旧表「府属＝巴川府、郡属＝河内郡」与湖州巴川府／河津城河内郡重名，"
    "疑表内复制错误；本表照录，不计入湖州巴川府。",
}

EMPTY = "（原表未填）"
NO_FU = "（未分府）"

# 原表口径错误、必须逐条点名的行：州属与府属互相矛盾，且府名被别州占用
FORCE_NO_FU = {
    "平都山": "旧表「州属＝三十六洞天、府属＝巴川府」，与湖州巴川府（白帝／锦官／青崖）重名；"
    "平都山不在湖州，故不并入，按洞天直属记录。"
}


def cell(value) -> str:
    text = "" if value is None else str(value).strip()
    return text.replace("|", "\\|").replace("\n", " ")


def main() -> int:
    if not XLSX.exists():
        print(f"✗ 找不到资产表：{XLSX}", file=sys.stderr)
        return 1

    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb["地图库"]

    # state -> fu -> [郡行]
    tree: "OrderedDict[str, OrderedDict[str, list[dict]]]" = OrderedDict()
    skipped: list[str] = []
    placeholders: list[str] = []
    fu_owner: dict[str, str] = {}
    gaps: list[str] = []

    for row in ws.iter_rows(min_row=2, values_only=True):
        name = (row[0] or "").strip() if row[0] else ""
        state = (row[2] or "").strip() if row[2] else ""
        fu = (row[3] or "").strip() if row[3] else ""
        jun = (row[4] or "").strip() if row[4] else ""
        if not state:
            if name:
                skipped.append(name)
            continue
        if not name:
            # 表里有一行只填了「戎州 / 怀柔府」，没有地名：那是对府本身的占位行，
            # 不是郡，跳过（府级档案另由 build_jiuzhou_map.py 生成）。
            placeholders.append(f"{state}·{fu}")
            continue
        if not fu:
            gaps.append(f"{name}：府属未填")
        if not jun or jun == "无":
            if jun != "无":
                gaps.append(f"{name}：郡属未填")
        # 府名跨州重名（旧表把平都山挂到湖州「巴川府」名下）：
        # 首个出现的州占有该府，后来的降为「未分府」，避免两页争同一编号。
        if name in FORCE_NO_FU:
            gaps.append(f"{name}：{FORCE_NO_FU[name]}")
            fu = NO_FU
        elif fu:
            first = fu_owner.setdefault(fu, state)
            if first != state:
                gaps.append(f"{name}：府属「{fu}」与「{first}·{fu}」重名，未并入，待作者裁定")
                fu = NO_FU
        tree.setdefault(state, OrderedDict()).setdefault(fu or NO_FU, []).append(
            {
                "name": name,
                "jun": jun,
                "intro": cell(row[1]),
                "note": cell(row[5]),
                "fac": cell(row[6]),
                "char": cell(row[7]),
                "army": cell(row[8]),
            }
        )

    # 郡名跨府重复（例：河津城「河内郡」与平都山「河内郡」）：照录，但逐行点名
    jun_owner: dict[str, list[str]] = {}
    for state, fus in tree.items():
        for fu, rows in fus.items():
            for r in rows:
                if r["jun"] and r["jun"] != "无":
                    jun_owner.setdefault(r["jun"], []).append(f"{state}·{fu}")
    for state, fus in tree.items():
        for fu, rows in fus.items():
            for r in rows:
                owners = jun_owner.get(r["jun"], [])
                if len(owners) > 1:
                    others = "、".join(o for o in owners if o != f"{state}·{fu}")
                    mark = f"（郡名与 {others} 重名，待裁）"
                    r["note"] = (r["note"] + mark) if r["note"] else mark

    states = sorted(
        tree.items(),
        key=lambda kv: STATE_ORDER.index(kv[0]) if kv[0] in STATE_ORDER else len(STATE_ORDER),
    )
    unknown_state = [s for s in tree if s not in STATE_ORDER]

    total_fu = sum(1 for _, f in states for name in f if name != NO_FU)
    total_jun = sum(len(v) for _, f in states for v in f.values())

    lines: list[str] = [
        "---",
        "id: WORLD-0040",
        "type: world",
        "name: 九州·行政区划表",
        "title: 九州·行政区划表",
        "aliases:",
        "- 行政区划",
        "- 州府郡表",
        "- 地图库总表",
        "subtype: 总表",
        "world: 九州",
        "canon_status: confirmed",
        "created_at: '2026-11-05'",
        f"updated_at: '{TODAY}'",
        "source_refs:",
        "- 99_Archive/Raw_Sources/九州旧稿/九州-世界观资产.xlsx",
        "- 01_Core_Memory/World/九州/总表/WORLD-0029_九州表.md",
        "import_batch: MAPADMIN-20261105",
        "generated_by: 00_System/scripts/import_map_admin.py",
        "tags:",
        "- 九州",
        "- 总表",
        "- 行政区划",
        "- 地图库",
        "---",
        "",
        "# 九州·行政区划表",
        "",
        "> 来源：`九州-世界观资产.xlsx` · 工作表「地图库」（72 行）｜由 `00_System/scripts/import_map_admin.py` 生成，**请勿手改**。",
        "> 改设定请改原始「地图库」表，再重跑脚本；只读本页容易被下一次生成覆盖。",
        "",
        "## 层级",
        "",
        "```text",
        "九州",
        "└── 州（大区，11 个：大陆八州 + 中州 + 海外十五州 + 三十六洞天）",
        "    └── 府（一州辖二至四府）",
        "        └── 郡（郡治即城邑，一郡一城）",
        "```",
        "",
        "| 层级 | 定位 | 例 |",
        "| --- | --- | --- |",
        "| 州 | 最高地方区划，例设州牧；军管州为都护府 | 湖州（[[LOC-0078_湖州\\|湖州]]） |",
        "| 府 | 州下分府，设府尹；府城多为州治所在 | 江陵府（湖州州城所在） |",
        "| 郡 | 府下分郡，郡治即城；本库一郡一城邑档案 | 江陵郡 → [[LOC-0057_武安都\\|武安都]] |",
        "",
        f"合计 **{len(states)}** 个大区、**{total_fu}** 个府、**{total_jun}** 个郡级条目。",
        "",
    ]

    for state, fus in states:
        jun_count = sum(len(v) for v in fus.values())
        lines += [
            f"## {state}",
            "",
            f"- **府数**：{sum(1 for n in fus if n != NO_FU)}　**郡数**：{jun_count}",
            "",
        ]
        for fu, rows in fus.items():
            lines += [
                f"### {fu}",
                "",
                "| 郡 | 治所（城邑） | 简介 | 备注 | 关联 |",
                "| --- | --- | --- | --- | --- |",
            ]
            for r in rows:
                assoc = []
                if r["fac"]:
                    assoc.append(f"势力：{r['fac']}")
                if r["char"]:
                    assoc.append(f"角色：{r['char']}")
                if r["army"]:
                    assoc.append(f"军团：{r['army']}")
                lines.append(
                    "| {jun} | {name} | {intro} | {note} | {assoc} |".format(
                        jun=r["jun"] or EMPTY,
                        name=r["name"],
                        intro=r["intro"] or "—",
                        note=r["note"] or "—",
                        assoc="；".join(assoc) or "—",
                    )
                )
            lines.append("")

    lines += [
        "## 待补与存疑",
        "",
    ]
    if gaps:
        lines += ["**原表空格**（照录，不替作者猜）：", ""]
        lines += [f"- {g}" for g in gaps]
        lines.append("")
    if ANOMALIES:
        lines += ["**口径存疑**（照录，待作者裁定）：", ""]
        lines += [f"- **{k}**：{v}" for k, v in ANOMALIES.items()]
        lines.append("")
    if placeholders:
        lines += [f"- 只填了州／府、缺地名的占位行（已按府处理，不建郡）：{'、'.join(placeholders)}", ""]
    if unknown_state:
        lines += [f"- 未登记的大区：{'、'.join(unknown_state)}", ""]
    if skipped:
        lines += [f"- 缺「州属」被跳过的行：{'、'.join(skipped)}", ""]

    lines += [
        "## 用法",
        "",
        "- 查某地在哪：先按州名找 `##`，再按府名找 `###`，郡与治所同名的一行即结论。",
        "- 写作时「府」是行政与司法口径（府尹、府狱），「郡」是地理与户籍口径（郡望、郡兵），不要混用。",
        "- 本页只记层级与归属；地貌、气候、口语见 [[WORLD-0029_九州表|九州·州域志]]，城邑细节见各自 LOC 档案。",
        "",
        "## 关联",
        "",
        "- **州域志**：[[WORLD-0029_九州表|九州·州域志]]",
        "- **地图**：[[九州地图|九州地图]]、[[九州行政层级.canvas|九州行政层级.canvas]]",
        "- **官制**：[[WORLD-0027_帝国官职表|帝国官职表]]（州牧、府尹、郡丞的品级见该表）",
        "",
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ {OUT.relative_to(VAULT)}：{len(states)} 大区 / {total_fu} 府 / {total_jun} 郡")
    if gaps:
        print(f"! 原表空格 {len(gaps)} 处：{'；'.join(gaps)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
