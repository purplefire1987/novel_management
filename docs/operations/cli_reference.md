# CLI 参考

对应 V4.0 §5.3 的 16 项命令，另加 3 条地图命令（§8），共 19 条。全部命令支持 `--vault` / `--runtime` / `--project` / `--log-level`，位置可放在子命令之前或之后。

```bash
novel --vault ~/vault status       # 等价
novel status --vault ~/vault
```

未给 `--project` 时：配置里有 `active_project.project_id` 就用它；没有则报错并列出候选，**绝不按「只有一个项目」去推断**。
`novel init-project` 建第一个项目时会自动写入 `active_project`，所以日常不必每次带 `--project`。

### 运行日志

每次命令跑完都会在 `<runtime>/logs/novel.log` 留一对记录：

```
2026-11-20 14:14:52 INFO  novel.cli | start cmd=status project=novel-001 vault=/path/to/vault
2026-11-20 14:14:52 INFO  novel.cli | done cmd=status rc=0 elapsed=0.015s
```

- `--log-level` 取 `DEBUG` / `INFO`（默认）/ `WARNING` / `ERROR` / `OFF`。
- 想在终端同时看见，设 `NOVEL_LOG_CONSOLE=1`；走 **stderr**，不污染 stdout。
- 日志写不进去（Runtime 只读、盘满、路径被普通文件占住）会静默降级，**不会让命令失败**。
- 这是运行日志，和 `audit/` 的审计日志（`ops.jsonl`，记谁改了哪个正式文件）是两回事：前者可丢，后者是证据。

---

## 1. 只读命令

### `novel doctor`

检查项：Python 版本、PyYAML、Vault 与 Runtime 是否存在、Runtime 是否被放进 Vault、公仓与 Vault 是否互相包含、四个隐私开关是否为 false。

Runtime 存在时顺带补建 `index/logs/tasks/approvals/backups` 五个子目录。

返回 1 表示有硬问题；隐私开关被打开、Runtime 在 Vault 内、公仓与 Vault 互相包含都算硬问题。

### `novel status`

Vault 路径、项目列表（含 Markdown 计数）、索引大小、任务总数与未结数、等待作者批准的任务、备份数量与最近一次是否 verified、最近 5 条操作。

### `novel validate [--strict]`

四组检查：

1. **结构**：`00_System/`、`10_Projects/` 是否存在。
2. **项目元数据**：`00_Project/project.yaml` 是否存在、`project_id` 是否为空或重复、`project_subdirs` 是否齐全（缺子目录算警告）。
3. **路径越界**：Vault 与项目目录是否都在 Vault 内。
4. **实体与链接**（逐项目）：
   - **Canon 六类 + `04_Story_Assets`** 下所有带 `id` 的文件的 ID 是否重复（重复算失败）。
     素材虽然按 `Battles/` `Settings/` 等分类分目录存放，ID 却是「全库连续编号，不按分类分段」，
     因此查重必须**跨子目录一起看**；模板里的 `id: {{id}}` 是占位符，不算身份、不参与查重。
   - `id` 与文件名前缀是否错位（如 `CHAR-0001_示例名.md` 里写 `id: LOC-0001`），算警告；
   - `[[双链]]` 是否可解析。解析范围是全 Vault 的文件名（Obsidian 语义），支持 `.md` / `.canvas` / `.base`，剥离 `#标题`；
   - 指向别项目的 `[[10_Projects/other/...]]` 算失败。

`--strict` 把断链与缺子目录等警告升级为失败，适合提交前跑。

### `novel task inspect <TASK-ID> [--json]`

任务字段、staging 产物清单（目标路径 + sha256 + 时间）、最近一次批准记录。

### `novel diff --task <TASK-ID> [--path <目标>] [--full]`

逐条比对 staging 与正式文件，输出 `difflib` 统一 diff。判定四种状态：

| 状态 | 含义 |
|---|---|
| `新增` | 正式文件不存在 |
| `改动` | 将替换正式文件 |
| `一致` | 与正式文件内容相同 |
| `冲突` | staging 生成后正式文件被改过（通常是作者手改） |

有冲突时返回 1。默认每个文件最多显示 120 行差异，`--full` 不截断。

---

## 2. 初始化

### `novel init-author [--author-id ID] --confirm`

目标目录非空则拒绝。建 `00_System/{Dashboards,Policies}`、`10_Projects`、`90_Shared_Assets`、`99_Archive`，写 `Vault_Config.yaml`、`Author_Profile.md`、`Project_Registry.md`，并创建 Runtime 的九个运行态子目录。

