# 架构：三条边界的物理隔离

## 决策

系统由三个互不嵌套的目录组成：

```
<public_repo>    公共代码与模板，可推 GitHub
<author_vault>   作者私人事实源，不配远端，不推
<runtime>        索引、缓存、日志、staging，可随时删除重建
```

三者不得互为父子。Vault 不在公仓内，公仓也不在 Vault 内。符号链接不得跨越三者。

## 为什么不用 `.gitignore` 隔离

`.gitignore` 是第二道防线，不是边界。历史教训：曾用「公仓在库根、私人库放子目录 + gitignore 排除」的形态，
一次目录改名后清洗清单没跟着改，3519 个文件在公网挂了三天。目录隔离是结构约束，gitignore 只是形式约束。

## 事实源优先级

| 数据 | 权威来源 |
|---|---|
| 设定、人物、地点、组织 | Vault 内 Markdown/YAML |
| 规划与正文 | Vault 内文件，状态由 frontmatter 标明 |
| 索引 | Runtime 内，可删除重建 |
| Agent 任务状态 | Runtime / 任务记录，不等于 Canon |

冲突时以作者确认过的 Markdown/YAML 为准。索引不得反向覆盖事实源。

## 路径解析

CLI 不从代码里取路径，一律走 `novel_system.config.load()`：

优先级：CLI 参数 → 环境变量 → 用户级配置 → Vault 级配置 → 仓库默认值。

所有写入路径经 `ensure_inside(child, vault)` 校验，`..` 与符号链接逃逸一律拒绝。

## 未采纳的方案

- **公仓内嵌私人目录 + gitignore**：已被证明不可靠，见上。
- **一个 Vault 只放一部小说**：作者可能写多部，改为 `10_Projects/<PROJECT_ID>_<slug>/`。
- **SQLite 作为事实源**：索引会损坏、会与 Markdown 不一致，且不可人工阅读。仅作加速层。
