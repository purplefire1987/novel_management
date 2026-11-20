"""命令层可预期错误。每种错误带固定退出码，脚本可据此分支。"""

from __future__ import annotations


class NovelError(Exception):
    """所有可预期错误的基类。消息面向作者，不含堆栈。"""

    code = 1


class UsageError(NovelError):
    """参数不合法、缺少必填项。"""

    code = 2


class ConflictError(NovelError):
    """目标已存在、内容已变更、索引与事实源不一致。"""

    code = 1


class SecurityError(NovelError):
    """路径越界、未授权写入、批准主体不符。"""

    code = 3


class ApprovalError(NovelError):
    """缺少有效批准、批准已失效。"""

    code = 4
