"""创作与审核链路（T09）。

三段，各有各的边界：

- `plan`    —— 规划够不够格开写。只读；派生只取确定性可读的条目。
- `draft`   —— 主笔产草稿。只写 staging，结构上碰不到 Canon。
- `review`  —— 审核与风险路由。风险只能升不能降，高风险不许自审。
- `text_approval` —— 作者批正文。绑字节哈希，且必须先过审核。
- `proposals` —— 从已批准正文提取记忆提案。**从 `validation/` 搬来**：
  它是业务流程（提取 → 校验源哈希 → 批准），住在校验包里会让包名与内容脱钩，
  而且它本来就依赖本包的 `review`，方向是反的。

为什么把「写」和「审」分成两个模块而不是一个 workflow 类：它们的前置、失败
方式、可重入性都不一样。写在 `write` 失败时应该不留痕迹；审在 `submit` 失败时
必须让「已经返修过几轮」可数。合成一个对象只会让两种语义互相污染。
"""

from __future__ import annotations

from . import draft, plan, proposals, review, text_approval

__all__ = ["draft", "plan", "proposals", "review", "text_approval"]
