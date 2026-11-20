#!/usr/bin/env python3
"""把州域志 + 行政区划表落成三层地理库：州 › 府 › 郡（郡治即城邑）。

两条前情：

1. 建库时 `05_Dashboard/Maps/` 只有 `.gitkeep`，州域志里「关联：地图库」那一行
   从没被解析成层级，地图一直是空的；上一版脚本只做到「州 → 城」两层。
2. 资产库「地图库」表本来就有「府属 / 郡属」两列，但「府」从来没成为实体，
   只被压成城档案里的一个字段。三层一压扁，写作时就分不清「一府辖几县」
   「府城与州城是否同城」，也查不到某个郡在哪。

本脚本做五件事，全部幂等：

1. 州域档案（LOC-0073~0083）：改成「府 → 郡 → 治所」三层表；
2. 府级档案（新建）：一府一页，上下双链；
3. 城邑档案（LOC-0001~0071）：补「层级」行，州属／府属改为双链；
4. `Maps/<地图页>.md`：按州 → 府 → 郡三级铺开；
5. `Maps/<地图页>.canvas`（方位图）+ `Maps/<层级树>.canvas`（层级树）。

数据源：州的地貌/口语/简介取 WORLD-0029，府郡归属取 WORLD-0040。
两页都是生成的，不要手改；改源头再重跑。

用法：

    python3 00_System/scripts/build_jiuzhou_map.py              # 全量
    python3 00_System/scripts/build_jiuzhou_map.py --maps-only  # 只重画三张图，不碰档案

**重跑前先读这段（2026-11-20 实测）：**

- `source_refs` / `created_at` / `import_batch` 这三个键由 `write_doc` 从既有档案里原样搬过去，
  不会被覆盖。这个脚本写于 11-05，比 11-19 的 provenance 改造更老，早先重跑会把 968 份档案的
  判别式来源降级成一条裸路径、`created_at` 重置成当天——已修，但**别的键仍会被覆盖**：
  一份档案只要不是本脚本认得的字段，重跑一次就没了。
- 正文里的 `<!-- AUTO:... -->` 受控块不归本脚本生成（`link_entities_to_map.py` 写实体索引、
  涉及章节）。**曾经**重跑一次就整块蒸发：2026-11-20 实测全量重跑改 39 份档案、净删 530 行，
  37 份州域／府档案的实体索引全没了。现已由 `carry_auto_blocks()` 按块名搬回去，
  再跑实测：39 份档案只有 `updated_at` 变，正文一字未动。
- `.canvas` 两张会整份重写。**但坐标不是随机的**：节点位置由本文件的常数
  （`radius=2200`、`700`／`380` 两级半径）算出来，常数不变则坐标一个不动
  （2026-11-20 实测：重画 217 个节点，0 个位移）。所以「怕打乱手摆的布局」这个顾虑
  不成立——这两张图从来就是算出来的，没人摆过。真正会变的是改了常数、
  或 WORLD-0040 增删了府郡。
- 只想修图不想碰档案时用 `--maps-only`，它跳过州域／府／城邑三种档案的写入。
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

# 路径与版式由 configure() 注入，唯一来源是 `maps.MapPaths`。
#
# 旧版这里是 `Path(__file__).resolve().parents[2]` 加一串 `private/00_Canon/...`：
# 脚本住在库内时成立，搬到公仓的包里以后，parents[2] 指向的是公仓，不是 Vault。
# 硬编码路径的生成器最危险的失败方式不是报错，是安静地读空表、写出空地图。
VAULT: Path
WORLD_TABLE: Path
ADMIN_TABLE: Path
LOC_DIR: Path
MAP_DIR: Path
LAYOUT_FILE: Path
TODAY = date.today().isoformat()

#: 预演开关。默认 True：跑一遍先报「会改几份」，加 --confirm 才落盘。
DRY_RUN = True
#: 本次会被改动的文件（内容真的有差异才算）。
PLANNED: list[Path] = []


def _write(path: Path, data: str) -> None:
    """唯一写盘点：内容没变就不算改动，预演时不落盘。"""
    if path.exists() and path.read_text(encoding="utf-8") == data:
        return
    PLANNED.append(path)
    if DRY_RUN:
        return
    path.write_text(data, encoding="utf-8")


LAYOUT: dict = {}
REGIONS: dict[str, tuple[str, float, float]] = {}
ORDER: list[str] = []
OFF_MAINLAND: set[str] = set()
NO_FU: str = "（未分府）"
#: 方位图以哪个大区为中心。原先写死成某个州名，等于把作品设定搬进公仓。
CENTER_REGION: str = ""
#: 府页上的补记（重名说明、待定事项），按府名取。同样属于作品内容，不进代码。
FU_NOTES: dict[str, str] = {}

# 命名：世界名、产物文件名、双链显示名、缺记时的提示语。
# 全部来自版式配置的 `naming` 段——公仓按 GPL-3.0 公开，代码里不留作品名词。
WORLD_NAME: str = ""
UPPER_INSIDE: str = ""
UPPER_OUTSIDE: str = ""
MAP_PAGE: str = ""
TREE_PAGE: str = ""
WORLD_TABLE_LABEL: str = ""
ADMIN_TABLE_LABEL: str = ""
WORLD_ALIAS: str = ""
ADMIN_ALIAS: str = ""
XLSX_NAME: str = ""
MISSING_NOTE: str = ""
WORLD_SHEET: str = ""
WORLD_REL: str = ""
ADMIN_REL: str = ""


def load_layout() -> dict:
    """读「版式」配置：大区顺序、方位、Canvas 偏移、是否在大陆上。

    这些都是**排版**，不是设定；写死在本文件里时，加一个州要改代码、
    改方位要改代码，而且和 WORLD-0029/0040 的顺序很容易对不上。
    """
    if not LAYOUT_FILE.exists():
        raise SystemExit(f"✗ 缺少版式配置：{LAYOUT_FILE.relative_to(VAULT)}")
    return yaml.safe_load(LAYOUT_FILE.read_text(encoding="utf-8")) or {}


def configure(paths: "MapPaths") -> None:
    """绑定一次运行所需的全部路径与版式；由 CLI 在调用 main() 前调用。"""
    global VAULT, WORLD_TABLE, ADMIN_TABLE, LOC_DIR, MAP_DIR, LAYOUT_FILE
    global LAYOUT, REGIONS, ORDER, OFF_MAINLAND, NO_FU, _LOC_INDEX
    global CENTER_REGION, FU_NOTES
    global WORLD_NAME, UPPER_INSIDE, UPPER_OUTSIDE, MAP_PAGE, TREE_PAGE
    global WORLD_TABLE_LABEL, ADMIN_TABLE_LABEL, WORLD_ALIAS, ADMIN_ALIAS
    global XLSX_NAME, MISSING_NOTE, WORLD_REL, ADMIN_REL, WORLD_SHEET
    global ADMIN_ALIASES, OFFICE_TABLE, OFFICE_NOTE
    global STATE_CITIES, PAGE_SPECIAL_NOTE, PAGE_SAME_CITY, PAGE_LINKS

    VAULT = paths.vault
    WORLD_TABLE = paths.world_table
    ADMIN_TABLE = paths.admin_table
    LOC_DIR = paths.loc_dir
    MAP_DIR = paths.map_dir
    LAYOUT_FILE = paths.layout_file
    _LOC_INDEX = None  # 换了 Vault 就必须重建档案索引，否则沿用上一轮的路径
    PLANNED.clear()  # 同上：上一次运行记下的待写文件不能带进这一次

    LAYOUT = load_layout()
    region_cfg: dict[str, dict] = LAYOUT.get("regions") or {}
    # 大区名 → (方位, dx, dy)；dx/dy 用于 Canvas 方位图
    REGIONS = {
        name: (cfg.get("direction", ""), float(cfg.get("dx", 0.0)), float(cfg.get("dy", 0.0)))
        for name, cfg in region_cfg.items()
    }
    ORDER = list(LAYOUT.get("order") or REGIONS.keys())
    OFF_MAINLAND = {name for name, cfg in region_cfg.items() if not cfg.get("mainland", True)}
    NO_FU = LAYOUT.get("no_fu", "（未分府）")
    CENTER_REGION = str(LAYOUT.get("center_region") or "")
    FU_NOTES = dict(LAYOUT.get("fu_notes") or {})

    naming = LAYOUT.get("naming") or {}
    WORLD_NAME = str(naming.get("world") or "")
    UPPER_INSIDE = str(naming.get("upper_inside") or WORLD_NAME)
    UPPER_OUTSIDE = str(naming.get("upper_outside") or "")
    MAP_PAGE = str(naming.get("map_page") or "地图")
    TREE_PAGE = str(naming.get("tree_page") or "层级")
    WORLD_TABLE_LABEL = str(naming.get("world_table") or paths.world_table.stem)
    ADMIN_TABLE_LABEL = str(naming.get("admin_table") or paths.admin_table.stem)
    WORLD_ALIAS = str(naming.get("world_alias") or "")
    ADMIN_ALIAS = str(naming.get("admin_alias") or "")
    XLSX_NAME = str(naming.get("xlsx") or paths.xlsx.name)
    MISSING_NOTE = str(naming.get("missing_note") or "未记")
    WORLD_SHEET = str(naming.get("world_sheet") or WORLD_TABLE_LABEL)
    ADMIN_ALIASES = [str(x) for x in (naming.get("admin_aliases") or [])]
    OFFICE_TABLE = str(naming.get("office_table") or "")
    OFFICE_NOTE = str(naming.get("office_note") or "")
    STATE_CITIES = {
        str(k): str(v) for k, v in (LAYOUT.get("state_cities") or {}).items()
    }
    page_notes = LAYOUT.get("page_notes") or {}
    PAGE_SPECIAL_NOTE = str(page_notes.get("special_note") or "")
    PAGE_SAME_CITY = str(page_notes.get("same_city_example") or "")
    PAGE_LINKS = [str(x) for x in (page_notes.get("links") or [])]
    WORLD_REL = paths.world_table.relative_to(paths.vault).as_posix()
    ADMIN_REL = paths.admin_table.relative_to(paths.vault).as_posix()


@dataclass
class Jun:
    name: str
    seat: str
    intro: str = ""
    note: str = ""
    assoc: str = ""
    kind: str = ""         # 聚落形态（城／乡／洞天／奇地…）；空＝未标注
    loc_id: str = ""       # 治所聚落的档案编号
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
        """郡数按**郡名**计，不按行数——一个郡可以辖多处聚落。"""
        return len({j.name for f in self.fu for j in f.jun if j.name} | {j.name for j in self.direct if j.name})

    @property
    def place_count(self) -> int:
        """聚落数＝行政序列里的地方数；界外洞天奇地不计。

        判据是「这一区是不是界外大区」，不是「条目形态等不等于『洞天』」——
        按形态名判会随作者逐条认定而失效：界外条目一改形态名，
        这条就会把它算进聚落，合计数当场多一。
        """
        if self.is_special:
            return 0
        return sum(len(f.jun) for f in self.fu) + len(self.direct)

    @property
    def is_special(self) -> bool:
        """界外大区：不分府、也没有郡名，条目全是洞天／奇地。"""
        return not self.fu and bool(self.direct) and all(j.kind for j in self.direct)


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
    """WORLD-0040：州 › 府 › 郡 › 聚落。界外那张表第一列是形态，不是郡。"""
    text = ADMIN_TABLE.read_text(encoding="utf-8")
    body = text.split("---", 2)[-1]
    out: dict[str, Region] = {}
    state: Region | None = None
    fu: Fu | None = None
    special = False
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            state = out.setdefault(line[3:].strip(), Region(name=line[3:].strip()))
            fu = None
            special = False
            continue
        if line.startswith("### "):
            if state is None:
                continue
            name = line[4:].strip()
            fu = Fu(name="" if name == NO_FU else name, state=state.name)
            state.fu.append(fu)
            special = False
            continue
        if not line.startswith("|") or state is None or fu is None:
            continue
        cells = [c.strip().replace("\\|", "|") for c in line.strip("|").split("|")]
        if cells[0] == "形态":
            # 界外洞天奇地表头：这一张不出郡，只出「形态 + 名称」
            special = True
            continue
        if len(cells) < 5 or cells[0] in ("郡", "---"):
            continue
        if special:
            fu.jun.append(
                Jun(name="", seat=cells[1], intro=cells[2], note=cells[3],
                    assoc=cells[4], kind=cells[0])
            )
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

    两类冲突：
    1. 无府名的行（界外条目的府属是跨表复制残留）→ 直接降为州直属；
    2. 府名跨州重名 → 首个出现的州占有该府，后来的降为州直属，
       并在该郡的备注里写清「旧表记作某府、与何处重名」，等作者裁定。
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


_LOC_INDEX: dict[str, str] | None = None


def loc_rel(loc_id: str) -> str:
    """地点编号 → 档案相对库根的路径（`LOC-0045` → `…/LOC-0045_<地名>.md`）。

    为什么不能直接拼 `{loc_id}.md`：档案名带后缀，形如 `LOC-0045_<地名>.md`。
    旧版对着不存在的 `LOC-0045.md` 拼路径，一张画布里 69 个城邑卡片全是死链，
    而且不报错——卡片在 Obsidian 里显示成空白，看着像「地图是空的」。
    """
    global _LOC_INDEX
    if _LOC_INDEX is None:
        _LOC_INDEX = {
            p.stem.split("_", 1)[0]: p.relative_to(VAULT).as_posix()
            for p in LOC_DIR.glob("LOC-*.md")
        }
    return _LOC_INDEX.get(loc_id, "")


def link(loc_id: str, name: str, *, in_table: bool = False) -> str:
    """生成实体双链；`in_table=True` 时把别名分隔符转义成 `\\|`。

    为什么必须分两种：markdown 表格把裸 `|` 当列分隔符，`[[LOC-0001_某地|某地]]`
    写进单元格会被劈成两格，双链当场失效。2026-11-20 就是这么坏掉的——
    地图页的表格被 Obsidian 的表格编辑器整体重排，每行多出一列。
    正文段落里则要用裸 `|`，转义了反而不认。
    """
    if not loc_id:
        return name
    sep = r"\|" if in_table else "|"
    return f"[[{loc_id}_{name}{sep}{name}]]"


#: 写盘时从既有档案里原样搬过去的 frontmatter 键。
#:
#: 这个脚本写于 2026-11-05，比 11-19 的 provenance 改造更老：它生成的 frontmatter
#: 只有 name/subtype/aliases 这些，`source_refs` 是一条裸路径。直接覆盖会把
#: 968 份档案的判别式来源（kind/import_id/file_sha256/locator/quote）降级掉，
#: `created_at` 也会被重置成今天——2026-11-20 重跑时实测发生，随即回滚。
#: 这三个键记的是既有档案的历史，生成器没有资格改。
PRESERVE_KEYS = ("created_at", "source_refs", "import_batch")


def split_frontmatter(text: str) -> tuple[list[list[str]], str] | None:
    """切成（frontmatter 的「键 + 其后附属行」块列表，正文）。无 frontmatter 返回 None。"""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return None
    blocks: list[list[str]] = []
    cur: list[str] = []
    for line in lines[1:end]:
        if re.match(r"^[A-Za-z_][\w-]*:", line):
            if cur:
                blocks.append(cur)
            cur = [line]
        elif cur:
            cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks, "\n".join(lines[end + 1 :])


_AUTO_BLOCK = re.compile(r"<!-- AUTO:([\w-]+) -->.*?<!-- /AUTO:\1 -->", re.S)


def carry_auto_blocks(old_body: str, new_body: str) -> str:
    """把旧正文里的 `<!-- AUTO:NAME -->…<!-- /AUTO:NAME -->` 整块搬到新正文。

    这些块不归本脚本生成（实体索引、涉及章节由 `link_entities_to_map.py` 写），
    但 `render_region` / `render_fu` 是整段替换正文的，重跑一次就连块带内容
    一起蒸发。2026-11-20 实测：全量重跑改 39 份档案、净删 530 行，
    37 份州域／府档案的「实体索引」全部消失，且脚本一声不响。
    """
    blocks = {m.group(1): m.group(0) for m in _AUTO_BLOCK.finditer(old_body)}
    if not blocks:
        return new_body
    seen: set[str] = set()

    def sub(m: re.Match[str]) -> str:
        seen.add(m.group(1))
        return blocks.get(m.group(1), m.group(0))

    out = _AUTO_BLOCK.sub(sub, new_body)
    leftovers = [b for k, b in blocks.items() if k not in seen]
    if leftovers:
        out = out.rstrip() + "\n\n" + "\n\n".join(leftovers) + "\n"
    return out


_STAMP = re.compile(r"^updated_at:.*$", re.M)


def write_doc(path: Path, text: str) -> None:
    """写生成结果，但保留既有档案的 PRESERVE_KEYS 与受控块。

    内容没变就不写：`updated_at` 每次都会算成今天，若不做这一步，重跑一次
    就有 39 份档案平白无故进 diff，真改动被埋在里面（2026-11-20 实测）。
    比对时把 `updated_at` 那一行抹掉再比——它本来就不该算「内容」。
    """
    old_text = path.read_text(encoding="utf-8") if path.exists() else ""
    if old_text:
        old = split_frontmatter(old_text)
        new = split_frontmatter(text)
        if old and new:
            keep = {}
            for block in old[0]:
                key = block[0].split(":", 1)[0].strip()
                if key in PRESERVE_KEYS:
                    keep[key] = block
            body = carry_auto_blocks(old[1], new[1])
            merged = [keep.pop(block[0].split(":", 1)[0].strip(), block) for block in new[0]]
            merged.extend(keep.values())
            fm = "\n".join("\n".join(b) for b in merged)
            # body 以单个 \n 开头（split_frontmatter 吃掉了 `---` 后那一空行），
            # 拼回时补一个 \n，否则 frontmatter 与标题之间那行空行会消失。
            text = f"---\n{fm}\n---\n{body}"
        elif old:
            text = carry_auto_blocks(old[1], text)

    if old_text and _STAMP.sub("", text) == _STAMP.sub("", old_text):
        return
    _write(path, text)


# ------------------------------------------------------------------ 生成：州
def render_region(region: Region) -> str:
    upper = UPPER_INSIDE if region.name not in OFF_MAINLAND else UPPER_OUTSIDE
    rows = []
    for fu in region.fu:
        first = True
        for jun in fu.jun:
            fu_cell = link(fu.loc_id, fu.name, in_table=True) if first else ""
            seat = link(jun.loc_id, jun.seat, in_table=True) if jun.loc_id else f"{jun.seat}（待建档）"
            way = jun.kind if (jun.kind and not jun.name) else (jun.name or "—")
            rows.append(f"| {fu_cell} | {way} | {seat} |")
            first = False
        if not fu.jun:
            rows.append(f"| {link(fu.loc_id, fu.name, in_table=True)} | — | — |")
    for jun in region.direct:
        seat = link(jun.loc_id, jun.seat, in_table=True) if jun.loc_id else f"{jun.seat}（待建档）"
        way = jun.kind if (jun.kind and not jun.name) else (jun.name or "—")
        rows.append(f"| （直属·不分府） | {way} | {seat} |")
    table = "\n".join(rows) if rows else "| — | — | — |"

    fu_list = [
        f"- {link(f.loc_id, f.name)}（{len({j.name for j in f.jun if j.name})} 郡 / "
        f"{len(f.jun)} 处聚落：{'、'.join(j.name or j.seat for j in f.jun) or '待补'}）"
        for f in region.fu
    ]
    if region.direct:
        fu_list.append(
            "- **州直属（不分府）**：" + "、".join(
                (link(j.loc_id, j.seat) if j.loc_id else j.seat)
                + (f"（{j.kind}）" if (j.kind and not j.name) else (f"（{j.name}）" if j.name else ""))
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
world: {WORLD_NAME}
canon_status: confirmed
created_at: '{TODAY}'
updated_at: '{TODAY}'
source_refs:
- {WORLD_REL}
- {ADMIN_REL}
import_batch: MAP-20261105
generated_by: novel maps build
tags:
- {WORLD_NAME}
- 地图库
- 州域
- 方位{region.direction}
---

# {region.name}

> 来源：`{XLSX_NAME}` · 工作表「{WORLD_SHEET}」「地图库」｜由 `novel maps build` 生成，**请勿手改**。
> 改设定请改 [[{WORLD_TABLE_LABEL}|{WORLD_ALIAS}]] 与 [[{ADMIN_TABLE_LABEL}|{ADMIN_ALIAS}]]，再重跑命令。

## 档案

- **层级**：{upper} › {region.name}
- **方位**：{region.direction}
- **主要地貌**：{region.terrain or '（{MISSING_NOTE}）'}
- **特色口语**：{region.slang or '（{MISSING_NOTE}）'}
- **下辖**：{f'{len(region.direct)} 处洞天奇地（界外，不入行政序列）' if region.is_special else f'{len(region.fu)} 府 / {region.jun_count} 郡 / {region.place_count} 处聚落'}

## 简介

{region.intro or '（{MISSING_NOTE}）'}

## 下辖府郡

| 府 | 郡 | 聚落 |
| --- | --- | --- |
{table}

## 下辖府

{fu_block}

## 关联

- **上层**：[[{WORLD_TABLE_LABEL}|{WORLD_ALIAS}]]、[[{ADMIN_TABLE_LABEL}|{ADMIN_ALIAS}]]
- **地图**：[[{MAP_PAGE}|{MAP_PAGE}]]、[[{TREE_PAGE}.canvas|{TREE_PAGE}]]
"""


