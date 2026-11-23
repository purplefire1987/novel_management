# novel-system

长篇小说管理系统 V4.0 的公共仓库。提供 Python CLI、配置 Schema、模板、九 Agent 定义与工作流规范。

> **状态：建设中。** 在作者发出「批准启用 V4.0」之前，本系统不承载正式创作，不得视为生产环境。

---

## 1. 三条边界

| 位置 | 放什么 | 不放什么 |
|---|---|---|
| **本仓库**（公共） | 代码、Schema、模板、Agent 定义、工作流、文档、虚构示例 | 任何真实小说内容、真实文件名清单、真实路径、备份 |
| **作者 Vault**（私人） | Canon、规划、正文、记忆、素材、账本 | 系统代码、第三方依赖 |
| **Runtime**（可重建） | 索引、缓存、日志、staging、临时文件 | 任何不能重建的事实 |

三者是三个独立目录，互不嵌套。Vault 不在本仓库内，本仓库也不在 Vault 内。

---

## 2. 安装

```bash
git clone <this-repo> novel-management
cd novel-management
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
novel doctor
```

可选依赖：`pip install -e ".[yaml]"` 启用 YAML 配置解析（无 PyYAML 时降级为内置简易解析）。

开发依赖：`pip install -e ".[dev]"` 后跑 `pytest`。

---

## 3. 配置

本系统不硬编码任何私人路径。所有路径在运行时从配置解析，优先级从高到低：

1. 命令行参数（`--vault`、`--runtime`）
2. 环境变量（`NOVEL_VAULT`、`NOVEL_RUNTIME`、`NOVEL_PUBLIC_REPO`）
3. 用户级配置 `~/.config/novel-system/config.yaml`
4. Vault 级配置 `<vault>/00_System/Vault_Config.yaml`
5. 本仓库 `config/defaults.yaml`（只含结构与隐私开关，不含绝对路径）

最小配置示例（写进 `~/.config/novel-system/config.yaml`）：

```yaml
paths:
  author_vault: "/absolute/path/to/author-vault"
  runtime: "/absolute/path/to/novel-runtime"
active_project:
  project_id: "novel-001"
```

`paths.public_repo` 可省略——默认取本包所在仓库根。

---

## 4. 命令

V4.0 §5.3 的 16 项命令全部实现；此外按 PRD §20.2 与后续任务补入地图、契约、索引、
时间线、审批与变更、发布、导入、Agent、创作与记忆几组命令，命令行**现为 36 条**：
三条地图命令（`maps paths` / `import-admin` / `build`）、四条契约命令
（`schema list` / `schema check` / `allocate-id` / `package-hash`，T02）、
两条索引命令（`rebuild-index` / `search`，T03）、三条时间线与认知命令
（`state` / `knowledge` / `reveal`，T04）、四条审批与变更命令
（`inspect` / `issue` / `change` / `impact`，T05）、一条发布命令
（`release`，含 10 个子命令，T06）、一条导入命令
（`import`，含 5 个子命令，T07）、两条 Agent 命令
（`preflight`、`agent` 含 3 个子命令，T08）、五条创作与记忆命令
（`plan` 含 3 个子命令、`write`、`review` 含 2 个子命令、`proposal` 含 7 个子命令、
`deps` 含 3 个子命令，T09–T10）。

