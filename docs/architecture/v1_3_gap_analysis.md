# V1.3 规范差距盘点与重构分批计划

> 基准：`超长篇小说全生命周期管理系统_产品需求与实施规范_V1.3`（7452 行）
> 被盘点版本：代码仓 `0.15.10`（27550 行 / 96 个 `.py`）、Vault `novel-001_jiuzhou`（1089 份 md）
> 盘点日期：2026-11-23
> 盘点方式：只读。全部数字来自实际命令输出，未估算。

## 0. 权威顺序

§28.2 明确：**第一至第二十六部分保留 V1.1，第二十七部分是规范性覆盖**。前文与 §27 冲突时一律以 §27 为准。

所以本盘点的判据全部取自 §27，不取 §11（V1.3 的第十一部分已变成「工作流系统」，不再是数据规范）。

§27.24 同时规定：首次实施必须「只读检查 → 输出盘点报告与分阶段计划 → 等待作者确认 → 只执行已批准阶段」。本文件是第 2 步的产物，**未获批准前不改动任何 Vault 业务文件**。

## 1. 差距矩阵

分三类：**A 不可逆**（改完回不去，或回退成本高于重做）、**B 结构性**（动很多文件但方向确定）、**C 增量**（新增能力，不影响存量）。

| # | 维度 | V1.3 要求 | 实测现状 | 类 | 规模 |
|---|---|---|---|---|---|
| 1 | ID 形态 §27.2 | `{TYPE}-{ULID}`，26 位 Crockford Base32，明确禁止 `ent_xxxxx` | `ent_000001`（下划线六位） | **A** | 1024 份带 ID 文件 + 全部引用 + 27 份契约 pattern + 全部测试 |
| 2 | 基础字段 §27.2.3 | `object_type` `schema_id` `lifecycle_status` `content_hash` `scope` | 覆盖率 0 / 0 / 0 / 4 / 2（共 27 份契约） | **A** | 27 份契约 + 1089 份文件回填 |
| 3 | `project_id` → `novel_id` §27.2.3 | 业务接口统一 `novel_id`，不得用 `project_id` 替代 | 代码 `project_id` 192 次 / 38 文件；`novel_id` 3 次 / 1 文件；真库两者均 0 份 | **B** | 38 个源码文件 + 契约 + 测试 |
| 4 | Canon 状态 §27.3 | 7 值（多 `superseded`）+ 8 条合法迁移，禁止 `idea→canon` 等 | 6 值，无 `superseded`；无迁移校验 | **B** | 契约 + CLI choices + 状态机 |
| 5 | Agent 命名 §27.14.2 | `chief_editor` `story_editor` `research_librarian` `lead_writer` `memory_manager` `manuscript_ingestor` | `editor_in_chief` `editor` `researcher` `writer` `memory_keeper` `ingestor`（6 个不同） | **B** | 9 个角色目录 + Prompts + 注册表 + 专家团 |
| 6 | 严重级别 §27.15 | `S0_blocker`—`S4_info`，禁止再用 `blocking`/`critical`/`severe` | `BLOCKER` `NON_BLOCKING` `SUGGESTION` | **B** | issue 模块 + 契约 + CLI |
| 7 | 伏笔状态 §27.6.1 | `idea→planned→planted→reinforced→ready_to_payoff→paid_off`，另有 `abandoned` `invalidated` | `planned` `planted` `hinted` `triggered` `paid_off` `abandoned`（缺 4 值，多 2 值） | **B** | 契约 + 状态机 + 4 份存量 |
| 8 | 工作流契约 §27.10 | 15 个顶层字段；每个 step 9 个字段 | 9 个顶层；step 无 schema ref / 幂等键 / checkpoint | **B** | 6 份 workflow.yaml + 校验器 |
| 9 | 包结构与分层 §27.16 | `novel_management/` + 12 个顶层包（cli/adapters/application/domain/…） | `novel_management/` 单层，按功能平铺 | **A** | 96 个文件全量移动 + 全部 import |
| 10 | CLI 命令族 §27.19 | 19 个命令族（`novel entity ...`） | 40 个平铺命令 | **B** | CLI 全量重组 + 文档 + 测试 |
| 11 | 退出码 §27.19.2 | 0—11 共 12 个语义退出码 | 只有 `0/1/2` | **C** | 错误类型体系 |
| 12 | SQLite 表 §27.4.3 | 21 张系统表 | 15 张索引表（`index_metadata`/`artifacts`/`entities`/…），缺运行态表（`task`/`workflow_run`/`approval`/`audit_event`/`lock_registry` 等） | **C** | 新增 9 张 + `novel_id` 列 |
| 13 | 规划对象 §27.17 | Storyline/Outline/Volume/Chapter/Scene/SceneBeat/Conflict/CharacterArc 各有契约 | 19 份规划文件无契约，四套 type 描述同一层（`scene_beat` 11 / `chapter_plan` 3 / `volume` 2 / `chapter_outline` 2 / `volume_design` 1） | **B** | 5 份新契约 + 19 份归一 |

