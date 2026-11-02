---
id: MEMORY-CH-0002
type: audit_report
report_type: Memory_Update
target: CH-0002
verdict: PROPOSED
generated_by: memory_manager/offline
created_at: '2026-11-03T04:33:42+08:00'
proposal_count: 6
---

# 记忆提取｜CH-0002

- 运行模式：offline
- 提案数：6
- 提案文件：`.novel_system/staging/memory/CH-0002.proposals.yaml`

## 提案清单
| 提案 | 类型 | 操作 | 目标 | 审批级别 | 证据 |
|---|---|---|---|---|---|
| PROP-CH-0002-01 | event | create | EVT-CH-0002-001 | auto | 沈砚看了一眼那线红，又看了一眼石阶下面的河。 |
| PROP-CH-0002-02 | character_state | update | STATE-CHAR-0001 | author | 沈砚看了一眼那线红，又看了一眼石阶下面的河。 |
| PROP-CH-0002-03 | character_state | update | STATE-CHAR-0002 | author | 苏未晞站在他身后半步，没有出声。 |
| PROP-CH-0002-04 | character_state | update | STATE-CHAR-0003 | author | "老陆说。 |
| PROP-CH-0002-05 | foreshadowing | update | FORESHADOW-0002 | author | "那晚我自己砍的。 |
| PROP-CH-0002-06 | summary | update | SUMMARY-CH-0002 | auto | CH-0002（作者备注标注，正文对应句段需人工确认） |

## 校验问题
无

## Agent 说明
# 记忆提取｜CH-0002

- 运行模式：offline（未配置 LLM）
- 提案数：6

## 事件提案
- EVT-CH-0002-001｜河心灭灯

## 状态变化提案
- STATE-CHAR-0001｜{'location': '白骨渡河心船上', 'injury': '左肩旧伤渗血至手腕，未处理', 'objective_status': '存活', 'mind': '', 'ability': '断水刀已六裂，再承载一次将断裂', 'holdings': '', 'awareness': ['西岸方向有一盏灯正在向船靠近'], 'unknown': ['老陆断指的真正原因'], 'goal': '', 'as_of_event': 'EVT-CH-0002-001'}
- STATE-CHAR-0002｜{'location': '白骨渡河心船上', 'injury': '右腕擦伤已结', 'objective_status': '存活', 'mind': '', 'ability': '', 'holdings': '', 'awareness': ['灯在移动说明有人持灯'], 'unknown': ['沈砚对师父之死的判断'], 'goal': '', 'as_of_event': 'EVT-CH-0002-001'}
- STATE-CHAR-0003｜{'location': '白骨渡河心船上', 'injury': '无新伤', 'objective_status': '存活', 'mind': '', 'ability': '', 'holdings': '', 'awareness': ['河心的灯昨夜转了一夜'], 'unknown': ['沈砚已怀疑师父上过西岸'], 'goal': '', 'as_of_event': 'EVT-CH-0002-001'}

## 冲突与待确认
- 无

> 离线模式只读取正文「作者备注」中的结构化标注（`- 事件: ...` / `- 状态: ...` / `- 关系: ...` / `- 伏笔: ...` / `- 摘要: ...`），不做语义推断。
