"""规划契约：这一章的章卡够不够开写，以及「必须/禁止」到底落在哪一行。

PRD §10.2 列了章节规划至少该有的字段，§10.3 给了 `creative_scope` 的三段结构
（allowed / approval_required / forbidden）。真库现状是**散文式章卡**：
「## 必须发生的剧情事实」是一张时间表，「## 禁止发生的内容」是一串列表项，
frontmatter 里没有 `must_happen` / `creative_scope`。

这带来一个必须说清的边界：**程序不替作者判定「没写就等于允许」。**

所以本模块只做三件事，一件也不越界：

1. `check()` —— 逐项报「有 / 没有」，缺就是缺。不把「章节目标」当成「入场状态」，
   也不因为某句话看起来像授权就认定它是授权。
2. `derive()` —— 只从**确定性可读**的两处派生：编号表格的第二列（必须发生）、
   「禁止」小节里明确以「不得／禁止／不许／不能」开头的条目。
   allowed 与 approval_required **一律不派生**：散文里没有对应来源，编一个出来
   就等于替作者扩大了授权范围。
3. `apply()` —— 把派生结果写回章卡 frontmatter。只插入**原本不存在的键**，
   已有键一律不动；写回前备份，写回后校验正文逐字节未变。

最后这条是硬约束：`plan derive --write` 不能成为「程序顺手改了作者手稿」的入口。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..infrastructure.filesystem import markdown as md
from ..domain.errors import ConflictError, UsageError
from ..infrastructure.filesystem.paths import ProjectPaths
from ..ids import IdAllocator
from ..infrastructure.filesystem.storage import dump_yaml, read_text, sha256_text, write_text_atomic

# ---------------------------------------------------------------------------
# §10.2 的字段表
# ---------------------------------------------------------------------------
# `meta_keys`：frontmatter 里认这些键。
# `headings`：正文里认这些小节标题（子串匹配，不区分「的」这类虚词）。
#
# 认哪一处就算「有」，出处写进报告——作者能看到系统凭什么判它有。
# 两条都不认的，就是不认，不许拿近义小节顶替。
@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    meta_keys: tuple[str, ...] = ()
    headings: tuple[str, ...] = ()
    required: bool = False
    note: str = ""


PLAN_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "chapter_id",
        "对应章节",
        ("chapter_id",),
        (),
        required=True,
        note="规划自己的号是 plan_XXXXXX；对应哪一章写在 chapter_id，两者不是同一个号",
    ),
    FieldSpec("pov", "POV", ("pov",), ("POV", "视角"), required=True),
    FieldSpec("story_anchor", "故事时间锚点", ("timeline", "story_anchor"), ("时间与地点", "时间锚点"), required=True),
    FieldSpec("entities", "参与实体", ("entities", "cast"), ("角色出场", "出场"), required=True),
    FieldSpec("locations", "场景地点", ("locations",), ("时间与地点", "场景列表", "地点")),
    FieldSpec("entry_state", "入场状态", ("entry_state",), ("入场状态", "上章结束")),
    FieldSpec("core_conflict", "核心冲突", ("core_conflict",), ("核心冲突",), required=True),
    FieldSpec("target_outcome", "目标结果", ("target_outcome",), ("章节目标", "目标")),
    FieldSpec("end_change", "章末变化", ("end_change",), ("章末变化", "章节结尾", "结尾钩子")),
    FieldSpec("must_happen", "必须发生内容", ("must_happen", "required_events"), ("必须发生",), required=True),
    FieldSpec("must_not_happen", "禁止发生内容", ("must_not_happen", "forbidden_events"), ("禁止发生", "不得引入"), required=True),
    FieldSpec("allowed_persistent_changes", "允许的持久变化", ("allowed_persistent_changes",), ("持久变化",)),
    FieldSpec("forbidden_reveals", "禁止提前揭示的信息", ("forbidden_reveals",), ("禁止提前揭示", "提前揭示")),
    FieldSpec("foreshadowing", "关联伏笔", ("foreshadowing", "foreshadow_ids"), ("关联伏笔", "伏笔")),
    FieldSpec("creative_scope", "创作授权（creative_scope）", ("creative_scope",), (), required=True),
    FieldSpec("dependencies", "依赖", ("depends_on", "dependencies"), ()),
)

# 开写前必须齐的那些：其余缺了是「规划偏薄」，这几项缺了是「不知道能写什么」。
REQUIRED_FOR_DRAFT = frozenset(spec.key for spec in PLAN_FIELDS if spec.required)

# 「禁止」类条目必须以这些词开头才算授权禁令。
# 为什么不用关键词包含：一句话里出现「不」不代表它是禁令，
# 「他不知道路」是叙述，不是约束。
_FORBIDDEN_LEAD = ("不得", "禁止", "不许", "不准", "不能", "严禁", "不可")


@dataclass(frozen=True)
class FieldHit:
    """一个字段被认在哪。`where` 是给人看的出处。"""

    key: str
    where: str


@dataclass
class PlanCheck:
    chapter_id: str
    path: Path
    rel: str
    found: list[FieldHit] = field(default_factory=list)
    missing: list[FieldSpec] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    scope: dict = field(default_factory=dict)
    body_sha: str = ""

    @property
    def missing_required(self) -> list[FieldSpec]:
        return [spec for spec in self.missing if spec.key in REQUIRED_FOR_DRAFT]

    @property
    def ready(self) -> bool:
        """够不够开写。只按「必需字段齐了没有」判，不掺别的。"""
        return not self.missing_required

    def as_dict(self) -> dict:
        return {
            "chapter_id": self.chapter_id,
            "path": self.rel,
            "ready": self.ready,
            "found": [{"field": hit.key, "where": hit.where} for hit in self.found],
            "missing": [{"field": spec.key, "label": spec.label, "required": spec.key in REQUIRED_FOR_DRAFT} for spec in self.missing],
            "creative_scope": self.scope,
            "body_sha256": self.body_sha,
        }


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------
def plan_files(pp: ProjectPaths) -> list[Path]:
    """项目里所有可能是章卡的档案（`02_Planning` 下全部 Markdown）。"""
    root = pp.planning
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*.md") if not _is_design_draft(path, root))


def _is_design_draft(path: Path, root: Path) -> bool:
    """设计草稿目录里的东西不是章卡。目录名带 `_` 前缀是项目约定。"""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return False
    return any(part.startswith("_") for part in parts[:-1])


def find_plan(pp: ProjectPaths, chapter_id: str) -> tuple[Path, dict, str]:
    """按 frontmatter 的 `id` 或 `chapter_id` 找章卡。

    两个键都认，因为历史上有两种写法：
      - `chapter_id`：规划与章节分开编号后的规范形态（`plan_000009` ↔ `ch_000123`）；
      - `id`：早期把章号直接当规划 id 用，`novel plan show plan_000001` 依赖这条。

    只认 `id` 会造成一条断链：`novel write --chapter ch_XXXXXX` 传给本函数的
    是**章节**号，而真库 23 份规划的 `id` 全是 `plan_XXXXXX`，于是永远匹配不到、
    永远拒绝开写。参数名叫 `--chapter`，实际要传规划号，这种接口本身就是陷阱。

    不按文件名匹配：文件名会改，`id` 是身份。
    匹配到多份直接报错——同一章两份规划，先问作者哪份该留。
    """
    target = chapter_id.strip()
    if not target:
        raise UsageError("必须给章节 ID 或规划 ID，例如 ch_000123 / plan_000009")
    hits: list[tuple[Path, dict, str]] = []
    wrong_type: list[str] = []
    for path in plan_files(pp):
        text = read_text(path)
        try:
            doc = md.parse(text, str(path))
        except Exception:
            continue
        fid = str(doc.meta.get("id") or "").strip()
        fch = str(doc.meta.get("chapter_id") or "").strip()
        if target not in (fid, fch):
            continue
        kind = str(doc.meta.get("type") or "").strip()
        # `type` 缺失时放宽；写了别的类型（比如 scene_card）就不算章卡。
        if kind and kind not in ("chapter_outline", "chapter_plan", "plan", "outline"):
            wrong_type.append(f"{path.name}（type: {kind}）")
            continue
        hits.append((path, doc.meta, doc.body))
    if not hits:
        hint = f"；同 ID 的档案存在但类型不是规划：{'、'.join(wrong_type)}" if wrong_type else ""
        raise UsageError(
            f"找不到 {target} 的章节规划：{_rel(pp, pp.planning)} 下没有 frontmatter "
            f"id 或 chapter_id 匹配的档案{hint}"
            f"\n  还没建的话：novel plan create --chapter {target} --title <章标题>"
        )
    if len(hits) > 1:
        names = "、".join(path.name for path, _, _ in hits)
        raise ConflictError(
            f"{target} 匹配到 {len(hits)} 份规划：{names}。"
            "先确认哪一份该留（重名会让 preflight 与写作读到不同的「必须发生」）"
        )
    return hits[0]


def check(pp: ProjectPaths, chapter_id: str) -> PlanCheck:
    """逐项报「有 / 没有」。不做推断，不给面子。"""
    path, meta, body = find_plan(pp, chapter_id)
    rel = _rel(pp, path)
    result = PlanCheck(chapter_id=chapter_id, path=path, rel=rel, meta=meta)
    result.body_sha = sha256_text(body)

    headings = _headings(body)
    for spec in PLAN_FIELDS:
        where = ""
        for key in spec.meta_keys:
            value = meta.get(key)
            if _present(value):
                where = f"frontmatter:{key}"
                break
        if not where:
            for heading in headings:
                if any(token in heading for token in spec.headings):
                    where = f"正文小节「{heading}」"
                    break
        if where:
            result.found.append(FieldHit(spec.key, where))
        else:
            result.missing.append(spec)

    raw_scope = meta.get("creative_scope")
    if isinstance(raw_scope, dict):
        result.scope = {
            "allowed": [str(item) for item in raw_scope.get("allowed") or []],
            "approval_required": [str(item) for item in raw_scope.get("approval_required") or []],
            "forbidden": [str(item) for item in raw_scope.get("forbidden") or []],
            "source": rel,
        }
    return result


def _present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _headings(body: str) -> list[str]:
    out: list[str] = []
    for line in body.splitlines():
        found = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if found:
            out.append(found.group(1).strip())
    return out


def _rel(pp: ProjectPaths, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(pp.root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# 派生：只做确定性可读的两处
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DerivedItem:
    text: str
    locator: str      # `相对路径#L起-L止`
    heading: str
    origin: str       # must_happen / must_not_happen / scope_forbidden


@dataclass
class Derivation:
    chapter_id: str
    rel: str
    must_happen: list[DerivedItem] = field(default_factory=list)
    must_not_happen: list[DerivedItem] = field(default_factory=list)
    scope_forbidden: list[DerivedItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.must_happen or self.must_not_happen or self.scope_forbidden)

    def as_dict(self) -> dict:
        def dump(items: list[DerivedItem]) -> list[dict]:
            return [{"text": item.text, "source": item.locator} for item in items]

        return {
            "chapter_id": self.chapter_id,
            "path": self.rel,
            "must_happen": dump(self.must_happen),
            "must_not_happen": dump(self.must_not_happen),
            "creative_scope": {"forbidden": dump(self.scope_forbidden)},
            "skipped": list(self.skipped),
        }


_SECTION_HEADINGS = {
    "must_happen": ("必须发生", "必须推进"),
    "must_not_happen": ("禁止发生", "不得发生", "不得引入", "禁止引入"),
    "allowed_changes": ("允许的持久变化", "持久变化"),
}


def derive(pp: ProjectPaths, chapter_id: str) -> Derivation:
    """从散文式章卡派生结构化约束。

    只读两处：编号表格的第二列、明确禁令列表。其余一律不猜。
    """
    path, meta, body = find_plan(pp, chapter_id)
    rel = _rel(pp, path)
    result = Derivation(chapter_id=chapter_id, rel=rel)
    offset = _body_offset(read_text(path))

    # 已经有结构化字段就不派生——已有内容优先，程序不覆盖作者写的。
    if _present(meta.get("must_happen")):
        result.skipped.append("frontmatter 已有 must_happen，未派生")
    if _present(meta.get("must_not_happen")):
        result.skipped.append("frontmatter 已有 must_not_happen，未派生")

    sections = _sections(body, offset)

    if not _present(meta.get("must_happen")):
        for heading in _match_sections(sections, _SECTION_HEADINGS["must_happen"]):
            rows = _table_rows(heading)
            for text, line_start, line_end in rows:
                result.must_happen.append(
                    DerivedItem(text, f"{rel}#L{line_start}-L{line_end}", heading.title, "must_happen")
                )

    if not _present(meta.get("must_not_happen")):
        for heading in _match_sections(sections, _SECTION_HEADINGS["must_not_happen"]):
            for text, line_start, line_end in _list_items(heading):
                item = DerivedItem(
                    text, f"{rel}#L{line_start}-L{line_end}", heading.title, "must_not_happen"
                )
                result.must_not_happen.append(item)

    # scope.forbidden 从「明确禁令」收：必须真是禁令语气，而不是一句叙述恰好含「不」。
    existing = meta.get("creative_scope")
    existing_forbidden = set()
    if isinstance(existing, dict):
        existing_forbidden = {str(item).strip() for item in existing.get("forbidden") or []}
    for item in result.must_not_happen:
        if not item.text.startswith(_FORBIDDEN_LEAD):
            result.skipped.append(f"不是禁令语气，未收进 creative_scope：{item.text[:24]}")
            continue
        if item.text in existing_forbidden:
            continue
        result.scope_forbidden.append(
            DerivedItem(item.text, item.locator, item.heading, "scope_forbidden")
        )
    return result


@dataclass(frozen=True)
class _Section:
    title: str
    level: int
    start: int          # 标题行（1-based，文件行号）
    end: int            # 下一个小节标题之前（含）
    lines: tuple[tuple[int, str], ...]   # (文件行号, 文本)


def _body_offset(text: str) -> int:
    """frontmatter 占了多少行。正文第一行的文件行号 = 偏移 + 1。"""
    if not text.startswith("---"):
        return 0
    lines = text.splitlines()
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return index + 1
    return 0


def _sections(body: str, offset: int) -> list[_Section]:
    raw = body.splitlines()
    heads: list[tuple[int, int, str]] = []   # (index in raw, level, title)
    for index, line in enumerate(raw):
        found = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if found:
            heads.append((index, len(found.group(1)), found.group(2).strip()))
    out: list[_Section] = []
    for position, (index, level, title) in enumerate(heads):
        end = len(raw)
        for next_index, next_level, _ in heads[position + 1:]:
            if next_level <= level:
                end = next_index
                break
        lines = tuple((offset + i + 1, raw[i]) for i in range(index + 1, end))
        out.append(_Section(title, level, offset + index + 1, offset + end, lines))
    return out


def _match_sections(sections: list[_Section], tokens: tuple[str, ...]) -> list[_Section]:
    return [sec for sec in sections if any(token in sec.title for token in tokens)]


def _table_rows(section: _Section) -> list[tuple[str, int, int]]:
    """取小节里第一张表的数据行，第二列当内容。

    认表头与分隔行（`| --- |`）是必需的：把表头当成一条「必须发生」
    会让约束里多出一句「事件」，直接污染 preflight。
    """
    rows: list[tuple[str, int, int]] = []
    header_seen = False
    for line_no, line in section.lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if rows:
                break        # 表格结束后不再往下找，避免抓到后文的表格
            continue
        cells = _split_row(stripped)
        if not header_seen:
            header_seen = True
            continue
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
            continue
        if len(cells) < 2:
            continue
        text = cells[1].strip()
        if text and text not in ("—", "-", "–"):
            rows.append((text, line_no, line_no))
    return rows


def _split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def _list_items(section: _Section) -> list[tuple[str, int, int]]:
    """列表项，支持缩进续行。返回值带起止行号，好让作者点回原文。"""
    items: list[tuple[str, int, int]] = []
    buffer: list[str] = []
    start = 0
    last = 0

    def flush() -> None:
        nonlocal buffer, start
        if buffer:
            text = "".join(buffer).strip()
            if text:
                items.append((text, start, last))
        buffer = []

    for line_no, line in section.lines:
        stripped = line.strip()
        match = re.match(r"^\s{0,3}(?:[-*+]|\d{1,9}[.)])\s+(.+)$", line)
        if match:
            flush()
            buffer = [match.group(1).strip()]
            start = line_no
            last = line_no
            continue
        if buffer and stripped and not stripped.startswith(("#", "|", ">")):
            buffer.append(stripped)
            last = line_no
            continue
        flush()
    flush()
    return items


# ---------------------------------------------------------------------------
# 写回：只插入缺失键，正文逐字节不动
# ---------------------------------------------------------------------------
def apply(
    pp: ProjectPaths,
    runtime: Path,
    chapter_id: str,
    derivation: Derivation | None = None,
) -> dict:
    """把派生结果写回章卡 frontmatter。

    三条硬线：
    1. 已有键不动 —— 作者写过的 `must_happen` 不因为「程序派生了 9 条」被替换；
    2. 正文不动 —— 写回前后 `body` 必须逐字节相同，不同就回滚并报错；
    3. 先备份 —— 落 `<runtime>/backups/plan/`，备份失败不写。
    """
    derivation = derivation or derive(pp, chapter_id)
    path, meta, _ = find_plan(pp, chapter_id)
    original = read_text(path)

    additions: dict = {}
    if derivation.must_happen and not _present(meta.get("must_happen")):
        additions["must_happen"] = [item.text for item in derivation.must_happen]
    if derivation.must_not_happen and not _present(meta.get("must_not_happen")):
        additions["must_not_happen"] = [item.text for item in derivation.must_not_happen]

    forbidden = list(derivation.scope_forbidden)
    if forbidden:
        raw_scope = meta.get("creative_scope")
        if not isinstance(raw_scope, dict):
            additions["creative_scope"] = {"forbidden": [item.text for item in forbidden]}
        elif not raw_scope.get("forbidden"):
            # 只有「forbidden 为空」时才补，绝不动 allowed / approval_required。
            merged = dict(raw_scope)
            merged["forbidden"] = [item.text for item in forbidden]
            additions["creative_scope"] = merged

    if not additions:
        return {"written": False, "reason": "没有可写回的键（已有结构化字段或不含确定性条目）", "path": _rel(pp, path)}

    updated = _insert_frontmatter(original, additions)
    before = md.parse(original, str(path))
    after = md.parse(updated, str(path))
    if after.body != before.body:
        raise ConflictError(
            "写回会导致正文变化，已中止。plan derive 只允许改 frontmatter——"
            "正文变化只能由作者或写作流程产生"
        )

    backup = _backup(runtime, path)
    write_text_atomic(path, updated)
    return {
        "written": True,
        "path": _rel(pp, path),
        "backup": str(backup),
        "added": sorted(additions),
        "counts": {
            "must_happen": len(additions.get("must_happen") or []),
            "must_not_happen": len(additions.get("must_not_happen") or []),
            "forbidden": len((additions.get("creative_scope") or {}).get("forbidden") or []),
        },
        "body_sha256": sha256_text(after.body),
    }


def _insert_frontmatter(text: str, additions: dict) -> str:
    """在 frontmatter 结束的 `---` 前插入新键，其余字节原样保留。

    不用「解析成 dict 再整体 dump」：那会重排原有键、改掉引号风格，
    对作者来说等于「我只想加两行，结果整段 frontmatter 都变了」。
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise UsageError("章卡没有 frontmatter，无法写回（先按模板补齐 --- 块）")
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        raise UsageError("frontmatter 没有结束标记 ---，拒绝写回")
    block = dump_yaml(additions).rstrip("\n")
    inserted = block + "\n"
    return "".join(lines[:end]) + inserted + "".join(lines[end:])


