# 冷启动代码审查（2026-11-23 · v0.15.5）

> 视角：完全不了解本项目、但能力足够的工程师，只看仓库与自己动手跑。
> 判据：每一条都附可复现的测量方式，不写"感觉"。
> 范围：`novel-management` 代码仓 + `超长篇小说管理系统` Vault + `novel-runtime` 运行时。

---

## 零、先说什么是对的

审查报告容易写成清单式否定，所以先把**经得起检验的设计**列出来——这些不该在后续整改中被顺手拆掉：

| 项 | 实测证据 |
|---|---|
| 任务状态机 | 11 态，单一起点 `queued`、双终点 `cancelled`/`completed`，从 `queued` 出发**全部可达**，无孤岛态 |
| 实体契约 | `additionalProperties: false`，真库 999 份档案实测**0 份不通过**——已覆盖的部分真的合规 |
| 22 步发布协议 | 临时 Vault 上从零跑通全 22 步，含 `propose declare-none` 与 `approve release --hash` 绑定字节 |
| 三物理边界 | `paths.py` 用目录结构隔离，不靠 `.gitignore`（ADR-001） |
| 幂等判据 | `maps` 复跑实测**0 文件改动**，"0 改动"而不是"没报错" |

问题集中在下半段：**契约覆盖的是"世界设定"，没覆盖"正文与规划"；工作流定义齐全，但没有执行器；文档坐标停在 V4.0。**

---

## 一、P0：会导致数据或信任损失

### P0-1 正文 28 份与 `chapter.schema.json` 字段名完全脱节

真库正文档案的实际 frontmatter：

```yaml
id: ch_001001
type: manuscript
title: 引子·楔子
volume_id: plan_000008
status: published
canon_status: deprecated
word_count: 1997
```

而 `schemas/chapter.schema.json` 要求的是另一套：

```
required: [chapter_id, project_id, stage, status]
properties: [approved_at, approved_by, chapter_id, continuity_signoff,
             order_in_volume, project_id, stage, status, title, volume, word_count]
```

四个硬矛盾：

| 数据侧 | 契约侧 |
|---|---|
| `id` | `chapter_id` |
| `type: manuscript` | 契约里**没有 type 字段** |
| `volume_id` | `volume` |
| 无 | `project_id`、`stage`（必填） |

后果不是"校验报错"，而是**校验根本不会发生**：`SCHEMA_BY_TYPE` 里没有 `manuscript` 这个键，正文 28 份全部落进"无契约"桶。整个系统最核心的产物（正文）在契约体系里不存在。

这不是"少写一份 schema"，是两套字段命名并行：`type: manuscript` 对 `chapter.schema.json`，`volume_id` 对 `volume`。修的时候必须选一套——而 `canon_status: deprecated` 出现在 `status: published` 的正文上，说明第三套语义（发布状态 vs 正典状态）也已经分叉。

**判据**：`grep -rh "^type: " 04_正文 | sort | uniq -c` → 全是 `manuscript`；`python -c "json.load(open('schemas/chapter.schema.json'))['required']"` → 四个字段一个都不在档案里。

> **处置状态：已在 0.15.6 修复。** 定位时发现错的是契约不是数据：`chapter_id` 在全系统没有任何生产者，
> 而 `draft.py` 的唯一新稿生成器写 `id`。故只改契约（`id`/`type`/`title`/`status`，`type` 接纳
> `chapter` 与 `manuscript` 两值），零数据改动。结果：28 份正文全部入契约，全库无契约 47 → 19。

### P0-2 唯一一份"风险披露"文档的坐标是 V4.0

`docs/operations/known_risks.md` 开头写：

> 按 V4.0 §18 第 13 条要求披露。

全文 24 处旧路径（`01_Canon`、`03_Manuscripts`、`07_Staging`…）。这份文档的全部价值在于"告诉后来者哪里不可靠"，而它自己就不可靠。更糟的是它谈的"能力缺口"（R-01 旧系统 30 条命令未迁移）在 0.15.1 之后已经部分不成立。

同类旧账：文档旧路径残留 **220 处**（12 种旧目录模式），分布在 29 个源码文件与 35 份文档里。其中 `cli_reference.md` 38 处、`known_risks.md` 24 处、`getting_started.md` 19 处。

