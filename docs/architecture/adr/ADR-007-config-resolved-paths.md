# ADR-007：Python CLI 一律通过配置解析路径

- **状态**：已接受
- **批准人**：作者
- **日期**：2026-11-20
- **相关**：ADR-001、ADR-004

## 背景

旧系统 30 条命令全部把路径写死在代码里：`private/00_Canon`、`private/03_Outline`、
`01_Entity_Graph` 这类字面量散落在十几个模块中。V4.0 迁移把目录改成
`10_Projects/<ID>/01_Canon`，**30 条命令一条都没法用**，不是坏了，是根本找不到文件。

更糟的是这些字面量不集中，改一处漏三处。

## 问题

路径硬编码的失效方式很隐蔽：代码不报错，只是读到 0 个文件、写出 0 个结果。
`validate` 第一次在新 Vault 上跑时报告「实体 0 份」，就是这个原因。

## 决策

**代码里不出现任何私人绝对路径，也不出现目录结构字面量。** 路径只有一个来源：

```
优先级从高到低
1. CLI 参数        --vault / --runtime / --project
2. 环境变量        NOVEL_VAULT / NOVEL_RUNTIME
3. 用户级配置      ~/.config/novel-system/config.yaml
4. Vault 级配置    <vault>/00_System/Vault_Config.yaml
5. 仓库默认值      config/defaults.yaml（只有结构与隐私开关，无绝对路径）
```

实现约定：

- 目录名从项目的 `project.yaml` 读取（`canon_root` 等），不在代码里拼；
- `ProjectPaths` 统一提供 `canon` / `planning` / `manuscripts` / `staging` / `archive` 等属性；
- 任何拼出来的路径都要过 `ensure_inside(target, vault)`；
- `doctor` 把「配置来自哪一层」打印出来，路径错了能一眼看到是哪层配的。

兼容层：真实 Vault 的 `Vault_Config.yaml` 用的是 `layouts.*` / `runtime_root` 写法，
与 CLI 早期的 `layout.*` / `paths.runtime` 不同。`config` 层做归一化折算，两套写法都认，
不要求作者改文件。

## 备选方案

| 方案 | 为什么没选 |
|---|---|
| 用当前工作目录推断 Vault | 在公仓里跑命令会指向公仓，正是最危险的误写场景 |
| 全局常量集中在 `constants.py` | 仍改一处漏三处，且改常量要改代码、要重装包 |
| 只用环境变量 | 作者开新终端就丢，且难以同时指向两个 Vault 做对比 |
| 读 Vault 内 `.obsidian/` 判断 | Obsidian 配置不是路径事实源，且不是每个 Vault 都有 |

## 影响

- 换目录、换机器、改项目结构都不用改代码；
- 代价：多一层配置解析，配置错了要有清晰的报错（`doctor` 承担这个职责）；
- 遗留：两个地图生成器（`build_jiuzhou_map.py`、`import_map_admin.py`）仍硬编码旧路径，
  已归档，重跑前必须改写。

## 验收方式

`tests/unit/test_config.py` 覆盖五层优先级与归一化；
`tests/integration/test_cli_flow.py` 覆盖相对路径按 cwd 解析；
CI 的 `guard` job 用 grep 确认 `src/` 内无 `/Users/` 或 `/home/` 字面量。
