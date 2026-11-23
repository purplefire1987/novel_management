"""运行日志模块的测试。

重点不在「能写一行」，而在三条约束：
写不进也不许让命令失败、重复 setup 不许叠加 handler、日志不许进 stdout。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_management.audit import logs


@pytest.fixture(autouse=True)
def clean_handlers():
    """每个用例后把 handler 收掉，免得残留句柄指向已删除的 tmp 目录。"""
    yield
    logs.setup_logging(None)


def test_writes_to_runtime_logs(tmp_path: Path) -> None:
    target = logs.setup_logging(tmp_path, "INFO")
    assert target == tmp_path / "logs" / "novel.log"

    logs.get_logger("t").info("基准 %s", 1)
    text = target.read_text(encoding="utf-8")
    assert "基准 1" in text
    assert "INFO" in text


def test_repeated_setup_does_not_duplicate_lines(tmp_path: Path) -> None:
    """同进程跑多次 main() 是常态（测试就是这么跑的）。

    旧实现每调一次加一个 FileHandler，一条日志会被写 N 遍。
    这里用「只有一行」作为判据，而不是「有内容」。
    """
    target = logs.setup_logging(tmp_path, "INFO")
    logs.setup_logging(tmp_path, "INFO")
    logs.setup_logging(tmp_path, "INFO")

    logs.get_logger("t").info("只应出现一次")
    lines = [ln for ln in target.read_text(encoding="utf-8").splitlines() if "只应出现一次" in ln]
    assert len(lines) == 1


def test_runtime_none_does_not_raise() -> None:
    assert logs.setup_logging(None, "INFO") is None
    logs.get_logger("t").info("无 Runtime 时写内存即可")


def test_off_level_writes_nothing(tmp_path: Path) -> None:
    target = logs.setup_logging(tmp_path, "OFF")
    assert target is None
    logs.get_logger("t").error("这一行不该落盘")
    path = tmp_path / "logs" / "novel.log"
    assert not path.exists() or path.read_text(encoding="utf-8") == ""


def test_unwritable_runtime_degrades_silently(tmp_path: Path) -> None:
    """Runtime 位置被普通文件占住 → mkdir 抛 OSError。

    日志是旁路：这时必须降级返回 None，而不是把命令带崩。
    """
    blocker = tmp_path / "runtime"
    blocker.write_text("not a directory", encoding="utf-8")

    assert logs.setup_logging(blocker, "INFO") is None
    logs.get_logger("t").warning("降级后仍可记录")


def test_does_not_touch_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    logs.setup_logging(tmp_path, "INFO")
    logs.get_logger("t").info("这条消息不该出现在终端")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_cli_writes_one_pair_per_command(tmp_path: Path) -> None:
    """从 CLI 真跑一次，验证 start/done 成对，且退出码进了日志。"""
    from novel_management.cli import main

    vault, runtime = tmp_path / "vault", tmp_path / "runtime"
    assert main(["init-author", "--vault", str(vault), "--runtime", str(runtime), "--confirm"]) == 0

    log_file = runtime / "logs" / "novel.log"
    text = log_file.read_text(encoding="utf-8")
    assert "start cmd=init-author" in text
    assert "done cmd=init-author rc=0" in text
