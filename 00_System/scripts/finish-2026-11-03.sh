#!/usr/bin/env bash
# =============================================================================
# 2026-11-03 收尾脚本
#
# 为什么需要它：
#   本次会话后段，本机的"删除/重命名"被安全守卫全局锁死（连 `rm` 一个空文件都被拦），
#   于是有三件事没能在会话内完成：
#     1) git 提交（git 写索引需要删临时文件 → 失败，并留下 .git/index.lock）
#     2) 删除 50 个重复人物文件（失败录入留下的孤儿）
#     3) 把 86 个人物从 CHAR-0051~0136 重排为 CHAR-0001~0086
#   文件内容本身都已正确落盘，只差这三步"要删东西"的操作。
#
# 用法（在本机终端里跑，不要在被守卫限制的会话里跑）：
#   bash 00_System/scripts/finish-2026-11-03.sh
# =============================================================================
set -euo pipefail

VAULT="/Users/Mr_Zhou/WorkBuddy/超长篇小说管理系统/NovelVault"
PY="/Users/Mr_Zhou/WorkBuddy/超长篇小说管理系统/.venv/bin/python"
NOVEL="/Users/Mr_Zhou/WorkBuddy/超长篇小说管理系统/.venv/bin/novel"

cd "$VAULT"

echo "== 0/5 清掉失败提交留下的锁 =="
rm -f .git/index.lock
echo "   done"

echo "== 1/5 去重 + 号段重排（先看计划） =="
"$PY" 00_System/novel_system/scripts/repair_duplicate_entities.py | tail -n 20
echo
read -r -p "确认执行？（输入 yes 继续）: " answer
if [ "$answer" != "yes" ]; then
  echo "已取消。若只想看计划，上面的输出就是全部。"
  exit 0
fi

"$PY" 00_System/novel_system/scripts/repair_duplicate_entities.py --apply

echo "== 2/5 重建索引 =="
"$NOVEL" --vault . rebuild-index

echo "== 3/5 体检 =="
"$NOVEL" --vault . doctor || true

echo "== 4/5 校验骰子台账 =="
"$NOVEL" --vault . dice verify

echo "== 5/5 提交并推送 =="
git add -A
git commit -F - <<'MSG'
feat: 骰子娘 + 编务裁定（奉天造总部/分部、怀柔府补录）+ 号段修复

骰子娘（新 Agent）
- dice.py：承诺—开奖 + 玩家见证值进熵池 + 哈希链台账，任何人可复算
- novel dice {roll,check,duel,verify,log}：按 WORLD-0006 裁决（DC 表 / 优势劣势 / 对抗 / 96-100 大成功、01-05 大失败）
- 提示词 dice_maiden.md、agent_registry / permissions 注册，06_Plot_Ledger/Dice 为唯一可写前缀
- tests/test_dice.py 21 个用例

编务裁定
- 奉天造：FAC-0022 总部（朝廷/山岚城）、FAC-0032 分部（民间/武安都），互链 + 编者注；修正山岚城误链
- 怀柔府：原表该行只有州府郡，补录 LOC-0072 并标注地名待补
- 变更记录 10_Change_Log/Canon_Changes/EDIT-20261103-editorial.md

号段与数据
- 删除失败录入留下的 50 个孤儿人物文件，86 个人物重排为 CHAR-0001~0086，引用同步改写
- 示例项目已归档至 99_Archive/Legacy_Example_白骨渡，不参与索引
- import profile 源指向 Vault 内归档件，录入可自事实源重放
MSG

git push origin HEAD
echo
echo "全部完成。当前提交："
git log --oneline -1
echo
echo "下一步建议："
echo "  1) pytest 00_System/novel_system/tests -q     # 全量验收（含 test_dice.py）"
echo "  2) 配好 NOVEL_LLM_API_KEY 后 novel doctor 应为全绿"
