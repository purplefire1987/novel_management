#!/usr/bin/env python3
"""把资产库「地图库」工作表落成文本总表：`WORLD-0040_行政区划表.md`。

背景：`地图库` 表本来就有「州属 / 府属 / 郡属」三列，但一直没有落成可检索的文本，
导致「府」这一层在知识库里根本不存在——州域档案直接列城，府被压扁成城档案里的一个字段。

本脚本只做一件事：把 72 行原始数据按 州 › 府 › 郡 归拢成一张 Markdown 总表，
供 `build_jiuzhou_map.py` 解析。幂等，可反复运行。

数据缺口不猜：原表空着的格子照原样留空，并在文末「待补」里点名。
"""

from __future__ import annotations

import re
import sys
from collections import OrderedDict
from datetime import date
from pathlib import Path

import openpyxl
import yaml

# 路径由 configure() 注入，唯一来源是 `maps.MapPaths`。
VAULT: Path
XLSX: Path
OUT: Path
LAYOUT_FILE: Path
TODAY = date.today().isoformat()

#: 与 jiuzhou.py 同义：默认预演，--confirm 才写。
DRY_RUN = True


def configure(paths: "MapPaths") -> None:
    global VAULT, XLSX, OUT, LAYOUT_FILE, STATE_ORDER
    global NON_ADMIN_STATES, NON_ADMIN_KINDS, ANOMALIES, WORLD_LABEL
    global WORLD_NAME, XLSX_REL, WORLD_REL
    global MAP_PAGE, TREE_PAGE, ADMIN_ALIAS, ADMIN_ALIASES, OFFICE_TABLE, OFFICE_NOTE
    VAULT = paths.vault
    XLSX = paths.xlsx
    OUT = paths.admin_table
    LAYOUT_FILE = paths.layout_file
    WORLD_LABEL = paths.world_table.stem
    WORLD_NAME = paths.world_table.parents[1].name
    XLSX_REL = paths.xlsx.relative_to(paths.vault).as_posix()
    WORLD_REL = paths.world_table.relative_to(paths.vault).as_posix()

    layout = yaml.safe_load(LAYOUT_FILE.read_text(encoding="utf-8")) or {}
    STATE_ORDER = list(layout.get("order") or [])
    if not STATE_ORDER:
        raise SystemExit(f"✗ 版式配置里没有 order：{LAYOUT_FILE}")
    NON_ADMIN_STATES = set(layout.get("special_states") or [])
    NON_ADMIN_KINDS = dict(layout.get("special_kinds") or {})
    ANOMALIES = dict(layout.get("anomalies") or {})

    # 命名与关联目标全部来自版式配置：代码里不留作品特有的名字（ADR-005）
    naming = layout.get("naming") or {}
    MAP_PAGE = str(naming.get("map_page") or "地图")
    TREE_PAGE = str(naming.get("tree_page") or "层级")
    ADMIN_ALIAS = str(naming.get("admin_alias") or "")
    ADMIN_ALIASES = [str(x) for x in (naming.get("admin_aliases") or [])]
    OFFICE_TABLE = str(naming.get("office_table") or "")
    OFFICE_NOTE = str(naming.get("office_note") or "")

#: 写盘时从既有档案里原样搬过去的 frontmatter 键。
#:
#: 本脚本的模板写的是一对裸路径，而 11-19 的 provenance 改造把 `source_refs` 升级成了
#: 判别联合（kind / import_id / file_sha256 / locator / quote）。重跑一次就把哈希与
#: 行级定位一起抹平——2026-11-20 实测：3 行判别式来源变成 2 行裸路径，静默无提示。
#: 这三行记的是档案历史，导入器没资格改。
PRESERVE_KEYS = ("created_at", "source_refs", "import_batch")


