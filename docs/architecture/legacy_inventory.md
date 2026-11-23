# 历史包袱与架构演变审计（2026-11-23）

> 目的：回答三个问题——**哪些设计是有意为之、哪些是历史遗留、哪些是为解决已经不存在的问题而引入的**。
> 方法：读 git 全历史 + 18 篇 ADR + CHANGELOG 1590 行 + 真库现数 + 静态扫描。所有数字现取，不引用旧小结。
> 结论按「能不能删」分档，不按「看起来旧不旧」分。

---

## 零、先说一件影响判断方式的事：这个仓库没有 PR

| 事实 | 现数 |
|---|---|
| 提交总数 | **18 次**（2026-11-20 04:28 → 2026-11-22 16:46） |
| 分支 | 只有 `main`，无 feature 分支 |
| tag | **0 个** |
| merge 提交 | 1 个，且是 `On main: temp-revert-cli-fix`（stash 产生的，不是真实合并） |
| 遗留 stash | **1 条**，2026-11-20 的 `temp-revert-cli-fix`，改的是 `cli/__init__.py` +54/−34 |
| 未提交改动 | **66 个已修改 + 90 个未跟踪**；`0.15.x` 全部工作、`workflows/catalog.yaml`、`workflows/registry.py`、6 个 `cli/*_cmd.py` **从未进过版本库** |

**所以「关键 PR」这件事不存在**，架构演变只能从 18 条提交信息 + ADR + CHANGELOG 复原。
这不是流程洁癖问题，它直接解释了下文一半的遗留：**没有 review 环节去比对「文档说的」和「代码做的」**，
文档承诺因此可以长期悬空（`core/`、「templates/project 被 init 读取」都是这么来的）。

演变节奏是「一个里程碑一次提交」：T01→T03→T04/05→T06→T07→T08→T09/10→0.13→0.14。
每次提交都自带 CHANGELOG 小节与 ADR，**决策理由写得比代码还全**——这是这个库最值钱的部分，
也是下文判定「有意为之」时最可靠的依据。

---

## 一、五个时代：每个设计是被什么压力逼出来的

| 时代 | 时间 | 提交 | 当时的压力 | 留下的东西 |
|---|---|---|---|---|
| **E0 移植期** | 11-20 04:28 | `3979b25` 骨架、`0203ad8` 地图重编码 | 从旧 Vault 搬一个能跑的骨架出来 | `maps/`、`examples/`、`templates/`、九 Agent 卡 |
| **E1 索引期** | 11-20 | T01–T03（0.4.2–0.6.0） | 中文检索不可用、路径写死 | `indexing/`、`ids.py`、`schemas/`、`frontmatter.py` |
| **E2 关口期** | 11-21 | T04–T08（0.7.0–0.11.0） | 「批的是 A、发的是 B」；旧稿导入要保原字节 | `timeline/`、`approvals/`、`changes/`、`release/`、`importing/`、`agents/` |
| **E3 闭环期** | 11-21 | T09/T10 + 0.13.0 | 草稿能绕过审核进基线；正文与记忆不同步 | `workflows/`、`validation/`、`reviews/`、`tasks/` |
| **E4 正名期** | 11-22 | 0.14.0/0.14.1 | 四套 ID 方言并存 | 六位小写 ID、`LEGACY_PREFIXES`、`BAND_LIMITS` |
| **E5 V1.1 对齐** | 11-23（未提交） | — | 新规范换了颗粒度 | Canon 六值迁移、`v1_1_delta.md` |

三个转折点值得记住，因为它们决定了哪些东西「当时是对的、现在多余」：

1. **0.6.0 把索引从 `index.json` 换成 SQLite 多 build 指针**（ADR-012 部分取代 ADR-003）。
   `index.json` 相关代码已不存在，但「索引可删」这条结论被保留下来，且**仍然正确**。
2. **0.14.0 全库重编号**。这一刀切得最狠，也留下了最多的「只迁了一半」——
   下文 C-1、C-2、D-1 全部是这一刀的余波。
3. **V1.1 不是 V1.0 的超集**（`v1_1_delta.md`）。所以现存代码里同时踩着三套坐标：
   V4.0 的 T01–T12、V1.0 的 §9/§11、V1.1 的 P0–P8。**没有任何一处文档把三套编号映到一张表上。**

