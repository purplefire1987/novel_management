# ADR-017：Agent 权限以矩阵为唯一真源，提示词分段装配，Preflight 确定性优先

- 状态：已接受（2026-11-21）
- 关联：细化 ADR-006（Agent 默认写 staging）、ADR-014（批准绑定字节）；
  依赖 ADR-012（索引检索）、ADR-013（事件 DAG 与认知分表）；PRD §6、§13 与 T08 验收
- 取代：无

## 背景

T08 要把九角色接进流程，要回答五个问题，每个都容易答成「看起来对」：

1. **谁能做什么**。权限写在三个地方：人读版 `agents/common/permissions.md`、
   PRD §6.1 的表格、以及代码。三份副本迟早不一致，而不一致时**没人会收到报错**。
2. **提示词里到底有什么**。把「请遵守规范」写进提示词，模型看不出边界在哪；
   把整份规范塞进去，九份提示词各写一遍，改一处要改九处。
3. **资料装不下怎么办**。「超了」有两种：软资料可以少给，硬约束一条都不能少。
   两者用同一个「截断」处理，等于允许系统悄悄丢掉「这章必须死人」这条约束。
4. **没有模型能不能跑完**。装一个模型才能生成资料包，意味着没配 API 就用不了，
   而写前检查恰恰是最该随时能跑的。
5. **调用花了多少**。不记账就无法回答「这个月烧了多少」，也无法回答
   「这条资料是哪个角色在什么时候取出来的」。

## 决策

### 1. 权限矩阵的机器副本 + 逐格比对测试

`agents/registry.py` 里的 `Role` 字段**不是权限枚举，而是 `permissions.md` 那张表的五列本身**，
`matrix_row()` 一字不差渲染那一行。测试 `parse_permission_table()` 读 md 表格，
与 `role.matrix_row()` 逐格比对。

理由：三份副本里只有一份能参与运行。让代码那一份**长得跟文档一样**，
比对就退化成字符串相等；比对不过就是文档与代码真的不一致，不是「表述差异」。

PRD §6.1 的 A1–A9 按规范 v1.7 §4 作废（「剧情规划师」并入编辑，「一致性审校」
转为总编签字）。本版以 **v1.7 裁定后的九角色**为准，与 `audit.AGENTS` 同名同序。

### 2. 能力位从矩阵推出，不另维护工具清单

角色有六个能力位：`CAP_READ_CANON / CAP_READ_SOURCE / CAP_WRITE_STAGING /
CAP_PROPOSE / CAP_WRITE_DRAFT / CAP_PUBLISH`。`allowed_tools(role)` 由能力位推导。

于是「`ingestor` 拿不到任何读 Canon 的工具」不是一条单独规则，而是它只读源稿的
**结果**。想给它加一个读 Canon 的工具，必须先改它的读权限——改不动，
因为改动会立刻让权限矩阵比对测试红。

两道锁恒为 `False`，`ensure_can_write_canon()` / `ensure_can_release()` 永远抛
`SecurityError`。这不是「默认值」，是任何角色都改不了的两句话：
**Agent 不写 Canon，Agent 不发布**（ADR-006）。

### 3. 「R（限授权）」是执行层拦截，不是备注

`dice_mistress`（骰子娘）的读权限是「限授权」。实现上：它调任何工具都必须显式给
`scope`，否则 `SecurityError`。写「建议注明授权范围」在提示词里，等于没拦。

配套：`_check_params` 拒绝未知参数。拼错一个键（`entitiy_id`）会变成
「查不到」，而「查不到」长得像「库里没有」。宁可报错。

### 4. 提示词分九段装配，公共文本只写一遍

`prompts.build()` 产出固定顺序的 `Section` 列表：

```
role_card → task → release → anchors → tools → schema
          → uncertainty → citation → injection → no_claim → scope → (extra)
```

前九个键是 `REQUIRED_KEYS`，缺一个就 `UsageError`。五段公共文本
（`UNCERTAINTY / CITATION / NO_PROMPT_INJECTION / NO_CLAIM_WITHOUT_ACTION /
SCOPE_BOUNDARY`）是模块常量，九份提示词共用。测试断言九份提示词的公共前缀
**逐字节相同**——不同就说明有人往某一份里加了私货。

「不得宣称完成了未执行的动作」这条（`NO_CLAIM_WITHOUT_ACTION`）不是为了好看：
Agent 说「已写入第 12 章」而实际只写进 staging，作者会以为发布过了。

### 5. 预算两条不变量

`Budget` 三档：`context_limit` / `output_reserve` / `hard_reserve`。

**不变量一：软额度必须是「剩余」额度。**

```python
@property
def soft_allowance(self) -> int:
    remaining = self.context_limit - self.output_reserve - self._hard_used - self._soft_used
    return max(remaining, 0)
```

初版没扣 `_soft_used`，八个检索级各领一份完整配额，32000 的预算装进了 48000 的内容。
这不是「估得不准」，是**每一级都以为别人没花**。

**不变量二：硬约束一条都不丢，装不下就 BLOCKED。**

`_place_hard` 全部返回，只记 `Overflow(section, items, over_by)`；
`gate` 见 overflow 判 `BLOCKED`。`_place_soft` 才按顺序装到装不下，记
`Truncation(section, kept, dropped, total, reason)`。

配套的顺序调整：`_fill_foreshadowing` 挪到 `_fill_states` **之前**。
未回收伏笔属 PRD §13.3 必须检查项，排在软资料后面会出现
「因为读了太多无关档案而阻断写作」——那个阻断理由是错的，但系统给不出来。

