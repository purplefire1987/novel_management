# 变更日志

格式：语义化版本。每条记录写明兼容性影响与迁移提示。

## 0.11.0 — 2026-11-21

T08「Agent 适配器与写前资料包」。九角色接入流程、提示词统一装配、检索预算两条不变量、
`novel preflight` 输出 PRD §13.4 结构。ADR-017 记了取舍。

**兼容性影响**：无。新增 `preflight` 与 `agent` 两条命令，既有命令的参数与语义不变。

**升级后要做的事**：**跑一次 `rebuild-index`**。索引版本 2 → 3，
`foreshadowing` 表从空表变为会填的表。老库不重建时 preflight 的
「未回收伏笔」一栏恒为 0——不报错，只是少了一整类硬约束。

### 新增命令

命令行 28 → **30 条**（新增 `preflight`；新增 `agent` 命令组，含三个子命令）。

- `preflight <CH-xxx> [--base-release R] [--budget N] [--output-reserve N]
  [--hard-reserve N] [--search-limit N] [--role R] [--no-write] [--json]`
  ——产出写前资料包，判 PASS / BLOCKED。默认落盘；
- `agent list [--json]`——九角色权限矩阵、可用工具、角色卡在位检查；
- `agent prompt --role <id> [--task T] [--base-release R] [--schema S] [--extra 文本] [--show] [--json]`
  ——渲染提示词；默认只看结构（带 `[硬]` 标记），`--show` 打全文；
- `agent usage [--day YYYYMMDD] [--json]`——当天调用计量台账。

### 一、权限矩阵是唯一真源，靠测试逐格比对

`Role` 的字段不是权限枚举，而是 `agents/common/permissions.md` 那张表的**五列本身**，
`matrix_row()` 一字不差渲染那一行。测试解析 md 表格逐格比对——三份副本里只有代码那份
参与运行，让它长得跟文档一样，比对就退化成字符串相等。

PRD §6.1 的 A1–A9 按规范 v1.7 §4 作废，本版以裁定后的九角色为准
（`editor_in_chief` / `world_architect` / `editor` / `dice_mistress` / `researcher` /
`writer` / `memory_keeper` / `ingestor` / `version_manager`），与 `audit.AGENTS` 同名同序。

两道锁恒为 `False`：`ensure_can_write_canon()` / `ensure_can_release()` 永远抛
`SecurityError`。**Agent 不写 Canon，Agent 不发布。**

能力位（六个）从矩阵推出，`allowed_tools()` 不另维护清单。所以「`ingestor` 拿不到
读 Canon 的工具」不是一条单独规则，是它只读源稿的结果——想加工具必须先改它的读权限，
而改权限立刻会让矩阵比对测试红。

「R（限授权）」是执行层拦截：`dice_mistress` 调工具必须显式给 `scope`，否则拒绝。
`_check_params` 拒绝未知参数——拼错一个键会变成「查不到」，而「查不到」长得像
「库里没有」。

### 二、提示词九段装配，公共文本只写一遍

固定顺序：`role_card → task → release → anchors → tools → schema → uncertainty →
citation → injection → no_claim → scope → (extra)`。前九个是 `REQUIRED_KEYS`，
缺一个就报错。五段公共文本（不确定性 / 引用 / 提示词注入 / 不宣称未执行 / 范围边界）
是模块常量，测试断言九份提示词的公共前缀**逐字节相同**。

### 三、预算两条不变量（其中一条修的是真缺陷）

- **软额度必须是「剩余」额度**。初版没扣已用量，八个检索级各领一份完整配额，
  32000 的预算装进了 48000 的内容。这不是估得不准，是每一级都以为别人没花。
- **硬约束一条都不丢**。装不下记 `Overflow` 并让 gate 判 BLOCKED；只有软资料记
  `Truncation`。顺带把未回收伏笔的填充挪到结构化状态**之前**——它是 §13.3 必须检查项，
  排在软资料后面会出现「因为读了太多无关档案而阻断写作」。
- 硬约束预留会回收：`hard_reserve` 是上限不是预扣，用不完的还给软资料。
- `Truncation` 合并进 `coverage.truncated_results`。初版只记在预算账里，
  资料包里的这个字段恒为空——截断在账上可见、在包里不可见，等于没做第一条验收。

### 四、Preflight 默认不调模型，模型只做加值

默认路径纯确定性 Python：读规划、查索引、放预算、判 gate。给模型时资料官只做一件
加值的事——把「必须发生 / 禁止发生」提炼成结构化约束。模型输出不可用（非 JSON、
缺字段、`role` 写错）就**退回原文照录**并记一条可读 issue，不「尽力而为提取字段」。

理由：写前检查的结论必须可复现。同一份库今天 PASS 明天 BLOCKED 而理由是「模型这次
理解得不一样」，这个 gate 就没有权威性。

八级检索（PRD §13.2）落成七次工具调用，第八级是「截断为真时人再去查」。
「一次 Top-K 不得视为完整知识」落实为 `truncated` 标志 + `coverage.truncated_results`，
CLI 每次都打印「有 N 处结果被截断（别当成『就这么多』）」。

### 五、调用计量落盘，只记元数据

`<runtime>/usage/YYYYMMDD.jsonl`，一行一次调用，只追加。记角色、工具、耗时、
tokens、成败、`task_id`，**不记正文**（与 `ops.jsonl` 同一口径）。
失败的调用也记——只记成功的账，等于把「试了三次才成」记成一次。

### 六、顺带修掉的四处基础缺陷

- 旧索引上 `preflight` 直接抛栈：`foreshadow` 工具去查 `window_policy`，
  而第 2 版建的表没有这一列，`sqlite3.OperationalError` 打满屏幕。真库实测撞到。
  现在按空列读（行还在、窗口信息诚实留空），并记一条 **BLOCKER**
  「未回收承诺这一整类硬约束本次无法检查，等于没做——先 rebuild-index」。
  **这一类没查 ≠ 这一类没问题**，后者才允许 PASS；
- `storage.dump_yaml` 重写为递归实现。原版只支持「标量 / 列表 / 一层对象列表」，
  `issues[].evidence[]` 这种三层嵌套会被写成一个字符串；
- `_yaml_scalar(None)` 从 `""` 改为 `null`。否则 `base_release` 写下去是空串，
  读回来过不了自己的 schema；
- 索引查询统一走 `project_clause()`。索引里 `project` 存的是目录名
  （`novel-001_smoke`）而非 `project_id`，等值比较会静默返回空——
  伏笔一条都查不到、报「未回收 0 条」，看着像「没有伏笔」。

### 新增契约