### `novel init-project --id <ID> --name <名字> --confirm`

`project_id` 只允许小写字母、数字、连字符。建 §4.2 的完整目录树，含 `07_Staging/` 九个分栏与 `08_Reviews/{editorial,author}`。写 `00_Project/project.yaml`，并在 `Project_Registry.md` 追加一行。

---

## 3. 索引

### `novel rebuild-index`

扫 Vault 内全部 Markdown（跳过隐藏目录、`07_Staging`、`08_Reviews`、`90_Archive`），写 `<runtime>/index/index.json`。

记录 `schema_version`、`built_at`、条目数、带 id 数、重复 id 列表。模板里的 `id: {{id}}` 视为无 id，不参与重复统计。

索引可随时删除重建，不反向修改任何事实源。

---

## 4. 任务

### `novel task create --type <类型> [--assignee <agent>] [--mode <模式>] [--scope <前缀>] [--input <文件>] [--note <备注>] [--as <操作者>]`

类型：`outline` `preflight` `draft` `revision` `review` `memory_update` `ingest` `simulation` `release`。
模式：`read_only` `staging` `draft` `proposal`。

首行输出 `TASK-000001`，便于脚本取用。`read_only` 任务不允许 `stage`。

状态流转固定：`pending → in_progress → {blocked, awaiting_author, done, cancelled}`；跳步被拒绝。

---

## 5. 提案与批准

### `novel stage --task <ID> --from <文件|-> --to <项目内正式路径> [--note] [--replace] [--as]`

- `--from -` 从 stdin 读。
- 产物落 `07_Staging/<TASK-ID>/<分栏>/<目标路径>`，分栏由任务类型决定（draft/revision→drafts，review→reviews，memory_update→memory，其余见 `staging/AREA_BY_TASK`）。
- `--to` 必须落在六个正式区之一（Canon / Planning / Manuscripts / Story_Assets / Ledgers / Memory），否则拒绝。`00_Project`、`07_Staging`、`90_Archive` 不是合法目标。
- 同一任务内同名目标已存在时拒绝；确需替换加 `--replace`。
- 每次 stage 都记下目标正式文件当时的 sha256（`base_sha256`），供后续判冲突。

### `novel approve --task <ID> [--note] [--reject] [--force] [--as author]`

- 非 `author` 一律拒绝（退出码 3）。
- 批准前先跑一遍 diff：存在冲突则拒绝，除非 `--force`（等于作者确认「以 staging 为准，覆盖我的手改」）。
- 批准记录写入 `<runtime>/approvals/APR-00000N.json`，绑定每个 staged 文件的 `target` / `staged` / `sha256` / `bytes`。
- `--reject` 记一条驳回，任务 `approval_state` 变为 `rejected`。
- 重新批准会生成新记录，旧记录保留。

### `novel publish --task <ID> --confirm [--confirm-canon] [--as author]`

四道闸：

1. 有 `decision=approved` 的批准记录；
2. staging 里每个批准过的文件，当前 sha256 与批准时一致；
3. staging 里没有批准范围外的新增文件；
4. 目标落在正式区；涉及 `01_Canon/` 时还要 `--confirm-canon`。

通过后：目标已存在则先复制到 `90_Archive/publish_archive/<TASK-ID>/<时间戳>/<目标路径>`，再原子写入。任务标 `done`，作者基线推进。

任务已 `done` 或 `cancelled` 时拒绝发布。

---

## 6. 作者手改

### `novel adopt [--dry-run] [--note] [--as author]`

扫描项目内受管文件（`.md/.markdown/.yaml/.yml/.json/.txt/.csv`，跳过 `07_Staging`、`08_Reviews`、`90_Archive`），与基线比对，报告新增 / 修改 / 删除，然后把基线推进到当前状态。

- **不回滚、不覆盖、不改文件内容**——它只登记事实。
- 首次运行建立基线，不产生「改动」记录。
- 历史保留最近 100 条，存 `<runtime>/authorship/<project_id>.json`。
- 只接受作者身份。

---

## 7. 备份与恢复

### `novel backup [--out <目录>] [--label <标签>] [--no-git] [--list]`

默认落 `<runtime>/backups/BK-YYYYMMDD-HHMMSS/`，内含 `files/`、`manifest.csv`、`meta.json`。

流程按 §11.2：遍历 → 逐文件复制 → 全量重算哈希比对 → 写清单与元数据。任何一条哈希不一致即判失败并保留现场。