已经符合、不需要动的：

- Python ≥ 3.11（`requires-python = ">=3.11"`）。
- Markdown/YAML 为事实源、SQLite 在 Vault 外做可重建投影（§27.4 的根本要求已满足）。
- `additionalProperties: false` + 无契约进 `no_contract` 桶（§27.21.6「未知字段不得静默删除」）。
- 原子写入 + 写前备份 + 内容保全校验（§27.11.2 的流程已实现大半）。
- 三物理边界隔离（公仓 / Vault / runtime）。

## 2. 分批计划

按依赖关系排。**第 1 批不依赖 ID 形态，可以立刻做；第 2 批是地基，一动全动。**

### P-A：无依赖项（可与 ID 决策并行）

| 项 | 内容 | 破坏面 | 回滚 |
|---|---|---|---|
| #11 退出码 | 建立 0—11 错误码映射，接进 `main()` | 错误类型 + CLI 出口 | 提交回退 |
| #6 严重级别 | `BLOCKER/NON_BLOCKING/SUGGESTION` → `S0—S4` | issue 模块 + 契约 + 测试 | 提交回退 |
| #7 伏笔状态 | 6 值 → 8 值 + 迁移表；存量 4 份 | 契约 + 4 份文件 | 文件备份 |
| #12 SQLite | 补 9 张运行态表 + `novel_id` 列 | 索引 schema + 重建 | 删库重建 |

### P-B：地基三项（必须先定 ID）

| 项 | 内容 | 为什么必须成组 |
|---|---|---|
| #1 ID 形态 | `ent_000001` → `ENT-01J…` | 所有引用者一起改；分批改会留下两种形态并存 |
| #2 基础字段 | 补 5 个字段并回填 | `schema_id` 依赖类型注册表，`content_hash` 依赖写入器 |
| #9 包结构 | `novel_management` → `novel_management` + 12 包 | 与 ID 改动同时做，避免两次全量 diff |

### P-C：契约与命令面

| 项 | 内容 |
|---|---|
| #3 `novel_id` | 192 处重命名 + 契约 + 测试 |
| #4 Canon 状态机 | 加 `superseded` + 8 条迁移校验 |
| #8 工作流契约 | 6 份 yaml 重写 + 校验器 |
| #10 CLI 分组 | 40 条命令 → 19 个族 |
| #13 规划对象 | 5 份新契约 + 19 份归一（**动作者正典，需单独点头**） |

### P-D：Agent 层

| 项 | 内容 |
|---|---|
| #5 命名对齐 | 6 个角色改名；建议同时做**别名映射**，避免规范再改一次就要动 9 个目录 |

## 3. 需要你裁定的事项

### 裁定 1：ID 迁移是否执行（决定 P-B 能否开工）

这是全库级不可逆操作。事实依据：

- 2026-11-22 你裁定「按 V1.0 §9.2 全库重排」，把 V4.0 的 `CHAR-0001` 改成 `ent_000001`，1046 份已迁移。
- 现在 V1.3 §27.2 要求 `{TYPE}-{ULID}`，形态上又回到「大写前缀 + 连字符」——**与 11-22 被否掉的形态同一个方向**，只是编号从序号换成 ULID。
- 上一轮你明确说过「先不改 ID 形态」。V1.3 与那句话直接冲突。

三个可选路径：

1. **严格执行 V1.3**：全库迁移到 `ENT-01J…`。代价：1024 份重编号 + 全部引用 + 冻结区（`08_Reviews`/`99_Archive` 不能替换）+ 测试全量重写。收益：与规范一致，ULID 天然全局唯一。
2. **保持 `ent_000001`，在文档里登记「已知偏离」**：代价：与规范不一致，后续 Agent 契约、Schema 都要带豁免说明。收益：零破坏。
3. **折中：新对象用 `ENT-ULID`，存量保留 `ent_`**：代价：两种形态并存，查重与引用解析都要双分支（这正是 `ids.py` 里 `LEGACY_*` 守卫要防的局面）。**我不建议**。

### 裁定 2：包是否整包改名

`novel_management` → `novel_management` 意味着 96 个文件路径全变、所有 `from novel_management import` 全改、`PYTHONPATH` 与测试配置全改。若改名，建议与 P-B 同批做，避免二次 diff；若不改名，在文档登记偏离。

### 裁定 3：Agent 命名是否第三次变动

