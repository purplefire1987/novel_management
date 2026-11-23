# 接手成本评估（2026-11-22）

> 视角：一个能力强、但对本项目零上下文的工程师，明天接手。
> 目的不是挑刺，是**找出「必须问人才能动」的地方**。能自己读懂的混乱只是烦，
> 读不懂又不敢动的才是成本。
>
> 全部数字现取，不引用旧小结。

## 一、量化的接手成本

| 维度 | 现数 | 说明 |
|---|---|---|
| 源码 | **25,040 行 / 91 文件 / 23 个包** | 不含测试与文档 |
| CLI | **36 条命令 / 18 个 `cli/*.py`** | 命令表 780 行 |
| 测试 | 4,513 + 4,454 + 547 + 214 行 | 4 层，`def test_` 578 个 |
| 文档 | 29 份 md，`known_risks.md` 744 行 / 60 条 | 风险登记本身是必读材料 |
| 最大单文件 | `maps/jiuzhou.py` 1,275 行 | `release/engine.py` 1,202 行次之 |
| 最大单函数 | `build_parser` **594 行** | 87 个 `add_parser` |

**改一处要读多少行**（这是最直接的指标）：

| 任务 | 必读文件 | 合计 |
|---|---|---|
| 改 `validate` 的一条检查 | `cli/__init__.py` + `cli/read.py` + `validation/{closure,dependencies,contract}.py` + `ids.py` + `markdown.py` + `indexing/builder.py` | **3,646 行 / 8 文件** |
| 改发布流程一步 | `cli/release_cmd.py` + `release/` 七个文件 + `approvals/` + `staging/` + `baseline/` + `audit/` | **3,928 行 / 12 文件** |
| 加一条 CLI 命令 | `cli/__init__.py`（594 行函数里插）+ 对应 `*_cmd.py` + `_common.py` | 至少 1,000 行 |

对 25,000 行的库来说这个比值偏高：**两个最核心的子系统各占全库 15%**，
且都跨越 8 个以上文件。

## 二、五类具体问题

### A. 名字说的事，和代码干的事不一样

这是最贵的成本——**它让人相信错误的地图**。

| 位置 | 名字承诺 | 实际内容 | 后果 |
|---|---|---|---|
| `cli/read.py`（549 行） | `read` 命令 | `cmd_doctor` / `cmd_status` / `cmd_validate` / `cmd_diff`。**`novel read` 根本不存在** | 想找读文件的实现，会在这个文件里绕很久 |
| `validation/proposals.py`（559 行） | 提案**校验** | docstring 首行：「记忆提案：从已批准正文**提取**」——是业务功能 | 以为 `validation/` 只做校验的人，会在这里遇到 LLM 信封校验 |
| `frontmatter.py` | docstring：「frontmatter 的**唯一**解析实现」 | 只取顶层键值（第 35 行注释：缩进行跳过）。**`markdown.py:87 load_meta` 是第二条路**，走 PyYAML 解嵌套 | 改 `frontmatter.py` 以为影响了全库，实际 `markdown.py` 那 10 处调用不受影响（`frontmatter` 3 处 / `markdown` 10 处） |
| `cli/flow.py`（343 行） | 「流程」 | `task` `stage` `approve` `adopt` `publish` | 与 `cli/work_cmd.py`（`plan` `write` `review` `proposal` `deps`）、`workflows/`（1,691 行）**三个都叫「工作流」**。改「批准」该动哪个？ |
| `core/` | `docs/developer/code_layout.md:82` 写「预留扩展位（T11 之后）」 | 空包，**零引用** | 文档承诺了一个不存在的东西 |

`validation/__init__.py` 只导出 `closure` / `dependencies` / `proposals` 三项，
其中两项是校验、一项是提取——包名与内容已经脱钩。

### B. 一个概念，四处实现：「批准」

新人最容易问、也最难自己搞清的一条。

| 模块 | 行数 | 它管的那一层 |
|---|---|---|
| `approvals/__init__.py` | 223 | 批准记录**存储与指纹校验**（`target_type` / `target_hash`） |
| `workflows/text_approval.py` | 134 | 正文批准**业务**：先查审核闸，再绑字节哈希 |
| `validation/proposals.py` | 559（含 `verify()`） | 记忆提案的批准**前置校验**（`STALE` 判定） |
| `release/gates.py` | 92 | 发布**闸门** |