默认包含 `.git`（私人 Git 历史），`--no-git` 可排除。`.obsidian`、`__pycache__`、`node_modules` 等缓存目录始终跳过。

### `novel restore --backup-id <ID> [--dry-run] [--confirm] [--overwrite]`

- 不带 `--confirm` 时只预演，不写盘。
- 预演分类：新增 / 内容相同 / 冲突 / 损坏。
- 清单里出现越界路径或哈希不符，直接拒绝恢复。
- 默认只写「新增」，冲突文件跳过并列清单。
- `--overwrite` 覆盖冲突文件，覆盖前先把被覆盖的版本另存到 `<runtime>/restore-undo/<备份ID>-<时间>/`。
- 恢复后建议依次跑 `doctor`、`validate`、`rebuild-index`。

---

## 8. 地图

三个子命令共用一套路径解析，唯一来源是 `Config` + `ProjectPaths`；代码里没有任何
`private/00_Canon/...` 这类硬编码路径。

### `novel maps paths [--world-subdir <名字>]`

打印本次会用到的六个路径：州域志、区划表、地点目录、输出目录、版式、资产表。
路径不对时先跑这条，比看报错猜快。

- `州域志` = `<project>/01_Canon/World/<world-subdir>/总表/<州域志文件名>.md`
- `区划表` = 同目录 `WORLD-0040_行政区划表.md`
- `地点目录` = `<project>/01_Canon/Locations/`
- `输出目录` = `<vault>/00_System/Dashboards/Maps/`
- `版式` = `<project>/00_Project/jiuzhou_layout.yaml`

### `novel maps import-admin [--xlsx <资产表>] [--confirm]`

把资产表「地图库」工作表读成 `WORLD-0040_行政区划表.md`。原表空格照录并在文末点名，
不替作者猜。**内容无变化时不写盘。**

### `novel maps build [--maps-only] [--confirm]`

重画地图页（`<地图页>.md`）与两张 canvas。

- **默认预演**：只报「将改几个文件」，不落盘。
- `--maps-only`：只重画图，不碰州域／府／城邑三种档案。
- 不加 `--maps-only` 会重写 11 份州域档案、28 份府档案，并给城邑补「层级」行；
  这些档案里有正文受控块与作者手改，重写时按块名搬回、按 `PRESERVE_KEYS` 保历史字段。
- 幂等的判据是**「写入 0 个文件」**，不是「命令没报错」。

```bash
novel maps paths                                    # 先确认路径
novel maps build --maps-only                        # 预演：会改几个文件
novel maps build --maps-only --confirm              # 只重画图
novel maps build --confirm                          # 连档案一起重跑（危险，先看预演）
```

---

## 9. 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 冲突：目标已存在、哈希不符、任务已发布、diff 有冲突、恢复清单损坏 |
| 2 | 用法错误：参数缺失、枚举值非法、状态流转非法、未指定项目 |

> 未指定项目也归 2：`--project` 与 `Vault_Config.yaml` 的 `active_project.project_id`
> 都没给时，写入类命令一律拒绝，不做「Vault 里只有一个项目就用它」的推断。

| 3 | 安全拒绝：路径越界、操作者无权、缺二次确认 |
| 4 | 批准问题：缺批准、批准已失效 |

---

## 10. 目录与文件落在哪

| 内容 | 位置 | 可否重建 |
|---|---|---|
| 任务记录 | `<runtime>/tasks/TASK-*.yaml` | 否（但可重开任务） |
| 批准记录 | `<runtime>/approvals/APR-*.json` | 否 |
| 操作日志 | `<runtime>/logs/ops.jsonl` | 否 |
| 索引 | `<runtime>/index/index.json` | 是 |
| 作者基线 | `<runtime>/authorship/<project_id>.json` | 是（重新 adopt） |
| 备份 | `<runtime>/backups/<BK-ID>/` | 否 |
| staging 产物 | `<vault>/10_Projects/<PROJECT>/07_Staging/<TASK-ID>/` | 否（发布后仍保留作证据） |
| 发布旧版归档 | `<vault>/10_Projects/<PROJECT>/90_Archive/publish_archive/` | 否 |
| 地图页与 canvas | `<vault>/00_System/Dashboards/Maps/` | 是（`maps build --confirm`） |
| 版式配置 | `<project>/00_Project/jiuzhou_layout.yaml` | 否（手写；属排版不属设定） |
