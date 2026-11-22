# 代码结构

## Vault 目录（V1.0 §8）

```
00_作者工作台/        系统级：Policies、Dashboards、Schemas、Vault_Config.yaml
01_小说项目/<项目>/
  ├── 00_项目管理/      进度、决策记录
  ├── 01_灵感与创意/
  ├── 02_世界知识库/    17 类，按 frontmatter 的 type 归位
  ├── 03_故事规划/      故事总纲 / 卷级粗纲 / 章节细纲 / 场景节拍 / 故事线 / 伏笔与悬念
  ├── 04_正文/          初稿 / 修订稿 / 正式定稿
  ├── 05_记忆与连续性/
  ├── 06_素材与研究/
  ├── 07_审核与报告/
  ├── 08_工作流与任务视图/
  ├── 09_版本与变更记录/
  ├── 10_项目配置/      project.yaml（目录名与路径的唯一来源）
  ├── _staging/        Agent 提案落点，不得被查询当成正式 Canon
  └── 90_Archive/      旧稿与原始素材（V1.0 无归档位，保留）
02_作者级通用资料/    跨项目模板
99_Archive/           旧系统全量（不在 V1.0 规范内，删不得）
```

**代码里一个目录字面量都不该有。** 需要目录名就从 `ProjectPaths` 取，
它读 `10_项目配置/project.yaml`，读不到才退回 `paths.DEFAULT_DIRS`。
`config/defaults.yaml` 的 `layout` 段是布局的唯一配置源。

## 分层