---

## 二、有意为之：看着奇怪，但拆了会出事

这一组全部有 ADR 或 CHANGELOG 的显式理由，且理由在 2026-11-23 仍然成立。

| # | 设计 | 为什么不能按「常识」改 | 依据 |
|---|---|---|---|
| A1 | 索引每次建新库、只切 `active.json` | 原地重建会在失败时留下半个库，且与并发读冲突 | ADR-012 |
| A2 | 批准绑 sha256，不绑任务 ID | 「批 A 发 B」在哈希层就不成立；这是全系统的信任根 | ADR-014 |
| A3 | 发布 22 步顺序写在一个函数里 | 第 4/5/7 步不可重排（`build_snapshot` 先 rmtree 再复制）。顺序即语义 | ADR-015 |
| A4 | `ensure_can_write_canon()` 永远抛 | 权限矩阵里两道锁恒 False，任何角色改不了。不是默认值，是常量 | ADR-017 |
| A5 | 三档预算里「硬约束排在软资料之前」 | 反了就会先装满素材、把伏笔挤掉 | ADR-017 |
| A6 | 截断要记两处（`Budget.truncations` + `coverage.truncated_results`） | 只记一处等于没做验收 | T08 |
| A7 | 事件 DAG 不按 `sequence` 连边 | `sequence` 语义未定义（叙述序 or 故事序），连边等于替作者判定倒叙 | ADR-013 |
| A8 | 客观状态查询不 select `knowledge` 表 | 事实与认知物理分表，不是过滤条件 | ADR-013 |
| A9 | ID 发号看「现存最大号 + 审计历史最大号」 | 删档不补号；只扫现存会把删掉的号再发一次 | §9.2 |
| A10 | `BAND_LIMITS["ch"]=999` | 旧稿占 1001–1028。没这条上限，新正文下一章拿到 `ch_001029` | 0.14.1 |
| A11 | `is_compound_id()` 判定宽（连字符 ≥2 即算） | 它防的是「隐身」，不是「存在」；复合 ID 已清零仍留宽判据 | 0.14.0 |
| A12 | `--confirm` / `--force` / `--dry-run` 三种参数并存 | 语义分别是「落盘」「越过拒绝」「只看不写」，不是不一致 | onboarding_audit §四.5 |
| A13 | 六维/十维审核清单在 V1.1 里失去原文依据却保留 | 与 V1.1 WF-14 的 15 步不冲突，删了会让 `dimensions.py` 空转 | v1_1_delta §五.1 |

**A10 与 A11 是同一类**：数据面已经干净（0 个复合 ID、0 个旧号文件），但守卫必须留着——
因为下一次导入旧稿会重新产生这类 ID。**这类「空转的守卫」不算包袱**，见第四节判定口径。

---

## 三、历史遗留：名字、位置、承诺与实现脱节

这一组不影响运行，影响的是「读代码的人相信了错误的地图」。全部在 `onboarding_audit.md` 有记录，
此处补上**为什么它一直没被修**——因为 18 次提交里没有一次做过「改名/搬文件」类的整理提交。

