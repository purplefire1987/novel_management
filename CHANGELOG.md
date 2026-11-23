# 变更日志

格式：语义化版本。每条记录写明兼容性影响与迁移提示。

## 0.15.8 — 2026-11-23

**更正一条误判，并补全 `approve` 之后的指引。**

审查报告 P1-2 原写「`novel publish` 直调 `publish_task()`，绕过 22 步协议」。
复核 `release/__init__.py:133` 后发现 `publish_task()` **内部调的就是 `execute()`**——
同一个 22 步执行器（含 `check_gates`、`check_approval`、`contract.check`）。
`publish` 不是旁路，是 `release execute` 的按任务快捷入口：自找该任务的计划、
核对已获批准，然后执行同一套步骤。

错因记在这里：当时只看到 `flow.py` 里那行 `publish_task(...)` 就下了结论，
没有往下读一层函数体。**判据停在调用层，结论就会停在错误的那一层。**

真正的问题只有一处，已修：`approve`（正文批准）之后只打印
`novel publish --task X` 一行，而实际链路是四步——

```
0) novel proposal declare-none --chapter ch_XXXXXX --reason <原因>   （本章不产记忆时）
1) novel release prepare --task <task>
2) novel approve release <REL-ID> --hash <计划哈希> --as author
3) novel publish --task <task> --as author --confirm
```

第 0 步是实测才发现的：直接跑 `release prepare` 会被记忆闭环以
`NO_MEMORY_DECISION` 拒绝（正文与记忆必须同批发布）。提示语里不写这一步，
用户走第一步就撞墙。章节号从 `task.output_files` 里提取，不用手填。

实测（临时库，从零）：`declare-none` → `prepare`（REL-000001，计划哈希 bac6a108…）
→ `approve release` → `publish` → 正式区确实写入 `04_正文/初稿/ch_000001.md`，
产出 `receipt RCPT-000001` 与 `index IDX-000001`。这同时验证了
**22 步协议在临时库能跑完整圈**——真库至今 `release status` 仍判 `NO_RELEASE`。

## 0.15.7 — 2026-11-23

**`novel write` 在真库 100% 不可用，原因是规划查找只认一个键。**

`find_plan()` 按 `frontmatter.id == 传入的 chapter_id` 匹配，而真库 23 份规划的
`id` 全是 `plan_XXXXXX`。`novel write --chapter ch_XXXXXX` 把**章节**号传进去，
于是永远匹配不到、永远拒绝开写；`novel plan check ch_XXXXXX` 同样报「找不到」。
参数名叫 `--chapter`，实际要传规划号——这种接口本身是陷阱，靠文档提醒不管用。

- `find_plan()` 改为**双键匹配**：`id` 或 `chapter_id` 任一命中即可。
  两个键都认是因为两种写法都真实存在：`chapter_id` 是规划与章节分开编号后的规范形态
  （`plan_000009` ↔ `ch_000123`），`id` 是早期把章号当规划 id 用的形态
  （`novel plan show plan_000001` 依赖这条，不能一刀砍掉）。
- `PLAN_FIELDS` 的「对应章节」原本认 `id`，于是每份规划都显示这一项「有」——
  出处是它自己的号，内容却对不上。改为只认 `chapter_id`，缺就报缺。
  真库 23 份旧规划会因此多一条「缺/必需 chapter_id」：这是实情，不是回归
  （那些章已写完，不需要再开写）。
- **新增 `novel plan create`**。此前 `plan` 只有 `show / check / derive`，
  章纲必须手写，而 `write` 的前置正是 `plan.check` 通过——新人第二条命令就卡死。
  发号走 `IdAllocator`（不自己编号），落点是 `03_故事规划/章节细纲/`，
  骨架只放 16 个小节标题，**不预填任何示例**：`derive()` 会把示例当成作者原话
  读成「必须发生」，那就是程序替作者做决定。
- 两条拒绝内建在 create 里：同章已有规划 → 拒绝（否则 `find_plan` 多份匹配时报错，
  那时要作者在两份「看起来都对」的文件里挑）；章号形态不对 → 拒绝（写错一个字符，
  这份章纲对 write 就等于不存在）。
- 找不到规划时的错误信息改为指当前路径（原来写着 V4.0 的 `02_Planning`），
  并直接给出 `novel plan create --chapter ... --title ...` 这条修复命令。

实测（临时库全链路）：预演 → 落盘 → `plan show ch_000001` 找到并逐项报缺 →
重复建被拒 → `CH-0001` 旧形态被拒。真库回归：`plan show plan_000001` 仍正常；
`plan show ch_001001` 仍找不到（旧稿确实没有章纲，不应凭空生成）。

测试基线：`tests/integration/test_t09_t10.py` 改前 21 ERROR → 改后 21 ERROR，
**无新增**；根因是 fixture 第 206 行在 V4.0 目录下取空列表，属 D6 既有欠账。

## 0.15.6 — 2026-11-23

**正文契约与写入器对齐。** 冷启动审查（`docs/architecture/cold_review_20261123.md` P0-1）量出：
`chapter.schema.json` 要求 `chapter_id` / `project_id` / `stage`，而这三个字段在全系统**没有任何生产者**——
`workflows/draft.py` 的 `render_document()` 写的是 `id` + `type: chapter` + `status: draft`，
索引（`indexing/builder.py` 三处）与 `validate` 也都读 `meta['id']`。

后果不是校验报错，而是**校验根本不发生**：`SCHEMA_BY_TYPE` 里没有 `manuscript` 键，
真库 28 份正文全部落进「无契约」桶。系统最核心的产物在契约体系里是隐形的。

- `chapter.schema.json` 重写：`id` 为唯一标识，`type` 接纳 `chapter`（新稿）/ `manuscript`
  （2026-11-04 导入的 28 份旧稿），`status` 承担生命周期（`draft`…`published`），
  `canon_status` 单独表达正典归属。旧版 `project_id` 删除——正文只存在于一个项目里，
  由路径决定，再写进 frontmatter 是第三份真相。
- `SCHEMA_BY_TYPE` 新增 `chapter` / `manuscript` 两键，同映射到 `chapter.schema.json`。
  **不为旧稿另造契约**：另造一份等于承认两套语义永久并存。
- `NARRATIVE_TYPES` 与 `changes/impact.py` 同步接纳 `manuscript`。此前那 28 份不进叙事索引，
  时间线/场景/揭示三个视图对它们全空，而空视图和「没有正文」在界面上长得一样。
- 样本修正：`chapter` 样本从旧字段名改为新形态；补上 0.15.1 新增 `relationship.schema.json`
  时漏掉的样本（`test_every_schema_has_a_sample` 因此一直红着）。

实测：真库 28 份正文**全部通过**契约；`novel write` 的实际产出通过契约；
全库过契约 999 → **1027**，无契约 47 → **19**（剩余全是规划类：`scene_beat` 11 /
`chapter_plan` 3 / `volume` 2 / `chapter_outline` 2 / `volume_design` 1）。
`validate`（1046 档 / 重复 0 / 双链 5685 条 / 0 断链）与 `doctor` 无回归。

测试基线：`test_schema_contract.py` 改前 27 红 → 改后 22 红，**无新增失败**；
减少的 5 条正是本次修复的。剩余 22 条是同一类既有欠账——样本里的 4 位号
（`ent_0002` / `fs_0001` / `prop_0102`）与六位契约冲突，属 D6。

BREAKING CHANGE: `chapter.schema.json` 的字段名变更。对数据无影响（存量本就是 `id` 形态），
但任何按旧契约（`chapter_id` / `stage`）生成的产物将被判为未知字段。

## 0.15.5 — 2026-11-23

**档 2 正名：五处「模块名与内容脱钩」一次改掉。** 依据
`docs/architecture/legacy_inventory.md` §五档 2 的 8–12 条。判据统一为
**CLI 表面零变化**——37 份 `--help` 输出改前改后逐字节 diff 为 0。

### 一、`cli/read.py` → `cli/inspect_cmd.py`（B1）

模块里没有 `read` 命令，装的是 `doctor`/`status`/`validate`/`diff`。找「读文件」
实现的人会在这个文件里绕很久。`git mv` + `cli/__init__.py` 两处 import。

