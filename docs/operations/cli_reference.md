# CLI 参考

对应 V4.0 §5.3 的命令集，另加 3 条地图命令（§10）、T03 的 `rebuild-index`/`search`、
T06 的 `release`、T07 的 `import`、T08 的 `preflight`/`agent`，以及 T09/T10 的
`plan` / `write` / `review` / `proposal` / `deps`，以及 V1.0 对齐时补的
`idea` / `entity` / `relation` / `event` / `continuity` / `workflow` / `project`。
顶层命令共 **43** 条
（`task` / `maps` / `schema` / `release` / `import` / `agent` / `plan` / `review` /
`proposal` / `deps` / `entity` / `relation` / `event` / `workflow` / `project` / `idea` /
`continuity` 是命令组，各自还有子命令）。
全部命令支持 `--vault` / `--runtime` / `--project` / `--log-level`，位置可放在子命令之前或之后。

```bash
novel --vault ~/vault status       # 等价
novel status --vault ~/vault
```

两处都写时**后者生效**（与普通命令行参数一致）。嵌套子命令（`task create`、`maps paths`、`schema list|check`）同样两种位置都收。

> 前置写法在 0.5.0 及更早版本**实际无效**：argparse 解析子命令时会新开一个 namespace，
> 解析完把它的属性整体搬回主 namespace，子命令的默认值 `None` 就把前面给的路径盖掉了。
> 表现为「未配置作者 Vault」，或者更糟——同时有 `~/.config/novel-system/config.yaml`
> 时静默作用在配置里那个库上。已在 0.5.1 修复。

未给 `--project` 时：配置里有 `active_project.project_id` 就用它；没有则报错并列出候选，**绝不按「只有一个项目」去推断**。
`novel init-project` 建第一个项目时会自动写入 `active_project`，所以日常不必每次带 `--project`。

### 运行日志

每次命令跑完都会在 `<runtime>/logs/novel.log` 留一对记录：

```
2026-11-20 14:14:52 INFO  novel.cli | start cmd=status project=novel-001 vault=/path/to/vault
2026-11-20 14:14:52 INFO  novel.cli | done cmd=status rc=0 elapsed=0.015s
```

- `--log-level` 取 `DEBUG` / `INFO`（默认）/ `WARNING` / `ERROR` / `OFF`。
- 想在终端同时看见，设 `NOVEL_LOG_CONSOLE=1`；走 **stderr**，不污染 stdout。
- 日志写不进去（Runtime 只读、盘满、路径被普通文件占住）会静默降级，**不会让命令失败**。
- 这是运行日志，和 `audit/` 的审计日志（`ops.jsonl`，记谁改了哪个正式文件）是两回事：前者可丢，后者是证据。

---

## 1. 只读命令

### `novel doctor`

检查项：Python 版本、PyYAML、**SQLite 版本与 FTS5 `trigram` 可用性**、Vault 与 Runtime 是否存在、Runtime 是否被放进 Vault、公仓与 Vault 是否互相包含、四个隐私开关是否为 false。

SQLite 那段会明说 trigram 能不能用：不能用时索引退化为 `LIKE` 子串扫描，
结果仍然正确、但大库上慢——**不会**偷偷换成对中文无效的 `unicode61`。
PyYAML 未安装时同样明说：遇到嵌套 frontmatter（`source_refs`、`aliases`）会直接报错并跳过该文件。

Runtime 存在时顺带补建 `index/logs/tasks/approvals/backups` 五个子目录。

返回 1 表示有硬问题；隐私开关被打开、Runtime 在 Vault 内、公仓与 Vault 互相包含都算硬问题。

### `novel status`

Vault 路径、项目列表（含 Markdown 计数）、索引（库文件名 `builds/<id>.sqlite`、产物数/块数、`fts=` 模式、依据的 revision、历史 build 份数）、任务总数与未结数、等待作者批准的任务、备份数量与最近一次是否 verified、最近 5 条操作。

### `novel validate [--strict]`

四组检查：

1. **结构**：`00_System/`、`10_Projects/` 是否存在。
2. **项目元数据**：`00_Project/project.yaml` 是否存在、`project_id` 是否为空或重复、`project_subdirs` 是否齐全（缺子目录算警告）。
3. **路径越界**：Vault 与项目目录是否都在 Vault 内。
4. **实体与链接**（逐项目）：
   - **Canon 六类 + `04_Story_Assets`** 下所有带 `id` 的文件的 ID 是否重复（重复算失败）。
     素材虽然按 `Battles/` `Settings/` 等分类分目录存放，ID 却是「全库连续编号，不按分类分段」，
     因此查重必须**跨子目录一起看**；模板里的 `id: {{id}}` 是占位符，不算身份、不参与查重。
   - `id` 与文件名前缀是否错位（如 `CHAR-0001_示例名.md` 里写 `id: LOC-0001`），算警告；
   - `[[双链]]` 是否可解析。解析范围是全 Vault 的文件名（Obsidian 语义），支持 `.md` / `.canvas` / `.base`，剥离 `#标题`；
   - **被检查的文件也是全 Vault**：`00_System`（看板、工作台）、`90_Shared_Assets`、
     `90_Archive` 都在范围内，只排除 `99_Archive/legacy_v1/` 与隐藏目录。
     历史区（`08_Reviews`、`90_Archive`）的断链**单独列出、不计失败**——
     那里保留当时编号，指向旧名在重编号后必然断，属设计而非缺陷。
     （0.14.0 之前只扫项目根且整个跳过 `90_Archive`，曾让 28 条真断链藏了三天。）
   - 指向别项目的 `[[10_Projects/other/...]]` 算失败。

`--strict` 把断链与缺子目录等警告升级为失败，适合提交前跑。

### `novel task inspect <TASK-ID> [--json]`

任务字段、staging 产物清单（目标路径 + sha256 + 时间）、最近一次批准记录。

### `novel diff --task <TASK-ID> [--path <目标>] [--full]`

逐条比对 staging 与正式文件，输出 `difflib` 统一 diff。判定四种状态：

| 状态 | 含义 |
|---|---|
| `新增` | 正式文件不存在 |
| `改动` | 将替换正式文件 |
| `一致` | 与正式文件内容相同 |
| `冲突` | staging 生成后正式文件被改过（通常是作者手改） |

有冲突时返回 1。默认每个文件最多显示 120 行差异，`--full` 不截断。

---

## 2. 初始化

### `novel init-author [--author-id ID] --confirm`

目标目录非空则拒绝。建 `00_System/{Dashboards,Policies}`、`10_Projects`、`90_Shared_Assets`、`99_Archive`，写 `Vault_Config.yaml`、`Author_Profile.md`、`Project_Registry.md`，并创建 Runtime 的九个运行态子目录。

### `novel init-project --id <ID> --name <名字> --confirm`

`project_id` 只允许小写字母、数字、连字符。建 §4.2 的完整目录树，含 `07_Staging/` 九个分栏与 `08_Reviews/{editorial,author}`。写 `00_Project/project.yaml`，并在 `Project_Registry.md` 追加一行。

---

## 3. 索引与检索

### `novel rebuild-index [--canon-revision <commit>]`

扫 Vault 内全部 Markdown，写 `<runtime>/index/builds/<build_id>.sqlite`，
然后把 `index/active.json` 指针切过去。

**排除的目录**：项目内 `07_Staging` / `08_Reviews` / `90_Archive`，以及 Vault 根归档
（名字从 `Vault_Config.yaml` 的 `layouts.archive` 取，当前是 `99_Archive`）。
输出里会打「跳过运行态/归档 N 份（目录清单）」——跳过什么必须看得见。

- **不原地重建**：每次写一个新文件，全程不覆盖已存在的库。读者要么拿到旧指针、
  要么拿到新指针，没有中间态。崩溃留下的半成品只是多一个没人指向的 `.sqlite`。
- **旧 build 不会自动清**（R-18）。要腾空间就手动删 `index/builds/` 下不要的，
  或者整个删掉 `index/` 重跑——索引是派生物，删了永远安全。
- `--canon-revision <commit>` 从 `git archive` 直读该 commit 的 Markdown，
  **不落临时目录、不切工作树**，也不碰 `00_Canon` 里的任何文件。
  不给时读工作树，并把当前 HEAD（如果 Vault 自己是 git 仓库）记进索引。

