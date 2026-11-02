---
type: index
---

# 实体索引

由 `novel rebuild-index` 生成 `Entity_Index.md` 之外，日常以 SQLite 为准。可按类型快速跳转：

- 人物：`01_Core_Memory/Characters/`
- 地点：`01_Core_Memory/Locations/`
- 势力：`01_Core_Memory/Factions/`
- 物品：`01_Core_Memory/Items/`
- 敌人：`01_Core_Memory/Enemies/`
- 伏笔：`06_Plot_Ledger/Foreshadowing/`
- 事件：`07_Entity_Graph/Events/`
- 关系：`07_Entity_Graph/Relationships/`

## 关系查询示例

```bash
# 查某实体的所有关系
novel query rel CHAR-0001

# 查某章节涉及的事件
novel query events CH-0002

# 全文检索
novel search "刀鞘"
```