**名字与 `novel inspect` 的关系写进了 docstring**：那条命令在 `approval_cmd.py`，
看的是 staging 里待批的候选，不是这里的体检与结构校验。不写清楚，
这次改名只是把一个歧义换成另一个。

### 二、`validation/proposals.py` → `workflows/proposals.py`（B2）

它做的是「从已批准正文提取 → 校验源哈希 → 批准」，是业务不是校验；而且它本来就
`import workflows.review`——校验包反过来依赖业务包。移动后：

- `validation/` 只剩 `closure`、`dependencies`、`contract`（三项都是真校验）；
- 依赖方向掰正为 `validation → workflows`，反方向为零；
- `validation/__init__.py` 与 `workflows/__init__.py` 的导出、三处 import 同步。

### 三、`frontmatter.py` 不再自称「唯一解析实现」（B3）

这句话误导过：真正的第二条路是 `markdown.load_meta`（走 PyYAML，能解嵌套）。
两者分工是**能力**不同，不是新旧关系。docstring 现在给了一张对照表
（依赖/能解什么/输入形状/谁在用），并写明「改这里只影响顶层解析，
调用点数远少于 `markdown`——想改一处影响全库是错的」。

### 四、删 `tasks` 的旧号与旧态兼容（C3）

`_LEGACY_TASK_RE`、`LEGACY_STATUS`、`normalize_task_id`、`normalize_status` 全删。
判据是**有没有东西可兼容**：真库 `runtime/tasks`、`staging`、`preflight` 至今 0 条，
这条兼容从上线起就没有对象。代价不是几行代码，而是每个读任务的地方都要走两遍：
`_paths()` 返回两份候选、`next_id()` glob 两遍、文件名反解两种形态——任何一处漏掉
就变成「任务不存在」。

- `schemas/task.schema.json` 的 `task_id` 同步收紧为 `^task_[0-9]{6}$`
  （**契约必须跟着代码走**，否则旧号能过校验却过不了运行时）。
- 实测：`task create` → `task_000001`；`task transition --to ready` 正常；
  `--to done` 被 argparse 的 choices 拒掉；`task inspect TASK-000001` 报
  「任务不存在」而不是崩。
- 若将来真要导入旧 runtime，先在那边把号改成 `task_` 再进来——一次转换，
  好过让运行时长期背着两套方言。

### 五、`ids.LEGACY_PREFIXES` 去掉映射内容，保留拒绝能力（C4）

11 条「前缀 → 中文去向」的描述删掉，改成纯名字集合（`frozenset`）：
`resolve_prefix("FAC")` 照样报错，只是提示不再逐条写「势力（已并入 ent）」。
理由两条：那 11 条是迁移期脚手架，全库 0 份旧号后它只会变成第二份要维护的真相
（第一份是 `TYPE_TO_PREFIX`）；去向本来就能从 `TYPE_TO_PREFIX` 推出来。

**审计报告的一条判据是错的，在此更正**：清单里写的是
「`allocate-id --type CHAR` 仍报错」。实测不成立——`CHAR` 在 `TYPE_TO_PREFIX` 里
映射到 `ent`，`resolve_prefix` 先命中它，**故意返回 `ent`**（这是 V1.0 的设计：
类型归 `type` 字段，ID 只表达「这是个世界实体」）。真正走废弃分支的是
`FAC`／`LOC`／`PLAN-VOL` 这类不在映射表里的。改前改后行为一致，拒绝能力未损。

### 验证

- 37 份 `--help` 与改前**逐字节一致**（含 `maps`、`release`、`task` 等长帮助）。
- `tests/integration/test_maps.py`（5）、`tests/unit/test_config_defaults.py`（2）、
  `tests/unit/test_release_lock.py`（9）全绿。
- 临时 Vault 端到端：`init-author` → `init-project` → `task create` →
  `task list` → `task transition`（顺流/非法值/旧号三种）。
- `import novel_system.workflows, novel_system.validation` 无循环依赖。

### 兼容性影响

- 旧任务号 `TASK-000001` 不再被识别：`runtime/tasks` 为 0 条，无迁移成本；
  若有外部脚本按旧号拼路径，需改 `task_`。
- `validation.proposals` 模块路径变了：外部只应通过 CLI 使用；库内三处 import 已改。
- 其余对外行为不变（CLI 参数、输出格式、契约除 `task_id` 外均未动）。

## 0.15.4 — 2026-11-23

**修好 `maps`，清掉历史包袱。** 依据 `docs/architecture/legacy_inventory.md` 的裁定：
① 修 `maps` ② 重写 `examples` ③ 让 `init` 真读模板 ④ 执行档 1。

### 一、`maps`：0.14.0 之后它一直是坏的，且会改坏数据

`jiuzhou.py` 还在 glob `LOC-*.md`，而全库已是 `ent_000001`。后果不是报错，是**安静地少画**：
真库预演报「缺州域 11 / 待建档治所 71」，只生成 **40 节点**（0.4.1 基线是 111）。
**加 `--confirm` 会把 111 节点的地图页降级成 40 节点。**

修的是三处，不是一处（这正是「只改一半」的典型）：

| # | 位置 | 旧 | 新 |
|---|---|---|---|
| 1 | `loc_files()` | `glob("LOC-*.md")` | `glob("ent_*.md")` + **`type: location` 过滤** |
| 2 | `name_of()`（新增） | `stem.split("_", 1)[1]` | 正则剥掉 `ent_\\d{6}_` 前缀 |
| 3 | `loc_rel()` | `stem.split("_", 1)[0]` | `ID_RE.match(...).group(1)` |

**第 2 条最关键也最容易漏**：`LOC-0001_某地` 只有一个下划线，`split("_", 1)[1]` 得 `某地`；
`ent_000906_怒涛府` 有两个，同样切法得 `000906_怒涛府`——地名表全部对不上，
而命令不报错。只把 glob 改成 `ent_*.md` 的话，症状从「40 节点」变成「40 节点 + 无报错」，
更难查。

- **不再自造 ID**：`allocate_fu()` 原来按 `LOC-` 最大号 +1 造新号。0.14.0 后 ID 由
  `ids.py` 统一发号，生成器自造会同时违反「形态」与「发号器」两条，且造出的号会进 canvas
  变成死链。现在缺档案就留空、标「待建档」。
- **`admin.py` 的 `id: WORLD-0040` 改成从文件名取**：重跑一次 `import-admin` 就会把
  `ent_000963` 改回旧形态，而这种破坏要等下次 `validate` 才暴露。
- 生成物落盘后的实测：**层级树 111 节点 / 110 连线**（与 0.4.1 基线一致），
  方位图 108/107；`file` 字段从已废弃的 `Locations/` 修正为 `地理与地点/`（死链 0）；
  复跑 **写入 0 个文件**（幂等）。备份 `<runtime>/backups/maps-pre-entfix-20261123-004453/`。
- 地图页文案里那句 `00_System/scripts/build_jiuzhou_map.py` 改成 `novel maps build`。

### 二、`examples/`：整份是 0.4.x 化石

旧示例 `CHAR-0001.md` **连 frontmatter 分隔符都没有**，`markdown.load_meta` 直接抛
`ParseError`；ID 是 V4.0 形态、`status: confirmed` 是旧枚举；测试对它 0 引用。
按现行契约重写为 2 实体 + 1 事件 + 1 章节 + `project.yaml`，四份全部过 `schema.check`。

README 里给了可复制的校验脚本，并写明一个**容易踩的坑**：`frontmatter.split` 负责切
`---`，`markdown.load_meta` 只收纯 YAML 块——整篇丢进去会报 `found another document`。

### 三、`init-project` 真读 `templates/project/project.yaml`

以前是 `cli/init.py` 里的字符串常量，模板文件从未被读取——那句「改了模板没生效」就是这么来的。
现在读模板并填充占位符，**产物与旧实现逐字节一致**（排除 `created_at`）。

- 模板缺失时**报错**，不回退内嵌默认值（静默回退会让同一个问题重新隐形）。
- 新增 `NOVEL_TEMPLATES_DIR` 覆盖，测试可用。
- 边界两条都实测：改模板 → 产物跟着变；模板缺失 → `UsageError`。
- 新增 `templates/README.md`，写清「谁读谁」与三条写模板的规矩。

### 四、契约修正：`chapter_id` 曾拒绝一切合法章节

