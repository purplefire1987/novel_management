# 质量门清单

系统级的八条硬规则。各工作流可以在 `workflows/<name>/workflow.yaml` 的
`quality_gates` 字段上追加更严的条件，但不能放宽这里任何一条。

**这份文件的来历**：原先在仓库根的 `quality_gates/baseline.yaml`，是一份 Markdown
文件里嵌着一段 YAML 代码块，没有任何代码读它，规则里的路径（`01_Canon/`、
`03_Manuscripts/Drafts`）又全是 V4.0 的。它被清掉是因为「没人引用的孤岛文件」，
不是因为规则失效——规则本身还成立，所以搬到这里，并逐条标出实现位置。

判据是「这条规则现在由什么拦住」。标 **未实现** 的条目是真的没有对应物，
不是没找到。

| 规则 | 含义 | 实现位置 |
|---|---|---|
| `no_unapproved_canon_write` | 写入正式区必须附作者批准记录 | `novel release prepare` → `approve release` → `publish`；批准绑定内容哈希（`package-hash`） |
| `source_required` | 每条新增事实必须带来源 | 契约的 `source_refs`（`_common.schema.json#/$defs/sourceRefList` 判别联合）；`release prepare` 对新增产物要求可定位来源 |
| `no_scope_escape` | 写入路径必须落在任务声明的 `output_scope` 内 | `tasks.output_scope` + `novel stage` 的落点校验（`paths.py` 的 `ensure_inside`） |
| `index_rebuildable` | 删掉索引后必须能完整重建，且不影响事实源 | `novel rebuild-index`；SQLite 在 `<runtime>/index/`，刻意不在 Vault 内 |
| `draft_not_canon` | 草稿与修订稿不得被当作事实引用 | Canon 只认「已落在正文里」；`canon_status` 已不承担准入含义（见 `docs/architecture/adr/`） |
| `signoff_binds_content` | 连续性签字绑定正文 sha256，正文改动即失效 | **未实现**。现在只有 `novel continuity check`（跑检查出报告），没有「签字」这一步，也就没有绑定与失效。见下方说明 |
| `conflict_reported` | 发现冲突必须单列报告，不得静默择一 | ID 查重分级（`formal` 两份才 FAIL、牵连归档 WARN）；`novel issue` / `novel change` / `novel impact` |
| `backup_before_destructive` | 破坏性操作前必须有可用备份与回滚命令 | `novel backup`（含清单与回读校验）；`novel restore` 默认预演；`--prune` 默认预演 |

## 关于 `signoff_binds_content` 未实现

这条要求的是「作者对某次连续性检查结果签字，签字绑定当时正文的 sha256；
正文一改，签字自动失效，必须重签」。它解决的是一个真问题：**连续性结论会过期**，
而报告文件本身不会自己说自己过期了。

现状缺三件东西：

1. 没有签字命令，`continuity check` 只产出报告，不记录「谁在哪个正文版本上认了这份报告」。
2. 报告里没有正文 `sha256`，所以无法判断它对应哪一版。
3. 没有失效机制，正文改动后旧报告仍原样留在 `07_审核与报告/`。

在补上之前，判断一份连续性报告是否仍然有效只能靠人工看时间戳——这正是这条规则
想消除的。记在这里是为了不让「清单上有、代码里没有」这件事继续隐身。
