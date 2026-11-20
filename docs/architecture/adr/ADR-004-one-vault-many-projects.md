# ADR-004：一个作者一个 Vault，多项目隔离

- **状态**：已接受
- **批准人**：作者
- **日期**：2026-11-20
- **相关**：ADR-001、ADR-007

## 背景

作者同时可能写多部作品，共享同一套系统、同一套 Agent、同一套工具链。
但也存在「把 A 书的人物写进 B 书」这种串味风险——对长篇创作来说，这是硬错误，不是小瑕疵。

## 问题

两种极端都不合适：

- 一部作品一个 Vault：作者要在多个 Obsidian 仓库之间切换，共享素材无法复用，Agent 上下文也要重配；
- 一个 Vault 一个平铺目录：所有作品混在一起，任何一次批量脚本都可能跨作品误改。

## 决策

**一个作者一个 Vault，Vault 内以 `10_Projects/<PROJECT_ID>_<slug>/` 隔离多部作品。**

- `project_id` 不可变且唯一；
- 每个项目自带完整结构（`00_Project` 到 `90_Archive`），互不共享目录；
- 每个项目的 `project.yaml` 显式声明 `canon_root` / `planning_root` / `manuscript_root` /
  `staging_root` / `archive_root`，路径相对 Vault 根；
- **未指定 `project_id` 的写入任务一律拒绝**（退出码 2），不做「猜一个最像的」；
- 默认禁止跨项目读取；确需共享的资产放 `90_Shared_Assets/`，那是唯一的共享层；
- 索引带 `project` 字段，查询默认按当前项目过滤。

## 备选方案

| 方案 | 为什么没选 |
|---|---|
| 一部作品一个 Vault | 切换成本高，共享素材要靠复制，Agent 每次要重新定位 |
| 一个 Vault 平铺所有作品 | 批量脚本的爆炸半径是整个 Vault，一次误改波及全部作品 |
| 用 Git 分支隔离作品 | 分支切换会改工作区文件，作者的 Obsidian 会跟着变，体验不可接受 |

## 影响

- 新增作品只需 `novel init-project`，系统层（Agent、CLI、模板）不变；
- `validate` 会检查跨项目双链并报告——这类链接通常是写错了项目；
- 代价：项目 ID 命名要一次定死，改 ID 需要迁移脚本与作者批准。

## 验收方式

`tests/integration/test_project_isolation.py` 覆盖：未指定项目拒绝写入、
A 项目的任务不能写 B 项目的路径、跨项目双链被报出。
