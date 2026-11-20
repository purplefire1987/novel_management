# 变更日志

格式：语义化版本。每条记录写明兼容性影响与迁移提示。

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

**两字中文词（`林砚`、`周决`）是这次最容易漏的一格**：`trigram` 的窗口是三字符，
两字切不出 token，硬走 FTS 会静默返回 0 条。`test_index_schema.py` 里有一条用例
专门钉住「两字在 FTS 下命中为 0」，防止有人把它当 bug「修」成走 FTS。

另有一层：`name`/`aliases` 只在 frontmatter、**不在正文块里**。所以正文搜不到时
还要再走一次名称路径（`mode=name`，并在结果里注明「正文里没有这个词；命中的是实体名」）。
没有这一层，两字人名永远检不出来。

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
   `00_System/Dashboards/` 因为 `00_` < `10_` 永远排最前。真实库上搜「陈默」，
   前三条全是 `工作台.md` 的摘录，`CHAR-0001_陈默.md` 被挤到看不见。
   这不只是难看：看板是从档案**派生**的，让人先引用派生页等于在索引里绕了个循环。
   **修法**：结果分层（作品内容 0 > 共享资产 1 > 系统文档与生成看板 2），层内再按 rank / id。

第 1、2 条都是「单测覆盖不到」的：夹具里的 Vault 只建了 `90_Archive`，
恰好跟错误常量同名；模板文件也只有真实库才有。**这类缺陷只有拿真库跑才暴露。**

### 测试

新增 5 个文件共 **75 条**：`test_markdown`（13）、`test_index_schema`（17）、
`test_index_build`（15，含 3 条跳过）、`test_index_search`（18）、
`test_t03_cli`（12，含 4 条跳过）。全量 **386 passed / 8 skipped**。

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