```
cli/          只做参数解析与输出格式化，不写业务判断
  ├── __init__.py   parser 与命令分发
  ├── read.py       doctor / status / validate / diff
  ├── init.py       init-author / init-project
  ├── index_cmd.py  rebuild-index / search
  ├── timeline_cmd.py state / knowledge / reveal
  ├── approval_cmd.py inspect / approve <object>
  ├── change_cmd.py issue / change / impact
  ├── flow.py       task / stage / diff / approve / adopt / publish
  ├── release_cmd.py release prepare / show / execute / resume / abort / status / repair / rollback / list / unlock
  ├── backup_cmd.py backup / restore
  ├── maps_cmd.py   maps paths / import-admin / build
  ├── schema_cmd.py schema list / check、package-hash
  ├── import_cmd.py import scan / report / stage / where / list
  ├── agent_cmd.py  preflight / agent list / agent prompt / agent usage
  ├── work_cmd.py   plan show|check|derive、write、review submit|status、proposal *、deps *
  └── id_cmd.py     allocate-id
config/       五级配置解析 + Vault_Config 兼容折算
paths.py      ProjectPaths：项目内目录解析与边界校验
logs.py       运行日志：写 <runtime>/logs/novel.log，与 audit 的审计日志是两件事
version.py    版本号唯一出处（__version__ 与 SPEC_VERSION），发布清单记的就是它
frontmatter.py 顶层键值的**轻量**解析（只认 `key: value`，缩进行跳过）
markdown.py    Markdown 解析与块 ID：frontmatter（走 PyYAML，**嵌套结构只有这条路能解**）+ 按空行切块 + 行号回指
canonical.py  字节规范化（去 BOM、LF）与规范化 JSON 包哈希
schema.py     JSON Schema 校验器（draft 2020-12 实用子集，零依赖）
ids.py        稳定 ID 分配：前缀表、分级查重（正式区/待发布/归档）、加锁、审计
storage/      哈希、原子写、不覆盖写、可验证复制
audit/        操作日志（ops.jsonl）与操作者校验
tasks/        任务记录与状态流转
approvals/    批准记录（绑定对象类型 + 内容 sha256，按当前字节复核）
changes/      问题单（ISSUE）、变更请求（CR）、影响分析；存 runtime，不进索引
staging/      staging 落盘与 manifest
reviews/      08_Reviews 的轻量写通道（`novel note`）：frontmatter 走 review_note 契约、
              原子写、同名即拒；不进 PUBLISH_TARGETS、不走 approve、不算签字基线
release/      发布协议（ADR-015）
  ├── model.py     ReleasePlan / ReleaseManifest / Journal / Receipt / ActivePointer 与 22 步名表
  ├── store.py     目录约定与读写：releases/<REL>/、receipts/、locks/；指针损坏即抛
  ├── engine.py    22 步执行、断点恢复、状态判词、故障注入
  ├── workspace.py 隔离快照：复制树 / 落精确字节 / 扫描 / 提交
  ├── gates.py     六道闸（产物批准、BLOCKER、RETCON、正式区、Canon 二次确认）
  └── lock.py      flock 发布锁；不按年龄或 PID 猜死锁
importing/    原稿导入（ADR-016）
  ├── model.py     定位符、Chapter/Paragraph/ImportFile、ImportManifest（十一维度）
  ├── scanner.py   格式/编码/哈希识别；非 UTF-8 与 P1 格式明确拒绝
  ├── preserve.py  原始字节保全：写后逐份回读 sha256
  ├── parser.py    切章与段落归属：标题层级、段落总数核对
  ├── extract.py   候选提及（词典来自 Canon 的 name/aliases），一律 approved=false
  ├── sampling.py  固定种子抽样；只量定位准确率
  └── report.py    清单/明细落盘、分批入 staging、台账、已发号持久化
baseline/     作者编辑基线（adopt）
backup/       备份、校验、恢复
adapters/     模型适配层；当前只有 MockLLM（确定性、不联网、不需密钥）
indexing/     SQLite 索引：schema 建表 / source 取文件 / builder 建库 / search 查询 / locate 定位
timeline/     事件 DAG（graph）/ 状态区间与认知查询（query）/ 前言提取（model）
agents/       Agent 适配器与写前资料包（ADR-017）
  ├── registry.py  九角色定义 + 权限矩阵的机器副本（matrix_row() 逐字渲染 md 那一行）
  ├── budget.py    三档预算、Truncation / Overflow、软额度必须是剩余额度
  ├── tools.py     能力位 → 可用工具；七个只读工具；ToolContext 统一记计量
  ├── prompts.py   九段装配（前九项 REQUIRED_KEYS）+ 五段公共文本常量
  ├── metering.py  <runtime>/usage/YYYYMMDD.jsonl，一行一次调用，只追加、不记正文
  ├── runner.py    信封：去围栏 → 解析 → schema 校验 → 核对 role；失败不猜
  └── preflight.py 八级检索落成七次工具调用，出 PRD §13.4 结构，判 PASS/BLOCKED
validation/ 发布前的语义校验（ADR-018）
  ├── proposals.py     记忆提案：KINDS 十类、绑源哈希、模型失败退骨架、declare-none
  ├── dependencies.py  依赖四级判定（VALID/STALE/NEEDS_REVIEW）、传递只走一级
  ├── closure.py       共同发布闸：四种阻断码，供 deps closure 与 release prepare 共用
  └── contract.py      发布闸的契约校验（AC-11/12/13/49）：ID 形态与唯一、schema、
                       来源与双链目标存在、引文字节一致、被删块仍被引用；
                       只校验本次变更；新增产物严格、既有产物对历史形态豁免
workflows/  创作链路（ADR-018）
  ├── plan.py           §10.2 十六字段认法、八项必需、只派生确定性可读的两处、写回只增不覆盖
  ├── draft.py          两道前置（plan.check + preflight PASS）、目标路径结构性固定、
  │                     只落 staging；_target_rel() 拼完再校验不在 Canon 下
  ├── review.py         风险路由（只能升不能降）、按风险配角色、自审拒绝、返修预算、
  │                     规则检查只判形状；ReviewStore 在 <runtime>/reviews/
  └── text_approval.py  正文批准：审过 + 角色齐 + 无 BLOCKER 才批，绑整份字节 sha256
```

依赖方向严格单向，越往下越不知道上层：

```
cli → release / workflows / validation / importing / agents / backup / staging / tasks / approvals / changes / baseline
    → timeline / indexing
    → paths / storage / audit / config
    → errors
```

`storage/` 与 `errors.py` 不 import 任何业务模块。这条约束让底层可以被单独测试，
也让「换掉写入实现」不需要改动业务代码。

## 三条不可动摇的约定

**1. 路径只能来自配置。** 代码里不出现 `01_Canon`、`10_Projects` 这类目录字面量。
需要目录名就从 `ProjectPaths` 取，或者从 `project.yaml` 读。

**2. 写入必须过边界校验。** 任何落盘前 `ensure_inside(target, vault)`。
`..`、绝对路径、符号链接逃逸三种情况都在 `tests/security/` 里有对应用例。

