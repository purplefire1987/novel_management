"""版本号只有一处。

CLI 的 `--version`、发布清单的 `tool_version`、计划里的 `schema_version`
都要同一个值。分散定义过一次（`cli/__init__.py` 里一份、pyproject 里一份），
结果发布清单记的版本和实际跑的不是一回事。
"""

from __future__ import annotations

__version__ = "0.13.0"

# V4.0 规范版本与包版本不是一回事：前者是文档版本，后者是代码版本。
SPEC_VERSION = "4.0.0"