17 张表照 PRD §16.1 建，**当前 14 张会填**：`index_metadata` `artifacts` `entities`
`aliases` `source_refs` `narrative_order` `blocks` `fts_blocks`，加 T04 的 `events`
`event_edges` `assertions` `state_changes` `knowledge` `revelations`。

剩下 3 张是空表：`foreshadowing`（伏笔契约待 T09）、`dependencies`（T09 之后）、
`issues`（**问题的权威副本在 runtime，不进索引**——见第 7 节）。

「建了空表」不等于「已支持」，也**不等于「表里有数据」**：真库六个语义表里
只有 `events` 有 7 行，其余五张是 0 行。输出里把「会填的表」「空表」「实际行数」
分开打，不合并成一句「已支持」。

坏文件（非 UTF-8、非法 YAML、没装 PyYAML 而 frontmatter 有嵌套）逐份记入 `失败` 计数后继续，
不会中断整次重建。

索引可随时删除重建，不反向修改任何事实源。

### `novel search <查询> [--limit N] [--json] [--show-index]`

按查询形态分三条路：

| 查询 | 路径 | 说明 |
|---|---|---|
| 完整 ID（`CHAR-0001`）或别名 | 精确查 | 命中档案没有正文块时也会返回一条「ID 命中」，不是空结果 |
| ≥ 3 字 | FTS5 `trigram` | `握住了刀柄` 这类长词走这里 |
| < 3 字 | `LIKE` 子串 | 两字的人名、地名走这里——trigram 的窗口是三字符，两字切不出 token |

正文块搜不到时还会再走一次**名称路径**（frontmatter 的 `name`/`aliases`），
此时 `mode=name` 并注明「正文里没有这个词；命中的是实体名或别名」。
实体名通常正文里也写着，这一层真正救的是**描述性别名**——实测 136 个别名里
有 35 个在正文中一次都不出现。

- `--limit N` 默认 20；
- `--json` 输出 `{query, mode, hits[]}`，每条带 `path` / `block_id` / `line_start` / `text`；
- `--show-index` 顺带打印用的是哪一份 build；
- **结果分层**：`10_Projects/` 的作品内容排最前，其次 `90_Shared_Assets/`，
  最后是 `00_System/` 下的系统文档与生成看板。生成看板是从档案派生的，
  让派生页排在事实源前面，等于让人引用摘要去改原文（真实库上搜一个两字人名，
  前三名全是 `工作台.md` 摘录、`10_Projects/` 下那份人物档案看不见）；
- 没命中退出码 **1**，还没建索引退出码 **2**（`2` 是「你得先 rebuild-index」，
  不是「搜不到」，脚本里别混用）。

FTS5 `trigram` 不可用时**不会**改用英文分词顶上：全部走 `LIKE`，结果对但慢，
`doctor` 和 `rebuild-index` 都会明说。`unicode61` 那种「看起来能用、实际检不出中文」的路子被明确排除。

---

## 4. 时间线与认知

### `novel state <实体ID> [--field <字段>] [--after <事件>|--before <事件>] [--json]`

查**客观**状态：某实体在某时点的字段值。只读 `assertions` 与 `state_changes`。

```bash
novel state CHAR-0001 --field location --after EVT-0007
novel state CHAR-0001 --json
```

返回五个值之一，含义不合并：

| 返回 | 含义 | 该怎么补 |
|---|---|---|
| `RESOLVED` | 唯一确定 | —— |
| `NOT_RECORDED` | 没有记录 | 补记录。**这不等于「没有」或「不知道」** |
| `AMBIGUOUS` | 有记录但结论不唯一 | 看输出的多条候选 |
| `UNORDERED` | 涉及事件排不出先后 | 补 `event_edges`，不要按 ID 猜 |
| `DISPUTED` | 记录被标为争议 | 先处理争议 |

区间是 `[since, until)`：`--after X` 把 X 本身算在内（开始事件即生效），
`--before X` 把 X 本身排除（失效事件即失效）。没绑事件的记录视为恒成立。

退出码：`RESOLVED` → 0；其余 → **1**（「查不到唯一答案」不是错误，是答案）。

### `novel knowledge <角色ID> [--about <命题>] [--after <事件>] [--json]`

查**人物认知**：谁在什么时候知道/相信/怀疑/误信了什么。读 `knowledge` 表，
带 `attitude` 前缀（如 `BELIEVES:父亲已死`）。

`NOT_RECORDED` 的含义在这里尤其重要：查不到只说明「没记他知不知道」，
不能推断成「他不知道」。

### `novel reveal <命题> [--json]`

查某个真相是在哪一章、以什么方式揭开的（`revelations` 表）。输出形如
`CH-0003/S02#p0012`，可直接回跳正文。

**当前真库的实际数据**（2026-11-21 现数）：事件 **7** 个（在 `05_Ledgers/Events/`，
复合 ID 形如 `EVT-CH-0001-001`）、**边 0 条**；断言、状态变化、认知、揭示各 **0** 行。

所以 `state` / `knowledge` / `reveal` 现在一律返回 `NOT_RECORDED`；一旦有记录绑定了
两个事件，因为没有任何先后关系，会返回 `UNORDERED` 并提示「事件图里 0 条先后关系」。
**命令能用，数据没有——两者不要混为一谈。**

---

## 5. 任务

### `novel task create --type <类型> [--assignee <agent>] [--mode <模式>] [--scope <前缀>] [--input <文件>] [--note <备注>] [--as <操作者>]`

类型：`outline` `preflight` `draft` `revision` `review` `memory_update` `ingest` `simulation` `release`。
模式：`read_only` `staging` `draft` `proposal`。

首行输出 `TASK-000001`，便于脚本取用。`read_only` 任务不允许 `stage`。

状态流转固定：`pending → in_progress → {blocked, awaiting_author, done, cancelled}`；跳步被拒绝。

---

## 6. 提案与批准

### `novel stage --task <ID> --from <文件|-> --to <项目内正式路径> [--note] [--replace] [--as]`

- `--from -` 从 stdin 读。
- 产物落 `07_Staging/<TASK-ID>/<分栏>/<目标路径>`，分栏由任务类型决定（draft/revision→drafts，review→reviews，memory_update→memory，其余见 `staging/AREA_BY_TASK`）。
- `--to` 必须落在六个正式区之一（Canon / Planning / Manuscripts / Story_Assets / Ledgers / Memory），否则拒绝。`00_Project`、`07_Staging`、`90_Archive` 不是合法目标。
- 同一任务内同名目标已存在时拒绝；确需替换加 `--replace`。
- 每次 stage 都记下目标正式文件当时的 sha256（`base_sha256`），供后续判冲突。

### `novel inspect <TASK-ID|TASK-ID:路径> [--json]`

批准之前要看的七项，一次给全：

1. 候选清单与每个文件**此刻**的 sha256；
2. staging 与磁盘的 `drift`（stage 之后被直接改过的文件）；
3. 这笔候选当前的批准状态（`NONE` / `VALID` / `STALE`）；
4. 指向它（任务 ID 或产物 ID）的 OPEN `BLOCKER`；
5. 覆盖它、但还没批准的 RETCON；
6. 变更范围（落哪几个正式区）；
7. 风险等级（`HIGH` / `MEDIUM` / `LOW`）。

**哈希按磁盘现算，不读 manifest。** manifest 是 stage 那一刻的记录，之后有人直接
改了 staging 里的文件它就过期了；拿过期哈希去批，批的是一份此刻已经不存在的字节。

### `novel approve artifact|proposal|release|change <对象> --hash <sha256> [--note] [--as author]`

四类对象各绑不同的东西：

| 子命令 | 绑什么 |
|---|---|
| `artifact` | 候选文件的 sha256（多个文件时是整包哈希） |
| `proposal` | 提案文件的 sha256 |
| `release` | 发布计划的哈希 |
| `change` | **影响报告的哈希**（见下一节） |

- 非 `author` 一律拒绝（退出码 3）；
- `--hash` 必填，且必须等于 `inspect` 报出的当前值——不接受「大致同意」；
- 批准后**改一个字节就失效**，没有「非实质修改」白名单；
- 文件消失也算失效（取不到当前哈希 ≠ 没改动）。

### `novel approve text --task <ID> --chapter <CH> [--note] [--as author]`

