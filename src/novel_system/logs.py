"""运行日志（与审计日志是两件事）。

- **本模块**记「谁在什么时候跑了什么命令、用了多久、退出码几」——给排障用，可丢。
- **审计日志**在 `audit/`，记「谁改了哪个正式文件」——是证据，不可丢。

三条硬约束：

1. **绝不写 stdout**。命令的终端输出是给作者看的契约，日志混进去会污染它。
2. **写不进去也不能让命令失败**。日志是旁路，Runtime 只读、磁盘满、路径被占，
   一律降级为「只记内存」，不许把 `novel validate` 变成因为日志挂了而失败。
3. **重复调用不重复挂 handler**。同一进程里跑多次 `main()`（测试就是这样），
   每调一次加一个 FileHandler，日志会出现 N 份重复行。
"""

from __future__ import annotations

import logging
from pathlib import Path

ROOT_NAME = "novel"
LOG_FILE = "novel.log"
DEFAULT_FORMAT = "%(asctime)s %(levelname)-5s %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def get_logger(name: str) -> logging.Logger:
    """取本系统的 logger。名字统一挂在 `novel.` 下，便于一次性调级别。"""
    return logging.getLogger(f"{ROOT_NAME}.{name}" if name else ROOT_NAME)


def log_path(runtime: Path | None) -> Path | None:
    return None if runtime is None else Path(runtime) / "logs" / LOG_FILE


def setup_logging(
    runtime: Path | None,
    level: str = "INFO",
    console: bool = False,
) -> Path | None:
    """装配日志，返回日志文件路径（降级时为 None）。

    `runtime` 为 None 表示还没配置 Runtime：只留内存 handler，不报错。
    `console` 默认关；作者要边跑边看时用 `--log-level DEBUG` 加 `NOVEL_LOG_CONSOLE=1`。
    """
    global _configured

    root = logging.getLogger(ROOT_NAME)
    root.setLevel(logging.DEBUG)
    root.propagate = False

    # 清掉上一次的 handler，避免同进程多次调用叠出重复行。
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001 - 关不掉也不该影响命令
            pass

    numeric = _level_of(level)
    target: Path | None = None

    if numeric is None:
        root.addHandler(logging.NullHandler())
        _configured = True
        return None

    formatter = logging.Formatter(DEFAULT_FORMAT, DATE_FORMAT)

    if runtime is not None:
        candidate = log_path(runtime)
        assert candidate is not None
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(candidate, encoding="utf-8")
            handler.setLevel(numeric)
            handler.setFormatter(formatter)
            root.addHandler(handler)
            target = candidate
        except OSError:
            # 目录建不出来、文件打不开：降级，不抛。
            target = None

    if console:
        stream = logging.StreamHandler()  # 默认 stderr，不碰 stdout
        stream.setLevel(numeric)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    if not root.handlers:
        root.addHandler(logging.NullHandler())

    _configured = True
    return target


def is_configured() -> bool:
    return _configured


def _level_of(level: str | int | None) -> int | None:
    """`OFF` / `NONE` 返回 None，表示只留 NullHandler。"""
    if level is None:
        return logging.INFO
    if isinstance(level, int):
        return level
    text = str(level).strip().upper()
    if text in {"OFF", "NONE", "SILENT"}:
        return None
    value = logging.getLevelName(text)
    return value if isinstance(value, int) else logging.INFO
