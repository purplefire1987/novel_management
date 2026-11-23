"""时间线与认知记录的数据模型。

五类记录对应 PRD §9 的五种结构，都放在 **frontmatter** 里，一个档案一种记录：

| 记录 | `type` | ID 前缀 | PRD |
|---|---|---|---|
| 事件 | `event` | `EVT-` | §9.2 §9.3 |
| 客观断言 | `assertion` | `AST-` | §9.4 |
| 状态变化 | `state_change` | `CHANGE-` | §9.7 |
| 人物认知 | `knowledge` | `KNOW-` | §9.5 |
| 读者揭示 | `revelation` | `REV-` | §9.6 |

## 为什么断言和认知分两张表

`epistemic_type` 一列就能区分客观与主观（PRD §8.3 列了五种信息性质）。但那样
写查询时**只要漏一个 `where`，角色误判就会当成事实返回**——而验收第 3 条正是
「角色误判不污染客观状态」。所以物理分表：客观断言进 `assertions`，人物认知进
`knowledge`，两个查询函数读两个不同的表，写错也串不起来。

## 判定顺序：`type` 优先，无 `type` 才看 ID 前缀

与 `ids.py` 同一条规矩（frontmatter 是权威）。真实库里有复合 ID（`EVT-CH-0001-001`
这类），前缀匹配必须宽，否则它们连「是事件」都识别不出来。

## 缺关键字段不抛异常

一个事件档案缺 `id` 是模板或笔误，不该让整次重建失败（T03 的坏文件策略）。
缺字段的记录**不生成**，但把原因收集进 `warnings`，由构建器汇总打印——
静默丢记录比报错更难查。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 记录类型 → ID 前缀
TYPE_PREFIX = {
    "event": "EVT-",
    "assertion": "AST-",
    "state_change": "CHANGE-",
    "knowledge": "KNOW-",
    "revelation": "REV-",
}

# 记录类型 → `type` 字段的别名写法（历史档案里出现过不同叫法）
TYPE_ALIASES = {
    "event": "event",
    "events": "event",
    "assertion": "assertion",
    "assertions": "assertion",
    "state_change": "state_change",
    "statechange": "state_change",
    "state_changes": "state_change",
    "knowledge": "knowledge",
    "revelation": "revelation",
    "revelations": "revelation",
}

KNOWLEDGE_ATTITUDES = frozenset({"KNOWS", "BELIEVES", "SUSPECTS", "DOUBTS"})
REVELATION_MODES = frozenset({"EXPLICIT", "IMPLICIT", "INFERRED"})
STORY_PRECISIONS = frozenset({"EXACT", "EVENT", "RELATIVE", "UNKNOWN"})
REVIEW_STATUSES = frozenset({"CLEAR", "NEEDS_REVIEW", "DISPUTED"})

EDGE_PRECEDES = "PRECEDES"


@dataclass(frozen=True)
class Event:
    """一个故事事件。`narrated_in` 是它被叙述的位置，与故事顺序无关（倒叙的来源）。"""

    id: str
    project: str
    title: str = ""
    summary: str = ""
    story_time: str = ""
    precision: str = ""
    narrated_in: str = ""
    path: str = ""


@dataclass(frozen=True)
class Edge:
    """`from_id` 先于 `to_id`。"""

    from_id: str
    to_id: str
    project: str
    relation: str = EDGE_PRECEDES
    path: str = ""


@dataclass(frozen=True)
class Assertion:
    """客观断言。`epistemic_type` 只允许 OBJECTIVE_ASSERTION（其余进 knowledge）。"""

    id: str
    project: str
    subject_id: str
    predicate: str
    value: str
    epistemic_type: str = "OBJECTIVE_ASSERTION"
    review_status: str = ""
    valid_from_event: str = ""
    valid_until_event: str = ""
    path: str = ""


@dataclass(frozen=True)
class StateChange:
    """字段级状态变化，绑定一个生效事件。"""

    id: str
    project: str
    entity_id: str
    attribute: str
    to_value: str
    from_value: str = ""
    event_id: str = ""
    path: str = ""


@dataclass(frozen=True)
class Knowledge:
    """某个角色对某个命题的态度。`attitude` 决定它是「知道」还是「以为」。"""

    id: str
    project: str
    holder_id: str
    subject_id: str
    predicate: str
    value: str
    attitude: str = "BELIEVES"
    valid_from_event: str = ""
    valid_until_event: str = ""
    path: str = ""


@dataclass(frozen=True)
class Revelation:
    """读者在哪个叙述位置得知某命题。读者知道不代表人物知道（PRD §9.6）。"""

    id: str
    project: str
    proposition_ref: str
    chapter_id: str = ""
    scene_id: str = ""
    block_id: str = ""
    revelation_mode: str = ""
    path: str = ""


@dataclass
class Extracted:
    """一个档案提取出来的全部记录，加上没能提取的原因。"""

    events: list[Event] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    assertions: list[Assertion] = field(default_factory=list)
    state_changes: list[StateChange] = field(default_factory=list)
    knowledge: list[Knowledge] = field(default_factory=list)
    revelations: list[Revelation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            (
                self.events,
                self.edges,
                self.assertions,
                self.state_changes,
                self.knowledge,
                self.revelations,
            )
        )


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _name_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def detect_type(meta: dict) -> str:
    """判断这是哪类语义记录；不是语义记录返回空串。"""
    raw_type = _text(meta.get("type")).lower()
    if raw_type in TYPE_ALIASES:
        return TYPE_ALIASES[raw_type]

    # 没有可用的 type，才退到 ID 前缀。前缀匹配要宽：真实库里有
    # `EVT-CH-0001-001` 这种复合 ID，严格匹配会把它判成「不是事件」。
    raw_id = _text(meta.get("id")).upper()
    if "{{" in raw_id:  # 模板占位符
        return ""
    for kind, prefix in TYPE_PREFIX.items():
        if raw_id.startswith(prefix):
            return kind
    return ""


def _proposition(meta: dict) -> tuple[str, str, str]:
    """取命题三元组。兼容扁平写法与 PRD §9.5 的嵌套 `proposition:` 写法。"""
    nested = meta.get("proposition")
    if isinstance(nested, dict):
        return (
            _text(nested.get("subject_id")),
            _text(nested.get("predicate")),
            _text(nested.get("value")),
        )
    return (
        _text(meta.get("subject_id")),
        _text(meta.get("predicate")),
        _text(meta.get("value")),
    )


def _narrative_position(meta: dict) -> tuple[str, str, str]:
    """取叙述位置。兼容扁平与 PRD §9.6 的嵌套 `narrative_position:` 写法。"""
    nested = meta.get("narrative_position")
    if isinstance(nested, dict):
        return (
            _text(nested.get("chapter_id")),
            _text(nested.get("scene_id")),
            _text(nested.get("block_id")),
        )
    return (
        _text(meta.get("chapter_id")),
        _text(meta.get("scene_id")),
        _text(meta.get("block_id")),
    )


def _predecessors(meta: dict) -> list[str]:
    """前置事件。三种写法都收：`predecessors` / `after` / `before`。"""
    out: list[str] = []
    out += _name_list(meta.get("predecessors"))
    out += _name_list(meta.get("after"))
    # `before: [X]` 读作「本事件在 X 之前」，方向与其余两项相反，
    # 由调用方按顺序反转，不能混进同一个列表。
    return out


def _successors(meta: dict) -> list[str]:
    """`before: [X]` 表示 X 在本事件之后。"""
    return _name_list(meta.get("before")) + _name_list(meta.get("successors"))


def extract(meta: dict, *, path: str, project: str) -> Extracted:
    """从一个档案的 frontmatter 提取语义记录。

    `path` 与 `project` 只用于溯源，不参与判定。
    """
    result = Extracted()
    kind = detect_type(meta)
    if not kind:
        return result

    raw_id = _text(meta.get("id"))
    if not raw_id or "{{" in raw_id:
        result.warnings.append(f"{path}: type 是 {kind} 但没有可用 id，未入库")
        return result

    if kind == "event":
        result.events.append(
            Event(
                id=raw_id,
                project=project,
                title=_text(meta.get("title") or meta.get("name")),
                summary=_text(meta.get("summary") or meta.get("description")),
                # 故事时间原样存字符串。PRD §9.2 允许「相对锚点」「未知」，
                # 强行解析成 datetime 会把这类信息压平；也绝不拿 git 时间顶替。
                story_time=_text(meta.get("story_time")),
                precision=_text(meta.get("precision")).upper(),
                narrated_in=_text(meta.get("narrated_in") or meta.get("chapter_id")),
                path=path,
            )
        )
        for pred in _predecessors(meta):
            if pred == raw_id:
                result.warnings.append(f"{path}: 事件 {raw_id} 把自己写成前驱，已忽略")
                continue
            result.edges.append(Edge(from_id=pred, to_id=raw_id, project=project, path=path))
        for succ in _successors(meta):
            if succ == raw_id:
                result.warnings.append(f"{path}: 事件 {raw_id} 把自己写成后继，已忽略")
                continue
            result.edges.append(Edge(from_id=raw_id, to_id=succ, project=project, path=path))

    elif kind == "assertion":
        epistemic = _text(meta.get("epistemic_type")).upper() or "OBJECTIVE_ASSERTION"
        subject, predicate, value = _proposition(meta)
        if not subject or not predicate:
            result.warnings.append(f"{path}: 断言 {raw_id} 缺 subject_id 或 predicate，未入库")
            return result
        if epistemic != "OBJECTIVE_ASSERTION":
            # 非客观的断言本质是某个人物的认知，转进 knowledge 表，
            # 免得它留在 assertions 里被状态查询当成事实。
            holder = _text(meta.get("holder_id"))
            if not holder:
                result.warnings.append(
                    f"{path}: 断言 {raw_id} 的 epistemic_type 是 {epistemic} 但没有 holder_id，未入库"
                )
                return result
            result.knowledge.append(
                Knowledge(
                    id=raw_id,
                    project=project,
                    holder_id=holder,
                    subject_id=subject,
                    predicate=predicate,
                    value=value,
                    attitude=_text(meta.get("attitude")).upper() or "BELIEVES",
                    valid_from_event=_text(meta.get("valid_from_event")),
                    valid_until_event=_text(meta.get("valid_until_event")),
                    path=path,
                )
            )
            return result
        result.assertions.append(
            Assertion(
                id=raw_id,
                project=project,
                subject_id=subject,
                predicate=predicate,
                value=value,
                epistemic_type=epistemic,
                review_status=_text(meta.get("review_status")).upper(),
                valid_from_event=_text(meta.get("valid_from_event")),
                valid_until_event=_text(meta.get("valid_until_event")),
                path=path,
            )
        )

    elif kind == "state_change":
        entity = _text(meta.get("entity_id"))
        attribute = _text(meta.get("attribute") or meta.get("field"))
        if not entity or not attribute:
            result.warnings.append(f"{path}: 状态变化 {raw_id} 缺 entity_id 或 attribute，未入库")
            return result
        result.state_changes.append(
            StateChange(
                id=raw_id,
                project=project,
                entity_id=entity,
                attribute=attribute,
                from_value=_text(meta.get("from_value") or meta.get("old_value")),
                to_value=_text(meta.get("to_value") or meta.get("new_value")),
                event_id=_text(meta.get("event_id") or meta.get("effective_event")),
                path=path,
            )
        )

    elif kind == "knowledge":
        holder = _text(meta.get("holder_id"))
        subject, predicate, value = _proposition(meta)
        if not holder or not subject or not predicate:
            result.warnings.append(
                f"{path}: 认知 {raw_id} 缺 holder_id / subject_id / predicate，未入库"
            )
            return result
        result.knowledge.append(
            Knowledge(
                id=raw_id,
                project=project,
                holder_id=holder,
                subject_id=subject,
                predicate=predicate,
                value=value,
                attitude=_text(meta.get("attitude")).upper() or "BELIEVES",
                valid_from_event=_text(meta.get("valid_from_event")),
                valid_until_event=_text(meta.get("valid_until_event")),
                path=path,
            )
        )

    elif kind == "revelation":
        prop = _text(meta.get("proposition_ref"))
        if not prop:
            result.warnings.append(f"{path}: 揭示 {raw_id} 缺 proposition_ref，未入库")
            return result
        chapter, scene, block = _narrative_position(meta)
        result.revelations.append(
            Revelation(
                id=raw_id,
                project=project,
                proposition_ref=prop,
                chapter_id=chapter,
                scene_id=scene,
                block_id=block,
                revelation_mode=_text(meta.get("revelation_mode")).upper(),
                path=path,
            )
        )

    return result