第五类对象：**正文本身**（PRD §14.5）。与上面四类的区别是它没有候选 ID 可给——
正文的「候选」就是这一章当前的草稿，所以用 `--task` + `--chapter` 定位，
哈希由程序按磁盘现算，**不接受命令行传入**。

```bash
$ novel approve text --task TASK-000004 --chapter CH-0001 --as author
CH-0001 还没有审核记录。先 `novel review --task TASK-000004 --chapter CH-0001 --as editor --verdict approve`

$ novel approve text --task TASK-000004 --chapter CH-0001 --as editor
正文批准只能由作者做（收到 'editor'）
```

递进条件全满足才写记录：

1. `review status` 的结论是 `APPROVE`（不是 `PARTIAL`，更不是 `REVISE`）；
2. 按风险等级该过的角色都过了（LOW：编辑；MEDIUM：+世界架构师；HIGH：+总编）；
3. 这一章没有 OPEN 的 `BLOCKER`。

批准记录额外带 `scope: CHAPTER_TEXT` 与整份文件字节的 sha256。**它不是确认弹窗**：
上面三条少一条就抛 `ApprovalError`，没有 `--force`。`proposal extract` 会回来核这个哈希，
所以「批完又改了两个字再提取」会被拦在提取那一步。

### `novel approve --task <ID> [--note] [--reject] [--force] [--as author]`

旧入口，保留兼容：它绑的同样是每个 staged 文件的内容指纹，`publish` 照它校验。
新内容应当用上面带对象类型的入口——旧入口说不出「批的是哪个对象」。

- 批准前先跑一遍 diff：存在冲突则拒绝，除非 `--force`（等于作者确认「以 staging 为准，覆盖我的手改」）。
- 批准记录写入 `<runtime>/approvals/APR-00000N.json`，绑定每个 staged 文件的 `target` / `staged` / `sha256` / `bytes`。
- `--reject` 记一条驳回，任务 `approval_state` 变为 `rejected`。
- 重新批准会生成新记录，旧记录保留。

### `novel publish --task <ID> --confirm [--confirm-canon] [--release <REL>] [--as author]`

**自 0.9.0 起，`publish` 只执行「已准备且已批准」的计划**，不再临时生成一份：

```bash
novel release prepare --task TASK-000007 --as author        # 1) 生成计划
novel approve release REL-000001 --hash <计划哈希> --as author   # 2) 批计划
novel publish --task TASK-000007 --as author --confirm     # 3) 执行
```

找不到计划时报错并给出这三步（退出码 2）。任务已 `done` 或 `cancelled` 时拒绝。

执行前后共六道闸：

1. 有 `decision=approved` 的产物批准记录；
2. staging 里每个批准过的文件，当前 sha256 与批准时一致；
3. staging 里没有批准范围外的新增文件；
4. 目标上没有 OPEN 的 `BLOCKER`（记在任务 ID 或产物 ID 上都算）；
5. 目标上没有「未批准的 RETCON」覆盖；
6. 目标落在正式区；涉及 `01_Canon/` 时还要 `--confirm-canon`（退出码 3）。

第 4、5 道放在主路径而不是做成可选检查：BLOCKER 被绕过一次，正式版就带着已知硬伤
出门；RETCON 被绕过一次，「独立审批」就没有意义了。

通过后：在隔离快照里按批准字节落变更 → 提交 → 建索引 → 写清单 → 切指针 → 写回执
→ 镜像回 Vault（旧内容归档到 `90_Archive/publish_archive/<任务或发布号>/<时间戳>/`）。
任务标 `done`，作者基线推进。

---

## 6.5 发布协议（`novel release`）

发布不是「把文件覆盖过去」，而是一次**可恢复的受审计操作**。完整取舍见 ADR-015。

### 三阶段与权限

| 阶段 | 命令 | 谁 |
|---|---|---|
| 准备 | `release prepare --task` | 任何角色都能跑（只生成计划，不动正式区） |
| 批准 | `approve release <REL> --hash` | **只能 author** |
| 执行 | `release execute <REL> --confirm` / `publish` | **只能 author** |

恢复类：`status` 谁都能查；`resume` / `abort` / `repair` / `rollback` 只能 author。

### 共同发布闸（正文 + 记忆）

`release prepare` 在这一批里看到 `03_Manuscripts/` 的文件时，会先问
`deps closure`：每条正文的记忆都说清楚了吗（PRD §15.1 / AC-15）。不满足就不生成计划：

```bash
$ novel release prepare --task TASK-000012 --as author
正文与记忆必须同批发布，现在这批不满足：
  - [NO_MEMORY_DECISION] CH-0001 既没有记忆提案，也没有「本章不产记忆」的声明。允许不产记忆，但不允许没人说过要不要——`novel proposal extract --task TASK-000012 --chapter CH-0001` 或 `novel proposal declare-none --chapter CH-0001 --reason ...`
  真要单独发正文，用 --skip-closure 并在计划里留下这条豁免
```

四种阻断码：

| 码 | 含义 |
|---|---|
| `NO_MEMORY_DECISION` | 既没提案，也没声明「本章不产记忆」 |
| `NO_MEMORY_NO_REASON` | 声明了不产记忆，但没写理由（等于跳过检查） |
| `PROPOSAL_STALE` | 提案绑的源哈希与正文当前字节不符 |
| `PROPOSAL_NOT_APPROVED` | 提案还没被作者批准 |

`--skip-closure` 是**唯一**的「这一批只发正文」出口，必须显式写在命令行上。
跳过时计划里会留下 `closure_skipped: true`，`release prepare` 与 `release show` 都会打一行

```
[WARN] 豁免      : 本批含正文但跳过了共同发布闸（--skip-closure），记忆可能落后于正文
```

这一条进计划的规范化 JSON，所以它也在作者批准的哈希里——豁免不是口头约定，
是批准对象的一部分。旧版（0.11.0 及更早）写的计划没有这个键，只在为 `true` 时才输出，
保证它们重新序列化后哈希不变、已批准的计划不会突然失效。

存在但非阻断的 `DEPENDENCY_*` 也会一并报出来，不拦发布——判不了的事情要让人看见，
但不该拿它当否决票。

### 22 步走什么

| 阶段 | 步 | 做什么 | 失败/中断的后果 |
|---|---|---|---|
| 候选 | 1–3 | 建事务日志、验基线、验计划与批准 | 正式版未动 |
| 候选 | 4–7 | 建隔离快照、落精确字节、校验、核文件集 | 同上，可续可停 |
| 候选 | 8–11 | 候选提交、建索引、`integrity_check`、算索引哈希 | 同上 |
| 候选 | 12–13 | 写不可变清单、切换前校验 | 同上 |
| 切换 | 14–18 | 取锁、再比对 base、写临时指针、fsync、原子替换 | 18 成功后正式版已变 |
| 收尾 | 19–22 | 写回执、释放锁、镜像回 Vault、备份提示 | 顺序颠倒会让「已切换」无凭证 |

### `release status` 的判词

| 判词 | 含义 | 怎么办 |
|---|---|---|
| `NO_RELEASE` | 从未发布过 | 正常；也会列出待续事务 |
| `OK` | 五项互相吻合 | —— |
| `RECEIPT_MISSING` | 指针与清单对得上，但没回执 | `release repair` |
| `MANIFEST_MISSING` | 指针指着一份不存在的清单 | 恢复模式，人工判断 |
| `MANIFEST_HASH_MISMATCH` | 清单或指针被改过 | 恢复模式，两边都不能猜 |
| `INDEX_MISSING` | 索引缺失或哈希不符 | 重建该版索引或回退 |
| `POINTER_CORRUPT` | 指针读不出来 | 恢复模式，**不自动猜** |

判词非 `OK` / `NO_RELEASE` 时退出码为 1，可直接用于脚本判断。

### 断点与恢复

```bash
novel release status                       # 先看判词和待续事务
novel release resume REL-000002            # 接着做（不重新准备计划）
novel release abort  REL-000002 --note "写错了"   # 或停掉（不动正式版）
novel release repair REL-000002 --as author       # 已切换但缺回执
novel release rollback --to REL-000001 --reason "新设定写坏了"
```

`abort` 对已切换的版本直接拒绝，并指出该用 `rollback`——把指针偷偷拨回去不是中止。

### 锁

`flock` 由内核判定归属。拿不到锁时报错带持有人信息（PID、主机、启动标识、任务、
发布号、开始时间）。**不按文件年龄、不按 PID 猜死锁**——慢机器上那会放出第二个发布者。
确认对方进程确实结束后：

