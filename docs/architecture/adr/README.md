# 架构决策记录（ADR）

每篇 ADR 记录**一个**已经做过的取舍：背景、问题、决策、备选方案、影响、状态、批准人、日期。
它的用途不是存档结论，而是让后来的人知道「为什么不能随便改这里」。

规则：

- 已接受的 ADR 不改结论。要改就写新的一篇，并在两篇之间标注取代关系。
- 决策与代码冲突时，先确认是哪一边错了，不要默默改代码。
- 新增决策涉及三处目录边界、事实源、写入关口、Agent 权限、备份策略、许可证时，必须先有 ADR。

| 编号 | 决策 | 状态 |
|---|---|---|
| [ADR-001](ADR-001-public-vault-separation.md) | 公共仓库与私人 Vault 物理隔离 | 已接受 |
| [ADR-002](ADR-002-markdown-as-source-of-truth.md) | Markdown / YAML 为 Canon 事实源 | 已接受 |
| [ADR-003](ADR-003-index-is-derived.md) | 索引可重建且不是事实源 | 已接受 |
| [ADR-004](ADR-004-one-vault-many-projects.md) | 一个作者一个 Vault，多项目隔离 | 已接受 |
| [ADR-005](ADR-005-private-data-stays-local.md) | 私人数据本地保存，不配置私人远端 | 已接受 |
| [ADR-006](ADR-006-staging-then-author-approval.md) | Agent 默认写 staging，作者最终批准 | 已接受 |
| [ADR-007](ADR-007-config-resolved-paths.md) | Python CLI 一律通过配置解析路径 | 已接受 |
| [ADR-008](ADR-008-workbuddy-plugin-registration.md) | WorkBuddy 插件注册与升级策略 | 已接受 |
| [ADR-009](ADR-009-backup-and-restore.md) | 备份与恢复策略 | 已接受 |
| [ADR-010](ADR-010-license-and-governance.md) | 公共仓库的许可证与贡献治理 | 已接受（GPL-3.0-only） |
| [ADR-011](ADR-011-schema-as-contract.md) | 字段契约以 JSON Schema 为真源，校验器自实现 | 已接受 |
| [ADR-012](ADR-012-sqlite-index-and-cjk-search.md) | 索引改用 SQLite；中文检索按长度分派 | 已接受（部分取代 003） |
| [ADR-013](ADR-013-event-dag-and-state-intervals.md) | 时间线用事件 DAG；状态按区间；认知与事实分表 | 已接受 |
| [ADR-014](ADR-014-approval-binds-bytes.md) | 批准绑定对象与当前字节；BLOCKER 不可豁免；RETCON 独立审批 | 已接受（细化 006） |

## 这些 ADR 里最重要的三句话

1. **隔离靠目录结构，不靠 `.gitignore`**（ADR-001）。忽略规则会在改名时失效，且失效时不会报错。
2. **批准绑定内容，不绑定任务**（ADR-006）。批的是 A、发的是 B，在 sha256 这一层就不成立。
3. **索引可以随时删除**（ADR-003）。删不掉的「可重建物」其实是第二事实源。

## 取代关系

- **ADR-012 部分取代 ADR-003**：载体从 `index.json` 换成 SQLite + 多 build 指针。
  ADR-003 的结论（索引纯派生、可随时删、不反向写事实源）**依然成立**，被换掉的只有那一个文件格式。
