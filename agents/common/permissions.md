# Agent 通用权限（权威定义）

符号：`R` 读取 / `S` 写 staging / `P` 提变更 / `A` 批准 / `X` 禁止。

| Agent | Canon 读取 | Canon 写入 | staging 写入 | 正文草稿 | 正式发布/批准 |
|---|---:|---:|---:|---:|---:|
| editor_in_chief（总编） | R | X | S/P | X | X |
| world_architect（世界架构师） | R | X | S/P | X | X |
| editor（编辑） | R | X | S/P | X | X |
| dice_mistress（骰子娘） | R（限授权） | X | S/P | X | X |
| researcher（资料官） | R | X | S/P | X | X |
| writer（主笔） | R | X | S/P | S | X |
| memory_keeper（记忆管理员） | R | X | S/P | X | X |
| ingestor（录入员） | R（源稿） | X | S | X | X |
| version_manager（版本管理员） | R | X | S/P | X | X |

**作者是唯一最终批准主体。** CLI 可执行经验证的批准动作，但 CLI 不决定内容。

## 所有 Agent 无条件遵守

1. 只访问当前任务明确授权的项目目录；默认禁止跨项目。
2. Canon 只读；任何 Canon 变更只能走 staging + 作者批准。
3. 不覆盖、不删除、不移动 Canon、定稿与作者手写文件。
4. 不批准自己的输出。
5. 不把其他项目的内容当作本项目事实。
6. 不把推断写成事实；引用必须附路径、ID 或来源标记。
7. 任务结束必须报告：读了什么、写了什么、未解决什么、置信度。
8. 无法落实的权限不得声称已落实；用独立目录 + CLI 校验 + 人工复核补偿。

## 交接包必含字段

`task_id`、`project_id`、目标、已批准输入清单、输出清单、约束与禁止项、
当前阶段、验收条件、未解决问题、下一责任 Agent、是否需要作者批准。

禁止只以自然语言声称「已完成」——必须给出可定位的产物与校验结果。
