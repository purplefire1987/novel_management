# ADR-011：字段契约以 JSON Schema 为真源，校验器自己实现

- 状态：已接受
- 日期：2026-11-20
- 批准人：作者（在「完成 T02」的任务指令中确认）
- 相关：ADR-003（索引不是事实源）、PRD §8.4、PRD §23 T02

## 背景

PRD §8.4 要求提供「Pydantic 模型 + 导出的 JSON Schema + 每类有效/无效 fixture」，
验收是三条：缺字段被拒绝、非法枚举被拒绝、未知字段被拒绝。

本项目 `pyproject.toml` 的 `dependencies = []`——零第三方依赖是既定架构，
`frontmatter` 模块甚至在没装 PyYAML 时会降级到标准库解析。装 `pydantic`
或 `jsonschema` 会让「能跑」变成一个需要联网装包的前提。

## 问题

契约要能执行，还是只要写出来？

- 只写 schema、不接校验器：`schemas/` 就只是一堆文档，T02 的三条验收一条都过不了。
  本仓库上一版正是这个状态：6 份 schema 躺了半年，零代码引用。
- 引第三方校验库：能过验收，但破坏零依赖，且 `pip install` 失败的机器上
  连 `novel validate` 都跑不起来。
- 自己写校验器：多约 300 行代码，但零依赖、行为可测、报错可中文化。

## 决策

1. **JSON Schema 是唯一真源**，放在仓库根 `schemas/*.schema.json`。
   共享片段放 `schemas/_common.schema.json` 的 `$defs`，用 `$ref` 引用。
2. **校验器自己实现**（`novel_management/schema.py`），覆盖 draft 2020-12 的实用子集：
   `$ref/$defs/type/enum/const/pattern/format/长度/数值区间/items/required/properties/
   additionalProperties/oneOf/anyOf/allOf/not`。不支持的 keyword 当注解忽略，
   并在模块 docstring 里列明不支持清单——不假装支持。
3. **不引入 pydantic 作为运行时依赖**。本机装了 pydantic 2.14，但那是环境巧合，
   不能成为代码前提。
4. **通用元数据字段（artifact_type/artifact_status/schema_version/…）不抽成 `$defs`**。
   JSON Schema 的 `additionalProperties: false` 不会继承父级 `properties`，
   抽出来会让每份 schema 都把全部字段判成「未知字段」。这 8 个字段在每份 schema
   里各写一遍，一致性由 `tests/unit/test_schema_contract.py` 守。

## 备选方案

- **A. 以 Pydantic 模型为真源，导出 JSON Schema**：类型提示更舒服，
  但 pydantic 成为必需依赖；且导出的 schema 带大量 `$defs`，自己写的校验器
  反而要支持更多特性。
- **B. 引 `jsonschema` 库**：最省事，但本机没装，且破坏零依赖。
- **C. 只写 schema 不校验**：等于没做 T02。上一版就是这个状态，已证伪。

## 影响

- 新增 22 份 schema（6 份原有加严 + 16 份新增），全部 `additionalProperties: false`。
- 校验器约 330 行，测试 93 条。新增类型忘了写 fixture 会直接测试失败。
- **与 PRD 的两处偏离，已记入 `known_risks`**：
  1. 编号位数按现状四位（PRD 写六位），schema 放宽到 4–6 位兼容两种；
  2. 通用元数据字段名沿用现状（`type`/`canon_status`），PRD 的
     `artifact_type`/`artifact_status` 以可选字段并存，迁移未做。
- `entity` schema 的字段面按库内实情收敛（966 份里 `subtype`/`world`/`canon_status`
  是 100% 出现的），否则严格校验会让现有档案全部非法。**这不是把脏数据合法化**，
  是承认现状并标注迁移方向。
