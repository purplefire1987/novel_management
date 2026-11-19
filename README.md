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

| 命令 | 用途 | 写入 |
|---|---|---|
| `novel doctor` | 检查配置、依赖、路径边界、隐私开关 | 否 |
| `novel status` | 展示 Vault、项目、Runtime 当前状态 | 否 |
| `novel validate` | 校验 Vault 结构、项目元数据、路径越界 | 否 |
| `novel init-author` | 初始化作者 Vault（需确认） | 是 |
| `novel init-project` | 创建小说项目骨架（需确认） | 是 |
| `novel rebuild-index` | 从 Markdown/YAML 重建索引到 Runtime | 是（仅 Runtime） |

后续阶段将补齐 `task`、`stage`、`diff`、`approve`、`adopt`、`publish`、`backup`、`restore`。

---

## 5. 数据安全约定

- **索引不是事实源。** SQLite 索引可随时删除重建；与 Markdown 冲突时以 Markdown 为准。
- **写入前检查目标是否被作者改过。** 冲突时停止或生成新文件，不静默覆盖。
- **越界即拒绝。** 所有路径经规范化校验，必须落在 Vault 根之内；`..` 与符号链接逃逸一律拒绝。
- **Agent 不写 Canon。** Agent 只能写 staging 与草稿，正式入库需作者批准。
- **`--yes` 不是万能开关。** 破坏性操作保留独立确认。

---

## 6. 目录

```text
src/novel_system/   代码（cli / config / core / storage / indexing / validation / workflows / agents / audit / adapters）
config/             默认配置与配置 Schema
schemas/            数据结构定义（project / entity / chapter / task / event / memory）
templates/          Markdown/YAML 模板（author / project / entity / planning / manuscript / memory / reports）
agents/             九 Agent 定义与权限
workflows/          六个工作流定义
quality_gates/      质量门规则
docs/               架构、用户、开发、运维、WorkBuddy 文档
examples/           完全虚构的示例项目
tests/              单元、集成、安全、验收测试
```

---

## 7. 测试

```bash
pytest
```

测试只用 `examples/sample_novel` 与临时目录中的虚构数据，不读取任何真实作品。

---

## 8. 许可

`LICENSE_PENDING`。在许可证确定前，本仓库不得作为对外发布的软件分发。