`chapter.schema.json` 的 `chapter_id` 还是 `^CH-[0-9]{4}$`，`memory.schema.json` 同。
也就是说 0.14.0 之后**没有任何一份合法章节能过契约**。按 `foreshadowing` 已有的写法
改成 `^(ch_[0-9]{6}|CH-[0-9]{3,4})$`（新形态 + 历史形态豁免）。

### 五、档 1 清理（零行为改动）

| 动作 | 证据 |
|---|---|
| 删 `src/novel_system/core/` | 1 行空包，全库引用 1 处（自己） |
| 删 **31 个**沙箱垃圾文件（顶层 + `maps/` 2 个） | 均为 8 位名、4 字节，`tempfile` 回退产物；顶层从近百项降到 18 项 |
| 删 `defaults.yaml` 两个死键 | `config_version` / `runtime_subdirs` 在 `src/` 0 引用 |
| 同步两份 `defaults.yaml` | 仓库根是 V4.0 旧副本、包内是 V1.0，**之前已分叉**；`test_config_defaults` 重新变绿 |
| `README.md`：删 `core/`、改数量（26 契约 + 1 公共 / 20 工作流 + 6 细则） | 现数 |
| `cli_reference.md`：补 7 条命令 + 改 35→43 + 修末尾 4 行路径 | 现数 |

### 验证（真机）

- `novel doctor` 全绿；`novel validate` 通过（1046 档 / 重复 0 / 双链 5679 条 0 断链）。
- `tests/integration/test_maps.py` **5 绿**（含新增「非地点档案不得进图」），
  `tests/unit/test_config_defaults.py` **2 绿**。
- **「退回就红」已验证**：把 `name_of` 退回 `split("_", 1)[1]`，新用例立即失败；改回即绿。

### 兼容性影响与未解决项

- `maps build` 的输出会变（节点 ID 从 `LOC-` 变 `ent_`、`file` 指向 `地理与地点/`）。
  这是修正，不是回归；已落盘并复跑确认幂等。
- **`tests/unit` 仍有 121 项红**，原因与本次改动无关：测试停在 V4.0 假设
  （`split_id('CHAR-0001')`、`vault/"10_Projects"`、`01_Canon/Locations`）。
  这是欠账，对应 `v1_1_delta.md` 的 D6，不在本轮范围。
- 仓库根的 `config/defaults.yaml` **从未被读取**（`DEFAULTS_PATH` 指向包内）。
  本轮只是让它不再与包内分叉；要不要去掉这个副本，需另定。

## 0.15.3 — 2026-11-23

**基线换成 V1.1。** 本轮只做第一刀：Canon 状态枚举按 V1.1 §4.2 收成六值。
差异全貌与其余四批见 `docs/operations/v1_1_delta.md`。

### 一、Canon 枚举：`confirmed` → `canon`

V1.1 §4.2「统一采用」`idea / proposal / pending_review / canon / deprecated / rejected`。
旧口径是 V1.0 §6.1 的七值（`confirmed` `proposed` `under_review` `retcon_candidate` …），
两套词并存时最容易被误判成「两个字段」，所以一次迁完，不做双写。

- 改写 **1000 份**：`confirmed→canon` 989、`proposed→proposal` 10、`draft→proposal` 1。
  `deprecated` 51 份不动。
- **只改 `canon_status:` 这一行**：逐份做行级 diff 断言（必须正好 1 行变化），
  sha256 前后各记一次；实际 sha 变化 1000 份，与记录集合完全相等，无意外改动。
- **`99_Archive` 不动**：归档是冻结区，与「历史对照表保留旧号」同一条道理。
- 迁移报告：`<runtime>/reports/canon_enum_migration_20261123-001749.json`
  （含每份的 from/to 与前后 sha）。备份 `BK-20261123-001720`（3041 文件 / 29.9MB，已回读校验）。

### 二、契约与写入点同步

- `entity` / `event` / `foreshadowing` 三份 schema 的 `canon_status` 收紧为六值，
  描述里写死旧值映射，省得下一个人再猜 `confirmed` 算什么。
- `novel entity create` / `novel event create` 的 `--canon-status` 默认改 `proposal` 并给了 `choices`
  —— 以前默认 `proposed` 但不受约束，写错值要到 `entity validate` 才发现。
- `maps/` 两处生成模板里写死的 `canon_status: confirmed` 改 `canon`：地图页是**生成物**，
  生成器不改，下次重画就把旧值带回来。
- Vault 侧 17 份模板与策略文档（`02_作者级通用资料/Templates/*`、`Policies/Obsidian插件配置.md`）
  同步改值；`T-新建档案.md` 里那句「改 confirmed」改成「改 canon」。
- 「Agent 不得自行置为 canon」写进 schema 描述——六值里 `canon` 是唯一需要作者的。

### 三、验证（真机，不是推演）

- `novel validate`：1046 份档案、重复 0、双链 5679 条 0 断链 ✅
- `novel entity validate`：973 份知识库档案全部通过新枚举 ✅
- `novel rebuild-index`：正常，`02_作者级通用资料/Templates/Event.md` 因模板未发号未入库（预期）

### 兼容性影响

- 读取侧：`99_Archive/legacy_v1` 与历史叙述里仍会出现 `confirmed`，那是当时的记录，不改。
- 写入侧：一律六值；`--canon-status confirmed` 现在会被 argparse 拒掉，报可用值。
- 若某个外部脚本按 `confirmed` 统计「已确认」，要改成 `canon`。

## 0.15.2 — 2026-11-22

**这一轮补的是「代码改了，Agent 与提示词还停在 V4.0」的落差。**
代码侧 T01–T08 早已跑在 V1.0 上，但任务状态机是 V4.0 的六态、
九份角色卡与九份提示词里教的是**已经不存在的命令**。

### 一、任务状态机对齐 V1.0 §10.4

- 状态集换成规范十一态：`queued → ready → running → waiting_review → approved / rejected → completed`，
  异常 `blocked` / `paused` / `failed` / `cancelled`。V4.0 的
  `pending`/`in_progress`/`awaiting_author`/`done` 读盘时归一（`LEGACY_STATUS`）——认得出、不再写。
  旧六态缺三样规范要求的东西：没有 `ready`（分给人 vs 人真动手只能口头同步）、
  没有 `approved`/`rejected`（审核结论只能塞进 `approval_state`，而那是「作者批准」不是「编辑审核」）、
  没有 `paused`/`failed`（中断与失败只能记成 `blocked`，而 blocked 的语义是「等外部条件」）。
- **§10.4 要求「每次迁移记录操作者、时间、原因、关联任务」，此前只存当前状态**。
  新增 `Task.status_history`，`transition()` 写四样；非顺流迁移（blocked/paused/failed/rejected/cancelled）
  **强制 `--reason`**——顺流也要编理由的话，理由字段很快全是「继续」。
- 新增 `novel task transition <task_id> --to <状态> [--reason] [--related] [--as]`。
  `TaskStore.transition()` 一直有实现、有迁移表、有跳步拒绝，却从没有命令入口，
  于是状态机在代码里成立、在操作上不存在。`task list --status` 也补上 `choices`。
- **发布成功后走 `TaskStore.mark_published()`**，不再直接赋 `task.status = "done"` 了事。
  它**刻意绕过** `TRANSITIONS`：发布引擎报 ACTIVE 时内容已在正式区，是既成事实；
  此时因「任务还停在 queued」拒绝记账，状态文件会与磁盘矛盾，而磁盘才是事实源。
  绕过权限，不绕过记账——这次迁移照样进 `status_history`。

### 二、九份角色卡（代码真正读的那套）路径全换

`agents/<role_id>/agent.md` 是 `novel agent prompt --role` 读的文件，此前写着
`07_Staging/`、`03_Manuscripts/Drafts/CH-XXXX.md`、`01_Canon/World`、`06_Memory/`、`05_Ledgers/`。

- Canon 十七类：`01_Canon/World` → `02_世界知识库/世界与宇宙`，`Systems` → `技术与文明`，
  `Rules` → `规则与机制`
