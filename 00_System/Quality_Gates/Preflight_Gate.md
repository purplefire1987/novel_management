# 写前 Gate（Preflight Gate）

章节从 `outline` 进入 `preflight_passed` 必须全部满足，否则阻断：

| 检查项 | 说明 | 不通过时 |
|---|---|---|
| `outline_exists` | `04_Outline/Chapters/<CH>.md` 存在且 frontmatter 合法 | BLOCKED |
| `outline_approved` | `## 作者批准` 中 `- [x] 已批准写作` | AUTHOR_APPROVAL_REQUIRED |
| `entities_resolvable` | 大纲中所有实体 ID 都能在 Vault 找到 | 记入待确认；关键实体缺失则 BLOCKED |
| `cast_sheets_loaded` | 本章出场角色的「外貌锚点／动作习惯／生活习惯／地域用语」已进 Pack | 记 MAJOR |
| `no_blocking_conflict` | 无 `CONFLICT` 级与阻断级问题 | BLOCKED |
| `foreshadowing_checked` | 本章涉及的伏笔窗口、状态、禁止提前揭示已核对 | 记 MAJOR |
| `awareness_boundary_checked` | 角色已知/未知信息已列出 | 记 MINOR |
| `pack_generated` | Preflight Pack 已写入 staging | BLOCKED |

判定规则：
- 存在 `blocks_write: true` 的问题 → 最高只能给 `REVISION_REQUIRED`；
- 存在 `BLOCKER` → `BLOCKED`；
- 存在需作者拍板项 → `AUTHOR_APPROVAL_REQUIRED`；
- 全部通过 → `ACCEPTED`，状态推进到 `preflight_passed`。
