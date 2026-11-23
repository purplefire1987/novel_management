# V1.1 差异清单（2026-11-23 只读侦查）

> 对照对象：`~/Documents/知识库/个人仓库/raw/articles/超长篇小说全生命周期管理系统_产品需求与实施规范_WorkBuddy_V1.1.md.md`
> （**V1.1**，6575 行 / 26 部分，文首写「完整合并版」，日期 2026-11-22）
> 现行基线：`~/Downloads/超长篇小说全生命周期管理系统_产品需求与实施规范.md`（**V1.0**，1467 行 / 33 节，
> 状态「实施基线 / WorkBuddy 可执行版」）
> 代码：`novel-management` 0.15.2；Vault：`01_小说项目/novel-001_jiuzhou`，1088 份 md。

**侦查范围**：结构、契约（模型/字段/枚举/ID/目录）、命令层、数据库表、阶段与验收。
叙述性段落（§3 各子系统、§8 每个 Agent 的逐条职责）只核对了名单与权限等级，未逐字比对。

---

## 〇、作者裁定（2026-11-23）

| # | 议题 | 裁定 | 状态 |
|---|---|---|---|
| 1 | ID 形态 | **风格不变**（小写 + 下划线 + 六位），**粒度按 V1.1 拆**：存量 `ent_` 不动，伏笔/素材/记忆等新模型各发自己的小写前缀（`fsp_` `sus_` `mat_` `chm_` …） | 待 D2 落地 |
| 2 | 正文目录 | **每卷都分初稿/修订稿/正式定稿**：`04_正文/卷一/{初稿,修订稿,正式定稿}/` | 待 D1 落地 |
| 3 | Canon 枚举 | **迁移到 6 值** | ✅ 0.15.3 已完成（1000 份） |
| 4 | 运行时位置 | **保持 Vault 外**；V1.1 §13.9 读作「版本存储与工作内容分离」，不是「必须在 Vault 内」 | 已定，无需改 |
| 5 | 命令族 | **全面改名**到 V1.1 §16.2 分组式，旧名做弃用提示 | 待 D4 落地 |

> 以下三、四节的原文保留，作为裁定的依据记录；已落地的项在表中标注。

---

## 一、两版是什么关系

V1.1 **不是 V1.0 的超集**。同一批决策在两版里颗粒度不同，且各有对方没有的章节。

| | V1.0（1467 行） | V1.1（6575 行） |
|---|---|---|
| 定位 | 可执行精简版，带执行总则与首次执行指令 | 完整产品文档，带完整数据模型与验收用例 |
| V1.0 独有 | §0 执行总则、§9 ID 与命名规范、§11 十维审核标准、§17 CLI 逐条约束、§28–§33 验收/仓库结构/首次指令/运行参数 | — |
| V1.1 独有 | — | §4–§7 共 28 类数据模型（字段级）、§11 二十个工作流的分步、§17.2 五十五张表、§21 仪表盘与质量指标、§23 A–T 二十个端到端验收、§25 实施约束 |
| 目录与 ID | 目录树 + `ent_` 等 12 个前缀 | 目录树更细（正文分卷、记忆 14 子目录、素材 9 子目录），**没有 ID 规范章节**，模型示例用 `ENT-XXXXXXXX` |

结论：不能「照 V1.1 重做」，要分三类处理——**新增义务**、**与现行冲突**、**V1.1 不再规定（沿用现行）**。

---

## 二、已经相符，不必动

| 条款 | 现状 |
|---|---|
| §10.1 三根目录 `00_作者工作台 / 01_小说项目 / 02_作者级通用资料` | 一致（另有 `99_Archive`，V1.1 未提，属遗留归档） |
| §10.1 项目内 11 个子目录、知识库 17 类 | 一致；类型映射已跑通（item 336 / skill 171 / location 110 / faction 102 / character 86 / creature 76 / world 43 / destiny 42 / event 7） |
| §8 / §8.1 九 Agent 名单 | 一致（`agents/` 九份 + `00_作者工作台/Policies/`） |
| §9.3 权限等级 P0–P5 | 与 `Agent_Permissions.md` 五列矩阵一致 |
| §11 二十个工作流 WF-00…WF-19 | `workflows/catalog.yaml` 二十条齐备 |
| §4.2 之外的 Canon 门禁、§12 写入协议、§13 双轨方向、§14 检索、§19 备份 | 已有实现（`staging/` `approvals/` `release/` `indexing/` `backup/`） |
| §22 P0–P8 阶段划分 | 与现行里程碑同一套（T01–T12 是 V4.0 坐标系，需一张映射表，见 §五.6） |

