# NovelVault + novel_system

超长篇小说个人知识库与受控 AI 写作工作流（MVP 实现）。

对应 PRD：`超长篇小说个人知识库_MVP_PRD.md` v1.0。

- **事实源**：`NovelVault/` 下的 Markdown + YAML（Obsidian 可直接打开）
- **可重建索引**：`NovelVault/.novel_system/index.sqlite`（SQLite + FTS5）
- **执行层**：Python（状态机 / 权限 / Schema / 事务 / 审计 / Git）
- **Agent 层**：总编、资料官、编辑、主笔、记忆管理员（Prompt 在 Vault 内，可改）

---

## 1. 目录

```text
NovelVault/                       # 唯一 Git 仓库：事实源 + 执行层代码
├── 00_System/
│   ├── Config/                 # system_config / agent_registry / permissions / workflow / id_rules
│   ├── Prompts/                # 5 个 Agent 的 System Prompt
│   ├── Quality_Gates/          # 写前 / 写作 / 写后三道门槛
│   ├── Schemas/                # `novel export-schemas` 导出的 JSON Schema
│   └── novel_system/           # Python 包（src 布局）
│       ├── src/novel_system/
│       │   ├── cli.py orchestrator.py config.py vault.py transaction.py ids.py gitops.py
│       │   ├── agents/         # base + 5 个 Agent
│       │   ├── workflow/       # state_machine / preflight / writing / review / memory_commit / ingest
│       │   ├── memory/         # entity_resolver / retrieval / context_builder / summarizer
│       │   ├── validators/     # schema / canon / continuity / foreshadowing / permissions
│       │   ├── database/       # sqlite / indexer / rebuild
│       │   └── audit/          # logger / reports
│       └── tests/              # AC-01 ~ AC-19 验收测试
├── 01_Core_Memory/             # 世界观、人物、势力、地点、物品、敌人
├── 02_Hot_Memory/              # 当前卷、当前剧情状态、人物当前状态
├── 03_Short_Term_Memory/       # 滚动摘要、活跃上下文、风格参考
├── 04_Outline/                 # 总纲 / 卷纲 / 章节纲 / 场景细部
├── 05_Story_Assets/            # 桥段、对话、感官、情绪资产
├── 06_Plot_Ledger/             # 伏笔账本、任务、冲突、人物弧线
├── 07_Entity_Graph/            # 关系、事件、实体索引
├── 08_Manuscripts/             # 正式正文
├── 09_Audit_Reports/           # Preflight / 章节审核 / 连续性 / 伏笔 / 记忆更新报告
├── 10_Change_Log/              # Canon 变更、状态事件、回滚记录
├── 11_Templates/               # 14 份文档模板
├── 12_Dashboard/               # Obsidian Dataview 看板
└── .novel_system/              # 运行时：index.sqlite / staging / logs / backups / locks
```

与 PRD 14.2 的结构差异（都是有意为之，PRD 未覆盖但必需）：

| 新增 | 原因 |
|---|---|
| `vault.py` | 唯一「碰盘」入口：frontmatter 解析、原子写、哈希、备份 |
| `transaction.py` | PRD 10.3 的 14 步事务、`rollback()`、报告补提交 |
| `ids.py` | PRD 7.2 的 ID 统一生成服务（Agent 不得造 ID） |
| `tests/` | PRD 17.1 的验收标准落地 |

---

## 2. 安装

```bash
cd /Users/Mr_Zhou/WorkBuddy/2026-11-03-04-14-38
python3 -m venv .venv
./.venv/bin/pip install -e NovelVault/00_System/novel_system
./.venv/bin/novel --vault NovelVault doctor
```

代码与 Vault 同属一个 Git 仓库（`00_System/novel_system/`），但 venv 建议放在 Vault 之外，
避免把解释器产物混进事实源目录（`.gitignore` 已兜底，双保险）。

CLI 入口为 `novel`；也可以用 `./.venv/bin/python -m novel_system.cli`。
Vault 路径解析顺序：`--vault` → `NOVEL_VAULT` 环境变量 → 当前目录/`NovelVault` → 逐级向上查找。

