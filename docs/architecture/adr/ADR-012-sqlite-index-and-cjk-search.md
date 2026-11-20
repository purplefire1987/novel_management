# ADR-012：索引改用 SQLite，检索按字符长度分派

- **状态**：已接受
- **批准人**：作者
- **日期**：2026-11-20
- **相关**：ADR-002、ADR-003（本篇部分取代它）、ADR-011

## 背景

T03 要把「00_Canon 解析与索引」做出来。PRD §16.1 要求 17 张表（产物、实体、别名、
正文块、断言、认知、伏笔……），§16.2 要求中文检索在**短词与长词上都能用**，
§16.4 要求指定 revision 重建且**不得在读者正在用的库上原地重建**。

ADR-003 当初选的是 `index/index.json` 单文件，并把「SQLite 作索引」列入备选但未采纳，
理由是「索引损坏时排查成本高；JSON 十万条以内足够，且可肉眼比对」。

到了 T03 这个理由不再成立：

- 正文块（`blocks`）不是「十万条以内」。一份长章按自然段切就是几百块，
  真实 Vault 1087 份 Markdown，块级索引是十万量级起步；
- 中文子串检索没法在 JSON 上做。要么全量扫内存，要么自己写倒排——后者等于重新实现 FTS；
- §16.1 的 17 张表是关系型形状（外键、一对多、边表），塞进单文件 JSON 会退化成嵌套数组，
  每次查询都要在 Python 里手写连接。

## 问题

三个必须同时成立，而且互相牵制：

1. **零依赖**（`dependencies = []`，ADR-011 同理）。不能引 ORM，也不能要求用户装 FTS 扩展。
2. **中文检索不能假装可用**。用默认的 `unicode61` 分词器，中文整段切一个 token，
   `"握住了刀柄"` 只有原句一字不差才命中——功能看起来是开着的，实际检不出东西。
3. **读者不能读到半成品库**。重建要跑几秒到几十秒，期间如果有别的命令在读，
   不能让它读到写了一半的表。

## 决策

### 一、索引落 SQLite，走标准库 `sqlite3`

`<runtime>/index/builds/<build_id>.sqlite`，一次重建写一个新文件。
表结构见 `indexing/schema.py`，17 张表照 §16.1 一字不差地建，其中：

- **8 张本轮会填**：`index_metadata` `artifacts` `entities` `aliases` `source_refs`
  `narrative_order` `blocks` `fts_blocks`；
- **9 张建空表**：`events` `event_edges` `assertions` `state_changes` `knowledge`
  `revelations` `foreshadowing` `dependencies` `issues`。

**「建了空表」不等于「已支持」。** 这件事写进库本身（`index_metadata.populated` /
`index_metadata.empty`），也写进 `rebuild-index` 的输出，避免下一轮的人把
「表在」读成「功能在」。空表只保证查询不会因为表不存在而报错。

ADR-003 的核心结论不变：索引仍然**纯派生**、随时可删、不反向写事实源。
被取代的只是「用 JSON 单文件」这一个载体选择。

### 二、中文检索按查询长度分派

| 查询形态 | 走哪条路 | 为什么 |
|---|---|---|
| ID / 别名精确命中 | `identifiers` 精确查 | 用户输了完整 ID，就该精确返回，不该按子串糊一片 |
| 长度 ≥ 3 | FTS5 `trigram` | trigram 把文本切成三字窗口，中文子串天然可查 |
| 长度 < 3 | 参数化 `LIKE '%..%' ESCAPE '\'` | trigram 切不出两字 token，硬走 FTS 会**静默返回 0 条** |

**关键点：两字中文词必须由 LIKE 兜住。** `trigram` 的窗口是三字符，`林砚` 只有两个字符，
在 FTS 里根本切不出 token。这不是实现瑕疵而是 tokenizer 的定义；`tests/unit/test_index_schema.py`
里有一条用例专门钉住这个行为（断言两字在 FTS 下命中数为 0），防止有人把它「修」成走 FTS。