**新人照着 `getting_started.md` 走，第一步就会撞到不存在的目录。**

### P0-3 备份无保留策略，已 189MB / 12301 文件

```
/Users/Mr_Zhou/WorkBuddy/novel-runtime/backups  189M  10 个子目录
最新单份 BK-20261123-001541                      38M
```

`backup/` 模块里没有任何 `max_backups` / `retention` / `prune` 代码。每次 `--confirm` 写入都是全量拷贝。10 个子目录里 2 个是人工建的（`maps-prov-39-*`），其余是 `BK-*`，两天内长到 38MB/份。

没有清理策略的备份不是备份，是磁盘泄漏——而且它会把真正需要保留的那一份埋掉。

> **处置状态：已在 0.15.9 加上 `novel backup --prune --keep N`（默认 10）。**
> 默认只预演，`--confirm` 才删；**只处理 `BK-*`**，人工建的 `maps-prov-39-*`
> 永远不在清理范围内——自动删掉人亲手建的备份，是这套工具最不该做的事。
>
> 一处修正：原报告写「保留最近 N 份 **+ 按天去重**」，实际只做了前者。
> **按天去重不该做**：同一天的两份备份可能是不同时间点的状态，按天合并会丢掉其中一份，
> 而备份的全部价值就是「那份还在」。
>
> 另外实测澄清：`backup --list` 只认 4 份 `BK-*`（各约 30MB / 3041 文件），
> 189MB 里其余是人工建的备份目录。

---

## 二、P1：闭环不成立

### P1-1 20 条工作流是文档，不是可执行流程

`catalog.yaml` 21 条定义（WF-00…WF-19 共 20 条 + 1 条裸名 `simulation`），字段面齐全：

```
工作流总数: 21
含 acceptance: 20   含 tool_permissions: 20   含 steps: 20
```

但命令层面：

```
workflow run   开一个实例（只记账，不执行步骤）
```

`steps` 写得很细，没有任何代码消费它。`tool_permissions` 也只进 `agents/tools.py` 过滤 LLM 可见工具，不约束 CLI。**结果是：工作流目录校验通过（20 条定义、字段齐全），但它描述的动作一件都不会自动发生。**

这不是"功能缺失"，是**验收假象**：`workflow validate` 全绿，读者会以为流程可执行。

### P1-2 `approve` 的提示语只说了发布链路的最后一步（原判有误，已更正）

> **本条原判是错的，先更正。** 原文写「`publish` 直调 `publish_task()`，绕过 22 步协议」。
> 复核 `release/__init__.py:133` 发现 `publish_task()` **内部调的就是 `execute()`**——同一个
> 22 步执行器（含 `check_gates`、`check_approval`、`contract.check`）。`publish` 不是旁路，
> 是 `release execute` 的**按任务快捷入口**：自己找到该任务的计划、核对已获批准，然后执行同一套步骤。
> 之前只看 `flow.py` 里那行 `publish_task(...)` 就下了结论，没往下读一层函数体。

真正的问题只剩一处：`approve`（正文批准）成功后只打印 `novel publish --task X`，
而实际前置是三步：

1. `novel release prepare --task X` —— 生成发布计划
2. `novel approve release <REL-ID> --hash <计划哈希> --as author` —— 批准计划
3. `novel publish --task X --as author --confirm` —— 执行

漏掉前两步，用户会先撞一次「任务还没有准备发布计划」，再被错误信息指引回头补。
信息没丢，只是延后一轮才暴露。已在 0.15.8 把三步写进提示语，并注明第 3 步与
`release execute` 等价。

**仍然成立的部分**：真实库 `release status` 判 `NO_RELEASE`、`releases/` 0 个文件——
22 步协议从上线至今一次都没走过。这条链路能不能跑，只有临时库验证过（22 步全绿）。

### P1-3 契约 27 份里 8 份没有任何代码引用

未被任何 `src/` 路径引用的契约：

```
change_request / dependency / execution_receipt / import_manifest
release_manifest / release_plan / source_ref / transaction_journal
```