**3. 业务模块不 print。** 输出只在 `cli/` 层。业务层返回结构化结果（`PublishResult` 之类），
由 CLI 决定怎么显示。这样同一套逻辑可以被测试直接调用，不必解析 stdout。

**4. 日志不写 stdout。** 运行日志只进 `<runtime>/logs/novel.log`（要看终端加
`NOVEL_LOG_CONSOLE=1`，走 stderr）。stdout 是给作者的输出契约，混进日志就没法读了。
写不进去（Runtime 只读、磁盘满、路径被普通文件占住）必须降级为空操作，**不许让命令失败**——
日志是旁路，不能反过来决定命令成败。

## 几个容易踩的实现细节

**写入不能假设 `os.replace` 可用。** 某些受限环境会拦 rename，此时临时文件删不掉、
目标文件也写不进去。`storage.write_atomic` 因此按目录记忆「能不能 rename」，失败就直接写，
每个目录最多留一个 `.novel-tmp`。

**`stage` 的 `--to` 是正式路径，不是 staging 路径。** 文件实际落在
`07_Staging/<TASK-ID>/<分栏>/<正式路径>`，同时记下目标正式文件当时的 `base_sha256`——
这是后面判断「提案生成后作者是否改过文件」的依据。

**批准记录存的是逐文件 sha256，不是任务级哈希。** 任务级哈希做不到「只批了一部分」的粒度，
也没法在发布时定位到底是哪个文件被动了。

**frontmatter 有两种解析器，别混用。** `frontmatter.py` 只认顶层 `key: value`，纯标准库，
给 CLI 的只读命令用（`validate` 只需要拿 `id`，没必要为它拉起 YAML）。`markdown.py` 走 PyYAML，
能读嵌套（`source_refs`、`aliases`），给索引用。没有 PyYAML 时 `markdown.py` **遇到嵌套直接抛
`ParseError` 并跳过该文件**，不猜——猜错的表现是「某些档案的别名索引悄悄少了一半」。
早期 `frontmatter.py` 的旧实现会把 `source_refs` 下的 `file_sha256` 读成顶层键，
全量 1085 份比对确认是修正而非回归。

**块 ID 只在产物内唯一，不是全库唯一。** 两份档案都有 `p0001` 是正常的，
所以 `blocks` 的唯一键是 `(project, path, block_id)`。PRD §9.1 说的是「同一产物内唯一」，
按全库唯一去建约束会让第二份档案直接插不进去。

**FTS5 外部内容表要靠触发器同步。** `fts_blocks` 用 `content='blocks', content_rowid='id'`，
不建触发器的话 `blocks` 有数据而 `fts_blocks` 空——查得到表、查不到结果，而且不报错。
本版整库重建，没有 UPDATE/DELETE 路径，所以只需要一个 `after insert`。

**`git -C <dir> rev-parse` 会向上找外层仓库。** Vault 若不是自己就是仓库根，
建在公仓子目录下的临时库会认成公仓的 HEAD——索引会**谎报**自己依据某个从没读过的 revision。
`source.py` 的 `_own_repo()` 先用 `--show-toplevel` 校验，不成立就报错而不是猜。

**argparse 子命令的默认值会盖掉主命令已解析的值。** 解析子命令时新开一个 namespace，
解析完把属性整体搬回主 namespace，于是子命令那份 `--vault` 的默认 `None` 抹掉了前面给的值。
主命令用真实默认值、子命令一律 `argparse.SUPPRESS`。**危害不是报错而是静默错库**，
回归测试在 `tests/unit/test_cli_arg_order.py`。

**隐藏目录判定用 Vault 内相对路径。** 用绝对路径判会把 `.tmp/vault` 这种临时树整树误判，
导致索引 0 条——这个 bug 真实发生过一次。

**发布时不要在 Vault 里就地改。** 22 步里 1–13 全在隔离快照上做，第 18 步才动指针。
就地改写到一半崩掉，正式区就是「混合版本」——这正是 PRD §17.1 的底线。
快照复制整棵树的代价见 `known_risks` R-27。

**恢复不要「取最新文件」。** 指针、清单、日志都带时间戳，取最新看着合理，
但崩溃恰恰会让时间戳失真（写了日志没换指针，或换了指针没写日志）。
必须四点交叉验证，判词见 `release/engine.py::status` 的文档表。

