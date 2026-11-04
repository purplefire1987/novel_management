"""实体关系投影：人物 ↔ 物品 ↔ 势力。

为什么单独一个模块
------------------
`map_sync` 管的是「谁在哪」，管不了「谁是谁的人、谁拿着什么」。这两类信息在
档案里其实都写了——人物页有 `势力`、`物品`，物品页有 `关联：角色`，势力页有
`关联角色`、`信物`——但它们是**各写各的**：人物说自己属于散人，散人页上并不会
因此出现这个人；物品说归陈默所有，陈默页上也看不到这件东西。

本模块把单向声明补成双向索引：

    权威侧（手写字段）            投影侧（自动块）
    人物.势力          ──────▶    势力页「成员」
    势力.关联角色      ──────▶    人物页「所属势力」
    人物.物品          ──────▶    物品页「持有者」
    物品.关联：角色    ──────▶    人物页「持有物品」
    势力.关联：物品库  ──────▶    物品页「归属势力」

同一台关系两头发言时（人物说“我归 A”，A 的成员表里却没有我），不算错——那只是
**还没补**。真正矛盾（人物说归 A，物品说归 B）由 `validators/integrity.py` 报出来。

写法纪律
--------
只写 `AUTO:RELATIONS` 标记块，块外一个字节不动；块内整块重算，集合显式排序，
重复运行结果逐字节相同（幂等）。字段名以「字段 → 目标类型」的白名单解释，
不做模糊猜测，避免把描述性段落里的偶然同名当成关系。
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .. import vault
from ..config import Config

BLOCK_START = "<!-- AUTO:RELATIONS -->"
BLOCK_END = "<!-- /AUTO:RELATIONS -->"

# 实体类型 → 目录
KIND_DIRS: tuple[tuple[str, str], ...] = (
    ("人物", "Characters"),
    ("势力", "Factions"),
    ("物品", "Items"),
    ("生物", "Enemies"),
)

# 声明字段：键名 → (目标类型, 语义标签)
# 只认这几个键，别的一律不看。新增关系类型时在这里加一行即可。
DECLARATIONS: dict[str, tuple[str, str]] = {
    # 人物侧
    "势力": ("势力", "所属势力"),
    "所属势力": ("势力", "所属势力"),
    "势力所属": ("势力", "所属势力"),
    "物品": ("物品", "持有"),
    "持有物": ("物品", "持有"),
    "随身物品": ("物品", "持有"),
    # 势力侧
    "上级势力": ("势力", "上级"),
    "关联角色": ("人物", "成员"),
    "关联：角色库": ("人物", "成员"),
    "关联：物品库": ("物品", "关联物品"),
    "信物": ("物品", "信物"),
    # 物品侧
    "关联：角色": ("人物", "持有者"),
    "持有者": ("人物", "持有者"),
    "归属": ("势力", "归属势力"),
}

# 同一字段名在不同类型档案下含义不同，这里按「当前档案类型」收窄。
ALLOWED: dict[str, set[str]] = {
    "人物": {"势力", "所属势力", "势力所属", "物品", "持有物", "随身物品"},
    "势力": {"上级势力", "势力所属", "所属势力", "关联角色", "关联：角色库", "关联：物品库", "信物"},
    "物品": {"关联：角色", "持有者", "归属"},
    "生物": set(),
}

WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")
STOP_CHARS = re.compile(r"[、，,；;/／|｜]")

REL_BLOCK_HEADINGS = ("关系（自动）", "关系图谱（自动）")


@dataclass
class Node:
    id: str
    name: str
    kind: str
    path: Path
    aliases: list[str] = field(default_factory=list)

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def label(self) -> str:
        return f"[[{self.stem}|{self.name}]]"


@dataclass(frozen=True)
class Edge:
    """一条有向声明：src --(label)--> dst。"""

    src: str
    dst: str
    label: str
    src_kind: str
    dst_kind: str


# ---------------------------------------------------------------- 读档
def _docs(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.md") if not p.name.startswith("."))


def load_nodes(cfg: Config) -> tuple[list[Node], dict[str, Node]]:
    nodes: list[Node] = []
    seen: set[str] = set()
    for kind, subdir in KIND_DIRS:
        for path in _docs(cfg.vault / "01_Core_Memory" / subdir):
            try:
                doc = vault.read_doc(path)
            except Exception:  # noqa: BLE001 - 坏档不该让整轮同步失败
                continue
            nid = str(doc.meta.get("id") or "")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            aliases = doc.meta.get("aliases")
            if isinstance(aliases, list):
                alias_list = [str(a).strip() for a in aliases if str(a).strip()]
            elif isinstance(aliases, str) and aliases.strip():
                alias_list = [aliases.strip()]
            else:
                alias_list = []
            nodes.append(
                Node(
                    id=nid,
                    name=str(doc.meta.get("name") or path.stem),
                    kind=kind,
                    path=path,
                    aliases=alias_list,
                )
            )
    return nodes, {n.id: n for n in nodes}


AUTO_BLOCK = re.compile(r"<!-- AUTO:[A-Z-]+ -->.*?<!-- /AUTO:[A-Z-]+ -->", re.S)

# 声明字段散布在两个段里：结构化属性在「档案」，指向别的实体的链接在「关联」。
# 两段都读；别的段不读，免得把叙述里的偶然同名当成关系。
FIELD_SECTIONS = ("档案", "关联", "关系")


def _strip_auto(body: str) -> str:
    """去掉所有 AUTO 受控块。

    必须去：`AUTO:RELATIONS` 自己就长成 `- **持有物**：[[ITEM-...]]`，
    重跑时若把它当声明再读一遍，关系会一轮一轮自我增殖。
    """
    return AUTO_BLOCK.sub("", body)


def _fields(body: str) -> list[tuple[str, str]]:
    """档案 + 关联段的全部键值对，**保留重复键**（人物页可能两条「所在地」）。"""
    sections = [_strip_auto(vault.extract_section(body, name)) for name in FIELD_SECTIONS]
    section = "\n".join(s for s in sections if s)
    pairs: list[tuple[str, str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("-", "*")):
            continue
        text = stripped[1:].strip()
        if text.startswith("**") and "**" in text[2:]:
            head, _, tail = text[2:].partition("**")
            value = tail.lstrip("：: ").strip()
        elif "：" in text:
            head, _, value = text.partition("：")
            value = value.strip()
        elif ":" in text:
            head, _, value = text.partition(":")
            value = value.strip()
        else:
            continue
        pairs.append((head.strip(), value))
    return pairs


class Resolver:
    """把字段值解析成节点 ID：先认双链，再认整段精确名。"""

    def __init__(self, nodes: list[Node], by_id: dict[str, Node]) -> None:
        self.by_id = by_id
        self.by_stem = {n.stem: n for n in nodes}
        self.by_name: dict[str, list[Node]] = defaultdict(list)
        for node in sorted(nodes, key=lambda n: n.id):
            self.by_name[node.name].append(node)
            for alias in node.aliases:
                self.by_name[alias].append(node)

    def one(self, raw: str, want: str) -> Node | None:
        """字段值 → 单个节点。优先级：双链 > 整值同名 > 分隔片段同名。"""
        candidates: list[Node] = []
        for hit in WIKILINK.finditer(raw):
            target = hit.group(1).strip()
            node = self.by_stem.get(target) or self.by_id.get(target.split("_")[0])
            if node is not None:
                candidates.append(node)
        if not candidates:
            exact = self.by_name.get(raw.strip(), [])
            candidates = list(exact)
        if not candidates:
            for piece in STOP_CHARS.split(raw):
                piece = piece.strip()
                if len(piece) < 2:
                    continue
                for node in self.by_name.get(piece, []):
                    candidates.append(node)
        for node in candidates:
            if node.kind == want:
                return node
        return None

    def many(self, raw: str, want: str) -> list[Node]:
        out: list[Node] = []
        seen: set[str] = set()
        for hit in WIKILINK.finditer(raw):
            target = hit.group(1).strip()
            node = self.by_stem.get(target) or self.by_id.get(target.split("_")[0])
            if node is not None and node.kind == want and node.id not in seen:
                seen.add(node.id)
                out.append(node)
        if out:
            return out
        for piece in STOP_CHARS.split(raw):
            piece = piece.strip()
            if len(piece) < 2:
                continue
            for node in self.by_name.get(piece, []):
                if node.kind == want and node.id not in seen:
                    seen.add(node.id)
                    out.append(node)
        return out


def collect_declarations(nodes: list[Node], by_id: dict[str, Node]) -> list[Edge]:
    resolver = Resolver(nodes, by_id)
    edges: list[Edge] = []
    seen: set[tuple[str, str, str]] = set()
    for node in nodes:
        allowed = ALLOWED.get(node.kind, set())
        if not allowed:
            continue
        body = _read_body(node.path)
        for key, value in _fields(body):
            if key not in allowed or not value:
                continue
            spec = DECLARATIONS.get(key)
            if spec is None:
                continue
            want, label = spec
            # 势力页的「上级势力」与「势力所属」同义，统一成「上级」，
            # 免得同一条边因为字段名不同被算成两条。
            if node.kind == "势力" and want == "势力" and label == "所属势力":
                label = "上级"
            for target in resolver.many(value, want):
                if target.id == node.id:
                    continue
                triple = (node.id, target.id, label)
                if triple in seen:
                    continue
                seen.add(triple)
                edges.append(Edge(node.id, target.id, label, node.kind, target.kind))
    return edges


def _read_body(path: Path) -> str:
    try:
        return vault.read_doc(path).body
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- 投影
def build_index(edges: list[Edge]) -> dict[str, list[tuple[str, str]]]:
    """节点 → [(关系标签, 对端 ID)]。正向 + 反向都收，渲染时按标签分组。"""
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    seen: set[tuple[str, str, str, str]] = set()

    def push(owner: str, label: str, other: str) -> None:
        key = (owner, label, other, "")
        if key in seen:
            return
        seen.add(key)
        out[owner].append((label, other))

    inverse = {
        "所属势力": "成员",
        "上级": "下级",
        "持有": "持有者",
        "关联物品": "归属势力",
        "信物": "作为信物",
        "持有者": "持有",
        "归属势力": "关联物品",
        "成员": "所属势力",
        "下级": "上级",
    }

    for edge in edges:
        push(edge.src, edge.label, edge.dst)
        back = inverse.get(edge.label)
        if back:
            push(edge.dst, back, edge.src)
    return out


def _render(node: Node, relations: list[tuple[str, str]], by_id: dict[str, Node]) -> str:
    lines: list[str] = [BLOCK_START, "", "## 关系（自动）", ""]
    order = ("所属势力", "上级", "下级", "成员", "持有", "持有者", "关联物品", "信物", "作为信物", "归属势力")
    grouped: dict[str, list[Node]] = defaultdict(list)
    for label, other in relations:
        target = by_id.get(other)
        if target is not None:
            grouped[label].append(target)

    written = 0
    for label in order:
        group = grouped.get(label)
        if not group:
            continue
        group = sorted(group, key=lambda n: n.id)
        if label in ("所属势力", "上级", "下级", "持有者", "归属势力", "作为信物") and len(group) == 1:
            lines.append(f"- **{label}**：{group[0].label}")
        else:
            shown = "、".join(n.label for n in group)
            lines.append(f"- **{label}**（{len(group)}）：{shown}")
        written += len(group)
    if not written:
        return ""

    # 只列「手写段里没有」的那几条，作为补链清单——和 map_sync 的做法一致。
    # 手写段 = 剥掉 AUTO 块之后的部分，否则块自己写的链接会被当成手写已收录，
    # 第二轮提示行消失、白写一次盘。
    body = _strip_auto(_read_body(node.path))
    hand = {m.group(1).strip() for m in WIKILINK.finditer(body)}
    missing = sorted(
        {n.id for group in grouped.values() for n in group if n.stem not in hand}
    )
    if missing:
        lines += ["", f"- 手写段尚未收录：{len(missing)} 条（本块已补上）"]

    lines += ["", "> 由实体档案的声明字段算出，随 `rebuild-index` / `publish` 自动刷新。", "", BLOCK_END]
    return "\n".join(lines)


def _replace_block(text: str, block: str) -> str:
    pattern = re.compile(re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END), re.S)
    if pattern.search(text):
        if not block:
            return pattern.sub("", text).rstrip() + "\n"
        return pattern.sub(lambda _: block.rstrip("\n"), text).rstrip("\n") + "\n"
    if not block:
        return text
    return text.rstrip("\n") + "\n\n" + block.rstrip("\n") + "\n"


# ---------------------------------------------------------------- 主流程
def run(cfg: Config, *, write: bool = True) -> dict[str, object]:
    nodes, by_id = load_nodes(cfg)
    edges = collect_declarations(nodes, by_id)
    index = build_index(edges)

    touched = 0
    if write:
        for node in nodes:
            block = _render(node, index.get(node.id, []), by_id)
            text = node.path.read_text(encoding="utf-8")
            new = _replace_block(text, block)
            if new != text:
                vault.write_atomic(node.path, new)
                touched += 1

    kinds = defaultdict(int)
    for edge in edges:
        kinds[edge.label] += 1
    return {
        "relations": len(edges),
        "nodes_with_relations": sum(1 for n in nodes if index.get(n.id)),
        "unlinked_floor": sum(1 for n in nodes if n.kind == "人物" and not index.get(n.id)),
        "by_label": dict(sorted(kinds.items())),
        "files_touched": touched,
    }


# ---------------------------------------------------------------- 供校验器复用
def dangling(cfg: Config) -> list[dict[str, object]]:
    """声明了关系、但对端档案不存在的边。给 integrity 校验用。"""
    nodes, by_id = load_nodes(cfg)
    resolver = Resolver(nodes, by_id)
    problems: list[dict[str, object]] = []
    for node in nodes:
        allowed = ALLOWED.get(node.kind, set())
        if not allowed:
            continue
        body = _read_body(node.path)
        for key, value in _fields(body):
            if key not in allowed or not value:
                continue
            spec = DECLARATIONS.get(key)
            if spec is None:
                continue
            want, _label = spec
            found = resolver.many(value, want)
            if found:
                continue
            # 字段有值却解析不出任何目标：要么写的是人名，要么指向坏档
            if len(value.strip()) >= 2:
                problems.append(
                    {
                        "node": node.id,
                        "field": key,
                        "value": value.strip()[:60],
                        "kind": node.kind,
                        "path": str(node.path.relative_to(cfg.vault)),
                    }
                )
    return problems
