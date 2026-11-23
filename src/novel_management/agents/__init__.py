"""Agent 适配器与写前资料包（T08）。

| 模块 | 管什么 |
|---|---|
| `registry` | 九角色注册与权限矩阵（`agents/common/permissions.md` 的机器副本） |
| `tools` | 类型化工具：能力位从矩阵推出，越权在网关层直接拒 |
| `budget` | 上下文预算：截断可见、硬约束不丢 |
| `prompts` | 提示词装配：角色卡 + PRD §6.4 的九项公共约束 |
| `metering` | 调用计量：`<runtime>/usage/YYYYMMDD.jsonl` |
| `runner` | 一次模型调用：装配 → 调用 → 信封校验，任一步失败都不猜 |
| `preflight` | PRD §13 资料包：八级检索、PASS／BLOCKED、预算账 |
"""

from . import budget, metering, preflight, prompts, registry, runner, tools
from .budget import Budget, Overflow, Truncation
from .metering import Meter
from .preflight import PreflightResult
from .prompts import Prompt
from .registry import ROLES, ROLE_IDS, Role
from .runner import AgentRun
from .tools import Tool, ToolResult, allowed_tools

__all__ = [
    "ROLES",
    "ROLE_IDS",
    "AgentRun",
    "Budget",
    "Meter",
    "Overflow",
    "PreflightResult",
    "Prompt",
    "Role",
    "Tool",
    "ToolResult",
    "Truncation",
    "allowed_tools",
    "budget",
    "metering",
    "preflight",
    "prompts",
    "registry",
    "runner",
    "tools",
]
