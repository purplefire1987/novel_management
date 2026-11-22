# V1.0 规范符合度对照（2026-11-22）

> 对照对象：`~/Downloads/超长篇小说全生命周期管理系统_产品需求与实施规范.md`（**V1.0**，
> 状态「实施基线」，建设方式「全新系统 Greenfield，不含旧系统迁移」）。
>
> 当前实现基于 `99_Archive/legacy_v1/PRD_v2.1.0.md` 的 **V4.0** 架构，已承载
> 1046 份档案 / 966 份 Canon / 5679 条双链。
>
> **这份文档设计时明确排除了「迁移现有内容」。**以下所有「不符」都要放在这个前提下看：
> 不是实现做错了，是两套规范对「起点」的假设不同。

## 一、总览

| 判定 | 条数 | 说明 |
|---|---|---|
| ✅ 已满足 | 6 | 架构分层、事实源、公私边界、写入协议、双轨版本、检索 |
| ⚠️ 部分满足 | 7 | 字段不全、命令缺失、工作流未编号 |
| ❌ 结构不符 | 5 | 目录树、ID 前缀、实体分类、关系/状态数据、审核清单 |

## 二、逐条对照

| V1.0 条款 | 要求 | 现状 | 判定 |
|---|---|---|---|
| §2.1 组件职责 | WorkBuddy / Obsidian / Python / GitHub 四层 | 四层齐备 | ✅ |
| §2.3 事实源 | Vault md 为内容源；SQLite 只存运行态与可重建索引 | 已实现且有 ADR | ✅ |
| §3 公私边界 | 私库不得有 Git 远端、不得在公仓内 | `Vault_Config.yaml` 三条硬约束 | ✅ |
| §5.1 类型注册表 | 18 个类型域 | `entity.schema.json` 13 种 `type`，实际用 8 种 | ⚠️ 部分 |
| §5.2 实体基类 | 16 字段（含 `novel_id` `content_version` `created_by` `confidence`） | 无 `novel_id` / `content_version` / `created_by` / `confidence` | ⚠️ 部分 |
| §5.3 关系 Relationship | 独立记录 + 时间有效性 | `05_Ledgers/Relationships/` 存在，**结构化 0 条** | ❌ 无数据 |
| §5.4 状态变化 | 追加式变更记录 | 表存在，**0 行** | ⚠️ 空 |
| §5.5 认知模型 | 区分确知/推测/误解/听闻/秘密/未确认 | `novel knowledge` 命令有，表 **0 行** | ⚠️ 空 |
| §5.6 时间模型 | 世界时间 / 叙事时间 / 记录时间 | `timeline/` 有三分，但 `events` 仅 **7 行** | ⚠️ 空 |
| §7 九 Agent | 9 角色 + 职责 + 权限 | `Policies/Agent_Permissions.md` + `Prompts/` 9 份 | ✅ |
| **§8 Vault 目录** | 3 根 / 项目 11 子 / 知识库 **16 类中文** | 4 根 / 项目 10 子 / 知识库 **6 类英文** | **❌** |
| **§9.2 ID 前缀** | 12 种下划线小写（`ent_` `rel_` `evt_` `sc_` `wf_` `task_` `prop_` `ver_` `bkp_` `src_` `novel_` `idea_`） | 23 种连字符大写（`CHAR-000001` 六位） | **❌** |
| §10 声明式工作流 | WF-00…WF-19 共 20 个，每个 16 字段 | **6 个 YAML**，无编号，缺 6 字段 | ⚠️ 部分 |
| §11 十维审核 | 结构/因果/人物/设定/时间/信息/伏笔/语言/读者体验/规划偏差 | `review.py` 只做风险路由与返修预算，**无十维清单载体** | ❌ 缺 |
| §12 写入协议 | 12 步（读哈希→验权限→提案→校验→审批→再读→原子写→复审→审计） | staging + 哈希绑定 + 原子写，基本对应 | ✅ |
| §13 双轨版本 | 公库 Git / 私库版本服务 | `release/` 22 步 + git revision 绑定 | ✅ |
| §15 存储运行时 | Vault 外 runtime | `~/WorkBuddy/novel-runtime` | ✅ |
| §16 检索 | 索引 + 资料包 | SQLite FTS5 + `preflight` 八级检索 | ✅ |
| §17/§29 CLI | 38 条（分组式） | 36 条（平铺式），缺约 10 条 | ⚠️ 部分 |
| §25 质量门矩阵 | 12 行操作 × 三类权限 | `release/gates.py` 三道闸（批准/问题/RETCON） | ⚠️ 部分 |