- `schemas/agent_envelope.schema.json`——PRD §6.3 信封，`role` 枚举九角色，
  `task_id` 形如 `TASK-000001`，`additionalProperties: false`；
- `schemas/preflight.schema.json`——PRD §13.4 十七项必填 + 预算 / 用量 / 索引 /
  近期正文 / 卷级摘要 / 模型 / 生成时间，`gate_result` 只允许 `PASS|BLOCKED`。

契约总数 22 → **24 类**。

### 测试与自检

- pytest 568 → **635 passed / 1 skipped**；`scripts/selfcheck.py` 33 → **39 项**；
- 验收清单 §14.3 由「查九个目录在不在」升级为「权限矩阵逐格比对 + 执行层硬拦 +
  九份提示词公共前缀」——文件存在性不是权限。

## 0.10.0 — 2026-11-21

T07「原稿导入」。五条新子命令、一套定位符，外加**三个真缺陷的修复**（T06 留下的
坐标系混用、ID 查重不分区域、台账跨批次覆盖）。ADR-016 记了取舍。

**兼容性影响**：无。新增 `import` 命令组，既有命令的参数与语义不变；
`schemas/import_manifest.schema.json` 的 `files.items` **放宽**（补了四个字段），
原来能过校验的记录现在照样能过。

**升级后要做的事**：无数据迁移。真实 Vault 上未执行过任何导入，
`<runtime>/imports/` 与 `90_Archive/Imports/` 都是空目录。

### 新增命令

命令行 27 → **28 条**（新增 `import`，含五个子命令）。

- `import scan <路径…> [--level N] [--seed S] [--sample N] [--batch-size N] [--json]`
  ——保全原始字节、解析章节、提取候选、抽样核对。**不写正式区**；
  有维度未达标时退出码 1；
- `import report <IMP> [--chapters] [--mentions] [--json]`——逐维度看账；
- `import stage <IMP> --batch N [--batch-size N] --as writer`——把一批写进 staging；
- `import where <IMP> <定位符>`——按 `文件名#L起-L止` 回读原文；
- `import list [--json]`——历次导入。

### 一、原始字节单独保全，写后回读

被接收的文件原样复制到 `90_Archive/Imports/<IMP>/raw/`，逐份回读 sha256 比对，
不符即中止整次导入。**不转码、不做换行归一化**：GBK 文件直接拒绝并说明
「本系统不猜编码」。转码是不可逆的猜测，猜错不报错，只会静默改字。

### 二、定位符只用文件名与行号

`文件名#L12` 或 `文件名#L12-L18`。不带章号（章号是解析结果，换切分规则就变）、
不带块 ID（派生数据）、不带字符偏移（换行归一化后会整体平移）。
`import where` 与抽样核对量的都是这件事：这条定位符能否反查回原文。

抽样准确率的口径写死在 `note` 里——**只衡量来源定位，不衡量语义提取**。
0 条样本时记 `0.0` 并说明「不适用」，不写 `1.0`。

### 三、十一个维度各自记账，没有 `success`

`ImportManifest` 逐维度落字段，`verdicts()` 逐条给结论，`blocking()` 只列会让
「发布候选」不成立的几条。Schema 的 `additionalProperties: false` 挡住
「随手加个 `ok: true`」。

两条口径分开：

- **未映射段落不阻断**（PRD §12.4 允许「映射到解析结果**或**显式未映射区」），
  但让「段落全部有归属」这一维度不达标；
- **提取覆盖不完整不阻断**——不能让「这章没提到人名」变成「这章不许进正文」。
  纯描写章照样入正文、照样可检索。

### 四、台账覆盖整个导入，已发号持久化

台账落在同一个目标文件上，若每批只写自己那一批，第二批发布时会把第一批的章
整段抹掉——报告看着完整，实际只描述了最后一次操作。所以按全计划渲染，
未发号的写「尚未发号」，空章写「不进正文」。

各批已发的号记在 `<runtime>/imports/<IMP>/assignments.json`：
重跑同一批复用原号（不多吃一个号），换切分方式则**拒绝**
（已发的号会与章对不上）。

### 五、修 T06 遗留：计划路径与快照根不是同一个坐标系

计划里的路径是**项目相对**（`03_Manuscripts/...`），快照的根是 **Vault**。
`apply_changes` 直接拼路径，于是每个变更文件在快照根多出一份「幽灵」，
发布索引同时收录正式文件与幽灵文件，搜索会命中一个打不开的路径。

第 7 步的「文件集与计划一致」检查也因此形同虚设——它比较的正是那套错路径，
幽灵让检查通过。现在两个方向各一个函数（`vault_rel` / `project_rel`），
**互相拒绝对方的输入**，混用立刻报错。回退计划的路径来自旧清单（Vault 相对），
一并换算。

### 六、修 T06 遗留：ID 查重不分区域

第一批发布后正式区与 staging 各有一份 `CH-0001`，`IdRegistry` 判成重号，
**第二批直接发不出号**。现在按区域分级：正式区内 ≥2 份才算撞号；
正式区 1 份 + staging 1 份是「同一对象在途」；牵连归档、或落在区划外的
（Vault 根的 `99_Archive/legacy_v1` 整库快照）只告警。

区划由 `ProjectPaths.area_roots()` 提供，目录名不写死。**不传区划时全库按正式区
处理**——宁可从严，也不能因为调用方忘了传参就悄悄放松；此时输出会说明「口径从严，
数可能偏多」（真库不带 `--project` 报 2 个、带上报 1 个，正是这个差别）。

### 七、解析器的两条务实让步

- TXT 里 `## 第一章` 照旧按层级认（同一份文字存成 `.md` 还是 `.txt` 应当切得一样）；
- `.md` 认不出 `#` 时退回「第N章」文字标记——纯文本改名来的稿子通篇没有 `#`，
  只认 `#` 会把整本当一章。

切不出章就如实说切不出：整篇一章 + `known_limits` 写明，并说明「章号与会话结构
需要作者手工整理」。

### 测试

505 → **568 passed / 1 skipped**。新增 `test_import_model.py`（23 条：定位符往返、
manifest 契约、抽样确定性）、`test_import_parser.py`（17 条：切章、空章、标题前内容、
段落总数核对）、`test_t07_import.py`（10 条：三条验收 + 四处缺陷回归）、
`test_ids.py` 加 7 条分级查重用例、`test_t06_release.py` 加 1 条幽灵文件用例。

后者验证过有效性：把 `apply_changes` 退回原版，`test_t06_release.py` 立刻红一片。