LIKE 路径要转义 `\ % _` 三个字符并显式 `ESCAPE '\'`——人名里出现 `%` 不常见，
但用户搜 `第1_章` 时会因为 `_` 是通配符而命中一片无关结果。

### 三、结果分层：派生物排在事实源之后

排序不能交给路径字典序。`00_System/Dashboards/` 下由 `maps build` 生成的看板页
因为 `00_` < `10_` < `90_` 永远排最前——真实库上搜「陈默」，前三条全是
`工作台.md` 的摘录，`CHAR-0001_陈默.md` 被挤到看不见。

于是按来源分三层：**作品内容（`10_Projects/`）0 > 共享资产（`90_Shared_Assets/`）1 >
系统文档与生成看板（其余）2**，层内再按 FTS `rank` 或块序号。

这不只是观感问题。看板页是从档案**派生**出来的，让人先引用派生页，等于在索引里
造了个循环：查到的答案其实是别处的摘要，而摘要的源头还要再查一次。
真正的事实源必须先出现。

### 四、FTS5 不可用时明确退化，不偷换分词器

探测不到 `trigram` 时：**不用 `unicode61` 顶上**，而是整库不建虚表，
`fts_mode=none`，全部查询走 LIKE。慢，但结果对。

反过来「用 unicode61 保证 `search` 能用」是更糟的选择：它会让 `doctor` 报「FTS 可用」、
让 `rebuild-index` 报「已建 FTS」，而作者搜两字人名得到 0 条却不知道为什么。
降级原因写进 `fts_blocks_unavailable` 表和 `doctor` 输出，可查。

### 五、不原地重建：一份 build 一个文件，只切换指针

`index/active.json` 是唯一的「当前用哪个库」的指针：

```json
{"build_id": "...", "db": "builds/<id>.sqlite", "canon_revision": "...", ...}
```

重建全程不碰已有文件。写完新库 → 校验 → 用 `write_text_atomic` 改指针。
读者要么拿到旧指针、要么拿到新指针，不存在中间态。

比「写临时文件再 `os.replace`」更彻底：`os.replace` 只有一个原子点，
一旦在替换前崩溃，临时文件还躺在目录里需要人清；而多 build 布局下，
**崩溃留下的半成品永远只是多一个没人指向的 `.sqlite`**，删不删都不影响正确性。

代价是旧 build 不会自动清理（见 R-18）。这是刻意选的：索引是派生物，
磁盘占用远比「删错了东西」便宜。

### 六、revision 必须来自被索引的树本身

`rebuild-index` 支持 `--canon-revision <commit>`：从 `git archive` 直接读该 commit 的
Markdown，**不落临时目录、不切工作树**。索引里记的 `canon_revision` 就是这次实际读的那个。

有个坑：`git -C <dir> rev-parse` 会**向上找外层仓库**。Vault 若不是自己就是仓库根，
临时库建在公仓子目录下会认成公仓的 HEAD，索引会声称自己在某个根本没被读过的 revision 上。
所以 `source.py` 里 `_own_repo()` 先用 `--show-toplevel` 校验 Vault 自己就是根，
不成立就明确报错而不是猜。

### 七、不索引哪几个目录，由配置说了算

排除 `07_Staging`（Agent 提案区）、`08_Reviews`（审阅意见）、`90_Archive`（项目内旧版）、
以及 Vault 根归档目录。**归档目录名从 `Vault_Config.yaml` 的 `layouts.archive` 取**，
不写死在代码里。

这条是踩出来的。最初常量写的是 `("07_Staging", "08_Reviews", "90_Archive")`——
看着没问题，因为 V4.0 项目内确实有 `90_Archive`。但 Vault 顶层的归档叫
**`99_Archive`**，于是整个归档（旧 PRD 2611 行、旧实体索引 75KB）被静默索引进库：
真实库上产物多 6 份、块多 928 块。**注释声称「跳过运行态/归档」，实际一份没跳过。**