四层抽象本身是对的（存储 / 业务 / 提案 / 流程），**但没有任何一处文档说明这四层的关系**。
`approvals/`（通用）与 `workflows/text_approval.py`（专用）看名字像重复实现，
读代码才发现是调用关系。这一条会让人反复重读。

### C. 抽象倒置：`maps` 用 argv 串回自己的 argparse

`cli/maps_cmd.py` 只有 62 行，看起来是干净的薄壳，实际是：

```python
# cli/maps_cmd.py:50,60
rc = admin.main(["--confirm"] if not dry else [])      # ← 拼 argv
return jiuzhou.main(argv)                              # ← 二次 argparse
```

于是 `maps` 参数存在**三处**：
1. `cli/__init__.py` 的 `p_maps` 定义（用户看见的）
2. `cli/maps_cmd.py` 拼 argv（翻译层）
3. `maps/jiuzhou.py:1122` 的 `main()` 里第二套 argparse（真正的解析）

改一个参数要同步三处，且第 3 处没有任何测试直接覆盖（测试都走 CLI）。
正确的形态是 `jiuzhou.build(paths, confirm=True)`——**库函数接收参数，不接收 argv**。

`maps/` 两个模块合计 1,817 行，其中两处 `main()` 共 **425 行**属于这套自造的命令行。

### D. 巨型入口：两个「不敢动」的函数

| 函数 | 行数 | 为什么不敢动 |
|---|---|---|
| `cli/__init__.py:build_parser` | **594** | 87 个 `add_parser` 全在一个函数体里，局部变量 `p_validate` / `p_state` / `p_in` 等 40 多个，靠注释分 10 个区。**加一条命令必须在这 594 行里找位置** |
| `release/engine.py:execute` | **319** | 22 步线性过程，引用 `Journal` `apply_changes` `snapshot` `plan.changes` `ApprovalStore` `contract_mod` `ReleaseManifest` `ActivePointer` `Receipt` 九个概念 |

`build_parser` 是纯粹的机械成本——594 行里没有一句业务逻辑，却因为单函数
不可分段阅读。`execute` 不同：它的顺序**就是**语义（第 4/5/7 步的注释明确写了
什么不能重排），拆分要非常小心，见第四节。

### E. 库里的垃圾与空壳

- `src/novel_system/maps/hy8j74o9`、`rs67h2x8`：各 4 字节 ASCII。
  沙箱 `tempfile` 回退的产物，**进了源码目录**。`git status` 里是未跟踪，
  但会在 `find`/`grep` 结果里制造噪音。
- `core/`：空包 + 28 字节 `__init__.py`，零引用，文档却说它是扩展位。

## 三、简化方案

> **执行状态（2026-11-23，0.15.4/0.15.5）**：本节 1–5 条全部执行完毕。
> 逐条结果与判据见 `docs/architecture/legacy_inventory.md` §九——**那里是唯一
> 权威的处置记录**，本文保留原样作为「当时怎么判断的」的存档。
> 一处需注意：第 1 条的候选名 `health_cmd.py` 未采用，实际用 `inspect_cmd.py`，
> 并在 docstring 里写明它与 `novel inspect` 命令的分工。

按「收益 / 风险」排序。档 1 不改任何行为，档 2 改结构，档 3 要作者裁定。

### 档 1：零行为改动（半天，可当天验完）

| # | 动作 | 判据 |
|---|---|---|
| 1 | `cli/read.py` → `cli/inspect_cmd.py`（或 `health_cmd.py`） | `git mv` + 两处 import；`novel --help` 输出逐字不变 |
| 2 | `validation/proposals.py` → `workflows/proposals.py` | 它依赖 `workflows` 与 `agents`，反向依赖已存在；移动后 `validation/` 只剩真校验 |
| 3 | `frontmatter.py` docstring 改成「**顶层键值**的轻量解析；嵌套结构走 `markdown.load_meta`」 | 一个文件、一句话 |
| 4 | 删 `core/` 与两个垃圾文件 | grep 零引用；`python -m novel_system --help` 不变 |
| 5 | `code_layout.md:82` 删掉「core/ 预留扩展位」 | 文档里不再承诺空气 |