`scripts/selfcheck.py` 26 → **33 项**（加导入链路 7 项，含「第二批发号不被 staging 副本
卡住」与「台账覆盖全计划」）。真库只读实测：`release status` 判词 `NO_RELEASE`、
`import list` 为空、`validate` 结论与 0.9.0 一致、`allocate-id` 认得出 CH 号段到 1028。

## 0.9.0 — 2026-11-21

T06「发布与恢复内核」。PRD §23 把它标成安全里程碑：交付 ReleasePlan、
ReleaseManifest、Git 隔离工作区、索引固定、正式指针、发布锁、幂等、resume/abort、
rollback 与故障注入。ADR-015 记了取舍。

**兼容性影响（重要）**：`publish` 不再是一步。

```
1) novel release prepare --task TASK-000007        # 生成计划（不动正式区）
2) novel approve release REL-000001 --hash <计划哈希> --as author
3) novel publish --task TASK-000007 --as author --confirm
```

没有「已准备且已批准」的计划时 `publish` 直接拒绝（码 2），**不会替作者临时生成
一份再当成批过**——那种便利的代价是作者从没看过那份计划。旧调用方需按上面三步改。

**升级后要做的事**：

- 无数据迁移。旧的 `publish_archive` 归档保留原样；新发布的归档路径多一层时间戳
  （`publish_archive/<任务或发布号>/<时间戳>/`），避免同任务多次发布互相覆盖。
- 第一份索引编号会从 `IDX-000001` 起，与旧 `builds/<时间戳>` 编号并存，互不影响。

### 新增命令

命令行 26 → **27 条**（新增 `release`，含 10 个子命令）。

- `release prepare --task <T>`——把已批准的 staging 翻译成 ReleasePlan。
  含 `01_Canon` 写入时需 `--confirm-canon`；
- `release show <REL> [--json]`——计划、批准、事务日志（逐步状态）、清单、回执；
- `release execute <REL> --confirm`——执行 22 步。含 Canon 写入时仍需 `--confirm-canon`；
- `release resume <REL>`——从中断处继续，不重新准备计划；
- `release abort <REL>`——中止未切换的发布。已切换的拒绝，并指出该用 rollback；
- `release status [--deep] [--json]`——交叉验证指针/清单/索引/日志/回执，
  给出判词；判词非 `OK`/`NO_RELEASE` 时退出码 1；
- `release repair <REL>`——补写缺失回执（仅在指针确实指向该版时）；
- `release rollback --to <REL> --reason <原因>`——生成回退包（新 release_id）；
- `release list [--json]`——所有发布与状态；
- `release unlock [--confirm]`——检查锁是否被持有（获取一次即释放，不删文件）。

### 一、22 步分两段：1–13 在快照里，第 18 步才动指针

Markdown、Git、SQLite 三者不可能塞进一个事务。所以不在 Vault 里就地改：
把 Vault 复制成隔离快照，按**批准的精确字节**落变更，在快照里提交、建索引、
写清单；全部做完才开始切换。

切换是「临时文件 → fsync 文件 → fsync 目录 → `os.replace`」，日志里 16/17/18
三步各记各的（`storage.write_atomic` 把写临时文件和替换做在一起，日志会写成
16、18 都完成而 17 根本没发生）。

正式版只由 `<runtime>/releases/active.json` 决定。它和 `index/active.json`
是两件东西：前者只在切换时变，后者随 `rebuild-index` 变。查询优先读前者，
否则会出现「新发布 + 旧索引」的混搭；清单钉住的索引库不存在时**报错进恢复模式**，
不退回另一份。

### 二、计划与清单分开

| | ReleasePlan | ReleaseManifest |
|---|---|---|
| 何时 | 执行前 | 构建后 |
| 可变 | 是（重准备换哈希，批准失效） | **否** |
| 证明 | 作者批的是这份计划 | 内容长这样，且只长这样 |

清单不写自己的哈希，也不写它引用的 commit 的父提交——写进去就成自引用。
落盘用规范形式（键排序、紧凑分隔符、中文不转义），于是文件字节哈希 ==
`package_hash(对象)`，回执里的哈希可以直接 `sha256_file` 复核。
不可变靠写入方式（`write_new`，已存在且不同就报冲突）+ 每次 `status` 重算比对。

### 三、恢复靠四点交叉验证

指针、清单、事务日志、内容哈希互相印证，**不取最新文件**——崩溃会让时间戳失真，
而那正是最需要判断的时候。

判词：`OK` / `NO_RELEASE` / `RECEIPT_MISSING` / `MANIFEST_MISSING` /
`MANIFEST_HASH_MISMATCH` / `INDEX_MISSING` / `POINTER_CORRUPT`。
最后一种是「读不出来」，此时进入恢复模式，**不自动猜正式版是哪一版**。

`repair` 只在指针确实指向该版、且清单哈希对得上时才补回执。补一张对不上账的
回执比没有回执更糟。

### 四、锁由内核判定

`flock(LOCK_EX|LOCK_NB)`。拿不到就抛，报错带持有人信息（PID、主机、启动标识、
任务、发布号、开始时间）。**不按文件年龄、不按 PID 猜死锁**——PRD §18.4 明令禁止，
那种做法在慢机器上会直接放出第二个发布者。强制处理必须 `release unlock` 显式操作。

平台没有 `fcntl` 时拒绝发布，不退化成「建个文件表示占着」。

### 五、幂等靠编号

`release_id` 在重试中不变，`txn_id` / `index_build_id` 由它派生，续做不会建出
第二份索引。索引编号显式传入时若已存在同名库，**报错而不是顺延**
（`IDX-2-2` 这种顺延会让清单指向一个不存在的库）。

已切换的版本重复执行只补回执与镜像，不再切换指针。

### 六、回退是一次新发布

新 `release_id`、`base_release` 为当前正式版、复用旧版内容与索引、作者单独批准、
回执写明「这是新发布 + 原因 + 对外平台需人工处理」。导致回退的那次发布记录不删。

**「复用旧版 revision」这条按内容实现，不按 commit sha 实现**：每次发布都在自己的
快照里新提交一次，sha 必然不同；把旧 sha 写进新清单会指向本版快照里不存在的提交，
四点交叉验证当场失败。替代做法是逐文件核对「本包文件集 == 目标版文件集」，
多一个少一个不一样都拒绝并列出路径——比比对 sha 更严。

### 七、镜像失败不撤销发布

第 21 步把内容镜像回 Vault 供 Obsidian 阅读。失败只写回执告警，不撤销切换：
正式查询读的是指针与索引，不读镜像。「已切换但镜像没写」是可接受且已记录的状态；
「因镜像失败把指针拨回去」会制造第二个不确定状态。

