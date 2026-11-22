# 模板说明

本目录是**活资产**：`novel init-project` 会真读 `project/project.yaml`。
改模板 → 下一次新建项目即生效，不必改代码。

> 2026-11-23 之前不是这样：`init-project` 用的是 `cli/init.py` 里的字符串常量，
> 本目录的 `project.yaml` 从未被读过。那句「改模板没生效」就是这么来的。

## 谁读谁

| 模板 | 读取方 | 占位符 |
|---|---|---|
| `project/project.yaml` | `novel init-project` | `project_id` `project_name` `slug` `author_id` `created_at` |
| 其余（`entity/` `manuscript/` `memory/` `planning/` `author/` `reports/`） | 暂无代码读取，供作者手工复制 | 见文件内说明 |

模板目录可用环境变量 `NOVEL_TEMPLATES_DIR` 覆盖（测试与多份模板时用）。
找不到模板时 `init-project` **报错**，不回退到内嵌默认值——静默回退会让
「改了模板没生效」重新变成隐形问题。

## 写模板的三条规矩

1. **占位符只用 `{name}` 形式**，因为填充走 `str.format`；正文里要写字面花括号必须写成 `{{`。
2. **目录名必须与 `src/novel_system/paths.py` 的常量一致**。两边分叉时，配置说一套、代码找一套，
   表现是「文件建出来了，但命令说找不到」。
3. **不要写作品设定**。公仓按 GPL-3.0 公开，模板里的示例名一律虚构（如 `林砚`、`灰石渡`）。
