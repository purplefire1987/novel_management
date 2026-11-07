"""结构完整性校验：单一事实源（SSOT）与可维护性。

为什么需要它
------------
`doctor` 原本只回答「目录在不在、索引有没有」——那是**能不能跑**。它回答不了
「跑得对不对」：同一个 ID 有没有两份档案、同一条关系两处说法是否打架、索引页
是否已经落后于实际文件、受控块有没有被手工编辑搞成半截。

这些问题的共同点是**平时不报错，出事很贵**：重复 ID 会让双链随机跳到其中一份，
索引漂移会让搜索给出已经改过的旧值。所以把它们固化成一次可重跑的体检。

两类维度分开计分（与 `09_Audit_Reports/知识库结构体检` 的口径一致）：

    SSOT      一份事实只有一个权威位置，其余都是投影
    可维护性   新增/改名/移动之后，没人需要靠记忆去补别的地方

只读，不写任何档案；报告由调用方决定落不落盘。

唯一的写操作是 `.novel_system/doc_cache.json.gz`——那是「上次读到的正文」，
不是事实源，删掉下次自动重建。全库 900+ 档案、单次读盘约 19ms，不复用这份
缓存，每跑一次体检就要付二十几秒。
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .. import vault
from ..config import Config
from ..links import WIKILINK
from .schema import CANON_STATUSES, Issue

ENTITY_DIRS = ("Characters", "Factions", "Items", "Enemies", "Locations", "Skills", "Destinies")
AUTO_OPEN = re.compile(r"<!-- AUTO:([A-Z-]+) -->")
AUTO_CLOSE = re.compile(r"<!-- /AUTO:([A-Z-]+) -->")
H1 = re.compile(r"^#\s+(.+?)\s*$", re.M)
SKIP_DIRS = {".git", ".obsidian", ".novel_system", ".pytest_cache", "__pycache__", "99_Archive", "00_System"}


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def add(self, severity: str, category: str, detail: str, refs: list[str] | None = None) -> None:
        self.issues.append(Issue(severity=severity, category=category, detail=detail, refs=refs or []))

    @property
    def by_category(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for issue in self.issues:
            out[issue.category] += 1
        return dict(sorted(out.items()))

    @property
    def blockers(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "BLOCKER"]

    def to_dict(self) -> dict[str, object]:
        return {
            "issues": [i.to_dict() for i in self.issues],
            "stats": self.stats,
            "by_category": self.by_category,
            "total": len(self.issues),
        }


def _docs(cfg: Config) -> list[Path]:
    out: list[Path] = []
    for subdir in ENTITY_DIRS:
        directory = cfg.vault / "01_Core_Memory" / subdir
        if not directory.is_dir():
            continue
        out.extend(sorted(p for p in directory.glob("*.md") if not p.name.startswith(".")))
    return out


def _all_markdown(cfg: Config) -> list[Path]:
    out: list[Path] = []
    for path in vault.iter_markdown(cfg.vault):
        if any(part in SKIP_DIRS for part in path.relative_to(cfg.vault).parts):
            continue
        if path.name.startswith("."):
            continue
        out.append(path)
    return out


# ------------------------------------------------------------------ 各项检查
def _check_identity(cfg: Config, docs: list[Path], report: Report) -> dict[str, dict]:
    """ID 唯一、文件名前缀与 ID 一致、name 与 H1 一致、canon_status 合法。"""
    by_id: dict[str, list[Path]] = defaultdict(list)
    records: dict[str, dict] = {}

    for path in docs:
        try:
            doc = vault.read_doc(path)
        except Exception:  # noqa: BLE001
            report.add("MAJOR", "坏档", f"无法解析 frontmatter：{path.name}", [path.stem])
            continue
        eid = str(doc.meta.get("id") or "").strip()
        if not eid:
            report.add("MAJOR", "缺ID", f"frontmatter 没有 id：{path.name}", [path.stem])
            continue
        by_id[eid].append(path)
        records[eid] = {
            "path": path,
            "name": str(doc.meta.get("name") or ""),
            "body": doc.body,
            "type": str(doc.meta.get("type") or ""),
        }

        prefix = path.stem.split("_")[0]
        if prefix != eid:
            report.add(
                "MINOR",
                "命名不一致",
                f"{path.name} 的文件名前缀是 {prefix}，frontmatter id 是 {eid}",
                [path.stem],
            )

        name = str(doc.meta.get("name") or "").strip()
        head = H1.search(doc.body)
        if name and head and head.group(1).strip() != name:
            report.add(
                "MINOR",
                "标题不一致",
                f"{eid} 的 frontmatter name「{name}」≠ 正文 H1「{head.group(1).strip()}」",
                [path.stem],
            )

        status = doc.meta.get("canon_status")
        if status and str(status) not in CANON_STATUSES:
            report.add(
                "MAJOR",
                "枚举越界",
                f"{eid} 的 canon_status「{status}」不在 {sorted(CANON_STATUSES)} 内",
                [path.stem],
            )

    for eid, paths in sorted(by_id.items()):
        if len(paths) > 1:
            report.add(
                "BLOCKER",
                "ID重复",
                f"{eid} 有 {len(paths)} 份档案，双链只会命中其中一份",
                [p.name for p in paths],
            )

    # 同类型内重名：不影响双链（stem 不同），但会让人搜错。
    # 跨类型同名是正常的（技能「正本通识」与物品「正本通识」本是两样东西），
    # 所以分组键带上 type。
    name_map: dict[tuple[str, str], list[str]] = defaultdict(list)
    for eid, rec in records.items():
        if rec["name"]:
            name_map[(rec["type"], rec["name"])].append(eid)
    for key, ids in sorted(name_map.items()):
        if len(ids) > 1:
            report.add("MINOR", "重名", f"{key[0]}「{key[1]}」被 {len(ids)} 个实体共用", sorted(ids))

    report.stats["档案总数"] = len(docs)
    # 口径分开数：地点、技能、命格各自成类，混在一起会让所有对比数字失真。
    # 「实体数」只数地图投影覆盖的四类（人物/势力/物品/生物），这样它能和
    # `实体分布.md` 的统计行直接对得上。
    def _count(*dirs: str) -> int:
        return sum(1 for p in docs if p.parent.name in dirs)

    report.stats["实体数"] = _count("Characters", "Factions", "Items", "Enemies")
    report.stats["地点数"] = _count("Locations")
    report.stats["技能数"] = _count("Skills")
    report.stats["命格数"] = _count("Destinies")
    report.stats["唯一ID"] = len(by_id)
    return records


def _check_links(cfg: Config, docs: list[Path], report: Report) -> None:
    """断链 + 受控块配对。全库扫一遍，但只报实体档案里的问题。"""
    files = _all_markdown(cfg)
    # 可解析目标：文件名去扩展名、相对路径去扩展名、完整相对路径（含 .canvas 这类非 md）
    known: set[str] = set()
    for path in cfg.vault.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        rel = path.relative_to(cfg.vault)
        known.add(path.name)
        known.add(path.stem)
        known.add(str(rel))
        known.add(str(rel.with_suffix("")))
    stems = {p.stem for p in files}
    broken: list[tuple[str, str]] = []
    unpaired: list[str] = []

    for path in files:
        try:
            text = vault.read_text(path)
        except Exception:  # noqa: BLE001
            continue
        for hit in WIKILINK.finditer(text):
            # 表格里的双链会写成 [[X\|别名]]，反斜杠是转义符，不属于目标名
            target = hit.group(1).strip().rstrip("\\").strip()
            # 文档里的占位示例 [[...]] / [[X]] 不是真链，跳过
            if not target or set(target) <= {".", "…", "X", "x", " "}:
                continue
            if target in known:
                continue
            if (cfg.vault / target).exists() or (cfg.vault / f"{target}.md").exists():
                continue
            # 章节链接允许写成 [[CH-0001]]，可解析到带标题的正文文件
            if target.startswith("CH-") and any(s.startswith(target + "_") for s in stems):
                continue
            broken.append((str(path.relative_to(cfg.vault)), target))

        opens = [m.group(1) for m in AUTO_OPEN.finditer(text)]
        closes = [m.group(1) for m in AUTO_CLOSE.finditer(text)]
        if sorted(opens) != sorted(closes):
            unpaired.append(path.name)

    if broken:
        sample = "、".join(f"[[{t}]]" for _p, t in broken[:5])
        report.add(
            "MAJOR",
            "断链",
            f"{len(broken)} 条双链指向不存在的文件（例：{sample}）",
            sorted({p for p, _t in broken})[:8],
        )
    if unpaired:
        report.add(
            "MAJOR",
            "受控块残缺",
            f"{len(unpaired)} 个文件的 AUTO 标记块没有成对闭合，下次同步会重复追加",
            unpaired[:8],
        )
    report.stats["断链"] = len(broken)


def _check_relations(cfg: Config, report: Report) -> None:
    """关系三查：指向不存在、单向未回链、唯一归属打架。"""
    from ..workflow import relation_sync

    dangling = relation_sync.dangling(cfg)
    # 「势力所属」里可能只写了类别（宗门、门阀、邪派…），那不是「待建档的势力」，
    # 是分类标签。不过滤掉的话，每个门阀都会挂一条假缺口，把真缺口埋了。
    cats = _faction_categories(cfg)

    def _category_only(d: dict) -> bool:
        # 只在势力卡上成立：人物卡的「势力」必须是真的势力，类别词算缺口
        if d.get("kind") != "势力":
            return False
        if d.get("field") not in ("势力所属", "所属势力", "势力"):
            return False
        text = str(d.get("value") or "")
        if "[[" in text:
            return False
        toks = [t.strip() for t in re.split(r"[,，、]", text) if t.strip()]
        return bool(toks) and all(t in cats for t in toks)

    dangling = [d for d in dangling if not _category_only(d)]
    if dangling:
        # 分两级：写了双链却解析不到 = 链错了（MAJOR）；
        # 只写了名字解析不到 = 还没建档（MINOR），属于知识缺口而不是错误。
        linked = [d for d in dangling if "[[" in str(d["value"])]
        named = [d for d in dangling if "[[" not in str(d["value"])]
        if linked:
            sample = "；".join(f"{d['node']}.{d['field']}=「{d['value']}」" for d in linked[:3])
            report.add(
                "MAJOR",
                "关系悬空",
                f"{len(linked)} 个关系字段写了双链但指向不存在的档案（例：{sample}）",
                [str(d["node"]) for d in linked[:8]],
            )
        if named:
            sample = "；".join(f"{d['node']}.{d['field']}=「{d['value']}」" for d in named[:4])
            report.add(
                "MINOR",
                "关系待建档",
                f"{len(named)} 个关系字段只写了名字、库里还没有对应档案（例：{sample}）",
                [str(d["node"]) for d in named[:8]],
            )

    nodes, by_id = relation_sync.load_nodes(cfg)
    edges = relation_sync.collect_declarations(nodes, by_id)
    pairs = {(e.src, e.dst, e.label) for e in edges}

    # 单向：A 声明了 B，B 那一侧没有任何回指。多半是只补了一头，不算错。
    one_way = 0
    for edge in edges:
        back_labels = {
            "所属势力": {"成员"},
            "持有": {"持有者"},
            "成员": {"所属势力"},
            "持有者": {"持有"},
        }.get(edge.label)
        if back_labels and not any((edge.dst, edge.src, b) in pairs for b in back_labels):
            one_way += 1
    if one_way:
        report.add(
            "MINOR",
            "关系单向",
            f"{one_way} 条关系只有单侧声明（不影响使用，反向索引会自动补全）",
        )

    # 唯一归属：一件物品被两个人声称持有，或一个人同时属于两个互斥势力
    holders: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge.label in ("持有", "持有者"):
            item = edge.dst if edge.label == "持有" else edge.src
            person = edge.src if edge.label == "持有" else edge.dst
            holders[item].add(person)
    conflicts = {item: ps for item, ps in holders.items() if len(ps) > 1}
    for item, people in sorted(conflicts.items())[:12]:
        report.add(
            "MAJOR",
            "归属冲突",
            f"{item} 同时被 {len(people)} 个实体声明持有：{sorted(people)}",
            [item, *sorted(people)],
        )

    report.stats["关系边"] = len(edges)
    report.stats["悬空关系字段"] = len(dangling)
    _check_faction_relations(cfg, report, nodes, by_id, edges)


FACTION_CATEGORY_CONFIG = "00_System/Config/faction_categories.yaml"


def _faction_categories(cfg: Config) -> set[str]:
    path = cfg.vault / FACTION_CATEGORY_CONFIG
    if not path.exists():
        return set()
    data = yaml.safe_load(vault.read_text(path)) or {}
    return {str(x).strip() for x in (data.get("categories") or []) if str(x).strip()}


def _check_faction_relations(
    cfg: Config,
    report: Report,
    nodes: list,
    by_id: dict,
    edges: list,
) -> None:
    """势力之间的四类毛病。

    势力卡的 `势力所属` 一栏是旧表格沿用的，同时装着「类别」和「上级」两种东西；
    关系引擎按「这个名字有没有对应档案」来分辨。这个办法只在**类别词恰好不和
    任何势力重名**时成立，所以这里把前提显式验一遍：

    1. 类别词撞上势力名   → 层级会被静默算错（MAJOR）
    2. 类别词没登记       → 多半是笔误或势力改过名（MINOR）
    3. 层级自相矛盾       → A 说 B 是它下级，B 却说自己的上级不是 A（MAJOR）
    4. 立场自相矛盾       → 同一对势力同时被记为盟友／中立／敌对中的两种（MAJOR）

    第 3、4 条今天都是 0——写进来是为了将来改设定时不至于无声出错。
    """
    from ..workflow import relation_sync

    factions = [n for n in nodes if n.kind == "势力"]
    names = {n.name: n.id for n in factions}
    cats = _faction_categories(cfg)

    # ---- 1) 类别词与势力同名
    clash = sorted(cats & set(names))
    if clash:
        report.add(
            "MAJOR",
            "类别撞名",
            f"{len(clash)} 个类别词同时也是势力名（{'、'.join(clash)}）——"
            "势力卡的「势力所属」会把它们解析成上级，层级会算错。"
            f"要么把它们从 {FACTION_CATEGORY_CONFIG} 移到势力卡，要么给势力改名",
            clash,
        )

    # ---- 2) 既非类别、也非势力的词
    unknown: dict[str, list[str]] = defaultdict(list)
    for n in factions:
        fields = dict(relation_sync._fields(relation_sync._read_body(n.path)))
        for tok in re.split(r"[,，、]", fields.get("势力所属", "")):
            tok = tok.strip()
            if not tok or tok in names or tok in cats:
                continue
            unknown[tok].append(n.id)
    if unknown:
        detail = "；".join(
            f"「{k}」（{len(v)} 张）" for k, v in sorted(unknown.items(), key=lambda kv: -len(kv[1]))[:5]
        )
        report.add(
            "MINOR",
            "势力类别未登记",
            f"{len(unknown)} 个词既不是已有势力、也不在类别白名单里：{detail}——"
            f"要么加进 {FACTION_CATEGORY_CONFIG}，要么是笔误、或那个势力已经改名/删除",
            [i for v in unknown.values() for i in v][:8],
        )

    # ---- 3) 层级矛盾
    parents: dict[str, set[str]] = defaultdict(set)
    children: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        if e.label == "上级":
            parents[e.src].add(e.dst)
            children[e.dst].add(e.src)
        elif e.label == "下级":
            children[e.src].add(e.dst)
            parents[e.dst].add(e.src)

    # 只有「A 单方面把 B 记为下级，而 B 明确写了别的上级、且没提 A」才算矛盾。
    # 多个上级本身合法——北府军同时归李家与帝室，是共管不是错误。
    for parent, kids in sorted(children.items()):
        for kid in sorted(kids):
            declared = parents.get(kid, set())
            if declared and parent not in declared and len(declared) == 1:
                only = next(iter(declared))
                report.add(
                    "MAJOR",
                    "层级矛盾",
                    f"{_name(by_id, parent)} 把 {_name(by_id, kid)} 记为下级，"
                    f"但 {_name(by_id, kid)} 只申报了 {_name(by_id, only)} 一个上级",
                    [parent, kid, only],
                )

    # 互相当对方上级 = 环
    for a, ups in parents.items():
        for b in ups:
            if a in parents.get(b, set()):
                report.add(
                    "MAJOR", "层级成环",
                    f"{_name(by_id, a)} 与 {_name(by_id, b)} 互称上级",
                    [a, b],
                )

    # ---- 4) 立场矛盾：盟友 / 中立 / 敌对 三者两两互斥
    #
    # 一对势力只能有一个横向立场。同时写两条通常不是笔误，而是**改设定时只加新行、
    # 忘了删旧行**——页面上就会出现「盟友 + 中立」两个互相打脸的标签。所以按 MAJOR
    # 报出来，由作者决定哪条算数。
    #
    # 前两条沿用旧类别名「恩怨矛盾」，与历史体检报告可比；牵涉中立的用「立场矛盾」。
    stance = {
        label: {frozenset((e.src, e.dst)) for e in edges if e.label == label}
        for label in ("盟友", "中立", "敌对")
    }
    for labels in (("盟友", "敌对"), ("盟友", "中立"), ("中立", "敌对")):
        category = "恩怨矛盾" if labels == ("盟友", "敌对") else "立场矛盾"
        for pair in sorted(stance[labels[0]] & stance[labels[1]], key=lambda p: sorted(p)):
            a, b = sorted(pair)
            report.add(
                "MAJOR", category,
                f"{_name(by_id, a)} 与 {_name(by_id, b)} 同时被记为{labels[0]}和{labels[1]}",
                [a, b],
            )

    # ---- 覆盖率：有多少势力一条对外关系都没有
    linked = {i for e in edges if e.src_kind == "势力" and e.dst_kind == "势力" for i in (e.src, e.dst)}
    isolated = [n.id for n in factions if n.id not in linked]
    report.stats["势力数"] = len(factions)
    report.stats["势力间关系边"] = sum(1 for e in edges if e.src_kind == "势力" and e.dst_kind == "势力")
    report.stats["孤立势力"] = len(isolated)


def _name(by_id: dict, ident: str) -> str:
    node = by_id.get(ident)
    return node.name if node is not None else ident


def _check_projection(cfg: Config, report: Report) -> None:
    """索引与派生页是否落后于实际档案（漂移检测）。"""
    docs = _docs(cfg)
    index_db = cfg.index_db
    report.stats["索引库存在"] = int(index_db.exists())

    # 派生页的统计行必须与实算一致，否则「一眼看过去」的数字会骗人
    # 口径必须和 map_sync 一致：只数人物/势力/物品/生物，不含地点档案
    entity_count = report.stats.get("实体数", 0)
    map_page = cfg.vault / "01_Core_Memory" / "Maps" / "实体分布.md"
    if map_page.exists():
        text = vault.read_text(map_page)
        hit = re.search(r"实体总数：\*\*(\d+)\*\*", text)
        if hit and int(hit.group(1)) != entity_count:
            report.add(
                "MINOR",
                "投影漂移",
                f"实体分布页记的实体总数 {hit.group(1)}，实际 {entity_count}；跑一次 rebuild-index 即可",
                ["实体分布"],
            )

    # AUTO 块存在但内容为空 = 渲染逻辑没覆盖到，或者档案字段全被删了
    empty_blocks: list[str] = []
    for path in docs:
        text = vault.read_text(path)
        for match in re.finditer(r"<!-- AUTO:([A-Z-]+) -->(.*?)<!-- /AUTO:\1 -->", text, re.S):
            inner = match.group(2).strip()
            if len(inner) < 12:
                empty_blocks.append(f"{path.stem}:{match.group(1)}")
    if empty_blocks:
        report.add(
            "MINOR",
            "空受控块",
            f"{len(empty_blocks)} 个 AUTO 块渲染为空，通常说明声明字段被删光了",
            empty_blocks[:8],
        )


def _check_catalog(cfg: Config, report: Report) -> None:
    """技能卡必须归属到某个技能库；命格不做强制约束（未分配是常态）。"""
    from ..workflow import catalog_sync, relation_sync

    broken = catalog_sync.dangling(cfg)
    if broken:
        sample = "；".join(f"{b['node']}.subtype=「{b['subtype']}」" for b in broken[:4])
        report.add(
            "MAJOR",
            "技能无库",
            f"{len(broken)} 张技能卡的 subtype 在 Skills/ 里找不到同名技能库（例：{sample}）",
            [str(b["node"]) for b in broken[:8]],
        )

    nodes, by_id = relation_sync.load_nodes(cfg)
    users: dict[str, int] = defaultdict(int)
    holders: dict[str, int] = defaultdict(int)
    resolver = relation_sync.Resolver(nodes, by_id)
    for node in nodes:
        if node.kind not in ("人物", "生物"):
            continue
        for key, value in relation_sync._fields(relation_sync._read_body(node.path)):
            if not value:
                continue
            if key == "技能":
                for hit in resolver.many(value, "技能"):
                    users[hit.id] += 1
            elif key == "命格" and node.kind == "人物":
                for hit in resolver.many(value, "命格"):
                    holders[hit.id] += 1

    skills = [n for n in nodes if n.kind == "技能"]
    fates = [n for n in nodes if n.kind == "命格"]
    report.stats["已挂人物技能"] = f"{sum(1 for n in skills if users.get(n.id))}/{len(skills)}"
    report.stats["已定人命格"] = f"{sum(1 for n in fates if holders.get(n.id))}/{len(fates)}"


def _check_hygiene(cfg: Config, report: Report) -> None:
    """临时文件、空目录。这两类最容易在批量操作后悄悄堆积。"""
    temps = [
        str(p.relative_to(cfg.vault))
        for p in cfg.vault.rglob("*")
        if p.is_file() and (p.name.startswith(".tmp_") or p.name.endswith(".tmp"))
        and ".git" not in p.parts
    ]
    if temps:
        report.add("MINOR", "临时残留", f"{len(temps)} 个写入残留文件未被清理", temps[:8])

    assets = cfg.vault / "05_Story_Assets"
    empty: list[str] = []
    if assets.is_dir():
        for child in sorted(assets.iterdir()):
            if child.is_dir() and not any(child.rglob("*.md")):
                empty.append(child.name)
    if empty:
        report.add(
            "MINOR",
            "素材目录空缺",
            f"05_Story_Assets 下 {len(empty)} 个目录没有素材卡：{'、'.join(empty)}",
            empty,
        )
    report.stats["临时文件"] = len(temps)
    report.stats["空素材目录"] = len(empty)


# ------------------------------------------------------------------ 主流程
def _check_config_drift(cfg: Config, report: Report) -> None:
    """配置与「被迫抄了一份」的地方对得上吗。

    Templater 模板跑在 Obsidian 里，读不了 YAML，只能把 ID 前缀和目录写死一份。
    写死就会漂移，而且漂得没有声音：模板出 `BIO-0001`，`id_rules.yaml` 认的是
    `ENEMY`，索引器按 type 判类时这张卡就掉出实体之外。这里只比对、只报，不改文件。
    """
    tpl = cfg.vault / "11_Templates/T-新建档案.md"
    if not tpl.exists():
        return
    text = vault.read_text(tpl)
    pairs = re.findall(r'type:\s*"([a-z_]+)",\s*prefix:\s*"([A-Z-]+)"', text)
    dirs = re.findall(r'dir:\s*"([^"]+)"', text)
    prefixes = {k: str(v) for k, v in (cfg.id_rules.get("prefixes") or {}).items()}
    tops = {
        str(v).split("/")[0]
        for v in (cfg.system.get("paths") or {}).values()
        if isinstance(v, str)
    }

    drift: list[str] = []
    for idx, (doc_type, prefix) in enumerate(pairs):
        want = prefixes.get(doc_type, "").split("-")[0]
        if want and want != prefix:
            drift.append(f"{doc_type}：模板写 {prefix}，id_rules.yaml 是 {want}")
        if idx < len(dirs):
            top = dirs[idx].split("/")[0]
            if top and top not in tops:
                drift.append(f"{doc_type}：目录 {dirs[idx]} 不在 system_config.paths 里")

    missing = [t for t in ("character", "location", "faction", "item", "enemy", "skill", "destiny")
               if t in prefixes and t not in {p[0] for p in pairs}]
    if missing:
        drift.append("模板缺类型：" + "、".join(missing))

    if drift:
        report.add(
            "MINOR",
            "配置漂移",
            f"Templater 模板与配置有 {len(drift)} 处不一致（改配置时忘了同步模板，新建的档案会带错前缀）",
            drift[:6],
        )
    report.stats["配置漂移"] = len(drift)


def _check_garrison_drift(cfg: Config, report: Report) -> None:
    """驻军表与军团卡对得上吗。

    `WORLD-0041_各州驻军表` 是派生表，由 `scripts/gen_garrison_table.py` 从军团卡生成。
    改了卡忘了重跑，表就静默过期——而且数字看着依然精确，比空表更危险。
    这里只比对支数与总人数，不改文件。
    """
    table = cfg.vault / "01_Core_Memory/World/九州/总表/WORLD-0041_各州驻军表.md"
    if not table.exists():
        return
    total = count = 0
    for p in (cfg.vault / "01_Core_Memory/Factions").glob("FAC-*.md"):
        text = vault.read_text(p)
        if "subtype: 军团" not in text[:900]:
            continue
        m = re.search(r"^-\s*\*\*军团人数\*\*：([\d,]+)", text, re.M)
        if m:
            total += int(m.group(1).replace(",", ""))
            count += 1
    body = vault.read_text(table)
    mt = re.search(r"\*\*军团\*\*：(\d+) 支", body)
    mn = re.search(r"\*\*总兵力\*\*：([\d,]+) 人", body)
    if not mt or not mn:
        report.add("MINOR", "驻军表格式",
                   "WORLD-0041 缺「军团 N 支 / 总兵力 N 人」两行，无法比对", [])
        return
    got_c, got_n = int(mt.group(1)), int(mn.group(1).replace(",", ""))
    if (got_c, got_n) != (count, total):
        report.add(
            "MINOR",
            "驻军表漂移",
            f"驻军表写 {got_c} 支 / {got_n:,} 人，军团卡实为 {count} 支 / {total:,} 人",
            ["重跑：00_System/scripts/gen_garrison_table.py"],
        )


# ---------------------------------------------------------------- 场景链
# `[ \t]*` 而不是 `\s*`：`\s` 会跨行，空字段会把下一行整行吞成自己的值。
SCENE_FIELD = re.compile(r"^-\s*\*\*(.+?)\*\*[：:][ \t]*(.*)$", re.M)
SCENE_REF = re.compile(r"SCENE-[A-Za-z0-9-]*[A-Za-z0-9]")
CN_DIGIT = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
CN_MONTH = {"正": 1, "腊": 12}
DAY_RE = re.compile(r"(正|腊|[一二三四五六七八九十]{1,3})月(初[一二三四五六七八九十]{1,3}|[一二三四五六七八九十]{1,3})")
HM_RE = re.compile(r"(\d{1,2}):(\d{2})")


def _cn_num(text: str) -> int:
    """「八」「二十」「三十」「正」「腊」→ 数字。只覆盖月日够用的范围。"""
    if text in CN_MONTH:
        return CN_MONTH[text]
    if "十" in text:
        tens, _, ones = text.partition("十")
        return (CN_DIGIT.get(tens, 1) if tens else 1) * 10 + (CN_DIGIT.get(ones, 0) if ones else 0)
    return CN_DIGIT.get(text, 0)


def _scene_time(text: str) -> tuple[tuple[int, int] | None, int | None, int | None]:
    """把「七月初八夜 21:00–21:30」「21:45」拆成 (日期, 起始分钟, 结束分钟)。

    日期可以没有——省掉的那一场默认沿用上一场的日子，这也正是时间倒退最容易藏身的地方。
    """
    day = None
    match = DAY_RE.search(text)
    if match:
        day = (_cn_num(match.group(1)), _cn_num(match.group(2).lstrip("初")))
    clocks = [(int(h), int(m)) for h, m in HM_RE.findall(text)]
    if not clocks:
        return day, None, None
    start = clocks[0][0] * 60 + clocks[0][1]
    end = clocks[1][0] * 60 + clocks[1][1] if len(clocks) > 1 else start
    if end < start:  # 22:00–01:00 这种跨夜区间
        end += 24 * 60
    return day, start, end


def _check_scene_chain(cfg: Config, report: Report) -> None:
    """场景之间的接缝。

    断裂几乎都发生在换场那一行：上一场湿透的衣裳下一场干了，人凭空换了个地方，
    时间倒退半小时。这三样在成稿里都要重写整段，在细纲里只是改一行字。
    所以查在细纲上——`sequence` 连不连、时间退不退、换了地点有没有交代怎么到的。
    """
    directory = cfg.vault / "04_Outline/Scenes"
    if not directory.is_dir():
        return

    by_chapter: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(directory.glob("SCENE-*.md")):
        doc = vault.read_doc(path)
        fields = {m.group(1).strip(): m.group(2).strip() for m in SCENE_FIELD.finditer(doc.body)}
        seq = doc.meta.get("sequence")
        chapter = doc.meta.get("chapter_id")
        sid = str(doc.meta.get("id") or path.stem)
        if not isinstance(seq, int) or not chapter:
            report.add("MINOR", "场景卡片", f"{path.name} 缺 sequence 或 chapter_id", [sid])
            continue
        by_chapter[str(chapter)].append({
            "id": sid,
            "seq": seq,
            "place": fields.get("地点", ""),
            "time": fields.get("时间", ""),
            "entry": fields.get("承接上文", ""),
            "next": fields.get("与下一场景的连接", ""),
        })

    report.stats["场景"] = sum(len(v) for v in by_chapter.values())
    claimed: dict[str, str] = {}

    for chapter, items in sorted(by_chapter.items()):
        items.sort(key=lambda s: s["seq"])
        seqs = [s["seq"] for s in items]
        if seqs != list(range(1, len(items) + 1)):
            report.add(
                "MAJOR", "场景跳号",
                f"{chapter} 的 sequence 是 {seqs}，应为 1..{len(items)}"
                "（叙事顺序断了，读者会在两条线之间来回跳）",
                [s["id"] for s in items],
            )

        prev_day: tuple[int, int] | None = None
        prev_end: int | None = None
        for idx, sc in enumerate(items):
            day, start, end = _scene_time(sc["time"])
            if start is not None and prev_end is not None:
                same_day = day is None or prev_day is None or day == prev_day
                if same_day and start < prev_end:
                    report.add(
                        "MAJOR", "场景时间倒退",
                        f"{sc['id']} 的「{sc['time']}」早于上一场结束 "
                        f"{prev_end // 60:02d}:{prev_end % 60:02d}，且没有交代日期推进",
                        [sc["id"]],
                    )
            if day is not None:
                if prev_day is not None and day < prev_day:
                    report.add(
                        "MAJOR", "场景时间倒退",
                        f"{sc['id']} 的日期 {day[0]}月{day[1]}日 早于上一场的 "
                        f"{prev_day[0]}月{prev_day[1]}日",
                        [sc["id"]],
                    )
                prev_day = day
            if start is not None:
                prev_end = end if end is not None else start

            if idx > 0:
                prev = items[idx - 1]
                if sc["place"] and prev["place"] and sc["place"] != prev["place"] and not sc["entry"]:
                    report.add(
                        "MINOR", "场景接缝缺失",
                        f"{sc['id']} 换了地点却没写「承接上文」：人是怎么到的、中间隔了多久",
                        [sc["id"]],
                    )

            refs = SCENE_REF.findall(sc["next"])
            for ref in refs:
                if ref in claimed and claimed[ref] != sc["id"]:
                    report.add(
                        "MAJOR", "场景重入",
                        f"{ref} 同时被 {claimed[ref]} 和 {sc['id']} 当成下一场"
                        "（两条线撞进同一场，读者会来回跳）",
                        [ref],
                    )
                else:
                    claimed[ref] = sc["id"]

            if idx + 1 < len(items):
                want = items[idx + 1]["id"]
                if refs and refs[0] != want:
                    report.add(
                        "MAJOR", "场景跳号",
                        f"{sc['id']} 写「接 {refs[0]}」，但按 sequence 下一场是 {want}",
                        [sc["id"]],
                    )
                elif not refs:
                    report.add(
                        "MINOR", "场景接缝缺失",
                        f"{sc['id']} 没写「与下一场景的连接」",
                        [sc["id"]],
                    )
            elif refs and any(r.startswith(f"SCENE-{chapter}-") for r in refs):
                report.add(
                    "MINOR", "场景接缝缺失",
                    f"{sc['id']} 已是 {chapter} 最后一场，却仍指向本章的 {refs[0]}",
                    [sc["id"]],
                )


# ------------------------------------------------------------------ 正文
# `[ \t]` 而不是 `\s`：`\s` 跨行，会把标题后的空行吞进匹配里，`$` 就落不到行尾。
MANUSCRIPT_BODY = re.compile(r"^##[ \t]*正文[ \t]*$(.*?)(?=^##[ \t]|\Z)", re.S | re.M)
HAN = re.compile(r"[\u4e00-\u9fa5]")
# 「先否定、再更正」：不是 A，是 B／而是 B／这是 B／那是 B。
# 反过来的「是 B，不是 A」不在此列——那是先看见再排除，读感不一样。
NOT_BUT = re.compile(r"不是[^，。！？；\n]{1,20}[，,]\s*(?:而是|这是|那是|是)")
CHAPTER_LIMITS = (3000, 4000)
STYLE_DENSITY_MAX = 1.0


def _check_manuscript(cfg: Config, report: Report) -> None:
    """正文的篇幅与句式密度。

    口径写在 `00_System/Prompts/writer.md`（唯一出口），这里只把它变成可查的数字：
    3,000–4,000 汉字、「不是A，是B」每千字不超过 1 处。
    字数不对不代表写得差，但它一定是**没按批准的口径交**——要么没写透，要么该拆章。
    """
    low, high = CHAPTER_LIMITS
    files = sorted((cfg.vault / "08_Manuscripts").rglob("CH-*.md"))
    for path in files:
        doc = vault.read_doc(path)
        match = MANUSCRIPT_BODY.search(doc.body)
        body = match.group(1) if match else ""
        han = len(HAN.findall(body))
        if not han:
            continue
        cid = str(doc.meta.get("id") or path.stem)
        declared = doc.meta.get("word_count")
        if isinstance(declared, int) and declared and abs(declared - han) / han > 0.03:
            report.add(
                "MINOR", "字数漂移",
                f"{cid} 的 word_count 写 {declared:,}，正文实为 {han:,} 汉字",
                [cid],
            )
        if not low <= han <= high:
            report.add(
                "MINOR", "篇幅越界",
                f"{cid} 正文 {han:,} 汉字，规范是 {low:,}–{high:,}（writer.md 第一节）",
                [cid],
            )
        density = len(NOT_BUT.findall(body)) * 1000 / han
        if density > STYLE_DENSITY_MAX:
            report.add(
                "MINOR", "句式密度",
                f"{cid}「不是A，是B」{density:.2f} 处/千字，上限 {STYLE_DENSITY_MAX:.0f} 处/千字",
                [cid],
            )


# 检查清单：(名字, 归属范围, 调用)。
# "fast" 只跑能在本机立刻发现问题的那几项；"full" 才跑跨档案汇总类检查。
# 两个范围共用同一份函数，口径不会分叉。
_CHECKS = (
    ("ID 与命名", "fast", lambda c, d, r: _check_identity(c, d, r)),
    ("双链解析", "fast", lambda c, d, r: _check_links(c, d, r)),
    ("受控块卫生", "fast", lambda c, d, r: _check_hygiene(c, r)),
    ("场景接缝", "fast", lambda c, d, r: _check_scene_chain(c, r)),
    ("正文篇幅", "fast", lambda c, d, r: _check_manuscript(c, r)),
    ("关系图", "full", lambda c, d, r: _check_relations(c, r)),
    ("索引页", "full", lambda c, d, r: _check_catalog(c, r)),
    ("关系投影", "full", lambda c, d, r: _check_projection(c, r)),
    ("配置漂移", "full", lambda c, d, r: _check_config_drift(c, r)),
    ("驻军对账", "full", lambda c, d, r: _check_garrison_drift(c, r)),
)
SCOPES = ("fast", "full")


def run(cfg: Config, *, scope: str = "full") -> Report:
    """跑一遍体检。

    `scope="fast"` 只跑「改一篇文章就可能在本地立刻发现」的几项：ID/文件名、
    双链、受控块卫生、场景接缝、正文篇幅。跳过的都是**跨档案汇总**类检查
    （关系图、索引页、配置漂移、驻军对账）——它们只在批量改档时才可能变化，
    却要再扫一遍全库。

    整段包在 `vault.cached_docs()` 里：受限宿主上一次读盘约 19ms，全库 900+
    档案就是 18 秒。不缓存的话 identity / links / relations / projection 各自
    重读一遍，三分钟就是这么来的。
    """
    if scope not in SCOPES:
        raise ValueError(f"未知的体检范围：{scope}（可选：{', '.join(SCOPES)}）")

    report = Report()
    docs = _docs(cfg)
    ran = 0
    with vault.cached_docs(cfg.vault / vault.CACHE_FILE) as cache:
        # 整批预读：各检查项加起来要碰几百份档案，逐份读就是几百次 20ms 的
        # 固定开销。一次原生进程读完之后，下面每一项都只是取缓存。
        cache.prefetch(_all_markdown(cfg), cfg.scratch_dir)
        for _name, need, fn in _CHECKS:
            if scope == "fast" and need != "fast":
                continue
            fn(cfg, docs, report)
            ran += 1
    report.stats["检查项数"] = ran
    report.stats["读盘次数"] = cache.misses
    return report


def checks_for_doctor(report: Report) -> list[dict[str, object]]:
    """裁成 doctor 的 check 形状：只留「过/不过」，细节交给完整报告。"""
    categories = report.by_category
    return [
        {
            "check": "唯一事实源：ID 无重复",
            "ok": categories.get("ID重复", 0) == 0,
            "detail": f"{categories.get('ID重复', 0)} 处重复",
        },
        {
            "check": "唯一事实源：关系可解析",
            "ok": categories.get("关系悬空", 0) == 0,
            "detail": f"{categories.get('关系悬空', 0)} 处悬空字段",
        },
        {
            "check": "唯一事实源：归属不冲突",
            "ok": categories.get("归属冲突", 0) == 0,
            "detail": f"{categories.get('归属冲突', 0)} 处冲突",
        },
        {
            "check": "可维护性：双链完整",
            "ok": categories.get("断链", 0) == 0,
            "detail": f"{report.stats.get('断链', 0)} 条断链",
        },
        {
            "check": "可维护性：受控块闭合",
            "ok": categories.get("受控块残缺", 0) == 0,
            "detail": f"{categories.get('受控块残缺', 0)} 个文件残缺",
        },
    ]


def render_markdown(report: Report, *, title: str = "结构完整性报告") -> str:
    lines = ["# " + title, "", "> 由 `novel integrity` 生成，只读检查，不改档案。", "", "## 统计", ""]
    for key, value in report.stats.items():
        lines.append(f"- {key}：**{value}**")
    lines += ["", "## 问题分布", ""]
    if not report.by_category:
        lines.append("无问题。")
    else:
        for category, count in report.by_category.items():
            lines.append(f"- {category}：{count}")
    lines += ["", "## 明细", ""]
    if not report.issues:
        lines.append("（空）")
    for issue in sorted(report.issues, key=lambda i: (i.severity != "BLOCKER", i.category)):
        refs = f"　`{', '.join(issue.refs)}`" if issue.refs else ""
        lines.append(f"- **[{issue.severity}] {issue.category}**：{issue.detail}{refs}")
    return "\n".join(lines) + "\n"
