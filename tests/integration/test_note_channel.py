"""`novel note`：08_Reviews 的轻量写通道。

这条通道的存在理由要能被测试证明，否则它只是「又多了一个写文件的命令」：
- 能写进去，且 frontmatter 合契约
- 同名即拒，绝不覆盖（审阅记录不该被静默改写）
- 不进正式区，所以 `publish` 的那套闸与它无关
- 分类落到正确子目录
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from conftest import project_root

PROJECT = "novel-001"


def _today() -> str:
    return date.today().isoformat().replace("-", "")


def _note(vault: Path, runtime: Path, *extra: str, body: str = "一条审阅意见。") -> int:
    from novel_system.cli import main

    return main(
        [
            "note",
            "add",
            "--vault",
            str(vault),
            "--runtime",
            str(runtime),
            "--project",
            PROJECT,
            *extra,
            "--body",
            body,
        ]
    )


def _reviews(vault: Path) -> Path:
    return project_root(vault) / "08_Reviews"


def test_writes_review_into_chapter_review_dir(vault_env) -> None:
    vault, runtime = vault_env
    code = _note(vault, runtime, "--title", "灰石渡第一章审阅", "--verdict", "revise", "--as", "editor")
    assert code == 0

    path = _reviews(vault) / "Audit_Reports" / "Chapter_Review" / f"灰石渡第一章审阅_{_today()}.md"
    assert path.exists(), f"没写到这里：{path}"
    text = path.read_text(encoding="utf-8")
    assert "type: review_report" in text
    assert "verdict: revise" in text
    assert "status: active" in text
    assert "一条审阅意见。" in text


def test_same_title_is_rejected_not_overwritten(vault_env) -> None:
    vault, runtime = vault_env
    assert _note(vault, runtime, "--title", "重复标题审查", body="第一版") == 0
    path = _reviews(vault) / "Audit_Reports" / "Chapter_Review" / f"重复标题审查_{_today()}.md"
    before = path.read_bytes()

    assert _note(vault, runtime, "--title", "重复标题审查", body="第二版") != 0
    assert path.read_bytes() == before, "同名写入不该改动已有记录"


def test_kind_routes_to_its_own_subdir(vault_env) -> None:
    vault, runtime = vault_env
    assert _note(vault, runtime, "--kind", "change-log", "--title", "卷一变更记录") == 0
    assert (_reviews(vault) / "Change_Log" / f"卷一变更记录_{_today()}.md").exists()

    assert _note(vault, runtime, "--kind", "audit", "--title", "全库体检") == 0
    assert (_reviews(vault) / "Audit_Reports" / f"全库体检_{_today()}.md").exists()


def test_unknown_kind_is_rejected(vault_env) -> None:
    vault, runtime = vault_env
    assert _note(vault, runtime, "--kind", "whatever", "--title", "x") != 0


def test_empty_body_is_rejected(vault_env) -> None:
    vault, runtime = vault_env
    assert _note(vault, runtime, "--title", "空意见", body="   ") != 0


def test_verdict_outside_enum_is_rejected_by_schema(vault_env) -> None:
    """`verdict` 只允许四个值。这层由 schema 拦，不在业务代码里再写一遍枚举。"""
    vault, runtime = vault_env
    assert _note(vault, runtime, "--title", "结论错值", "--verdict", "lgtm") != 0
    assert not (_reviews(vault) / "Audit_Reports" / "Chapter_Review" / f"结论错值_{_today()}.md").exists()


def test_reviews_are_not_in_formal_area(vault_env) -> None:
    """08_Reviews 不该进正式区：不是作品内容，也不该被算进签字基线。"""
    from novel_system import config as cfg_mod
    from novel_system.paths import PUBLISH_TARGETS, ProjectPaths

    vault, runtime = vault_env
    cfg = cfg_mod.load(vault=vault, runtime=runtime, project=PROJECT)
    pp = ProjectPaths.resolve(cfg, PROJECT)

    assert "review_root" not in PUBLISH_TARGETS
    target = pp.reviews / "Audit_Reports" / "x.md"
    assert pp.is_control_path(target) is False, "08_Reviews 不得被当成正式区路径"


def test_dry_run_writes_nothing(vault_env) -> None:
    vault, runtime = vault_env
    assert _note(vault, runtime, "--title", "只看看", "--dry-run") == 0
    assert not (_reviews(vault) / "Audit_Reports" / "Chapter_Review" / f"只看看_{_today()}.md").exists()


def test_list_shows_written_notes(vault_env) -> None:
    from novel_system.cli import main

    vault, runtime = vault_env
    assert _note(vault, runtime, "--title", "能被列出来的审查") == 0
    code = main(
        [
            "note",
            "list",
            "--vault",
            str(vault),
            "--runtime",
            str(runtime),
            "--project",
            PROJECT,
        ]
    )
    assert code == 0
