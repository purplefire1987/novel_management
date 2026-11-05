---
type: note
name: Obsidian 插件配置说明
canon_status: confirmed
created_at: '2026-11-05'
updated_at: '2026-11-05'
---

# Obsidian 插件配置说明

> 三个社区插件已装好并配好，配置随库一起走（`.obsidian/plugins/*/data.json`）。
> 换机或重装后，只要 `.obsidian/` 跟着仓库走，不用重新点一遍设置。

## 一、Dataview：把库当数据库查

- 配置：`.obsidian/plugins/dataview/data.json` —— 已开 `enableDataviewJs`、`enableInlineDataviewJs`、`prettyRenderInlineFields`。
- 入口页：[[12_Dashboard/看板|知识库看板]]。九节查询，覆盖档案分布、`canon_status` 分层、待定清单、最近改动、素材库存、缺别名、孤立档案、连通块、章节进度。
- 写法约定：
  - 普通聚合用 DQL（`TABLE ... FROM ... WHERE ... GROUP BY`），够用且快。
  - 要算「入链」和「连通块」这类图指标只能用 `dataviewjs`——DQL 拿不到反向链接。
- 与命令行的分工：**看板看分布，`novel integrity` 看逐条问题**。两者不重复。

## 二、Templater：建档不再手抄 ID

- 配置：`.obsidian/plugins/templater-obsidian/data.json` —— `templates_folder` 指向 `11_Templates`，`trigger_on_file_creation: false`（不抢新建流程）。
- 模板：[[11_Templates/T-新建档案|T-新建档案]]
  - 选类型（人物／地点／势力／物品／生物）→ 扫目录算下一个编号 → 自动改名并移进对应目录 → 生成骨架。
  - 编号规则与 `00_System/Config/id_rules.yaml` 一致：`CHAR-0007`、`LOC-0112` 这种。
  - 产出的档案是 `canon_status: proposed`，等你确认后改 `confirmed`。
- 怎么用：命令面板 → **Templater: Insert Template** → 选 `T-新建档案`。建议给它绑一个快捷键。
- **不要**把 `11_Templates/Character.md` 这类改成 Templater 语法：那些是 `novel new` 命令用的占位符模板（`{{id}}`、`{{name}}`），引擎按字符串替换，改了会坏。

## 三、InfraNodus Advanced Graph View：看结构缺口

- 用途：把双链画成主题团，标出**两团之间「该连没连」的缺口**——这正是「人物关系够不够密」的直观答案。
- 打开：命令面板 → **InfraNodus: Advanced Graph View**；搜索框可限定 `path:01_Core_Memory/Characters`。
- 读法：色块＝主题团；虚线＝结构缺口；度数最高的节点＝把几团黏住的桥。
- 配套：[[07_Entity_Graph/图谱分析|图谱结构分析]] 记了同一天的量化结果与复算方法。**看图形，对数字。**

## 四、一个必须知道的陷阱

含派生页算出来的连通性会骗人。`实体分布` 与 `Entity_Index` 是自动生成的，几乎链到每个实体，
在图里会把 700 多个节点连成一团——那是「索引感」，不是「关系」。
做结构判断时先在搜索框排除 `07_Entity_Graph` 与 `01_Core_Memory/Maps`，或者直接看 [[07_Entity_Graph/图谱分析|图谱分析]] 里剔除后的数字。

## 关联

- 看板：[[12_Dashboard/看板|知识库看板]]
- 图谱：[[07_Entity_Graph/图谱分析|图谱结构分析]]
- 工作台：[[12_Dashboard/Home|九州·工作台]]
