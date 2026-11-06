"""实体 ↔ 地图联动：把人物/势力/物品/生物挂到地点上，并随正文更新自动刷新。

为什么单独一个模块
------------------
`import-assets` 只在导入那一刻写一次「关联：角色库」这类静态链接；之后新写的章节、
新立的档案、挪过的据点，这些链接全都不会跟着变。地图页于是慢慢变成一张过期的快照。

本模块把「谁在哪」变成**可重算的投影**：

    源：01_Core_Memory 下的实体档案（人的故乡、势力的据点、物的存放地……）
        + 08_Manuscripts 下的正文（谁在哪一章出现）
    出：地点页的「本地实体」块 / 实体页的「地理位置与出场」块 / Maps/实体分布.md

三个触点保证「热更新」：
1. `novel rebuild-index` 之后自动跑（orchestrator.rebuild_index）
2. `novel publish` 之后自动跑（review.publish）——正文一发布，地图页就变
3. `python 00_System/scripts/link_entities_to_map.py` 手动跑，便于排查

写法纪律
--------
只写两个带标记的块，块外的内容一个字节都不碰：

    <!-- AUTO:LOCAL-ENTITIES -->
    ...
    <!-- /AUTO:LOCAL-ENTITIES -->

块内每次整块重算，所以重复运行结果一致（幂等）。手写的「关联」段不动，
自动块只补**手写段里没有的**那条链接——这样它既是索引，也是一份「还差什么」的清单。
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .. import vault
from ..config import Config
from ..links import WIKILINK

LOC_BLOCK_START = "<!-- AUTO:LOCAL-ENTITIES -->"
LOC_BLOCK_END = "<!-- /AUTO:LOCAL-ENTITIES -->"
ENT_BLOCK_START = "<!-- AUTO:GEO-APPEARANCE -->"
ENT_BLOCK_END = "<!-- /AUTO:GEO-APPEARANCE -->"

ENTITY_DIRS: tuple[tuple[str, str], ...] = (
    ("Characters", "人物"),
    ("Factions", "势力"),
    ("Items", "物品"),
    ("Enemies", "生物"),
)

# 实体档案里哪些字段是「地点」，按可信度从高到低
PLACE_FIELDS: tuple[str, ...] = (
    "据点",
    "驻地",
    "驻扎地",
    "驻扎地／驻地",
    "所在地",
    "存放地",
    "故乡",
    "栖息地",
    "籍贯",
    "活动范围",
)

# 没有直接地点时，沿「所属势力」借一层——人的据点通常就是组织的据点
FACTION_FIELDS: tuple[str, ...] = ("势力所属", "所属势力", "所属", "阵营")

LOC_STEM = re.compile(r"(LOC-\d{4})")
CH_STEM = re.compile(r"(CH-\d{4})")
FAC_STEM = re.compile(r"(FAC-\d{4})")


@dataclass
class Entity:
    id: str
    name: str
    kind: str                       # 人物 / 势力 / 物品 / 生物
    path: Path
    aliases: list[str] = field(default_factory=list)
    places: set[str] = field(default_factory=set)     # LOC id
    via: dict[str, str] = field(default_factory=dict)  # LOC id → 借来的来源说明
    chapters: list[str] = field(default_factory=list)  # CH id，按序

    @property
    def stem(self) -> str:
        return self.path.stem


@dataclass
class Place:
    id: str
    name: str
    path: Path
    level: str = ""                 # 州域 / 府 / 城
    state: str = ""                 # 上级州 id
    fu: str = ""                    # 上级府 id
    aliases: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 读取
def _meta_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _docs(directory: Path) -> list[Path]:
    """目录下的实体/地点档案。

    必须手写 `.name` 过滤：`pathlib.Path.glob("*.md")` 和 `glob` 模块不同，
    **会**匹配 `.tmp_xxx.md` 这种点开头文件。曾因此把 177 个写入半成品当成
    真正的实体读进来，导致 ID 重复、统计虚高。
    """
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.md") if not p.name.startswith("."))


def load_places(cfg: Config) -> dict[str, Place]:
    root = cfg.vault / "01_Core_Memory" / "Locations"
    places: dict[str, Place] = {}
    for path in [x for x in _docs(root) if x.name.startswith("LOC-")]:
        try:
            doc = vault.read_doc(path)
        except Exception:  # noqa: BLE001 - 坏档不该让整轮同步失败
            continue
        pid = str(doc.meta.get("id") or "")
        if not pid:
            continue
        place = Place(
            id=pid,
            name=str(doc.meta.get("name") or path.stem),
            path=path,
            level=str(doc.meta.get("subtype") or ""),
            aliases=_meta_list(doc.meta.get("aliases")),
        )
        # 州属 / 府属 两项存的是双链，取 LOC 号即可；没有就留空
        fields = _fields(doc.body)
        for key, attr in (("州属", "state"), ("府属", "fu")):
            hit = LOC_STEM.search(fields.get(key, ""))
            setattr(place, attr, hit.group(1) if hit else "")
        places[pid] = place
    return places


def load_entities(cfg: Config) -> list[Entity]:
    out: list[Entity] = []
    seen: set[str] = set()
    for subdir, kind in ENTITY_DIRS:
        for path in _docs(cfg.vault / "01_Core_Memory" / subdir):
            try:
                doc = vault.read_doc(path)
            except Exception:  # noqa: BLE001
                continue
            eid = str(doc.meta.get("id") or "")
            if not eid or eid in seen:      # 同 ID 只认第一份，避免统计与链接重复
                continue
            seen.add(eid)
            out.append(
                Entity(
                    id=eid,
                    name=str(doc.meta.get("name") or path.stem),
                    kind=kind,
                    path=path,
                    aliases=_meta_list(doc.meta.get("aliases")),
                )
            )
    return out


def _body_of(path: Path) -> str:
    try:
        return vault.read_doc(path).body
    except Exception:  # noqa: BLE001
        return ""


# 受控块是本模块**自己的输出**。解析前必须剥掉，否则会自己吃自己：
# `GEO-APPEARANCE` 里那行 `- **所在地**：[[LOC-0055_融金城|融金城]]` 就是它渲染的，
# 一旦被当回输入读，正文里随手提一句地名就会被固化成永久落点——删掉正文里的
# 那句话也去不掉，因为块里还留着。关系块的 `_strip_auto` 同理。
AUTO_BLOCK = re.compile(r"<!-- AUTO:[A-Z-]+ -->.*?<!-- /AUTO:[A-Z-]+ -->", re.S)


def _strip_auto(text: str) -> str:
    return AUTO_BLOCK.sub("", text)


def _fields(body: str) -> dict[str, str]:
    """读「档案」段成字典。键在原文里带 `**`（`- **据点**：…`），这里去掉。"""
    raw = vault.key_values(vault.extract_section(body, "档案"))
    return {k.replace("*", "").strip(): v for k, v in raw.items()}


def resolve_places(entities: list[Entity], places: dict[str, Place]) -> None:
    """两条路取并集：实体页自己写的地点，以及地点页「关联」里点名的人。"""
    by_name: dict[str, str] = {}
    for pid, place in places.items():
        by_name[place.name] = pid
        for alias in place.aliases:
            by_name.setdefault(alias, pid)

    # 正向：实体档案里出现的地点双链 / 地点名
    for entity in entities:
        body = _strip_auto(_body_of(entity.path))
        for hit in LOC_STEM.finditer(body):
            if hit.group(1) in places:
                entity.places.add(hit.group(1))
        if entity.places:
            continue
        fields = _fields(body)
        for key in PLACE_FIELDS:
            raw = fields.get(key, "")
            if not raw:
                continue
            for name, pid in by_name.items():
                if len(name) >= 2 and name in raw:
                    entity.places.add(pid)

    # 反向：地点页「关联」段里的双链，反推该实体在此
    by_stem = {e.stem: e for e in entities}
    by_id = {e.id: e for e in entities}
    for pid, place in places.items():
        body = _strip_auto(_body_of(place.path))
        section = vault.extract_section(body, "关联") or vault.extract_section(body, "实体索引（自动汇总）")
        for link in WIKILINK.finditer(section):
            stem = link.group(1).strip()
            entity = by_stem.get(stem) or by_id.get(stem.split("_")[0])
            if entity is not None:
                entity.places.add(pid)

    # 三级：自己没写地点，就沿「所属势力」借一层。跑两轮，够覆盖「组织属组织」的情况。
    by_fac_id = {e.id: e for e in entities if e.kind == "势力"}
    by_fac_name: dict[str, Entity] = {}
    for faction in by_fac_id.values():
        by_fac_name.setdefault(faction.name, faction)
        for alias in faction.aliases:
            by_fac_name.setdefault(alias, faction)

    def parent_of(entity: Entity) -> Entity | None:
        fields = _fields(_body_of(entity.path))
        for key in FACTION_FIELDS:
            raw = fields.get(key, "")
            if not raw:
                continue
            for hit in FAC_STEM.finditer(raw):
                parent = by_fac_id.get(hit.group(1))
                if parent is not None and parent is not entity:
                    return parent
            for name, faction in by_fac_name.items():
                if len(name) >= 2 and name in raw and faction is not entity:
                    return faction
        return None

    for _ in range(2):
        for entity in entities:
            if entity.places:
                continue
            parent = parent_of(entity)
            if parent is None or not parent.places:
                continue
            for pid in parent.places:
                entity.places.add(pid)
                entity.via.setdefault(pid, f"依「{parent.name}」据点推断")

    # 补齐上级：只写了「在某城」的实体，也该出现在它所属的府页与州页上。
    #
    # 这一步以前是**间接**完成的——实体的 `GEO-APPEARANCE` 渲染出「州 › 府 › 城」整条链，
    # 下一轮把链里的三个 ID 全读回来。那等于用输出当输入：正文里删掉地名，州页上仍然挂着。
    # 现在按层级直接算，上级不再需要 `via` 标记（它们不是「推断」，是行政隶属）。
    for entity in entities:
        for pid in list(entity.places):
            place = places.get(pid)
            if place is None:
                continue
            for ancestor in _chain_ids(place, places):
                if ancestor in entity.places:
                    continue
                entity.places.add(ancestor)
                # 上级继承下级的来历：城是从「所属势力」借来的，它所属的府与州也一样，
                # 否则统计里那 7 个「推断」会凭空变成「直接写出」，看着像正文写了地点。
                if pid in entity.via:
                    entity.via[ancestor] = entity.via[pid]


def resolve_chapters(cfg: Config, entities: list[Entity]) -> dict[str, tuple[str, str]]:
    """扫正文，记下每个实体出现在哪些章。返回 章号 → (文件 stem, 标题)。

    用 stem 而不是章号做双链：正文文件名带标题（`CH-0001_雨夜的血案`），
    `[[CH-0001]]` 会落到同名大纲上，读者点过去看到的是细纲而不是正文。
    """
    root = cfg.vault / "08_Manuscripts"
    texts: list[tuple[str, str]] = []
    info: dict[str, tuple[str, str]] = {}
    if root.exists():
        for path in sorted(root.rglob("CH-*.md")):
            if path.name.startswith(".") or any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            hit = CH_STEM.match(path.stem)
            if not hit:
                continue
            chapter = hit.group(1)
            try:
                doc = vault.read_doc(path)
            except Exception:  # noqa: BLE001
                continue
            info[chapter] = (path.stem, str(doc.meta.get("title") or chapter))
            texts.append((chapter, doc.body))
    texts.sort(key=lambda x: x[0])

    for entity in entities:
        names = [n for n in [entity.name, *entity.aliases] if len(n) >= 2]
        if not names:
            continue
        for chapter, body in texts:
            if any(n in body for n in names):
                entity.chapters.append(chapter)
    return info


# ---------------------------------------------------------------- 结构
def _chain_ids(place: Place, places: dict[str, Place]) -> list[str]:
    """从州到城的地点 ID 序列，只含有档案的那几级。"""
    ids: list[str] = []
    fu = places.get(place.fu) if place.fu else None
    state = places.get(fu.state if fu and fu.state else place.state) if (place.state or (fu and fu.state)) else None
    if state:
        ids.append(state.id)
    if fu:
        ids.append(fu.id)
    ids.append(place.id)
    return ids


def _render_chain(ids: list[str], places: dict[str, Place]) -> str:
    return " › ".join(f"[[{places[i].path.stem}|{places[i].name}]]" for i in ids if i in places)


def _collapse(chains: list[list[str]]) -> list[list[str]]:
    """去掉被更长链包含的短链。

    档案里同时点了城、府、州三处（或由名称反推出来），会渲染成
    「中州」「中州›皇极府」「中州›皇极府›上京都」三条——后两条是第一条的延长，
    留着只是噪音。
    """
    chains = [c for c in chains if c]
    keep: list[list[str]] = []
    for chain in chains:
        if any(len(other) > len(chain) and other[: len(chain)] == chain for other in chains):
            continue
        if chain not in keep:
            keep.append(chain)
    return keep


def _existing_links(text: str) -> set[str]:
    """手写段里的双链——**必须排除受控块自己写的那些**。

    否则第一轮写完块，第二轮就会把块内的链接当成「手写已收录」，
    `fresh` 归零、提示行消失、再写一次盘。表现是「跑两三次才稳定」。
    """
    outside = re.sub(r"<!-- AUTO:[A-Z-]+ -->.*?<!-- /AUTO:[A-Z-]+ -->", "", text, flags=re.S)
    return {m.group(1).strip() for m in WIKILINK.finditer(outside)}


def _render_place_block(place: Place, members: list[Entity], places: dict[str, Place]) -> str:
    if not members:
        return ""
    body = _body_of(place.path)
    already = _existing_links(body)
    lines = [LOC_BLOCK_START, "", "## 实体索引（自动汇总）", ""]
    total = 0
    for kind in ("势力", "人物", "物品", "生物"):
        group = [e for e in members if e.kind == kind]
        if not group:
            continue
        fresh = [e for e in group if e.stem not in already]
        total += len(group)
        shown = "、".join(f"[[{e.stem}|{e.name}]]" for e in group)
        lines.append(f"- **{kind}**（{len(group)}）：{shown}")
        if fresh:
            lines.append(f"  - 其中 {len(fresh)} 条是本页「关联」段尚未收录的："
                         + "、".join(e.name for e in fresh))
    if not total:
        return ""
    chapters = sorted({c for e in members for c in e.chapters})
    if chapters:
        lines += ["", f"- **涉及章节**：{'、'.join(chapters)}"]
    lines += ["", f"- 全库汇总：[[实体分布]]", "", LOC_BLOCK_END, ""]
    return "\n".join(lines)


def _render_entity_block(entity: Entity, places: dict[str, Place], chapters: dict[str, tuple[str, str]]) -> str:
    if not entity.places and not entity.chapters:
        return ""
    lines = [ENT_BLOCK_START, "", "## 地理位置与出场（自动）", ""]
    if entity.places:
        ids = sorted(i for i in entity.places if i in places)
        chains = _collapse([_chain_ids(places[i], places) for i in ids])
        # 必须显式排序：places 是 set，字符串哈希每个进程都不同，
        # 不排序会让「有两个以上地点」的实体每跑一次换个顺序，白写一次盘。
        chains.sort(key=tuple)
        rendered = []
        for chain in chains:
            text = _render_chain(chain, places)
            tail = chain[-1]
            if tail in entity.via:
                text += f"（{entity.via[tail]}）"
            rendered.append(text)
        if rendered:
            lines.append("- **所在地**：" + "；".join(rendered))
    if entity.chapters:
        parts = []
        for chapter in entity.chapters:
            stem, _title = chapters.get(chapter, (chapter, chapter))
            parts.append(f"[[{stem}]]" if stem == chapter else f"[[{stem}|{chapter}]]")
        lines.append(f"- **出场章节**（{len(entity.chapters)}）：" + "、".join(parts))
    lines += ["", ENT_BLOCK_END]
    return "\n".join(lines)


def _replace_block(text: str, start: str, end: str, block: str) -> str:
    """把受控块整块换成新内容；没有就追加到文末。块外内容不动。"""
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if pattern.search(text):
        if not block:
            return pattern.sub("", text).rstrip() + "\n"
        return pattern.sub(lambda _: block.rstrip("\n"), text).rstrip("\n") + "\n"
    if not block:
        return text
    return text.rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n"


def _render_map_page(
    entities: list[Entity],
    places: dict[str, Place],
    chapters: dict[str, tuple[str, str]],
) -> str:
    located = [e for e in entities if e.places]
    orphan = [e for e in entities if not e.places]
    by_place: dict[str, list[Entity]] = defaultdict(list)
    for entity in entities:
        for pid in entity.places:
            by_place[pid].append(entity)

    # 州 → 府 → 城 的展示顺序
    def order(pid: str) -> tuple[str, str, str]:
        place = places[pid]
        if place.level == "府":                      # 府本级：它自己就是府，不再挂在某个府下
            state = places.get(place.state) if place.state else None
            return (state.name if state else "（未归属）", "（府本级）", place.name)
        if place.level == "州域":                    # 州域页：与州同名，单列一档
            return (place.name, "（州本级）", place.name)
        fu = places.get(place.fu) if place.fu else None
        state = places.get(fu.state) if (fu and fu.state) else places.get(place.state)
        return (
            state.name if state else "（未归属）",
            fu.name if fu else "（州直属）",
            place.name,
        )

    # 分组标签的排序：本级在前，其次按名称，州直属压尾
    rank = {"（州本级）": "0", "（府本级）": "1", "（州直属）": "9"}

    def fu_key(name: str) -> str:
        return rank.get(name, "5") + name

    lines = [
        "---",
        "type: dashboard",
        "title: 实体分布",
        "name: 实体分布",
        "generated_by: 00_System/scripts/link_entities_to_map.py",
        "---",
        "",
        "# 实体分布",
        "",
        "> **自动生成，不要手改。** 本页由实体档案的地点字段与正文的出场记录算出，",
        "> `novel rebuild-index`、`novel publish` 之后会自动刷新，也可以手动跑",
        "> `python 00_System/scripts/link_entities_to_map.py`。",
        "",
        "## 统计",
        "",
        f"- 实体总数：**{len(entities)}**（人物 / 势力 / 物品 / 生物）",
        f"- 已挂到地点：**{len(located)}**"
        f"（档案直接写出 {sum(1 for e in entities if e.places - set(e.via))}"
        f"／沿所属势力推断 {sum(1 for e in entities if e.places and not (e.places - set(e.via)))}）",
        f"- 尚未挂地点：**{len(orphan)}**",
        f"- 地点档案：**{len(places)}**",
        f"- 已入正文的章节：**{len(chapters)}**",
        "",
        "## 按地理层级",
        "",
    ]

    grouped: dict[str, dict[str, list[tuple[str, str, list[Entity]]]]] = defaultdict(lambda: defaultdict(list))
    for pid, members in by_place.items():
        state_name, fu_name, place_name = order(pid)
        grouped[state_name][fu_name].append(
            (place_name, pid, sorted(members, key=lambda e: e.id))
        )

    for state_name in sorted(grouped):
        lines.append(f"### {state_name}")
        for fu_name in sorted(grouped[state_name], key=fu_key):
            lines.append(f"#### {fu_name}")
            for place_name, pid, members in sorted(grouped[state_name][fu_name], key=lambda x: x[0]):
                place = places[pid]
                detail = []
                for kind in ("势力", "人物", "物品", "生物"):
                    group = [e for e in members if e.kind == kind]
                    if group:
                        detail.append(f"{kind} {len(group)}")
                lines.append(f"- [[{place.path.stem}|{place_name}]]（{'／'.join(detail)}）")
            lines.append("")

    if orphan:
        lines += ["## 尚未挂地点的实体", "", "> 档案里没有可识别的地点字段。补 `据点`／`故乡`／`栖息地` 后会自动归位。", ""]
        for kind in ("势力", "人物", "物品", "生物"):
            group = [e for e in orphan if e.kind == kind]
            if not group:
                continue
            lines.append(f"- **{kind}**（{len(group)}）：" + "、".join(f"[[{e.stem}|{e.name}]]" for e in group))
        lines.append("")

    if chapters:
        lines += ["## 章节 ↔ 地点", ""]
        for chapter in sorted(chapters):
            stem, title = chapters[chapter]
            here = sorted(
                {pid for e in entities if chapter in e.chapters for pid in e.places}
            )
            cast = [e for e in entities if chapter in e.chapters]
            place_names = "、".join(
                f"[[{places[p].path.stem}|{places[p].name}]]" for p in here if p in places
            )
            lines.append(
                f"- **[[{stem}|{chapter}]]** {title}："
                f"{len(cast)} 个实体出场" + (f"，地点 {place_names}" if place_names else "")
            )
        lines.append("")

    lines += [
        "## 维护",
        "",
        "- 想让某个实体出现在这里：在它的档案里写 `据点`（势力）、`故乡`（人物）、`所在地`（物品）、`栖息地`（生物），",
        "  或者在地点页的「关联」段加上它的双链。两条路都认。",
        "- 想让某章的人物浮出来：正文里写出实体名或它的别名即可，下一轮同步就会算进去。",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- 主流程
def run(cfg: Config, *, write: bool = True) -> dict[str, object]:
    places = load_places(cfg)
    entities = load_entities(cfg)
    resolve_places(entities, places)
    chapters = resolve_chapters(cfg, entities)

    touched = 0
    if write:
        by_place: dict[str, list[Entity]] = defaultdict(list)
        for entity in entities:
            for pid in entity.places:
                by_place[pid].append(entity)

        for pid, place in places.items():
            block = _render_place_block(place, by_place.get(pid, []), places)
            text = vault.read_text(place.path)
            new = _replace_block(text, LOC_BLOCK_START, LOC_BLOCK_END, block)
            if new != text:
                vault.write_atomic(place.path, new)
                touched += 1

        for entity in entities:
            block = _render_entity_block(entity, places, chapters)
            text = vault.read_text(entity.path)
            new = _replace_block(text, ENT_BLOCK_START, ENT_BLOCK_END, block)
            if new != text:
                vault.write_atomic(entity.path, new)
                touched += 1

        map_dir = cfg.vault / "01_Core_Memory" / "Maps"
        map_dir.mkdir(parents=True, exist_ok=True)
        page = map_dir / "实体分布.md"
        text = _render_map_page(entities, places, chapters)
        if not page.exists() or vault.read_text(page) != text:
            vault.write_atomic(page, text)
            touched += 1

    return {
        "entities": len(entities),
        "located": sum(1 for e in entities if e.places),
        "direct": sum(1 for e in entities if e.places - set(e.via)),
        "inferred": sum(1 for e in entities if e.places and not (e.places - set(e.via))),
        "orphan": sum(1 for e in entities if not e.places),
        "places": len(places),
        "chapters": len(chapters),
        "files_touched": touched,
    }