**正式版指针只有一个。** `releases/active.json` 决定正式版，`index/active.json`
只是「上次重建的结果」。查询优先读前者；清单钉住的索引库不存在时**报错进恢复模式**，
不退回另一份——退回去就是「新发布 + 旧索引」的混搭。

**日志的 16/17/18 三步要各记各的。** `storage.write_atomic` 把「写临时文件」和
「替换」做在一起，用它日志会写成 16、18 都完成而 17（fsync）根本没发生。
`_write_pointer_atomic` 因此手写了这三步。

**执行阶段不能依赖任务记录还在。** 计划自带 `staged_paths`，按它取字节。
任务 yaml 是运行态、可能被清理；把它当唯一依据，清理过一次就再也续不了发布。

**锁的归属由内核判，不看文件年龄也不看 PID。** 锁文件里写持有人只是给人看的。
「超过 N 分钟就当残留删掉」在慢机器上会直接放出第二个发布者（PRD §18.4 明令禁止）。
没有 `fcntl` 时拒绝发布，**不退化成锁文件**——假锁比没锁更危险。

**规范化 JSON 让「文件字节哈希 == package_hash(对象)」。** 所以回执里的清单哈希
可以直接 `sha256_file` 复核，不必再解析一遍。四类发布记录都走这条，
并有两条测试盯着它们与 `schemas/*.schema.json` 的契约。

**软额度必须是「剩余」额度。** `Budget.soft_allowance` 要扣掉 `_soft_used` 与
`_hard_used`。初版没扣，八个检索级各领一份完整配额，32000 的预算装进了 48000 的内容——
不是估得不准，是**每一级都以为别人没花**。回归测试
`test_soft_allowance_is_not_double_spent`。

**硬约束要排在软资料之前放置。** `_fill_foreshadowing` 在 `_fill_states` 之前调用。
未回收伏笔属 §13.3 必须检查项，排在软资料后面会出现「因为读了太多无关档案而阻断写作」——
理由错了，但系统给不出正确的那个。`_place_hard` 只记 `Overflow` 不丢条目，
`_place_soft` 才记 `Truncation`。

**截断要记在两个地方。** `Budget.truncations` 是预算账，`coverage.truncated_results`
是资料包字段。初版只有前者，资料包里的 `truncated_results` 恒为空——
截断在账上可见、在包里不可见，等于没做「截断可见」这条验收。

**索引里的 `project` 是目录名，不是 `project_id`。** 存的是 `novel-001_smoke`，
拿 `novel-001` 去等值比较会**静默返回空**。所有查询统一走
`tools.project_clause()`（`project = ? or project like ? || '\_%' escape '\'`）。
踩过的表现：伏笔一条都查不到，报「未回收 0 条」，看着像「这本书没有伏笔」。

**伏笔的字段叫 `state`，不是 `status`。** `PLANNED / PLANTED / HINTED / TRIGGERED /
PAID_OFF / ABANDONED` 存在 `state`。只读通用 `status` 会把所有伏笔读成「状态未知」，
于是「已回收的」也被算进未回收。

**`storage.dump_yaml` 必须递归。** 原版只支持「标量 / 列表 / 一层对象列表」，
`issues[].evidence[]` 这种三层嵌套会被写成一个字符串——写下去不报错，读回来是字符串，
schema 校验才发现对不上。`_yaml_scalar(None)` 写 `null` 而不是 `""`，
否则 `base_release` 回读成空串，过不了自己的 schema。

**草稿目标路径必须由代码拼，不能做参数。** `draft._target_rel()` 固定拼
`03_Manuscripts/Drafts/<CH>.md`，拼完**再校验一次**不在 `01_Canon` 下，违反直接
`SecurityError`。给一个「指定目标路径」的选项，等于把「写稿」变成「写正式区」的入口；
而校验放在拼完之后，是为了防 `chapter_id` 本身被塞成 `../01_Canon/x`。

**写回 frontmatter 不能「解析成 dict 再整体 dump」。** `plan --write` 只做插入：
找出原本不存在的键，插在 `---` 之后，正文逐字节不动（变了抛 `ConflictError` 并回滚），
写前先备份到 `<runtime>/backups/plan/`。整体 dump 会重排原有键、改掉引号风格、
把作者手写的多行块压成一行——一次「补一个字段」能把章卡格式全冲掉。

