"""运行日志（与审计日志是两件事）。

- **本模块**记「谁在什么时候跑了什么命令、用了多久、退出码几」——给排障用，可丢。
- **审计日志**在 `audit/`，记「谁改了哪个正式文件」——是证据，不可丢。

三条硬约束：

1. **绝不写 stdout**。命令的终端输出是给作者看的契约，日志混进去会污染它。
2. **写不进去也不能让命令失败**。日志是旁路，Runtime 只读、磁盘满、路径被占，
   一律降级为「只记内存」，不许把 `novel validate` 变成因为日志挂了而失败。
3. **重复调用不重复挂 handler**。同一进程里跑多次 `main()`（测试就是这样），
   每调一次加一个 FileHandler，日志会出现 N 份重复行。

## 脱敏（AC-47）

密钥进日志有两条路：调用点手写 `logger.info(f"...{key}")`，或者异常堆栈里带出
一个含 key 的请求体。**只堵第一条没用**——第二条发生在框架内部，调用点根本看不到。
所以脱敏挂在 `Formatter` 上：无论消息从哪来、是不是异常堆栈，只要经过 formatter
就过一遍替换。这样新增日志自动受保护，不需要每个调用点记得脱敏。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

ROOT_NAME = "novel"
LOG_FILE = "novel.log"
DEFAULT_FORMAT = "%(asctime)s %(levelname)-5s %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

PLACEHOLDER = "<REDACTED>"

# 只认「键 + 分隔符 + 值」的形状，不对裸单词动手：
# 把「token 用量 1200」改成「<REDACTED> 用量」是误伤，比漏脱敏更让人不敢信日志。
_SECRET_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Bearer 必须排在「键值对」规则之前：否则 `Authorization: Bearer eyJ...`
    # 会先被键值对规则把 `Bearer` 这个词当值换掉，JWT 本体反而留在日志里。
    # 那是脱敏最坏的失败方式——看起来脱过了。
    # PEM 私钥整段放最前：若让键值对规则先跑，它会把 `-----BEGIN` 换成占位符，
    # 这条就再也匹配不到 `-----END`，密钥体留在后几行。
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        f"{PLACEHOLDER}:PRIVATE_KEY",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{8,}=*"), f"Bearer {PLACEHOLDER}"),
    # Basic / Digest / Negotiate / NTLM：凭据紧跟在方案词后面，方案不能当值。
    (
        re.compile(r"(?i)\b(basic|digest|negotiate|ntlm)\s+[A-Za-z0-9+/=._~\-]{8,}"),
        rf"\1 {PLACEHOLDER}",
    ),
    # key=value / key: value / "key": "value"。authorization 排除已知方案前缀，
    # 否则 `Authorization: Basic xxx` 会被这条先把 `Basic` 换成占位符、凭据留在原地。
    (
        re.compile(
            r"(?i)\b(api[_-]?key|apikey|access[_-]?key|secret[_-]?key|secret|"
            r"token|access[_-]?token|refresh[_-]?token|password|passwd|pwd|"
            r"authorization|client[_-]?secret|"
            r"response|nonce|opaque|signature|x-api-key|private[_-]?key)\b"
            r"(\s*[:=]\s*)(?!bearer|basic|digest|negotiate|ntlm)([\"']?)([^\s,;\"'\)}\]]{4,})"
        ),
        r"\1\2\3" + PLACEHOLDER,
    ),
    # 常见云厂商密钥形状
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), PLACEHOLDER),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), PLACEHOLDER),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), PLACEHOLDER),
    # URL 里的 user:pass@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s:@]+:[^/\s@]+@"), rf"\1{PLACEHOLDER}@"),
    # query string 里的 token
    (re.compile(r"(?i)([?&](?:api[_-]?key|token|secret|access_token)=)([^&\s]{4,})"), rf"\1{PLACEHOLDER}"),
)


def redact(text: str) -> str:
    """把疑似密钥替换成占位符。纯函数，便于直接测。"""
    if not text:
        return text
    for pattern, repl in _SECRET_RULES:
        text = pattern.sub(repl, text)
    return text


class RedactingFormatter(logging.Formatter):
    """在 formatter 层脱敏：消息、参数、异常堆栈一视同仁。

    为什么不放在 `Filter` 里：`Filter` 拿到的是 `record.msg` 与 `record.args`，
    异常堆栈还没渲染出来；而堆栈恰恰是最容易带出请求体与密钥的地方。
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))



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

    formatter = RedactingFormatter(DEFAULT_FORMAT, DATE_FORMAT)

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