- 正文：`03_Manuscripts/Drafts/` → `04_正文/初稿/`，`Final/` → `04_正文/正式定稿/`
- 规划 `02_Planning/` → `03_故事规划/`；记忆 `06_Memory/` → `05_记忆与连续性/`
- **任务不在 Vault**：`07_Staging/tasks/TASK-*.yaml` 实为 `<runtime>/tasks/task_NNNNNN.yaml`
- staging 按任务归档：`_staging/<task_id>/<area>/<目标相对路径>`，不是 V4.0 的固定用途目录
- 每份补「## 工作流」段，指向 `novel workflow list --agent <role_id>` 与 `workflows/catalog.yaml`

### 三、九份提示词（Vault 里人读的那套）命令层重写

`00_作者工作台/Policies/Prompts/` 里教的命令**五个里有四个不存在**：

| 旧 | 新 |
|---|---|
| `novel ingest` | `novel import scan` → `novel import stage` |
| `novel find` / `novel show` | `novel search` / `novel entity show` |
| `novel annotate` / `commit-memory` | 已取消；`novel proposal extract` 从已批准正文抽取 |
| `novel history` / `snapshot` / `rollback` / `log` | `novel status` / `backup` / `restore` / `release rollback` |
| `novel audit` | `novel validate`（结构）+ `novel review submit`（内容十维） |
| `import-assets` / `import-lore` | **无对应命令**，只能逐份 `novel entity create` |

- `--split chapter/number/single` 四模式已废：解析器自动识别 Markdown 标题、中文章节标记、
  英文章节标记，切不出就整篇一章并记 `known_limits`。`--level` 只在多级标题时需要。
- 复合 ID 与四位旧号全清：`EVT-CH-0001-001` → `evt_000001`、`SCENE-CH-0001-002` → `scene_000002`、
  `CH-0045` → `ch_000045`。
- **落点修正**：`07_审核与报告/Change_Log/Ingest/` 是空壳，11 份批次日志实际都在
  `09_版本与变更记录/`，提示词教的落点是错的。
- **删掉「补记忆标注」整节**：V1.0 没有解析「作者备注（不进入正文）」的代码。
  留着这一节会让人手写一堆永远不被读的标注，还误以为系统已经知道那些事。

### 四、命名：一张表消灭歧义

九份提示词的文件名与代码注册名**只有两份一致**（`writer`、`world_architect`），
其余七份各是一套方言（`chief_editor` vs `editor_in_chief`、`lore_keeper` vs `researcher` …）。

**注册名不改**：审计日志里已经写着 `ingestor`，改了之后那些历史记录就查不到主人；
`schemas/task.schema.json` 的 `assigned_to` enum 也认它。
改为在 `Policies/README.md` 里放一张「文件名 ↔ role_id ↔ 中文」对照表，
并在每份提示词开头写明注册名。

### 五、小修

- 新增 `novel workflow list --agent <role_id>`：九个角色各自负责 1–9 条流程，此前只能按 `--stage` 过滤，
  「我在哪些流程里」得自己数 catalog。
- 补建 `02_世界知识库/时间线/`：十七类里最后一项，V1.0 重排时漏了。
- `novel --version` 与 `--help` 的描述从「V4.0 §5.3 命令集」改为「V1.0 §17」；
  `allocate-id --type` 的帮助从 `CHARACTER／LOCATION` 改为 `ent/ch/scene/evt/plan`（旧名仍认）。

### 已知未解决

- **提示词文件名未重命名**（`chief_editor.md` 等七份）。`git mv` 被 Vault 里一个
  2026-11-20 的陈旧 `.git/index.lock`（0 字节）挡住，沙箱又禁止删文件。
  对照表已解决「对不上」的问题，但一劳永逸仍需作者清锁后重命名。
- 沙箱残留清单见 `docs/沙箱残留清单_20261122.md`（本轮新增：`03_故事规划/{Scene_Beats,Volume_Outlines}`、
  `04_正文/{Drafts,Final}`、`05_记忆与连续性/Hot/` 英文子目录）。
- **pytest 仍不可用**（`tmp_path` 要临时目录，被安全中心拦）。本轮的验证全部走真机 CLI：
  状态机七次迁移 + 三次跳步拒绝 + 旧态归一 + 九个角色过滤 + 四项全量回归。

## 0.15.1 — 2026-11-22

**修「工具写出的文件过不了自己的校验」那一类问题。**
0.15.0 补的 CLI（`entity`／`relation`／`event`／`workflow`／`continuity`）此前只跑了预演，
这一轮把落盘路径逐条跑通，问题都在写入侧：契约没跟上、空值写法撞约束、开关不设防。

### 一、`event`／`relationship` 契约与写入器对齐

- **`event create` 写出的 6 个字段在旧契约里全是未知字段**（`novel_id`/`status`/`time_precision`/
  `narrative_time`/`created_by`/`confidence`），而 `event.schema.json` 是 `additionalProperties: false`。
  即「工具写出的文件过不了自己的校验」。现按 V1.0 §5.10 补齐：`time_precision`、`duration`、
  `narrative_time`、`first_chapter_id`、`related_chapters`、`witnesses`、`causes`、`involved`、
  `states_before`／`states_after`、`fact_status`、`information_status`、`confidence`、`created_by`／`updated_by`。
  存量 7 份事件字段一个未删，`sequence` 与 `location_id` 原样保留。
- **新建 `schemas/relationship.schema.json`**。此前**根本没有关系契约**：`relation create` 写出的
  `rel_*.md` 落到目录里就没人管，`entity validate` 又把它当实体判，于是 `rel_` 先撞 entity 的
  `^(ent|asset)_` 模式，真正的字段问题被第 1 条错误挡住。字段面照 V1.0 §5.3 逐条落。
- `relation create` / `event create` 现在**写盘前先过契约**，预演阶段就报错。
- `entity validate` 的契约路由从 if-elif 改成 `SCHEMA_BY_TYPE`（`schema.schema_name_for`），
  漏一类不再静默按 entity 判。

### 二、空值不写进 frontmatter

`summary: ""` 撞 `minLength: 1`，`location: ""` 撞 `^ent_[0-9]{6}$`——空串会撞约束，
让一份本来没问题的档案校验不过。新增 `entity_cmd._compact()`：空字符串、空列表不写进
frontmatter；**显式取值一律保留**（`unknown`／`false`／`0`／`null`）。
`time_precision: unknown`（查过，确实不知道）与「没有这个字段」（没查）必须能区分。

### 三、事件的事实状态默认 `unknown`

V1.0 §5.10 要求区分「作者确认发生／角色声称发生／传闻／计划」。新增 `--fact-status`，
默认 `unknown` 而**不是** `confirmed`：把传闻默认成事实是这类库最难查的污染，
默认 `unknown` 是一个看得见的缺口，`continuity check` 能报出来。

### 四、任务 ID 改 `task_`（V1.0 §9.2）

Vault 里 1046 份文档在 0.15.0 已统一到 `ent_000001` 形态，但 **runtime 里的任务仍是
`TASK-000001`**——重编号脚本只扫 Vault，扫不到 Runtime。`schemas/task.schema.json` 里
也硬编码着 `^TASK-[0-9]{6}$`。现改为 `task_000001`，`task_` 前缀按 §9.2。

- **兼容性**：旧号 `TASK-000001` 仍读得懂（`tasks.normalize_task_id`／`_LEGACY_TASK_RE`），
  已存在的 runtime 目录不会因为这次改名变成「任务不存在」。新号只发 `task_`。
- 真库当时 `tasks/` 与 `staging/` 都是空的，**无数据迁移成本**。
- 新增 `novel task list`（`TaskStore.list()` 一直有实现却没有命令入口，
  等于任务状态机对作者不可见）。

另修 `version.SPEC_VERSION`：它写着 `4.0.0`，而代码对齐的是产品文档 **V1.0**
（该文档第 5 行「文档版本：V1.0」）。一个「指的是哪份规范」的常量写错版本号，
比没有它更误导——读代码的人会以为系统仍在按 V4.0 实现。该常量此前无人引用，改动无副作用。

### 五、`--allow-unchecked` 只有作者能开

`review submit` 的十维闸门里写着 `reviewer_role == "author"`——**这是个永远不会成立的条件**，
角色只有九个（`editor`／`editor_in_chief`／…），`author` 不在其中。于是「作者可以越过」
这条通道从没打开过，而 `--allow-unchecked` 又没人校验身份，任何角色加上它都能绕过十维。
一条死分支配一个不设防的开关，正好把口子开在最不该开的地方。

