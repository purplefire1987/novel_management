# novel_management

超长篇小说个人知识库 + 受控 AI 写作工作流。以 Obsidian Vault 作为唯一事实源，
用 Python 执行层把「设定 → 大纲 → 写前召回 → AI 起草 → 审核 → 发布 → 记忆更新」
做成一条有权限、有事务、可审计、可回滚的流水线。

写超长篇真正的成本不在「写不出来」，而在**写了三百万字之后忘掉自己写过什么**：
某个人物的左手在第 87 章已经断了、某个伏笔该回收了没回收、某句设定前后矛盾。
这套系统把这类问题交给机器去查，把创作决定权留给人。

## 核心设计

| 原则 | 落地方式 |
|---|---|
| **事实源优先** | 一切都是 Markdown + YAML frontmatter，Obsidian 直接打开；SQLite 索引随时可从 Markdown 重建，删了不心疼 |
| **权限由代码执行** | Agent 不能写 `01_Core_Memory/**` 和 `08_Manuscripts/**`，越权直接抛 `PermissionDenied`，而不是写在 Prompt 里靠模型自觉 |
| **单通道写入** | 所有正式写入走同一个事务：校验 → 备份 + manifest → staging → 原子替换 → 更新索引 → 审计 → Git 提交 |
| **可回滚** | 每次写入有 `operation_id`，`novel rollback <op_id>` 还原文件、删除新建、重建索引 |
| **状态机强制顺序** | `outline → preflight_passed → draft → editor_approved → published → memory_committed`，跳步即报错 |
| **人工终审** | 记忆变更以「提案」形式产出，作者批准后才落盘；规则引擎只判可判定事实（ID、生死、伤势回退、伏笔窗口、幂等），不硬判文学质量 |

## 目录结构

```text
超长篇小说管理系统/            # ← 仓库根，同时也是 Obsidian Vault 根
├── 00_System/                  # 配置 / Prompt / 质量门 / Schema
│   ├── Config/                 # system_config、agent_registry、permissions、workflow、id_rules
│   ├── Prompts/                # 5 个 Agent 的 System Prompt（可改）
│   ├── Quality_Gates/          # 写前 / 写作 / 写后三道门
│   └── novel_system/           # Python 执行层（src 布局）
├── 01_Core_Memory/             # 世界观、人物、势力、地点、物品、敌人（Canon）
├── 02_Hot_Memory/              # 当前卷、剧情状态、人物当前状态
├── 03_Short_Term_Memory/       # 滚动摘要、活跃上下文、风格参考
├── 04_Outline/                 # 总纲 / 卷纲 / 章节纲 / 场景细部
├── 05_Story_Assets/            # 桥段、对话、感官、情绪资产
├── 06_Plot_Ledger/             # 伏笔账本、任务、冲突、人物弧线、未解问题
├── 07_Entity_Graph/            # 关系、事件、实体索引
├── 08_Manuscripts/             # 正式正文
├── 09_Audit_Reports/           # Preflight / 审核 / 连续性 / 伏笔 / 记忆更新报告
├── 10_Change_Log/              # Canon 变更、状态事件、回滚记录、Ingest 录入批次
├── 11_Templates/               # 14 份文档模板
├── 12_Dashboard/               # Obsidian Dataview 看板
├── 99_Archive/Raw_Sources/     # 待录入的原始旧稿（不参与索引，但受 Git 管理）
└── .novel_system/              # 运行时：index.sqlite / staging / logs / backups
```

> 录入新稿时，把 txt/md 放进 `99_Archive/Raw_Sources/`，再执行 `novel ingest`。
> 该目录不参与实体索引，但会随 Git 一起版本化——原始底稿是唯一能对账的东西，不要删。

## 快速开始

```bash
# 1. 装依赖（建议 venv 放在 Vault 之外）
python3 -m venv .venv
./.venv/bin/pip install -e 00_System/novel_system

# 2. 体检
./.venv/bin/novel doctor

# 3. 配模型（任何 OpenAI 兼容接口都行）
export NOVEL_LLM_API_KEY=sk-...
# 然后在 00_System/Config/system_config.yaml 里改 base_url 与 model
```

不配 Key 也能跑：系统降级为离线确定性模式，会明确标注「离线占位」，
并且**生成的骨架草稿会被审核环节拦下，不允许发布**——不假装在写。

## 单章闭环

```bash
novel preflight CH-0002      # 召回设定/状态/事件/伏笔/近章正文，装配资料包 + 硬规则检查
novel write CH-0002          # 主笔按资料包起草 → 只进 staging
novel review CH-0002         # 总编审核：设定冲突、连续性、大纲对齐、信息越界
novel publish CH-0002 --yes  # 作者确认 → 事务写入 08_Manuscripts + Git 提交
novel commit-memory CH-0002  # 从正式正文提取事件/状态/关系/伏笔变更提案
novel apply-memory CH-0002 --approve all   # 批准并落盘，回归审计
novel audit CH-0002          # 写后连续性回归
novel rollback <op_id>       # 出问题时整体回退
```

## 录入既有旧稿