---

## 3. 命令

```bash
novel doctor                     # 环境体检（目录/提示词/模板/索引/Git/LLM）
novel rebuild-index              # 从 Markdown 重建 SQLite（索引永远可丢）
novel status [CH-0002]           # 章节状态机与索引统计
novel show CHAR-0001 --body      # 实体详情

novel preflight CH-0002          # 写前：资料包 + 硬规则 + 总编裁决
novel write CH-0002              # 主笔写草稿 → 只进 staging
novel review CH-0002             # 总编审稿 → 报告 + 状态推进
novel publish CH-0002 --yes      # 作者确认 → 锁定正式正文（事务 + Git）
novel commit-memory CH-0002      # 从正文提取记忆提案（不写入）
novel apply-memory CH-0002 --approve auto|all|--ids PROP-...
novel audit CH-0002              # 写后连续性回归审计
novel rollback OP-2026...        # 按 operation_id 回滚（有备份 + 冲突保护）

novel search "刀鞘"              # 章节全文检索（中文二元组 FTS）
novel new character 新人物 --set aliases='[别名]'
novel log --limit 20             # 审计日志
novel export-schemas             # 导出 Pydantic JSON Schema 到 00_System/Schemas

# 录入既有旧稿（txt / md）
novel ingest <文件> --dry-run     # 只预演切章结果，不写任何文件
novel ingest <文件> --split chapter --volume VOL-0001 --tag
                                 # 切章 + 分配 CH 号 + 建大纲与正文 + 批次日志 + Git + 快照标签
novel annotate CH-0003 --from ann.txt   # 给已发布章节补记忆标注（事件/状态/关系/伏笔/摘要）

# 版本控制
novel history --limit 20         # 仓库状态 + 提交历史 + 快照标签
novel snapshot vol01-complete --message "第一卷完稿"   # 给当前提交打标签
```

命令行退出码：`3` 工作流拒绝、`4` 权限拒绝、`5` 事务失败、`6` 版本控制错误、`1` 配置/文件错误。

---

## 4. 受控闭环

```text
作者批准大纲（`- [x] 已批准写作`）
   ↓ novel preflight    资料官召回 → 硬规则 → 总编裁决 → Pack + 报告
preflight_passed
   ↓ novel write        主笔只读 Pack，草稿写 staging/draft
draft
   ↓ novel review       总编审核 → editor_approved（不通过则 revision）
editor_approved
   ↓ novel publish --yes  作者确认 → 事务写入 08_Manuscripts + Git
published
   ↓ novel commit-memory  记忆管理员产出提案（staging/memory/*.yaml）
   ↓ novel apply-memory   校验 → 备份 → 原子写 → 索引 → 审计 → Git → 回归审计
memory_committed
```

每一步都有对应的报告落在 `09_Audit_Reports/`，正式写入都有 `operation_id` 可回滚。

### 事务顺序（PRD 10.3 / 15）

`校验 → 备份 + manifest → staging 副本 → 原子替换 → 更新索引 → 审计日志 → Git commit → 回归审计`

失败时自动还原已替换的文件、删除本次新建的文件，并在 `audit_log` 记 `failed`。
`rollback` 有冲突保护：若文件在本操作之后又被改动，默认拒绝覆盖，需 `--force`。

---

## 5. 权限与护栏

- 权限矩阵在 `00_System/Config/permissions.yaml`，由 `validators/permissions.py` 强制；
  `01_Core_Memory/**`、`08_Manuscripts/**` 等正式路径对 Agent 是硬锁，只有作者或
  `system_executor`（代表已批准提案）能写。
- Agent 只能写 `.novel_system/staging/**` 与 `09_Audit_Reports/**`。
- 状态机在 `workflow.yaml`，非法迁移直接抛 `WorkflowError`（例如跳过 preflight 直接写）。
- 伏笔规则：窗口外不得 `paid_off`/`triggered`；超窗提示 `overdue`；回收必须有事件或章节引用；章节比较一律按数值。