现改为判**操作者**（`reviewer == "author"`）：非作者用 `--allow-unchecked` 直接
`SecurityError`（exit 3）；作者用则放行，并在报告里写明「覆盖 0/10」。

### 六、陈旧锁自动接管

`ids._acquire` 只看锁文件在不在，不看持有进程死活。进程被杀后锁永远留着，
之后每次发号都报「另一个进程正在分配 ID」。现加 `_holder_state()`：读锁里的 pid，
`os.kill(pid, 0)` 判活；**确认已死**才接管（只接管一次），判断不了时一律当活锁
（误判活锁只是多等几秒，误判死锁会让两个进程同时发号）。

### 验证

- 真库 `entity validate` **973 份全部通过**、`validate` **1046 份 / 重复 0 / 双链 5679 条零断链**、
  `doctor` 全部通过。
- 临时 Vault 端到端跑通：`entity create` → `relation create` → `event create` → `stage` →
  `review submit`（十维共 10/10 → APPROVE；缺项 → 拒绝；`--role editor --as editor --allow-unchecked` → 拒绝；
  `--as author --allow-unchecked` → 放行）。

### 已知未解决（留待作者裁定）

- **发布编号仍是 `REL-000001`**（`release/store.py`）。V1.0 §9.2 的十二条前缀里没有「发布」，
  最接近的是 `ver_`（私库版本），但「发布版本」与「私库版本」是不是同一件事，得作者定。
  好处是现在 `release status` 判 `NO_RELEASE`，一份发布也没有，改起来无迁移成本；
  坏处是发布协议绑着 22 步事务、计划哈希与批准记录，改编号得连 `approve release <REL-ID>`
  的入参一起动。不动的原因是「没有 V1.0 对应物」这件事本身需要裁定，不该由我替它挑一个。
- **章卡 ID 与章节 ID 仍共用一个字符串**：`novel write --chapter plan_000001` 产出的草稿是
  `04_正文/初稿/plan_000001.md`，`review submit` 也要求 `--chapter` 同时能在 `03_故事规划` 里
  找到同 id 的章卡。V1.0 §9.2 有独立的 `ch_` 前缀（发号已验证 `ch_000001`），但章卡里没有
  指向正文 ID 的字段，两者之间没有映射。改名会牵动真库 2 章正文与 28 份旧稿，待裁定。
- **pytest 在本会话完全不可用**：`tmp_path` 需要临时目录，而本会话的临时目录被安全中心拦，
  fixture 初始化即 `FileNotFoundError`。全部验证改用真机 CLI 端到端跑（见上）。
  这不算代码问题，但没有测试护栏这件事要说出来——`tests/unit/test_ids.py` 等仍以
  live CLI 之外的方式无法回归。
- 沙箱禁止删除，`docs/沙箱残留清单_20261122.md` 列了 V4.0 空壳目录、旧指针、
  重复仪表盘与临时垃圾文件。这些只影响整洁，不影响 `validate`／`doctor`。

## 0.15.0 — 2026-11-22

**按 V1.0 规范（`超长篇小说全生命周期管理系统_产品需求与实施规范.md`）重排目录树与 ID 体系。**
这是当日第三次全库改写（前两次：ID 形态合规、六位 + 前缀收敛），也是幅度最大的一次：
根目录、项目内子目录、知识库分类、ID 格式四处同时变。

### 一、目录树：V4.0 → V1.0

| V4.0 | V1.0 |
|---|---|
| `00_System` | `00_作者工作台` |
| `10_Projects` | `01_小说项目` |
| `90_Shared_Assets` | `02_作者级通用资料` |
| `01_Canon` | `02_世界知识库`（17 类） |
| `02_Planning` | `03_故事规划`（6 子） |
| `03_Manuscripts` | `04_正文`（初稿／修订稿／正式定稿） |
| `04_Story_Assets` | `06_素材与研究` |
| `05_Ledgers` | **解散**：事件/关系/时间线进知识库、伏笔进规划、骰子进素材 |
| `06_Memory` | `05_记忆与连续性` |
| `07_Staging` | `_staging` |
| `08_Reviews` | 拆成 `07_审核与报告` + `09_版本与变更记录` |

`99_Archive` 保留——V1.0 的四个根里没有归档位，但它装着旧系统全量，删不得。
新建 V1.0 要求但当前缺失的空目录：`00_项目管理`、`01_灵感与创意`、
`08_工作流与任务视图`、以及知识库里暂时没有数据的 `种族与族群`／`资源与经济`／
`技术与文明`／`文化与社会`／`特殊设定`／`规则与机制`。

搬迁 1170 份文件、清理 80 个空目录、2 个 canvas 的 327 个文件节点路径重算。

### 二、知识库分类：6 类垃圾桶 → 17 类

旧结构里 `World/` 442 份、`Systems/` 226 份，按 `type` 字段拆开看：

| 旧目录 | 份数 | 实际构成 |
|---|---|---|
| `World/` | 442 | 物品 336 + 生物 76 + 世界条目 30 |
| `Systems/` | 226 | 技能 171 + 命途 42 + 世界条目 13 |
| `Rules/` | 0 | 空目录，43 个 `RULE-*` 散在别处 |

**668 份（69%）目录与语义不符。**这不是编号问题，是分类维度不足——
`type` 字段早就有 8 种精确值，目录却只有 6 个位置。

新分类按 `type` 归位：物品与装备 336 / 技能与能力 171 / 地理与地点 110 /
势力与组织 102 / 人物与角色 86 / 生物与生态 76 / 世界与宇宙 43 / 力量与成长 42。

**`规则与机制` 目录是空的**：库里 43 份 `RULE-` 前缀的档案 `type` 都是 `world`
（世界总表、规则书），没有一份 `type: rule`。这是真实的数据缺口，不是搬迁遗漏。

### 三、ID：`CHAR-000001` → `ent_000001`

V1.0 §9.2 用**单前缀 `ent_` 收所有世界知识实体**，类别靠 `type` 字段区分。

| 旧 | 新 | 份数 |
|---|---|---|
| `CHAR` `LOC` `FAC` `ITEM` `RULE` | `ent_000001..000966` | 966 |
| `ITEM`（素材） | `asset_000001..000022` | 22 |
| `CH`（旧稿） | `ch_001001..001028` | 28 |
| `CH`（章卡） | `plan_000001..000008` | 8 |
| `SCENE` | `scene_000001..000011` | 11 |
| `EVT` | `evt_000001..000007` | 7 |
| `FORESHADOW` | `fs_000001..000004` | 4 |

**三处 V1.0 没给答案、由实现补齐的：**

1. **正文/场景/规划/伏笔没有前缀。** §9.2 原文是「**建议**前缀」，不是封闭枚举，
   按同风格补 `ch_` `scene_` `plan_` `fs_`。塞进 `ent_` 会让「正文」与「世界设定」
   共用一个号段——写得多的人永远分不清 `ent_000400` 是物品还是第 400 章。
2. **`ent_` 必须重排。** 五个旧前缀的数字互相重叠（204 个号被多前缀占用），
   合并即撞号。966 份按（旧前缀, 数字）排序后连续重编。
   **这是唯一一处「数字会变」的地方**，其余前缀一律保留原数字。
3. **旧稿号段靠 `ch_001001+` 活下来。** 若按 `ent_` 那样归零重排，
   上一轮刚恢复的「旧稿是材料不是底稿」这个区分就没了。

`ids.MIN_WIDTH`/`MAX_WIDTH` 保持 6；`ID_RE` 改为 `^([a-z]+)_(\d{6})$`；
`LEGACY_ID_RE` 新增，用于认出未迁移的旧号而不是判成「不是 ID」——
判成不是 ID 的后果是它在查重时完全隐身。

`BAND_LIMITS` 键从 `CH` 改为 `ch`（旧稿 1001+ 的边界不变）。

### 四、代码侧

- `paths.DEFAULT_DIRS` / `CANON_KINDS` 换成 V1.0 的目录名与 17 类。
- `config/defaults.yaml` 的 `layout` 段重写（这是布局的唯一配置源）。
- 45 处硬编码目录名批量替换（10 个文件）。
- `cli/init.py` 的 `PLANNING_DIRS` / `MANUSCRIPT_DIRS` 改中文子目录；
  `LEDGER_DIRS` 删除（Ledgers 已解散）。
