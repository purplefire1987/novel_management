# 旧系统 30 条命令的去向

V4.0 重建后，旧 CLI（`novel_management/cli.py`，1215 行）随旧公仓归档到
`99_Archive/legacy_v1/`。这里逐条记录它当初做什么、现在有没有对应实现、
以及为什么值得或不值得迁移。

**2026-11-22 重写**。上一版停在 0.11.0，把 `preflight`／`write`／`review`／
`commit-memory` 都记成「无」——那四条 0.11/0.12 已经实现，表比代码旧了三版。
现在按 0.13.0 的 36 条命令重新对一遍。

用途标记：

- **已覆盖** —— 新 CLI 有等价命令
- **该迁移** —— 日常真会用，V4.0 数据模型下仍然成立
- **不建议迁移** —— 语义与 V4.0 冲突，或已被 git / Agent 承担

| # | 命令 | 原来做什么 | 现状 | 判断 |
|---|---|---|---|---|
| 1 | `doctor` | 环境与配置体检 | `novel doctor` | 已覆盖 |
| 2 | `integrity` | 结构校验：单一事实源、双链、受控块、场景接缝、正文篇幅 | `novel validate` | 已覆盖。**「受控块残缺」「场景接缝」两项尚未接管** |
| 3 | `style` | 正文读数：篇幅区间 + 灌水四项（形容词密度、重复句、对话占比） | 无 | **该迁移**：写完一章就地验，是写作时的即时反馈，不是事后审计 |
| 4 | `rebuild-index` | Markdown → SQLite 索引 | `novel rebuild-index` | 已覆盖 |
| 5 | `index-md` | 生成 `Entity_Index.md` 全量索引页 | 无（用 Dataview / `.base`） | 不建议迁移：静态索引页会过期 |
| 6 | `status` | 章节与索引状态 | `novel status` | 已覆盖 |
| 7 | `preflight` | 写前资料包：相关实体、上章摘要、未回收伏笔、冲突检查 | `novel preflight`（0.11.0） | 已覆盖，八级预算 + FAIL/BLOCKED 比旧版更细 |
| 8 | `write` | 主笔生成章节草稿（写 staging） | `novel write`（0.12.0） | 已覆盖。旧版直接落盘，新版永不写 `01_Canon` |
| 9 | `review` | 总编审核草稿（列出问题清单） | `novel review submit\|status`（0.12.0）+ `novel note`（0.13.0） | 已覆盖。**结论落盘走 `note`**，不再只留在 runtime |
| 10 | `publish` | 作者确认后锁定正式正文 | `novel publish` + `novel release` | 已覆盖，语义更强（22 步 + 四道闸 + 契约校验） |
| 11 | `commit-memory` | 从正式正文提取记忆提案 | `novel proposal extract`（0.12.0） | 已覆盖 |
| 12 | `apply-memory` | 审批并应用记忆提案 | `novel proposal approve\|reject`（0.12.0） | 已覆盖。必经作者批准，Agent 不得代办 |
| 13 | `audit` | 写后连续性回归审计（时间线、称谓、已死角色出场） | 无 | **该迁移**：长篇小说最贵的错误是连续性断裂，而它只在几十章后才暴露 |
| 14 | `sign` | 给章节做连续性签字（绑正文 sha256） | 无 | **该迁移**：`publish` 校验内容指纹，但「审校通过」是独立声明，不是同一件事 |
| 15 | `sign-status` | 查看签字是否仍有效 | 无 | 与 14 成对，缺一个另一个没用 |
| 16 | `adopt` | 登记作者手改、推进基线 | `novel adopt` | 已覆盖。`adopt` 只接受 `--as author` |
| 17 | `rollback` | 按 operation_id 回滚一次正式写入 | `novel release rollback` | 部分覆盖：粒度是**整次发布**，不是单次操作。B 面：回退要连索引一起处理 |
| 18 | `search` | 全文检索章节正文 | `novel search`（0.6.0） | 已覆盖，三级检索比旧版全表 LIKE 准 |
| 19 | `find` | 按名称/别名/概要查实体档案（录入前必查） | 无（`search` 有名称路径，但入口不同） | **该迁移**：防重复建档的第一道闸，也是写作时最常用的查询 |
| 20 | `show` | 查看实体详情（摘要 + 关联 + 出处） | 无 | 该迁移，优先级低于 19 |
| 21 | `new` | 新建实体，ID 由系统生成 | `novel allocate-id` | 已覆盖（只发号，不建文件）。发号看现存最大号 + 审计历史最大号 |
| 22 | `ingest` | 录入原始 txt/md 稿件：切章 + 建大纲 + 建正文 + 批次提交 | `novel import scan\|report\|stage`（0.10.0） | 已覆盖 |
| 23 | `annotate` | 给已发布章节补「作者备注」结构化标注 | 无 | 该迁移，可先手工写 frontmatter 过渡 |
| 24 | `import-assets` | 按 profile 分类录入 Excel 资产库 | `novel import`（共用 profile 机制） | 已覆盖 |
| 25 | `import-lore` | 把 Markdown 设定稿按标题区间切分录入 | `novel import` | 已覆盖 |
| 26 | `history` | 仓库状态、提交历史、快照标签 | `git log` / `git tag` | 不建议迁移：git 已经做得更好 |
| 27 | `snapshot` | 给当前提交打快照标签 | `git tag` | 不建议迁移，同上 |
| 28 | `log` | 查看审计日志（谁在什么时候改了什么） | `novel status` + `<runtime>/logs/ops.jsonl` | 已覆盖。**缺 `log --limit` 这样的直接查询入口** |
| 29 | `export-schemas` | 把 Pydantic 模型导出为 JSON Schema | `novel schema list\|check`（`schemas/` 手写） | 不建议迁移：V4.0 的 schema 是手写契约，不是模型副产品 |
| 30 | `dice` | 骰子娘：`roll` / `check` / `duel` / `verify` / `log`，哈希链台账可复算 | 无 | **该迁移**：九角色名单里有骰子娘，但她目前没有任何工具可调 |

