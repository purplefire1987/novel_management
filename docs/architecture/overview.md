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
| 原稿（导入的旧稿） | Vault 归档区 `90_Archive/Imports/<IMP>/raw/`，逐份 sha256 记在清单里 |
| 索引 | Runtime 内，可删除重建 |
| Agent 任务状态 | Runtime / 任务记录，不等于 Canon |

冲突时以作者确认过的 Markdown/YAML 为准。索引不得反向覆盖事实源。

**原稿不是 Canon**。它进的是归档区（不在正式区六个根目录里），
引用它的是「定位符 `文件名#L起-L止`」而不是章号——章号是解析结果，会随切分规则变。

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

## 导入链路：原稿怎么进这套关口

旧稿导入不是捷径，只是把「生成提案」换成了「从原稿切出来」：

```
原稿文件            →  90_Archive/Imports/<IMP>/raw/     （import scan，写后回读 sha256）
解析与提取          →  <runtime>/imports/<IMP>/           （manifest.json + detail.json）
某批章节            →  07_Staging/<TASK-ID>/<分栏>/...    （import stage）
之后                →  与 staged 内容完全相同：approve → release prepare → approve release → execute
```

- **scan 一行正式区都不碰**；`stage` 只写 staging；发布仍是 T06 的 22 步。
- 台账 `<project>/05_Ledgers/Imports/<IMP>.md` 覆盖**整个导入计划**：
  已发号的写 CH 号，未发的写「尚未发号」。分批发布时读它才知道还差哪几章。
- 各批已发的号记在 `<runtime>/imports/<IMP>/assignments.json`，
  重跑同一批复用原号；换切分方式直接拒绝（已发的号会与章对不上）。
- **ID 查重按区域分级**：正式区两份才算撞号，staging 那份是同一对象在途。
  不分级的后果是发布过一次就再也发不出号。

## 未采纳的方案

- **公仓内嵌私人目录 + gitignore**：已被证明不可靠，见上。
- **一个 Vault 只放一部小说**：作者可能写多部，改为 `10_Projects/<PROJECT_ID>_<slug>/`。
- **SQLite 作为事实源**：索引会损坏、会与 Markdown 不一致，且不可人工阅读。仅作加速层。