| 命令 | 用途 | 写入 |
|---|---|---|
| `novel doctor` | 检查配置、依赖、SQLite/FTS 能力、路径边界、公仓/Vault 隔离、隐私开关 | 否 |
| `novel status` | 展示 Vault、项目、索引（build 与依据的 revision）、任务、等待批准项、备份、最近操作 | 否 |
| `novel validate` | 校验结构、实体 ID、双链、跨项目引用；`--strict` 把警告计为失败 | 否 |
| `novel search` | 中文/ID/别名检索；两字词走 LIKE，长词走 FTS trigram | 否 |
| `novel schema list` | 列出可校验类型与必填字段 | 否 |
| `novel schema check` | 按类型校验 JSON：缺字段/非法枚举/未知字段一律拒绝 | 否 |
| `novel allocate-id` | 分配稳定 ID；默认预演，`--confirm` 才写审计 | 是（仅 Runtime） |
| `novel package-hash` | 规范化 JSON 的 sha256；`--check` 比对期望值 | 否 |
| `novel init-author` | 初始化作者 Vault（需确认） | 是 |
| `novel init-project` | 创建小说项目骨架（需确认） | 是 |
| `novel rebuild-index` | 从 Markdown 建 SQLite 索引；`--canon-revision` 可指定 commit | 是（仅 Runtime） |
| `novel task create` | 创建任务记录 | 是（仅 Runtime） |
| `novel task inspect` | 查看任务详情、staging 产物与批准状态 | 否 |
| `novel stage` | 把 Agent 产出写入 staging | 是（仅 staging） |
| `novel diff` | 查看 staging 与正式文件的差异，标出冲突 | 否 |
| `novel state` | 某实体在某时点的客观字段值；排不出先后返回 `UNORDERED` | 否 |
| `novel knowledge` | 某人物在某时点知道/相信什么（与客观事实分表） | 否 |
| `novel reveal` | 某个真相在第几章哪一块被揭开 | 否 |
| `novel inspect` | 批准前要看七项：候选与哈希、drift、批准状态、BLOCKER、未批准 RETCON、范围、风险 | 否 |
| `novel approve <对象>` | 按对象批准（artifact / proposal / release / change），必给 `--hash` | 是（仅 Runtime） |
| `novel approve --task` | 旧入口，保留兼容；同样绑定内容指纹 | 是（仅 Runtime） |
| `novel issue` | 问题单：新建/列/解决/豁免；BLOCKER 不可豁免 | 是（仅 Runtime） |
| `novel change` | 变更请求：五类，RETCON 走独立审批 | 是（仅 Runtime） |
| `novel impact` | 生成影响报告（报告哈希即变更批准的对象） | 是（仅 Runtime） |
| `novel adopt` | 登记作者手改、推进基线；`--dry-run` 只报告 | 是（仅 Runtime） |
| `novel release prepare` | 把已批准的 staging 翻译成 ReleasePlan（不动正式区） | 是（仅 Runtime） |
| `novel release show` | 计划、批准、22 步事务日志、清单、回执 | 否 |
| `novel release execute` | 执行发布 22 步；可从中断处续 | 是（正式区 + Runtime） |
| `novel release status` | 交叉验证指针/清单/索引/日志/回执，给出判词 | 否 |
| `novel release resume` \| `abort` | 接着做 / 停掉未切换的发布 | 是（仅 Runtime） |
| `novel release repair` | 补写缺失回执（仅当指针确实指向该版） | 是（仅 Runtime） |
| `novel release rollback` | 生成回退包：新发布号，复用旧版内容与索引 | 是（仅 Runtime） |
| `novel release list` \| `unlock` | 列出发布 / 检查锁是否被持有 | 否 |
| `novel publish` | 执行一份已准备且已批准的计划 | 是（正式区） |
| `novel backup` | 本地备份，含清单与回读校验；`--list` 列备份 | 是（Runtime） |
| `novel restore --dry-run` | 预演恢复，列出新增/冲突/损坏 | 否 |
| `novel restore` | 执行恢复；默认不覆盖现存文件 | 是 |
| `novel import scan` | 扫原稿：保全原始字节、解析章节、提取候选、抽样；不写正式区 | 是（归档区 + Runtime） |
| `novel import report` | 逐维度看账（接收/保全/映射/覆盖/抽样/冲突） | 否 |
| `novel import stage` | 把某一批章节写进 staging | 是（仅 staging） |
| `novel import where` | 按 `文件名#L起-L止` 回读原文 | 否 |
| `novel import list` | 历次导入与可发布状态 | 否 |
| `novel preflight CH-0001` | 写前资料包：八级检索 + 预算账 + PASS／BLOCKED；截断逐条可见 | 是（Runtime） |
| `novel agent list` | 九角色权限矩阵、可用工具、角色卡是否在位 | 否 |
| `novel agent prompt --role` | 渲染某角色提示词；默认只看结构，`--show` 打全文 | 否 |
| `novel agent usage` | 当天调用计量台账（只记元数据，不记正文） | 否 |
| `novel plan show` \| `check` | 章卡是否够格开写：逐项列出 §10.2 字段的有无与出处 | 否 |
| `novel plan derive` | 从「必须／禁止」派生结构化字段；只派生确定性可读的两处 | 是（章卡，需 `--write`） |
| `novel write` | 主笔写草稿：前置规划与资料包，产物只进 staging | 是（仅 staging） |
| `novel review submit` \| `status` | 审核：风险路由、返修预算、高风险不许自审 | 是（仅 Runtime） |
| `novel approve text` | 正文批准：要求先过审核，绑字节哈希 | 是（仅 Runtime） |
| `novel proposal extract` \| `approve` | 记忆提案：从已批准正文提取，源哈希一变即失效 | 是（仅 Runtime） |
| `novel proposal declare-none` | 声明本章不产记忆（必须写理由） | 是（仅 Runtime） |
| `novel deps check` \| `show` \| `closure` | 依赖失效（只有相关变化才失效）与共同发布检查 | 否 |
| `novel maps paths` | 打印地图生成用到的全部路径 | 否 |
| `novel maps import-admin` | 从资产表重排行政区划表；默认预演 | 是（Canon） |
| `novel maps build` | 重画地图页与两张 canvas；默认预演 | 是（Canon） |