### 八、故障注入让断点变成回归用例

`NOVEL_RELEASE_FAULT=before:18` / `after:18` 在指定步骤前后抛异常，磁盘上留下的
痕迹与真实崩溃一致（步骤状态 + 指针 + 清单）。`tests/integration/test_t06_release.py`
用它覆盖六类断点，不需要人手工 kill 进程。

### 测试

456 → **505 passed / 1 skipped**。新增 `test_release_model.py`（22 条，哈希稳定性
与规范形式）、`test_release_lock.py`（10 条）、`test_t06_release.py`（17 条断点与验收）。

同时把 `test_workflow_flow.py`、`test_spec_checklist.py`、`test_t05_cli.py` 里
依赖一步发布的断言改成三步流程——不是放宽断言，是把「谁批了什么」按新流程重新钉住。

## 0.8.0 — 2026-11-21

T05「审批、变更请求、影响分析」。四个新命令、五类变更、一道发布闸门。

**兼容性影响**：`approve` 拆成四个子命令
（`artifact` / `proposal` / `release` / `change`），每个都要显式给 `--hash`。
旧的 `approve --task X` **保留**（它已绑文件指纹，`publish` 也照着它校验），
但新内容应当走新入口——旧入口说不出「批的是哪个对象」。`publish` 新增两条拒绝
理由（OPEN BLOCKER、未批准的 RETCON 覆盖），以前能发的任务现在可能被拒，
这是设计意图。

**升级后要做的事**：无需迁移数据。旧批准记录没有 `target_type`，
`inspect` 会按 `NONE` 处理，重批一次即可。

### 新增命令

命令行 22 → **26 条**。

- `inspect <TASK|TASK:路径> [--json]`——批准前要看的七项：候选清单与各自哈希、
  staging 与磁盘的 drift、批准状态、阻断问题、未批准 RETCON、变更范围、风险等级；
- `approve artifact|proposal|release|change <ID> --hash <H>`——四类对象各自绑
  不同的哈希，`--as` 只接受 `author`；
- `issue new|list|resolve|waive`——问题单，三级（BLOCKER/NON_BLOCKING/SUGGESTION）
  三态（OPEN/RESOLVED/WAIVED）；
- `change create|list`——五类变更请求（PLAN_CHANGE/STATE_CHANGE/CORRECTION/
  RETCON/EDITORIAL）；
- `impact <CR-ID>`——生成影响报告：受影响的产物、正文、事件、断言、认知、伏笔、
  依赖七类，报告哈希即变更批准的对象。

### 一、批准绑当前字节，不读 manifest

`inspect` 与 `approve` 都重新计算 staging 文件的 sha256。manifest 与磁盘不一致
不算错误，作为 `drift` 报给作者。`verify()` 逐文件比对，**文件消失也算失效**
（`current` 里取不到 ≠ 没改动）；另有包哈希比对兜「文件都没变但候选集合变了」。
两道闸门各有独立用例，删任一道都会红。

### 二、BLOCKER 不可豁免

`issue waive` 对 BLOCKER 直接拒绝，报错里给两条合法路径：修掉后 `resolve`
（必须写核验依据），或者先批准缩小范围的变更。`inspect` 同时扫任务 ID 与产物 ID
上的 OPEN BLOCKER——同一个问题可能记在任务上也可能记在产物上。

### 三、RETCON 独立审批

`DRAFT → IMPACTED → APPROVED|REJECTED`。没跑 `impact` 不许批；`--hash` 必须与
当前报告一致；报告列出的受影响正文既没 `--rewrite-chapters` 也没 `--narrow-scope`
时拒绝——PRD §19.3 不允许「只改档案就宣称 RETCON 完成」。

`blocking_scope()` 拦「先改档案、后补变更」：有未批准 RETCON 覆盖这批路径时，
普通文件审批停下。

### 四、问题与变更存 runtime

`<runtime>/issues/` 与 `<runtime>/changes/`，不进正式区、不进索引。索引必须是
「Vault + commit」的纯函数，塞进 runtime 状态就会让同一个 commit 建出两个不同
的库。索引里的 `issues` / `dependencies` 两张表**建了但空着**，输出里写明
「建了不等于已支持」。

## 0.7.0 — 2026-11-21

T04「时间线、事件、状态、认知」。三条验收：倒叙查询正确、排不出先后返回
`UNORDERED`、角色误判不污染客观状态。

**兼容性影响**：`rebuild-index` 开始填 `events` / `event_edges` / `assertions` /
`state_changes` / `knowledge` / `revelations` 六张表（原先是空表）。

真库实测：事件 **7** 个（`05_Ledgers/Events/`）、**边 0 条**，断言/状态变化/认知/揭示
全为 **0**。所以查询只会得到 `UNORDERED` 或 `NOT_RECORDED`——
**「表在」不等于「有数据」，「有事件」也不等于「有先后」**，输出里分别报，
`UNORDERED` 时会提示「事件图里 0 条先后关系」。

### 新增命令

命令行 19 → **22 条**。

- `state <实体ID> [--field F] [--after E|--before E] [--json]`——客观状态；
- `knowledge <角色ID> [--about 命题] [--after E] [--json]`——人物认知；
- `reveal <命题> [--json]`——某个真相是在哪一章、以什么方式揭开的。

### 一、事件存成 DAG，比较靠可达性

`EventGraph.compare()` 只给 `BEFORE` / `AFTER` / `SAME`，**没有路径就是
`UNORDERED`**。不按 ID、不按章节号、不按插入顺序兜底。有环直接报错——
环说明设定自相矛盾，此时任何拓扑序都是在错误答案里挑一个。

`latest_among()` 返回**一批**极大元，不是一条。集合大于 1 时调用方必须返回
`UNORDERED`，接口上就不给「取第一条」的机会。

### 二、状态是区间 `[since, until)`

`--after X` 命中左端点（开始事件算已生效），`--before X` 排除右端点（失效事件
算已失效）。没有绑事件的记录视为恒成立。

### 三、客观状态查询不读 `knowledge`

`assertions` / `state_changes` 与 `knowledge` 物理分表，`state_answer()` 里
不存在对 `knowledge` 的 select。不是过滤条件，是两张表隔开——过滤会被改掉，
`select *` 会绕过。

五个返回码 `RESOLVED` / `NOT_RECORDED` / `AMBIGUOUS` / `UNORDERED` / `DISPUTED`
不合并：补救动作各不相同。

### 四、规划记录不入事实表

`02_Planning` 下的章卡、细纲是「计划发生」，不是「已经发生」。索引只把正文派生
记录进事实表，规划目录下的同类文件单独计数（`skipped_planning`）。

