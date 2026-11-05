<%*
// 新建实体档案：问类型 → 算下一个 ID → 改名移入对应目录 → 生成骨架
// 用 Templater 插入（命令面板 → Templater: Insert Template），不要在库里手动新建空文件。
//
// 前缀与目录的**权威**是 00_System/Config/id_rules.yaml（前缀）与
// system_config.yaml（paths，目录）。Templater 读不了 YAML，只能在下面抄一份；
// 抄了就会漂移，所以 `novel integrity` 里有「配置漂移」一项专门比对这两份——
// 加类型、改前缀、挪目录时，两处一起改。
const TYPES = [
  {
    label: "人物", type: "character", prefix: "CHAR",
    dir: "01_Core_Memory/Characters", subtype: "人物",
    fields: [
      ["人物级别", "主要人物／次要人物／龙套"],
      ["年龄", ""], ["性别", ""], ["种族", "人类"],
      ["境界", ""], ["形象概述", ""],
      ["所属势力", ""], ["所在地", ""], ["持有物品", ""],
      ["核心欲望", ""], ["核心恐惧", ""], ["弱点", ""],
    ],
  },
  {
    label: "地点", type: "location", prefix: "LOC",
    dir: "01_Core_Memory/Locations", subtype: "地点",
    fields: [
      ["层级", "州域／府／郡／城邑／场所"],
      ["上级", ""], ["主要地貌", ""], ["气候", ""],
      ["人口规模", ""], ["势力", ""], ["特征", ""],
    ],
  },
  {
    label: "势力", type: "faction", prefix: "FAC",
    dir: "01_Core_Memory/Factions", subtype: "势力",
    fields: [
      ["性质", "门派／商会／官府／军伍／帮会"],
      ["所在地", ""], ["首领", ""], ["关联角色", ""],
      ["主要业务", ""], ["与其他势力的关系", ""],
    ],
  },
  {
    label: "物品", type: "item", prefix: "ITEM",
    dir: "01_Core_Memory/Items", subtype: "物品",
    fields: [
      ["品类", "兵器／灵器／机兵／信物／日用品"],
      ["品阶", ""], ["所在地", ""], ["持有者", ""],
      ["来源", ""], ["作用", ""], ["代价", ""],
    ],
  },
  {
    label: "生物", type: "enemy", prefix: "ENEMY",
    dir: "01_Core_Memory/Enemies", subtype: "生物",
    fields: [
      ["栖息地", ""], ["分布地", ""], ["危险等级", ""],
      ["形态", ""], ["习性", ""], ["弱点", ""],
    ],
  },
  {
    label: "技能", type: "skill", prefix: "SKL",
    dir: "01_Core_Memory/Skills", subtype: "方术",
    fields: [
      ["品级", "上品／中品／下品"], ["能力系别", ""],
      ["效果", ""], ["代价", ""], ["备注", ""],
    ],
  },
  {
    label: "命格", type: "destiny", prefix: "FATE",
    dir: "01_Core_Memory/Destinies", subtype: "命格",
    fields: [
      ["命格类型", ""], ["命格效果", ""], ["觉醒条件", ""],
      ["代价", ""], ["持有者", ""],
    ],
  },
];

const pick = await tp.system.suggester(
  TYPES.map(t => t.label),
  TYPES,
  false,
  "要建哪一类档案？"
);
if (!pick) return;

const title = await tp.system.prompt("档案名（人物名 / 地名 / 势力名）", "");
if (!title) return;

// 扫同目录取最大编号：目录里有 CHAR-0071_x.md 就出 CHAR-0072
const rx = new RegExp("^" + pick.prefix + "-(\\d{4})");
let max = 0;
for (const f of app.vault.getMarkdownFiles()) {
  if (!f.path.startsWith(pick.dir + "/")) continue;
  const m = f.basename.match(rx);
  if (m) {
    const n = parseInt(m[1], 10);
    if (n > max) max = n;
  }
}
const id = pick.prefix + "-" + String(max + 1).padStart(4, "0");
const today = tp.date.now("YYYY-MM-DD");

const lines = [
  "---",
  "id: " + id,
  "type: " + pick.type,
  "name: " + title,
  "title: " + title,
  "aliases: []",
  "subtype: " + pick.subtype,
  "world: 九州",
  "canon_status: proposed",
  "created_at: '" + today + "'",
  "updated_at: '" + today + "'",
  "source_refs: []",
  "tags:",
  "- " + pick.subtype,
  "---",
  "",
  "# " + title,
  "",
  "> 新建于 " + today + "｜`canon_status: proposed`，作者确认后改 `confirmed`。",
  "",
  "## 概要",
  "",
  "",
  "## 档案",
  "",
];
for (const [k, hint] of pick.fields) {
  lines.push("- **" + k + "**：" + (hint ? "（" + hint + "）" : ""));
}
lines.push("", "## 关联", "");

await tp.file.move(pick.dir + "/" + id + "_" + title);
tR += lines.join("\n");
-%>
