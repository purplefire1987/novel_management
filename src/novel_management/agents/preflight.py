"""写前资料包（Preflight）。PRD §13 逐条落地。

一件事说清楚：**这一层不用模型也能跑完**。Orchestrator 是确定性 Python
执行层（PRD §6.2），资料包里哪些是事实、哪些是没查到，必须由代码算，
不能由模型说了算。所以默认路径是「检索 + 时间线 + 规划」拼出来的；
给了 `llm` 时，资料官（researcher）只做一件加值的事——把规划里的
「必须发生／禁止发生」提炼成结构化约束。提炼失败就退回原文照录，
**不因为模型没答好就不给包**。

§13.2 的八级检索顺序，逐级对应到代码：

| 级 | 做什么 | 落点 |
|---|---|---|
| 1 精确 ID | 规划按 frontmatter id 找 | `plan` 工具 |
| 2 别名解析 | 规划里的人和地，按名/别名解成实体 ID | `_resolve_entity` |
| 3 结构化状态 | 每个参与实体的客观字段 | `state` 工具 |
| 4 事件与认知 | 相关事件、POV 认知、读者揭示 | `related_events` / `knowledge` / `reveal` |
| 5 关键词 | 按实体名回查档案与正文 | `search` 工具 |
| 6 近期正文 | 上一章正文节选 | `_recent_text` |
| 7 卷级摘要 | 所属卷的纲节选 | `_volume_summary` |
| 8 按需补查 | 查不到的进 `unresolved_gaps`，不是「没有」 | `coverage` |

八级都在预算里走一遍（`budget.place`）。**截断的进 `truncated_results`，
硬约束放不下就 BLOCKED**——两条验收就压在这一句上。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..infrastructure.filesystem import markdown as md
from ..adapters.mock import LLMClient
from novel_management.domain.canonical import utc_now
from ..domain.errors import UsageError
from ..infrastructure.database.indexing import locate as locate_mod
from ..infrastructure.database.indexing.search import index_metadata as index_metadata_of
from ..infrastructure.filesystem.paths import ProjectPaths
from ..schema import check as schema_check
from ..infrastructure.filesystem.storage import dump_yaml, sha256_file, write_text_atomic
from novel_management.domain.version import __version__
from . import registry, runner, tools
from .budget import Budget
from .metering import Meter
from .tools import ToolContext

PREFLIGHT_DIR = "preflight"
ID_PREFIX = "PREFLIGHT"
SCHEMA_NAME = "preflight"

CHAPTER_ID_RE = re.compile(r"^CH-[0-9]{4,6}$")
PREFLIGHT_ID_RE = re.compile(rf"^{ID_PREFIX}-([0-9]{{4,6}})$")

# 一次 preflight 最多追多少个实体。规划里列了几十个名字时，
# 全量追会把预算吃光，然后把该看的伏笔挤掉——先截断并记下来，比默默少看更安全。
MAX_ENTITIES = 24
MAX_KEYWORD_HITS = 6
RECENT_TEXT_CHARS = 1200
VOLUME_CHARS = 800

# 关键消息。测试与 CLI 都靠这些字样判断「到底为什么被拦」。
MSG_PLANNING_MISSING = "找不到该章的规划：02_Planning 里没有 frontmatter id 匹配的档案"
MSG_SCOPE_MISSING = "规划里没有 creative_scope（创作授权），无法判定可写范围"
MSG_BUDGET_OVERFLOW = "硬约束放不下预算：不压缩硬约束、也不静默丢弃，只能阻断"
MSG_FORESHADOW_OVERDUE = "有 HARD 窗口已过期且未回收的伏笔"
MSG_NO_RELEASE = "尚无正式发布：本次按工作树快照计算，不含任何已发布版本的含义"
MSG_INDEX_MISSING = "索引不可用：无法做状态、认知与关键词检索"
# 缺一整类硬约束不等于「这次没有这类问题」。旧索引要拦住，不能只提示。
MSG_FORESHADOW_UNAVAILABLE = (
    "索引里读不到伏笔数据：未回收承诺这一整类硬约束本次无法检查，等于没做"
    "——先 novel rebuild-index 再跑 preflight"
)

SEV_BLOCKER = "BLOCKER"
SEV_NON_BLOCKING = "NON_BLOCKING"
SEV_SUGGESTION = "SUGGESTION"

GATE_PASS = "PASS"
GATE_BLOCKED = "BLOCKED"

# 已了结的伏笔状态。剩下的一律算「未回收承诺」（PRD §10.4）。
CLOSED_FORESHADOW_STATES = frozenset({"PAID_OFF", "ABANDONED"})


def preflight_dir(runtime: Path) -> Path:
    return Path(runtime) / PREFLIGHT_DIR


def issue(
    severity: str,
    category: str,
    message: str,
    evidence: list[str] | None = None,
) -> dict:
    return {
        "severity": severity,
        "category": category,
        "message": message,
        "evidence": list(evidence or []),
    }


@dataclass
class PreflightResult:
    doc: dict
    path: Path | None = None

    @property
    def gate(self) -> str:
        return str(self.doc.get("gate_result") or "")

    @property
    def passed(self) -> bool:
        return self.gate == GATE_PASS

    @property
    def blockers(self) -> list[dict]:
        return [item for item in self.doc.get("issues", []) if item.get("severity") == SEV_BLOCKER]

    @property
    def truncations(self) -> list[dict]:
        return list(self.doc.get("coverage", {}).get("truncated_results", []))

    def as_dict(self) -> dict:
        return {"doc": self.doc, "path": str(self.path) if self.path else ""}


def next_id(runtime: Path) -> str:
    """下一个 preflight 号。只看已有文件的最大号，不复用空号——
    号是引用锚点，复用会让「PREFLIGHT-000003」在两次对话里指两个东西。
    """
    highest = 0
    directory = preflight_dir(runtime)
    if directory.is_dir():
        for path in directory.iterdir():
            match = PREFLIGHT_ID_RE.match(path.stem)
            if match:
                highest = max(highest, int(match.group(1)))
    return f"{ID_PREFIX}-{highest + 1:06d}"


# ---------------------------------------------------------------------------
# 规划解析
# ---------------------------------------------------------------------------
_WIKI_LINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
_ENTITY_ID = re.compile(r"^([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)")


def _meta_list(meta: dict, *keys: str) -> list[str]:
    for key in keys:
        value = meta.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return [value.strip()]
        elif isinstance(value, (list, tuple)):
            items = [str(item).strip() for item in value if str(item).strip()]
            if items:
                return items
    return []


def _entity_refs(plan_meta: dict, plan_body: str) -> list[str]:
    """从规划里抽「参与实体」。两条来源，都不猜：

    1. frontmatter 的 participants／entities／characters／pov；
    2. 正文里的 `[[ID_名称|别名]]` 双链——先把 ID 部分取出来。

    抽不到就返回空，由调用方记一条 gap。**不要从人名反推 ID**：
    同名不同人、同名不同朝的情况在长篇里太常见。
    """
    refs: list[str] = []
    for raw in _meta_list(plan_meta, "participants", "entities", "characters", "pov"):
        match = _ENTITY_ID.match(raw.strip())
        if match:
            refs.append(match.group(1))
        else:
            refs.append(raw.strip())
    for match in _WIKI_LINK.finditer(plan_body or ""):
        target = match.group(1).strip()
        found = _ENTITY_ID.match(target)
        if found:
            refs.append(found.group(1))
    seen: list[str] = []
    for ref in refs:
        if ref and ref not in seen:
            seen.append(ref)
    return seen


def _project_arg(ctx: ToolContext) -> str:
    return ctx.project or ctx.pp.project_id


def _entity_name(ctx: ToolContext, entity_id: str) -> str:
    where, where_args = tools.project_clause("entities", _project_arg(ctx))
    row = ctx.conn.execute(
        "select name from entities where id = ?" + where + " limit 1",
        (entity_id, *where_args),
    ).fetchone()
    return str(row["name"] or "").strip() if row else ""


def _resolve_entity(ctx: ToolContext, name: str) -> tuple[str, str]:
    """把名字解成实体 ID。返回 (id, 依据)；解不出返回 ("", 原因)。

    `looks_like_id` 的直接认——规划里写了 ID 就是最强依据。
    否则按 entities.name 与 aliases 精确匹配，**只认精确**：
    模糊匹配在中文里会把「李三」解到「李三娘」。
    """
    token = (name or "").strip()
    if not token:
        return "", "空名字"
    if _ENTITY_ID.match(token) and re.search(r"-[0-9]", token):
        return token, "id"
    where, where_args = tools.project_clause("entities", _project_arg(ctx))
    try:
        rows = ctx.conn.execute(
            "select id from entities where (name = ? collate nocase"
            "      or id in (select entity_id from aliases where alias = ? collate nocase))"
            + where,
            (token, token, *where_args),
        ).fetchall()
    except Exception:  # 索引不可用时不要崩在这里，交给上层记 issue
        return "", "索引不可用"
    ids = [row["id"] for row in rows]
    if not ids:
        return "", "库里没有这个实体"
    if len(set(ids)) > 1:
        return "", f"名字对到多个实体：{', '.join(sorted(set(ids)))}"
    return ids[0], "name"


def _section_lines(body: str, keyword: str) -> list[str]:
    """从规划正文里取某个小节的条目。

    只认「标题里含 keyword 的小节」，取到下一个同级标题为止。
    这是给「必须发生／禁止发生」用的：规划怎么写就怎么抄，
    **不替作者归纳**——归纳错了比不归纳更危险。
    """
    items: list[str] = []
    active = False
    level = 0
    for line in (body or "").splitlines():
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            depth = len(heading.group(1))
            title = heading.group(2)
            if keyword in title:
                active = True
                level = depth
                continue
            if active and depth <= level:
                active = False
            continue
        if active:
            stripped = line.strip()
            if stripped and not stripped.startswith(">"):
                items.append(stripped.lstrip("-*").strip())
    return [item for item in items if item]


def _constraints_from_plan(plan_meta: dict, plan_body: str, plan_rel: str) -> list[dict]:
    """关键约束：优先取规划里的结构化字段，其次取正文小节的条目。"""
    items: list[dict] = []
    for raw in _meta_list(plan_meta, "must_happen", "required_events"):
        items.append({"text": raw, "kind": "MUST", "source": plan_rel})
    for raw in _meta_list(plan_meta, "must_not_happen", "forbidden_events"):
        items.append({"text": raw, "kind": "MUST_NOT", "source": plan_rel})
    for keyword, kind in (("必须发生", "MUST"), ("禁止", "MUST_NOT"), ("不得", "MUST_NOT")):
        for raw in _section_lines(plan_body, keyword):
            items.append({"text": raw, "kind": kind, "source": plan_rel})
    scope = plan_meta.get("creative_scope")
    if isinstance(scope, dict):
        for raw in scope.get("forbidden") or []:
            text = str(raw).strip()
            if text:
                items.append({"text": text, "kind": "SCOPE", "source": plan_rel})
    # 去重但保持顺序：同一句话在 frontmatter 和正文各写一遍是常事。
    seen: set[str] = set()
    unique: list[dict] = []
    for item in items:
        if item["text"] in seen:
            continue
        seen.add(item["text"])
        unique.append(item)
    return unique


def _creative_scope(plan_meta: dict, plan_rel: str) -> dict:
    raw = plan_meta.get("creative_scope")
    if not isinstance(raw, dict):
        return {}
    scope = {
        "allowed": [str(item) for item in raw.get("allowed") or []],
        "approval_required": [str(item) for item in raw.get("approval_required") or []],
        "forbidden": [str(item) for item in raw.get("forbidden") or []],
        "source": plan_rel,
    }
    return scope


# ---------------------------------------------------------------------------
# 近期正文与卷级摘要
# ---------------------------------------------------------------------------
def _ordinal(chapter_id: str) -> int:
    found = re.search(r"([0-9]{4,6})$", chapter_id or "")
    return int(found.group(1)) if found else 0


def _manuscript_docs(pp: ProjectPaths) -> list[tuple[int, Path, dict]]:
    root = pp.manuscripts
    rows: list[tuple[int, Path, dict]] = []
    if not root.is_dir():
        return rows
    for path in sorted(root.rglob("*.md")):
        try:
            meta = md.parse(path.read_text(encoding="utf-8", errors="replace"), str(path)).meta
        except Exception:
            continue
        raw_id = str(meta.get("id") or "").strip()
        if not CHAPTER_ID_RE.match(raw_id):
            continue
        rows.append((_ordinal(raw_id), path, meta))
    return rows


def _recent_text(pp: ProjectPaths, chapter_id: str) -> dict:
    """上一章正文节选。找不到就把原因写进 note，不要给一段空字符串。"""
    target = _ordinal(chapter_id)
    candidates = [row for row in _manuscript_docs(pp) if row[0] and row[0] < target]
    if not candidates:
        return {"chapter_id": "", "path": "", "excerpt": "", "chars": 0,
                "note": "没有更早的正文；本章是开篇或正文尚未入库"}
    _, path, meta = max(candidates, key=lambda row: row[0])
    body = md.parse(path.read_text(encoding="utf-8", errors="replace"), str(path)).body
    excerpt = body.strip()[:RECENT_TEXT_CHARS]
    return {
        "chapter_id": str(meta.get("id") or ""),
        "path": pp.rel(path),
        "excerpt": excerpt,
        "chars": len(excerpt),
        "note": f"节选前 {RECENT_TEXT_CHARS} 字" if len(body.strip()) > RECENT_TEXT_CHARS else "",
    }


def _volume_summary(ctx: ToolContext, plan_meta: dict) -> dict:
    volume_id = str(plan_meta.get("volume_id") or "").strip()
    if not volume_id:
        return {"volume_id": "", "path": "", "excerpt": "", "note": "规划里没有 volume_id"}
    result = tools.call(ctx, "researcher", "plan", chapter=volume_id)
    payload = result.payload
    if not payload.get("found"):
        return {"volume_id": volume_id, "path": "", "excerpt": "", "note": "找不到该卷的规划"}
    body = str(payload.get("body") or "").strip()
    excerpt = body[:VOLUME_CHARS]
    return {
        "volume_id": volume_id,
        "path": str(payload.get("path") or ""),
        "excerpt": excerpt,
        "note": f"节选前 {VOLUME_CHARS} 字" if len(body) > VOLUME_CHARS else "",
    }


# ---------------------------------------------------------------------------
# 检索各级
# ---------------------------------------------------------------------------
def _events_of(ctx: ToolContext, entity_ids: list[str]) -> list[dict]:
    """参与实体身上被记录过的事件。事件表空了就返回空——不编。"""
    if not entity_ids:
        return []
    marks = ",".join("?" for _ in entity_ids)
    where, where_args = tools.project_clause("e", _project_arg(ctx))
    rows = ctx.conn.execute(
        "select distinct a.subject_id, e.id, e.title, e.path"
        " from assertions a join events e on e.id = a.id or e.id = a.valid_from_event"
        f" where a.subject_id in ({marks})" + where,
        (*entity_ids, *where_args),
    ).fetchall()
    rows = [row for row in rows if row["id"]]
    return [
        {
            "event_id": str(row["id"]),
            "title": str(row["title"] or ""),
            "relation": f"涉及 {row['subject_id']}",
            "source": str(row["path"] or ""),
        }
        for row in rows
    ]


def _reveals_of(ctx: ToolContext, entity_ids: list[str]) -> list[dict]:
    if not entity_ids:
        return []
    marks = ",".join("?" for _ in entity_ids)
    where, where_args = tools.project_clause("assertions", _project_arg(ctx))
    rows = ctx.conn.execute(
        "select distinct id from assertions"
        f" where subject_id in ({marks})" + where + " limit 5",
        (*entity_ids, *where_args),
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        result = tools.call(ctx, "researcher", "reveal", proposition=str(row["id"]))
        payload = result.payload
        out.append(
            {
                "proposition_id": str(row["id"]),
                "status": str(payload.get("status") or ""),
                "position": str(payload.get("value") or ""),
                "source": str((payload.get("records") or [{}])[0].get("path") or ""),
            }
        )
    return out


def _keyword_hits(ctx: ToolContext, topics: list[str], limit: int) -> tuple[list[dict], list[dict]]:
    """按关键词回查。返回 (hits, truncations)。

    命中数达到 limit 时，**不能把结果当成「就这么多」**（PRD §13.2）。
    这里把 `total > len(hits)` 转成一条截断记录，进 `coverage.truncated_results`。
    """
    hits: list[dict] = []
    truncations: list[dict] = []
    for topic in topics:
        result = tools.call(ctx, "researcher", "search", query=topic, limit=limit, scope="")
        payload = result.payload
        for hit in payload.get("hits") or []:
            hits.append(
                {
                    "path": str(hit.get("path") or ""),
                    "artifact_id": str(hit.get("artifact_id") or ""),
                    "block_id": str(hit.get("block_id") or ""),
                    "quote": str(hit.get("snippet") or ""),
                }
            )
        if result.truncated:
            total = int(payload.get("total") or 0)
            kept = len(payload.get("hits") or [])
            truncations.append(
                {
                    "section": f"search:{topic}",
                    "kept": kept,
                    "dropped": max(total - kept, 0),
                    "total": max(total, kept + 1),
                    "reason": "Top-K 上限；命中数不等于全部，需要时按需补查",
                }
            )
    return hits, truncations


def _foreshadowing(ctx: ToolContext) -> tuple[list[dict], list[dict], bool]:
    """返回 (条目, 附注, 是否真的查到了表)。

    第三项不是装饰：False 意味着「这一整类硬约束没查」，调用方必须当阻断处理。
    """
    result = tools.call(ctx, "researcher", "foreshadow")
    payload = result.payload
    items: list[dict] = []
    for row in payload.get("items") or []:
        items.append(
            {
                "id": str(row.get("id") or ""),
                "title": str(row.get("title") or ""),
                "state": str(row.get("state") or ""),
                "window_policy": str(row.get("window_policy") or ""),
                "window_end": str(row.get("window_end") or ""),
                "planted_in": str(row.get("planted_in") or ""),
                "payoff_in": str(row.get("payoff_in") or ""),
                "overdue": False,
                "source": "",
            }
        )
    supported = bool(payload.get("supported"))
    return items, [payload.get("note")] if payload.get("note") else [], supported


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def build(
    pp: ProjectPaths,
    runtime: Path,
    *,
    chapter_id: str,
    base_release: str = "",
    budget: Budget | None = None,
    llm: LLMClient | None = None,
    role: str = "researcher",
    task: dict | None = None,
    meter: Meter | None = None,
    search_limit: int = MAX_KEYWORD_HITS,
    write: bool = True,
) -> PreflightResult:
    """产出一份资料包。`write=False` 只算不落盘。"""
    chapter = (chapter_id or "").strip()
    if not CHAPTER_ID_RE.match(chapter):
        raise UsageError(f"章节 ID 形状不对：{chapter!r}（应形如 CH-000102）")

    registry.role(role)  # 角色不合法就在这里停，不要跑到一半才发现
    budget = budget or Budget()
    meter = meter or Meter(runtime=runtime)

    if not base_release:
        base_release = str(locate_mod.release_pointer(runtime).get("release_id") or "")

    doc: dict = {
        "preflight_id": next_id(runtime),
        "base_release": base_release or None,
        "chapter_id": chapter,
        "input_hashes": {},
        "story_anchor": {},
        "narrative_position": {},
        "key_constraints": [],
        "entity_states": [],
        "character_knowledge": [],
        "reader_revelations": [],
        "related_events": [],
        "foreshadowing": [],
        "creative_scope": {},
        "coverage": {
            "checked_entities": [],
            "checked_topics": [],
            "unresolved_gaps": [],
            "truncated_results": [],
        },
        "source_refs": [],
        "issues": [],
        "gate_result": GATE_PASS,
    }
    issues: list[dict] = doc["issues"]
    gaps: list[dict] = doc["coverage"]["unresolved_gaps"]
    truncated: list[dict] = doc["coverage"]["truncated_results"]

    if not base_release:
        issues.append(issue(SEV_NON_BLOCKING, "OTHER", MSG_NO_RELEASE))

    with ToolContext(pp=pp, runtime=runtime, project=pp.project_id, meter=meter) as ctx:
        try:
            ctx.conn  # 先探一次索引，缺索引时给出可读的 issue 而不是栈
        except Exception as exc:
            issues.append(issue(SEV_BLOCKER, "OTHER", f"{MSG_INDEX_MISSING}：{exc}"))
            doc["gate_result"] = GATE_BLOCKED
            return _finish(doc, budget, meter, runtime, write)

        try:
            doc["index"] = _index_info(ctx)
        except Exception:
            doc["index"] = {}

        _fill_from_plan(ctx, pp, doc, budget, issues, gaps, role=role)
        # 伏笔先于软资料放：它是硬约束（§13.3 必须检查的「未回收承诺」），
        # 排在软资料后面的话，等它上场时软资料已经把窗口吃满，
        # 缓冲就会「因为读了太多无关档案而阻断」——这不是作者要的那种拦。
        _fill_foreshadowing(ctx, doc, budget, issues)
        _fill_states(ctx, pp, doc, budget, issues, gaps)
        _fill_knowledge(ctx, pp, doc, budget, gaps)
        _fill_events_and_reveals(ctx, doc, budget, gaps)
        _fill_keywords(ctx, doc, budget, truncated, search_limit)
        _fill_recent_and_volume(ctx, pp, doc, budget, gaps)
        _merge_model_constraints(ctx, doc, budget, issues, llm, meter, base_release, task, role)

    _decide_gate(doc, budget, issues, gaps)
    return _finish(doc, budget, meter, runtime, write)


def _index_info(ctx: ToolContext) -> dict:
    meta = index_metadata_of(ctx.conn)
    return {
        "build_id": meta.get("build_id", ""),
        "canon_revision": meta.get("canon_revision", ""),
        "fts_mode": meta.get("fts_mode", ""),
    }


def _fill_from_plan(
    ctx: ToolContext,
    pp: ProjectPaths,
    doc: dict,
    budget: Budget,
    issues: list[dict],
    gaps: list[dict],
    *,
    role: str,
) -> None:
    chapter = doc["chapter_id"]
    result = tools.call(ctx, role, "plan", chapter=chapter)
    payload = result.payload

    if not payload.get("found"):
        issues.append(issue(SEV_BLOCKER, "OTHER", MSG_PLANNING_MISSING, [str(payload.get("path") or "")]))
        gaps.append({"topic": "planning", "detail": MSG_PLANNING_MISSING})
        return

    plan_rel = str(payload.get("path") or "")
    meta = payload.get("meta") or {}
    body = str(payload.get("body") or "")

    plan_path = pp.vault / plan_rel if plan_rel else None
    if plan_path is not None and plan_path.exists():
        doc["input_hashes"]["planning"] = sha256_file(plan_path)

    anchor_text = "；".join(
        item
        for item in (
            str(meta.get("timeline") or meta.get("story_time") or meta.get("story_anchor") or "").strip(),
            str(meta.get("time_note") or "").strip(),
        )
        if item
    )
    doc["story_anchor"] = {
        "story_time": anchor_text,
        "event_id": str(meta.get("anchor_event") or ""),
        "source": plan_rel,
        "note": "" if anchor_text else "规划里没有故事时间锚点，时间相关判断无法落地",
    }
    if not anchor_text:
        gaps.append({"topic": "story_anchor", "detail": "规划没写故事时间（timeline／story_time）"})

    doc["narrative_position"] = {
        "chapter_id": chapter,
        "scene_id": "",
        "pov": str(meta.get("pov") or ""),
        "position": "章节起点",
    }

    scope = _creative_scope(meta, plan_rel)
    if scope:
        doc["creative_scope"] = scope
    else:
        issues.append(issue(SEV_BLOCKER, "SCOPE", MSG_SCOPE_MISSING, [plan_rel]))
        gaps.append({"topic": "creative_scope", "detail": MSG_SCOPE_MISSING})

    constraints = _constraints_from_plan(meta, body, plan_rel)
    doc["coverage"]["checked_topics"].append("planning:constraints")
    fitted = budget.place("key_constraints", constraints, hard=True)
    doc["key_constraints"] = list(fitted.items)
    if fitted.overflow is not None:
        issues.append(
            issue(
                SEV_BLOCKER,
                "BUDGET",
                f"{MSG_BUDGET_OVERFLOW}（{fitted.overflow.describe()}）",
                [plan_rel],
            )
        )
    if not constraints:
        gaps.append(
            {
                "topic": "key_constraints",
                "detail": "规划里没有「必须发生／禁止发生」条目，也没有 creative_scope.forbidden",
            }
        )

    # §13.2 第 1、2 级：已解析出实体时先记进 coverage
    refs = _entity_refs(meta, body)
    if refs:
        doc["_pending_entity_refs"] = refs
    else:
        gaps.append(
            {
                "topic": "participants",
                "detail": "规划里抽不到参与实体：frontmatter 没有 participants／pov 实体 ID，正文也没有 [[ID_名称]] 双链",
            }
        )


def _fill_states(
    ctx: ToolContext,
    pp: ProjectPaths,
    doc: dict,
    budget: Budget,
    issues: list[dict],
    gaps: list[dict],
) -> None:
    refs: list[str] = list(doc.pop("_pending_entity_refs", []) or [])
    resolved: list[str] = []
    terms: list[str] = []
    for raw in refs[:MAX_ENTITIES]:
        entity_id, how = _resolve_entity(ctx, raw)
        if not entity_id:
            gaps.append({"topic": "entity", "detail": f"{raw}：{how}"})
            continue
        resolved.append(entity_id)
        # 关键词检索用「规划里怎么写的」而不是实体 ID：作者写「抄书人」时，
        # 他要找的是正文里所有出现「抄书人」的地方，而 ID 只能命中档案本身。
        # 规划里直接写 ID 的情况，先把它换成实体名再查。
        term = raw.strip()
        if _ENTITY_ID.match(term) and re.search(r"-[0-9]", term):
            term = _entity_name(ctx, entity_id) or term
        terms.append(term)
    if len(refs) > MAX_ENTITIES:
        gaps.append(
            {
                "topic": "entity",
                "detail": f"规划里有 {len(refs)} 个参与实体，本次只追前 {MAX_ENTITIES} 个",
            }
        )
    doc["_resolved_entities"] = resolved
    doc["_keyword_terms"] = terms
    doc["coverage"]["checked_entities"] = list(resolved)
    if not resolved:
        return

    anchor = str(doc["story_anchor"].get("event_id") or "")
    entries: list[dict] = []
    for entity_id in resolved:
        result = tools.call(ctx, "researcher", "state", entity=entity_id, anchor=anchor)
        payload = result.payload
        entries.append(_state_of_payload(entity_id, anchor, payload))
    fitted = budget.place("entity_states", entries, hard=False)
    doc["entity_states"] = list(fitted.items)
    for entry in doc["entity_states"]:
        if entry["status"] and entry["status"] != "RESOLVED":
            gaps.append(
                {
                    "topic": "entity_state",
                    "detail": f"{entry['entity_id']} 的客观状态是 {entry['status']}（{entry.get('value') or '无记录'}）",
                }
            )


def _state_of_payload(entity_id: str, anchor: str, payload: dict) -> dict:
    records = payload.get("records") or []
    status = str(payload.get("status") or "")
    return {
        "entity_id": entity_id,
        "field": str(payload.get("predicate") or ""),
        # 值只放值。`NOT_RECORDED` 的解释走 note——把一整句说明塞进 value 会让
        # 下游把它当成「字段值就是这句话」，而它其实是一句关于查询结果的说明。
        "value": str(payload.get("value") or "") if status == "RESOLVED" else "",
        "status": status,
        "event": anchor,
        "note": "" if status == "RESOLVED" else str(payload.get("note") or ""),
        "source": str((records[0] if records else {}).get("path") or ""),
    }


def _fill_knowledge(
    ctx: ToolContext,
    pp: ProjectPaths,
    doc: dict,
    budget: Budget,
    gaps: list[dict],
) -> None:
    """只查「本章 POV 角色」的认知。

    为什么不查全部参与实体的认知：认知是双向的，N 个角色就是 N² 条查询，
    预算第一轮就被它吃光。POV 是「这一章读者跟着谁看」，它的认知错得最要命。
    """
    entity_ids = list(doc.pop("_resolved_entities", []) or [])
    if not entity_ids:
        return
    pov = str(doc["narrative_position"].get("pov") or "").strip()
    holders = [entity_id for entity_id in entity_ids if entity_id in pov]
    if not holders:
        holders = [entity_id for entity_id in entity_ids if entity_id.startswith("CHAR-")][:1]
    if not holders:
        gaps.append(
            {
                "topic": "character_knowledge",
                "detail": "认不出本章 POV 对应的角色实体 ID，认知检查未执行",
            }
        )
        return

    anchor = str(doc["story_anchor"].get("event_id") or "")
    entries: list[dict] = []
    for holder in holders:
        result = tools.call(ctx, "researcher", "knowledge", holder=holder, anchor=anchor)
        entries.extend(_knowledge_of_payload(holder, payload=result.payload))
    fitted = budget.place("character_knowledge", entries, hard=False)
    doc["character_knowledge"] = list(fitted.items)
    for entry in doc["character_knowledge"]:
        if entry["status"] and entry["status"] != "RESOLVED":
            gaps.append(
                {
                    "topic": "character_knowledge",
                    "detail": f"{entry['holder_id']} 的认知是 {entry['status']}——"
                    "「查不到」不等于「不知道」，写作时按未知处理并复述这条",
                }
            )
    doc["coverage"]["checked_topics"].append("knowledge:pov")


def _knowledge_of_payload(holder: str, *, payload: dict) -> list[dict]:
    records = payload.get("records") or []
    status = str(payload.get("status") or "")
    return [
        {
            "holder_id": holder,
            "subject_id": str(payload.get("subject") or ""),
            "predicate": str(payload.get("predicate") or ""),
            "value": str(payload.get("value") or "") if status == "RESOLVED" else "",
            "status": status,
            "note": "" if status == "RESOLVED" else str(payload.get("note") or ""),
            "source": str((records[0] if records else {}).get("path") or ""),
        }
    ]


def _fill_events_and_reveals(
    ctx: ToolContext, doc: dict, budget: Budget, gaps: list[dict]
) -> None:
    entity_ids = list(doc.get("coverage", {}).get("checked_entities") or [])
    if not entity_ids:
        return
    events = _events_of(ctx, entity_ids)
    fitted = budget.place("related_events", events, hard=False)
    doc["related_events"] = list(fitted.items)
    if not events:
        gaps.append({"topic": "related_events", "detail": "这几位参与实体身上没有已登记的事件"})

    reveals = _reveals_of(ctx, entity_ids)
    fitted = budget.place("reader_revelations", reveals, hard=False)
    doc["reader_revelations"] = list(fitted.items)
    if not reveals:
        gaps.append(
            {"topic": "reader_revelations", "detail": "没有客观断言档案，读者揭示检查未执行"}
        )


def _fill_keywords(
    ctx: ToolContext, doc: dict, budget: Budget, truncated: list[dict], search_limit: int
) -> None:
    topics = list(doc.pop("_keyword_terms", []) or [])
    if not topics:
        return
    hits, hits_truncations = _keyword_hits(ctx, topics, search_limit)
    doc["coverage"]["checked_topics"].extend(f"keyword:{topic}" for topic in topics)
    truncated.extend(hits_truncations)

    # 去重：同一个块被两个实体名命中时只留一条，否则 source_refs 会被灌水。
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for hit in hits:
        key = (hit["path"], hit["block_id"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    fitted = budget.place("source_refs", unique, hard=False, reason="来源引用超预算，截断（命中数已在 truncated_results）")
    doc["source_refs"] = list(fitted.items)


def _fill_recent_and_volume(
    ctx: ToolContext, pp: ProjectPaths, doc: dict, budget: Budget, gaps: list[dict]
) -> None:
    recent = _recent_text(pp, doc["chapter_id"])
    if recent.get("path"):
        path = pp.vault / recent["path"]
        if path.exists():
            doc["input_hashes"]["recent_text"] = sha256_file(path)
    fitted = budget.place("recent_text", [recent], hard=False)
    doc["recent_text"] = fitted.items[0] if fitted.items else {**recent, "excerpt": "", "chars": 0}
    if not recent.get("excerpt"):
        gaps.append({"topic": "recent_text", "detail": str(recent.get("note") or "没有近期正文")})

    plan_meta = {}
    plan_rel = str(doc["story_anchor"].get("source") or "")
    if plan_rel:
        path = pp.vault / plan_rel
        if path.exists():
            try:
                plan_meta = md.parse(path.read_text(encoding="utf-8", errors="replace"), plan_rel).meta
            except Exception:
                plan_meta = {}
    volume = _volume_summary(ctx, plan_meta)
    fitted = budget.place("volume_summary", [volume], hard=False)
    doc["volume_summary"] = fitted.items[0] if fitted.items else {**volume, "excerpt": ""}
    if not volume.get("excerpt"):
        gaps.append({"topic": "volume_summary", "detail": str(volume.get("note") or "没有卷级摘要")})


def _fill_foreshadowing(ctx: ToolContext, doc: dict, budget: Budget, issues: list[dict]) -> None:
    items, notes, supported = _foreshadowing(ctx)
    if supported:
        for note in notes:
            issues.append(issue(SEV_NON_BLOCKING, "RULES", note))
    else:
        issues.append(issue(SEV_BLOCKER, "RULES", MSG_FORESHADOW_UNAVAILABLE))
    unrecovered = [
        dict(item) for item in items if str(item.get("state") or "") not in CLOSED_FORESHADOW_STATES
    ]
    chapter = doc["chapter_id"]
    for item in unrecovered:
        window_end = str(item.get("window_end") or "")
        if window_end and _ordinal(window_end) and _ordinal(chapter) > _ordinal(window_end):
            item["overdue"] = True
    # 未回收承诺属于 §13.3 的「必须检查」，按硬约束放；装不下就阻断。
    fitted = budget.place("foreshadowing", unrecovered, hard=True)
    doc["foreshadowing"] = list(fitted.items)
    if fitted.overflow is not None:
        issues.append(
            issue(SEV_BLOCKER, "BUDGET", f"{MSG_BUDGET_OVERFLOW}（{fitted.overflow.describe()}）")
        )
    for item in doc["foreshadowing"]:
        if item.get("overdue") and str(item.get("window_policy") or "") == "HARD":
            issues.append(
                issue(
                    SEV_BLOCKER,
                    "RULES",
                    f"{MSG_FORESHADOW_OVERDUE}：{item['id']}（窗口终点 {item.get('window_end')}）",
                    [str(item.get("id") or "")],
                )
            )
        elif item.get("overdue"):
            issues.append(
                issue(
                    SEV_NON_BLOCKING,
                    "RULES",
                    f"伏笔 {item['id']} 已过窗口终点 {item.get('window_end')}，策略是 "
                    f"{item.get('window_policy') or '未写'}：只告警，但要在正文里交代",
                    [str(item.get("id") or "")],
                )
            )


def _merge_model_constraints(
    ctx: ToolContext,
    doc: dict,
    budget: Budget,
    issues: list[dict],
    llm: LLMClient | None,
    meter: Meter,
    base_release: str,
    task: dict | None,
    role: str,
) -> None:
    """让资料官把「必须发生／禁止发生」提炼成结构化约束。

    提炼失败不影响出包：原文照录的约束已经在 `key_constraints` 里了，
    模型这一路只是加分项。这条降级路径**必须显式记一条 issue**，
    否则「这一包是模型提炼的」和「这一包是原文照抄的」在产物里长得一样。
    """
    if llm is None:
        return
    entity_ids = list(doc["coverage"]["checked_entities"] or [])
    instructions = (
        "任务：为下面的章节整理「写前约束清单」，供主笔开工。\n"
        f"- 章节：{doc['chapter_id']}\n"
        f"- 故事锚点：{doc['story_anchor'].get('story_time') or '（未给）'}\n"
        f"- 参与实体：{', '.join(entity_ids) or '（未解析出）'}\n"
        "- 只输出信封 JSON，result 形如："
        '{"key_constraints": [{"text": "…", "kind": "MUST|MUST_NOT|RULE|SCOPE"}]}\n'
        "- 每条都要能在规划或档案里指到出处；没有出处的不要写。\n"
        "- 不要改写原文语义，不要补全规划里没写的约束。"
    )
    run = runner.run(
        llm,
        role,
        instructions=instructions,
        task=task,
        release=base_release,
        anchors={
            "chapter_id": doc["chapter_id"],
            "story_time": str(doc["story_anchor"].get("story_time") or ""),
        },
        meter=meter,
    )
    if not run.ok:
        issues.append(
            issue(
                SEV_NON_BLOCKING,
                "OTHER",
                "资料官输出不可用，关键约束保持规划原文照录：" + run.describe_errors(),
            )
        )
        doc["model"] = {"used": False, "role": role, "error": run.describe_errors()}
        return

    raw = run.result().get("key_constraints")
    if not isinstance(raw, list):
        issues.append(
            issue(
                SEV_NON_BLOCKING,
                "OTHER",
                "资料官输出里没有 key_constraints 数组，关键约束保持规划原文照录",
            )
        )
        doc["model"] = {"used": False, "role": role, "error": "result.key_constraints 不是数组"}
        return

    added: list[dict] = []
    skipped = 0
    for item in raw:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            added.append(
                {
                    "text": str(item["text"]).strip(),
                    "kind": str(item.get("kind") or "RULE"),
                    "source": f"model:{role}",
                }
            )
        else:
            skipped += 1
    if skipped:
        issues.append(
            issue(
                SEV_NON_BLOCKING,
                "OTHER",
                f"资料官给出的 {skipped} 条约束形状不对（缺 text），已跳过——跳过记在这里，不算静默丢弃",
            )
        )

    merged = list(doc["key_constraints"]) + added
    fitted = budget.place("key_constraints", merged, hard=True)
    doc["key_constraints"] = list(fitted.items)
    if fitted.overflow is not None:
        issues.append(
            issue(SEV_BLOCKER, "BUDGET", f"{MSG_BUDGET_OVERFLOW}（{fitted.overflow.describe()}）")
        )
    doc["model"] = {
        "used": True,
        "role": role,
        "model": run.model,
        "added_constraints": len(added),
        "skipped": skipped,
    }


def _decide_gate(doc: dict, budget: Budget, issues: list[dict], gaps: list[dict]) -> None:
    """PASS／BLOCKED 只看阻断项。备注可以多，但「有条件通过」不存在（PRD §13.4）。"""
    if budget.hard_overflow and not any(item["category"] == "BUDGET" for item in issues):
        issues.append(issue(SEV_BLOCKER, "BUDGET", MSG_BUDGET_OVERFLOW))
    doc["gate_result"] = GATE_BLOCKED if any(
        item.get("severity") == SEV_BLOCKER for item in issues
    ) else GATE_PASS
    doc["coverage"]["unresolved_gaps"] = list(gaps)


def _finish(
    doc: dict, budget: Budget, meter: Meter, runtime: Path, write: bool
) -> PreflightResult:
    # 预算层丢掉的条目必须进 `coverage.truncated_results`，与查询层的 Top-K 截断
    # 汇成一册。只记在 `budget.truncated` 里等于没记：读资料包的人看的是 coverage，
    # 那里空着就会被读成「资料齐全」。
    merged: list[dict] = list(doc["coverage"].get("truncated_results") or [])
    seen = {(item.get("section"), item.get("dropped")) for item in merged}
    for item in budget.truncations:
        key = (item.section, item.dropped)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item.as_dict())
    doc["coverage"]["truncated_results"] = merged

    doc["budget"] = budget.report()
    doc["usage"] = meter.summary()
    doc["generated_at"] = utc_now()
    doc["schema_version"] = __version__

    # 内部中转字段不外泄：它们在 schema 里会变成「未知字段」。
    for key in ("_pending_entity_refs", "_resolved_entities", "_keyword_terms"):
        doc.pop(key, None)

    errors = schema_check(SCHEMA_NAME, doc)
    if errors:
        # 出了 schema 错就是自己的 bug。宁可直接炸，也不要写出一份
        # 「看着像资料包、字段却对不上」的文件——那会让下游静默读到空值。
        raise UsageError("Preflight 不符合 preflight.schema.json：" + "；".join(errors[:5]))

    path = None
    if write:
        target = preflight_dir(runtime) / f"{doc['preflight_id']}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(target, dump_yaml(doc))
        path = target
    return PreflightResult(doc=doc, path=path)


def load(runtime: Path, preflight_id: str) -> dict:
    path = preflight_dir(runtime) / f"{preflight_id}.yaml"
    if not path.exists():
        raise UsageError(f"没有这份资料包：{path}")
    import json

    import yaml  # type: ignore

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise UsageError(f"资料包不是映射：{path}")
    return data


def to_json(doc: dict) -> str:
    import json

    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True)