def _fm_blocks(fm: list[str]) -> list[list[str]]:
    """切 frontmatter：每个「键 + 其后附属行」成一块。"""
    blocks: list[list[str]] = []
    cur: list[str] = []
    for line in fm:
        if re.match(r"^[A-Za-z_][\w-]*:", line):
            if cur:
                blocks.append(cur)
            cur = [line]
        elif cur:
            cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def keep_meta(new_lines: list[str], path: Path) -> list[str]:
    """把既有档案里的 PRESERVE_KEYS 拼回新生成的 frontmatter。"""
    if not path.exists():
        return new_lines
    raw = path.read_text(encoding="utf-8").split("\n")
    if not raw or raw[0].strip() != "---":
        return new_lines
    old_end = next((i for i in range(1, len(raw)) if raw[i].strip() == "---"), None)
    if old_end is None:
        return new_lines
    keep = {
        b[0].split(":", 1)[0].strip(): b
        for b in _fm_blocks(raw[1:old_end])
        if b[0].split(":", 1)[0].strip() in PRESERVE_KEYS
    }
    if not keep:
        return new_lines

    new_end = next(i for i in range(1, len(new_lines)) if new_lines[i].strip() == "---")
    out: list[str] = [new_lines[0]]
    for block in _fm_blocks(new_lines[1:new_end]):
        key = block[0].split(":", 1)[0].strip()
        out.extend(keep.pop(key, block))
    for block in keep.values():
        out.extend(block)
    out.append("---")
    out.extend(new_lines[new_end + 1:])
    return out

# 州（大区）的排列顺序、界外大区、界外条目的形态、原表已知的口径问题，
# 全部来自项目的版式配置（`<project>/00_Project/jiuzhou_layout.yaml`）。
#
# 这些原先写死在代码里，等于把「这部作品有哪些州、哪个洞天算奇地、哪一行是跨州复制残留」
# 一并搬进了公仓。公仓以 GPL-3.0 公开、按 ADR-005 不含任何作品设定，
# 所以一律下沉：`order` / `special_states` / `special_kinds` / `anomalies`。
STATE_ORDER: list[str] = []
NON_ADMIN_STATES: set[str] = set()
NON_ADMIN_KINDS: dict[str, str] = {}
ANOMALIES: dict[str, str] = {}
#: 州域志的文件名（不含 .md），用于生成双链。从 MapPaths 取，不在代码里写书名。
WORLD_LABEL: str = ""
#: 世界名（`World/<这个名字>/总表/` 的中间那层），以及两份来源文件的 Vault 相对路径。
#: 它们都从实际路径取，代码里不留任何具体地名。
WORLD_NAME: str = ""
XLSX_REL: str = ""
WORLD_REL: str = ""

EMPTY = "（原表未填）"
NO_FU = "（未分府）"