这已经是第三套写法：V4.0 一套 → 上一轮按注册名一套（`editor_in_chief`）→ V1.3 §27.14 又一套（`chief_editor`）。建议做法：**以 V1.3 名为准，同时保留旧名作为 alias 映射**，这样下次规范再改只需加一行映射，不必再动目录与 Prompts。

## 4. 未验证的能力（§27.24.5 要求显式列出）

以下 V1.3 要求的能力，我**没有证据**表明当前可运行，不得假设可用：

- ULID 生成：代码库无任何 ULID 依赖（`grep ulid` 零命中）。需引入 `python-ulid` 或自实现 Crockford Base32（约 40 行）。
- 字段级保密过滤（§27.8）：当前无字段分类实现，检索层没有「返回前过滤」。
- `rule_expression` 白名单 AST（§27.9.2）：无实现。
- 工作流暂停/恢复/补偿（§27.10）：`runtime/tasks`、`staging`、`preflight` 均为 0 条，这套流程从未真实使用过。
- GC 契约（§27.13.4）：无实现。
- 跨平台（Windows/Linux）：仅有 macOS 实测记录，无 CI 证据。

## 5. 与未完成旧清单的关系

上一轮处置清单 #6—#10 尚未全部完成：

- #6 清旧路径 220 处：**未完成**。V1.3 第 27 部分没有改变当前目录布局（§10.1 与现库一致），所以这项仍然有效。
- #7 红测试：进行中，270 → 210（已消灭 60，新增 0）。
- #8 规划 type 归一：对应本文件的 #13，**等裁定**。
- #9 权限差异说明：**未完成**。V1.3 §27.14 给了完整的权限矩阵，可直接据此写，比原计划更准确。
- #10 清死文件：`quality_gates/baseline.yaml` 已迁到 `docs/operations/quality_gates.md` 并删除孤岛目录；`.tmp`（284MB / 117 目录）待清。

建议：**#6 与 #7 继续按原计划推进**（它们与 V1.3 不冲突），V1.3 重构从 P-A 起步，#1/#9 等裁定。

## 6. 执行进度（2026-11-24 现取）

已从「盘点」进入「施工」。做完的按证据记，没做的照旧留白——**不写「基本完成」这种话**。

| # | 状态 | 证据 |
|---|---|---|
| #1 ID 形态 | **完成** | 代码 0.17.0；Vault 1024 份迁移、1018 份改名、7053 处引用；`validate` 1046 份 0 重复、5685 双链无断链；`maps` 108/111 节点不变；1073 份原文保全逐字节对比通过 |
| #9 包结构 | **完成** | 12 个顶层包就位，`security/` 与 `diagnostics/` 只有包位（docstring 写明还没从别处拆出什么） |
| #2 基础字段 | 未动 | 5 个字段仍缺，覆盖率 0/0/0/4/2 |
| #3 `novel_id` | 未动 | `project_id` 仍 192 处；`entity create` 已写 `novel_id`，但接口层没统一 |
| #4 Canon 7 值 | 未动 | 仍 6 值，无 `superseded`，无迁移校验 |
| #5 Agent 命名 | 未动 | 六个角色名仍与 §27.14.2 不同 |
| #6 严重级别 | 未动 | 仍 `BLOCKER/NON_BLOCKING/SUGGESTION` |
| #7 伏笔状态 | 未动 | 仍 6 值 |
| #8 工作流契约 | 未动 | 仍 9 个顶层字段 |
| #10 CLI 命令族 | 未动 | 仍 41 条平铺命令 |
| #11 退出码 | 未动 | 仍 0/1/2 |
| #12 SQLite 表 | 未动 | 仍 15 张 |
| #13 规划对象契约 | 未动 | 19 份无契约 |

### 第 4 节中已由本轮消除的「未验证能力」

- **ULID 生成**：已自实现 Crockford Base32（`domain/ulid.py`，约 140 行）。
  实测：26 字符、时间戳可回读、同毫秒单调递增、字母表拒绝 I/L/O/U。
  迁移拼法 `compose(ts, payload)` 另测「字典序 == 旧序号序」。

其余六项（字段级保密、`rule_expression` AST、工作流暂停恢复、GC、跨平台）**仍然没有证据**，不要当成可用。

### 本轮暴露、尚未处置的两件事

1. `chapter_range` 字段的语义混在一起：`CH-0001..CH-0045` 是**章号范围**，
   `ch_001001..ch_001028` 是**旧稿对象 ID 范围**。V1.3 的 ID 无法表达「范围」，
   这个字段该拆成 `chapter_count` / `{start_id, end_id}` 还是列表，需要作者定，
   属于 #13（规划对象契约）的一部分。
2. `01_小说项目/novel-001_jiuzhou/README.md` 是个**目录**（里面还有一份 README.md），
   2026-11-22 17:12 留下。不影响任何命令（`rglob` 会命中里面的文件），但结构上是个错。

