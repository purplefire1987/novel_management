"""全局参数的位置不该改变含义。

回归的是这样一个缺陷：`novel --vault X status` 里的 X 被静默丢弃，
命令退化成「未配置作者 Vault」，而 `novel status --vault X` 正常。

根因在 argparse：解析子命令时会新开一个 namespace，解析完把它的全部属性
搬回主 namespace，子命令那份 `--vault` 的默认值 None 就把主命令刚解析到的
X 盖掉了。修法是子命令那份默认值取 SUPPRESS，让「没写就不产生属性」。
"""

from __future__ import annotations

import pytest

from novel_management.cli import build_parser, main

GLOBALS = ("--vault", "/tmp/v", "--runtime", "/tmp/r", "--project", "p1", "--log-level", "DEBUG")


def _parse(argv: list[str]):
    return build_parser().parse_args(argv)


def test_global_args_before_subcommand_survive() -> None:
    ns = _parse([*GLOBALS, "status"])
    assert ns.vault == "/tmp/v"
    assert ns.runtime == "/tmp/r"
    assert ns.project == "p1"
    assert ns.log_level == "DEBUG"


def test_global_args_after_subcommand_still_work() -> None:
    ns = _parse(["status", *GLOBALS])
    assert ns.vault == "/tmp/v"
    assert ns.runtime == "/tmp/r"
    assert ns.project == "p1"
    assert ns.log_level == "DEBUG"


def test_defaults_are_intact_when_nothing_is_given() -> None:
    ns = _parse(["status"])
    assert ns.vault is None
    assert ns.runtime is None
    assert ns.project is None
    assert ns.log_level == "INFO"


def test_nested_subcommand_keeps_global_args() -> None:
    """两层子命令（schema list）同理会覆盖，一并守住。"""
    ns = _parse([*GLOBALS, "schema", "list"])
    assert ns.vault == "/tmp/v"
    assert ns.project == "p1"


def test_nested_subcommand_accepts_global_args_at_tail() -> None:
    ns = _parse(["schema", "list", *GLOBALS])
    assert ns.vault == "/tmp/v"
    assert ns.log_level == "DEBUG"


def test_later_occurrence_wins() -> None:
    """两处都写时，靠后的那次生效——与普通命令行参数的直觉一致。"""
    ns = _parse(["--vault", "/tmp/first", "status", "--vault", "/tmp/second"])
    assert ns.vault == "/tmp/second"


@pytest.fixture()
def env(tmp_path):
    vault, runtime = tmp_path / "vault", tmp_path / "runtime"
    assert main(["init-author", "--vault", str(vault), "--runtime", str(runtime), "--confirm"]) == 0
    return vault, runtime


def test_status_reads_vault_written_before_subcommand(env, capsys) -> None:
    """端到端：前置写法必须真的把 Vault 用起来，而不只是解析层看起来对。"""
    vault, runtime = env
    capsys.readouterr()

    assert main(["--vault", str(vault), "--runtime", str(runtime), "status"]) == 0
    before = capsys.readouterr().out
    assert str(vault) in before
    assert "未配置" not in before

    assert main(["status", "--vault", str(vault), "--runtime", str(runtime)]) == 0
    after = capsys.readouterr().out
    assert str(vault) in after

    # 两种写法的输出应当一致；不一致就说明其中一条路径走了不同配置
    assert before == after