def cell(value) -> str:
    text = "" if value is None else str(value).strip()
    return text.replace("|", "\\|").replace("\n", " ")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    global DRY_RUN
    DRY_RUN = "--confirm" not in args
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
            # 表里有一行只填了州名与府名、没有地名：那是对府本身的占位行，
            # 不是郡，跳过（府级档案另由地图页生成器建）。
            placeholders.append(f"{state}·{fu}")
            continue

        # 界外洞天奇地：府与郡都不成立，只留「形态（洞天／奇地） + 名称」。原表那一格的
        # 府属／郡属是跨表复制残留（把别处的府郡填到了界外条目上），降到备注里说明来路，不当结构用。
        if state in NON_ADMIN_STATES:
            legacy = "／".join(x for x in (fu, jun) if x)
            note = cell(row[5])
            if legacy:
                note = (note + "；" if note else "") + f"原表记「{legacy}」，界外不入府郡"
            kind = NON_ADMIN_KINDS.get(name, "")
            if not kind:
                gaps.append(f"{name}：界外条目，形态（洞天／奇地）未认定，待作者裁定")
            tree.setdefault(state, OrderedDict()).setdefault(NO_FU, []).append(
                {
                    "name": name,
                    "jun": "",
                    "kind": kind,
                    "intro": cell(row[1]),
                    "note": note,
                    "fac": cell(row[6]),
                    "char": cell(row[7]),
                    "army": cell(row[8]),
                }
            )
            continue

        if not fu:
            gaps.append(f"{name}：府属未填")
        if not jun or jun == "无":
            if jun != "无":
                gaps.append(f"{name}：郡属未填")
        # 府名跨州重名：首个出现的州占有该府，后来的降为「未分府」，避免两页争同一编号。
        if fu:
            first = fu_owner.setdefault(fu, state)
            if first != state:
                gaps.append(f"{name}：府属「{fu}」与「{first}·{fu}」重名，未并入，待作者裁定")
                fu = NO_FU
        tree.setdefault(state, OrderedDict()).setdefault(fu or NO_FU, []).append(
            {
                "name": name,
                "jun": jun,
                "kind": "",
                "intro": cell(row[1]),
                "note": cell(row[5]),
                "fac": cell(row[6]),
                "char": cell(row[7]),
                "army": cell(row[8]),
            }
        )

    # 郡名跨府重复才算冲突。**同一府下同名多行是正常的**——一个郡可以辖多处聚落。
    # 此前按「行数」判重，把这类正常情况误报成重名，
    # 还印出「（郡名与  重名，待裁）」这种双空格残句。
    jun_owner: dict[str, set[tuple[str, str]]] = {}
    for state, fus in tree.items():
        for fu, rows in fus.items():
            for r in rows:
                if r["jun"] and r["jun"] != "无":
                    jun_owner.setdefault(r["jun"], set()).add((state, fu))
    for state, fus in tree.items():
        for fu, rows in fus.items():
            for r in rows:
                owners = jun_owner.get(r["jun"], set())
                if len(owners) > 1:
                    others = "、".join(f"{s}·{f}" for s, f in sorted(owners) if (s, f) != (state, fu))
                    mark = f"（郡名与 {others} 重名，待裁）"
                    r["note"] = (r["note"] + mark) if r["note"] else mark

    states = sorted(
        tree.items(),
        key=lambda kv: STATE_ORDER.index(kv[0]) if kv[0] in STATE_ORDER else len(STATE_ORDER),
    )
    unknown_state = [s for s in tree if s not in STATE_ORDER]

    total_fu = sum(1 for _, f in states for name in f if name != NO_FU)
    # 郡数按**郡名**计，不按行数：一个郡可以辖多处聚落。界外的洞天奇地也不计郡。
    total_jun = len(
        {
            r["jun"]
            for _, f in states
            for rows in f.values()
            for r in rows
            if r["jun"] and r["jun"] != "无"
        }
    )
    # 聚落＝行政序列里的行（城、乡等）；界外的洞天奇地单列，不算城市。
    total_place = sum(
        len(rows) for state, f in states if state not in NON_ADMIN_STATES for rows in f.values()
    )
    total_tian = sum(
        len(rows) for state, f in states if state in NON_ADMIN_STATES for rows in f.values()
    )

    lines: list[str] = [
        "---",
        "id: WORLD-0040",
        "type: world",
        f"name: {ADMIN_ALIAS}",
        f"title: {ADMIN_ALIAS}",
        "aliases:",
        *(f"- {alias}" for alias in ADMIN_ALIASES),
        "subtype: 总表",
        f"world: {WORLD_NAME}",
        "canon_status: confirmed",
        "created_at: '2026-11-05'",
        f"updated_at: '{TODAY}'",
        "source_refs:",
        f"- {XLSX_REL}",
        f"- {WORLD_REL}",
        "import_batch: MAPADMIN-20261105",
        "generated_by: novel maps import-admin",
        "tags:",
        f"- {WORLD_NAME}",
        "- 总表",
        "- 行政区划",
        "- 地图库",
        "---",
        "",
        f"# {ADMIN_ALIAS}",
        "",
        f"> 来源：`{XLSX.name}` · 工作表「地图库」｜由 `novel maps import-admin` 生成，**请勿手改**。",
        "> 改设定请改原始「地图库」表，再重跑脚本；只读本页容易被下一次生成覆盖。",
        "",
        "## 层级",
        "",
        "```text",
        "世界",
        f"└── 州（大区，{len(states)} 个）",
        "    └── 府（一州辖零至数府；界外大区不分府）",
        "        └── 郡（府下分郡；郡是户籍与地理单位）",
        "            └── 聚落（城、乡、洞天、奇地等；一郡可辖多处）",
        "```",
        "",
        "| 层级 | 定位 | 例 |",
        "| --- | --- | --- |",
        "| 州 | 最高地方区划，例设州牧；军管州为都护府 | 见各州页 |",
        "| 府 | 州下分府，设府尹；府城多为州治所在 | 见各府页 |",
        "| 郡 | 府下分郡，管户籍与地理；**与聚落不是一对一**，一郡可辖多处 | 见各郡所在的行 |",
        "| 聚落 | 郡下的具体地点，形态不一：城、乡、洞天、奇地 | 见各 LOC 档案 |",
        "",
        f"合计 **{len(states)}** 个大区、**{total_fu}** 个府、**{total_jun}** 个郡、"
        f"**{total_place}** 处聚落；另有界外洞天奇地 **{total_tian}** 处（不入行政序列）。",
        "",
    ]

    for state, fus in states:
        rows_all = [r for rows in fus.values() for r in rows]
        non_admin = state in NON_ADMIN_STATES
        jun_count = len({r["jun"] for r in rows_all if r["jun"] and r["jun"] != "无"})
        if non_admin:
            head = f"- **府郡**：—（界外，不入行政序列）　**洞天奇地**：{len(rows_all)} 处"
        else:
            head = (
                f"- **府数**：{sum(1 for n in fus if n != NO_FU)}　"
                f"**郡数**：{jun_count}　**聚落**：{len(rows_all)} 处"
            )
        lines += [f"## {state}", "", head, ""]
        for fu, rows in fus.items():
            lines += [
                f"### {fu}",
                "",
                "| 形态 | 名称 | 简介 | 备注 | 关联 |"
                if non_admin else "| 郡 | 聚落 | 简介 | 备注 | 关联 |",
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
                    "| {first} | {name} | {intro} | {note} | {assoc} |".format(
                        first=r["kind"] or r["jun"] or EMPTY,
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
        lines += ["**原表口径修正**（作者已裁定，照此执行）：", ""]
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
        "- 查某地在哪：先按州名找 `##`，再按府名找 `###`，表里**每一行是一处聚落**；"
        "同一个郡可能连着好几行（一郡辖多处聚落）。",
        "- 写作时「府」是行政与司法口径（府尹、府狱），「郡」是地理与户籍口径（郡望、郡兵），不要混用。",
        "- 郡下除了城，还会有乡、洞天、奇地等形态；界外大区只记形态，不入府郡。",
        f"- 本页只记层级与归属；地貌、气候、口语见 [[{WORLD_LABEL}]]，聚落细节见各自 LOC 档案。",
        "",
        "## 关联",
        "",
        f"- **州域志**：[[{WORLD_LABEL}]]",
        f"- **地图**：[[{MAP_PAGE}|{MAP_PAGE}]]、[[{TREE_PAGE}.canvas|{TREE_PAGE}.canvas]]",
        *(
            [f"- **官制**：[[{OFFICE_TABLE}|{OFFICE_TABLE.split('_', 1)[-1]}]]（{OFFICE_NOTE}）"]
            if OFFICE_TABLE
            else []
        ),
        "",
    ]

    payload = "\n".join(keep_meta(lines, OUT))
    unchanged = OUT.exists() and OUT.read_text(encoding="utf-8") == payload
    if not unchanged:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        if not DRY_RUN:
            OUT.write_text(payload, encoding="utf-8")
    print(f"✓ {OUT.relative_to(VAULT)}：{len(states)} 大区 / {total_fu} 府 / {total_jun} 郡")
    if gaps:
        print(f"! 原表空格 {len(gaps)} 处：{'；'.join(gaps)}")
    if unchanged:
        print("· 内容无变化，未写盘")
    elif DRY_RUN:
        print(f"· 预演：将重写 {OUT.relative_to(VAULT).as_posix()}（加 --confirm 执行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