| # | 遗留 | 现数/证据 | 代价 |
|---|---|---|---|
| B1 | `cli/read.py` 里没有 `read` 命令 | 553 行，装的是 `doctor`/`status`/`validate`/`diff` | 找「读文件」实现会绕很久 |
| B2 | `validation/proposals.py` 是提取业务，不是校验 | 559 行；docstring 首行「从已批准正文提取」 | 包名与内容脱钩；`validation/` 四项里两项真校验 |
| B3 | `frontmatter.py` 自称「唯一解析实现」 | 69 行，只解顶层键值；`markdown.load_meta` 是第二条路（走 PyYAML） | 改一处以为影响全库，实际 `frontmatter` 3 处调用 vs `markdown` 10 处 |
| B4 | `core/` 空包 | `__init__.py` **1 行**，全库引用 **1 处**（自己） | 文档说它是「T11 后扩展位」，实际是空气 |
| B5 | `docs/developer/code_layout.md:293` 说目录名写进 `templates/project/project.yaml` | `init.py` 里 `templates` 出现 **0 次**，骨架是内嵌 `PROJECT_META` 常量（`init.py:54`） | 改模板文件不会有任何效果 |
| B6 | `examples/sample_novel/` 是 0.4.x 化石 | `CHAR-0001.md` **没有 frontmatter `---`**，`markdown.load_meta` 直接抛 `ParseError`；ID 是 V4.0 形态、`status: confirmed` 是旧枚举；测试对它 **0 引用** | 「示例」跑不通，新人第一课就撞墙 |
| B7 | `templates/` 7 份模板 | 代码 **0 引用**、测试 **0 引用**；README:272 仍宣称它是资产 | 作者以为改模板能影响生成物 |
| B8 | `docs/operations/cli_reference.md` 停在 0.13.0 | 收录 36 条，实现 **43 条**；缺 `idea` `entity` `relation` `event` `continuity` `workflow` `project` 七条 | 新命令无文档 |
| B9 | `workflows/catalog.yaml` 里 20 条已编号，`simulation` 仍是旧 id 形态 | 其余 19 条 `WF-xx`，它单列 `side_workflows`；这是**有意为之**（旁支不是生产步骤） | 不算包袱，但要在 catalog 顶部注明，否则会被后人「顺手编号」 |
| B10 | `config/defaults.yaml` 两个死键 | `config_version`、`runtime_subdirs` 在 `src/` 里 **0 引用** | 极小；但会让人以为改了生效 |

**B9 特殊**：它是唯一一个「看起来像遗留、其实是有意设计」的条目，列在这里是为了**防止别人修它**。

---

## 四、为解决已经不存在的问题而引入的

判定口径：**当初要挡的那个风险，现在在数据面上已经消失，且不会周期性回来**。
与第三节的区别：第三节是「说错了」，这一节是「当年对的，现在没对象了」。

| # | 东西 | 当年解决什么 | 为什么问题已不存在 | 现数证据 |
|---|---|---|---|---|
| **C1** | `maps/` 整个包（1835 行） | 旧 Vault 有 `00_System/scripts/build_jiuzhou_map.py`，要重画 Obsidian 地图页与两张 canvas | **它现在已坏，且会破坏数据**（见下） | `jiuzhou.py` 6 处 `LOC-*.md` glob；库里 `LOC-*` **0 个**、`ent_*` **966 个**；预演报 `! 缺州域档案：['中州','凉州',…11 个]` + `! 待建档治所 71` |
| **C2** | `maps/jiuzhou.py:normalize_aliases()` | 修早期生成留下的 `aliases:` 尾空格 | 那段早期生成本身已经不存在；且 glob `LOC-*.md` 永远匹配 0 个 | 函数体 `LOC_DIR.glob("LOC-*.md")`，且用 `path.write_text` 绕过 `write_atomic` |
| **C3** | `tasks` 的 `_LEGACY_TASK_RE` / `LEGACY_STATUS` | 兼容改名前的 `TASK-000001` 旧目录与 V4.0 六态 | **真实库一个旧任务都没有**，兼容层无数据可兼容 | `<runtime>/tasks` **0 条**；`<runtime>/staging` **0**；`<runtime>/preflight` **0** |
| **C4** | `ids.LEGACY_PREFIXES`（11 个旧前缀映射） | 让 `validate` 能说「这几份还是旧号」 | 全库旧号 **0 个**（只有 `99_Archive` 冻结区，且不进正式区扫描） | 文件名 `*CH-0*`/`*CHAR-0*`/`*ITEM-0*`/`*EVT-0*` 合计 **0** |
| **C5** | 遗留 stash `temp-revert-cli-fix` | 0.5.1 修 argparse 时临时回滚用 | `cli/__init__.py` 此后重写了十几轮，stash 里那 54/34 行已无对应上下文 | `git stash list` 1 条，日期 11-20 |

### C1 必须单独说清楚：它现在是「能跑的破坏性命令」

`maps build --maps-only` 预演结果（真库，未落盘）：

```
! 缺州域档案：['中州','凉州','海州','扬州','越州','湖州','陵州','戎州','云州','海外十五州','三十六洞天，七十二奇地']
✓ 方位图 40 节点 / 39 连线
! 待建档治所 71：[…71 项…]
· 预演：将改 3 个文件
```

对比 CHANGELOG 记录的 0.4.1 验收基线：**节点 111 / 边 110**。
现在只剩 40/39——因为 `loc_files()` 按 `LOC-*.md` 扫，扫不到任何文件，71 个治所全部没有 `loc_id`。
**一旦有人加 `--confirm`，会把现有的 111 节点地图降级成 40 节点。** 这是全部发现里唯一一条
「已经在损坏边缘」的条目。

