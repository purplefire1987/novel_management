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

V4.0 §5.3 的 16 项命令全部实现。另有九条不在 §5.3 内：三条地图命令
（`maps paths` / `import-admin` / `build`，把原先散在 Vault 里的两个生成器收进系统）、
四条契约命令（`schema list` / `schema check` / `allocate-id` / `package-hash`，T02 产物）、
两条索引命令（`rebuild-index` / `search`，T03 产物）。

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
| `novel approve` | 作者批准（绑定内容指纹）；`--reject` 驳回 | 是（仅 Runtime） |
| `novel adopt` | 登记作者手改、推进基线；`--dry-run` 只报告 | 是（仅 Runtime） |
| `novel publish` | 已批准内容转正式；旧版先归档 | 是（正式区） |
| `novel backup` | 本地备份，含清单与回读校验；`--list` 列备份 | 是（Runtime） |
| `novel restore --dry-run` | 预演恢复，列出新增/冲突/损坏 | 否 |
| `novel restore` | 执行恢复；默认不覆盖现存文件 | 是 |
| `novel maps paths` | 打印地图生成用到的全部路径 | 否 |
| `novel maps import-admin` | 从资产表重排行政区划表；默认预演 | 是（Canon） |
| `novel maps build` | 重画地图页与两张 canvas；默认预演 | 是（Canon） |

### 主链

```bash
novel task create --type draft --assignee writer            # 拿到 TASK-000001
novel stage  --task TASK-000001 --from 草稿.md \
             --to 03_Manuscripts/Drafts/CH-0001.md --as writer
novel diff   --task TASK-000001                             # 作者审阅
novel approve --task TASK-000001 --as author                # 绑定内容指纹
novel publish --task TASK-000001 --as author --confirm      # 写正式区
```

- `approve` / `publish` / `adopt` / `restore` 只接受 `--as author`，Agent 无法代办。
- 批准绑定每个文件当时的 sha256。批准后 staging 被改动 → 发布直接拒绝，需重新批准。
- 同一条任务不能发布两次；内容有更新请新建任务。
- 写入 `01_Canon/` 时 `publish` 额外要求 `--confirm-canon`。
- 发布前若目标已存在，旧版本先复制到 `90_Archive/publish_archive/<TASK-ID>/<时间戳>/`。

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
- **写入前检查目标是否被作者改过。** `diff` 会标出「staging 生成后正式文件被改过」的冲突；`approve` 遇到冲突默认拒绝。
- **越界即拒绝。** 所有路径经规范化校验，必须落在 Vault 根之内；`..` 与符号链接逃逸一律拒绝；`stage` 的目标必须在六个正式区内。
- **Agent 不写 Canon。** Agent 只能写 staging 与草稿，正式入库需作者批准；Canon 还要二次确认。
- **批准绑定内容，不是绑定任务。** 批准记录里存每个文件的 sha256，发布时逐个重算比对。
- **作者手改不可被覆盖。** `adopt` 只登记、不回滚；`publish` 前发现正式文件在 staging 之后被改过，一律判冲突。
- **`--yes` 不是万能开关。** 每个破坏性动作有自己的确认参数：`--confirm`、`--confirm-canon`、`--overwrite`。
- **操作留痕。** 所有写入记 `op_id` / 时间 / 操作者 / 输入 / 输出 / 结果，落在 `<runtime>/logs/ops.jsonl`，只记元数据不记正文。


---

## 6. 目录

```text
src/novel_system/
  cli/            命令入口与分发（read / init / index_cmd / flow / backup_cmd / maps_cmd / schema_cmd / id_cmd）
  config/         五级配置解析（含 V4.0 Vault_Config 写法兼容）
  paths.py        项目内目录解析与边界校验
  frontmatter.py  frontmatter 解析（只顶层 key: value，纯标准库）
  markdown.py     Markdown 解析与块 ID（走 PyYAML，负责嵌套结构）
  canonical.py    字节规范化与规范化 JSON 包哈希
  schema.py       JSON Schema 校验器（draft 2020-12 实用子集，零依赖）
  ids.py          稳定 ID 分配（查重、加锁、审计）
  storage/        哈希、原子写、不覆盖写、可验证复制、YAML 输出
  audit/          操作日志与操作者校验（作者专属动作在此拦截）
  tasks/          任务记录与状态流转
  approvals/      批准记录（绑定每个文件的 sha256）
  staging/        staging 落盘与 manifest
  release/        发布四道闸与旧版归档
  baseline/       作者编辑基线（adopt）
  backup/         备份、校验、恢复
  indexing/       SQLite 索引（17 表；schema/source/builder/search/locate）
  validation/ workflows/ agents/ adapters/ core/   预留扩展位（T04–T09）
config/             默认配置与配置 Schema
schemas/            22 类数据契约（JSON Schema，additionalProperties 一律 false）
templates/          Markdown/YAML 模板（author / project / entity / planning / manuscript / memory / reports）
agents/             九 Agent 定义与权限
workflows/          六个工作流定义
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
pytest                                    # 122 条
python3 scripts/selfcheck.py              # 21 项端到端冒烟，不依赖 pytest
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