### 主链

```bash
novel task create --type draft --assignee writer            # 拿到 TASK-000001
novel plan  check CH-0001                                   # 规划够格吗（八项必需字段）
novel preflight CH-0001                                     # 资料包，判 PASS 才能开写
novel write --task TASK-000001 --chapter CH-0001 \
            --mock-response 模型输出.json --as writer        # 草稿只落 staging
novel diff   --task TASK-000001                             # 作者审阅
novel review submit --task TASK-000001 --chapter CH-0001 \
             --verdict approve --as editor                  # 审核（风险路由）
novel inspect TASK-000001                                   # 七项：哈希、drift、BLOCKER、风险…
novel approve artifact TASK-000001 --hash <sha256> --as author   # 绑定当前字节
novel approve text --task TASK-000001 --chapter CH-0001 --as author   # 批正文本身
novel proposal extract --task TASK-000001 --chapter CH-0001  # 从已批准正文提记忆
novel proposal approve PROP-000001 --as author
novel release prepare --task TASK-000001 --as author        # 生成发布计划（过共同发布闸）
novel approve release REL-000001 --hash <计划哈希> --as author   # 批准这份计划
novel publish --task TASK-000001 --as author --confirm      # 执行发布（22 步）
novel release status                                        # 判词应为 OK
```

- `approve` / `publish` / `adopt` / `restore` / `release execute|rollback|abort|repair`
  只接受 `--as author`，Agent 无法代办。
- 批准绑定每个文件**此刻**的 sha256。批准后 staging 被改动（哪怕一个字节）→ 发布直接拒绝，需重新批准。
- 发布前还会拦两道：目标上有 OPEN 的 `BLOCKER`、有未批准的 `RETCON` 覆盖。
- **多了两道 T09/T10 的闸**：`approve text` 要求先有 `APPROVE` 的审核结论（按风险该过的
  角色齐、无 BLOCKER），它不是确认弹窗也没有 `--force`；`release prepare` 要求每条正文的
  记忆都说清楚（提案已批准，或 `proposal declare-none --reason` 明确声明不产记忆）。
  真要单独发正文，用 `--skip-closure`，豁免会记进计划的 `closure_skipped` 并进批准哈希。
- 同一条任务不能发布两次；内容有更新请新建任务。
- 写入 `01_Canon/` 时 `publish` 额外要求 `--confirm-canon`。
- **发布不是覆盖写**：1–13 步全在隔离快照里做，第 18 步才切换正式指针。
  任何一步崩掉都能 `release status` 查、`resume` 续、`abort` 停，正式版不会被写坏（ADR-015）。
- 旧版内容先复制到 `90_Archive/publish_archive/<任务或发布号>/<时间戳>/`，再写新内容。

### 写前资料包

开工前先让系统把「这一章要守的东西」摆出来，而不是靠记性：

```bash
novel plan check CH-0001                             # 规划够格吗（八项必需字段）
novel rebuild-index                                  # 索引 3 版起填伏笔表，4 版起填依赖表
novel preflight CH-0001                              # 出资料包，最后一行是判词
novel agent list                                     # 九角色谁能做什么
novel agent prompt --role writer --task TASK-000001   # 主笔这次拿到的提示词结构
```

- 判词只有两种：`PASS` 或 `BLOCKED`。`BLOCKED` 会说清是哪一条硬约束（未回收伏笔、
  创意范围缺失、预算装不下硬约束），不写成「建议检查一下」；
- **截断是可见的**：软资料装不下会进 `coverage.truncated_results`，
  输出明确提示「有 N 处结果被截断」。所谓「没查到」与「查到但没放进去」是两回事；
- **默认不调模型**。空着 `llm` 也能跑完，结论可复现；接了模型，资料官只多做一件事
  ——把「必须发生 / 禁止发生」提炼成结构化约束，失败就退回原文照录并记一条 issue；