档 1 全部做完，第 A 节五条问题消掉四条。

### 档 2：结构拆分（1–2 天）

**2.1 拆 `build_parser`（594 → 6 个 `_add_*`，各 60–120 行）**

纯机械，风险极低：所有局部变量都是 `Parameter` 对象，函数内无状态。

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novel", ...)
    sub_common = _common_parent(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    for add in (_add_read, _add_ids, _add_flow, _add_approve,
                _add_release, _add_agents, _add_work, _add_maps):
        add(sub, sub_common)
    return parser
```

**验收判据（必须用这个）**：拆前 `novel --help`、`novel <每条命令> --help`
共 37 份输出存成文件；拆后逐份 `diff`。**必须 0 差异**——argparse 的帮助文本
顺序、缩进、默认值展示都会变，靠人眼看不出。

**2.2 把 `maps` 的 argv 串接改成函数调用**

```python
# 现在
jiuzhou.main(["--confirm"])
# 目标
jiuzhou.build(paths, confirm=True, maps_only=...)
```

`main()` 保留为脚本入口（`if __name__ == "__main__"`），从 CLI 路径上摘掉。
判据：`novel maps build --confirm` 改动的文件集与改前**逐字节相同**。

**2.3 `execute` 按发布阶段切成 4 个私有方法**

22 步可以自然分成：准备（1–5）/ 校验（6–13）/ 落盘（14–19）/ 切换与回执（20–22）。
拆法必须是「同一函数内的方法提取」，**不是**新模块——顺序语义留在
`execute` 的调用序里，一眼能看完。

判据：`tests/integration/test_t06_release.py` 全绿，
且 `journal` 记录的事件序列与拆前逐条一致。

### 档 3：要你裁定的两件

**3.1 「批准」四层要不要合并**

我的建议是**不合并，只补一张图**。四层是真实的分层（存储 / 业务 / 提案 / 流程），
合并会制造新的巨型模块。成本不在代码，在没人画这张关系图。
可以在 `docs/developer/code_layout.md` 加 10 行说明 + 一张表。

**3.2 `cli/flow.py` 与 `cli/work_cmd.py` 的边界**

两者都装「多步流程命令」。可选：
- 合成一个 `cli/flow_cmd.py`（5 + 5 = 10 条命令，约 800 行）——更集中，但更大
- 保持两个，改名成 `cli/author_flow.py`（作者主流程）与 `cli/agent_flow.py`
  （Agent 工作流）——**我倾向这个**：它们服务的角色本来就不同，名字该说出来

## 四、明确不建议动的

写这一节是因为上面所有建议都可能被过度执行。

1. **`release/engine.py` 的 22 步顺序不能拆成独立模块。** 第 4/5/7 步的注释
   已经写过原因：`build_snapshot` 会先 `rmtree` 再全量复制，重排会把前三步作废，
   且在禁删环境必炸（R-56）。顺序即语义，拆散后没人能再确认「这个顺序是对的」。

2. **`known_risks.md` 744 行 / 60 条不是负债。** 它是这个项目唯一一份
   「为什么当初不那样做」的记录。删掉它，上面第一节的所有成本会翻倍。

3. **ID 号段空洞（1007 个）是设计。** PRD 明写「分配后不变、空号保留」。
   看到 `CH-000003..000030` 是空号就想回收，会毁掉「按号段判断内容年代」。

4. **`agents/preflight.py` 1,091 行看着长，内聚性没问题。** 它是「一个资料包的
   多个槽位」，`_fill_*` 系列函数职责同构。拆成多个文件只会让人多跳几次。

5. **参数风格已经一致，不要"统一"它。** `--confirm` 33 处、`--force` 4 处、
   `--dry-run` 6 处——语义各有分工（确认落盘 / 越过拒绝 / 只看不写），
   不是不一致。

## 五、一句话结论

**代码本身不难读**（docstring 密度高、决策理由都写在头部注释里，这在新项目里罕见）。
真正的成本是**导航**：36 条命令散在 18 个文件，两个核心子系统各跨 8–12 个文件，
且有三处「名字与内容不符」在持续误导。

档 1 的 5 条动作（半天、零行为改动）能消掉大部分导航成本。
档 2 的机械拆分能把「不敢动的函数」从 2 个降到 0 个。
档 3 那两件，需要你定。
