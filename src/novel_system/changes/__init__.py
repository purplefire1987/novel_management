"""变更治理（T05）：问题等级、变更请求、影响分析。

三个部分：

- `issues` ── 问题登记与等级；BLOCKER 不能豁免；
- `requests` ── 五类变更的登记与独立审批（RETCON）；
- `impact` ── 按索引反查影响的报告。

问题与变更都存在 `<runtime>/` 下：它们是审核过程的判词与决定，不是作品内容。
放进 Vault 就会被算进正式区与签字基线，反而让「签字失效」变成日常噪音。
"""

from .impact import Impact, analyse
from .issues import (
    BLOCKER,
    CATEGORIES,
    NON_BLOCKING,
    OPEN,
    RESOLVED,
    SEVERITIES,
    STATUSES,
    SUGGESTION,
    WAIVED,
    Issue,
    IssueStore,
)
from .requests import (
    APPROVED,
    CORRECTION,
    DRAFT,
    EDITORIAL,
    IMPACTED,
    INDEPENDENT_APPROVAL,
    PLAN_CHANGE,
    REJECTED,
    RETCON,
    STATE_CHANGE,
    TYPES,
    ChangeRequest,
    ChangeStore,
)

__all__ = [
    "APPROVED",
    "BLOCKER",
    "CATEGORIES",
    "CORRECTION",
    "DRAFT",
    "EDITORIAL",
    "IMPACTED",
    "INDEPENDENT_APPROVAL",
    "NON_BLOCKING",
    "OPEN",
    "PLAN_CHANGE",
    "REJECTED",
    "RESOLVED",
    "RETCON",
    "SEVERITIES",
    "STATE_CHANGE",
    "STATUSES",
    "SUGGESTION",
    "TYPES",
    "WAIVED",
    "ChangeRequest",
    "ChangeStore",
    "Impact",
    "Issue",
    "IssueStore",
    "analyse",
]