# ------------------------------------------------------------------ 生成：府
def render_fu(fu: Fu, region: Region) -> str:
    rows = []
    for jun in fu.jun:
        seat = link(jun.loc_id, jun.seat, in_table=True) if jun.loc_id else f"{jun.seat}（待建档）"
        intro = jun.intro or "—"
        note = jun.note or "—"
        way = jun.kind if (jun.kind and not jun.name) else (jun.name or "—")
        rows.append(
            f"| {way} | {seat} | {intro[:120]}{'…' if len(intro) > 120 else ''} "
            f"| {note[:80]}{'…' if len(note) > 80 else ''} |"
        )
    table = "\n".join(rows) if rows else "| — | — | — | — |"
    seats = "、".join(j.seat for j in fu.jun) or "待补"
    jun_n = len({j.name for j in fu.jun if j.name})

    notes: list[str] = []
    # 补记按府名从版式配置取（`fu_notes`）。这些文字记的是这一页的历史与重名纠纷，
    # 属作品内容；写死在代码里就等于把设定搬进公仓。
    if note := FU_NOTES.get(fu.name):
        notes.append(note.strip())
    note_block = ("\n".join(notes) + "\n\n") if notes else ""

    return f"""---
id: {fu.loc_id}
type: location
name: {fu.name}
title: {fu.name}
aliases:
- {region.name}·{fu.name}
subtype: 府
world: {WORLD_NAME}
canon_status: confirmed
created_at: '{TODAY}'
updated_at: '{TODAY}'
source_refs:
- {ADMIN_REL}
import_batch: MAP-20261105
generated_by: novel maps build
tags:
- {WORLD_NAME}
- 地图库
- 府级
- {region.name}
---

# {fu.name}

> 来源：`{XLSX_NAME}` · 工作表「地图库」｜由 `novel maps build` 生成，**请勿手改**。
> 改设定请改 [[{ADMIN_TABLE_LABEL}|{ADMIN_ALIAS}]]，再重跑命令。

## 档案

- **层级**：{WORLD_NAME} › {link(region.loc_id, region.name)} › {fu.name}
- **州属**：{link(region.loc_id, region.name)}（{region.direction}）
- **下辖**：{jun_n} 郡 / {len(fu.jun)} 处聚落（{seats}）
- **府治**：{fu_seat_note(fu, region)}

{note_block}## 下辖郡县

| 郡 | 聚落 | 简介 | 备注 |
| --- | --- | --- | --- |
{table}

## 关联

- **上层**：{link(region.loc_id, region.name)}（州）
- **同级府**：{'、'.join(link(f.loc_id, f.name) for f in region.fu if f.name != fu.name) or '—'}
- **行政区划总表**：[[{ADMIN_TABLE_LABEL}|{ADMIN_ALIAS}]]
- **地图**：[[{MAP_PAGE}|{MAP_PAGE}]]、[[{TREE_PAGE}.canvas|{TREE_PAGE}]]
"""