## 统计（30 条逐条对上，不重不漏）

- **已覆盖 18 条**：#1 #2 #4 #6 #7 #8 #9 #10 #11 #12 #16 #18 #21 #22 #24 #25 #28，
  加 #17（部分覆盖，记在下方）
- **仍缺 8 条**：#3 `style`、#13 `audit`、#14 `sign`、#15 `sign-status`、
  #19 `find`、#20 `show`、#23 `annotate`、#30 `dice`
- **不建议迁移 4 条**：#5 `index-md`、#26 `history`、#27 `snapshot`、#29 `export-schemas`

17 + 1 + 8 + 4 = 30

## 仍缺的 8 条，按「缺了会不会立刻卡住」排序

不是按代码复杂度排，是按「作者今天就会撞上吗」排：

1. **`find`** —— 写作时最常做的是「这个人叫什么、我之前写过哪些设定」。
   现在只能在 `search` 里搜，而 `search` 是全文检索，不是按 ID/别名精确定位。
2. **`audit`** —— 连续性断裂只在几十章后暴露。越晚做，修起来越贵。
3. **`sign` + `sign-status`** —— 「我看过了」目前没有任何地方记。
   `publish` 校验的是内容指纹，不是审校声明，两件事不能互相顶替。
4. **`style`** —— 篇幅与灌水四项是**写的时候就该看**的反馈，
   事后审计已经写完了，改不动。
5. **`dice`** —— 骰子娘在九角色名单里，没有工具可调，等于这个角色是空的。
6. **`show`** —— `cat` 也能看，但没有「关联 + 出处」的聚合。
7. **`annotate`** —— 可先手工写 frontmatter 过渡，不急。

## 两处「部分覆盖」要单独说

- **#2 `integrity`**：双链、ID、边界三项已接管；**「受控块残缺」与「场景接缝」没有**。
  受控块（`<!-- AUTO:NAME -->`）是生成器重跑时的保护，场景接缝是场景卡之间的连续性。
  两项都是旧系统里真会报错的东西，不该算「已覆盖」。
- **#17 `rollback`**：旧版按 `operation_id` 回滚单次写入，新版按整次发布回退。
  粒度变粗了。理由是新版一次发布是原子的（22 步、隔离快照、指针切换），
  拆成单操作回滚会破坏原子性假设；代价是「只想撤掉一份文件」也要走整次回退。

## 为什么不一次全迁

旧实现建立在三个 V4.0 已放弃的前提上：SQLite 索引（ADR-003 改为可重建的派生数据）、
`canon_status` 准入（ADR-002 改为「只认正文」）、Agent 直接写文件（ADR-006 改为 staging + 批准）。
照搬会把旧概念一起搬回来。每条都要按新模型重写，而不是复制。

## 0.13.0 关掉的一条

旧表的「部分覆盖」里记着 `review` 需要 `08_Reviews` 写通道（`known_risks` 的 R-03）。
0.13.0 已用 `novel note` 补上：不走 `publish`、不算签字基线，但有 schema 校验、
原子写、同名即拒。R-03 关闭。
