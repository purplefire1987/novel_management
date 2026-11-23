"""九角色注册：机器副本必须与人读版逐格一致。

这一组测试的真正作用不是「测代码有没有 bug」，而是**防两类漂移**：

1. `agents/common/permissions.md` 改了一格，代码没改——权限表就成了摆设；
2. 有人在 `registry.ROLES` 里多加一个角色，而 PRD 与文档都没有这个角色。
   九角色是冻结名单（规范 v1.7 §4），多一个少一个都要先改裁定。

所以这里不写「断言 editor_in_chief 是 R」这种抄写题，而是把 md 表格解析出来，
与代码逐格比对。表格怎么改，测试就怎么跟着走。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from novel_management.agents import registry, tools
from novel_management.errors import SecurityError, UsageError

ROOT = Path(__file__).resolve().parents[2]
PERMISSIONS = ROOT / "agents" / "common" / "permissions.md"

# 九角色（规范 v1.7 §4 冻结名单）。
EXPECTED_IDS = {
    "editor_in_chief",
    "world_architect",
    "editor",
    "dice_mistress",
    "researcher",
    "writer",
    "memory_keeper",
    "ingestor",
    "version_manager",
}


def parse_permission_table(path: Path) -> dict[str, tuple[str, ...]]:
    """把 permissions.md 的表格解析成 {角色: (五列,)}。

    只认「第一格是 `id（中文名）`、后面正好五格」的行，
    **不按行号数**——表格以后加注释行、加空行都不该让测试失效。
    """
    rows: dict[str, tuple[str, ...]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 6:
            continue
        head = cells[0]
        match = re.match(r"^([a-z_]+)（(.+?)）$", head)
        if not match:
            continue
        rows[match.group(1)] = tuple(cells[1:])
    return rows


def test_nine_roles_exact() -> None:
    assert set(registry.ROLE_IDS) == EXPECTED_IDS
    assert len(registry.ROLES) == 9


def test_matrix_matches_permissions_md() -> None:
    documented = parse_permission_table(PERMISSIONS)
    assert set(documented) == EXPECTED_IDS, "permissions.md 的行与九角色名单不一致"
    for role in registry.ROLES:
        assert role.matrix_row() == documented[role.id], (
            f"{role.id} 的权限与 permissions.md 不一致："
            f"代码 {role.matrix_row()} / 文档 {documented[role.id]}"
        )


def test_no_agent_may_write_canon_or_publish() -> None:
    """两道锁：写 Canon 与发布，九角色一个都不许。"""
    for role in registry.ROLES:
        assert role.canon_write is False, f"{role.id} 不该能写 Canon"
        assert role.may_release is False, f"{role.id} 不该能发布"
        with pytest.raises(SecurityError):
            registry.ensure_can_write_canon(role.id)
        with pytest.raises(SecurityError):
            registry.ensure_can_release(role.id)


def test_only_writer_may_draft() -> None:
    drafters = [role.id for role in registry.ROLES if role.draft_write]
    assert drafters == ["writer"]
    for role_id in EXPECTED_IDS - {"writer"}:
        with pytest.raises(SecurityError):
            registry.ensure_can_draft(role_id)


def test_ingestor_cannot_write_staging_by_proposal() -> None:
    """录入员只写 staging，不提变更（permissions.md 那一格是 S 不是 S/P）。"""
    ingestor = registry.role("ingestor")
    assert ingestor.staging_write is True
    assert ingestor.may_propose is False


def test_unknown_role_rejected() -> None:
    with pytest.raises(UsageError):
        registry.role("plot_planner")  # PRD §6.1 的角色，已裁定作废
    with pytest.raises(UsageError):
        registry.role("")


def test_tool_availability_follows_matrix() -> None:
    """工具不是另写一张清单，而是从能力位推出来的。"""
    # 所有能读 Canon 的角色都拿到读工具
    for role in registry.ROLES:
        if role.read_scope == registry.SCOPE_ALL:
            assert "search" in tools.allowed_tool_names(role.id), role.id
    # 只能读源稿的录入员拿不到任何 Canon 读工具
    assert tools.allowed_tool_names("ingestor") == ()
    caps = tools.capabilities(registry.role("ingestor"))
    assert tools.CAP_READ_CANON not in caps
    assert tools.CAP_READ_SOURCE in caps


def test_scoped_reader_must_state_scope() -> None:
    """「R（限授权）」不是一句说明，是执行层的拦截。"""
    from novel_management.agents.tools import ToolContext

    ctx = ToolContext(pp=None, runtime=Path("/nonexistent"), project="p")  # type: ignore[arg-type]
    with pytest.raises(SecurityError) as exc:
        tools.call(ctx, "dice_mistress", "search", query="灰石渡")
    assert "限授权" in str(exc.value)


def test_calling_forbidden_tool_raises() -> None:
    from novel_management.agents.tools import ToolContext

    ctx = ToolContext(pp=None, runtime=Path("/nonexistent"), project="p")  # type: ignore[arg-type]
    with pytest.raises(SecurityError):
        tools.call(ctx, "ingestor", "search", query="灰石渡")


def test_unknown_param_rejected() -> None:
    """拼错参数名必须报错，不能变成「查不到」。"""
    from novel_management.agents.tools import ToolContext

    ctx = ToolContext(pp=None, runtime=Path("/nonexistent"), project="p")  # type: ignore[arg-type]
    with pytest.raises(UsageError) as exc:
        tools.call(ctx, "researcher", "search", q="灰石渡")
    assert "不认识参数" in str(exc.value)


def test_every_role_has_prompt_card() -> None:
    for role_id in EXPECTED_IDS:
        path = registry.prompt_file(role_id)
        assert path.exists(), f"{role_id} 缺角色卡"
        text = path.read_text(encoding="utf-8")
        for section in ("## 职责", "## 输入", "## 输出", "## 禁止", "## 完成判据"):
            assert section in text, f"{role_id} 缺 {section}"