def fu_seat_note(fu: Fu, region: Region) -> str:
    """府治：州城所在府则与州城同城，其余原表未记。"""
    # 州城与府城同城的地点由版式配置的 state_cities 给出，没有列举的不猜
    state_city = STATE_CITIES.get(region.name)
    seats = [j.seat for j in fu.jun]
    if state_city and state_city in seats:
        return f"与州城同城（{state_city}）"
    return "（原表未记，待作者定）"


# ------------------------------------------------------------------ 生成：城
def add_alias(text: str, alias: str) -> str:
    """把郡名写进 aliases，让「查某个郡」能落到郡治那一页。已有则不动。"""
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

    chain = [UPPER_INSIDE if region.name not in OFF_MAINLAND else UPPER_OUTSIDE, link(region.loc_id, region.name)]
    if fu is not None:
        chain.append(link(fu.loc_id, fu.name))
    if jun.name:
        chain.append(jun.name)

    new_lines = [f"- **层级**：{' › '.join(chain)}", f"- **州属**：{link(region.loc_id, region.name)}"]
    if fu is not None:
        new_lines.append(f"- **府属**：{link(fu.loc_id, fu.name)}")
    if jun.name:
        # 不写「郡治即本城」：一个郡可以辖好几处聚落，只有其中一处是郡治，原表没说哪一处。
        new_lines.append(f"- **郡属**：{jun.name}")
    elif jun.kind:
        new_lines.append(f"- **形态**：{jun.kind}（界外，不入府郡）")

    text = re.sub(r"^- \*\*(层级|州属|府属|郡属|形态)\*\*：.*\n?", "", text, flags=re.M)
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
        if r.is_special:
            rows.append(
                f"| {link(r.loc_id, r.name, in_table=True)} | {r.direction} | {fu_names} "
                f"| 0 | — | — |"
            )
            continue
        rows.append(
            f"| {link(r.loc_id, r.name, in_table=True)} | {r.direction} | {fu_names} "
            f"| {len(r.fu)} | {r.jun_count} | {r.place_count} |"
        )
    table = "\n".join(rows)

    sections = []
    for r in regions:
        blocks = []
        for fu in r.fu:
            jun_lines = []
            for jun in fu.jun:
                seat = link(jun.loc_id, jun.seat) if jun.loc_id else f"{jun.seat}（待建档）"
                if jun.kind and not jun.name:
                    # 洞天／奇地：条目本身就是名字，前面不挂郡
                    jun_lines.append(f"  - {seat}（{jun.kind}）")
                else:
                    jun_lines.append(f"  - {jun.name or '—'} → {seat}")
            blocks.append(f"- {link(fu.loc_id, fu.name)}\n" + ("\n".join(jun_lines) if jun_lines else "  - （郡待补）"))
        for jun in r.direct:
            seat = link(jun.loc_id, jun.seat) if jun.loc_id else f"{jun.seat}（待建档）"
            if jun.kind and not jun.name:
                blocks.append(f"- {seat}（{jun.kind}·界外，不入郡）")
            else:
                blocks.append(f"- （直属·不分府）{jun.name or '—'} → {seat}")
        block = "\n".join(blocks) if blocks else "- （不分府：海外／界外自理）"
        counts = (
            f"- **界外**：{len(r.direct)} 处洞天奇地（不入行政序列）"
            if r.is_special
            else f"- **府郡**：{len(r.fu)} 府 / {r.jun_count} 郡 / {r.place_count} 处聚落"
        )
        sections.append(
            f"""### {r.name}（{r.direction}）

- **地貌**：{r.terrain or '—'}　**口语**：{r.slang or '—'}
{counts}

{block}
"""
        )

    total_fu = sum(len(r.fu) for r in regions)
    total_jun = sum(r.jun_count for r in regions)
    total_place = sum(r.place_count for r in regions)
    missing = [(r.name, f.name, j.seat) for r in regions for f in r.fu for j in f.jun if not j.loc_id]

    # 「格局」一段从数据算，不写死州名。写死的坏处 2026-11-20 见过了：模板里留着
    # 四个查无此名的「方位＋州名」叫法，而表里根本没有这些州，读者会以为有另一套别名。
    ring = [r for r in regions if r.name not in OFF_MAINLAND and r.name != CENTER_REGION]
    ring_txt = "、".join(f"{r.name}（{r.direction}）" for r in ring) or "—"
    off = [r.name for r in regions if r.name in OFF_MAINLAND]
    off_txt = "、".join(f"「{n}」" for n in off) if off else "—"

    # 重名郡只在**跨府**时才算冲突。同一个府下同名多行是正常的：一个郡可以辖多处
    # 聚落。此前按行数判重，把这类正常情况误报成「重名待裁」。
    owner: dict[str, set[str]] = {}
    for r in regions:
        for fu in r.fu:
            for j in fu.jun:
                if j.name:
                    owner.setdefault(j.name, set()).add(f"{r.name}·{fu.name or NO_FU}")
        for j in r.direct:
            if j.name:
                owner.setdefault(j.name, set()).add(f"{r.name}·（直属）")
    dup_jun = [f"{k}（{'、'.join(sorted(v))}）" for k, v in owner.items() if len(v) > 1]

    multi: list[str] = []
    for r in regions:
        cnt: dict[str, list[str]] = {}
        for fu in r.fu:
            for j in fu.jun:
                if j.name:
                    cnt.setdefault(j.name, []).append(j.seat)
        for j in r.direct:
            if j.name:
                cnt.setdefault(j.name, []).append(j.seat)
        multi += [f"{k}辖{'、'.join(v)}" for k, v in cnt.items() if len(v) > 1]

    info_block = ""
    if multi:
        info_block += f"\n> **一郡多聚落**：{'；'.join(multi)}——郡与聚落不是一对一，属正常结构。\n"
    if dup_jun:
        info_block += (
            f"\n> **跨府重名郡（待裁）**：{'、'.join(dup_jun)}。同名郡挂在两个府下，"
            "必有一处是原表笔误；本页照录，不猜哪一处对。\n"
        )

    miss_block = ""
    if missing:
        miss_block = "\n> 待建档治所：" + "、".join(f"{s}（{st}·{fu}）" for st, fu, s in missing) + "\n"

    _special_note = PAGE_SPECIAL_NOTE
    _same_city = PAGE_SAME_CITY
    _links = "\n".join(PAGE_LINKS)

    return f"""---
type: map
name: {MAP_PAGE}
title: {MAP_PAGE}
generated_by: novel maps build
canon_status: confirmed
created_at: '{TODAY}'
updated_at: '{TODAY}'
tags:
- {WORLD_NAME}
- 地图库
- 总图
---

# {MAP_PAGE}

> 本页由 `00_System/scripts/build_jiuzhou_map.py` 从 [[{WORLD_TABLE_LABEL}|{WORLD_ALIAS}]] 与
> [[{ADMIN_TABLE_LABEL}|{ADMIN_ALIAS}]] 生成，**请勿手改**。改设定改两张表，再重跑脚本。
> 方位图见 [[{MAP_PAGE}.canvas|{MAP_PAGE}.canvas]]，层级树见 [[{TREE_PAGE}.canvas|{TREE_PAGE}.canvas]]。

## 格局

{UPPER_INSIDE}大陆居中，环列 {len(ring)} 州：{ring_txt}。
{UPPER_OUTSIDE}另有 {len(off)} 处：{off_txt}。

| 州 | 方位 | 所辖府 | 府数 | 郡数 | 聚落 |
| --- | --- | --- | --- | --- | --- |
{table}

合计 **{len(regions)}** 个大区、**{total_fu}** 个府、**{total_jun}** 个郡、**{total_place}** 处聚落。
层级：**{UPPER_INSIDE} › 州 › 府 › 郡 › 聚落**。郡管户籍与地理，**与聚落不是一对一**——一郡可辖多处聚落，
形态也不止城（还有乡、洞天、奇地）。{_special_note}
{info_block}{miss_block}
## 分州

{chr(10).join(sections)}

## 用法

- 写场景先查本页「地貌 + 口语」，再进具体的聚落档案取细节。
- 行政口径：**州 → 府 → 郡 → 聚落**。府设府尹、管刑名钱粮；郡是户籍与地理单位，
  一郡可辖多处聚落，别把「一郡」当成「一城」。
{_same_city}
- 方位看 [[{MAP_PAGE}.canvas|方位图]]；层级看 [[{TREE_PAGE}.canvas|行政层级图]]。
- 新州、新府、新城先补进 [[{ADMIN_TABLE_LABEL}|行政区划表]] → 重跑脚本 → 档案与地图一起更新。

## 关联

- **数据源**：[[{WORLD_TABLE_LABEL}|{WORLD_ALIAS}]]、[[{ADMIN_TABLE_LABEL}|{ADMIN_ALIAS}]]
{_links}
"""