---

## 三、必须作者裁决的冲突（五项）

### 1. ID 形态：`ent_000001` vs `ENT-XXXXXXXX`

- V1.1 §4.1–§7.14 的模型示例共出现 **28 种前缀**：`ENT-` `REL-` `STC-` `EVT-` `FSP-` `SUS-` `MYS-` `NLK-`
  `MAT-` `RST-` `EXC-` `FND-` `CAD-` `USG-` `CHM-` `EVM-` `CSM-` `RLM-` `ICM-` `SKM-` `KSM-` `TLM-`
  `FSM-` `CTR-` `CTI-` `MUP-` `VER-` `NOV-`，全部**连字符 + 大写**。
- V1.1 **没有任何一节规定 ID 的格式规则**；V1.0 §9.2 规定的是**下划线小写** 12 个前缀（`ent_` `rel_` `evt_` …）。
- 现状：`ent_` 966 份、`ch_` 28 份（旧稿）、`asset_` 22、`plan_` 8、`evt_` 7。
- 若按 V1.1 示例改：**第四次全库改名**（1088 份文件 + 双链 + 连续性签字基线全部失效），
  且 V1.1 没给出「类型 → 前缀」的映射表（`ENT` 收所有实体，还是按 28 类拆开，得二选一）。
- 若保持：需要在文档里写明「模型示例是占位风格，格式以 §9.2 为准（V1.0 保留）」。

**待裁决**：保持 `ent_` / 全面改 `ENT-` / 只给新模型（伏笔、素材、记忆）发独立前缀，存量不动。

### 2. 正文目录：分卷 vs 三段

- V1.1 §10.1：`04_正文/{卷一, 卷二, ...}`。
- V1.0 §8：`04_正文/{初稿, 修订稿, 正式定稿}`；现状与此一致，且**三个目录都是空的**（0 份正文）。
- 牵连：`agents/*/agent.md`、九份提示词、`workflows/*`、`novel write` 的落点都写着 `04_正文/初稿`、`04_正文/正式定稿`。
- 改的成本目前最低（无文件），但会把「稿件阶段」从目录名挪到 frontmatter（`stage` 字段已有）。

**待裁决**：保持三段 / 改分卷 / 分卷 + 阶段子目录两层。

### 3. Canon 枚举：`canon` vs `confirmed`

- V1.1 §4.2「统一采用」：`idea / proposal / pending_review / canon / deprecated / rejected`（6 值，无 `retcon_candidate`）。
- 现状数据：`confirmed` 988 份、`deprecated` 51、`proposed` 10、`draft` 1；
  `entity.schema.json` 的枚举是 `draft/proposed/confirmed/deprecated/archived`。
- 改成 V1.1 的字面值 = 988 份 frontmatter 批量替换 + 全库 `validate` 重跑；语义上 `confirmed→canon`、
  `proposed→proposal`、`under_review→pending_review`，`retcon_candidate` 无对家（现由 `issue`/`change` 承载）。

**待裁决**：迁移到 6 值 / 保持现状并在 schema 注明映射 / 双写兼容（新写 `canon`，旧读 `confirmed`）。

> **已裁定并落地（0.15.3）**：迁移到 6 值。实测 989→canon、10→proposal、1→proposal，
> `deprecated` 51 不动，`99_Archive` 冻结不动；1000 份逐份行级 diff 断言通过。

### 4. 运行时位置：Vault 内还是 Vault 外

- V1.1 §13.9「版本存储目录建议」：`Author_Vault/.novel_management/{database,version_store,audit,logs,cache}`，
  并说「版本存储必须与工作 Vault 内容分离管理」。
