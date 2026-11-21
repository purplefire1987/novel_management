# 架构：三条边界的物理隔离

## 决策

系统由三个互不嵌套的目录组成：

```
<public_repo>    公共代码与模板，可推 GitHub
<author_vault>   作者私人事实源，不配远端，不推
<runtime>        索引、缓存、日志、staging，可随时删除重建
```

三者不得互为父子。Vault 不在公仓内，公仓也不在 Vault 内。符号链接不得跨越三者。

## 为什么不用 `.gitignore` 隔离

`.gitignore` 是第二道防线，不是边界。历史教训：曾用「公仓在库根、私人库放子目录 + gitignore 排除」的形态，
一次目录改名后清洗清单没跟着改，3519 个文件在公网挂了三天。目录隔离是结构约束，gitignore 只是形式约束。

## 事实源优先级

| 数据 | 权威来源 |
|---|---|
| 设定、人物、地点、组织 | Vault 内 Markdown/YAML |
| 规划与正文 | Vault 内文件，状态由 frontmatter 标明 |
| 索引 | Runtime 内，可删除重建 |
| Agent 任务状态 | Runtime / 任务记录，不等于 Canon |

冲突时以作者确认过的 Markdown/YAML 为准。索引不得反向覆盖事实源。

## 路径解析

CLI 不从代码里取路径，一律走 `novel_system.config.load()`：

优先级：CLI 参数 → 环境变量 → 用户级配置 → Vault 级配置 → 仓库默认值。

所有写入路径经 `ensure_inside(child, vault)` 校验，`..` 与符号链接逃逸一律拒绝。

## 写入链路：为什么提案只能落在 staging

一项内容从 Agent 手里到正式区，必须经过六个物理位置：

```
Agent 产出            →  07_Staging/<TASK-ID>/<分栏>/...   （stage）
作者看 diff           →  只在终端显示，不写盘             （diff）
作者批准字节          →  <runtime>/approvals/APR-*.json    （approve，绑 sha256）
得到发布计划          →  <runtime>/releases/REL-*/plan.json （release prepare）
作者批准计划          →  同上，另一笔 APR 记录             （approve release，绑计划哈希）
发布                  →  01_Canon / 03_Manuscripts/...     （publish / release execute，旧版先归档）
```

发布本身落四份记录 + 一个指针：

```
<runtime>/releases/<REL>/plan.json      作者批准的「要发什么」（可变，重准备即换哈希）
<runtime>/releases/<REL>/snapshot/      隔离工作区：完整候选内容 + 一次提交
<runtime>/releases/<REL>/manifest.json  构建后的不可变事实（逐文件 sha256）
<runtime>/releases/<REL>/journal.json   22 步事务日志（崩在哪、还能不能续）
<runtime>/releases/active.json          正式指针：**只有它决定正式版是哪一版**
<runtime>/receipts/RCPT-*.json          切换成功的凭证（可补写，带 repaired 标记）
```

每道关口拦的是不同的事：

| 关口 | 拦什么 | 实现位置 |
|---|---|---|
| `stage` | 目标不在正式区、路径越界、无任务 | `staging/` |
| `approve` | 非作者代办、带着冲突硬批 | `audit/ensure_actor` + `cli/flow.py` |
| `release prepare` | 没有产物批准、有 OPEN BLOCKER、未批准 RETCON 覆盖、计划说不出批什么 | `release/gates.py` |
| `release execute` | 批准后被换内容、基线变了、有人正在发布、Canon 缺二次确认 | `release/engine.py` + `release/lock.py` |
| `adopt` | 把作者手改当脏数据回滚 | `baseline/` |

关键设计：**批准绑定内容而不是绑定任务**。批准记录里存每个文件当时的 sha256，发布时逐个重算比对——
「批的是 A，发的是 B」在哈希这一层就不成立。同时 `stage` 会记下目标正式文件当时的 `base_sha256`，
所以「提案生成之后作者又改过正式文件」能被识别为冲突，而不是被静默覆盖。

## 未采纳的方案

- **公仓内嵌私人目录 + gitignore**：已被证明不可靠，见上。
- **一个 Vault 只放一部小说**：作者可能写多部，改为 `10_Projects/<PROJECT_ID>_<slug>/`。
- **SQLite 作为事实源**：索引会损坏、会与 Markdown 不一致，且不可人工阅读。仅作加速层。