同时它违反三条现存约束：
- **公仓零设定**：包名与文件名仍是 `jiuzhou`，V1.0/V1.1 都不要求地图功能（两版规范里「地图」出现 **0 次**）；
- **库函数不接收 argv**：`jiuzhou.main()` 手工嗅探 `sys.argv` 里的 `--maps-only`/`--confirm`，并用 `global DRY_RUN`；
- **写入必须走 `write_atomic`**：`normalize_aliases()` 直接 `path.write_text`。

---

## 五、可安全移除：三档，按「能不能当天验完」分

### 档 1 — 零行为改动，当天可验（建议立刻做）

| # | 动作 | 验收判据 | 风险 |
|---|---|---|---|
| 1 | 删 `src/novel_management/core/` | `grep -r "novel_management.core"` 为 0；`novel --help` 输出逐字不变 | 无（引用仅自身） |
| 2 | 删仓库根 **79 个** 8 位垃圾目录 + `maps/hy8j74o9`、`maps/rs67h2x8` | 删前后 `git status --short` 只少 `??`；`pytest`（能跑时）不变 | 无（均为沙箱 tempfile 回退产物，每个含 1 个 4 字节文件） |
| 3 | 删 `examples/sample_novel/` 或整体重写为现行契约 | 若重写：`markdown.load_meta` 不抛错 + `schema.check("entity")` 0 错 | 无（0 引用；当前版本连解析都失败） |
| 4 | 删 `config/defaults.yaml` 的 `config_version`、`runtime_subdirs` | `PYTHONPATH=src python3 -m novel_management doctor` 不变 | 极低 |
| 5 | 改 `docs/developer/code_layout.md:293` 与 `README.md:272` 的模板描述 | 文档与 `init.py` 实际行为一致 | 无 |
| 6 | 补 `cli_reference.md` 缺的 7 条命令 | 43 条与实现一一对应 | 无 |

档 1 合计：删 **2 个包（1 个空包 + 示例）+ 81 个垃圾目录 + 2 个死键**，改 2 处文档。
**源码行数减少约 218 行（`core` 1 + maps 内 8 行垃圾不算行数的话以文件计）**，不值得吹数字——
它的价值是让 `find`/`grep`/`git status` 干净。

### 档 2 — 改结构，需一次完整回归

| # | 动作 | 判据 | 为什么值得 |
|---|---|---|---|
| 7 | `maps` 从公仓剥离为**私人脚本**（移出 `src/`，或整体删除后把生成逻辑留在 Vault 侧） | 剥离前先 `maps build --maps-only` 存三份产物哈希作基线；剥离后 `novel --help` 少一条命令，无其他变化 | 1835 行、最大单文件、公仓带作品名、且已坏。**V1.1 §25.1「降低私人代码优先」正对着它** |
| 8 | `cli/read.py` → `cli/inspect_cmd.py` | `git mv` + 两处 import；37 份 `--help` 输出 diff 为 0 | 消除 B1 |
| 9 | `validation/proposals.py` → `workflows/proposals.py` | 移动后 `validation/` 只剩 `closure`/`dependencies`/`contract` | 消除 B2；依赖方向本来就有（proposals 依赖 workflows） |
| 10 | `frontmatter.py` docstring 改为「顶层键值轻量解析」 | 一句话 | 消除 B3 的误导 |
| 11 | 删 `tasks` 的 `_LEGACY_TASK_RE` / `LEGACY_STATUS` | `grep` 后无 `TASK-\d{6}` 形态残留；`task` 命令建/查不变 | C3：无数据可兼容。**但若计划导入旧 runtime，则保留** |
| 12 | 删 `ids.LEGACY_PREFIXES` 的**映射表**，保留 `LEGACY_ID_RE` 的**拒绝能力** | `allocate-id --type CHAR` 仍报错（改用通用提示） | C4：0 旧号；但「拒绝发旧号」这条不能丢 |

档 2 里 **7 与 8–12 的性质不同**：7 是「搬子系统」，8–12 是「正名」。可分开做。

### 档 3 — 需要你裁定，不建议我擅自动