## 三、五处结构不符的实情

### 1. 目录树（§8）

```
V1.0 要求                         现状
Author_Vault/                     .
├── 00_作者工作台/                 ├── 00_System/
├── 01_小说项目/Novel_A/           ├── 10_Projects/novel-001_jiuzhou/
│   ├── 00_项目管理/               │   ├── 00_Project/
│   ├── 01_灵感与创意/             │   ├── (无)
│   ├── 02_世界知识库/16 类        │   ├── 01_Canon/6 类
│   ├── 03_故事规划/               │   ├── 02_Planning/
│   ├── 04_正文/{初稿,修订,定稿}   │   ├── 03_Manuscripts/
│   ├── 05_记忆与连续性/           │   ├── 05_Ledgers/ + 06_Memory/
│   ├── 06_素材与研究/             │   ├── 04_Story_Assets/
│   ├── 07_审核与报告/             │   ├── 08_Reviews/
│   ├── 08_工作流与任务视图/       │   ├── (无)
│   ├── 09_版本与变更记录/         │   ├── (无)
│   ├── 10_项目配置/               │   ├── 00_Project/
│   └── _staging/                  │   └── 07_Staging/
└── 02_作者级通用资料/             └── 90_Shared_Assets/
```

### 2. 知识库分类：668 份（69%）错配

`01_Canon` 现有 6 目录，`type` 字段却已有 8 种精确值。交叉表：

| 目录 | 份数 | 实际 type 构成 |
|---|---|---|
| `World/` | 442 | item 336 + creature 76 + world 30 |
| `Systems/` | 226 | skill 171 + destiny 42 + world 13 |
| `Locations/` | 110 | location 110 ✅ |
| `Organizations/` | 102 | faction 102 ✅ |
| `Characters/` | 86 | character 86 ✅ |
| `Rules/` | **0** | 空目录；43 个 `RULE-*` 散在 `Systems/Destinies`(1) `Systems/Skills`(12) `World/九州/总表`(30) |

**`World/` 与 `Systems/` 是垃圾桶目录。** 这正是上一轮「ITEM 529 混技能素材、
CHAR 204 混生物命途」的根因——不是编号错了，是分类维度不够。

好消息：`type` 字段已经能 1:1 映射到 V1.0 的 16 类，**细分可脚本化，不需要人工判断**。

| V1.0 目录 | 映射自 type | 份数 |
|---|---|---|
| 物品与装备 | item | 336 |
| 技能与能力 | skill | 171 |
| 地理与地点 | location | 110 |
| 势力与组织 | faction + organization | 102 |
| 人物与角色 | character | 86 |
| 生物与生态 | creature | 76 |
| 世界与宇宙 | world | 43 |
| 力量与成长 | destiny | 42 |
| 规则与机制 | rule（跨目录散落） | 0 份有 `type: rule` |
| 事件与历史 / 关系与状态 / 时间线 | 在 `05_Ledgers`，非 Canon | — |
| 种族与族群 / 资源与经济 / 技术与文明 / 文化与社会 / 特殊设定 | 无对应 type | — |

### 3. ID 格式（§9.2）

V1.0 用**单前缀 `ent_` 收所有实体**，靠 `entity_type` 字段区分；当前每类一个前缀。

```
V1.0:  ent_00012   entity_type: character
现状:  CHAR-000086  type: character
```

功能上等价。差异是**文件名与全部 5679 条双链**——改格式等于第三次全库重命名。
前两次（改形态、改六位）都在同一天完成，都使连续性签字失效。

### 4. 工作流（§10）

已有 6 个：`canon_change` `legacy_ingestion` `manuscript_production`
`memory_update` `release` `simulation`。