已有的 txt / md 稿件不必手动抄进 Vault，走录入通道：

```bash
# 1. 先预演，确认切章正确
novel ingest 99_Archive/Raw_Sources/old_draft.txt --dry-run

# 2. 正式入库：切章 → 分配 CH 号 → 建大纲 + 正式正文 → 批次日志 → Git 提交 → 快照标签
novel ingest 99_Archive/Raw_Sources/old_draft.txt --split chapter --volume VOL-0001 --tag

# 3. 补记忆标注（原始稿没有标注，不补则抽不出任何事实）
novel annotate CH-0003 --from /tmp/ann-CH-0003.txt

# 4. 抽取并提交记忆
novel commit-memory CH-0003 --approve auto
```

- 切分方式：`auto` / `chapter`（第X章）/ `heading`（Markdown 标题）/ `number`（1、）/ `single`（整篇一章）
- 首个标题前的书名、作者行会自动跳过，并在输出里提示跳过字数
- 整批录入是**一个事务**：失败全不写，成功可整体 `novel rollback <操作号>` 撤回
- 同一文件按内容哈希判重，重复录入会被拒绝（`--force` 可强制重录，续接章号）

## 版本控制

系统把 Git 做进了事务里，每次正式写入自动提交（`novel-system: <动作> [OP-...]`）。在此之上：

```bash
novel history --limit 20                              # 仓库状态 + 提交历史 + 快照标签
novel snapshot vol01-complete --message "第一卷完稿"    # 给当前提交立路标
novel rollback OP-20261103-202623-f525                # 按操作号精确回退
```

## 骰子与规则裁决（骰子娘）

写到"这一刀砍不砍得中"的地方，随机数必须是真的、且能被桌上所有人复核。`novel dice` 就是那个通道：

```bash
novel dice check --bonus 11 --dc 55 --label "辨识机关图纸" --witness 甲=37 --witness 乙=08
novel dice check --bonus 11 --dc 55 --label "夹击" --mode advantage      # 优势取高 / 劣势取低
novel dice duel --a 30 --b 40 --name-a 陈默 --name-b 邢咎 --label "斗剑"  # 差 > 20 触发碾压
novel dice roll 3d6 --label "断水刀伤害"                                  # 非 d100 骰型
novel dice verify                                                        # 任何人复算 + 验链
```

- **承诺—开奖**：掷骰前记录 `sha256(server_seed)`，开奖后公开种子，任何人可重算。
- **玩家见证值进熵池**：`--witness 甲=37` 与种子一起合成 entropy，主持人无法单方面决定结果。
- **哈希链台账**：`06_Plot_Ledger/Dice/rolls.jsonl` 追加式记录，改任何一条历史链就断。
- **规则来自事实源**：DC 表、优势劣势、对抗、大成功 96–100 / 大失败 01–05、极值 10 倍，
  全部引自 `01_Core_Memory/World/九州/规则书/`，不自创。
- 掷骰是"发生过的事"，**不参与 `novel rollback`**。

## 索引与视图（Obsidian）

```bash
novel rebuild-index   # 重扫 Markdown → 重建 SQLite → 顺带刷新实体索引页
novel index-md        # 只重画 07_Entity_Graph/Entity_Index.md
novel find 环教        # 按名称 / 别名 / 概要查单条
```

- **全量索引**：`07_Entity_Graph/Entity_Index.md`，按类型分组、按编号排序、每条都是双链，
  由 `novel index-md` 生成（含 `generated_at`，请勿手改）。
- **动态表格**：`12_Dashboard/九州视图.base` 是 Obsidian Bases 视图（人物 / 势力 / 地点 / 物品 /
  敌人 / 设定 / 大纲），读 frontmatter，不依赖 Dataview。
- **仓库根 = Vault 根 = 本目录**（不再是 `NovelVault/` 子目录）。Obsidian 不允许仓库嵌套，
  Vault 一旦是某个 Vault 的子文件夹就打不开——所以路径不要再套一层。

## 编务团（WorkBuddy 专家团）

`novel-kb-team`「小说知识库编务团」把上述流程角色化：知识库总管 + 录入员 / 资料官 / 主笔 /
责编 / 记忆管理员 / 版本管理员 / 骰子娘，共 8 人，配套技能 `novel-vault-ops`（全部命令与纪律）。
装在 `~/.workbuddy/plugins/marketplaces/my-experts/plugins/novel-kb-team/`。

## 状态

- 验收测试 `AC-01 ~ AC-24`（33 个用例）+ 骰子娘 `tests/test_dice.py`（21 个用例）
- 示例故事《白骨渡》已整体归档至 `99_Archive/Legacy_Example_白骨渡/`，不参与索引
- 九州旧稿已录入：底层记忆 10 篇 + 世界观资产 684 个文档 / 1125 条记录
- 录入通道已实跑验证：切章 / 标注 / 抽取 / 批次回滚 / 快照标签均可用
- 规则引擎只管可判定事实；叙事质量由审核 Agent 给建议，不阻断

## 许可

本仓库内容（含正文、设定、代码）以 **GPL-3.0** 发布，见 [LICENSE](LICENSE)。