---

## 6. LLM 与离线模式

```bash
export NOVEL_LLM_API_KEY=sk-...
export NOVEL_LLM_BASE_URL=https://api.openai.com/v1   # 兼容 OpenAI 协议即可
export NOVEL_LLM_MODEL=gpt-4o-mini
```

- 有 Key：5 个 Agent 走真实模型（Prompt 见 Vault 内 `00_System/Prompts/`）。
- 无 Key：自动降级为 **offline** 模式，报告里显式标注 `[OFFLINE]`，不会伪装成模型输出。
  - 总编：只按硬规则给裁决；
  - 资料官 / 编辑：确定性结构化输出；
  - 主笔：产出「离线占位骨架」，审核阶段会被 `blocks_write` 拦下，**不能发布**；
  - 记忆管理员：只读取正文「作者备注」里的结构化标注，不做语义推断。

### 作者备注标注格式（离线模式下的记忆提取来源）

```markdown
## 作者备注（不进入正文）
- 事件: 事件名 | 客观事实 | 参与:CHAR-0001,CHAR-0002 | 地点:LOC-0002 | 结果:...
- 状态: CHAR-0001 | 位置=... | 伤势=... | 能力=... | 生死=存活 | 认知=... | 未知=...
- 关系: REL-0001 | 性质=... | 说明=...
- 伏笔: FORESHADOW-0002 | 状态=hinted | 说明=...
- 摘要: 一句话
```

`认知=` 写入「认知边界」，`生死=` 写入「客观生死」，两者永不混写。

---

## 7. 演示数据

Vault 内置一套可运行示例（《白骨渡》）：

- CH-0001《楔子：血里的刀》已完成全流程（`memory_committed`，含 EVT-CH-0001-001/002）
- CH-0002《渡口无灯》已完成全流程（13xx 字正文、EVT-CH-0002-001、状态与摘要已更新）
- FORESHADOW-0001 窗口 CH-0004～CH-0008（未到，禁止提前揭示）
- FORESHADOW-0002 窗口 CH-0002～CH-0003，已在 CH-0002 推进到 `hinted`
- ENEMY-0001 邢咎：客观状态「生死未明」，但沈砚「认为」其已死 —— 认知与事实分离的示例

---

## 8. 测试

```bash
./.venv/bin/python -m pytest novel_system/tests -q
```

20 个用例覆盖 PRD 17.1 全部 AC：

| 用例 | 验证点 |
|---|---|
| AC-01 | 重复实体 ID 被拒绝 |
| AC-02 | 伤势无故恢复被识别（有治疗事件则放行） |
| AC-03 | 死亡人物出场 → BLOCKER |
| AC-04 | 角色误判只进认知边界 |
| AC-05 | 超过回收窗口 → overdue |
| AC-06 | 无来源/过早回收 → 拒绝 |
| AC-07 | 引用不存在的实体 → 失败 |
| AC-08 / AC-13 | Agent 越权写正式路径 → PermissionDenied |
| AC-09 | 事务中途失败 → 文件不变 + 审计 failed |
| AC-10 | 回滚恢复文件并重建索引 |
| AC-11 | 重复记忆提交不产生重复事件 |
| AC-12 | 删除索引后可从 Markdown 重建 |
| AC-14 | CH-0010 排在 CH-0002 之后 |

> 若你的环境默认临时目录不可写，测试已把 `basetemp` 指到项目内 `.pytest_tmp/`。

---

## 9. 已知边界

- 离线模式不产文学正文（这是设计约束，不是缺陷）；要真实写作必须配 Key 或由作者改写草稿。
- 规则引擎只做「时间 / 状态 / 引用 / 生死」这类可判定事实；叙事质量、伏笔自然度交给 LLM。
- 向量检索、图数据库按 PRD 后置，未实现。
- `.gitignore` 排除了 `index.sqlite` 与 `backups/`；换机器后需 `novel rebuild-index`，历史回滚依赖本地备份。