- `agent list` 的矩阵与人读版 `agents/common/permissions.md` 逐格相同，靠测试比对，
  不靠自觉同步。

### 项目怎么解析

写入类命令必须知道写哪部作品，规则只有两条，**不猜**：

1. `--project <id>`；
2. 否则读 `Vault_Config.yaml` 的 `active_project.project_id`。

两者都没有就返回退出码 2，哪怕 Vault 里只有一个项目。`init-project` 建第一个项目时会自动
写入 `active_project`，所以日常不必每次带 `--project`；建第二个项目后，写它就得显式指定。

不推断的理由：单项目时推断看着无害，等第二个项目建起来，同一条命令的语义就悄悄变了，
而写入类命令选错项目改的是另一部书的 Canon。

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功（`validate` 有警告但非 `--strict` 时也算成功） |
| 1 | 冲突：已存在、哈希不符、任务已发布、diff 发现冲突 |
| 2 | 用法错误：参数缺失、状态流转非法 |
| 3 | 安全拒绝：路径越界、操作者无权、需要二次确认 |
| 4 | 批准问题：缺批准、批准失效 |

详细语义见 [`docs/operations/cli_reference.md`](docs/operations/cli_reference.md)。


---

## 5. 数据安全约定

- **索引不是事实源。** 索引可随时删除重建；与 Markdown 冲突时以 Markdown 为准。`07_Staging`、`08_Reviews`、`90_Archive` 不进索引。
- **正式版只由 `<runtime>/releases/active.json` 一个指针决定。** 指针损坏就进恢复模式，**不猜**；`index/active.json` 只是「上次重建的结果」，不是正式版。
- **发布不就地改。** 22 步里 1–13 全在隔离快照上做，第 18 步才切换指针；任何一步崩掉，正式区仍是上一版（ADR-015）。
- **写入前检查目标是否被作者改过。** `diff` 会标出「staging 生成后正式文件被改过」的冲突；`approve` 遇到冲突默认拒绝。
- **越界即拒绝。** 所有路径经规范化校验，必须落在 Vault 根之内；`..` 与符号链接逃逸一律拒绝；`stage` 的目标必须在六个正式区内。
- **Agent 不写 Canon。** Agent 只能写 staging 与草稿，正式入库需作者批准；Canon 还要二次确认。
- **Agent 的权限只有一份真源。** 九角色的能力位从权限矩阵推出，工具清单不另维护；矩阵代码副本与人读版逐格比对，不一致就是测试红。这道锁是 `False` 而不是「默认」——任何角色都改不了「不写 Canon、不发布」这两句。
- **写前资料包分软硬。** 软资料装不下会截断并**明确告诉你截断了几处**；硬约束（未回收伏笔、创意范围、必须发生的事）一条都不许丢，装不下就判 `BLOCKED`，不静默压缩。
- **批准绑定内容，不是绑定任务。** 批准记录里存每个文件的 sha256，发布时逐个重算比对。
- **作者手改不可被覆盖。** `adopt` 只登记、不回滚；`publish` 前发现正式文件在 staging 之后被改过，一律判冲突。
- **`--yes` 不是万能开关。** 每个破坏性动作有自己的确认参数：`--confirm`、`--confirm-canon`、`--overwrite`。
- **操作留痕。** 所有写入记 `op_id` / 时间 / 操作者 / 输入 / 输出 / 结果，落在 `<runtime>/logs/ops.jsonl`，只记元数据不记正文。


---

## 6. 目录

