# 旧系统 30 条命令的去向

V4.0 重建后，旧 CLI（`novel_system/cli.py`，1215 行）随旧公仓归档到
`99_Archive/legacy_v1/`。这里逐条记录它当初做什么、现在有没有对应实现、
以及为什么值得或不值得迁移。

用途标记：

- **已覆盖** —— 新 CLI 有等价命令，不必迁移
- **该迁移** —— 日常真会用，V4.0 数据模型下仍然成立
- **不建议迁移** —— 语义与 V4.0 冲突，或已被 git / Agent 承担

| # | 命令 | 原来做什么 | 现状 | 判断 |
|---|---|---|---|---|
| 1 | `doctor` | 环境与配置体检 | `novel doctor` | 已覆盖 |
| 2 | `integrity` | 结构校验：单一事实源、双链、受控块、场景接缝、正文篇幅 | `novel validate`（双链/ID/边界） | 已覆盖，但「受控块残缺」「场景接缝」两项尚未接管 |
| 3 | `style` | 正文读数：篇幅区间 + 灌水四项（形容词密度、重复句、对话占比等） | 无 | **该迁移**：写完一章就地验，是写作时的即时反馈，不是事后审计 |
| 4 | `rebuild-index` | Markdown → SQLite 索引 | `novel rebuild-index`（JSON） | 已覆盖 |
| 5 | `index-md` | 生成 `Entity_Index.md` 全量索引页 | 无（V4.0 用 Dataview / `.base` 视图） | 不建议迁移：静态索引页与看板重复，且会过期 |
| 6 | `status` | 章节与索引状态 | `novel status` | 已覆盖 |
| 7 | `preflight` | 写前资料包：相关实体、上章摘要、未回收伏笔、冲突检查 | 无 | **该迁移**：这是「写作前该读什么」的答案，缺了主笔只能凭记忆写 |
| 8 | `write` | 主笔生成章节草稿（写 staging） | 无（V4.0 由 Agent 产出 + `novel stage` 落盘） | 不建议原样迁移：生成动作归 Agent，CLI 只管落盘与校验 |
| 9 | `review` | 总编审核草稿（列出问题清单） | 无 | 部分迁移：审核结论应写 `08_Reviews/`，需要一条写通道 |
| 10 | `publish` | 作者确认后锁定正式正文 | `novel publish`（四道闸，语义更强） | 已覆盖 |
| 11 | `commit-memory` | 从正式正文提取记忆提案 | 无 | **该迁移**：这是「正文 → 记忆」的唯一通道，缺了记忆永远是手写的 |
| 12 | `apply-memory` | 审批并应用记忆提案 | 无 | **该迁移**：与 11 成对，只做提取不做应用等于没闭环 |
| 13 | `audit` | 写后连续性回归审计（时间线、称谓、已死角色出场等） | 无 | **该迁移**：长篇小说最贵的错误是连续性断裂，而它只在几十章后才暴露 |
| 14 | `sign` | 给章节做连续性签字（绑正文 sha256） | 无 | 部分覆盖：`novel publish` 会校验内容指纹，但签字是「审校通过」的独立声明，不是同一件事 |
| 15 | `sign-status` | 查看签字是否仍有效 | 无 | 与 14 成对 |
| 16 | `adopt` | 登记作者手改、推进基线 | `novel adopt` | 已覆盖 |
| 17 | `rollback` | 按 operation_id 回滚一次正式写入 | 无（有 `backup` / `restore`，粒度是整库） | **该迁移**：整库回滚代价太大，按操作回滚才是日常需要的粒度 |
| 18 | `search` | 全文检索章节正文 | 无 | **该迁移**：写到 80 万字以后，「这句话我在哪章写过」只能靠它 |
| 19 | `find` | 按名称/别名/概要查实体档案（录入前必查） | 无 | **该迁移**：防重复建档的第一道闸，也是写作时最常用的查询 |
| 20 | `show` | 查看实体详情（摘要 + 关联 + 出处） | 无（`cat` 也能看，但没有聚合） | 该迁移，优先级低于 19 |
| 21 | `new` | 新建实体，ID 由系统生成 | **`novel allocate-id`**（0.5.0 起） | 已覆盖：只发号，不建文件 |
| 22 | `ingest` | 录入原始 txt/md 稿件：切章 + 建大纲 + 建正文 + 批次提交 | 无 | **该迁移**：旧稿入库的唯一入口 |
| 23 | `annotate` | 给已发布章节补「作者备注」结构化标注 | 无 | 该迁移，但可先手工写 frontmatter 过渡 |
| 24 | `import-assets` | 按 profile 分类录入 Excel 资产库（人物/势力/物品/地点/生物） | 无 | **该迁移**：966 份 Canon 就是这么来的，没有它就没法再导入 |
| 25 | `import-lore` | 把 Markdown 设定稿按标题区间切分录入 | 无 | 该迁移，与 24 共用 profile 机制 |
| 26 | `history` | 仓库状态、提交历史、快照标签 | `git log` / `git tag` | 不建议迁移：git 已经做得更好 |
| 27 | `snapshot` | 给当前提交打快照标签 | `git tag` | 不建议迁移，同上 |
| 28 | `log` | 查看审计日志（谁在什么时候改了什么） | `novel status`（最近操作）+ `<runtime>/logs/ops.jsonl` | 已覆盖，但缺 `log --limit` 这样的直接查询入口 |
| 29 | `export-schemas` | 把 Pydantic 模型导出为 JSON Schema | 无（`schemas/` 七份手写） | 不建议迁移：V4.0 的 schema 是手写契约，不是模型副产品 |
| 30 | `dice` | 骰子娘：`roll` / `check` / `duel` / `verify` / `log`，哈希链台账可复算 | 无 | **该迁移**：九 Agent 名单里的骰子娘目前没有任何工具可调 |