## 0.6.0 — 2026-11-20

T03「00_Canon 解析与索引」。六项交付：Markdown/YAML 解析、块 ID、SQLite、
中文检索、revision 绑定、rebuild。

**兼容性影响**：`index/index.json` 不再是索引本体。`status` 会自动认旧的
`index/index.json` 与 `index/index.sqlite` 并提示重跑，但新索引写在
`index/builds/<build_id>.sqlite` + `index/active.json` 指针下。
**需要跑一次 `rebuild-index`**，否则 `search` 会直接告诉你还没有索引。

### 新增命令

- `rebuild-index [--canon-revision <rev>]`——重建索引。给 revision 时从
  `git archive` 读该 commit 的 Markdown，不落临时目录、不切工作树；
- `search <查询> [--limit N] [--json] [--show-index]`——中文/ID/别名检索。

命令行 18 → **19 条**。

### 一、解析与块 ID

`markdown.py`：frontmatter（PyYAML，缺库时遇嵌套**直接报错**而不是猜）+ 正文分块。
块 ID 形如 `p0001`，4 位宽，同一产物内唯一。块按空行切；围栏代码块整段一块；
连续列表/引用/表格各自一块。每块记 `line_start`/`line_end`，**指原文行号**，
可直接回跳。

坏文件（非 UTF-8、非法 YAML、嵌套结构无 PyYAML）**记入 `failed` 后继续**，
不让一份坏档案中断整次重建。

### 二、17 张表，8 填 9 空

表清单一字不差照 PRD §16.1 建。本轮填 `index_metadata` `artifacts` `entities`
`aliases` `source_refs` `narrative_order` `blocks` `fts_blocks`；
`events` `event_edges` `assertions` `state_changes` `knowledge` `revelations`
`foreshadowing` `dependencies` `issues` 建空表留给 T04/T05/T07。

**这不是「已支持」**。`index_metadata.populated`/`empty` 把这件事写进库里，
`rebuild-index` 输出里也分两行打——「建了但本版不填」与「本版会填但这次没数据」
是两件事，混在一起会误导人。

### 三、中文检索按长度分派

| 查询 | 路径 |
|---|---|
| ID / 别名精确 | `identifiers` 精确查 |
| ≥ 3 字 | FTS5 `trigram` |
| < 3 字 | 参数化 `LIKE ... ESCAPE '\'` |

**两字中文词是这次最容易漏的一格**：`trigram` 的窗口是三字符，两字切不出 token，
硬走 FTS 会静默返回 0 条。`test_index_schema.py` 里有一条用例专门钉住
「两字在 FTS 下命中为 0」，防止有人把它当 bug「修」成走 FTS。

另有一层：`name`/`aliases` 只在 frontmatter、**不在正文块里**。实体名通常正文里
也会写到，所以全文路径能命中（实测 972 个实体，`name` 在自身档案正文零出现的是
**0 个**）；但有一分别名是描述性称呼，正文中一次都不出现（实测 136 个别名里
**35 个**），只搜正文永远捞不到那份档案。所以正文空手时再走一次名称路径
（`mode=name`，结果里注明「正文里没有这个词；命中的是实体名或别名」）。

FTS5 `trigram` 不可用时**不用 `unicode61` 顶上**——那会让中文按整段切词、
`doctor` 却报「FTS 可用」。退化路径是整库不建虚表、`fts_mode=none`、全走 LIKE，
慢但结果对，原因写进 `fts_blocks_unavailable` 表。

每条命中带 `path` / `block_id` / `line_start`。块 ID（`p0007`）与行号是两套坐标：
前者用于跨命令引用，后者能直接跳到原文那一行——只给块 ID 还得自己数段落。

### 四、不原地重建

每次重建写 `index/builds/<build_id>.sqlite`，写完才切 `index/active.json` 指针。
**全程不覆盖任何已存在的文件**，读者要么拿到旧指针、要么拿到新指针。

比「临时文件 + `os.replace`」更彻底：崩溃留下的半成品永远只是多一个没人指向的
`.sqlite`，删不删都不影响正确性。代价是旧 build 不自动清（R-18）。

### 五、revision 绑定

`--canon-revision` 走 `git archive` 直读该 commit，索引里记的 `canon_revision`
就是实际读的那棵树。修了一个会让索引**谎报 revision** 的坑：`git -C` 会向上找
外层仓库，Vault 不是仓库根时会把公仓 HEAD 当成自己的。现在先校验
`rev-parse --show-toplevel` 等于 Vault 本身，否则明确报错。

### 六、其他

- `doctor` 新增 SQLite 段：报版本 + trigram 可用性，不可用时直接说「会退化为 LIKE」；
- `status` 打印库文件名（`builds/<id>.sqlite`）、产物/块数、`fts=`、依据的 revision、历史 build 数；
- 索引布局收敛到 `indexing/locate.py`，CLI 不再各处重复定义路径常量。

### 七、在真实 Vault 上跑出来的三个缺陷

代码写完时单测全绿、`rebuild-index` 也「成功」，但拿真实库（1073 份 Markdown）一跑，
三处都是**不报错**的那种错，全部已修并补了回归：

1. **归档一份没跳过**。排除名单硬编码成 `90_Archive`，而 V4.0 的 Vault 顶层归档叫
   `99_Archive`。于是旧 PRD（2611 行）、旧实体索引（75KB）整个被索引进库：
   产物多 6 份、块多 928 块。注释写着「跳过运行态/归档」，实际只跳了项目内的 `90_Archive`。
   **修法**：目录名改从配置取（`skip_dirs_from_layout`），`rebuild-index` 一行打印
   「跳过运行态/归档 N 份（07_Staging、08_Reviews、90_Archive、99_Archive）」——
   跳过什么必须看得见，否则下一个人只会再被骗一次。
2. **模板占位符被当成重号**。`id: {{id}}` 的模板文件被计入 `duplicate_ids`，
   真实库上多报一个 `{{id}}`。`validate` 早就排除这种（`if not eid or "{{" in eid: continue`），
   builder 没跟上——同一个库在两处给出不同结论。**修法**：判据统一，占位符退化成文件名。
3. **生成的看板压过真正的档案**。`LIKE` 结果按插入顺序（即路径字典序）返回，
   `00_System/Dashboards/` 因为 `00_` < `10_` 永远排最前。真实库上搜一个两字人名，
   前三条全是 `工作台.md` 的摘录，`10_Projects/` 下那份人物档案被挤到看不见。
   这不只是难看：看板是从档案**派生**的，让人先引用派生页等于在索引里绕了个循环。
   **修法**：结果分层（作品内容 0 > 共享资产 1 > 系统文档与生成看板 2），层内再按 rank / id。

