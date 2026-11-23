"""九角色注册。**权限矩阵的机器副本。**

人读版在 `agents/common/permissions.md`，本模块是它的机器版。两处重复是没办法的：
文档要给人看，代码要能拦住错误。所以不靠「保持同步」这种自觉，靠
`tests/unit/test_agents_registry.py` 把 md 表格解析出来逐格比对——
**表格改了一个字而代码没改，测试就红。**

角色名单已冻结（规范 v1.7 §4）：总编、世界架构师、编辑、骰子娘、资料官、
主笔、记忆管理员、录入员、版本管理员。PRD v2.1 §6.1 的 A1–A9 里
「剧情规划师」「一致性审校」两个角色已裁定作废（前者并入编辑，后者转为总编签字），
所以这里没有它们，也不该有。

三条硬约束写在这一层，不写进提示词：

1. **任何 Agent 都不能写 Canon**（`canon_write` 恒 False）。想改 Canon 只能
   写 staging + 提案，由作者批准后 CLI 落位。
2. **任何 Agent 都不能发布**（`may_release` 恒 False）。批准与发布是作者专属，
   `audit.AUTHOR_ONLY` 已经这么拦了，这里再拦一次——两道锁不是冗余，
   是防止以后有人只改一处。
3. **不批准自己的输出**：由 approvals 层按 proposer 比对，不在本层表达。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..domain.errors import SecurityError, UsageError

# ---- 读范围 ----
SCOPE_ALL = ""            # 全库 Canon 可读
SCOPE_AUTHORIZED = "authorized"  # 只读当前任务授权的范围
SCOPE_SOURCE = "source"   # 只读原稿

_READ_LABEL = {
    SCOPE_ALL: "R",
    SCOPE_AUTHORIZED: "R（限授权）",
    SCOPE_SOURCE: "R（源稿）",
}


@dataclass(frozen=True)
class Role:
    """一个角色的权限与产出边界。

    字段不是五种权限的枚举，而是 permissions.md 那五列本身——
    这样 `matrix_row()` 能一字不差地渲染出文档里那一行，
    比对用的是字符串相等，不是「差不多」。
    """

    id: str
    name: str
    duty: str
    read_scope: str = SCOPE_ALL
    staging_write: bool = True
    may_propose: bool = True
    draft_write: bool = False

    # 恒为 False，留字段是为了让「谁能写 Canon / 谁能发布」有地方可问。
    canon_write: bool = False
    may_release: bool = False

    def matrix_row(self) -> tuple[str, str, str, str, str]:
        """按 permissions.md 的五列渲染本行。"""
        return (
            _READ_LABEL[self.read_scope],
            "S" if self.canon_write else "X",
            "S/P" if (self.staging_write and self.may_propose) else ("S" if self.staging_write else "X"),
            "S" if self.draft_write else "X",
            "S/P" if self.may_release else "X",
        )


ROLES: tuple[Role, ...] = (
    Role("editor_in_chief", "总编", "风险路由、争议建议、阶段审核汇总；唯一签字人"),
    Role("world_architect", "世界架构师", "规则、能力边界、宏观一致性"),
    Role("editor", "编辑", "语言、节奏、视角、可读性；兼总纲与章节规划"),
    Role(
        "dice_mistress",
        "骰子娘",
        "随机性与分支推演（可按需空转）",
        read_scope=SCOPE_AUTHORIZED,
    ),
    Role("researcher", "资料官", "检索、来源核验、写前资料包 Preflight"),
    Role(
        "writer",
        "主笔",
        "授权内创作正文草稿与修订稿",
        draft_write=True,
    ),
    Role("memory_keeper", "记忆管理员", "从批准正文提取记录提案"),
    Role(
        "ingestor",
        "录入员",
        "原稿导入与消歧；只写 staging，不提变更",
        read_scope=SCOPE_SOURCE,
        may_propose=False,
    ),
    Role("version_manager", "版本管理员", "依赖、差异、影响与恢复建议"),
)

ROLE_IDS: tuple[str, ...] = tuple(role.id for role in ROLES)

_BY_ID = {role.id: role for role in ROLES}

# 角色卡与提示词的落点：仓库根的 `agents/`。允许用环境变量覆盖，
# 测试与将来的「作者自己改角色卡」都走这一条，不在代码里写死绝对路径。
AGENTS_DIR_ENV = "NOVEL_AGENTS_DIR"
PERMISSIONS_DOC = "common/permissions.md"


def agents_dir() -> Path:
    """角色卡目录。

    默认取仓库根的 `agents/`（本文件位于 `src/novel_management/agents/`，向上三级）。
    找不到就让调用方去撞 `UsageError`——**不能返回一个空目录然后装作角色卡为空**。
    """
    import os

    override = os.environ.get(AGENTS_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3] / "agents"


def role(role_id: str) -> Role:
    key = (role_id or "").strip()
    found = _BY_ID.get(key)
    if found is None:
        raise UsageError(f"未知角色：{key!r}（可选 {', '.join(ROLE_IDS)}）")
    return found


def maybe_role(role_id: str) -> Role | None:
    return _BY_ID.get((role_id or "").strip())


def role_of_actor(actor: str) -> Role | None:
    """把 CLI 的 `--as` 换成角色。作者不是角色，返回 None。"""
    return maybe_role(actor)


def ensure_can_stage(role_id: str) -> Role:
    found = role(role_id)
    if not found.staging_write:
        raise SecurityError(f"{found.id}（{found.name}）不允许写 staging")
    return found


def ensure_can_draft(role_id: str) -> Role:
    found = role(role_id)
    if not found.draft_write:
        raise SecurityError(
            f"{found.id}（{found.name}）不产出正文草稿；正文只有主笔写（见 agents/common/permissions.md）"
        )
    return found


def ensure_can_write_canon(role_id: str) -> None:
    """永远抛。写成函数是为了让调用点读起来像一条规则，而不是一句注释。"""
    found = role(role_id)
    raise SecurityError(
        f"{found.id}（{found.name}）不能写 Canon。任何 Canon 变更只能走"
        "「staging → 作者批准 → publish」，这条不接受参数"
    )


def ensure_can_release(role_id: str) -> None:
    found = role(role_id)
    raise SecurityError(
        f"{found.id}（{found.name}）不能发布或批准。发布是作者专属动作（--as author）"
    )


def prompt_file(role_id: str) -> Path:
    """角色卡路径。缺文件直接报错，不返回一个「大概会存在」的路径。"""
    found = role(role_id)
    path = agents_dir() / found.id / "agent.md"
    if not path.exists():
        raise UsageError(
            f"角色卡缺失：{path}；九个角色各需一份 agent.md（PRD §6.4 要求独立提示词）"
        )
    return path


def read_prompt_card(role_id: str) -> str:
    return prompt_file(role_id).read_text(encoding="utf-8")


def permission_rows() -> list[tuple[str, tuple[str, str, str, str, str]]]:
    """给 `novel agent list` 与测试用的稳定输出。"""
    return [(role.id, role.matrix_row()) for role in ROLES]