```bash
novel release unlock --confirm   # 真的尝试获取一次，拿不到就拒绝，不删文件
```

### 回退

回退是一次**新发布**：新 `release_id`、`base_release` 为当前正式版、复用旧版内容与
索引、作者单独批准、回执写明原因与「对外平台需人工处理」。导致回退的那次发布记录
不删。

「复用旧版内容」按**逐文件 sha256** 担保（发布前核对本包文件集与目标版完全一致），
不按 commit sha——每次发布都在自己的快照里新提交一次，sha 必然不同，复用旧 sha
会让交叉验证指向一个不存在的提交。

### 故障注入（仅测试）

```bash
NOVEL_RELEASE_FAULT=before:18 novel release execute REL-000002 --as author --confirm
NOVEL_RELEASE_FAULT=after:18  novel release execute REL-000003 --as author --confirm
```

在指定步骤前后抛异常，磁盘痕迹与真实崩溃一致。`tests/integration/test_t06_release.py`
用它覆盖六类断点。

---

## 7. 问题、变更与影响

问题单与变更单存 `<runtime>/issues/` 与 `<runtime>/changes/`，**不进正式区、不进索引**。
理由：它们是审核判词，进正式区就被算进连续性签字基线；进索引就让索引不再是
「Vault + commit」的纯函数。

### `novel issue new|list|resolve|waive`

```bash
novel issue new --severity BLOCKER --category CONTINUITY --target CHAR-0001 \
                --description "第三章人物已死却又出场" --as editor
novel issue list [--status OPEN] [--severity BLOCKER]
novel issue resolve ISSUE-000001 --note "已改第三章，正文 sha256 见下" --as editor
novel issue waive  ISSUE-000002 --note "措辞问题，不影响设定" --as editor
```

等级：`BLOCKER` / `NON_BLOCKING` / `SUGGESTION`。
状态：`OPEN` / `RESOLVED` / `WAIVED`。
类别：`KNOWLEDGE_BOUNDARY` `CONTINUITY` `TIMELINE` `RULES` `SCOPE` `QUALITY` `OTHER`。

**`waive` 对 BLOCKER 一律拒绝**（退出码 4），报错里给两条合法路径：修掉后 `resolve`
（必须写核验依据），或者先批准缩小范围的变更。豁免一个已知硬伤等于让它带着章号
进正式版。

目标可以写任务 ID 也可以写产物 ID：`inspect` 两种都扫——同一个问题可能记在任务上
（还没发布），也可能记在产物上（发布后才发现）。

### `novel change create|list`

```bash
novel change create --type RETCON --reason "父亲其实没死" \
                    --target 01_Canon/Characters/CHAR-0001.md --as chief_editor
novel change list [--status IMPACTED]
```

五类：`PLAN_CHANGE` `STATE_CHANGE` `CORRECTION` `RETCON` `EDITORIAL`。

状态机：`DRAFT → IMPACTED → APPROVED|REJECTED`。**RETCON 必须走独立审批**：

1. 先 `impact` 生成报告，没有报告不许批（闭眼签字）；
2. `approve change` 绑的是**影响报告的哈希**，报告重新生成过就失效；
3. 报告列出受影响正文，而作者既没 `--rewrite-chapters` 也没 `--narrow-scope` → 拒绝。
   PRD §19.3：只改档案不处理正文时不能宣称 RETCON 完成；
4. 有未批准的 RETCON 覆盖某路径时，这条路径上的普通文件审批会被拦下
   （否则独立审批只是可绕过的装饰）。

### `novel impact <CR-ID> [--json]`

生成影响报告：受影响的产物、正文块、事件、断言、认知、伏笔、依赖七类。
报告内容整体取规范化哈希，即变更批准的对象。

**报告是快照**。改完正文再生成一次，哈希就变了，旧批准自动失效——这是设计意图，
不是需要「刷新一下」的麻烦。

---

## 8. 作者手改

### `novel adopt [--dry-run] [--note] [--as author]`

扫描项目内受管文件（`.md/.markdown/.yaml/.yml/.json/.txt/.csv`，跳过 `07_Staging`、`08_Reviews`、`90_Archive`），与基线比对，报告新增 / 修改 / 删除，然后把基线推进到当前状态。

- **不回滚、不覆盖、不改文件内容**——它只登记事实。
- 首次运行建立基线，不产生「改动」记录。
- 历史保留最近 100 条，存 `<runtime>/authorship/<project_id>.json`。
- 只接受作者身份。

---

## 8.5 原稿导入（`novel import`）

把旧稿搬进系统。四步，**每一步都不写正式区**：

```bash
novel import scan ~/旧稿/ --batch-size 20 --as ingestor   # 保全 + 解析 + 提取 + 抽样
novel import report IMP-000001 --chapters                 # 看账
novel import stage IMP-000001 --batch 1 --as writer       # 进 staging
# 之后仍走 approve artifact → release prepare → approve release → release execute
```

### `novel import scan <路径…> [--level N] [--seed S] [--sample N] [--batch-size N] [--json]`

`路径` 可以是文件也可以是目录（目录会展开，同名不同内容直接拒绝）。收 `.md` / `.markdown` / `.txt`。

- **原始字节原样保全**到 `90_Archive/Imports/<IMP>/raw/`，写后逐份回读 sha256 比对，
  不符即中止整次导入。不转码、不归一化换行；
- 非 UTF-8 文件（GBK 等）**拒绝并说明**——本系统不猜编码。docx/epub/pdf 属 P1，同样明确拒绝；
- `--level N` 按第 N 级 Markdown 标题切章，`0`（默认）取最浅一级；
- `--seed` 抽样种子，同种子同结果；`--sample` 抽样条数，默认 100；
- 逐维度给结论，**任何一维不达标退出码就是 1**，不写「导入成功」这种总括。

### `novel import report <IMP> [--chapters] [--mentions] [--json]`

十一个维度逐条列出，另可看章表与候选提及。候选提及**全部是候选**（`approved: false`），
是否成为事实由作者判断。

### `novel import stage <IMP> --batch N [--batch-size N] --as writer`

把某一批写进 staging，返回任务号。`--batch-size 0` 表示整批一次放完。

- **只有标题没有正文的空章不发号**，会在输出里点名「跳过」，不占 CH 号；
- 台账 `05_Ledgers/Imports/<IMP>.md` 覆盖**整个导入**：已发号的写 CH 号，
  未发的写「尚未发号」，空章写「不进正文」；
- 重跑同一批**复用原号**（不多吃一个号）；换切分方式则拒绝，因为已发的号会与章对不上。

### `novel import where <IMP> <定位符>`

按 `文件名#L起-L止` 回读原文。定位符只用文件名与行号——不带章号（章号是解析结果，
换切分规则就变）、不带块 ID（派生数据）。读不到就报错，不返回空串。

```bash
novel import where IMP-000001 "旧稿.md#L3-L9"
```

### `novel import list [--json]`

历次导入与各自的可发布状态。

### 两个不要混淆的口径

- **未映射段落不阻断发布**（PRD §12.4 允许映射到「显式未映射区」），
  但它让「非空段落全部有归属」这一维度不达标；
- **抽样准确率只衡量来源定位**（定位符能否反查回原文），不衡量语义提取对不对。
  后者要人看，系统不代劳。

---

## 8.6 写前资料包（`novel preflight`）

写这一章之前，先把「必须守的东西」摆出来。八级检索各走一次，最后给一个判词。

```bash
novel preflight CH-000102 --base-release REL-000020
novel preflight CH-000102 --no-write --json          # 只看不落盘
novel preflight CH-000102 --budget 16000             # 挤小预算，看哪些被截断
```

### `novel preflight <章号> [--base-release R] [--budget N] [--output-reserve N] [--hard-reserve N] [--search-limit N] [--role R] [--no-write] [--json]`

| 参数 | 默认 | 说明 |
|---|---|---|
| `--base-release` | 当前正式指针 | 基线发布；指不到就按工作树算，并记一条 `WARNING` |
| `--budget` | 32000 | 上下文上限（按非空白字符估 token） |
| `--output-reserve` | 4000 | 给输出留的空间 |
| `--hard-reserve` | 6000 | 给硬约束留的空间；**是上限不是预扣**，用不完还给软资料 |
| `--search-limit` | 6 | 每个关键词最多取几条（Top-K） |
| `--role` | `researcher` | 执行角色，决定可用工具 |
| `--no-write` | 否 | 只算不落盘 |