（`_common` 是 `$ref` 基座，不算。）这 8 份正是**运行时产物**的契约——发布清单、事务日志、导入清单、依赖判定结果。它们存在，是因为 V1.1 要求；它们没被引用，是因为对应模块还没写出产物（`releases/`、`transactions/` 均 0 文件）。

也就是说：**契约走在实现前面，且看不出哪些已经有实现。**

### P1-4 47 份档案无契约，全是创作链路的核心产物

```
manuscript      28   ← 正文
scene_beat      11
chapter_plan     3
volume           2
chapter_outline  2
volume_design    1
```

四个不同的 `type` 描述同一层东西（规划）：`chapter_plan` / `chapter_outline` / `volume` / `volume_design`。这是**同一概念四种命名**，且四种都没有契约。

### P1-5 规划没有创建入口

```
novel plan {show, check, derive}
  show    逐项看 §10.2 的字段有没有、出处在哪
  check   只判够不够开写
  derive  从「必须发生／禁止发生」派生结构化字段
```

没有 `create`。`plan_*.md` 必须手写，而 `novel write` 的前置条件正是 `plan.check` 通过。**新人按文档走，第二步就卡住**——没有任何命令能产出第一份合法的章纲。

对照：`entity create` / `event create` / `relation create` 都有（0.15.1 补的），唯独创作链路最上游的规划没有。

> **处置状态：已在 0.15.7 修复。** 追这条时发现比「缺 create」更严重的问题：`find_plan()` 按
> `frontmatter.id == 传入的 chapter_id` 匹配，而真库 23 份规划的 `id` 是 `plan_XXXXXX`，
> 于是 `novel write --chapter ch_XXXXXX` 永远找不到规划、永远拒绝开写——**写新章这条链路
> 在真库 100% 不可用**。参数名叫 `--chapter` 却要传规划号，靠文档提醒不管用。
> 修法：`find_plan` 双键匹配（`id` 或 `chapter_id`）+ 新增 `plan create`（发号走 `IdAllocator`，
> 骨架不预填示例，否则 `derive()` 会把示例当成作者原话读成「必须发生」）。

---

## 三、P2：一致性与可维护性

### P2-1 文档声明的数字与实测不符

| 声明位置 | 说的 | 实测 |
|---|---|---|
| `README.md:312` | 30 条命令 | **43** 条 |
| README 命令表 | — | 只列 **35** 条，缺 `continuity/entity/event/idea/note/project/relation/workflow` 八条 |
| 记忆与小结 | 26 份 schema + 1 公共 | **27** 份 `.json` |
| 记忆与小结 | 真库 23 种 type | **17** 种（多算的是 `07_审核与报告` 下的报告类档案） |

最后一条是自己的教训：**统计口径没写清，数字就会被重复引用成事实。**

### P2-2 命令体系扁平，41 条各成一族

43 个顶层命令里只有 `init-author/init-project` 成对，其余 41 条平铺。`doctor`/`status`/`validate`/`diff` 是只读族，`stage`/`approve`/`publish`/`release` 是发布族——语义上有族，接口上没有。

### P2-3 同名不同物与同物不同名

| 现象 | 两处 |
|---|---|
| `reviews/` 模块（191 行，存审核记录） | `workflows/review.py`（审核工作流） |
| `staging/` 模块（216 行） | `_staging/` 目录（真库 1 个 README） |
| `manuscript` | `chapter`（契约名） |
| `asset` | `story_asset`（契约枚举里叫这个） |
| `WF-00`…`WF-19` | `simulation`（裸名，无前缀） |
| `inspect` 命令 | `inspect_cmd` 模块（装的是 doctor/status/validate/diff，**不含 inspect**） |

最后一条是 0.15.5 正名时留下的新歧义：`novel inspect` 在 `approval_cmd.py`，`inspect_cmd.py` 里反而没有 `inspect`。docstring 写了分工，但**文件名仍在误导**。

### P2-4 十个单文件包，其中一个只有 mock

```
approvals audit backup baseline config reviews staging storage tasks   （均 1 文件）
adapters  （2 文件：__init__.py + mock.py）
```

`adapters/mock.py` 只有 `LLMClient` Protocol + `MockLLM`——**从未接过真实模型**。ADR-017 写的是"Agent 适配器"，实现是"测试替身"。这不算错（先定协议），但文档没说"目前只有 mock"。

