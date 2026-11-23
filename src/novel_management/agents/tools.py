"""类型化工具：Agent 能调什么，由权限矩阵**推出来**，不是另写一张清单。

为什么要有这一层，而不是让提示词里写「你可以用 search」：

- 提示词是给模型看的，模型可以不听。工具网关是执行层，不听就直接报错。
  PRD §6.4 最后一句——「实际安全边界由执行层实现，不依赖提示词」。
- 参数的形状要定死。同一个 `search`，模型给 `{"q": ...}` 还是 `{"query": ...}`
  都得有人挡一下，否则错的是「查不到」而不是「参数错」，看起来像库是空的。

能力位（capability）与 `agents/common/permissions.md` 的五列一一对应：

| 能力 | 来自哪一列 |
|---|---|
| `READ_CANON` | Canon 读取（`R`／`R（限授权）`） |
| `READ_SOURCE` | Canon 读取里的「源稿」那一格 |
| `WRITE_STAGING` | staging 写入 |
| `PROPOSE` | staging 写入里的 `P` |
| `WRITE_DRAFT` | 正文草稿 |
| `PUBLISH` | 正式发布/批准（九角色全是 X） |

于是 `allowed_tools(role)` 不用手维护：角色有什么能力，就自动拿到该能力的工具。
`ingestor` 拿不到任何 Canon 读工具，不是因为它被单独点名，而是因为它的读取范围
只有源稿——这才是「由矩阵推导」和「另抄一份清单」的区别，前者改矩阵就自动跟着变，
后者一定会走样。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..infrastructure.filesystem import markdown as md
from ..domain.errors import ConflictError, SecurityError, UsageError
from ..infrastructure.database.indexing import locate as locate_mod
from ..infrastructure.database.indexing.search import connect as index_connect
from ..infrastructure.database.indexing.search import index_metadata as index_metadata_of
from ..infrastructure.database.indexing.search import search as index_search
from ..infrastructure.filesystem.paths import ProjectPaths
from novel_management.domain.timeline import query as timeline_query
from novel_management.domain.timeline.query import MODE_AFTER, MODE_BEFORE
from . import registry
from .registry import (
    SCOPE_ALL,
    SCOPE_AUTHORIZED,
    SCOPE_SOURCE,
    Role,
)

# ---- 能力位 ----
CAP_READ_CANON = "READ_CANON"
CAP_READ_SOURCE = "READ_SOURCE"
CAP_WRITE_STAGING = "WRITE_STAGING"
CAP_PROPOSE = "PROPOSE"
CAP_WRITE_DRAFT = "WRITE_DRAFT"
CAP_PUBLISH = "PUBLISH"

# 索引是派生的（ADR-003），但它不是「随时等价」。旧版建的表缺列时，
# 唯一诚实的选择是把「这一项没查」说出来，而不是返回 0 条装作查过。
NOTE_FORESHADOW_STALE = (
    "索引里读不到伏笔数据（建库版本较旧，或缺 foreshadowing 表）："
    "这一项检查等于没做，先 novel rebuild-index 再跑 preflight"
)


def capabilities(role: Role) -> frozenset[str]:
    """角色当前具备的能力，全部从权限矩阵推。"""
    caps: set[str] = set()
    if role.read_scope in (SCOPE_ALL, SCOPE_AUTHORIZED):
        caps.add(CAP_READ_CANON)
    if role.read_scope == SCOPE_SOURCE:
        caps.add(CAP_READ_SOURCE)
    if role.staging_write:
        caps.add(CAP_WRITE_STAGING)
    if role.may_propose:
        caps.add(CAP_PROPOSE)
    if role.draft_write:
        caps.add(CAP_WRITE_DRAFT)
    if role.may_release:
        caps.add(CAP_PUBLISH)
    return frozenset(caps)


# ---- 工具声明 ----
@dataclass(frozen=True)
class Param:
    name: str
    type: str
    required: bool = False
    doc: str = ""


@dataclass(frozen=True)
class Tool:
    name: str
    capability: str
    summary: str
    params: tuple[Param, ...]
    run: Callable[["ToolContext", dict], "ToolResult"]
    needs_index: bool = False
    # 只有这些角色能用。留空表示「有该能力的角色都能用」。
    only_roles: tuple[str, ...] = ()

    def signature(self) -> str:
        parts = []
        for param in self.params:
            mark = "" if param.required else "?"
            parts.append(f"{param.name}:{param.type}{mark}")
        return f"{self.name}({', '.join(parts)})"

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "capability": self.capability,
            "summary": self.summary,
            "signature": self.signature(),
            "params": [
                {"name": p.name, "type": p.type, "required": p.required, "doc": p.doc}
                for p in self.params
            ],
            "only_roles": list(self.only_roles),
        }


@dataclass(frozen=True)
class ToolResult:
    """一次工具调用的结果。

    `truncated` 不是装饰：只要结果被 `limit` 截过就置真，并且 `note` 里写明
    「命中多少、给了多少」。PRD §13.2 那句「一次 Top-K 返回不得视为完整知识」
    靠这个字段落地——**没标 truncated 的结果才可以当成「就这么多」**。
    """

    tool: str
    payload: dict
    truncated: bool = False
    note: str = ""
    tokens: int = 0


@dataclass
class ToolContext:
    pp: ProjectPaths
    runtime: Path
    project: str = ""
    # 计量挂在这里而不是让每个调用点自己记：漏记一次，账就对不上，
    # 而「对不上的账」比「没有账」更容易让人得出错误结论。
    meter: Any = None
    _conn: sqlite3.Connection | None = field(default=None, repr=False)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = index_connect(locate_mod.active_db(self.runtime))
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "ToolContext":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _project_arg(ctx: ToolContext) -> str:
    return ctx.project or ctx.pp.project_id


def project_clause(alias: str, project: str) -> tuple[str, tuple]:
    """项目过滤条件。

    索引里的 `project` 是目录名（`novel-001_smoke`），CLI 与 `ProjectPaths`
    给的是 project_id（`novel-001`）。只做等值比较会**静默返回空**——
    「伏笔一条都没有」和「过滤条件没对上」在结果里长得一模一样。
    所以两种都认，与 `indexing.search._project_clause` 保持同一口径。
    """
    if not project:
        return "", ()
    return f" and ({alias}.project = ? or {alias}.project like ? || '\\_%' escape '\\')", (
        project,
        project,
    )


# ---- 各工具的实现 ----
def _run_search(ctx: ToolContext, argv: dict) -> ToolResult:
    query = str(argv.get("query") or "").strip()
    limit = int(argv.get("limit") or 20)
    result = index_search(ctx.conn, query, limit=limit, project=_project_arg(ctx))
    total = result.total
    dropped = max(total - len(result.hits), 0)
    note = result.note
    if dropped:
        note = (note + "；" if note else "") + f"命中 {total} 条，本次只取前 {len(result.hits)} 条"
    return ToolResult(
        tool="search",
        payload={
            "query": result.query,
            "mode": result.mode,
            "total": total,
            "hits": [
                {
                    "path": hit.path,
                    "artifact_id": hit.artifact_id,
                    "block_id": hit.block_id,
                    "line_start": hit.line_start,
                    "snippet": hit.snippet,
                }
                for hit in result.hits
            ],
            "note": note,
        },
        truncated=bool(dropped),
        note=note,
    )


def _anchor_mode(argv: dict) -> str:
    return MODE_BEFORE if argv.get("before") else MODE_AFTER


def _run_state(ctx: ToolContext, argv: dict) -> ToolResult:
    answer = timeline_query.state_answer(
        ctx.conn,
        entity_id=str(argv["entity"]),
        attribute=str(argv.get("field") or ""),
        anchor=str(argv.get("anchor") or ""),
        mode=_anchor_mode(argv),
        project=_project_arg(ctx),
    )
    return ToolResult(
        tool="state",
        payload={
            "status": answer.status,
            "subject": answer.subject,
            "predicate": answer.predicate,
            "value": answer.value,
            "note": answer.note,
            "records": [
                {"id": rec.id, "value": rec.value, "event": rec.event, "path": rec.path}
                for rec in answer.records
            ],
        },
        truncated=False,
        note=answer.note,
    )


def _run_knowledge(ctx: ToolContext, argv: dict) -> ToolResult:
    answer = timeline_query.knowledge_answer(
        ctx.conn,
        holder_id=str(argv["holder"]),
        subject_id=str(argv.get("subject") or ""),
        predicate=str(argv.get("predicate") or ""),
        anchor=str(argv.get("anchor") or ""),
        mode=_anchor_mode(argv),
        project=_project_arg(ctx),
    )
    return ToolResult(
        tool="knowledge",
        payload={
            "status": answer.status,
            "subject": answer.subject,
            "predicate": answer.predicate,
            "value": answer.value,
            "note": answer.note,
            "records": [
                {"id": rec.id, "value": rec.value, "event": rec.event, "path": rec.path}
                for rec in answer.records
            ],
        },
        note=answer.note,
    )


def _run_reveal(ctx: ToolContext, argv: dict) -> ToolResult:
    answer = timeline_query.revelation_answer(
        ctx.conn, proposition_ref=str(argv["proposition"]), project=_project_arg(ctx)
    )
    return ToolResult(
        tool="reveal",
        payload={
            "status": answer.status,
            "subject": answer.subject,
            "note": answer.note,
            "records": [
                {"id": rec.id, "value": rec.value, "event": rec.event, "path": rec.path}
                for rec in answer.records
            ],
        },
        note=answer.note,
    )


def _find_planning_doc(pp: ProjectPaths, chapter_id: str) -> Path | None:
    """按 frontmatter 里的 id 找章节规划。

    走 frontmatter 而不是文件名：文件名是人起的，`id` 是发过号的。
    目录名也不写死（`02_Planning` 下面是 `Chapter_Outlines` 还是 `细纲`
    是内容组织，不是系统约定），递归找就是了。
    """
    root = pp.planning
    if not root.is_dir():
        return None
    hits: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        try:
            meta = md.parse(path.read_text(encoding="utf-8", errors="replace"), str(path)).meta
        except Exception:  # 坏文件不该让整个检索失败
            continue
        if str(meta.get("id") or "").strip() == chapter_id:
            hits.append(path)
    if len(hits) > 1:
        raise ConflictError(
            f"{chapter_id} 有多份规划：{', '.join(str(p) for p in hits)}；"
            "同一章只能有一份有效细纲，先合并再跑 preflight"
        )
    return hits[0] if hits else None


def _run_plan(ctx: ToolContext, argv: dict) -> ToolResult:
    chapter = str(argv["chapter"]).strip()
    path = _find_planning_doc(ctx.pp, chapter)
    if path is None:
        return ToolResult(
            tool="plan",
            payload={"chapter_id": chapter, "found": False, "path": "", "meta": {}, "body": ""},
            note=f"没有找到 {chapter} 的规划（按 frontmatter id 找遍 {ctx.pp.planning}）",
        )
    doc = md.parse(path.read_text(encoding="utf-8", errors="replace"), str(path))
    return ToolResult(
        tool="plan",
        payload={
            "chapter_id": chapter,
            "found": True,
            "path": ctx.pp.rel(path),
            "meta": doc.meta,
            "body": doc.body,
        },
    )


def _table_columns(conn, table: str) -> set[str]:
    """表不存在时返回空集，不抛。

    `pragma table_info` 对不存在的表返回 0 行而不是报错，正好用来判定
    「这张表在不在、有哪些列」，比 try/except 猜错误串稳。
    """
    try:
        rows = conn.execute(f"pragma table_info({table})").fetchall()
    except Exception:
        return set()
    return {str(row["name"]) for row in rows}


def _run_foreshadow(ctx: ToolContext, argv: dict) -> ToolResult:
    project = _project_arg(ctx)
    populated = index_metadata_of(ctx.conn).get("populated", "")
    supported = "foreshadowing" in populated.split(",")
    columns = _table_columns(ctx.conn, "foreshadowing")
    if not columns:
        return ToolResult(
            tool="foreshadow",
            payload={"supported": False, "count": 0, "items": [], "note": NOTE_FORESHADOW_STALE},
            note=NOTE_FORESHADOW_STALE,
        )

    # 旧版索引的 foreshadowing 表没有窗口三列。补空列而不是让整条命令崩掉：
    # 「这一项没查」要比「preflight 直接抛栈」有用得多，但必须让调用方看得见。
    wanted = ("id", "title", "status", "planted_in", "payoff_in", "window_policy", "window_end")
    select = ", ".join(
        name if name in columns else f"'' as {name}" for name in wanted
    )
    where, where_args = project_clause("foreshadowing", project)
    rows = ctx.conn.execute(
        f"select {select} from foreshadowing where 1 = 1" + where,
        where_args,
    ).fetchall()
    note = ""
    if not supported:
        note = NOTE_FORESHADOW_STALE
    return ToolResult(
        tool="foreshadow",
        payload={
            "supported": supported,
            "count": len(rows),
            "items": [
                {
                    "id": row["id"],
                    "title": row["title"],
                    "state": row["status"],
                    "planted_in": row["planted_in"],
                    "payoff_in": row["payoff_in"],
                    "window_policy": row["window_policy"],
                    "window_end": row["window_end"],
                }
                for row in rows
            ],
            "note": note,
        },
        note=note,
    )


def _run_index_meta(ctx: ToolContext, argv: dict) -> ToolResult:
    meta = index_metadata_of(ctx.conn)
    return ToolResult(
        tool="index_meta",
        payload={
            "build_id": meta.get("build_id", ""),
            "canon_revision": meta.get("canon_revision", ""),
            "fts_mode": meta.get("fts_mode", ""),
            "populated": meta.get("populated", ""),
            "artifact_count": meta.get("artifact_count", ""),
        },
    )


TOOLS: tuple[Tool, ...] = (
    Tool(
        "search",
        CAP_READ_CANON,
        "检索正文与实体名（中文短词走子串，长词走 trigram）",
        (
            Param("query", "str", True, "查询词；像 ID 的走精确匹配"),
            Param("limit", "int", False, "最多几条，默认 20"),
        ),
        _run_search,
        needs_index=True,
    ),
    Tool(
        "state",
        CAP_READ_CANON,
        "某实体在某时点的客观字段值（只读断言，不读人物认知）",
        (
            Param("entity", "str", True, "实体 ID"),
            Param("field", "str", False, "只看某个字段"),
            Param("anchor", "str", False, "参照事件 ID"),
            Param("before", "bool", False, "查参照事件之前；省略为之后"),
        ),
        _run_state,
        needs_index=True,
    ),
    Tool(
        "knowledge",
        CAP_READ_CANON,
        "某角色在某时点对某命题的态度（查不到不等于「不知道」）",
        (
            Param("holder", "str", True, "持有认知的角色 ID"),
            Param("subject", "str", False, "命题主体 ID"),
            Param("predicate", "str", False, "命题谓词"),
            Param("anchor", "str", False, "参照事件 ID"),
            Param("before", "bool", False, "查参照事件之前"),
        ),
        _run_knowledge,
        needs_index=True,
    ),
    Tool(
        "reveal",
        CAP_READ_CANON,
        "某命题在哪些叙述位置揭示给读者",
        (Param("proposition", "str", True, "命题 ID"),),
        _run_reveal,
        needs_index=True,
    ),
    Tool(
        "plan",
        CAP_READ_CANON,
        "读某章的已批准规划（按 frontmatter id 找，不看文件名）",
        (Param("chapter", "str", True, "章节 ID，如 CH-0001"),),
        _run_plan,
    ),
    Tool(
        "foreshadow",
        CAP_READ_CANON,
        "伏笔清单与回收窗口策略（HARD 窗口过期会阻断）",
        (),
        _run_foreshadow,
        needs_index=True,
    ),
    Tool(
        "index_meta",
        CAP_READ_CANON,
        "当前索引的构建号、revision、FTS 模式与本版填充了哪些表",
        (),
        _run_index_meta,
        needs_index=True,
    ),
)

_TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


def tool(name: str) -> Tool:
    found = _TOOLS_BY_NAME.get(name)
    if found is None:
        raise UsageError(f"未知工具：{name!r}（可选 {', '.join(_TOOLS_BY_NAME)}）")
    return found


def allowed_tools(role_id: str) -> tuple[Tool, ...]:
    """角色能用的工具，纯粹由权限矩阵推出。"""
    found = registry.role(role_id)
    caps = capabilities(found)
    return tuple(
        item
        for item in TOOLS
        if item.capability in caps
        and (not item.only_roles or found.id in item.only_roles)
    )


def allowed_tool_names(role_id: str) -> tuple[str, ...]:
    return tuple(item.name for item in allowed_tools(role_id))


def _check_params(item: Tool, argv: dict) -> dict:
    declared = {param.name: param for param in item.params}
    unknown = sorted(set(argv) - set(declared) - {"scope"})
    if unknown:
        raise UsageError(
            f"{item.name} 不认识参数 {', '.join(unknown)}；签名 {item.signature()}"
            "（不忽略多余参数：拼错一个键就变成「查不到」，看起来像库里没有）"
        )
    missing = [
        param.name for param in item.params if param.required and argv.get(param.name) in (None, "")
    ]
    if missing:
        raise UsageError(f"{item.name} 缺必填参数 {', '.join(missing)}；签名 {item.signature()}")
    return {key: value for key, value in argv.items() if key in declared}


def call(ctx: ToolContext, role_id: str, name: str, **argv: Any) -> ToolResult:
    """按角色调用工具。越权、拼错参数、越范围读，全都在这一层拦。"""
    role = registry.role(role_id)
    item = tool(name)

    if item not in allowed_tools(role_id):
        caps = ", ".join(sorted(capabilities(role))) or "无"
        raise SecurityError(
            f"{role.id}（{role.name}）不能调用 {name}：该工具需要 {item.capability}，"
            f"该角色具备的能力是 {caps}（矩阵见 agents/common/permissions.md）"
        )

    # 「R（限授权）」不是一句说明，是一条要执行的限制：没给范围就不许查。
    if role.read_scope == SCOPE_AUTHORIZED and not str(argv.get("scope") or "").strip():
        raise SecurityError(
            f"{role.id}（{role.name}）的读取限授权范围：调用 {name} 必须显式给 scope，"
            "否则等于全库可读"
        )

    params = _check_params(item, argv)
    try:
        result = item.run(ctx, params)
    except Exception:
        if ctx.meter is not None:
            ctx.meter.tool_call(role=role.id, name=name, ok=False)
        raise
    result = ToolResult(
        tool=result.tool,
        payload=result.payload,
        truncated=result.truncated,
        note=result.note,
        tokens=_result_tokens(result.payload),
    )
    if ctx.meter is not None:
        ctx.meter.tool_call(role=role.id, name=name, ok=True, tokens=result.tokens)
    return result


def _result_tokens(payload: dict) -> int:
    import json

    from .budget import estimate_tokens

    try:
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    return estimate_tokens(text)
