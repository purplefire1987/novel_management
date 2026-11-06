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

## 篇幅与文风（细则见 `00_System/Prompts/writer.md`，此处只列可查项）

草稿交出前必须满足，任一不满足 → `REVISION_REQUIRED`：

| 检查项 | 判据 |
|---|---|
| 篇幅 | 正文汉字数 3,000–4,000；`word_count` 与实测差 ≤3% |
| 句式 | 「不是A，是B」≤ 每千字 1 处，且都属 writer.md 列的三种例外 |
| 白描 | 每场至少一处非视觉描写；无「美丽／可怕／阴森／气氛紧张」这类结论词成串出现 |
| 角色八维 | 性别、种族、年龄、外貌锚点、性格、动作习惯、生活习惯、地域，与角色卡一致 |
| 跨场延续 | 场景卡的 `carry` 全部延续，或写明它怎么变了 |