```text
src/novel_management/
  cli/            命令入口与分发（read / init / index_cmd / flow / release_cmd / import_cmd / backup_cmd / maps_cmd / schema_cmd / agent_cmd / work_cmd / id_cmd / note_cmd）
  config/         五级配置解析（含 V4.0 Vault_Config 写法兼容）
  paths.py        项目内目录解析与边界校验
  version.py      版本号唯一出处（发布清单与 `--version` 都读它）
  frontmatter.py  frontmatter 解析（只顶层 key: value，纯标准库）
  markdown.py     Markdown 解析与块 ID（走 PyYAML，负责嵌套结构）
  canonical.py    字节规范化与规范化 JSON 包哈希
  schema.py       JSON Schema 校验器（draft 2020-12 实用子集，零依赖）
  ids.py          稳定 ID 分配（查重、加锁、审计）
  storage/        哈希、原子写、不覆盖写、可验证复制、YAML 输出
  audit/          操作日志与操作者校验（作者专属动作在此拦截）
  tasks/          任务记录与状态流转
  approvals/      批准记录（绑定对象类型与每个文件的 sha256）
  changes/        问题单、变更请求、影响分析（存 Runtime，不进索引）
  staging/        staging 落盘与 manifest
  reviews/        08_Reviews 轻量写通道：不使用 publish 的审阅记录（schema + 原子写 + 同名拒）
  release/        发布协议：22 步执行、断点恢复、正式指针、发布锁（ADR-015）
  baseline/       作者编辑基线（adopt）
  backup/         备份、校验、恢复
  indexing/       SQLite 索引（17 表；schema/source/builder/search/locate）
  timeline/       事件 DAG、状态区间、认知查询
  importing/      原稿导入：定位符 / 保全 / 切章 / 提取 / 抽样 / 分批入 staging（T07）
  agents/         九角色注册与权限矩阵、预算、类型化工具、提示词装配、计量、Preflight（T08）
  workflows/      创作与审核：规划契约 / 写稿（只落 staging）/ 审核与风险路由 / 正文批准（T09）
  validation/     写后闭环：记忆提案 / 依赖失效 / 共同发布（T10）；发布闸的契约校验（ID/契约/来源/引用）
  adapters/       模型适配层（当前只有 Mock）
config/             默认配置与配置 Schema
schemas/            26 类数据契约 + 1 份公共定义（JSON Schema，additionalProperties 一律 false）
templates/          Markdown/YAML 模板；其中 project/project.yaml 由 `init-project` 真读
agents/             九 Agent 角色卡与权限矩阵（人读版 permissions.md 与代码逐格比对）
workflows/          catalog.yaml（20 条工作流）+ 6 份分步细则（detail_ref 指向）
quality_gates/      质量门规则
scripts/            selfcheck.py（端到端冒烟）、privacy_scan.py（隐私扫描）
docs/               架构（含 ADR）、用户、开发、运维、WorkBuddy 文档
examples/           完全虚构的示例项目
tests/              unit / integration / security / acceptance / fixtures
.github/            CI 与 PR、Issue 模板
```

---

## 7. 测试与检查

```bash
pytest                                    # 701 用例：700 passed / 1 skipped
python3 scripts/selfcheck.py              # 50 项端到端冒烟，不依赖 pytest
python3 scripts/privacy_scan.py --repo .  # 公仓隐私扫描
```

三条互相独立：`pytest` 覆盖单元与拒绝路径，`selfcheck` 在没有 pytest 的机器上也能跑通全链路，
`privacy_scan` 只看「有没有私人内容混进公仓」。CI 上三个都会跑。

测试只用 `examples/sample_novel` 与临时目录中的虚构数据。`tests/conftest.py` 会把用户级配置和
公仓路径都指到临时目录——否则在配了真实 Vault 的机器上跑测试，就会去动作者的作品。

---

## 8. 文档索引

| 文档 | 什么时候看 |
|---|---|
| [上手指南](docs/user/getting_started.md) | 第一次装系统 |
| [日常创作](docs/user/daily_workflow.md) | 每天怎么用：六个动作 |
| [架构总览](docs/architecture/overview.md) | 想改代码之前 |
| [ADR 列表](docs/architecture/adr/README.md) | 想推翻某个设计之前 |
| [命令参考](docs/operations/cli_reference.md) | 记不清某个参数或退出码 |
| [备份与恢复](docs/operations/backup_restore.md) | 动手做破坏性操作之前 |
| [已知风险](docs/operations/known_risks.md) | 想知道哪里还没做完 |
| [旧系统 30 条命令的去向](docs/operations/legacy_commands.md) | 想知道哪条还该补回来 |
| [代码结构](docs/developer/code_layout.md) · [测试约定](docs/developer/testing.md) | 加功能之前 |
| [贡献指南](CONTRIBUTING.md) · [安全策略](SECURITY.md) | 提交之前 |

---

## 9. 许可

**GNU General Public License v3.0（`GPL-3.0-only`）**，全文见 [LICENSE](LICENSE)。

选它的理由与未采纳的方案见 [ADR-010](docs/architecture/adr/ADR-010-license-and-governance.md)。
一句话：代码可自由使用与修改，但分发衍生作品时必须以同一许可开源。

许可只覆盖**代码与模板**。任何作品内容都不在本仓库内，归作者所有。
