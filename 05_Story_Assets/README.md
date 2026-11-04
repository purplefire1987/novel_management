---
type: note
title: 素材库使用说明
name: 素材库使用说明
tags:
- 规范
- 素材
---

# 素材库使用说明（05_Story_Assets）

## 这个目录是什么

存**可复用的写作素材**，不是事实档案。判断标准一句话：

> 能进 `01_Core_Memory` 的是「世界是什么」（事实，唯一）；
> 只能进这里的，是「世界怎么写」（样本，可多份、可互相矛盾）。

一个东西只能有一份事实，但可以有很多份写法。

## 为什么录入员（`novel ingest`）不往这里写

`novel ingest` 的职责边界是 **原稿 → 章节**：

| 它做的事 | 落点 |
| --- | --- |
| 按标题切章 | `08_Manuscripts/Volume_XX/CH-XXXX.md`（正文） |
| 建空大纲骨架 | `04_Outline/Chapters/CH-XXXX.md` |
| 写批次日志 | `10_Change_Log/Ingest/INGEST-*.md` |
| 留存原件 | `99_Archive/Raw_Sources/` |

代码里没有任何一条路径写 `05_Story_Assets`——整个 `novel_system` 源码对这个目录的引用数为 0。
设计上它属于**人工/半自动**环节：从已入库的正文里挑段落、归类、标注可复用点。
`import-lore`（设定稿）、`import-assets`（Excel 资产库）也一样，都只写 `01_Core_Memory`。

所以你看到的是「管道通了，但提炼那一步没人做」——不是漏了，是从来没建过。

## 各子目录放什么

| 目录 | 放什么 | 举例 |
| --- | --- | --- |
| `Settings/` | 空间/组织/规则的可复用设定块 | 已有 `ASSET-0001_明工造工坊设定` |
| `Battles/` | 打斗段落样本，按「兵器/人数/地形」标注 | 短兵相接、以一敌多、雨夜巷战 |
| `Dialogue/` | 对话样本，按「人物口径」标注 | 石季崇的审问腔、项月溪的女学生腔 |
| `Emotional_Beats/` | 情绪节拍样本，按「情绪→动作」标注 | 恐惧时的生理反应写法 |
| `Sensory/` | 五感样本，按「感官→词群」标注 | 雨、铁锈、血腥、灯油 |
| `Highlights/` | 自认写得好的句子/段落，标为什么好 | 开篇第一段、反转句 |
| `Templates/` | 素材卡片模板 | — |

## 写作纪律

1. **一条素材一张卡**，frontmatter 必带 `source_refs`（指回正文的 `CH-XXXX` 或旧稿 `CH-10xx`）。
2. 引用旧稿的，`canon_status` 一律 `deprecated`：可以学语感，不能当事实依据。
3. 卡片里**引原文要加引号并标出处**，改写过的另起一段，不要混着写。
4. 素材之间可以重复、可以冲突；一旦涉及"事实"，请改去 `01_Core_Memory` 对应档案。

## 现在缺什么

五个目录（Battles / Dialogue / Emotional_Beats / Sensory / Highlights）是空的。
旧稿 `99_Archive/Original_Draft/CH-1001..1028`（5.5 万字、28 节）就是现成的素材源，
先从里面抽卡片，比凭空造样本可靠。