每个已有 9 个字段，V1.0 要求 16 个。缺：`preconditions`（与 start_conditions 分开）、
`input_schema`、`output_schema`、`tool_permissions`、`retry_policy`、
`idempotency`、`audit_events`、`acceptance`。

且没有 WF 编号。V1.0 的 20 个与现有 6 个是**交叉关系**，不是包含关系：

| V1.0 WF | 现有 workflow | 关系 |
|---|---|---|
| WF-00 初始化 | — | 缺 |
| WF-01/02 灵感 | — | 缺（无灵感子系统） |
| WF-03 原稿录入 | `legacy_ingestion` | 有 |
| WF-04…07 设定/实体/事件/研究 | `canon_change` | 覆盖 1 个，缺 3 |
| WF-08…12 总纲→资料包 | `manuscript_production` 前半 | 覆盖，缺拆分 |
| WF-13/14 初稿/审核 | `manuscript_production` 后半 | 覆盖 |
| WF-15 作者定稿 | `release` 前半 | 有 |
| WF-16 记忆提取 | `memory_update` | 有 |
| WF-17 连续性检查 | — | 缺 |
| WF-18 版本发布 | `release` | 有 |
| WF-19 备份诊断 | — | 缺（有 `backup` 命令，无工作流定义） |
| — | `simulation` | V1.0 无对应（骰子娘在 §7 有角色，§10 无工作流） |

### 5. CLI 缺口（§17/§29）

V1.0 列 38 条，当前 36 条，**缺 9 条、部分 3 条**：

| V1.0 命令 | 现状 | 缺口 |
|---|---|---|
| `project list` / `project show` | 无 | 缺（项目注册表在 `Project_Registry.md`，无命令） |
| `idea create` | 无 | 缺（灵感子系统整体不存在） |
| `entity create` / `entity show` | 无（`schema` 只校验形状） | 缺 |
| `relation create` | 无 | 缺（关系只能手写 md） |
| `event create` | 无 | 缺（事件只能手写 md） |
| `workflow list/validate/run/pause/resume` | 无 | 缺（6 个 YAML 无人读） |
| `task list/show/retry` | `task create/inspect` | 缺 list/show/retry |
| `continuity check` | 无（散在 `validate`） | 缺 |
| `version *`（11 条） | `release` 单命令含子命令 | 缺 tag/history/inspect/diff/verify |
| `memory extract` | `proposal` | 等价 ✅ |
| `file diff` | `diff` | 等价 ✅ |
| `change propose/approve/commit` | `change create` / `approve` / `publish` | 等价 ✅ |

## 四、迁移成本

| 项 | 动作量 | 风险 | 可逆 |
|---|---|---|---|
| 知识库细分 16 类 | 668 份移目录 + canvas 路径重算 + 重建索引 | 中（双链不断，但 canvas 的 `file` 是绝对路径） | 是（tar 备份） |
| 项目内子目录重命名 | 10 个目录 + `paths.py` 常量 + `Vault_Config.yaml` | 中（代码里 `01_Canon` 等字样有硬编码） | 是 |
| 根目录重命名 | 3 个根 + config + 全部代码常量 | 中高 | 是 |
| ID 改 `ent_` | 1046 份重命名 + 5679 条引用 + 全部签字失效 | **高** | 是（tar 备份） |
| 补 9 条 CLI | 约 1200 行新代码 + 测试 | 低 | 是 |
| 工作流补字段 + 编号 | 6 → 20 个 YAML | 低 | 是 |
| 十维审核清单 | 1 个模板 + `review` 命令输入结构 | 低 | 是 |

## 五、结论

**代码层与 Agent 层的问题可修**（补命令、补字段、补清单），不影响现有 1046 份数据。

**数据层有两个独立的决定**，不该捆在一起做：

1. **目录细分**（16 类）：收益明确（消除 668 份错配），成本中等，双链不断。
2. **ID 改格式**（`ent_`）：功能等价，成本是第三次全库重命名 + 签字全失效。

如果只做 1 不做 2，知识库结构就符合 V1.0 而 ID 不符合；
如果两个都做，一天之内会经历两次全库改写，出问题难以定位到是哪一次引起的。
**建议分两次，先目录后 ID（或不做 ID）。**