| # | 议题 | 选项 |
|---|---|---|
| 13 | `maps` 的最终去向 | ① 整体删除（承认地图页是静态资产，以后手改）② 移出公仓留在 Vault 私有脚本 ③ 修好它（把 6 处 `LOC-*` 改成 `ent_*` + 接 `type: location` 过滤） |
| 14 | `examples/` 要不要保留 | ① 删 ② 重写成能过 `validate` 的最小项目（会变成测试夹具，一举两得） |
| 15 | `templates/` 7 份模板 | ① 删（`init.py` 不读它）② 让 `init.py` 真读它（把 `PROJECT_META` 搬进模板） |

我倾向：13 选 ②（保留能力、退出主线）、14 选 ②（示例顺带变成夹具）、15 选 ①（宁可删掉也不留「改了没用」的文件）。

---

## 六、明确不要动（防止上面被过度执行）

1. **`release/engine.py` 22 步不要拆成模块。** 顺序即语义，拆散后没人能再确认顺序对。
   要拆只能在同函数内提私有方法。
2. **`known_risks.md` 744 行 / 60 条不是负债。** 它是唯一一份「为什么当初不那样做」的记录；
   5 条已关闭条目留着是给「这个坑会不会回来」留证据。
3. **ID 空号（1007 个）是设计。** 回收会毁掉「按号段判断内容年代」。
4. **`BAND_LIMITS`、`is_compound_id` 的宽判据、`LEGACY_ID_RE` 的拒绝能力**：数据面干净不等于守卫可以拆。
5. **`adapters/mock.py`**：不是遗留。它是「测试不许真调模型」这条硬要求的唯一实现（125 行 / 2 文件）。
6. **`workflows/` 下 6 个 `workflow.yaml` 不是 V4.0 残留**：它们全部被 `catalog.yaml` 的 `detail_ref`
   引用（WF-03→legacy_ingestion、WF-05→canon_change、WF-13→manuscript_production、
   WF-16→memory_update、WF-18→release、旁支→simulation）。**两级结构的细则层，删了 20 条工作流就只剩标题。**
7. **`catalog.yaml` 里 `simulation` 的旧 id 形态是有意的**：旁支不是生产步骤，不该占 `WF-2x` 号。

---

## 七、真正的「欠账」不是包袱，别混着算

下面这些看着像遗留，其实是**没做完**（`v1_1_delta.md` 的 D1–D6），不能当包袱删：

| 缺口 | 现数 | 批次 |
|---|---|---|
| 24 个新数据契约（伏笔 32 字段、悬念 27、记忆 43…） | `schemas/` 26 份，缺 20 余类 | D2 |
| 目录细化（记忆 14 / 素材 9 / 伏笔 4） | `05_记忆与连续性` 5 份平铺；`06` 有 6 个英文遗产目录 | D1 |
| 版本服务 11 功能 + `VER-` | 只有 `release/` 22 步；`release status` 判 `NO_RELEASE` | D3 |
| 索引 55 张表 | 17 张，`issues` 建而不填 | D6 |
| A–T 二十验收 | 578 个 test，但 M/N/O/P/Q/R/S/T 八项无用例 | D6 |
| 三套阶段编号（T01–T12 / P0–P8 / §23 A–T） | 无映射表 | 建议并入 D0 |

---

## 八、一句话结论

这个库的**设计质量高、演进记录完整**（18 篇 ADR + 1590 行 CHANGELOG + 60 条风险登记，罕见），
真正的包袱只有三处，且都集中在**「移植期搬进来、后来改不动」的边界上**：

- **一个坏掉且会破坏数据的子系统**：`maps`（1835 行，6 处旧前缀，预演已报 71 个治所缺失）；
- **一层无数据可兼容的兼容代码**：`tasks` 旧号/旧态 + `ids` 旧前缀映射（runtime 0 任务、库里 0 旧号）；
- **一批说错了的地图**：`core/`、`examples/`、`templates/`、`read.py`、`proposals.py`、`cli_reference.md`。

档 1 六条动作零风险、当天可验；档 2 五条正名 + 一条剥离；档 3 三件要你定。

---

## 九、处置记录（2026-11-23，0.15.4 → 0.15.5）

作者当日裁定并已执行。**本节随代码走，不留在「待办」状态**——否则这份清单自己会变成
它批评的那种「说错了的地图」。

