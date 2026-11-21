"""时间线、状态区间、人物认知与读者揭示（T04）。

四个子模块：

- `model` ── 五类记录的数据结构与 frontmatter 提取；
- `graph` ── 事件 DAG：环检测、可比性、最晚候选；
- `query` ── 状态区间与认知查询，返回 PRD §16.3 的五个状态码。

**客观断言与人物认知物理分表**（`assertions` / `knowledge`），查询函数也分开写。
这不是洁癖：验收要求「角色误判不污染客观状态」，靠「记得加过滤条件」守不住，
靠表结构隔开才守得住。
"""

from .graph import (
    COMPARE_AFTER,
    COMPARE_BEFORE,
    COMPARE_SAME,
    COMPARE_UNORDERED,
    EventGraph,
)
from .model import (
    EDGE_PRECEDES,
    KNOWLEDGE_ATTITUDES,
    REVELATION_MODES,
    TYPE_PREFIX,
    Assertion,
    Edge,
    Event,
    Extracted,
    Knowledge,
    Revelation,
    StateChange,
    detect_type,
    extract,
)
from .query import (
    AMBIGUOUS,
    DISPUTED,
    MODE_AFTER,
    MODE_BEFORE,
    NOT_RECORDED,
    RESOLVED,
    UNORDERED,
    Answer,
    Record,
    knowledge_answer,
    revelation_answer,
    state_answer,
)

__all__ = [
    "AMBIGUOUS",
    "COMPARE_AFTER",
    "COMPARE_BEFORE",
    "COMPARE_SAME",
    "COMPARE_UNORDERED",
    "DISPUTED",
    "EDGE_PRECEDES",
    "KNOWLEDGE_ATTITUDES",
    "MODE_AFTER",
    "MODE_BEFORE",
    "NOT_RECORDED",
    "REVELATION_MODES",
    "RESOLVED",
    "TYPE_PREFIX",
    "UNORDERED",
    "Answer",
    "Assertion",
    "Edge",
    "Event",
    "EventGraph",
    "Extracted",
    "Knowledge",
    "Record",
    "Revelation",
    "StateChange",
    "detect_type",
    "extract",
    "knowledge_answer",
    "revelation_answer",
    "state_answer",
]
