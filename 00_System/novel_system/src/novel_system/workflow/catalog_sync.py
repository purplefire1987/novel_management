"""目录投影：技能库页的「属技表」、命格总览页的「命格表」。

为什么单独一个模块
------------------
`relation_sync` 算的是**图**：谁连谁。可技能库页要的是一张**清单**——某个
体系下有哪些技能、什么品级、谁在练。这张表如果手写，就会和 172 张技能卡
各说各话；放在这里由档案汇总，才是单一事实源。

两个模块各写各的标记块，互不覆盖：
`relation_sync` 写 `AUTO:RELATIONS`，本模块写 `AUTO:CATALOG`。
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .. import vault
from ..config import Config
from . import relation_sync

CATALOG_START = "<!-- AUTO:CATALOG -->"
CATALOG_END = "<!-- /AUTO:CATALOG -->"

MARKER = re.compile(re.escape(CATALOG_START) + r".*?" + re.escape(CATALOG_END), re.S)

# 哪些目录里可能有目录块
SCAN_DIRS = ("Skills", "Destinies")


def _cell(text: str) -> str:
    """表格单元格里不能出现竖线和换行。"""
    return (text or "").replace("|", "｜").replace("\n", " ").strip()


def _meta(node: relation_sync.Node) -> dict:
    try:
        return vault.read_doc(node.path).meta
    except Exception:  # noqa: BLE001
        return {}


def _imprint(node: relation_sync.Node) -> str:
    try:
        body = vault.read_doc(node.path).body
    except Exception:  # noqa: BLE001
        return ""
    # 只吃本行的值：`\s*` 会跨行吞掉下一个字段（「关联角色」曾被当成烙印）
    hit = re.search(r"^- \*\*命运烙印\*\*[：:][ \t]*(.*)$", body, re.M)
    return hit.group(1).strip() if hit else ""


def _collect(cfg: Config) -> tuple[dict[str, list], dict[str, list]]:
    """技能 → 修习者；命格 → 持有者。都从档案的声明字段反查。"""
    nodes, by_id = relation_sync.load_nodes(cfg)
    resolver = relation_sync.Resolver(nodes, by_id)
    users: dict[str, list] = defaultdict(list)
    holders: dict[str, list] = defaultdict(list)

    for node in nodes:
        if node.kind not in ("人物", "生物"):
            continue
        body = relation_sync._read_body(node.path)
        for key, value in relation_sync._fields(body):
            if not value:
                continue
            if key == "技能":
                for target in resolver.many(value, "技能"):
                    users[target.id].append(node)
            elif key == "命格" and node.kind == "人物":
                for target in resolver.many(value, "命格"):
                    holders[target.id].append(node)

    for bucket in (users, holders):
        for key in bucket:
            bucket[key].sort(key=lambda n: n.id)
    return users, holders


def _render_skills(lib: str, nodes: list[relation_sync.Node], users: dict[str, list]) -> str:
    rows = sorted(
        (n for n in nodes if n.kind == "技能" and n.name and _meta(n).get("subtype") == lib),
        key=lambda n: n.id,
    )
    if not rows:
        return ""
    lines = [
        f"| 技能 | 品级 | 系别 | 修习者（{sum(1 for n in rows if users.get(n.id))} 项有人练） |",
        "| --- | --- | --- | --- |",
    ]
    for node in rows:
        meta = _meta(node)
        who = "、".join(u.name for u in users.get(node.id, [])) or "—"
        lines.append(
            f"| {node.label} | {_cell(str(meta.get('grade') or '未定'))} "
            f"| {_cell(str(meta.get('school') or '—'))} | {_cell(who)} |"
        )
    lines += ["", f"> 共 {len(rows)} 项。本表由技能卡汇总，改技能请改卡片，别改这里。"]
    return "\n".join(lines)


def _render_destinies(nodes: list[relation_sync.Node], holders: dict[str, list]) -> str:
    rows = sorted((n for n in nodes if n.kind == "命格"), key=lambda n: n.id)
    if not rows:
        return ""
    lines = [
        f"| 命格 | 编号 | 命运烙印 | 持有者（{sum(1 for n in rows if holders.get(n.id))} 格已定人） |",
        "| --- | --- | --- | --- |",
    ]
    for node in rows:
        who = "、".join(h.name for h in holders.get(node.id, [])) or "—"
        lines.append(f"| {node.label} | {node.id} | {_cell(_imprint(node)) or '—'} | {_cell(who)} |")
    lines += ["", f"> 共 {len(rows)} 格。持有者由人物页的 `命格` 字段反查，改人物即可，不用改这里。"]
    return "\n".join(lines)


def run(cfg: Config, *, write: bool = True) -> dict[str, object]:
    nodes, _by_id = relation_sync.load_nodes(cfg)
    users, holders = _collect(cfg)

    touched = 0
    pages = 0
    for sub in SCAN_DIRS:
        directory = cfg.vault / "01_Core_Memory" / sub
        if not directory.is_dir():
            continue
        for path in sorted(p for p in directory.glob("*.md") if not p.name.startswith(".")):
            text = vault.read_text(path)
            if CATALOG_START not in text:
                continue
            pages += 1
            doc = vault.read_doc(path)
            outline = str(doc.meta.get("subtype") or "")
            if sub == "Destinies":
                table = _render_destinies(nodes, holders)
            else:
                table = _render_skills(outline, nodes, users)
            block = f"{CATALOG_START}\n\n{table}\n\n{CATALOG_END}" if table else f"{CATALOG_START}\n{CATALOG_END}"
            new = MARKER.sub(lambda _: block, text)
            if new != text and write:
                vault.write_atomic(path, new)
                touched += 1
            elif new != text:
                touched += 1
    return {"catalog_pages": pages, "files_touched": touched}


def dangling(cfg: Config) -> list[dict[str, object]]:
    """技能卡里写了所属库、但库里没有这张卡的档案。

    比的是 `subtype`（兵法 / 玄法…），不是页面名（`九州·技能库·兵法`）——
    技能卡的 subtype 就是照库页的 subtype 填的。
    """
    nodes, _by_id = relation_sync.load_nodes(cfg)
    problems: list[dict[str, object]] = []
    libs = {str(_meta(n).get("subtype") or "") for n in nodes if n.kind == "技能库"}
    for node in nodes:
        if node.kind != "技能":
            continue
        if str(_meta(node).get("subtype") or "") not in libs:
            problems.append(
                {
                    "node": node.id,
                    "subtype": str(_meta(node).get("subtype") or ""),
                    "path": str(node.path.relative_to(cfg.vault)),
                }
            )
    return problems
