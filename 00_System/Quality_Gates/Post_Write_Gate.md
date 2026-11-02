# 写后 Gate（Post-Write Gate）

记忆提交要成功，以下必须同时成立，任一失败即整体回滚：

- 作者已确认正文（章节状态 ≥ `author_approved` 且已 `published`）；
- 每条提案都有 `evidence`（正文句段）或章节引用；
- Schema 校验通过；
- 实体引用有效（`target_id`、`participants`、`location_id` 均存在）；
- 状态变化合法（不违反世界规则，不出现死者复活）；
- 重大 Canon 变化（死亡/复活、新世界规则、伏笔回收）已获作者批准；
- 连续性检查完成；
- SQLite 与文件一致（提交后重建校验通过）；
- 审计日志已写入；
- Git commit 成功，或失败原因被显式记录且不阻断已完成的文件写入（随后必须补提交）。

回滚要求：
- 有 operation ID；
- 有备份或 Git 可回退版本；
- 回滚本身进入审计日志；
- 回滚后重建索引。
