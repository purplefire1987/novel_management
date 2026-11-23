"""状态区间与人物认知的查询语义。

PRD §16.3 规定返回码，五个值各有明确含义，**不许合并**：

| 返回 | 含义 | 常见错法 |
|---|---|---|
| `RESOLVED` | 唯一确定 | —— |
| `NOT_RECORDED` | 没有记录 | 说成「不存在」「不知道」 |
| `AMBIGUOUS` | 有记录但结论不唯一 | 随便挑一条 |
| `UNORDERED` | 涉及的事件不可比较 | 按 ID 或章节顺序硬排 |
| `DISPUTED` | 记录自身被标为争议 | 直接采信 |

## 三条不能踩的线

**一、`NOT_RECORDED` ≠ 「没有」。** 查不到认知记录时，人物可能知道也可能不知道，
系统只知道「没记」。PRD §9.5 专门写了这一条。

**二、`UNORDERED` 时不许挑「最新一条」。** PRD §16.3 原文：不得在 AMBIGUOUS 或
UNORDERED 时随意返回"最新一条"。所以 `latest_among()` 返回的是一**批**候选，
不是一条；多于一條就说明排序不成立。

**三、客观状态查询不读 `knowledge` 表。** 这不是「记得加过滤条件」，是两张表
物理隔开：`state_answer()` 里根本不存在对 knowledge 的 select。
验收第 3 条「角色误判不污染客观状态」靠的就是这个。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .graph import COMPARE_BEFORE, COMPARE_SAME, COMPARE_UNORDERED, EventGraph

RESOLVED = "RESOLVED"
NOT_RECORDED = "NOT_RECORDED"
AMBIGUOUS = "AMBIGUOUS"
UNORDERED = "UNORDERED"
DISPUTED = "DISPUTED"

MODE_AFTER = "AFTER"
MODE_BEFORE = "BEFORE"


@dataclass(frozen=True)
class Record:
    """一条候选记录在查询里的中间形态。"""

    id: str
    value: str
    event: str
    path: str
    kind: str
    review_status: str = ""


@dataclass(frozen=True)
class Answer:
    status: str
    subject: str
    predicate: str = ""
    value: str = ""
    records: tuple[Record, ...] = ()
    note: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == RESOLVED


def _load_graph(conn, project: str) -> EventGraph:
    rows = conn.execute(
        "select id from events where project = ? or ? = ''", (project, project)
    ).fetchall()
    ids = [row[0] for row in rows]
    edges = conn.execute(
        "select from_id, to_id from event_edges where project = ? or ? = ''",
        (project, project),
    ).fetchall()
    return EventGraph.from_edges(ids, [(r[0], r[1]) for r in edges])


def _project_clause(project: str) -> tuple[str, tuple]:
    """`project` 可能是目录名（`p1_project`）也可能是 project_id。两种都认。"""
    if not project:
        return "", ()
    return " and (project = ? or project like ? || '\\_%' escape '\\')", (project, project)


def _anchor_relation(
    graph: EventGraph, event: str, anchor: str, mode: str
) -> str:
    """返回 `SAME` / `IN_RANGE` / `OUT_OF_RANGE` / `UNORDERED` / `UNKNOWN_EVENT`。

    区间语义按 PRD §9.3：`[start, end)` —— 开始事件完成即生效。
    `--after X` 读作「X 发生后」，X 本身算在内（SAME 命中）；
    `--before X` 读作「X 发生前」，X 本身不算（SAME 落在区间外）。
    两种方向对「早于 X 的生效记录」都算在区间内——状态是累积的，
    查某时点的值要把之前所有生效记录一起拿来排序，不能只留锚点那一条。
    """
    if not anchor:
        return "IN_RANGE"
    if not event:
        # 没有绑定事件的记录（例如「人物天生就活着」）视为恒成立。
        return "IN_RANGE"

    known = set(graph.nodes)
    if event not in known or anchor not in known:
        return "UNKNOWN_EVENT"

    relation = graph.compare(event, anchor)
    if relation == COMPARE_SAME:
        return "SAME" if mode == MODE_AFTER else "OUT_OF_RANGE"
    if relation == COMPARE_UNORDERED:
        return "UNORDERED"
    return "IN_RANGE" if relation == COMPARE_BEFORE else "OUT_OF_RANGE"


def _edge_hint(graph: EventGraph) -> str:
    """图里一条边都没有时说清楚：是没人记先后，不是没有事件。

    真库当前正是这个状态（7 个事件、0 条边）。不解释的话，`UNORDERED` 会被读成
    「系统坏了」或者「这两个事件确实同时发生」，两种误读都会导致错误补写正文。
    """
    if graph.edges:
        return ""
    return (
        f"（事件图有 {len(graph.nodes)} 个事件、0 条先后关系——是还没记录谁先谁后，"
        "不是没有事件；补上 event_edges 后即可判定。）"
    )


def _expired(graph: EventGraph, until: str, anchor: str, mode: str) -> bool:
    """`[since, until)` 的上界：锚点是否已经走到 `until` 上。

    `--after X`：锚点就是 X，`until <= X` 即失效（`until` 本身不进区间）。
    `--before X`：锚点落在 X 之前，只有 `until < X` 才算失效。
    """
    if not until or not anchor:
        return False
    relation = _anchor_relation(graph, until, anchor, MODE_AFTER)
    if relation == "SAME":
        return mode == MODE_AFTER
    return relation == "IN_RANGE"


# 汇总多个字段时的严重度排序：一个问题字段就能让整次查询「不是确定答案」。
_STATUS_RANK = {UNORDERED: 4, AMBIGUOUS: 3, DISPUTED: 2, RESOLVED: 1, NOT_RECORDED: 0}


def state_answer(
    conn,
    *,
    entity_id: str,
    attribute: str = "",
    anchor: str = "",
    mode: str = MODE_AFTER,
    project: str = "",
) -> Answer:
    """查某实体在某事件（前/后）的客观字段值。

    **只读 `assertions` 与 `state_changes`。** 人物认知在 `knowledge` 表，
    这个函数不碰它——角色以为的事不是事实。

    不给 `attribute` 时返回**整个实体的快照**（每个字段各自判定）。不能把不同
    字段的记录扔进同一个池子挑「最晚」——`location=LOC-0001` 与 `life_status=ALIVE`
    本来就不该比较，比出来的「最晚」是假的。
    """
    if attribute:
        graph = _load_graph(conn, project)
        return _state_one(
            conn,
            entity_id=entity_id,
            attribute=attribute,
            anchor=anchor,
            mode=mode,
            project=project,
            graph=graph,
        )

    predicates = _entity_predicates(conn, entity_id, project)
    if not predicates:
        return Answer(
            status=NOT_RECORDED,
            subject=entity_id,
            note="没有客观记录。这不等于「不存在」或「未知」——只是索引里没有这一条。",
        )

    graph = _load_graph(conn, project)
    answers = [
        _state_one(
            conn,
            entity_id=entity_id,
            attribute=predicate,
            anchor=anchor,
            mode=mode,
            project=project,
            graph=graph,
        )
        for predicate in predicates
    ]
    return _combine(entity_id, anchor, answers)


def _entity_predicates(conn, entity_id: str, project: str) -> list[str]:
    """实体有哪些字段有客观记录（断言字段名 ∪ 状态变化字段名）。"""
    proj_sql, proj_args = _project_clause(project)
    rows = conn.execute(
        "select predicate from assertions where subject_id = ?" + proj_sql,
        (entity_id, *proj_args),
    ).fetchall()
    changes = conn.execute(
        "select attribute from state_changes where entity_id = ?" + proj_sql,
        (entity_id, *proj_args),
    ).fetchall()
    return sorted({str(r[0]) for r in rows + changes if r[0]})


def _combine(entity_id: str, anchor: str, answers: list[Answer]) -> Answer:
    """把每字段的结果合并成一个快照答案。

    只要有任一字段不是 `RESOLVED`，整次查询就不是确定答案——取最严重的那一个
    状态，但把每个字段各自的结论都留在 `fields` 里，不丢掉能确定的部分。
    """
    fields = [
        {
            "predicate": a.predicate,
            "status": a.status,
            "value": a.value,
            "event": a.extra.get("event", ""),
            "record": a.extra.get("record", ""),
            "path": a.extra.get("path", ""),
            "note": a.note,
        }
        for a in answers
    ]
    worst = max(answers, key=lambda a: _STATUS_RANK.get(a.status, 0))
    records: list[Record] = []
    for answer in answers:
        records.extend(answer.records)

    if worst.status == RESOLVED:
        value = "，".join(f"{a.predicate}={a.value}" for a in answers)
        return Answer(
            status=RESOLVED,
            subject=entity_id,
            value=value,
            records=tuple(records),
            extra={"fields": fields, "path": worst.extra.get("path", "")},
        )

    trouble = [a.predicate for a in answers if a.status != RESOLVED]
    return Answer(
        status=worst.status,
        subject=entity_id,
        value=worst.value,
        records=tuple(records),
        note=f"字段 {'、'.join(trouble)} 无法给出确定值（{worst.status}）。"
        + (f" 参照 {anchor}。" if anchor else ""),
        extra={"fields": fields, "path": worst.extra.get("path", "")},
    )


def _state_one(
    conn,
    *,
    entity_id: str,
    attribute: str,
    anchor: str,
    mode: str,
    project: str,
    graph: EventGraph,
) -> Answer:
    """单个字段的判定。`state_answer` 与快照查询共用这一段。"""
    proj_sql, proj_args = _project_clause(project)

    attr_sql = ""
    attr_args: tuple = ()
    if attribute:
        attr_sql = " and attribute = ?"
        attr_args = (attribute,)

    changes = conn.execute(
        "select id, entity_id, attribute, to_value, event_id, path"
        " from state_changes where entity_id = ?" + attr_sql + proj_sql,
        (entity_id, *attr_args, *proj_args),
    ).fetchall()

    pred_sql = ""
    pred_args: tuple = ()
    if attribute:
        pred_sql = " and predicate = ?"
        pred_args = (attribute,)

    assert_sql = (
        "select id, subject_id, predicate, value, valid_from_event, valid_until_event,"
        " review_status, path"
        " from assertions where subject_id = ?" + pred_sql + proj_sql
    )
    asserts = conn.execute(assert_sql, (entity_id, *pred_args, *proj_args)).fetchall()

    candidates: list[Record] = []
    for cid, _eid, attr, value, event, path in changes:
        candidates.append(
            Record(id=cid, value=str(value), event=str(event or ""), path=path, kind="change")
        )
    for aid, _sid, pred, value, since, until, review, path in asserts:
        # 区间是 `[since, until)`：锚点走到 until 上，这条就不该再算。
        if _expired(graph, str(until or ""), anchor, mode):
            continue
        candidates.append(
            Record(
                id=aid,
                value=str(value),
                event=str(since or ""),
                path=path,
                kind="assertion",
                review_status=str(review or ""),
            )
        )

    if not candidates:
        return Answer(
            status=NOT_RECORDED,
            subject=entity_id,
            predicate=attribute,
            note="没有客观记录。这不等于「不存在」或「未知」——只是索引里没有这一条。",
        )

    disputed = [r for r in candidates if r.review_status == DISPUTED]

    if graph.cycle():
        return Answer(
            status=UNORDERED,
            subject=entity_id,
            predicate=attribute,
            records=tuple(candidates),
            note="事件图有环，先后关系不成立，拒绝排序：" + " → ".join(graph.cycle()),
        )

    in_range: list[Record] = []
    unordered: list[Record] = []
    unknown: list[Record] = []
    for record in candidates:
        relation = _anchor_relation(graph, record.event, anchor, mode)
        if relation in ("IN_RANGE", "SAME"):
            in_range.append(record)
        elif relation == "UNORDERED":
            unordered.append(record)
        elif relation == "UNKNOWN_EVENT":
            unknown.append(record)
        # OUT_OF_RANGE 直接丢弃：那条在这个时点还没生效／已失效

    if not in_range and not unordered and not unknown:
        return Answer(
            status=NOT_RECORDED,
            subject=entity_id,
            predicate=attribute,
            note=f"在 {anchor} 这个时点上没有生效的记录（有的记录都在区间之外）。",
        )

    if unordered:
        return Answer(
            status=UNORDERED,
            subject=entity_id,
            predicate=attribute,
            records=tuple(unordered),
            note=(
                f"有 {len(unordered)} 条记录生效于与 {anchor or '参照事件'} 不可比较的事件，"
                "不能判定哪条更晚。按 PRD §16.3 不返回「最新一条」。"
                + _edge_hint(graph)
            ),
        )

    if unknown:
        return Answer(
            status=UNORDERED,
            subject=entity_id,
            predicate=attribute,
            records=tuple(unknown),
            note="有记录绑定了索引里不存在的事件（"
            + "、".join(sorted({r.event for r in unknown if r.event}))
            + "），无法确定生效顺序。"
            + _edge_hint(graph),
        )

    # 没有绑定事件的记录读作「从一开始就成立」。它不是「最晚」的候选：
    # 只要存在任何绑定了事件的同类记录，基线就一定更早。把基线混进
    # `latest_among` 会让它与每个事件都「不可比」，凭空造出 UNORDERED。
    dated = [r for r in in_range if r.event]
    pool = dated or in_range

    latest = graph.latest_among(r.event for r in pool)
    top = [r for r in pool if r.event in latest] if latest else pool

    if len({r.value for r in top}) > 1:
        distinct_events = {r.event for r in top if r.event}
        if len(distinct_events) > 1:
            return Answer(
                status=UNORDERED,
                subject=entity_id,
                predicate=attribute,
                records=tuple(top),
                note=(
                    f"最晚的 {len(distinct_events)} 条记录分别生效于 "
                    + "、".join(sorted(distinct_events))
                    + "，这些事件互不可比，不能判定哪条更晚。"
                    "按 PRD §16.3 不返回「最新一条」。"
                ),
            )
        return Answer(
            status=AMBIGUOUS,
            subject=entity_id,
            predicate=attribute,
            records=tuple(top),
            note="同一时点上有多条记录给出不同值，记录本身互相矛盾，不挑其中一条。",
        )

    if disputed:
        return Answer(
            status=DISPUTED,
            subject=entity_id,
            predicate=attribute,
            value=top[0].value,
            records=tuple(candidates),
            note="记录被标为 DISPUTED，值仅供参考。",
        )

    chosen = top[0]
    return Answer(
        status=RESOLVED,
        subject=entity_id,
        predicate=attribute,
        value=chosen.value,
        records=tuple(candidates),
        extra={"event": chosen.event, "path": chosen.path, "record": chosen.id},
    )


def knowledge_answer(
    conn,
    *,
    holder_id: str,
    subject_id: str = "",
    predicate: str = "",
    anchor: str = "",
    mode: str = MODE_AFTER,
    project: str = "",
) -> Answer:
    """查某角色在某时点对某命题的态度。

    只读 `knowledge` 表。查不到返回 `NOT_RECORDED`——**不能解释成「不知道」**，
    也不能拿客观断言去倒推人物知道什么。
    """
    proj_sql, proj_args = _project_clause(project)

    sql = (
        "select id, holder_id, subject_id, predicate, value, attitude,"
        " valid_from_event, valid_until_event, path from knowledge where holder_id = ?"
    )
    args: list = [holder_id]
    if subject_id:
        sql += " and subject_id = ?"
        args.append(subject_id)
    if predicate:
        sql += " and predicate = ?"
        args.append(predicate)
    sql += proj_sql

    rows = conn.execute(sql, (*args, *proj_args)).fetchall()
    if not rows:
        return Answer(
            status=NOT_RECORDED,
            subject=holder_id,
            predicate=predicate,
            note="没有该角色对这条命题的认知记录。"
            "「查不到」不等于「不知道」——可能只是没记（PRD §9.5）。",
        )

    graph = _load_graph(conn, project)
    if graph.cycle():
        return Answer(
            status=UNORDERED,
            subject=holder_id,
            predicate=predicate,
            note="事件图有环，认知的有效区间无法判定：" + " → ".join(graph.cycle()),
        )

    valid: list[Record] = []
    unordered: list[Record] = []
    for rid, _h, sid, pred, value, attitude, since, until, path in rows:
        record = Record(id=rid, value=f"{attitude}:{value}", event=str(since or ""), path=path, kind="knowledge")
        since_rel = _anchor_relation(graph, str(since or ""), anchor, mode)

        if since_rel == "UNORDERED":
            unordered.append(record)
            continue
        if since_rel in ("IN_RANGE", "SAME") and not _expired(graph, str(until or ""), anchor, mode):
            valid.append(record)
        # 其它情况：这条认知在该时点还没形成或已失效

    if unordered:
        return Answer(
            status=UNORDERED,
            subject=holder_id,
            predicate=predicate,
            records=tuple(unordered),
            note=f"认知记录的有效区间与 {anchor or '参照事件'} 不可比较，不能判定。",
        )

    if not valid:
        return Answer(
            status=NOT_RECORDED,
            subject=holder_id,
            predicate=predicate,
            note=f"在 {anchor} 这个时点上没有生效的认知记录（记录都在区间之外）。",
        )

    dated = [r for r in valid if r.event]
    pool = dated or valid
    latest = graph.latest_among(r.event for r in pool)
    top = [r for r in pool if r.event in latest] if latest else pool
    if len({r.value for r in top}) > 1:
        distinct_events = {r.event for r in top if r.event}
        status = UNORDERED if len(distinct_events) > 1 else AMBIGUOUS
        return Answer(
            status=status,
            subject=holder_id,
            predicate=predicate,
            records=tuple(top),
            note=(
                "最晚的几条认知记录分别生效于互不可比的事件，不能判定哪条更晚。"
                if status == UNORDERED
                else "同一时点上有多条认知记录给出不同结论，记录本身互相矛盾。"
            ),
        )

    return Answer(
        status=RESOLVED,
        subject=holder_id,
        predicate=predicate,
        value=top[0].value,
        records=tuple(valid),
        extra={"event": top[0].event, "path": top[0].path, "record": top[0].id},
    )


def _position(chapter: str, scene: str, block: str) -> str:
    """叙述位置的可读写法。缺哪段就省哪段，不拿 `?` 占位糊成 `CH/?#p0032`。"""
    parts = [p for p in (chapter, scene, block) if p]
    return " · ".join(parts) if parts else "（未标位置）"


def revelation_answer(conn, *, proposition_ref: str, project: str = "") -> Answer:
    """查某命题在哪些叙述位置被揭示给读者。

    「读者知道」与「人物知道」是两件事（PRD §9.6）：这里只回答读者一侧，
    不推断任何角色的认知。
    """
    proj_sql, proj_args = _project_clause(project)
    rows = conn.execute(
        "select id, chapter_id, scene_id, block_id, revelation_mode, path"
        " from revelations where proposition_ref = ?" + proj_sql,
        (proposition_ref, *proj_args),
    ).fetchall()
    if not rows:
        return Answer(
            status=NOT_RECORDED,
            subject=proposition_ref,
            note="没有该命题的读者揭示记录。读者是否知道，系统只认显式记录。",
        )
    records = tuple(
        Record(
            id=rid,
            value=_position(chapter, scene, block),
            event="",
            path=path,
            kind=f"revelation:{mode or 'UNSPECIFIED'}",
        )
        for rid, chapter, scene, block, mode, path in rows
    )
    return Answer(
        status=RESOLVED,
        subject=proposition_ref,
        value="、".join(dict.fromkeys(r.value for r in records)),
        records=records,
    )
