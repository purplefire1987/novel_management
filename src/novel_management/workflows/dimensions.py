"""十维审核清单（V1.0 §11）。

**为什么单独一个模块**：清单是**数据**，不是流程。它要能被三处用到——
`review submit` 收结论、`review status` 报覆盖率、`审核报告` 渲染成表格。
写在任何一个里面，另外两处就得抄一遍，抄三份的清单一定会有一份落后。

`machine_checkable` 这个字段是这套清单的关键。V1.0 §11 列了十项，
但机器能判的只有四类形状（正文为空、残留系统注释、混入多份文档、
出现禁止条目里被引号括起来的专名）。剩下六项——因果、人物、时间、
信息边界、读者体验、规划偏差——机器判不了。

**判不了就说判不了。** 给一个「看起来审过了」的绿灯，比留空更危险：
留空至少让人知道这格没人填，假绿灯会让人以为有人填过。所以每项都有
`NOT_CHECKED` 这个取值，而且它**不能**凑成 APPROVE。
"""

from __future__ import annotations

from dataclasses import dataclass

# 四级问题分级（V1.0 §11 原文顺序）。
#
# PREFERENCE 单列出来，是因为「风格建议伪装成事实错误」是审核里最常见的越权：
# 把「我觉得这段节奏慢」写成 BLOCKER，作者改也不是、不改也不是。
# 分级的机器含义：BLOCKER 拦批准；MAJOR 不拦但必须逐条答复；
# MINOR 记着；PREFERENCE 可以不改。
SEVERITIES: tuple[tuple[str, str], ...] = (
    ("BLOCKER", "阻断性错误"),
    ("MAJOR", "重要问题"),
    ("MINOR", "一般建议"),
    ("PREFERENCE", "主观偏好"),
)
SEVERITY_IDS = tuple(sid for sid, _ in SEVERITIES)
SEVERITY_LABELS = dict(SEVERITIES)

# 单项审核的取值。
# - PASS：查过，没问题
# - ISSUE：查过，有问题（问题本身另记在 findings 里，这里只标查过且有）
# - NOT_APPLICABLE：查过，本章不涉及（比如没有伏笔的章节，「伏笔」一项 N/A）
# - NOT_CHECKED：**没查**。它与 PASS 完全不同，不能互相顶替。
VERDICT_IDS = ("PASS", "ISSUE", "NOT_APPLICABLE", "NOT_CHECKED")


@dataclass(frozen=True)
class Dimension:
    id: str
    name: str
    question: str
    primary_owner: str
    machine_checkable: bool
    machine_scope: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "question": self.question,
            "primary_owner": self.primary_owner,
            "machine_checkable": self.machine_checkable,
            "machine_scope": self.machine_scope,
        }


# 十项，顺序与 V1.0 §11 原文一致（1 结构 … 10 规划偏差）。顺序是原文的一部分，
# 报告里换序会让「第 7 项」这类口头引用对不上。
REVIEW_DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        "structure",
        "结构审核",
        "章节目标是否达成？开头、推进、转折、结尾各自有效吗？",
        "editor",
        False,
        "只能判「有没有正文」；章目标是否达成要对着章卡读，机器判不了",
    ),
    Dimension(
        "causality",
        "因果审核",
        "每个行为都有动机吗？每个事件都有前因后果吗？",
        "editor",
        False,
        "判不了。因果链在章卡里是数据，正文里是语义，两者对齐需要读",
    ),
    Dimension(
        "character",
        "人物审核",
        "性格、能力、认知、关系、人物弧光是否一致？",
        "world_architect",
        False,
        "只能查「提到的实体是否都存在」；性格与能力边界要读档案",
    ),
    Dimension(
        "setting",
        "设定审核",
        "是否违反已确认的世界规则、力量体系、物品限制？",
        "world_architect",
        False,
        "能查禁止条目里被引号括起来的专名（`_quoted_terms`）；语义级违规判不了",
    ),
    Dimension(
        "timeline",
        "时间审核",
        "事件顺序、旅行耗时、年龄、冷却时间、持续时间是否合理？",
        "world_architect",
        False,
        "能用 `novel state` 查记录过的状态变化；没记录的部分只能报 UNORDERED",
    ),
    Dimension(
        "information",
        "信息审核",
        "角色是否提前知道了秘密？叙述视角是否越界？",
        "researcher",
        False,
        "能用 `novel knowledge`／`novel reveal` 查认知边界；查不到不等于没有越界",
    ),
    Dimension(
        "foreshadowing",
        "伏笔审核",
        "新增、推进、回收、失效的伏笔是否登记？",
        "editor",
        True,
        "能判「登记的伏笔是否都有出处」；「该登记却没登记」判不了",
    ),
    Dimension(
        "language",
        "语言审核",
        "语病、重复、节奏、视角、语气、风格。",
        "editor",
        True,
        "能判形状：残留系统注释、重复段落、混入多份文档",
    ),
    Dimension(
        "reader_experience",
        "读者体验",
        "信息密度、理解成本、悬念、情绪、章节吸引力。",
        "editor_in_chief",
        False,
        "判不了。这一项本来就是主观的，写进清单是为了让它显形，不是为了让机器判",
    ),
    Dimension(
        "plan_drift",
        "规划偏差",
        "正文与批准细纲／节拍的偏差及其理由。",
        "editor",
        True,
        "能判「正文引用的章卡是否存在」；偏离是否有理由要对着章卡读",
    ),
)