第 1、2 条都是「单测覆盖不到」的：夹具里的 Vault 只建了 `90_Archive`，
恰好跟错误常量同名；模板文件也只有真实库才有。**这类缺陷只有拿真库跑才暴露。**

### 八、公仓零设定：把残留的作品名接回版式配置

这一节不是 T03 的功能，是同一天发现并清掉的**已公开泄露**。

用真库 902 个名字（实体名 + 别名 + 档案标题）反扫公仓文本文件，命中三处：

| 文件 | 残留 | 性质 |
|---|---|---|
| `maps/jiuzhou.py` | 12 项：世界名、三层行政示例、规则双链、界外设定说明 | 整段页面模板 |
| `maps/admin.py` | 7 项：区划表页的 title / aliases / 官制双链 | 页面模板 |
| `docs/operations/known_risks.md` | 5 项，含**情节标题** | 文档举例 |

前两处的 docstring 都写着「代码里不留作品设定」，`jiuzhou_layout.yaml` 也已经装好了
`naming` / `special_states` / `fu_notes`——**只是代码没接上**。所以修法不是「把模板搬出去」，
是「把已经存在的变量接上」，再补四组缺的配置：

- `naming.admin_aliases` / `office_table` / `office_note`（区划表页的别名与官制双链）；
- `state_cities`（州城即府城的对应，原先是写死的字面量）；
- `page_notes.special_note` / `same_city_example` / `links`（页面补充文案与关联条目）。

**验收按「输出逐字节不变」做**，不是「命令没报错」：

| 产物 | 结果 |
|---|---|
| 方位图 `.canvas` | **零差异** |
| 层级树 `.canvas` | 节点 111 / 边 110，逐节点比对「同 id 不同内容: 0」；只有 JSON 缩进与 `-0.0` 表示不同 |
| 地图页 `.md` | 只差 `generated_by`（旧值是 `00_System/scripts/…`，代码 0.4.1 起就是这个新值，Vault 一直没重跑） |

改的过程中差点引入一处无关变更：把「八州环列」写成 `{len(inner)} 州环列`，输出从
「八州」变「8 州」。**数据驱动了，文案也变了**——加了个 `_cn_count()` 把 1–10 转回中文数字，
才回到零差异。这类「好心改成动态、结果改了字面」的差异，只有逐字节比对才抓得到。

`privacy_scan.py` 对这三处**一个都没报**（166 文件、0 错 0 警）：它的规则是文件名形态、
绝对路径、密钥，**不看正文里有没有作品名**。这是规则缺口，记在 R-22，没有顺手补——
补它需要一份真实名清单，而清单本身就是私人数据。

### 测试

新增 5 个文件共 **75 条**：`test_markdown`（13）、`test_index_schema`（17）、
`test_index_build`（15，含 3 条跳过）、`test_index_search`（18）、
`test_t03_cli`（12，含 4 条跳过）。全量 **396 passed / 1 skipped**。

跳过的都是环境限制（沙箱不能 `git init` 写 `.git`），不是代码缺陷。

两条关键回归做了「退回修复必须失败」的验证：把 `order by` 改回原样，
`test_project_content_ranks_before_generated_dashboard` 精确报出
「第一条是 00_System/Dashboards/工作台.md」，改回来即通过。

## 0.5.1 — 2026-11-20

修一个**静默错库**的缺陷：全局参数 `--vault` / `--runtime` / `--project` /
`--log-level` 只有写在子命令**之后**才生效，写在之前会被无声丢弃。

验收 T02 时实际撞上：`novel --vault <库> validate` 报「未配置作者 Vault」，
而 `novel validate --vault <库>` 正常。危险之处不在报错——报错还算好的；
真正糟的是同时给了 `--vault` 和别的来源时，命令会**安安静静作用在另一个库上**。

**兼容性影响**：无。只是让本来就声称成立的前置写法真的成立。

### 根因

argparse 解析子命令时会新开一个 namespace，解析完把它**全部属性**搬回主
namespace。于是子命令那份 `--vault` 的默认值 `None` 覆盖了主命令刚解析出的值。
这跟「先解析后合并」的直觉相反，所以看代码不容易看出来。

### 修法

拆成两份参数组：主命令用真实默认值，子命令用 `argparse.SUPPRESS`——
「没写就不产生属性」，主命令那份才留得住。嵌套子命令（`task create`、
`maps paths`、`schema list|check`）共用同一份，一并修好。

### 测试

新增 `tests/unit/test_cli_arg_order.py`（7 条）：前后两种位置、嵌套子命令、
不写时的默认值、两处都写时后者生效、以及一条端到端比对两种写法输出一致。

**已实测该测试能抓到缺陷**：把修复退回原版后，正好是 3 条「前置」用例失败、
4 条「后置与默认值」用例通过——与根因判断完全吻合，不是碰巧全绿。

---

## 0.5.0 — 2026-11-20

完成 PRD §23 **T02：Schema、ID 与哈希**。六项交付（第 8 章模型 / JSON Schema /
fixture / 稳定 ID 分配 / 字节规范化 / 包哈希）与三条验收全部到位。

**兼容性影响**：`schemas/` 由 6 份增至 22 份，全部加 `additionalProperties: false`。
`entity` 的 `required` 从 `[id,type,name,status]` 改为 `[id,type,name]`——库内 966 份
档案都有 `canon_status` 而多数没有 `status`，旧 required 会让它们全部非法。

### 新增

- **`schemas/` 22 类契约**（PRD §8.4 的 18 类 P0 + 现有 6 类加严）：
  `entity`/`event`/`chapter`/`memory`/`project`/`task` 之外，补
  `artifact`/`source_ref`/`assertion`/`state_change`/`knowledge`/`revelation`/
  `foreshadowing`/`dependency`/`issue`/`approval`/`change_request`/
  `import_manifest`/`release_plan`/`release_manifest`/`transaction_journal`/
  `execution_receipt`。共享片段在 `_common.schema.json` 的 `$defs`。
- **`schema.py`**：纯标准库的 draft 2020-12 子集校验器。支持
  `$ref/$defs/type/enum/const/pattern/format/长度/数值/items/required/properties/
  additionalProperties/oneOf/anyOf/allOf/not`；不支持的 keyword 当注解忽略并在
  模块 docstring 列明。**不引入 jsonschema 或 pydantic**（见 ADR-011）。