- `cli/read.py` 的文件名错位检查改按 `_` 取前两段拼 ID（原来按 `-` 切，误报 4 份）。

### 五、两处口径放宽（都有理由，都写进 schema 注释）

1. **`entity.id` 接受 `asset_`**：素材卡也属于 V1.0 §5.2 的统一实体基类。
2. **伏笔的 `target_reveal_window` 接受 `CH-0003` 章次占位**：正文与章卡都还没写时，
   只能写章次。一旦章卡建立，应改为 `plan_xxxxxx`。
   同理 event 的 `chapter_id` 接受 `plan_`（当前指向章卡，不是正文）。

### 六、验证

| 项 | 结果 |
|---|---|
| `validate` | **通过**，1046 份、重复 0、双链 5679 条**活文档零断链** |
| 契约校验 | 998 份**全部通过**（跳过 23 份无 frontmatter 的说明页） |
| 索引 | `ent_000086` 命中 8 处，旧号 `CHAR-000086` **0 命中** |
| canvas | 2 个，327 个文件节点路径重算 |

### 七、两处**没做完**、不要当成已完成

1. **`.git/index.lock` 与误建的 `README.md/` 目录删不掉。** 安全中心拦截了
   `rm`/`mv`/`git clean` 对该路径的所有删除操作。`README.md/` 下还有一份旧内容的
   `README.md`，需要手工 `rm -rf "01_小说项目/novel-001_jiuzhou/README.md"`。
2. **V1.0 §17 还缺 9 条 CLI**（`project list/show`、`idea create`、`entity create/show`、
   `relation create`、`event create`、`workflow` 五条、`continuity check`），
   §10 的 20 个 WF 编号工作流只做了 6 个且字段不全，§11 的十维审核还没有清单载体。
   这三块见 `docs/operations/v1_conformance.md`。

## 0.14.1 — 2026-11-22

修正 0.14.0 的两处设计错误。**这是一次数据回正，不是新功能。**
0.14.0 未推送即被本节修正，所以外部只需看这一版。

### 一、旧稿恢复独立号段：`CH-001001..001028`

0.14.0 把旧稿 28 份从 `CH-1001..1028` 压进了 `CH-000003..030`，与新正文同段。
代价当时写在变更日志里（「只能靠物理路径区分」），但它比那句话严重：

- `旧稿章节对照表` 的四列本来是「旧稿原号 | 存档文件 | 字数 | 正文占位号」。
  压平后第一、二列都变成正文号，**对照关系失去区分**——那张表的用途就是对照。
- `allocate-id` 取「最大号 + 1」。旧稿占 1001+ 后，新正文下一章会拿到
  `CH-001029`，直接落进存档段。这个错误不会报错，只会安静地把正文编号到错误的地方。

现在：旧稿 id 回到 `CH-001001`…`CH-001028`——**数值就是原来的 `CH-1001`…`CH-1028`**，
只把位宽补到六位。`id_rules.yaml` 里 `reserved_bands: CH 1000` 的语义随之恢复。
新正文 `CH-000001`/`CH-000002` 不动；`CH-000003..000030` 变成空号，按 PRD
「分配后不变、空号保留」不回收。

影响面：28 份改名、52 份文件 133 处引用（素材卡 `source_refs` 与正文双链、
看板、对照表 60 处四位旧号）。

### 二、`ids.BAND_LIMITS`：`CH` 的活文档段上限 999

号段分层不止是约定，得让发号器认。新增：

```python
BAND_LIMITS = {"CH": 999}
```

- `max_number(prefix, upper=)` 与 `_historical_max(prefix, upper=)` 只统计段内的号。
  段外的旧稿 1028 不再把新正文的游标推上去。
- 段满（下一个要发 1000）时 `allocate` 直接抛 `UsageError`，**不自动越过**。
  悄悄越过去会同时毁掉两件事：存档段的意义，以及「按号段粗略判断内容年代」的可行性。
- `allocate-id` 输出改为「段内最大号 000002，活文档段 1..999」，不再报全库最大号 1028
  ——那个数会让人以为下一个要发 1029。同时修掉两处 `:04d` 硬编码，跟随 `MIN_WIDTH`。

上限按前缀配，不是全局：`CHAR` 没有上限，1000 以上照发。

### 三、`validate` 的覆盖面补全

`cmd_validate` 原来有两个盲区，合起来让 28 条真断链藏了半天：

1. 只扫项目根（`pp.root.rglob("*.md")`），`00_System/`（看板、工作台）与
   `90_Shared_Assets/` 不在检查范围。
2. `if rel.startswith("90_Archive/"): continue` 把整个归档区跳过。

于是旧稿被改坏后，`旧稿章节对照表` 里 28 条 `[[CH-1001_引子·楔子]]` 全断，
`validate` 仍然报「4945 条双链，无断链」。**这是靠另写一个独立检查器才发现的**
（见 R-60）。

现在扫全 Vault，只排除 `99_Archive/legacy_v1/`（旧系统另一套文件）与隐藏目录。
历史区（`08_Reviews`、`90_Archive`）的断链**单独列出、不计失败**：那里保留
当时编号，指向旧名在重编号后必然断，属设计；但不能因此整目录不查。

双链覆盖 4945 → **5679 条**。同时修掉 4 条真断链：

| 位置 | 原链接 | 实际目标 |
|---|---|---|
| 看板 | `[[PLAN-VOL-000003_分章表]]` | `PLAN-VOL-000002_分章表` |
| 看板 | `[[PLAN-VOL-000003_S1细纲]]` | `PLAN-CH-000001_S1细纲` |
| 看板 | `[[旧稿章节对照表]]` | `ITEM-000002`（改名后原名不再解析） |
| 工作台 | `[[SCENE-CH-0001-003]]` | `SCENE-000003`（复合 ID 残留） |

前两条是重编号「无脑替换」的产物：`[[VOL-0001_分章表]]` 在重编号前**就是断链**
（真名一直叫 `PLAN-VOL-0002_分章表`），替换把 `VOL-0001` 变成 `PLAN-VOL-000003`，
于是链接从一个不存在的名字换到另一个不存在的名字，看起来改过了、其实还是断的。

### 四、测试

`tests/unit/test_ids.py` 追加 5 例钉住号段边界：段外占位不影响新正文、
只统计段内最大号、段满拒绝、上限按前缀配、`upper` 只改统计口径。
本机 pytest 因沙箱临时目录不可用跑不动（`tmp_path` 清理被拦），改用内联脚本验证 **6/6 通过**。

---

## 0.14.0 — 2026-11-22

ID 体系重构：全库重排号段、统一六位、遗留前缀按 PRD 收敛。

**这不是一次代码版本升级，是一次数据迁移。** 代码侧只改了 `ids.py` 的位数常量与
前缀表（约 20 行），但库里 1046 份档案的文件名、frontmatter、5818 处引用全变了。
所以它的兼容性影响比其他版本大：

> **迁移提示**：任何缓存了旧 ID 的外部脚本、书签、Obsidian canvas、Dataview 查询
> 都需要跟着改。对照表在 `~/WorkBuddy/novel-runtime/reports/id_remap_mapping.json`
> （`id_map` 是 old→new，`path_map` 是旧路径→新路径）。
> 备份在 `~/WorkBuddy/novel-runtime/backups/vault-pre-remap-20261122-013855.tar.gz`。

### 一、位数：四位 → 六位

`ids.MIN_WIDTH` 4 → 6。`CHAR-0001` → `CHAR-000001`。关闭 R-14。

schema pattern 仍是 `[0-9]{4,6}`：不是要「两种写法并存」，而是历史归档
（`99_Archive/legacy_v1`）与 `{{id}}` 模板里还是四位，收紧会让它们校验失败。
**新号一律六位。**

### 二、前缀收敛：六类遗留前缀按 PRD 迁移

| 旧 | 份数 | 新 | 依据 |
|---|---|---|---|
| `SKL` | 171 | `ITEM` | PRD 无「技能」类型 |
| `CREATURE` | 76 | `CHAR` | PRD：生物是角色的标签 |
| `WORLD` | 43 | `RULE` | 全是体系／总表／规则，无地理条目 |
| `FATE` | 42 | `CHAR` | PRD 无「命途」类型 |
| `ASSET` | 22 | `ITEM` | PRD：素材归 ITEM |
| `VOL` | 2 | `PLAN-VOL` | PRD 明确 |