## 统计（30 条逐条对上，不重不漏）

- **该迁移 14 条**：#3 style、#7 preflight、#11 commit-memory、#12 apply-memory、#13 audit、
  #17 rollback、#18 search、#19 find、#20 show、#22 ingest、#23 annotate、
  #24 import-assets、#25 import-lore、#30 dice
- **已覆盖 8 条**：#1 doctor、#2 integrity、#4 rebuild-index、#6 status、#10 publish、#16 adopt、
  #21 new（`allocate-id`，0.5.0 起；只发号不建文件）、#28 log
- **部分覆盖 3 条**：#9 review（需要 `08_Reviews/` 写通道）、#14 sign、#15 sign-status
  （与 `publish` 的内容指纹校验部分重叠，但「审校通过」是独立声明，不能等同）
- **不建议迁移 5 条**：#5 index-md、#8 write（生成动作归 Agent）、#26 history、#27 snapshot
  （git 已做得更好）、#29 export-schemas

8 + 14 + 3 + 5 = 30（#21 `new` 已由 `allocate-id` 覆盖，从「该迁移」移入「已覆盖」）

## 迁移顺序建议

按「缺了会立刻卡住」排序，不按代码复杂度：

1. ~~**`new`**~~ —— **已有 `novel allocate-id`**（0.5.0）。取号同时看现存最大号与审计
   历史最大号，本前缀有重号直接拒绝，锁文件防并发。手抄 ID 撞号（`ASSET-0001` 就是这样来的）
   这条路已经堵住。**`find` 仍缺**——按 ID 反查文件目前只能 grep。
2. **`search`** —— 只读、无写入风险，纯收益。
3. **`preflight`** —— 主笔写每章前都要用。
4. **`commit-memory` + `apply-memory`** —— 记忆闭环。
5. **`import-assets` + `import-lore`** —— 只有再导入旧稿时才需要，但需要时绕不过去。
6. 其余按需。

## 为什么不一次全迁

旧实现建立在三个 V4.0 已放弃的前提上：SQLite 索引（ADR-003 改为可重建的派生数据）、
`canon_status` 准入（ADR-002 改为「只认正文」）、Agent 直接写文件（ADR-006 改为 staging + 批准）。
照搬会把旧概念一起搬回来。每条都要按新模型重写，而不是复制。
