"""权限执行层：Agent 的读写范围由 Python 强制，不依赖 Prompt（PRD 10.1）。"""

from __future__ import annotations

import re
from typing import Any

from ..config import Config


class PermissionDenied(RuntimeError):
    """越权访问。这是硬错误，调用方不得捕获后继续写入。"""


def _glob_to_regex(pattern: str) -> str:
    """支持 ** 的 glob：** 匹配任意层级，* 匹配单层。"""
    out = ["^"]
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i : i + 2] == "**":
                out.append(".*")
                i += 2
                if pattern[i : i + 1] == "/":
                    i += 1
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    out.append("$")
    return "".join(out)


def path_matches(rel_path: str, pattern: str) -> bool:
    # 只去掉开头的 "./" 或 "/"，不能用 lstrip("./")——那会把 .novel_system 的点也吃掉
    if rel_path.startswith("./"):
        rel_path = rel_path[2:]
    rel_path = rel_path.lstrip("/")
    if re.fullmatch(_glob_to_regex(pattern), rel_path):
        return True
    # 目录形式（以 / 结尾）视作前缀
    if pattern.endswith("/") and rel_path.startswith(pattern):
        return True
    return False


def match_any(rel_path: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        if path_matches(rel_path, pattern):
            return pattern
    return None


def agent_write_scope(cfg: Config, agent: str) -> list[str]:
    return list(cfg.agent_def(agent).get("write") or [])


def agent_read_scope(cfg: Config, agent: str) -> list[str]:
    return list(cfg.agent_def(agent).get("read") or [])


def check_agent_write(cfg: Config, agent: str, rel_path: str) -> None:
    """Agent 写文件前的硬校验：先看路径锁，再看 Agent 自己的写范围。"""
    if agent == "author":
        return

    locks = cfg.permissions.get("path_locks") or {}
    denied = list(locks.get("agent_denied_paths") or [])
    allowed_global = list(locks.get("agent_allowed_prefixes") or [])

    if match_any(rel_path, denied) and not any(
        rel_path.startswith(prefix) for prefix in allowed_global
    ):
        raise PermissionDenied(
            f"Agent {agent} 试图写正式事实源 {rel_path}：路径锁拒绝。"
            "所有正式写入必须经过 Python 执行器的事务流程。"
        )

    scope = agent_write_scope(cfg, agent)
    if not scope:
        raise PermissionDenied(f"Agent {agent} 没有写权限。")
    if not match_any(rel_path, scope):
        raise PermissionDenied(
            f"Agent {agent} 的写范围不包含 {rel_path}（允许：{', '.join(scope)}）。"
        )


def check_action(cfg: Config, agent: str, action: str) -> str:
    """返回 allow / propose / deny；deny 直接抛异常。"""
    matrix = (cfg.permissions.get("matrix") or {}).get(agent) or {}
    decision = matrix.get(action, "deny")
    if decision == "deny":
        raise PermissionDenied(f"Agent {agent} 无权限执行 {action}（权限矩阵 deny）。")
    return decision


def can_propose_canon_change(cfg: Config, agent: str) -> bool:
    return bool(cfg.agent_def(agent).get("can_propose_canon_change", False))


def resolve_approval_class(cfg: Config, kind: str, payload: dict[str, Any]) -> str:
    """按更新类型决定审批级别（PRD 12.3）。返回 auto / author / blocking。"""
    table = cfg.permissions.get("approval_classes") or {}

    if kind in table:
        return str(table[kind])

    if kind == "character_state":
        text = " ".join(str(v) for v in payload.values())
        if any(word in text for word in ("死亡", "复活", "已死")):
            return "blocking"
        if "伤势" in payload or "injury" in payload or "ability" in payload:
            return "author"
        return str(table.get("character_location", "author"))

    return str(table.get("new_entity", "author"))


def assert_formal_write_allowed(cfg: Config, actor: str) -> None:
    """只有作者（或 Python 执行器代表已获批提案）能写正式文件。"""
    if actor not in ("author", "system_executor"):
        raise PermissionDenied(
            f"{actor} 不能直接写正式文件；请先生成提案并获得作者批准。"
        )