教训有两条：

1. 目录名不许写死在代码里（`code_layout.md` 三条约定之一）。这类错误不会报错，
   只会让索引悄悄多出一堆不该被当作依据的内容；
2. **「跳过了多少份」必须打印出来**。原先只打行数、不列名单，
   多跳一个少跳一个都看不出来。现在打「跳过运行态/归档 N 份（目录名清单）」。

## 备选方案

| 方案 | 为什么没选 |
|---|---|
| 继续用 `index.json` | 块级索引十万量级，每次查询全量扫内存；中文子串检索无从下手 |
| SQLite + `unicode61` 分词 | 中文按整段切词，两字以上任意子串都检不出，且会谎报「检索可用」 |
| 引 `jieba` 或 `sqlite-vec` 做分词/向量 | 破坏 `dependencies = []`；分词结果还会随词典版本漂移，索引不可复现 |
| 常驻进程 + 内存索引 | 违背「作者机器上长期无人值守」前提（ADR-003 已否） |
| 临时文件 + `os.replace` 原子替换 | 只有一个原子点，崩溃留垃圾；且换库期间仍可能被读者撞见半成品 |
| 每次原地 `DELETE` + 重插 | 读者会看到空库；一旦重建中途失败，读者手上就没有索引了 |

## 影响

- 索引目录从「一个 JSON」变成「指针 + builds 目录」，`status` 会打印当前指向哪一份。
  旧 `index.sqlite` 仍兼容读取（`locate.active_db()`），不强制迁移。
- **真实 Vault 实测**：1067 份产物 / 12867 块 / 单份库 **11.5 MiB（12.1 MB）**。
  盘上会积累历史 build（R-18），需要作者偶尔手动清 `builds/`——不自动删是刻意的，
  但 `status` 目前只报份数不报总占用，堆到几百 MB 之前不会有人注意。
- `blocks` 表是有损的：只保留块级文本与行号，不保留原始字节。要原文回指 Markdown。
- 块 ID 与行号只对「生成那一刻」的文件成立。作者手改正文后，同一 `p0001` 可能已指向别的段落
  （R-19）。跨时间引用要带文件 sha256，不能只写块 ID。
- FTS5 是 SQLite 编译期选项。本机 `sqlite3 3.50.4` 有 trigram；
  换到没有的机器上功能降级但不报错——`doctor` 会明说。
- 库文件损坏的排查成本确实比 JSON 高。缓解方式是「删掉整个 `index/` 重跑」，
  因为它是派生物，这条路径永远安全。

## 验收方式

- `tests/unit/test_index_schema.py`（17 条）：17 表齐全、trigram 探测、退化分支、触发器同步、
  两字词在 FTS 下命中为 0（钉住分派依据）。
- `tests/unit/test_index_build.py`（15 条）：块 ID 与行号、坏文件不中断、跳过 Staging/Archive、
  **Vault 顶层 `99_Archive` 也跳过**、模板占位符不算重号、**不覆盖正在用的索引**、
  git revision 源只读该 commit、不误认外层仓库。
- `tests/unit/test_index_search.py`（18 条）：两字走 LIKE、三/四字走 FTS、中英混合、ID 精确、
  名称与别名路径、`LIKE` 通配符转义、FTS 不可用时的 note、
  **作品内容排在生成看板之前**、命中带原文行号。
- `tests/integration/test_t03_cli.py`（12 条）：端到端 rebuild + search，
  以及 **`00_Canon` 整树 sha256 前后一致**（本任务不改事实源）。

两条关键回归做了「退回修复必须失败」的验证：把 `order by` 改回原样，
`test_project_content_ranks_before_generated_dashboard` 精确失败并报出
「第一条是 00_System/Dashboards/工作台.md」，改回来即通过。
全绿只说明测试没意义——这一步不能省。