**审核拿全文还是拿正文，是两回事。** 最初的 `submit()` 把**整份文件**（含 frontmatter
的 `---`）当正文喂给规则检查，于是每份稿都被判「正文里混了两份文档」BLOCKER，
同时风险词一个也读不到（关键词在正文里，但判定被前面的 BLOCKER 短路了）。
现在 `digest = sha256_text(整个文件)` 与 `body = md.parse(...).body` 分开用：
哈希要整份字节，风险与规则检查只要正文。

**单字风险词必须过搭配过滤。** 死亡关键词里放了单字「死」，「死寂的院子」「死守城门」
会让每一章都变成 HIGH——判多了的结果是没人再看风险等级。`_DEATH_EXCLUDE`
（`路/角/寂/守/心/板/锁/账/扛/磕/缠/党/规矩`）宁可漏判（`--risk HIGH` 能补），
也不让场景描写被判成死亡事件。

**审核记录的「轮」是轮号，不是提交次数。** `rounds_used = len({round for r in history
if r.verdict == "REVISE"})`：同一轮三个审核人都判 REVISE 只消耗一轮。反过来，
全是 APPROVE 但角色没齐（`PARTIAL`）不消耗预算——这条缺口写在 `known_risks` R-50。

**`draft_of()` 必须唯一。** 按「任务 + 章号」找 staging 里的草稿，找到两份就抛，
不取最新。理由与「哈希按磁盘现算」相同：两个候选里挑一个，挑错的那个后果是
「批的是一份后来又改过的字节」，而系统毫不知情。

**共同发布闸放在准备阶段，不是执行阶段。** 计划是作者要批的东西，如果计划里只有正文、
记忆是旧的，作者在批准那一刻就该知道；等到执行第 3 步再拦，作者已经看过一遍计划了。
`--skip-closure` 的豁免记进计划的 `closure_skipped` 字段，因此它也在作者批准的哈希里。
这个字段**只在为 `true` 时输出**：0.11.0 及更早写下的计划没有这个键，无条件输出会让
它们重新序列化后哈希变化，已批准待执行的计划会突然变成「批准的是另一份计划」。

## 加一个新命令的步骤

1. 业务逻辑写在新模块或已有模块里，返回结构化结果，不 print；
2. `cli/__init__.py` 加 parser 与 `HANDLERS` 映射；
3. 需要写盘的话，先确认走的是 `storage` 的安全写入；
4. 写测试：一条成功路径、一条拒绝路径（越界 / 越权 / 缺确认，按命令性质选）；
5. 更新 `docs/operations/cli_reference.md` 与 README 的命令表。

## 加一个新类型（数据契约）

1. 写 `schemas/<类型>.schema.json`，**必须**带 `additionalProperties: false`
   与 `required`——否则「未知字段被拒绝」对它不成立；
2. 通用元数据那 8 个字段（`artifact_type`/`artifact_status`/`schema_version`/
   `created_by_task`/`derived_from`/`supersedes`/`tags`/`extensions`）在每份
   schema 里各写一遍。**不要抽成 `$defs`**：`additionalProperties: false`
   不继承父级 `properties`，抽出来会把所有字段判成未知字段；
3. 在 `tests/fixtures/schemas/samples.json` 加一对 `valid` / `invalid` 样本；
4. 跑 `pytest tests/unit/test_schema_contract.py`。少了样本它会直接说「缺样本」，
   不用等谁想起来补。

校验器支持哪些关键字、哪些故意不支持，见 `src/novel_system/schema.py` 的模块
docstring。写了不支持的 keyword 不会报错，只是**不生效**——这点必须自己确认。

## 加一个新目录

**项目元数据**的键写在 `templates/project/project.yaml`，`init-project` 会读它
（2026-11-23 起；在此之前该模板从未被读取，改它不会有任何效果）。

**目录骨架**由 `init-project` 按 `layout.project_subdirs` / `canon_subdirs`
（`config/defaults.yaml`）建，另有几组代码常量（`PLANNING_DIRS` / `MANUSCRIPT_DIRS` /
`MEMORY_DIRS` / `STAGING_AREAS`）。**改一处不够**：模板、配置、常量三者必须对齐，
否则「配置说的」和「代码建的」会分叉。

若某个目录需要特殊语义（例如「不得进索引」），改 `paths.py` 的常量并补测试，
不要在各处散落字符串判断。