DIMENSION_IDS = tuple(item.id for item in REVIEW_DIMENSIONS)
DIMENSION_NAMES = {item.id: item.name for item in REVIEW_DIMENSIONS}
_BY_ID = {item.id: item for item in REVIEW_DIMENSIONS}


def get(dimension_id: str) -> Dimension:
    from ..domain.errors import UsageError

    key = (dimension_id or "").strip()
    if key not in _BY_ID:
        aliases = {item.name: item.id for item in REVIEW_DIMENSIONS}
        if key in aliases:
            return _BY_ID[aliases[key]]
        raise UsageError(
            f"未知审核维度 {dimension_id!r}。可用：" + "、".join(f"{i}({DIMENSION_NAMES[i]})" for i in DIMENSION_IDS)
        )
    return _BY_ID[key]


def blank() -> dict[str, dict]:
    """十项全 NOT_CHECKED。**这是起点，不是通过。**"""
    return {dim: {"verdict": "NOT_CHECKED", "note": ""} for dim in DIMENSION_IDS}


def normalise(raw: dict | None) -> dict[str, dict]:
    """把外部来的维度表补齐成十项。

    补齐而不是只收给了的：漏掉的项要显式变成 NOT_CHECKED，
    这样「没查」和「查了没问题」在数据结构里就分得开。
    """
    out = blank()
    for key, value in (raw or {}).items():
        if key not in _BY_ID:
            continue
        if isinstance(value, str):
            verdict, note = value, ""
        elif isinstance(value, dict):
            verdict = str(value.get("verdict") or "NOT_CHECKED")
            note = str(value.get("note") or "")
        else:
            continue
        verdict = verdict.upper()
        if verdict not in VERDICT_IDS:
            verdict = "NOT_CHECKED"
        out[key] = {"verdict": verdict, "note": note}
    return out


def unchecked(dimensions: dict | None) -> list[str]:
    """还没查的维度名。空列表 = 十项都有结论。"""
    table = normalise(dimensions)
    return [DIMENSION_NAMES[dim] for dim in DIMENSION_IDS if table[dim]["verdict"] == "NOT_CHECKED"]


def issues(dimensions: dict | None) -> list[str]:
    table = normalise(dimensions)
    return [DIMENSION_NAMES[dim] for dim in DIMENSION_IDS if table[dim]["verdict"] == "ISSUE"]


def coverage(dimensions: dict | None) -> tuple[int, int]:
    table = normalise(dimensions)
    done = sum(1 for dim in DIMENSION_IDS if table[dim]["verdict"] != "NOT_CHECKED")
    return done, len(DIMENSION_IDS)


def render_markdown(dimensions: dict | None) -> str:
    """渲染成报告里那张表。没查的用 `未审`，不写成空白——
    空白会被读成「没问题」。"""
    table = normalise(dimensions)
    lines = ["| # | 维度 | 结论 | 说明 |", "|---|---|---|---|"]
    labels = {"PASS": "通过", "ISSUE": "有问题", "NOT_APPLICABLE": "不涉及", "NOT_CHECKED": "**未审**"}
    for index, dim in enumerate(DIMENSION_IDS, start=1):
        cell = table[dim]
        note = cell["note"].replace("|", "\\|")
        lines.append(f"| {index} | {DIMENSION_NAMES[dim]} | {labels[cell['verdict']]} | {note} |")
    return "\n".join(lines)