代价说清楚：`ITEM` 从 336 涨到 **529**（物品＋技能＋素材同号段），
`CHAR` 从 86 涨到 **204**（人物＋生物＋命途同号段）。
语义靠 `type` 字段区分，不靠号段推断——**以后不要再从号段猜类型**。

### 三、号段重排

每个前缀从 `000001` 连续编号，组内保持原有相对顺序。
新号段：`ITEM` 529 / `CHAR` 204 / `LOC` 110 / `FAC` 102 / `RULE` 43 /
`CH` 30 / `SCENE` 11 / `EVT` 7 / `FORESHADOW` 4 / `PLAN-VOL` 4 / `PLAN-CH` 2。

**`CH` 的号段语义变了**：旧稿 28 份原在 `CH-1001`…`CH-1028`（`id_rules.yaml` 的
`reserved_bands: CH 1000`），重排后变成 `CH-000003`…`CH-000030`，与新正文
`CH-000001`/`CH-000002` 同段。旧稿与新正文现在只能靠物理路径
`90_Archive/Original_Draft/` 区分。**这是「全库连续编号」的必然代价**，
如果将来要恢复「旧稿独立号段」，得单独再排一次。

> 同日已恢复。见 0.14.1。

### 四、12 份审阅记录删掉 id

`review_note` 契约的 `required` 里没有 `id`，`novel note` 写的报告也不带 id——
这 12 份是 note 通道之前的遗留批次号（`REVIEW-CH-*`、`PREFLIGHT-CH-*`、
`MEMORY-CH-*`、`RB-*`、`AUDIT-*`、`MIGRATE-*`，以及一份中文前缀的）。
按最新设计它们不该是「产物」，删 id 并去掉文件名里的 ID 前缀。

### 五、`ids.py` 前缀表

- `LEGACY_PREFIXES` 移走六类已迁移前缀，只留 PRD 未定义且确实存在历史号的类别
  （`RELSHIP`/`REVIEW`/`PREFLIGHT`/`MEMORY`/`RB`/`INGEST`/`OUTLINE`/`LORE`/`GRAPH`）。
- `TYPE_TO_PREFIX`：`volume` → `PLAN-VOL`，`skill`→`ITEM`、`creature`→`CHAR`、
  `world`→`RULE`、`fate`→`CHAR`、`asset`→`ITEM`。
- `allocate-id --type SKL` 现在报「未知 ID 类型」——这是**期望行为**：
  它提示调用方前缀已迁移，而不是悄悄发出一个非 PRD 前缀。

### 六、验证

`validate`：1046 份带 id、**重复 0**、双链 4945 条**零断链**、通过。
两个 canvas 共 217 个文件节点**零断**。索引重建后旧 ID 检索 0 命中、新 ID 20 命中。

**未做**：`adopt --as author` 需作者执行（`AUDIT_ONLY` 设计上 Agent 不得代办）；
`release prepare` 仍未跑过，T06 安全里程碑仍未过。

## 0.13.0 — 2026-11-22

三件事：给 `08_Reviews` 一条写通道、给发布闸补上契约校验、给日志加脱敏。
前两件都不只是「加功能」——它们各自补的是一处「看似有、实际没有」的地方。

### 一、`novel note`：08_Reviews 的轻量写通道

`08_Reviews` 一直不在 `PUBLISH_TARGETS` 里，所以 `publish` 拒绝它；这本身是对的
（审阅意见不是作品内容，进正式区会被算进签字基线，把「编辑的意见」和「作者的正文」
绑在同一份哈希上）。但「不发布」被当成了「可以手写文件」，于是它成了全库唯一
一处没有契约的写入点。

新命令保留 publish 的三样、去掉它的两样：

- 保留：frontmatter 走 `review_note` schema、原子写、进 oplog
- 去掉：approve 批准链、加入签字基线

`--kind review|audit|change-log` 决定落到 `Audit_Reports/Chapter_Review`、
`Audit_Reports`、`Change_Log`。**同名即拒，绝不覆盖**——审阅记录不该被静默改写。

### 二、发布闸补契约校验（AC-11／12／13／49）

`_validate_candidate` 此前只做四样：路径、字节、可解析、正式区归属。它证明
「文件是对的」，不证明「内容能成立」。新增 `validation/contract.py` 补上另外四样：

| 检查 | 阻断码 |
|---|---|
| ID 形态（PRD §8.1） | `ID_SHAPE` |
| 本次发布内 ID 唯一 | `ID_DUP` |
| frontmatter 合契约 | `SCHEMA_INVALID` |
| 正式断言必须带来源（AC-11） | `SOURCE_MISSING` |
| 来源/双链目标必须存在（AC-12） | `SOURCE_NOT_FOUND`、`REF_BROKEN` |
| 引文与来源字节一致（AC-13） | `SOURCE_HASH_MISMATCH` |
| 被删的块仍被引用（AC-49） | `BLOCK_REMOVED_BUT_REFERENCED` |

两条让步各有理由：**只校验本次变更**（全库体检是 `validate` 的事）；**新增产物严格、
既有产物对历史形态豁免**（33 份素材库的裸路径来源补不出 hash，补就是编造）。
没有契约的 type 记 `no_contract` 而非静默跳过——没查不等于没问题。

### 三、日志脱敏（AC-47）

脱敏挂在 `Formatter` 上而不是调用点：密钥进日志有两条路，第二条是**异常堆栈**
里带出的请求体，发生在框架内部，调用点根本看不到。覆盖 Bearer / Basic / Digest、
PEM 私钥整段、常见云厂商密钥形状、URL 内嵌凭据、query string token。

规则顺序有讲究：PEM 与 Bearer 必须排在「键值对」规则之前，否则
`Authorization: Bearer eyJ...` 会先把 `Bearer` 这个词当值换掉，JWT 本体留在原地
——**看起来脱过了，比没脱更危险**。这条已写成回归用例。

### 四、Schema 契约向真实数据收敛

盘点发现 999 份可校验产物**通过 0 份**：契约是照 PRD 的理想模型写的，真实库是另一套。
T02 的 fixture 是自造的，所以测试一直绿，真实库从未接过契约。改动 5 处：

- 时间戳接受 `YYYY-MM-DD`（965 份只有日期，PRD §8.2 的 UTC 时刻从未落地）
- `sourceRef` 增加「裸路径」第五分支（33 份历史形态）
- `event` 认 `id`/`name`/`chapter_id`/`location_id`（原契约写 `event_id`/`summary`）
- `foreshadowing` 认 `status`(小写)/`planted_in`/`target_reveal_window`/`importance`
- `race_groups` 收 object（WORLD-0025 用「族属 → 种族列表」映射）

对齐后 **998/999 通过**，剩 1 份是模板（本就不该校）。

### 五、真实库数据修复

- **复合型 ID 22 个**：`SCENE-CH-0001-001`→`SCENE-0001`…（11）、
  `VOL-0001-DESIGN`→`PLAN-VOL-0001` 等（4）、`EVT-CH-0001-001`→`EVT-0001`…（7）。
  父子关系本来就有 `chapter_id` + `sequence` 承载，ID 里编码章序正是 PRD §18.1 禁止的。
- **重号 2 处**：`ASSET-0001`→`0021`、`ASSET-0002`→`0022`，只改「后到 + 零引用 +
  deprecated」的 Battles 两份；Settings 与归档保持原号。
- **断链 11 条**：`[[CH-0001]]`→`[[CH-0001_雨夜的血案|CH-0001]]`（Obsidian 按文件名匹配）。
- `validate`：重复 0、双链 4945 条零断链、退出码 0。

### 六、`validate` 不再误判代码里的双链

写作时举例说明「裸 ID 双链长什么样」必然写出 `[[CH-0001]]` 字面量。Obsidian 不解析
代码里的双链，检查器却把它算成断链——假断链比漏报更误导，它会训练人忽略这项检查。
现在跳过围栏代码块与行内代码。

### 兼容性影响