**默认写盘**，落 `<runtime>/preflight/PF-000001.yaml`。理由：资料包是给下一步按号引用的
产物，不落盘就没法引用；而它写的是 runtime，不是正式区，写坏了删掉重跑即可。
这与 `publish` / `restore` 那种「默认预演」的动作是两种东西。

`--no-write` 时输出会明说「这个号没被占用，下次还可能发到同一个号」——
号只在落盘时占用，不复用空号。

### 判词只有两种

`gate_result` 是 `PASS` 或 `BLOCKED`，没有第三种。BLOCKED 的常见原因：

| category | 触发条件 | 怎么办 |
|---|---|---|
| `BUDGET` | 硬约束装不下（`Overflow`） | 加 `--budget` 或 `--hard-reserve`，或缩小本章范围 |
| `SCOPE` | 规划文档里没有 `creative_scope` | 先在章卡里写明允许与禁止 |
| `PLANNING` | 找不到章节规划文档 | 补章卡，或确认章号 |
| `FORESHADOW` | 有已过窗口仍未回收的伏笔 | 回收它，或改窗口 |
| `RULES` | 索引是旧版建的，读不到伏笔（这一整类硬约束没查） | `novel rebuild-index` |
| `INDEX` | 索引不可用 | `novel rebuild-index` |

`MSG_NO_RELEASE`（找不到正式发布）只记 `WARNING` 不阻断——新项目还没发布过，
这不是错误状态。

### 截断可见

软资料装不下时记 `Truncation` 并进 `coverage.truncated_results`。终端每次都会打印：

```
有 5 处结果被截断（看 truncated_results，别当成「就这么多」）：
  - search：37 条里收了 6 条，丢 31 条（超出每级 Top-K）
  - knowledge：12 条里收了 3 条，丢 9 条（软额度不足）
```

同理 `unresolved_gaps` 列出查不到的缺口（没写规划、索引里没这个实体……）。
**「没查到」与「查到但没放进去」是两件事**，两者分开报，不合并成「资料不全」。

### 模型是可选的

不给模型（默认）走纯确定性路径：读规划 → 查索引 → 放预算 → 判 gate，结论可复现。

给了模型，资料官只多做一件事：把规划里的「必须发生 / 禁止发生」提炼成结构化约束。
模型输出不可用（不是 JSON、缺字段、`role` 写错）→ **退回原文照录**，
记一条 issue（`资料官输出不可用：…`），`model.used=false`。不会「尽力而为提取字段」。

理由：写前检查的结论要能复现。同一份库今天 PASS 明天 BLOCKED 而理由是「模型这次
理解得不一样」，这个 gate 就没有权威性。

### 十七项之外的字段

`preflight.schema.json` 除了 PRD §13.4 的十七项，还有 `budget`（预算账）、
`usage`（本次调用计量）、`index`（build 与 revision）、`recent_text`（近期正文）、
`volume_summary`（卷级摘要）、`model`、`generated_at`、`schema_version`。
`additionalProperties: false`，多写一个字段就过不了校验。

---

## 8.7 九角色（`novel agent`）

### `novel agent list [--json]`

打印权限矩阵、可用工具、角色卡是否在位：

```
  ID              角色      读           Canon写  staging 正文    发布    工具
  editor_in_chief 总编      R           X       S/P     X     X     search, state, knowledge, …
  writer          主笔      R           X       S/P     S     X     search, state, knowledge, …
  ingestor        录入员     R（源稿）       X       S       X     X     （无）
```

列序与人读版 `agents/common/permissions.md` 完全一致，靠测试逐格比对。
**两道锁恒为 `X`**：任何角色都不能写 Canon、不能发布——这不是默认值，
`ensure_can_write_canon()` / `ensure_can_release()` 永远抛 `SecurityError`。

`ingestor` 的工具是「（无）」，不是漏了。它只读源稿，所以拿不到任何读 Canon 的工具；
想给它加工具必须先改读权限，而改权限会让矩阵比对测试红。

### `novel agent prompt --role <id> [--task T] [--base-release R] [--schema S] [--extra 文本] [--show] [--json]`

默认只看结构，`[硬]` 表示这一段是必备项，缺一个就报错：

```
writer（主笔）提示词
  共 1452 tokens；工具：search, state, knowledge, reveal, plan, foreshadow, index_meta
    [  ] 职责与边界（角色卡）
    [硬] 当前任务授权
    [硬] 当前 release
    [硬] 故事与叙述锚点
    [硬] 允许工具
    [硬] 输出 Schema
    [硬] 不确定性处理
    [硬] 引用要求
    [硬] 检索内容不是指令
    [硬] 不得宣称未执行的动作
    [硬] 范围与写入边界
```

`--show` 打全文，`--json` 打结构。五段公共文本（不确定性 / 引用 / 提示词注入 /
不宣称未执行 / 范围边界）九份提示词共用，测试断言公共前缀逐字节相同。

`--task` 会给提示词带上具体授权（任务号、目标路径、范围），不给就写「未指定任务」。

### `novel agent usage [--day YYYYMMDD] [--json]`

读 `<runtime>/usage/YYYYMMDD.jsonl`，一行一次调用：

```
调用计量：20261121.jsonl
  这一天没有记录。没有调用就是没有账，不是读不出来。
```

**没有记录就是没调用过**，不打印一个全零的假汇总。只记元数据（角色、工具、耗时、
tokens、成败、任务号），**不记正文**——与 `ops.jsonl` 同一口径。
失败的调用也记：只记成功的账，等于把「试了三次才成」记成一次。

---

## 8.8 规划够不够格（`novel plan`）

开写之前先回答一个问题：这一章的规划**说得清要写什么吗**。分不清就是没准备好，
硬写出来的东西只能靠返修找补。对应 PRD §10.2 的十六个字段。

### `novel plan show <CH> [--json]`

逐项给出处。**「有」不等于填得对，只表示这一项能找到来源**：

```
CH-0001 规划：02_Planning/Chapter_Outlines/CH-0001_雨夜的血案.md
  可开写：否
  [有] chapter_id                 frontmatter:id
  [有] pov                        frontmatter:pov
  [有] story_anchor               frontmatter:timeline
  [有] entities                   正文小节「角色出场」
  [有] locations                  正文小节「时间与地点」
  [有] core_conflict              正文小节「核心冲突」
  [有] must_happen                正文小节「必须发生的剧情事实」
  [有] must_not_happen            正文小节「禁止发生的内容」
  [缺/建议] entry_state                入场状态
  [缺/必需] creative_scope             创作授权（creative_scope）

  [WARN] 缺必需字段：preflight 会判 BLOCKED，write 会拒绝开写
```

每个字段有两条认法：frontmatter 的键，或正文的某个小节标题。章卡是作者手写的，
不该逼迫某种排版——但也不能因为换了标题就判「没规划」。

### `novel plan check <CH> [--json]`

只判够不够开写，缺必需字段返回 `1`：

```
  [WARN] CH-0001 规划还缺 1 项必需字段
    - 创作授权（creative_scope）
```

八项必需（缺了不是「规划偏薄」，是「不知道能写什么」）：
`chapter_id` / `pov` / `story_anchor` / `entities` / `core_conflict` /
`must_happen` / `must_not_happen` / `creative_scope`。
其余八项缺了只算建议，不进判词。

### `novel plan derive <CH> [--write] [--as author] [--json]`

把散文里的约束解析成结构化字段，**每条带回溯定位符**：

```
CH-0001 可派生的结构化约束
  必须发生：10 条
    - 武安城暴雨骤至，街巷封闭，明工造夜间作业照常    [02_Planning/Chapter_Outlines/CH-0001_雨夜的血案.md#L56-L56]
    - 公主避难奔逃，进入武区街巷，被"东星帮"追捕，误为逃犯目标    [...#L57-L57]
  ...
  禁止发生：5 条
    - 不得让陈默在本章觉醒灵能或使用「金手指」的具体能力（他在 CH-0002 才被注意到）。    [...#L69-L69]
  creative_scope.forbidden：5 条（只收明确禁令语气的条目）
```

只派生**确定性可读的两处**：