- **`ids.py`**：稳定 ID 分配。取号同时看现存最大号与审计历史最大号（删档不补号）；
  本前缀有重号即拒绝；锁文件防并发；每次分配写 `<runtime>/id-allocations.jsonl`。
  复合型 ID（`EVT-CH-0001-001`）识别为不合法，不计入号段。
- **`canonical.py`**：UTF-8 去 BOM / CRLF→LF；规范化 JSON（键排序、紧凑、中文不转义、
  禁 NaN/Infinity）；`package_hash` 与 `diff_paths`。
- **`frontmatter.py`**：把原本只在 `cli/_common.py` 里的解析挪到底层，成为唯一实现。
  行为变化：缩进的嵌套行不再被当成顶层键（旧实现把 `source_refs` 下的
  `file_sha256` 读成了顶层键）。已在真实库 1085 份文件上全量比对：新结果是旧结果的
  严格子集，`id`/`type`/`status`/`title`/`canon_status` 零差异。

### 命令行（15 → 18 条）

新增 `schema list`、`schema check`、`allocate-id`、`package-hash`。
`allocate-id` 默认预演，`--confirm` 才发号。

### 测试

新增 167 条（schema 契约 93、canonical 27、ids 32、CLI 集成 15），全量 312 通过。
schema 契约测试对**每一类**都做三件事：删一个必填键、塞一个未知字段、用 invalid 样本，
少一环就红。

## 0.4.2 — 2026-11-20

补齐 PRD §23 **T01 工程骨架**的两项缺件：运行日志与 Mock LLM。至此 T01 七项交付
（pyproject / CLI / 配置 / 日志 / pytest / Mock LLM / 目录初始化）与三条验收全部到位。

### 新增

- **`logs.py` + `--log-level`**：每次命令在 `<runtime>/logs/novel.log` 留一对
  `start` / `done`（含命令、项目、退出码、耗时）。`runtime_subdirs` 里早就有 `logs`，
  此前没有任何代码往里写。级别取 `DEBUG`/`INFO`/`WARNING`/`ERROR`/`OFF`，
  终端要看设 `NOVEL_LOG_CONSOLE=1`（走 stderr）。
- **`adapters/mock.py`**：`LLMClient` 契约 + `MockLLM`（脚本化/回调/兜底三选一，
  记录每次调用）。确定性、不联网、不读任何 API Key 环境变量。
  真模型接入属 T08，此处只钉形状。
- 16 条测试：日志 7 条（含「重复 setup 不叠 handler」「写不进去要降级」
  「不碰 stdout」）、Mock 8 条（含 socket 打死仍可跑）、重复初始化 1 条
  （逐文件 sha256 比对，比「退出码非 0」更强）。

### 修复

- 同一进程内多次调用（测试就是这样）原先没有日志设施；现按「先清 handler 再装」实现，
  避免一条日志写 N 遍。

### 兼容性

- 无破坏性变更。终端输出一字未改；日志只进文件。
- 新增全局参数 `--log-level`，默认 `INFO`；不想要日志传 `--log-level OFF`。

---

## 0.4.1 — 2026-11-20

`validate` 的 ID 查重扩到素材库。起因是真实 Vault 里 `ASSET-0001` 被
`Battles/` 与 `Settings/` 两份文件同时占用，而旧版 `validate` 报「重复 0 份」。

### 变更（行为）

**`validate` 现在会因素材库重号而失败。** 查重范围由「Canon 六类」扩为
「Canon 六类 + `04_Story_Assets`」。素材虽按 `Battles/` `Settings/` 等分类分目录存放，
ID 却是**全库连续编号、不按分类分段**（素材卡模板原话），所以必须跨子目录一起看。
汇总行同时给出两侧份数：`实体 986 份（Canon 966 / 素材 21），重复 1 份`。

### 修复

- 旧版只扫 `CANON_KINDS`，素材目录根本不在扫描范围 → 素材重号永远隐形。
  这是漏报，不是「干净」。
- 模板里的 `id: {{id}}` 是占位符，按 `rebuild-index` 的同一判据跳过，不参与查重、
  也不算「id 与文件名错位」。

### 兼容性

- 有素材重号的 Vault，`validate` 退出码由 0 变 1。这是预期行为，改号后即恢复。
- 无重号的 Vault 输出只多了一段份数说明，退出码不变。
- 新增 3 条回归测试：跨子目录重号报错、占位符不误报、号段唯一时正常通过。

---

## 0.4.0 — 2026-11-20

两个地图生成器从「Vault 里的脚本」重编码为「系统命令」，许可证定为 GPL-3.0。

### 新增

- `src/novel_system/maps/`：`jiuzhou.py`（重画地图页与两张 canvas）、`admin.py`（资产表 → 行政区划表）、
  `__init__.py`（路径解析 `MapPaths`）
- CLI 新增 `maps paths` / `maps import-admin` / `maps build`，命令总数 16 → 19
- `tests/integration/test_maps.py`：4 条，覆盖「缺版式报错」「路径落在项目内」「默认预演不写盘」「落盘后二次运行为 0 改动」
- `LICENSE`：GPL-3.0 全文（gnu.org 官方 `gpl-3.0.txt`）

### 变更（破坏性）

**地图生成器不再有默认的「直接写盘」行为。** 旧脚本跑起来就改文件；现在默认预演，
必须加 `--confirm` 才写。理由：这两个生成器整段替换档案正文，一次误跑会改 39 份 Canon。
迁移：把 `python3 00_System/scripts/build_jiuzhou_map.py` 换成
`novel maps build --confirm`；只想修图用 `--maps-only`。

**版式配置从 Vault 移到项目内**：`00_System/Config/jiuzhou_layout.yaml`
→ `<project>/00_Project/jiuzhou_layout.yaml`。多项目时各项目可以有各自的排版。

### 修复

- 两个生成器此前硬编码 `Path(__file__).parents[2]` + `private/00_Canon/...`，
  V4.0 目录重排后全部失效；现在路径唯一来源是 `Config` 与 `ProjectPaths`
- `admin.py` 原先在模块导入时读版式文件，导入即报错；改为 `configure()` 时读
- `PLANNED` 跨调用累积：同进程跑两次会把上一轮的路径带进这一轮的统计（测试当场抓到）

### 兼容性

- 三个 `maps` 子命令均要求能解析出项目（`--project` 或 `active_project`）
- 生成结果与迁移前逐字节一致：11 大区 / 28 府 / 66 郡 / 108+111 节点，预演报「0 个文件改动」

---

## 0.3.0 — 2026-11-20

补齐 §3.2 的仓库治理、附录 A 的十篇 ADR、§14 的验收测试，以及 §12 要求的隐私扫描。
命令集不变，但**项目解析规则收紧了**，这是本次唯一的破坏性变更。