- V1.0 §8.1：版本存储、SQLite、日志、备份放 **Vault 之外的本地系统数据目录**。
- 现状：`~/WorkBuddy/novel-runtime`（Vault 外），`doctor` 与 `paths.py` 把它当硬约束，`--runtime` 越界直接拒。
- 这是**架构级**冲突：移进去要改 `paths.py`/`doctor`/`release`/测试的越界断言，并决定 `.novel_management` 是否入备份、
  是否被 Obsidian 索引、是否与「私库不入公仓」的检查交互。

**待裁决**：保持 Vault 外 / 改为 `.novel_management/` / 两者都支持（默认外，允许显式内）。

### 5. 命令族命名：分组式 vs 平铺式

V1.1 §16.2 与 V1.0 §17 是**同一张表**（V1.1 只多一条 `version gc`），共 39 条。现状 43 个顶层命令，差异集中在七处：

| V1.1 / V1.0 | 现状 | 差异性质 |
|---|---|---|
| `novel version snapshot/tag/history/inspect/diff/impact/rollback-propose/rollback-approve/restore/verify/gc` | `novel release plan/status/prepare/execute/...`（22 步协议） | 命名与模型都不同：V1.1 是「版本」对象（`VER-`），现状是「发布」对象（`REL-`） |
| `novel change propose/approve/commit` | `novel change create/approve/reject` | 动词不同，`commit` 无对家 |
| `novel file diff` | `novel stage diff` | 命名 |
| `novel task retry` | `novel task transition --to` | 缺 retry |
| `novel memory extract` | `novel proposal extract` | 命名 |
| `novel continuity check` | `novel continuity` | 缺子命令层 |
| `novel backup create/verify` | `novel backup`（单命令、无 verify 子命令） | 缺子命令层 |

**待裁决**：加一层兼容别名（旧命令保留，新名字转发）/ 全面改名 / 只补缺的三条（`task retry`、`backup verify`、`version gc`）。

---

## 四、V1.1 新增义务（不需裁决，按批做）

| # | 义务 | 现状缺口 | 依据 |
|---|---|---|---|
| 1 | **24 个新数据契约**：FSP 伏笔 32 字段、SUS 悬念 27、MYS 谜题 13、NLK 叙事关联 6、MAT 素材 30、RST 研究主题 29、EXC 摘录 26、FND 结论 19、CAD 创意转化 22、USG 使用记录 17、CHM 章节记忆 43、EVM 事件记忆 24、CSM 人物状态 35、RLM 关系记忆 19、ICM 物品流转 19、SKM 技能记忆 21、KSM 角色认知 23、TLM 时间线 19、FSM 伏笔状态 13、CTR 连续性规则 19、CTI 连续性问题 19、MUP 记忆提案 31 | `schemas/` 28 份里只有 `foreshadowing`（字段名不同：`planted_in`/`surface_meaning` vs `plant_chapter_id`/`actual_meaning`）与 `memory`（6 字段通用层）；其余 20 余类**没有契约** | §4.1–§7.14 |
| 2 | **目录细化**：`05_记忆与连续性/` 14 个子目录、`06_素材与研究/` 9 个、`03_故事规划/伏笔与悬念/` 4 个 | `05` 只有 5 份 md 平铺；`06` 是 6 个英文遗产目录（Battles/Dialogue/Emotional_Beats/Highlights/Sensory/Templates）+ 22 份 md 平铺；`伏笔与悬念` 是 5 个英文目录（Character_Arcs/Missions/Dice/Conflicts/Unresolved_Questions）+ 6 份 md | §7.2 §6.2 §10.1 |
| 3 | **版本服务 11 个功能 + L1–L4 层级 + `VER-` 数据结构 + `version_store/{snapshots,objects,manifests,diffs,metadata}`** | 只有 `release/` 22 步协议；`release status` 判 `NO_RELEASE`（正式指针为空） | §13.4–§13.9 |
| 4 | **仪表盘 14 项 + 质量指标 9 项** | `00_作者工作台/Dashboards/看板.md` 是旧口径 | §21.1 §21.2 |
| 5 | **A–T 二十个端到端验收** | `tests/` + 真机 CLI 验证，无 A–T 对照；其中 M/N/O/P/Q/R/S/T 八项（伏笔生命周期、悬念谜题、素材研究、角色认知隔离、物品流转、技能状态、记忆去重、非线性时间线）**完全没有用例** | §23 |
| 6 | **五十五张表** | 索引 17 张（`artifacts` `entities` `events` `fts_blocks` …），`issues` 建而不填 | §17.2 |
| 7 | **工作流字段名对齐**：`workflow_id/name/description/trigger/preconditions/inputs/outputs/steps/agents/permissions/quality_gates/approval_points/failure_handling/retry_policy/resume_policy/idempotency/acceptance_criteria` | `catalog.yaml` 用 `id`/`input_schema`/`output_schema`/`tool_permissions`/`pause_resume` | §11.1 |

