"""写后闭环（T10）：记忆提案、依赖失效、共同发布。

三件事，一条链：

```
proposals    提取 → 校验（源哈希）→ 批准        记忆从哪来
dependencies 声明 → 比对 → STALE / NEEDS_REVIEW  改了东西谁受影响
closure      发布前逐章问：记忆说清楚了吗        不许半边生效
```

`closure` 同时调前两个模块，所以它排最后：闸门要能看到提案与依赖的**当前**状态，
而不是各自缓存的快照。
"""

from __future__ import annotations

from . import closure, dependencies, proposals

__all__ = ["closure", "dependencies", "proposals"]