### P2-5 `quality_gates/baseline.yaml` 是死文件

代码读的是 `catalog.yaml` 里的 `quality_gates` 字段（`workflows/registry.py:49`、`cli/workflow_cmd.py:140`），**不读这个文件**。而这个 `.yaml` 文件的内容是 markdown 散文包着 ` ```yaml ` 围栏——它本身就不是合法 YAML。

一个扩展名叫 `.yaml`、内容是 markdown、没有任何 reader 的文件，只会在排查时浪费一次时间。

### P2-6 `validate` 这个名字宽于它的职责

`validate` 不跑契约校验。契约由 `release` 的 `validation/contract.py` 闸门跑。所以：

- `novel validate` 通过 ≠ 数据合规
- 真库 `validate` 报"1046 档 / 重复 0 / 断链 0"，同时 47 份档案没有契约、28 份正文与契约字段名不符

**名字承诺了"校验"，实际做的是"查重 + 双链 + 命名一致性"。** 这是最容易被误读成"数据没问题"的一条。

### P2-7 权限：RBAC 只管 Agent，不管 CLI

`ensure_actor` 覆盖 37 处，写入命令基本都有——**但只校验"这个 actor 存在"，不校验"这个角色能不能做这件事"**。

`may_propose` / `may_release` / `read_scope` 只在 `agents/tools.py` 里用于过滤 LLM 可见工具。CLI 层实测：`novel stage --as ingestor` 可以往 `02_世界知识库/` 提交 Canon 变更（矩阵里 ingestor 只有 S 无 P）。

按 ADR-006，这是**有意的**（staging 之后由人批，不靠角色拦）。但没有任何文档写"角色权限只在 Agent 通道生效，CLI 通道靠 staging + 人工批准"。读者看到权限矩阵，会以为有硬约束。

### P2-8 运行时几乎全空，说明流程从未真实使用

```
tasks=0  staging=0  preflight=0  reviews=0  releases=0  issues=0  transactions=0
backups=12301 文件
```

七个 0 一个 12301。**除了备份，这套流程没有留下任何运行痕迹。** 这不是"没配好"，是"设计出来的路径与实际使用路径不重合"。

另有 5 个 8 字符 tempfile 残留目录（各 1 个 4KB 文件）在 runtime 根。

### P2-9 测试：579 用例，258 红

```
unit=25 文件  integration=16 文件  用例=579
红：121 unit + 137 integration
```

红的全部是 V4.0 假设（`split_id('CHAR-0001')`、`vault/"10_Projects"`、`01_Canon/Locations`）。绿的是 `test_maps`(5)、`test_config_defaults`(2)、`test_release_lock`(9)、`test_agents_prompts`。

**44% 的用例在测一套已经不存在的目录结构。** 它们既不能防回归，又会淹没真正的失败。

### P2-10 上下文成本：35 份文档 + 18 份 ADR + 2117 行顶层单文件

新人要理解"一次发布"需要读：ADR-006（staging）、ADR-014（批准绑字节）、ADR-015（22 步）、ADR-018（审核与记忆闭环）、`known_risks.md`（已过期）、`cli_reference.md`（38 处旧路径）。

其中 ADR-004/012/013/016/018 也含旧路径。**ADR 是"为什么这么设计"的唯一记录，而它们指向的目录已经不存在了。**

顶层单文件：`ids.py` 619 行、`schema.py` 406 行、`paths.py` 324 行。

`cli` 里最大的文件是 `cli/__init__.py` **1019 行**——43 条命令的注册、参数定义与分发全在这一个文件里。每加一条命令都要改它，这是 `cli` 成为最大模块（6482 行）的结构性原因。

---

## 四、模糊不清的说法（需要作者裁定）

这一节不是缺陷，是必须有人拍板才能继续的歧义：

1. **`canon_status` 与 `status` 在正文上同时存在**。真库 `ch_001001` 是 `status: published` + `canon_status: deprecated`。正文已发布但正典状态废弃——这两者谁说了算？
2. **"正式区六根"实际是五根**。`PUBLISH_TARGETS` 里 `02_世界知识库` 出现两次（`canon_root` 与 `ledger_root` 指向同一目录），错误信息里因此打印两遍同一路径。
3. **`publish` 与 `release` 的适用边界**。单章直发什么时候合规？ADR-015 说必须走协议，`flow.py` 提供了快路。
4. **`workflow run` 的语义**。"只记账"的实例由谁消费？如果永远没有消费者，它就是审计日志而不是工作流引擎。
5. **`governance` 与 `quality_gates` 两套质量规则**。catalog 里有 `quality_gates` 字段，仓库根有 `quality_gates/baseline.yaml`（死文件），哪个是权威？

---

## 五、建议处置顺序

按"修了能解锁后面多少事"排，不按严重度排：

| # | 动作 | 为什么排这个位置 |
|---|---|---|
| 1 | ~~正文契约二选一~~ | **已做（0.15.6）**：错的是契约，改契约不动数据；28 份正文已入契约 |
| 2 | ~~给 `plan` 加 `create`~~ | **已做（0.15.7）**：顺带查出 `find_plan` 只认 `id`，`write` 在真库原本 100% 不可用 |
| 3 | 重写 `known_risks.md`（或标注为历史归档） | 它是唯一的风险入口，过期比没有更坏 |
| 4 | ~~明确 `publish` 与 `release` 的分工，改 `approve` 的提示语~~ | **已改（0.15.8）**：原判「提示语指向旁路」有误，`publish` 走的就是 22 步；只补全了缺失的前两步 |
| 5 | ~~备份加保留策略~~ | **已做（0.15.9）**：`backup --prune --keep N`，默认预演、只动 `BK-*`；按天去重经判断不做 |
| 6 | 清 215 处旧路径（先 docs，再 29 个源码文件） | 纯机械，但量大，适合单独一批 |
| 7 | 修 258 个红测试，或整体标注 `@pytest.mark.legacy` 隔离 | 让绿的部分重新有信号 |
| 8 | 补 47 份无契约档案的类型归一（四种规划 type 收成一种） | 与 #1 同批做，避免二次返工 |
| 9 | 写一份"CLI 与 Agent 两条通道的权限差异"说明 | 消除 RBAC 的误读 |
| 10 | 删 `quality_gates/baseline.yaml`，清 5 个 tempfile 残留目录 | 零风险，随手 |

---

## 六、方法论说明（本轮的自我修正）

上一轮审查出现了大量来回调用：同一文件分 5 次读、同一日志换 4 种 grep、失败后换参数盲试（`init-author` 跑 3 次、`approve release` 跑 2 次）。原因有四条，本轮已改：

1. **先建地图再下钻**——一次性拉模块清单/命令清单/契约清单/测试规模，后续定位都在索引里找
2. **整份读，不分段**——`tasks/__init__.py` 一次 Read 完，不再 `grep → Read 1-140 → Read 188-248`
3. **失败只读一次完整 stderr**——不猜参数
4. **显式待办**——四个审查域各自成任务，做完即关，防止发散

本轮共 7 次批量调用完成全部核实，其中 5 次是并行三路。同样的结论，调用量约为上一轮的三分之一。

---

## 附：本轮实测数据

| 指标 | 数值 |
|---|---|
| 版本 | novel 0.15.5 |
| 顶层命令 | 43（41 条各成一族） |
| 源码规模 | 27550 行 / 96 个 `.py`（`cli` 6482 / `workflows` 2836 / `agents` 2802 / `release` 2710 / `maps` 1895） |
| 契约 | 27 份 `.json`，`SCHEMA_BY_TYPE` 覆盖 14 类，真库实际用到 11 类 |
| 真库档案 | 1046 份带 frontmatter（另 10 份无）+ 无 frontmatter 10 |
| 契约结果 | 过 999 / 无契约 47 / 不通过 0 |
| type 种类 | 17 |
| 工作流 | catalog 21 条（20 条完整 + 1 条裸名），`run` 只记账 |
| 运行时 | tasks/staging/preflight/reviews/releases/issues/transactions 全 0；backups 12301 文件 / 189MB |
| 测试 | 579 用例，258 红（121 unit + 137 integration） |
| 文档 | `docs/` 下 35 份 `.md`（其中 ADR 18 份 + 索引 1 份），旧路径残留 220 处（12 种模式） |