---

## 五、V1.1 不再规定、但现状依赖的（保留，标注依据已换）

1. **十维审核清单**：V1.1 删掉 V1.0 §11，只在 WF-14 列了 15 步检查（设定一致性/时间线/人物状态/角色认知/物品技能/伏笔悬念/逻辑/语言/节奏…）。
   现状 `workflows/dimensions.py` 的十维**与 V1.1 不冲突，但失去原文依据**。
2. **ID 与命名规范**：V1.0 §9 整节消失，只剩「所有正式实体文件必须具有唯一 ID」「不允许依赖文件名作为唯一身份标识」。
3. **执行总则与首次执行指令**（V1.0 §0 §31 §32）：V1.1 无对家；现行 ADR 与 `Vault_Config.yaml` 继续有效。
4. **模块布局**：V1.1 §2.4 画的是 `novel_management/core/{config,database,models,...}`；现状是 `src/novel_management/<domain>/`。
   纯命名差异，按 §25.1「降低私人代码优先」不建议为对齐而搬迁 —— 记一条 ADR 说明不采纳即可。
5. **`§25.1 私人代码优先序`**（Obsidian 原生 > 插件 > 模板/Schema > Prompt > 声明式工作流 > Python 增强 > 私人代码）：
   这是 V1.1 新加的**实施方针**，对第 4 项（仪表盘）与第 2 项（素材目录）有直接影响 —— 能用 Dataview + 模板做的不写 Python。
6. **阶段坐标**：V1.1 §22 是 P0–P8；现行里程碑是 T01–T12（V4.0 坐标系）。两者是同一批工作的两套编号，需要一张映射表，
   否则「P5 做完了吗」永远要现场推。

---

## 六、建议批次（每批独立可验收）

| 批次 | 内容 | 影响面 | 验收 |
|---|---|---|---|
| **D0** | 五项裁决落定 + 本文件更新为「已裁定」 | 无写入 | 作者确认 |
| **D1** | 目录细化（记忆 14 / 素材 9 / 伏笔 4）+ 英文遗产目录收编 | 33 份 md 移动、40+ 空壳目录 | `novel validate` 0 错；双链不断 |
| **D2** | 24 个数据契约（先 FSP/SUS/MYS/NLK + CTR/CTI，再素材 6 类，再记忆 9 类） | 新增 schema，不动存量 | `novel schema check` 全通过；`entity validate` 对存量仍 0 错 |
| **D3** | 版本服务 11 功能（`novel version *`）+ `VER-` + L1–L4 | 与 `release/` 并存或替换，取决于裁决 5 | 空库跑通 snapshot→tag→diff→verify→restore 预演 |
| **D4** | 命令族对齐（别名层或改名）+ 7 处差异 | CLI 表面 | 旧命令全部仍可用；`--help` 有对照 |
| **D5** | 仪表盘 14 项 + 指标 9 项（Dataview/模板优先） | `00_作者工作台` | 每项指标能指到数据来源 |
| **D6** | A–T 验收矩阵：能脚本化的 8 项补用例，其余写人工步骤 | `docs/operations/` + `tests/` | 每项有「怎么跑、看什么」 |

D1 与 D2 互不依赖，可并行；D3 依赖裁决 5；D5 依赖 D2（没有记忆契约就没有「待入库记忆」指标）。