| 来源 | 派生到 |
|---|---|
| 编号表格的第二列 | `must_happen` |
| 以「不得／禁止／不许／不准／不能／严禁／不可」开头的列表项 | `must_not_happen` + `creative_scope.forbidden` |

**`allowed` 与 `approval_required` 一律不派生**。散文里没有这两项的来源，
编一个出来就是替作者扩权——比漏派生危险得多。
禁令用「开头词」判而不是关键词包含：「他不知道路」是叙述，不是约束。

`--write` 才真的写回，三条硬线：

- 只插入**原本不存在**的键，已有的一律不覆盖；
- 正文**逐字节不变**（变了抛 `ConflictError` 并回滚）；
- 写之前先备份到 `<runtime>/backups/plan/`。

写回只接受 `--as author`。不做「解析成 dict 再整体 dump」——那会重排原有键、
改掉引号风格，把作者的手写格式冲掉。

---

## 8.9 写稿（`novel write`）

```bash
novel write --task TASK-000004 --chapter CH-0001 \
            --mock-response ./draft.json --as writer
```

### 两道前置，缺一条就不写

1. `plan check` 说规划够格——缺必需字段直接拒；
2. `preflight` 说资料包 `PASS`——`--preflight <PF-id>` 指定，省略则复用最新 `PASS`，
   没有就现跑一次（`write=False`，即不落盘）。判 `BLOCKED` 就拒。

**只认 `PASS`**：更新的 `BLOCKED` 不能顶掉更旧的 `PASS`，否则「多跑一次就把闸门跑松了」。

### 目标路径是结构性的，不是参数

草稿固定落 `03_Manuscripts/Drafts/<CH>.md`，由代码拼出后**再校验一次不在 `01_Canon` 下**，
违反直接 `SecurityError`。没有 `--to` 参数可给：能指定目标路径的写稿命令，
迟早会被用成「直接写正式区」。

落盘只走 `StagingArea.stage()` ——写稿本身永远不会动正式区。

### 本期未接真实适配器

`--mock-response` 从文件读一段（约定的）模型输出。不给就直接报错，**不静默生成占位稿**：
一份看起来像正文的占位稿，比一个明确的报错更容易被当真。`--model` 留给真适配器。
`--replace` 覆盖本任务已有的同名草稿（返修时用）。

---

## 8.10 审核（`novel review`）

### `novel review submit --task <ID> --chapter <CH> [--role <角色>] [--verdict approve|revise] [--risk LOW|MEDIUM|HIGH] [--max-rounds N] [--note] [--as <审核人>]`

```
审核记录 REVIEW-0002
  CH-0001 第 2 轮   world_architect / world_architect
  结论：APPROVE   风险：LOW

发现
  [NON_BLOCKING/QUALITY] 正文只有 41 字，短于常见章节；确认不是被截断的输出

下一步：novel approve text --task TASK-000004 --chapter CH-0001 --as author
```

### 风险只有一个方向

确定性标签命中即**至少**该等级，人工与模型只能往上抬：

| 标签 | 触发词（示例） | 底线 |
|---|---|---|
| 死亡 | 死亡、身亡、丧命、殒命、断气、咽气、尸体、暴毙、死 | `HIGH` |
| 复活 | 复活、复生、死而复生、起死回生 | `HIGH` |
| 核心规则变更 | 规则变更、新增规则、改写规则、能力规则 | `HIGH` |
| 身份揭露 | 身份揭露、暴露身份、真实身份、真正的身份 | `HIGH` |
| RETCON | RETCON、改写历史、推翻既有、推翻前文 | `HIGH` |

`--risk` 传更低的等级会被**忽略并留说明**，不是报错——降级请求要留下痕迹，
否则事后没人知道有人试过。

单字「死」必须过搭配过滤（`路/角/寂/守/心/板/锁/账/扛/磕/缠/党/规矩`）：
宁可漏判（`--risk HIGH` 能补），也不要让「死寂的院子」把每一章都抬成 HIGH。

### 按风险配审核角色

| 风险 | 必须过的角色 |
|---|---|
| `LOW` | 编辑 |
| `MEDIUM` | 编辑、世界架构师 |
| `HIGH` | 编辑、世界架构师、总编 |

**高风险不许自审**：`reviewer_role == author_role` 且 `APPROVE` → `SecurityError`，
没有 `--force`。给 `--force` 就等于把这个检查删了。

### 返修预算

`--max-rounds`（默认 3）按**提交轮次**计，超了就不收新记录并说清已用几轮。
判定顺序是「先算预算 → 再判资格 → 最后写记录」：反过来会让预算耗尽的稿子
仍写进一条 `REVISE`，看起来像「还有人在推进」。

### 规则检查只判形状，不判语义

机器能看的是：空正文、正文里混了系统注释标记、正文里出现第二份文档、
禁令条目里点了名的专有名词却出现在正文里。

「人物用了还没获得的信息」这类**语义**问题，每次明确写成一条 `SUGGESTION` 并注
「判不了，需人工」——不假装查过，也不假装没问题。

### `novel review status --task <ID> --chapter <CH> [--json]`

```
CH-0001 审核状态
  结论：APPROVE   风险：LOW
  返修轮次：0/3
  已通过角色：editor
```

结论五态：`NOT_REVIEWED` / `APPROVE` / `PARTIAL`（该过的角色没齐）/ `REVISE` / `BLOCKED`。
`PARTIAL` 不能拿去批正文。

---

## 8.11 记忆提案（`novel proposal`）

正文批准之后、发布之前，把正文里的记忆提取成**提案**——作者先看，再决定要不要进正式区。

### `novel proposal extract --task <ID> --chapter <CH> [--mock-response <文件>] [--model] [--note]`

```
记忆提案 PROP-0002
  [OK]   CH-0001 提取出 3 条候选
  源：03_Manuscripts/Drafts/CH-0001.md  7a6a301d83ec2619…
  绑定批准：APR-000003
  提取方式：确定性骨架
    [SUMMARY] CH-0001 正文共 41 字、2 段
    [ENTITY] 正文出现已登记实体：抄书人
    [ISSUE] 本提案由确定性骨架生成：只含摘要与实体清单，语义级事件／状态／认知未提取，需记忆管理员补
```

三条规则：

1. **输入是已批准的候选，不要求正文已 ACTIVE**（PRD §15.1）。正文还没发布，
   记忆照样可以合法提取出来——否则「正文与记忆同批发布」永远无法成立。
2. **必须绑源哈希**。找不到 `CHAPTER_TEXT` 批准、或批准哈希与当前字节不符，直接拒。
   这样「改完正文拿旧提案去发」在提取这一步就断掉。
3. **模型不可用就退回确定性骨架**，不退化成「什么都没提取」。骨架只含摘要与已登记实体，
   并自报一条 `ISSUE` 说「语义级未提取，需记忆管理员补」。有提取总比空手强，
   但必须自己声明缺了什么。

`--mock-response` 的信封 `role` 与本次角色不一致时会拒收并说明（角色写错会让审核记录归错人）。

### 其余子命令

| 命令 | 做什么 |
|---|---|
| `proposal list` | 列出提案：`PROP-0001  CH-0001  APPROVED  3 条` |
| `proposal show <PROP>` | 逐条看，每条带引用出处 |
| `proposal verify <PROP>` | 按当前字节重算源哈希，变了就是 `STALE` |
| `proposal approve <PROP> --as author` | 作者批准（只能 author） |
| `proposal reject <PROP> --note "..." --as author` | 驳回 |
| `proposal declare-none --chapter <CH> --reason "..." --as author` | 显式声明本章不产记忆 |

**「没有提案」与「声明不产记忆」是两件事**：前者是漏做，后者是决定。
`declare-none` 的 `--reason` 必填——没有理由的声明等于跳过检查，发布闸门会判
`NO_MEMORY_NO_REASON`。

---

## 8.12 依赖与共同发布（`novel deps`）

`dependencies` 表从索引 v4 起会填，来源是档案 frontmatter 的 `depends_on`。
**权威在 Markdown，索引只是派生视图**，所以 `deps check` 直接读文件不读索引——
索引可能是上一轮的。

### `novel deps check [--json]`

```
依赖状态
  [WARN] CH-0001            STALE         02_Planning/Chapter_Outlines/CH-0001_smoke.md
      CHAR-0002 内容已变（HARD）：deadbeef0000… → a1a9566ce678…
  [WARN] 1 份产物声明了依赖，1 份需要处理
```