### 新增

- `CONTRIBUTING.md` / `SECURITY.md` / `CODE_OF_CONDUCT.md`：提交前四项检查、泄露处置流程、行为准则
- `.pre-commit-config.yaml`：本地提交前跑隐私扫描、私钥检测、大文件拦截、ruff
- `.github/workflows/ci.yml`：三个 job —— `privacy`（工作区 + 近 50 条提交）、`test`（3.11/3.12 双版本，pytest + selfcheck + Schema 可解析）、`guard`（无私人目录形态、无硬编码家目录）
- `.github/pull_request_template.md` 与两个 Issue 模板
- `docs/architecture/adr/`：ADR-001 ~ ADR-010，每篇含背景、问题、决策、备选方案、影响、状态、批准人、日期；索引页附「最重要的三句话」
- `docs/user/`：上手指南、日常六个动作；`docs/developer/`：代码分层、测试约定
- `docs/operations/known_risks.md`：12 条已知风险，按能力缺口 / 数据问题 / 工程流程 / 结构取舍分类
- `scripts/privacy_scan.py`：8 类规则（私人目录形态、真实档案命名、本机绝对路径、凭据、禁止后缀、垃圾文件、大文件、历史提交），零依赖、可被测试、报告里的样本自动打码
- `tests/acceptance/test_spec_checklist.py`：31 条，逐条对应 §14.1–§14.5
- `tests/integration/test_project_isolation.py`：7 条多项目隔离用例
- `tests/conftest.py`：把用户级配置与公仓路径都指到临时目录，避免测试碰到作者的真实 Vault
- `tests/fixtures/`：完全虚构的样本稿与夹具规则

### 变更

- **项目解析不再推断**：此前「Vault 里只有一个项目就用它」，现在必须由 `--project` 或 `Vault_Config.yaml` 的 `active_project.project_id` 指定。单项目时推断看起来无害，但第二个项目建起来后同一条命令的语义就变了，而写入类命令选错项目改的是另一部书的 Canon。
- `init-project` 建第一个项目时自动写入 `active_project`（只填空值，不覆盖作者已有配置），所以日常命令仍不必带 `--project`
- 任务记录新增 `author_id`（`schemas/task.schema.json` 同步置为必填）：任务的身份由 author + project + task_id 三者构成
- `README.md`：命令表补 `--project` 语义、测试节改为一键三跑、新增文档索引
- 版本号 0.2.0 → 0.3.0

### 兼容性

- **破坏性**：任何未指定 `--project` 且 Vault 配置里没有 `active_project` 的写入命令，现在返回退出码 2。升级后先跑一次 `novel status` 确认当前项目
- 已有 `tasks/*.yaml` 缺 `author_id` 字段时仍能读取（空字符串），新建任务会带该字段
- 其余命令的参数、退出码、输出语义不变

### 测试

122 条（单元 22 / 集成 43 / 安全 26 / 验收 31）。另有两项不依赖 pytest 的检查：
`scripts/selfcheck.py` 端到端 21 项、`scripts/privacy_scan.py` 全仓扫描 0 发现。

## 0.2.0 — 2026-11-20

V4.0 §5.3 的命令集补齐（6 → 16 项），CLI 从「只能看」变成「能走完整创作链路」。

### 新增

- `task create` / `task inspect`：任务记录落 Runtime，含九种任务类型与六态状态机，跳步被拒绝
- `stage`：Agent 产出唯一落点，强制分栏（drafts/reviews/memory/plans/preflight/…），记录目标文件的 `base_sha256` 用于判冲突
- `diff`：staging 与正式文件逐条比对，输出四种状态（新增/改动/一致/冲突）
- `approve`：只接受 `--as author`，批准绑定每个文件的 sha256；`--reject` 驳回
- `publish`：四道闸（有批准 / 内容未被换 / 无越权新增 / 目标合法），Canon 需 `--confirm-canon`，旧版先归档
- `adopt`：登记作者手改并推进基线，不回滚不覆盖
- `backup`：清单 + 全量回读校验，按 §11.2 判定「复制成功且校验通过」才算完成
- `restore`：默认预演、不覆盖现存文件、`--overwrite` 前另存被覆盖版本
- 操作日志 `<runtime>/logs/ops.jsonl`：op_id / 时间 / 操作者 / 输入 / 输出 / 结果，只记元数据
- `docs/operations/cli_reference.md`：16 项命令的语义、拒绝条件与退出码

### 变更

- `validate` 增强：实体 ID 重复、id 与文件名错位、全 Vault 双链解析（含 `.canvas`、剥离 `#标题`）、跨项目引用；新增 `--strict`
- `status` 增强：任务、等待批准项、备份、最近操作
- `doctor` 增强：公仓与 Vault 互相包含检查；Runtime 子目录就绪检查
- `rebuild-index`：跳过 `07_Staging` / `08_Reviews` / `90_Archive`；`id: {{id}}` 模板占位不参与重复统计；索引输出 `schema_version: 2`
- 配置解析：认得 V4.0 `Vault_Config.yaml` 的 `layouts.*` 与 `runtime_root` 写法（此前真实 Vault 的 runtime 恒为未配置）
- `project.yaml` 的目录字段同时支持 Vault 相对与项目相对两种写法
- 原子写保权限：新文件 0644，覆盖时保留原权限（此前 mkstemp 会留下 0600）

### 兼容性

- 旧 6 条命令的参数与返回码不变，既有脚本不受影响
- `validate` 在没有 `--strict` 时仍返回 0，断链只作警告

### 测试

61 条（单元 21 / 集成 36 / 安全 4）。另有 `scripts/selfcheck.py` 端到端 21 项冒烟，
不依赖 pytest 与 tempfile，适合真机快速自检。

## 0.1.0 — 2026-11-20

首个骨架版本，不构成可用系统。

### 新增

- CLI 六条命令：`doctor`、`status`、`validate`、`init-author`、`init-project`、`rebuild-index`
- 配置系统：五级优先级解析，代码内无私人路径
- 路径安全：`..` 拒绝、符号链接逃逸拒绝、Vault 边界校验
- Schema 六份：project / entity / chapter / task / event / memory
- 模板七类：author / project / entity / planning / manuscript / memory / reports
- 九 Agent 定义与统一权限矩阵
- 六份工作流定义与基线质量门
- 测试：25 条（单元、集成、安全）

### 已知不支持

- 尚不能写入 Canon、不能发布、不能备份恢复（后续阶段）
- 尚不能注册到 WorkBuddy
- 许可证未定（`LICENSE_PENDING`）