def _backup(runtime: Path, path: Path) -> Path:
    from ..audit import now_iso

    target_dir = runtime / "backups" / "plan"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_iso().replace(":", "").replace("-", "").replace("+", "_")
    target = target_dir / f"{path.stem}.{stamp}.md"
    write_text_atomic(target, read_text(path))
    return target


def render_yaml(derivation: Derivation) -> str:
    """打印给作者看的 YAML 片段（不写盘时用）。"""
    payload = {
        "must_happen": [item.text for item in derivation.must_happen],
        "must_not_happen": [item.text for item in derivation.must_not_happen],
        "creative_scope": {"forbidden": [item.text for item in derivation.scope_forbidden]},
    }
    return dump_yaml(payload).rstrip()


# ---------------------------------------------------------------------------
# 建章卡
# ---------------------------------------------------------------------------
# 章纲落在 `03_故事规划/章节细纲/`。这个目录名与 `cli/init.py` 的 PLANNING_DIRS
# 是同一份约定——两处必须同字符串，否则新项目骨架里不会有这个目录。
CHAPTER_PLAN_DIR = "章节细纲"

# 骨架只放小节标题，**不预填任何示例内容**。
#
# 原因：`derive()` 从「必须发生」表格第二列与「禁止」小节的禁令句里派生结构化约束。
# 骨架里写一句示例（比如「主角拿到钥匙」），derive 会把它当成作者原话读进去，
# 于是程序替作者做了决定——正是本模块开头那条边界要防的事。
_SKELETON = """## POV

## 时间与地点

- 故事时间：
- 场景：

## 角色出场

## 入场状态

## 核心冲突

## 章节目标

## 章末变化

## 必须发生

## 禁止发生

## 持久变化

## 禁止提前揭示

## 关联伏笔

## 依赖
"""