- `event` / `foreshadowing` 契约放宽：`event_id`、`summary`、`state`、`surface_meaning`
  等 PRD 字段名保留为**可选**别名，`id`/`name` 为权威。旧样本不受影响。
- 发布闸新增阻断：**已在用的发布流程会在第 6 步更严**。历史形态（裸路径来源、
  日期精度）对既有产物豁免，新产物不放行。
- 测试 658 → **701**（unit 458 / integration 183 / acceptance 34 / security 26），
  0 失败 1 跳过；selfcheck 50/50。

## 0.12.1 — 2026-11-21

修 T06 发布协议里一处重复劳动：第 8 步不再重建快照。

`execute` 的第 4 步已经把 vault 复制进 `snapshot`、第 5 步写入变更、第 7 步核对完
文件集，第 8 步却又调 `workspace.build_snapshot()`——它第一件事是
`if dest.exists(): shutil.rmtree(dest)`，把刚建好的快照整个删掉再全量复制一遍。

**兼容性影响**：无外部行为变化。

- 发布的最终内容、`canon_revision`、清单、22 步日志都一致——`build_snapshot`
  自己也是 copy→apply→commit 的完整实现，与分步版本同结果。
- 变化只有两点：少一次全量删除 + 全量复制（真库 1000+ 文件）；不再依赖删除权限
  （此前任何禁止删除的环境都会在 `rmtree` 上发布失败，哪怕前七步都已成功）。
- `workspace.build_snapshot()` 与只被它使用的 `SnapshotResult` 一并删除。
- 验证：T06 19 个用例全过；全量 658 用例 0 失败；selfcheck 50/50。

## 0.12.0 — 2026-11-21

T09「创作与审核」与 T10「记忆与完整发布闭环」。九项交付：规划契约、`write`、
`review`（风险路由 + 返修预算）、正文批准、记忆提案、源哈希校验、提案审核、
正文/记忆共同发布、依赖失效。ADR-018 记了取舍。

**兼容性影响**：四条。

1. **`release prepare` 多了一道共同发布闸**。批次里含 `03_Manuscripts/` 且没有任何
   记忆提案、也没有「本章不产记忆」声明时，准备阶段直接拒绝。要单独发正文，
   必须显式 `--skip-closure`。
2. **`ApprovalStore.latest_for()` / `status_for()` 只看内容批准**（绑了文件的、
   `target_type` 为 `artifact` 或空）。发布计划、变更、记忆提案这三类对象批准
   不再被当成「任务已批准」——否则一次 `proposal approve` 会顶掉正文批准。
3. **索引 `dependencies` 表从第 4 版起会填**，`SCHEMA_VERSION` 3 → 4，
   `BUILDER_VERSION` 0.12.0。新加 `content_hash`、`validation_status` 两列。
4. **发布计划多了一个可选字段 `closure_skipped`**（`schemas/release_plan.schema.json`）。
   只在 `true` 时输出，所以 0.11.0 及更早写下的计划重新序列化后哈希不变——
   **已批准、待执行的旧计划不会因为这升级变成「批准的是另一份计划」**。
   用 `--skip-closure` 的那些批次会把豁免写进计划，因此它也在作者批准的哈希里。

**升级后要做的事**：**跑一次 `rebuild-index`**。不跑的话 `dependencies` 表仍是空的、
伏笔表也还是旧列（0.11.0 的提示仍然适用）。

### 一、T09：草稿在结构上碰不到 Canon

`workflows/draft.py` 的目标路径由代码生成（`03_Manuscripts/Drafts/<CH>.md`），
生成后再校验一次不在 `01_Canon` 下；落盘只走 `StagingArea.stage()`。
验收「草稿不写正式 00_Canon」不是靠提醒，是靠写不出来。

写之有两道前置：`plan.check()` 说规划够格，`preflight` 说资料包 PASS。
缺一道就不写——不知道边界、没查资料就动笔，写出来的东西没法判对错。

正文与元数据分离：模型返回结构化信封，`chapter_text` 是正文，
新事实候选 / 偏离 / 未决问题走元数据。正文里不掺系统注释（PRD §14.1）。

### 二、规划契约：只派生确定性可读的两处

真库章卡是散文式的：「必须发生的剧情事实」是张时间表，「禁止发生的内容」是一串列表。
`novel plan derive` 只读这两处：

- 表格第二列 → `must_happen`；
- 以「不得／禁止／不许／不能」开头的条目 → `must_not_happen` 与 `creative_scope.forbidden`。

**`allowed` 与 `approval_required` 一律不派生**：散文里没有来源，编一个就是替作者扩权。
写回只插入缺失键，写完校验正文逐字节未变。真库实测 CH-0001 派生 10 条必须发生、
5 条禁止，每条带 `文件#L行号`。

### 三、审核：风险只能升，返修有硬预算

- 死亡／复活／核心规则变更／身份揭露／RETCON 命中即至少 HIGH，命中位置一并报出。
  单字「死」会误伤「死路／死寂／死守」，命中后过一遍搭配过滤；
- 人工与模型只能往上抬；降级请求被忽略并留说明；
- 审核角色 = 草稿角色时 `APPROVE` 直接 `SecurityError`，没有 `--force`；
- 每份 `REVISE` 算一轮，用完就不再接受审核。顺序是「先判预算、再判资格、最后写记录」。

规则检查只看形状：空正文、系统注释、正文里混进第二份文档、禁令条目里的专名。
**语义问题（人物用了还没获得的信息）机器判不了**——每次都写进 findings，不假装查过。

### 四、正文批准：不是确认弹窗

`novel approve text --task --chapter --as author` 要求：审核结论 APPROVE、
按风险该过的角色都过过、没有 OPEN BLOCKER。绑整份文件字节的 sha256。
改一个字节，批准即失效（PRD §14.5）。

### 五、T10：未发布也能提取，改了就失效

- 提取的输入是**已批准候选**，不要求正文 ACTIVE（PRD §15.1）——
  这样正文与记忆才能同批发布；
- 提案存 `source.sha256` 与 `source.staged`。正文改一个字节，`verify()` 判 `STALE`，
  `approve` 与发布都拒绝；
- 没给模型时退回确定性骨架：只有章节字数与已登记实体清单，并写明「语义条目未提取」。
  模型路径失败也一样退回，不「尽力而为地提取」出一堆看着像事实的东西。

### 六、共同发布：不许半边生效

`novel deps closure` 与 `release prepare` 对每一条正文问一句：有已批准且未失效的
记忆提案，或作者显式写的「本章不产记忆 + 理由」？都没有就阻断。

**「没有提案」与「声明不产记忆」是两件事**：前者是漏做，后者是决定。
混为一谈，闸门就退化成「没做也能过」。

### 七、依赖失效：只波及相关产物

`novel deps check` 直接读 frontmatter 的 `depends_on`：

| 情形 | 结果 |
|---|---|
| 哈希一致 | VALID |
| 哈希不一致（HARD／SOFT） | STALE／NEEDS_REVIEW |
| 没记 content_hash、目标不存在 | NEEDS_REVIEW |

传递只走一级：A 依赖 B、B 自己 STALE 时 A 记 NEEDS_REVIEW 而不是 STALE。
一次局部改动不该沿着引用链炸开——那样 STALE 会多到没人看。

### 八、修掉的真缺陷

- **规则检查把整份文件当正文**：frontmatter 的 `---` 被当成「正文里混了两份文档」，
  每一份草稿都会被判 BLOCKER。现在哈希绑整份字节、检查只看 `body`；
- **`latest_for` 把对象批准当内容批准**：`proposal approve` 排在正文批准之后，
  `release prepare` 拿到「批准覆盖 0 个文件」的记录去核对 staging。现在按
  `target_type` 与「有没有绑文件」过滤；
- **提案的源路径解析错**：`source_target` 是项目内相对路径，却拿 `vault / target` 去拼，
  `verify` 永远报「源文件已不存在」。现在优先用 `source.staged`，其次 `pp.at(target)`。

### 九、数字

- 命令行 30 → **35 条**（`plan` / `write` / `review` / `proposal` / `deps`）；
  `approve` 多了 `text` 这一种对象；
- pytest 635 → **655 passed / 1 skipped**；`scripts/selfcheck.py` 39 → **50 项**；
- 契约仍 24 类（新增的是模块而不是新 schema）；索引表 17 张，填充 16 张。

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