四级判定：

| 情形 | 结果 | 为什么 |
|---|---|---|
| 哈希与记录一致 | `VALID` | 没变 |
| 不一致（`HARD`） | `STALE` | 依赖的规则变了，产物可能已不成立 |
| 不一致（`SOFT`） | `NEEDS_REVIEW` | 软依赖只影响观感，不该一票判死 |
| 没记 `content_hash` 或目标不存在 | `NEEDS_REVIEW` | **判不了不等于没问题** |

**只有真正变了的那条依赖才让产物失效**。`RULE-000003` 改一个字不该让全书每份档案
都变「需要复核」——那样 STALE 会多到没人看，等于没有。

传递性**只走一级**：A 依赖 B、B 自己 `STALE` 时 A 记 `NEEDS_REVIEW` 而不是 `STALE`。
B 的失效源于「它依赖的东西变了」，不等于 B 的内容变了；直接升级成 STALE 会让
一次局部改动沿引用链炸开。

### `novel deps show <产物ID>`

```
CH-0001  STALE
  路径：02_Planning/Chapter_Outlines/CH-0001_smoke.md
    → CHAR-0001  HARD  c65be8bae7d3…
    → CHAR-0002  HARD  deadbeef0000…
  [WARN] CHAR-0002 内容已变（HARD）：deadbeef0000… → a1a9566ce678…
```

### `novel deps closure (--task <ID> | --chapter <CH>...) [--json]`

发布前问「每条正文的记忆都说清楚了吗」，也是 `release prepare` 调用它的那道闸：

```
共同发布检查
  正文：CH-0001
    CH-0001：提案 PROP-0001，3 条
  [OK]   可以同批发布
```

阻断码见 §6.5「共同发布闸」。

---

## 8.13 审阅记录（`novel note`）

把审阅、审计、变更记录写进 `08_Reviews/`。**这是唯一不经过 `publish` 的写入通道**。

它存在的理由：`08_Reviews` 不在 `PUBLISH_TARGETS` 六根里，`publish` 会拒绝它（这是对的，
审阅意见不是作品内容，进正式区会被算进签字基线）。但「不能发布」被当成了「可以手写文件」，
于是它成了全库唯一一处没有契约的写入点。

```
novel note add --title <标题> [--kind review|audit|change-log]
               [--body <正文> | --body-file <文件> | 管道]
               [--scope <范围>] [--task-id TASK-xxxx] [--chapter-id CH-xxxx]
               [--verdict pass|revise|reject|partial]
               [--source-sha256 <64 位十六进制>]
               [--dry-run] [--as <角色>]
novel note list [--kind <类>] [--json]
```

### 三种 `kind` 落到哪

| kind | 子目录 | frontmatter `type` |
|---|---|---|
| `review`（默认） | `Audit_Reports/Chapter_Review/` | `review_report` |
| `audit` | `Audit_Reports/` | `audit_report` |
| `change-log` | `Change_Log/` | `change_log` |

目录根从 layout 的 `review_root` 取，不写死 `08_Reviews`。

### 三条不变量

1. **同名即拒，绝不覆盖。** 审阅记录不该被静默改写——要修正就换标题，
   或把旧的标成 `superseded`。文件名是 `<标题>_<YYYYMMDD>.md`。
2. **先过 schema 再落盘。** frontmatter 按 `schemas/review_note.schema.json` 校验，
   不合契约直接拒。落盘后才发现字段错，就只能在最不该脏的地方留一份坏文件。
3. **`verdict` 只是意见，不是批准。** 审阅说 `pass`，正文照样要 `approve` + `publish`
   才能成为正式事实。这条不写清楚，很容易演变成「总编说行就行」。

### 为什么默认直写而不是先预演

这条通道存在的意义是「轻」——如果写一条意见也要先预演再确认，编辑就会绕回去手写文件，
而手写才是真风险。保护落在别处：同名拒绝、schema 校验、每次写入进 oplog。
`--dry-run` 仍保留，用于先看标题会变成什么文件名。

```console
$ novel note add --kind review --title "CH-0001 场景卡一致性" \
    --chapter-id CH-0001 --verdict revise --as editor --body-file review.md
  [OK]   已写入 10_Projects/novel-001_jiuzhou/08_Reviews/Audit_Reports/Chapter_Review/CH-0001-场景卡一致性_20261122.md
  08_Reviews 不进正式区，不需要 approve/publish，也不算签字基线
  审阅结论「revise」只是意见；正文要成为正式事实仍须 approve + publish
  op: OP-20261122-011158-F612
```

## 8.14 实体、关系、事件、灵感

这四条都是**默认预演**，加 `--confirm` 才落盘。全走 `write_atomic`，写后回读校验。

### `novel entity create --name <名> --type <类型> [--subtype] [--alias] [--summary] [--canon-status] [--source] --confirm`

新建一份世界知识档案。**`--type` 决定目录**（`paths.TYPE_TO_CANON_KIND`），
认不出的类型直接报错并列出可用值——不兜底到「特殊设定」。

`--canon-status` 只收六值：`idea` / `proposal` / `pending_review` / `canon` /
`deprecated` / `rejected`（默认 `proposal`）。**`canon` 的准入判据是「已落在正文里」**，
不是「作者点过头」。

### `novel entity show <实体ID> [--json]`

按 ID 看一份档案：frontmatter、双链、出处。ID 必须带前缀（`ent_000001`），
只写号会报错——`ent_` 是公共号段，光有号说明不了类型。

### `novel entity validate [<实体ID>]`

校验契约。给 ID 校验单份；不给则扫整个知识库。用的是 `schemas/entity.schema.json`，
`additionalProperties: false`，多写一个键就判错。

### `novel relation create --from <ID> --to <ID> --relation-type <类型> [--valid-from] [--valid-to] --confirm`

登记一条关系。**追加，不覆盖历史**：同一对实体可以有多次登记，
按 `valid_from` / `valid_to` 区间读「某时点是什么关系」。
`from` / `to` 必须是已存在的实体 ID，写错不会静默新建。

### `novel event create --name <名> [--world-time] [--precision] [--narrative] [--participant] [--location] --confirm`

登记一条事件。`--precision` 收 `exact/day/month/season/year/era/unknown`；
**时间不确定就写不确定**，不要为了填满而猜一个精确时刻（ADR-013）。
`--narrative` 是叙述位置，与 `world_time` 是两回事。

### `novel idea create [--text <文本>|--text-file <文件>] [--title] [--tag] [--source] --confirm`

登记一条灵感。**原文照录，不做取舍**（WF-01）：这一步的价值是「先留住」，
筛选是后面的事。落点在 `01_灵感与创意`。

### `novel continuity check [--scope all|chapter|volume] [--json]`

跑一遍连续性检查（WF-17）。输出是**账面缺口**：哪些项没查、哪些查了没结论。
「没查」不等于「没问题」——空结果必须读成「不知道」，不是「通过」。

---

## 8.15 工作流与项目

### `novel workflow list|show|validate|run|pause|resume`

`catalog.yaml` 的 20 条工作流（`WF-00`…`WF-19`，另有旁支 `simulation`）。

- `list` 列全部；`show <WF-ID>` 看完整定义（含 `detail_ref` 指向的细则）。
- `validate` 逐字段校验目录：编号连不连续、必填缺不缺、`detail_ref` 指没指空。
- `run` **只开实例、只记账，不执行步骤**。实例落 `<runtime>/workflows/`，不进 Vault。

### `novel project list|show`

`list` 列 Vault 里所有项目；`show <项目ID>` 看结构与知识库分布（各分类多少份）。
两项目同名不算错——以 `project_id` 为准，目录名只是给人看的。

---

## 9. 备份与恢复

### `novel backup [--out <目录>] [--label <标签>] [--no-git] [--list]`

默认落 `<runtime>/backups/BK-YYYYMMDD-HHMMSS/`，内含 `files/`、`manifest.csv`、`meta.json`。

流程按 §11.2：遍历 → 逐文件复制 → 全量重算哈希比对 → 写清单与元数据。任何一条哈希不一致即判失败并保留现场。

默认包含 `.git`（私人 Git 历史），`--no-git` 可排除。`.obsidian`、`__pycache__`、`node_modules` 等缓存目录始终跳过。

