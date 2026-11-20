# ADR-002：Markdown / YAML 为 Canon 事实源

- **状态**：已接受
- **批准人**：作者
- **日期**：2026-11-20
- **相关**：ADR-003

## 背景

Canon（设定库）需要同时满足四个要求：作者能直接读写、能进 Git 做 diff、能被脚本批量处理、
在十年后仍然能打开。这四条把候选格式限得很死。

## 问题

如果用数据库或二进制格式存 Canon：

- 作者无法在 Obsidian 里直接改，任何修改都得走工具；
- Git diff 不可读，改了什么看不出来；
- 格式一旦废弃，作品就锁死在某个版本的软件里。

但纯文件也有代价：没有约束、容易写歪、批量改容易漏。

## 决策

**Canon 的唯一事实源是 Vault 内的 Markdown 文件 + YAML frontmatter。** 具体约定：

- 正文与档案：`.md`，frontmatter 用 YAML，字段依 `schemas/entity.schema.json`；
- 项目元数据：`00_Project/project.yaml`；
- 账本、时间线：`.md` 表格或 `.yaml`，依 `05_Ledgers/` 各目录约定；
- 结构化视图：`.base` / Dataview 查询，**只读派生，不承载事实**。

frontmatter 解析器在本仓库内实现（`cli/_common.py` 的 `read_frontmatter`），不强制依赖 PyYAML：
无 PyYAML 时降级为纯标准库解析，代价是数字变字符串、嵌套映射读不到——这两个限制写进文档，
不让它变成静默的坑。

## 备选方案

| 方案 | 为什么没选 |
|---|---|
| SQLite 存 Canon | 作者改不了、diff 不可读、十年后未必打得开 |
| JSON 单一文件 | 合并冲突时整文件冲突，无法按条目解决 |
| DOCX / Scrivener 工程 | 二进制，脚本不可处理，版本管理失效 |
| Markdown + 强制外部 Schema 校验器 | 引入运行依赖，作者换机器就跑不起来 |

## 影响

- 作者可以随时用任何编辑器改任何文件，这是一条**合法主路径**，不是例外；
- 系统因此必须提供「收编」能力（`novel adopt`）而不是「回滚」能力；
- 一致性校验只能在读取时做，不能靠写入时拦截——所以 `novel validate` 是必需品。

## 验收方式

`tests/unit/test_storage.py`、`tests/integration/test_workflow_flow.py` 覆盖 frontmatter 读写往返；
`validate` 在真实 Vault 上能识别 966 份实体、0 重复 ID。
