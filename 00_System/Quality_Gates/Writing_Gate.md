# 写作 Gate（Writing Gate）

主笔从 `preflight_passed` 进入 `draft` 必须满足：

- 使用作者批准的大纲（`## 作者批准` 勾选）；
- 章节目标、POV、时间、地点四项非空；
- 输入上下文来自 Preflight Pack，不得自行扩大读取范围；
- 关键状态与资料包一致（伤势、位置、持有物、灯符等资源）；
- 草稿只能写入 `.novel_system/staging/draft/`；
- 不得直接写 `08_Manuscripts/`。

硬失败（直接拒绝执行）：
- 没有 Preflight Pack；
- 大纲未批准；
- 章节状态不是 `preflight_passed` / `revision`；
- 主笔试图写正式路径。
