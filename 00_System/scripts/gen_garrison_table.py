#!/usr/bin/env python3
"""从军团档生成 `WORLD-0041_各州驻军表`。

为什么不手写这张表：军团人数与驻地的事实源是 28 张 `FAC-*.md`（subtype: 军团），
手抄一遍就会漂移——改了一张卡，表不知道。这里读卡出表，表只是视图。

用法：`.venv/bin/python 00_System/scripts/gen_garrison_table.py`
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from pathlib import Path

import yaml

VAULT = Path(__file__).resolve().parents[2]
FAC_DIR = VAULT / "01_Core_Memory/Factions"
LAYOUT = VAULT / "00_System/Config/jiuzhou_layout.yaml"
OUT = VAULT / "01_Core_Memory/World/九州/总表/WORLD-0041_各州驻军表.md"

# 按 0031 编制表反推「该军团人数在名义上够授哪一级主官」。
# 只是算术对照，禁卫系与王牌各有特例，不当任命依据用。
TIERS = [
    (90_000, "上将军（三品）"),
    (30_000, "将军（从四品）"),
    (10_000, "校尉（六品）"),
    (3_000, "团总（从六品）"),
    (800, "守备（八品）"),
    (100, "旗总（从八品）"),
    (50, "队正（九品）"),
    (5, "伍长（从九品）"),
]


def tier_of(num: int) -> str:
    for floor, label in TIERS:
        if num >= floor:
            return label
    return "不足一伍"


# 世家名单，用于把「帝室以外的持有者」分成世家与江湖势力两类。
HOUSES = {"魏氏", "袁氏", "王氏", "萧氏", "谢氏", "周家", "卫家", "杨家", "李家", "白家", "项家", "朱氏"}


# 控制权口径：`所属势力` 里出现帝室即算帝国一方；多势力（如「李家,帝室」）算共管。
def control_of(owner: str) -> str:
    names = [x.strip() for x in re.split(r"[,，、]", owner) if x.strip()]
    imperial = "帝室" in names
    others = [n for n in names if n != "帝室"]
    if imperial and others:
        return "帝室＋世家共管"
    if imperial or not names:
        return "帝室直控"
    if all(n in HOUSES for n in names):
        return "世家私兵"
    return "江湖势力"


def field(text: str, key: str) -> str:
    m = re.search(r"^-\s*\*\*" + key + r"\*\*：(.*)$", text, re.M)
    return m.group(1).strip() if m else ""


def load() -> list[dict]:
    order = yaml.safe_load(LAYOUT.read_text(encoding="utf-8"))["order"]
    rows = []
    for p in sorted(FAC_DIR.glob("FAC-*.md")):
        text = p.read_text(encoding="utf-8")
        if "subtype: 军团" not in text[:900]:
            continue
        geo = re.search(r"\*\*所在地\*\*：(.+)", text)
        chain = geo.group(1).strip() if geo else ""
        states = re.findall(r"\[\[LOC-\d{4}_([^\]|]+)", chain)
        num = field(text, "军团人数").replace(",", "")
        rows.append({
            "id": p.name.split("_")[0],
            "name": re.search(r"^name:\s*(.+)$", text[:900], re.M).group(1).strip(),
            "num": int(num) if num.isdigit() else 0,
            "power": field(text, "战力评价") or "—",
            "kind": field(text, "军团类型") or "—",
            "garrison": field(text, "驻扎地"),
            "owner": field(text, "所属势力") or "—",
            "state": states[0] if states else "",
        })
    rank = {s: i for i, s in enumerate(order)}
    rows.sort(key=lambda r: (rank.get(r["state"], 99), -r["num"]))
    return rows


def render(rows: list[dict]) -> str:
    order = yaml.safe_load(LAYOUT.read_text(encoding="utf-8"))["order"]
    total = sum(r["num"] for r in rows)
    by_state = defaultdict(list)
    for r in rows:
        by_state[r["state"] or "未定"].append(r)

    out = []
    out.append("---")
    out.append("id: WORLD-0041")
    out.append("type: world")
    out.append("name: 九州·各州驻军表")
    out.append("title: 九州·各州驻军表")
    out.append("aliases: []")
    out.append("subtype: 各州驻军表")
    out.append("world: 九州")
    out.append("canon_status: proposed")
    out.append("created_at: '2026-11-05'")
    out.append(f"updated_at: '{date.today().isoformat()}'")
    out.append("source_refs:")
    out.append("- 01_Core_Memory/World/九州/大盛帝国/WORLD-0031_军制与武装力量.md")
    out.append("- 01_Core_Memory/Factions/（28 张 subtype: 军团 的档案）")
    out.append("tags:")
    out.append("- 九州")
    out.append("- world-asset")
    out.append("- 总表")
    out.append("- 军制")
    out.append("- 驻军")
    out.append("---")
    out.append("")
    out.append("# 九州·各州驻军表")
    out.append("")
    out.append("> **性质**：派生视图，不是独立设定源。每一行都读自 `01_Core_Memory/Factions/` 下")
    out.append("> `subtype: 军团` 的档案（`军团人数`、`驻扎地`、`所属势力`、`战力评价`），州归属取自卡内")
    out.append("> `GEO-APPEARANCE` 的所在地链。**要改兵力，改军团卡，再重跑**")
    out.append("> `00_System/scripts/gen_garrison_table.py`；直接改本表会在下次重跑时被覆盖。")
    out.append("> 编制层级与主官品级口径见 [[WORLD-0031_军制与武装力量|军制与武装力量]]「编制层级」。")
    out.append("")
    out.append("## 全军总览")
    out.append("")
    out.append(f"- **军团**：{len(rows)} 支")
    out.append(f"- **总兵力**：{total:,} 人")
    out.append(f"- **按 3 万／军折算**：约 {round(total / 30000)} 个军；按 5 万／军折算约 {round(total / 50000)} 个军")
    out.append(f"- **按满编 30 万／集团军群折算**：约 {total / 300000:.1f} 个满编集团军群——即帝国全部兵力也凑不出三四个满编")
    out.append("")
    out.append("## 控制权分布")
    out.append("")
    out.append("按 `所属势力` 拆四类；多势力（如 `李家,帝室`）记共管——这一类最有戏，名义上皇帝的兵，实际听家主的。")
    out.append("")
    out.append("| 控制方 | 军团数 | 兵力 | 占全军 |")
    out.append("| --- | --- | --- | --- |")
    by_ctrl = defaultdict(list)
    for r in rows:
        by_ctrl[control_of(r["owner"])].append(r)
    for key in ("帝室直控", "帝室＋世家共管", "世家私兵", "江湖势力"):
        items = by_ctrl.get(key)
        if not items:
            continue
        sub = sum(r["num"] for r in items)
        out.append(f"| {key} | {len(items)} | {sub:,} | {sub / total * 100:.1f}% |")
    out.append(f"| **合计** | **{len(rows)}** | **{total:,}** | 100% |")
    out.append("")

    out.append("## 各州兵力排序")
    out.append("")
    out.append("| 州 | 军团数 | 兵力 | 占全军 | 主力 |")
    out.append("| --- | --- | --- | --- | --- |")
    ranked = sorted(by_state.items(), key=lambda kv: -sum(r["num"] for r in kv[1]))
    for state, items in ranked:
        sub = sum(r["num"] for r in items)
        main = max(items, key=lambda r: r["num"])
        share = f"{sub / total * 100:.1f}%" if total else "—"
        out.append(f"| {state} | {len(items)} | {sub:,} | {share} | {main['name']}（{main['num']:,}） |")
    out.append(f"| **合计** | **{len(rows)}** | **{total:,}** | 100% | — |")
    out.append("")

    out.append("## 分州明细")
    out.append("")
    out.append("按九州地理顺序排（中州居中，其余按方位），不按兵力排——这一节是查驻地用的。")
    out.append("")
    out.append("「按编制应授」是由人数按 [[WORLD-0031_军制与武装力量|0031 编制表]] 反推的**名义**层级，")
    out.append("只作算术对照；禁卫系、王牌军团的实际官号可特殊，不作为任命依据。")
    out.append("")

    def srank(s: str) -> tuple:
        return (0, order.index(s)) if s in order else (1, 0)

    for state, items in sorted(by_state.items(), key=lambda kv: srank(kv[0])):
        sub = sum(r["num"] for r in items)
        head = f"未定州属（{sub:,}）" if state == "未定" else f"{state}（{sub:,}）"
        out.append(f"### {head}")
        out.append("")
        out.append("| 军团 | 兵力 | 战力 | 类型 | 归属 | 驻扎地 | 按编制应授 |")
        out.append("| --- | --- | --- | --- | --- | --- | --- |")
        for r in items:
            link = f"[[{r['id']}_{r['name']}|{r['name']}]]"
            gar = r["garrison"] or "**未定**"
            out.append(
                f"| {link} | {r['num']:,} | {r['power']} | {r['kind']} | {r['owner']} | {gar} | {tier_of(r['num'])} |"
            )
        out.append("")

    missing_gar = sorted([r for r in rows if not r["garrison"]], key=lambda r: -r["num"])
    missing_state = [r for r in rows if not r["state"]]
    out.append("## 待定与缺口")
    out.append("")
    if missing_gar:
        out.append(f"### 缺驻扎地（{len(missing_gar)} 支，共 {sum(r['num'] for r in missing_gar):,} 人）")
        out.append("")
        out.append("| 军团 | 兵力 | 归属 | 类型 |")
        out.append("| --- | --- | --- | --- |")
        for r in missing_gar:
            out.append(f"| [[{r['id']}_{r['name']}|{r['name']}]] | {r['num']:,} | {r['owner']} | {r['kind']} |")
        out.append("")
        out.append("补法：在军团卡的 `## 档案` 里填 `- **驻扎地**：<地名>`，并确保该地名有 `LOC-` 档案（")
        out.append("`GEO-APPEARANCE` 会自动带出州与府；没有地名档案就先建 LOC，否则本表只能列在「未定州属」）。")
        out.append("")
    if missing_state:
        out.append("### 缺州归属")
        out.append("")
        for r in missing_state:
            out.append(f"- {r['name']}（{r['id']}）：卡内 `GEO-APPEARANCE` 无所在地链，多半是没有 `LOC-` 地名档案。")
        out.append("")

    weak = [r for r in rows if r["power"] == "1"]
    weak_sum = sum(r["num"] for r in weak)
    capital = sum(r["num"] for r in by_state.get("中州", []))
    strong_gar = [r for r in missing_gar if r["power"].isdigit() and int(r["power"]) >= 4]
    ctrl_share = {k: sum(r["num"] for r in v) / total * 100 for k, v in by_ctrl.items()}
    dual = by_ctrl.get("帝室＋世家共管", [])
    dual_txt = "、".join(f"{r['name']} {r['num']:,}（{r['owner']}）" for r in dual)
    out.append("## 观察（据本表算出，待作者裁定）")
    out.append("")
    if capital:
        liang = sum(r["num"] for r in by_state.get("凉州", []))
        out.append(f"1. **京畿重兵**。中州一州 {capital:,} 人，占全军 {capital / total * 100:.1f}%；"
                   f"而凉州（挡冰原兽潮的方向）全部驻军 {liang:,} 人、{len(by_state.get('凉州', []))} 支。"
                   f"写「边患紧急、朝廷却按兵不动」时有账面依据。")
    out.append(f"2. **三成兵力是账面数字**。战力评为 1 的 {len(weak)} 支合计 {weak_sum:,} 人，占 "
               f"{weak_sum / total * 100:.1f}%——点兵场面别把这些当成能打的。")
    if strong_gar:
        names = "、".join(r["name"] for r in strong_gar)
        out.append(f"3. **能打的反而没定驻地**。缺驻扎地的 {len(missing_gar)} 支里，战力 4 以上有 "
                   f"{len(strong_gar)} 支：{names}（共 {sum(r['num'] for r in strong_gar):,} 人）。"
                   f"王牌驻哪是政治表态，不是后勤问题，建议优先定。")
    if dual:
        out.append(f"4. **兵权不在皇帝一家**。帝室直控 {ctrl_share.get('帝室直控', 0):.1f}%，"
                   f"世家私兵 {ctrl_share.get('世家私兵', 0):.1f}%，另有 "
                   f"{ctrl_share.get('帝室＋世家共管', 0):.1f}% 是「挂着帝室名、听世家话」的共管军："
                   f"{dual_txt}。朝廷调得动，未必打得动。")
    out.append("")

    out.append("## 关联")
    out.append("")
    out.append("- 编制层级与主官品级：[[WORLD-0031_军制与武装力量|军制与武装力量]]")
    out.append("- 官职与管辖：[[WORLD-0027_帝国官职表|帝国官职表]]")
    out.append("- 爵位门槛：[[WORLD-0028_帝国爵位表|帝国爵位表]]")
    out.append("- 州域地貌：[[WORLD-0029_九州表|九州表]]；府郡归属：[[WORLD-0040_行政区划表|行政区划表]]")
    out.append("")
    return "\n".join(out)


def main() -> int:
    rows = load()
    if not rows:
        raise SystemExit("✗ 没读到任何军团档，检查 FAC 目录与 subtype 字段")
    OUT.write_text(render(rows), encoding="utf-8")
    total = sum(r["num"] for r in rows)
    print(f"✓ 驻军表 {len(rows)} 支 / {total:,} 人 → {OUT.relative_to(VAULT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