_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\r\n\t]')


@dataclass
class CreatedPlan:
    """一份新建的章纲。`dry_run` 时 `path` 是**将要**写入的位置。"""

    plan_id: str
    chapter_id: str
    path: Path
    rel: str
    title: str
    dry_run: bool

    def as_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "chapter_id": self.chapter_id,
            "path": self.rel,
            "title": self.title,
            "dry_run": self.dry_run,
        }


def create(
    pp: ProjectPaths,
    runtime: Path,
    *,
    chapter_id: str,
    title: str,
    volume_id: str = "",
    actor: str = "author-local",
    confirm: bool = False,
) -> CreatedPlan:
    """建一份章纲骨架。只建不存在的，且只建空骨架。

    两条拒绝写在这里而不是留给调用方：

    1. **同一章已有规划 → 拒绝。** `find_plan()` 在多份匹配时报错，那时要作者
       在两份之间挑，而两份都「看起来对」；在这里拦住，问题就不会发生。
    2. **章号形态不对 → 拒绝。** 规划靠 `chapter_id` 被找到，写错一个字符，
       这份章纲对 `novel write` 就等于不存在。

    不发新号则返回「将要发」的那个号（`dry_run=True`），预演看到的号就是
    落盘拿到的号——同一套取号逻辑。
    """
    cid = (chapter_id or "").strip()
    if not re.fullmatch(r"ch_[0-9]{6}", cid):
        raise UsageError(f"章节 ID 应为 ch_XXXXXX（六位数字），收到 {chapter_id!r}")

    name = _UNSAFE_NAME.sub("", (title or "").strip())
    if not name:
        raise UsageError("--title 不能为空：文件名与 frontmatter 的 name 都要用它")

    for path in plan_files(pp):
        try:
            doc = md.parse(read_text(path), str(path))
        except Exception:
            continue
        keys = {
            str(doc.meta.get("id") or "").strip(),
            str(doc.meta.get("chapter_id") or "").strip(),
        }
        if cid in keys:
            raise ConflictError(
                f"{cid} 已经有章纲：{path.name}。"
                "一章两份规划会让 preflight 与 write 读到不同的「必须发生」，"
                "先确认哪份该留"
            )

    alloc = IdAllocator(pp.vault, runtime, actor=actor, areas=pp.area_roots())
    issued = alloc.allocate("plan", count=1, reason=f"建章纲 {cid}", dry_run=not confirm)
    plan_id = issued[0]

    from ..audit import now_iso  # 与本模块其它处一致：延迟导入，避免顶层循环

    stamp = now_iso()[:10]
    meta: dict[str, object] = {
        "id": plan_id,
        "type": "chapter_outline",
        "chapter_id": cid,
        "name": name,
        "title": name,
        "created_at": stamp,
        "updated_at": stamp,
    }
    if volume_id.strip():
        meta["volume_id"] = volume_id.strip()

    path = pp.planning / CHAPTER_PLAN_DIR / f"{plan_id}_{name}.md"
    text = "---\n" + dump_yaml(meta).rstrip("\n") + "\n---\n\n" + _SKELETON
    rel = _rel(pp, path)

    if confirm:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise ConflictError(f"目标文件已存在：{rel}")
        write_text_atomic(path, text)

    return CreatedPlan(
        plan_id=plan_id, chapter_id=cid, path=path, rel=rel, title=name, dry_run=not confirm
    )
