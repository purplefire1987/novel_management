"""写后闭环（T10）：依赖失效、共同发布。

```
dependencies 声明 → 比对 → STALE / NEEDS_REVIEW  改了东西谁受影响
closure      发布前逐章问：记忆说清楚了吗        不许半边生效
```

`closure` 调 `dependencies`，所以它排最后：闸门要看到依赖的**当前**状态，
而不是缓存的快照。`contract` 是同目录下的结构校验，与这两条链并列。

**提案（proposals）不在这里。** 它做的是「从已批准正文提取 → 校验源哈希 → 批准」，
那是业务流程不是校验，现在住在 `workflows/proposals.py`：它本来就依赖
`workflows.review`，放在 `validation/` 下等于让校验包反过来依赖业务包。
包名与内容脱钩的代价是具体的——找「记忆提案为什么没过」的人会先翻这个包。
"""

from __future__ import annotations

from . import closure, dependencies

__all__ = ["closure", "dependencies"]