### `novel restore --backup-id <ID> [--dry-run] [--confirm] [--overwrite]`

- 不带 `--confirm` 时只预演，不写盘。
- 预演分类：新增 / 内容相同 / 冲突 / 损坏。
- 清单里出现越界路径或哈希不符，直接拒绝恢复。
- 默认只写「新增」，冲突文件跳过并列清单。
- `--overwrite` 覆盖冲突文件，覆盖前先把被覆盖的版本另存到 `<runtime>/restore-undo/<备份ID>-<时间>/`。
- 恢复后建议依次跑 `doctor`、`validate`、`rebuild-index`。

---

## 10. 地图

三个子命令共用一套路径解析，唯一来源是 `Config` + `ProjectPaths`；代码里没有任何
`private/00_Canon/...` 这类硬编码路径。

### `novel maps paths [--world-subdir <名字>]`

打印本次会用到的六个路径：州域志、区划表、地点目录、输出目录、版式、资产表。
路径不对时先跑这条，比看报错猜快。

- `州域志` = `<project>/01_Canon/World/<world-subdir>/总表/<州域志文件名>.md`
- `区划表` = 同目录 `WORLD-0040_行政区划表.md`
- `地点目录` = `<project>/01_Canon/Locations/`
- `输出目录` = `<vault>/00_System/Dashboards/Maps/`
- `版式` = `<project>/00_Project/jiuzhou_layout.yaml`

**作品名与页面文案全部来自这份版式配置**，代码里一个都不留（ADR-005 补充条款）。
`naming` 管名字与文件名，`state_cities` 管「州城即府城」的对应，`page_notes` 管页面上的
补充文案与关联条目；`special_states` / `special_kinds` / `anomalies` / `fu_notes` 管界外与补记。
缺了不兜底：能省就省掉那一段，省不掉就报错。

### `novel maps import-admin [--xlsx <资产表>] [--confirm]`

把资产表「地图库」工作表读成 `WORLD-0040_行政区划表.md`。原表空格照录并在文末点名，
不替作者猜。**内容无变化时不写盘。**

### `novel maps build [--maps-only] [--confirm]`

重画地图页（`<地图页>.md`）与两张 canvas。

- **默认预演**：只报「将改几个文件」，不落盘。
- `--maps-only`：只重画图，不碰州域／府／城邑三种档案。
- 不加 `--maps-only` 会重写 11 份州域档案、28 份府档案，并给城邑补「层级」行；
  这些档案里有正文受控块与作者手改，重写时按块名搬回、按 `PRESERVE_KEYS` 保历史字段。
- 幂等的判据是**「写入 0 个文件」**，不是「命令没报错」。

```bash
novel maps paths                                    # 先确认路径
novel maps build --maps-only                        # 预演：会改几个文件
novel maps build --maps-only --confirm              # 只重画图
novel maps build --confirm                          # 连档案一起重跑（危险，先看预演）
```

---

## 11. 契约与发号

### `novel schema list [--schema-dir <目录>]`

列出可校验的类型（当前 24 类）与每类的必填字段。schema 默认从**仓库根 `schemas/`**
读取；装成包后可放 `novel_management/schemas/`，或用 `NOVEL_SCHEMAS` 环境变量指定。

### `novel schema check <类型> <文件…>`

按类型校验 JSON 文件。`-` 表示从 stdin 读。

- 退出 **1**：读懂了，但内容不合契约（缺字段、非法枚举、未知字段、ID 形态错）。
- 退出 **2**：读不懂输入（不是合法 JSON、文件不存在、类型名不存在）——这是用法问题，不是数据问题。

```bash
novel schema list
novel schema check entity tmp/char.json
cat tmp/approval.json | novel schema check approval -
```

未知字段默认拒绝（PRD §8.4）。要扩展只能写进 `extensions` 字段，不能就地加键。

### `novel allocate-id --type <类型> [--count N] [--confirm] [--allow-dirty]`

分配稳定 ID（PRD §8.1）。`--type` 收 `CHARACTER`／`character`／`CHAR` 三种写法。

- **默认预演**，只报「将分配什么号」；`--confirm` 才写 `<runtime>/id-allocations.jsonl`。
- 取号同时看**现存文件最大号**与**审计里发过的最大号**。删档后不会从洞里补号——
  补号等于让历史引用指向另一个对象。
- 本前缀已有重号时**拒绝发号**（`ASSET-0001` 即此），`--allow-dirty` 可强行发。
  其它前缀的重号不会阻塞（`ASSET` 撞号不该让 `CHAR` 也发不出号）。
- 发号用锁文件 `<runtime>/locks/id-allocate.lock` 防并发；锁被占时直接报错，
  不静默降级（PRD §8.1 明令禁止「无锁扫描最大号加一」）。
- 复合型 ID（`EVT-CH-0001-001`）不计入号段，命令会提示有几个、在哪些文件。
- **号段上限**（`ids.BAND_LIMITS`）：只有 `CH` 有，活文档段 `1..999`。
  取号只统计段内的最大号，旧稿存档段（`CH-001001..001028`）不参与。
  段满了报错而不是自动越过——越过去会让新正文写进存档段，且不可逆。
  输出里的「段内最大号」也是这个口径，不是全库最大号。

```bash
novel allocate-id --type CHARACTER                 # 预演
novel allocate-id --type CHARACTER --confirm       # 真发号，写审计
novel allocate-id --type FORESHADOW --count 3      # 一次三个
novel allocate-id --type chapter                   # CH：段内 000002 → CH-000003
```

### `novel package-hash <文件|-> [--check <哈希>] [--canonical]`

算规范化 JSON 的 sha256（PRD §17.3）。键排序、无多余空白、不转义中文、禁 NaN/Infinity。

审批与发布绑定的就是这个值，所以缩进、键序、文件里的换行都不影响结果。

```bash
novel package-hash tmp/release_plan.json
novel package-hash tmp/release_plan.json --check <批准时记下的哈希>
novel package-hash tmp/release_plan.json --canonical   # 同时看规范化后的原文
```

`--check` 不一致退 **1**；JSON 里有 NaN/Infinity 退 **2**（那不是合法 JSON）。

---

## 12. 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 冲突：目标已存在、哈希不符、任务已发布、diff 有冲突、恢复清单损坏 |
| 2 | 用法错误：参数缺失、枚举值非法、状态流转非法、未指定项目 |

> 未指定项目也归 2：`--project` 与 `Vault_Config.yaml` 的 `active_project.project_id`
> 都没给时，写入类命令一律拒绝，不做「Vault 里只有一个项目就用它」的推断。

| 3 | 安全拒绝：路径越界、操作者无权、缺二次确认 |
| 4 | 批准问题：缺批准、批准已失效 |

---

## 13. 目录与文件落在哪

| 内容 | 位置 | 可否重建 |
|---|---|---|
| 任务记录 | `<runtime>/tasks/TASK-*.yaml` | 否（但可重开任务） |
| 批准记录 | `<runtime>/approvals/APR-*.json` | 否 |
| 问题单 | `<runtime>/issues/ISSUE-*.json` | 否 |
| 变更单 | `<runtime>/changes/CR-*.json` | 否 |
| 操作日志 | `<runtime>/logs/ops.jsonl` | 否 |
| 调用计量 | `<runtime>/usage/YYYYMMDD.jsonl` | 否（只追加；不记正文） |
| 写前资料包 | `<runtime>/preflight/PF-*.yaml` | 是（删掉重跑；号不复用） |
| 索引库 | `<runtime>/index/builds/<build_id>.sqlite` | 是 |
| 索引指针 | `<runtime>/index/active.json` | 是（指向哪一份 build） |
| 作者基线 | `<runtime>/authorship/<project_id>.json` | 是（重新 adopt） |
| 备份 | `<runtime>/backups/<BK-ID>/` | 否 |
| staging 产物 | `<vault>/01_小说项目/<PROJECT>/_staging/<TASK-ID>/` | 否（发布后仍保留作证据） |
| 发布旧版归档 | `<vault>/01_小说项目/<PROJECT>/90_Archive/publish_archive/` | 否 |
| 地图页与 canvas | `<vault>/00_作者工作台/全局仪表盘/Maps/` | 是（`maps build --confirm`） |
| 版式配置 | `<project>/10_项目配置/jiuzhou_layout.yaml` | 否（手写；属排版不属设定） |
