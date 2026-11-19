# 变更日志

格式：语义化版本。每条记录写明兼容性影响与迁移提示。

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