`Truncation` 最终合并进 `coverage.truncated_results`（去重）。
初版只记在 `budget.truncated` 里，资料包里的 `truncated_results` 恒为空——
**截断在预算账上可见、在资料包里不可见**，等于没做 T08 第一条验收。

### 6. PRD §13.2 八级检索落成七次工具调用

精确 ID → 别名解析 → 结构化状态 → 事件与认知 → 关键词 → 近期正文 → 卷级摘要
→ 按需补查。前七级各对应一次 `ToolContext.call()`，第八级是「`truncated` 为真时
人再去查」，不自动展开。

PRD 那句「一次 Top-K 返回不得视为完整知识」落实为两个可见物：
`truncated` 布尔标志 + `coverage.truncated_results` 清单。CLI 每次打印都带一句
「有 N 处结果被截断（看 truncated_results，别当成『就这么多』）」。

检索命中数记在 `budget.report()` 里，`search-limit` 可调。`foreshadowing`
表从本版起真的会填（`POPULATED_TABLES`），`SCHEMA_VERSION` 2→3。

### 7. Preflight 默认不调模型，模型只做加值

默认路径纯确定性 Python（PRD §6.2 Orchestrator 的职责）：读规划文档、查索引、
放预算、判 gate。给 `llm` 时，资料官只做一件加值的事——把「必须发生 / 禁止发生」
提炼成结构化约束。

模型失败（输出不是 JSON、字段缺失、`role` 写错）时：**退回原文照录**，
记一条可读 issue（`资料官输出不可用：…`），`model.used=False`。不「尽力而为提取字段」。

理由：写前检查的结论必须是可复现的。同一份库、同样的参数，今天 PASS 明天 BLOCKED
而理由是「模型这次理解得不一样」，这个 gate 就没有权威性了。

### 8. 信封校验不猜

`runner.run` 三步：装配提示词 → 调模型 → 去围栏 / 解析 / schema 校验 / 核对 `role`。
任一步失败 `AgentRun.errors` 非空，`.ok` 为假。没有「自动修复 JSON」这条路。

`role` 核对单独列一条：角色写错会让审核记录归错人，比 JSON 语法错更难发现。

### 9. 计量落盘，只记元数据

`<runtime>/usage/YYYYMMDD.jsonl`，一行一次调用，只追加。
字段：角色、工具名、耗时、prompt/completion tokens、成败、`task_id`。
**不记正文**（与 `audit/ops.jsonl` 同一口径）。

`ToolContext` 自带 `meter`，`call()` 里统一记——**失败的调用也记**。
只记成功的账，等于把「试了三次才成」记成一次。

## 备选方案

- **权限只写文档，代码里 `if role == "writer"` 硬判**。这类判断会散落在十几个地方，
  加一个角色要改一圈，且漏改不报错。矩阵是「一处定义、处处推导」。
- **把 PRD §6.1 的 A1–A9 也实现成角色**。规范 v1.7 已裁定作废。
  实现两套角色表，等于给「到底谁是审核人」留两个答案。
- **提示词用模板引擎拼**。九个角色九份模板，公共文本复制九遍，
  改一句规范要改九处——第九处一定会漏。
- **截断与溢出合并成一个 `truncated` 标志**。那就分不出「少给了一段背景」
  与「丢了三条硬约束」。前者可以让作者接受，后者必须拦。
- **资料包装不下就自动压缩（摘要化）**。摘要由谁做？模型。那就回到了
  「结论依赖模型当时的状态」。宁可 BLOCKED 让作者调预算或减范围。
- **Preflight 必须配模型**。会让「写前检查」变成只有配了 API 的人能用的功能，
  而它恰恰是最该随手能跑的一步。
- **计量写进 `ops.jsonl`**。`ops.jsonl` 是操作审计（谁写了什么），
  用量是另一件事（花了多少），混在一起两边都不好查。

## 影响

- 新增 `src/novel_management/agents/`：`registry`（九角色与矩阵）、`budget`（预算与截断）、
  `tools`（能力位与七个工具）、`prompts`（九段装配）、`metering`（台账）、
  `runner`（信封与校验）、`preflight`（资料包主流程）。
- 新增 `src/novel_management/cli/agent_cmd.py`；命令行 28 → **30 条**
  （新增 `preflight`；新增 `agent` 命令组，含 `list`/`prompt`/`usage`）。
- 新增两份契约：`schemas/agent_envelope.schema.json`（PRD §6.3）、
  `schemas/preflight.schema.json`（PRD §13.4 十七项 + 预算/用量/索引）。
- 索引侧：`SCHEMA_VERSION` 2 → 3、`BUILDER_VERSION` 0.8.0 → 0.11.0，
  `foreshadowing` 从 `EMPTY_TABLES` 移入 `POPULATED_TABLES` 并补三列
  （`window_policy` / `window_end` / `reveal_scope`）。**老库要跑一次 `rebuild-index`
  才有伏笔数据**，否则 preflight 的「未回收伏笔」一栏恒为 0。
- `storage.dump_yaml` 重写成递归实现（原版只支持一层对象列表，`issues[].evidence[]` 会写坏），
  `_yaml_scalar(None)` 从 `""` 改为 `null`（否则 `base_release` 回读成空串，过不了 schema）。
- 已知限制：`state` 是伏笔生命周期的字段名，不是 `status`；索引 `project` 存目录名
  （`novel-001_smoke`）而非 `project_id`，等值比较会静默返回空，统一走 `project_clause()`。
  真库上 `foreshadowing` 要到第 3 版建库后才开始填。
