"""全库唯一的 wikilink 解析口径。

为什么单独成模块
----------------
同一串正则在 `validators/integrity.py`、`workflow/relation_sync.py`、
`workflow/map_sync.py` 里各抄过一份。三份一旦漂移，症状是**静默**的：
断链检查说「没问题」，关系投影却少了一条边，图谱看起来「就是没连上」。
口径只该有一处，其余一律 import。

表格里的转义竖线
----------------
Obsidian 的表格单元格里必须把别名分隔符写成 `\\|`（裸 `|` 会切断单元格），
所以真实档案里 `[[X|别名]]` 与 `[[X\\|别名]]` 两种写法并存。
只认前一种时，**表格里的链接会被整段漏掉**——而「索引 / 速查」类链接恰恰
最爱写成表格。这里一并接受，顺带补上 `[[X#小节]]` 与 `[[X#小节|别名]]`。
"""

from __future__ import annotations

import re

# 目标可以是 ID、文件名或带路径的名字；`#` 之后是锚点，`|` / `\|` 之后是别名。
WIKILINK = re.compile(r"\[\[([^\]|#\\]+?)(?:#[^\]|]*)?(?:\\?\|[^\]]*)?\]\]")


def targets(text: str) -> list[str]:
    """按出现顺序取出链接目标（去首尾空白，不含锚点与别名）。"""
    return [m.group(1).strip() for m in WIKILINK.finditer(text)]


def target_set(text: str) -> set[str]:
    """链接目标集合，用于「这段里已经提到过谁」的判断。"""
    return {m.group(1).strip() for m in WIKILINK.finditer(text)}