# ------------------------------------------------------------------ Canvas
_CN_DIGITS = "〇一二三四五六七八九"


def _cn_count(n: int) -> str:
    """1–10 用中文数字：图上的字样是「八州环列」，不是「8 州环列」。"""
    if 1 <= n <= 9:
        return _CN_DIGITS[n]
    if n == 10:
        return "十"
    if 11 <= n <= 19:
        return "十" + _CN_DIGITS[n % 10]
    return str(n)


def build_canvas(regions: list[Region]) -> dict:
    """方位图：州居中环列，府贴州向外，城贴府。"""
    nodes: list[dict] = []
    edges: list[dict] = []

    def node(nid: str, rel: str, x: float, y: float, w: int, h: int,
             color: str = "", label: str = "") -> None:
        # 没有档案就退成纯文字卡片，绝不写一条指向空路径的 file——那种卡片
        # 在 Obsidian 里是一片空白，比没画更误导人。
        if not rel:
            text_node(nid, label or "（缺档案）", x, y, w, h, color or "2")
            return
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

    radius = 2200
    inner = [r for r in regions if r.name not in OFF_MAINLAND and r.name != CENTER_REGION]
    # 环列的州数按数据实际条数算，不写死
    text_node(
        "center",
        f"# {UPPER_INSIDE}\n大陆居中，{_cn_count(len(inner))}州环列",
        -150, -60, 300, 120, "1",
    )
    for r in inner:
        _, dx, dy = REGIONS[r.name]
        cx, cy = dx * radius, dy * radius
        rid = f"region-{r.slug}"
        node(rid, loc_rel(r.loc_id), cx - 130, cy - 35, 260, 70, "4", r.name)
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
            node(fid, loc_rel(fu.loc_id), fx - 105, fy - 28, 210, 56, "5", fu.name)
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
                node(cid, loc_rel(jun.loc_id), jx - 90, jy - 24, 180, 48, "", jun.seat)
                edge(fid, cid)

    mid = next((r for r in regions if r.name == CENTER_REGION), None) if CENTER_REGION else None
    if mid:
        rid = f"region-{CENTER_REGION}"
        node(rid, loc_rel(mid.loc_id), -130, 300, 260, 70, "4", CENTER_REGION)
        edges.append({"id": f"e-center-region-{CENTER_REGION}", "fromNode": "center", "fromSide": "bottom",
                      "toNode": rid, "toSide": "top", "color": "3"})
        for i, fu in enumerate(mid.fu):
            fx, fy = -520 + i * 420, 470
            fid = f"fu-{fu.loc_id}"
            node(fid, loc_rel(fu.loc_id), fx, fy, 210, 56, "5", fu.name)
            edge(rid, fid)
            for j, jun in enumerate(fu.jun):
                if not jun.loc_id:
                    continue
                cid = f"city-{jun.loc_id}"
                node(cid, loc_rel(jun.loc_id),
                     fx + (j % 2) * 200, fy + 100 + (j // 2) * 80, 180, 48, "", jun.seat)
                edge(fid, cid)

    off_y = radius + 900
    for i, r in enumerate([x for x in regions if x.name in OFF_MAINLAND]):
        rid = f"region-{r.slug}"
        bx = -700 + i * 1200
        node(rid, loc_rel(r.loc_id), bx, off_y, 300, 80, "6", r.name)
        edges.append({"id": f"e-center-{rid}", "fromNode": "center", "fromSide": "right",
                      "toNode": rid, "toSide": "left", "color": "6"})
        for k, fu in enumerate(r.fu):
            fid = f"fu-{fu.loc_id}"
            node(fid, loc_rel(fu.loc_id), bx, off_y + 130 + k * 80, 240, 56, "5", fu.name)
            edge(rid, fid)
            for j, jun in enumerate(fu.jun):
                if not jun.loc_id:
                    continue
                cid = f"city-{jun.loc_id}"
                node(cid, loc_rel(jun.loc_id),
                     bx + 320 + j * 200, off_y + 130 + k * 80 + 4, 180, 48, "", jun.seat)
                edge(fid, cid)
    return {"nodes": nodes, "edges": edges}


def build_tree_canvas(regions: list[Region]) -> dict:
    """层级树：左州、中府、右郡（治所）。纯行政树，不表方位。"""
    nodes: list[dict] = []
    edges: list[dict] = []

    def node(nid: str, rel: str, x: float, y: float, w: int, h: int,
             color: str = "", label: str = "") -> None:
        if not rel:
            text_node(nid, label or "（缺档案）", x, y, w, h, color or "2")
            return
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

    text_node("root", f"# {UPPER_INSIDE}\n州 › 府 › 郡", -600, 0, 320, 120, "1")

    y = 0
    for r in regions:
        blocks: list[tuple[object, list[Jun]]] = [(fu, fu.jun) for fu in r.fu]
        if r.direct:
            blocks.append((None, r.direct))
        total = sum(max(len(j), 1) * (H_JUN + GAP_JUN) + GAP_FU for _, j in blocks)
        total = max(total, H_STATE + 60)

        sid = f"state-{r.loc_id}"
        node(sid, loc_rel(r.loc_id), X_STATE, y + total / 2 - H_STATE / 2,
             W_STATE, H_STATE, "4", r.name)
        edge("root", sid, "3")

        fy = y
        for fu, jun in blocks:
            bh = max(len(jun), 1) * (H_JUN + GAP_JUN)
            if fu is None:
                for i, j in enumerate(jun):
                    if not j.loc_id:
                        continue
                    node(f"city-{j.loc_id}", loc_rel(j.loc_id),
                         X_FU + 60, fy + i * (H_JUN + GAP_JUN), W_JUN, H_JUN, "6", j.seat)
                    edge(sid, f"city-{j.loc_id}", "6")
                fy += bh + GAP_FU
                continue
            fid = f"fu-{fu.loc_id}"
            node(fid, loc_rel(fu.loc_id), X_FU, fy + bh / 2 - H_FU / 2, W_FU, H_FU, "5", fu.name)
            edge(sid, fid, "2")
            for i, j in enumerate(jun):
                if not j.loc_id:
                    continue
                node(f"city-{j.loc_id}", loc_rel(j.loc_id),
                     X_JUN, fy + i * (H_JUN + GAP_JUN), W_JUN, H_JUN, "", j.seat)
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


def main(argv: list[str] | None = None) -> int:
    """`--maps-only`：只重画地图页与两张画布，不碰 110 份实体档案。

    加这个开关的原因是「重跑」的代价不对称：画布坏了要重画，但重画不该顺带
    重写州域／府／城邑三种档案——那些档案里有正文受控块、作者手改、
    以及生成器根本不认识的键。见文件头「重跑前先读这段」。
    """
    args = sys.argv[1:] if argv is None else argv
    maps_only = "--maps-only" in args
    global DRY_RUN
    DRY_RUN = "--confirm" not in args

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

    written: list[str] = []
    fu_written: list[str] = []
    touched = 0

    if not maps_only:
        normalize_aliases()

        for r in regions:
            if r.loc_id:
                write_doc(LOC_DIR / f"{r.loc_id}_{r.name}.md", render_region(r))
                written.append(f"{r.loc_id} {r.name}")

        for r in regions:
            for fu in r.fu:
                if not fu.loc_id:
                    continue
                write_doc(LOC_DIR / f"{fu.loc_id}_{fu.name}.md", render_fu(fu, r))
                fu_written.append(f"{fu.loc_id} {fu.name}")

    # 郡名唯一才写进别名：重名的郡不写，避免一条名字挂两处
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

    if not maps_only:
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

    write_doc(MAP_DIR / f"{MAP_PAGE}.md", build_md(regions))

    geo = build_canvas(regions)
    _write(MAP_DIR / f"{MAP_PAGE}.canvas", json.dumps(geo, ensure_ascii=False, indent=2) + "\n")
    tree = build_tree_canvas(regions)
    _write(MAP_DIR / f"{TREE_PAGE}.canvas", json.dumps(tree, ensure_ascii=False, indent=2) + "\n")

    missing = [f"{f.name}/{j.seat}" for r in regions for f in r.fu for j in f.jun if not j.loc_id]
    missing += [f"（直属）{j.seat}" for r in regions for j in r.direct if not j.loc_id]
    broken = [n["file"] for c in (geo, tree) for n in c["nodes"]
              if n.get("type") == "file" and not (VAULT / n["file"]).exists()]
    if maps_only:
        print("· --maps-only：未写实体档案（州域／府／城邑原样不动）")
    else:
        print(f"✓ 州域档案 {len(written)}：{written[0]} … {written[-1]}")
        print(f"✓ 府级档案 {len(fu_written)}：{fu_written[0]} … {fu_written[-1]}")
        print(f"✓ 城邑档案补层级 {touched} 个")
    print(f"✓ 地图页 {MAP_DIR / (MAP_PAGE + '.md')}")
    print(f"✓ 方位图 {len(geo['nodes'])} 节点 / {len(geo['edges'])} 连线")
    print(f"✓ 层级树 {len(tree['nodes'])} 节点 / {len(tree['edges'])} 连线")
    if broken:
        print(f"✗ 画布里有 {len(broken)} 个指向不存在档案的节点：{broken[:5]}")
        return 1
    if missing:
        print(f"! 待建档治所 {len(missing)}：{missing}")

    changed = sorted({p.relative_to(VAULT).as_posix() for p in PLANNED})
    if DRY_RUN:
        print(f"· 预演：将改 {len(changed)} 个文件（加 --confirm 执行）")
        for rel in changed[:12]:
            print(f"    {rel}")
        if len(changed) > 12:
            print(f"    …另 {len(changed) - 12} 个")
    else:
        print(f"✓ 写入 {len(changed)} 个文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
