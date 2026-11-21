# CLI 参考

对应 V4.0 §5.3 的命令集，另加 3 条地图命令（§8）与 T03 的 `rebuild-index`/`search`，
顶层命令共 **19** 条（`task` / `maps` / `schema` 是命令组，各自还有子命令）。
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

剩下 3 张是空表：`foreshadowing`（伏笔契约待 T07/T09）、`dependencies`（T07 之后）、
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

### `novel approve --task <ID> [--note] [--reject] [--force] [--as author]`

旧入口，保留兼容：它绑的同样是每个 staged 文件的内容指纹，`publish` 照它校验。
新内容应当用上面带对象类型的入口——旧入口说不出「批的是哪个对象」。

- 批准前先跑一遍 diff：存在冲突则拒绝，除非 `--force`（等于作者确认「以 staging 为准，覆盖我的手改」）。
- 批准记录写入 `<runtime>/approvals/APR-00000N.json`，绑定每个 staged 文件的 `target` / `staged` / `sha256` / `bytes`。
- `--reject` 记一条驳回，任务 `approval_state` 变为 `rejected`。
- 重新批准会生成新记录，旧记录保留。

### `novel publish --task <ID> --confirm [--confirm-canon] [--as author]`

六道闸：

1. 有 `decision=approved` 的批准记录；
2. staging 里每个批准过的文件，当前 sha256 与批准时一致；
3. staging 里没有批准范围外的新增文件；
4. 目标上没有 OPEN 的 `BLOCKER`（记在任务 ID 或产物 ID 上都算）；
5. 目标上没有「未批准的 RETCON」覆盖；
6. 目标落在正式区；涉及 `01_Canon/` 时还要 `--confirm-canon`。

第 4、5 道放在主路径而不是做成可选检查：BLOCKER 被绕过一次，正式版就带着已知硬伤
出门；RETCON 被绕过一次，「独立审批」就没有意义了。

通过后：目标已存在则先复制到 `90_Archive/publish_archive/<TASK-ID>/<时间戳>/<目标路径>`，再原子写入。任务标 `done`，作者基线推进。

任务已 `done` 或 `cancelled` 时拒绝发布。

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

列出可校验的类型（当前 22 类）与每类的必填字段。schema 默认从**仓库根 `schemas/`**
读取；装成包后可放 `novel_system/schemas/`，或用 `NOVEL_SCHEMAS` 环境变量指定。

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

```bash
novel allocate-id --type CHARACTER                 # 预演
novel allocate-id --type CHARACTER --confirm       # 真发号，写审计
novel allocate-id --type FORESHADOW --count 3      # 一次三个
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
| 索引库 | `<runtime>/index/builds/<build_id>.sqlite` | 是 |
| 索引指针 | `<runtime>/index/active.json` | 是（指向哪一份 build） |
| 作者基线 | `<runtime>/authorship/<project_id>.json` | 是（重新 adopt） |
| 备份 | `<runtime>/backups/<BK-ID>/` | 否 |
| staging 产物 | `<vault>/10_Projects/<PROJECT>/07_Staging/<TASK-ID>/` | 否（发布后仍保留作证据） |
| 发布旧版归档 | `<vault>/10_Projects/<PROJECT>/90_Archive/publish_archive/` | 否 |
| 地图页与 canvas | `<vault>/00_System/Dashboards/Maps/` | 是（`maps build --confirm`） |
| 版式配置 | `<project>/00_Project/jiuzhou_layout.yaml` | 否（手写；属排版不属设定） |