### 已处置：档 3 的三项裁定

| # | 裁定 | 执行结果 |
|---|---|---|
| 13 | `maps` **修好**（不是删、不是剥离） | 见下；真库已落盘，层级树回到 111 节点 |
| 14 | `examples` **重写**为可校验的最小项目 | 2 实体 + 1 事件 + 1 章节，四份全过 `schema.check` |
| 15 | 让 `init.py` **真读** `templates/project/project.yaml` | 产物与旧实现逐字节一致；新增 `templates/README.md` |

### 已处置：档 1 六条

1. 删 `core/`（1 行空包、仅自引用）✅
2. 删 31 个沙箱垃圾文件（顶层 29 + `maps/` 2）✅ 顶层从近百项降到 18 项
3. `examples` 重写（见上）✅
4. `defaults.yaml` 两个死键删除，并**同步两份分叉的副本**✅
5. `code_layout.md` 与 `README.md` 的模板/数量描述改正 ✅
6. `cli_reference.md` 补 7 条命令、35→43、末尾 4 行路径 ✅

### 修 `maps` 时的额外发现（原报告未覆盖）

- **`name_of` 才是主因**：`stem.split("_", 1)[1]` 对 `ent_000906_怒涛府` 得 `000906_怒涛府`。
  只改 glob 不改这里，症状会从「40 节点」变成「40 节点且不报错」——更难查。
- **`admin.py` 也会写坏 ID**：`id: WORLD-0040` 是生成模板里的硬编码，重跑 `import-admin`
  会把 `ent_000963` 改回旧形态。
- **契约在拒绝一切合法章节**：`chapter.schema.json` 的 `chapter_id` 仍是 `^CH-[0-9]{4}$`，
  `memory.schema.json` 同。已按 `foreshadowing` 的「新形态 + 历史豁免」写法修正。

### 已处置：档 2 的 8–12 条（0.15.5）

判据统一为**CLI 表面零变化**：37 份 `--help` 改前改后逐字节 diff 为 0。

| # | 动作 | 执行结果 |
|---|---|---|
| 8 | `cli/read.py` → `cli/inspect_cmd.py` | `git mv` + 注册处 5 行；docstring 写明与 `novel inspect`（在 `approval_cmd.py`）的分工 |
| 9 | `validation/proposals.py` → `workflows/proposals.py` | 依赖方向掰正为 `validation → workflows`（反向 0）；`validation/` 只剩三项真校验 |
| 10 | `frontmatter.py` docstring | 不再自称「唯一实现」，改为与 `markdown.load_meta` 的**能力对照表** |
| 11 | 删 `tasks` 的 `_LEGACY_TASK_RE` / `LEGACY_STATUS` 等四件 | 顺带删 `normalize_task_id` / `normalize_status`；契约 `task_id` 同步收紧为 `^task_[0-9]{6}$` |
| 12 | `ids.LEGACY_PREFIXES` 去掉映射内容 | 改成 `frozenset` 纯名字集合，**拒绝能力保留**；`FAC`/`LOC`/`PLAN-VOL` 仍报错 |

**更正本报告的一条判据。** #12 原来写的是「`allocate-id --type CHAR` 仍报错」——实测
**不成立**：`CHAR` 在 `TYPE_TO_PREFIX` 里映射到 `ent`，`resolve_prefix` 先命中它，
是**故意返回 `ent`**（V1.0 的设计：类型归 `type` 字段）。真正走废弃分支的是
`FAC`／`LOC`／`PLAN-VOL` 这些不在映射表里的。判据写错比不写更糟：照着它验，
会以为改坏了。

### 仍未处置（不在本轮范围）

| 项 | 状态 | 归属 |
|---|---|---|
| 档 2 第 7 条：`maps` 从公仓剥离为私人脚本 | 未动 | 搬子系统，与「正名」性质不同，需单独一轮 |
| 仓库根 `config/defaults.yaml` 从未被读取 | 仅消除分叉 | 要不要删这个副本，另定 |
| `tests/unit` 121 项红（V4.0 假设） | 未动 | D6 |
| 6 个英文遗产目录（`06_素材与研究` 等） | 未动 | D1 |
| 提示词文件名与注册名不一致（七份） | 未动 | 需先清 Vault 里的陈旧 `index.lock` |
