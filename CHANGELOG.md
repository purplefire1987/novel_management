# 变更日志

格式：语义化版本。每条记录写明兼容性影响与迁移提示。

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
