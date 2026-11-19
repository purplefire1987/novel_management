# 在 WorkBuddy 中启用（Phase 6 规划）

当前阶段**尚未注册任何 Agent 团队**。本文档描述后续做法与约束。

## 约束

1. 不覆盖已有插件。目标目录若已存在，先备份并报告差异。
2. 先查本机 WorkBuddy 版本与插件规范，机制变化时先出兼容性差异报告。
3. 权限矩阵以 `agents/common/permissions.md` 为权威；Prompt 里不得出现与之冲突的授权。
4. 注册前先在测试环境验证：角色识别、项目隔离、越界拒绝。

## 步骤（待执行）

1. 确认 `~/.workbuddy/plugins/marketplaces/` 下目标市场目录。
2. 生成插件包：`plugin.json` + 九份 `agent.md` + 权限说明。
3. 写入源目录 → 复制到 `cache/<market>/<name>/<version>/` → 更新 `installed_plugins.json` 的 `installPath` 与 `version`。
4. 重启 WorkBuddy，新建会话验证九人可被选中。

## 已知坑

- 注册表 `installPath` 指向缓存旧版本时，实际加载的是旧 Prompt，且不会报错。
- `.orphaned_at` 标记会让某个版本被静默跳过。
- 改完必须三处同步：源目录、缓存目录、注册表版本号。
