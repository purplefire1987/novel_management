---
type: dashboard
---

# 种族清单（8 属 / 35 族）

> 本页**不存数据**。属与族从 [[WORLD-0025_种族表|九州·种族志]] 的 `## 属` / `### 族` 标题现读现算，
> 人物/敌人计数从各自 frontmatter 的 `race` 汇总。改种族请改种族表，改归属请改档案，本页自动跟随。

## 一键核对

```dataviewjs
const PATH = "01_Core_Memory/World/九州/总表/WORLD-0025_种族表.md";
const file = app.vault.getAbstractFileByPath(PATH);
if (!file) {
  dv.paragraph("找不到种族表：" + PATH);
} else {
  const text = await app.vault.read(file);
  const lines = text.split("\n");

  // 一级 = ##（排除用途/总览/关联这类非属小节），二级 = ###
  const skipH2 = /^(用途|总览|关联|索引|口径)/;
  const groups = [];
  let cur = null;
  for (const line of lines) {
    const h2 = line.match(/^##\s+(.+?)\s*$/);
    const h3 = line.match(/^###\s+(.+?)\s*$/);
    if (h3) {
      if (cur) cur.races.push(h3[1]);
      continue;
    }
    if (h2) {
      const name = h2[1];
      if (skipH2.test(name)) { cur = null; continue; }
      cur = { name, races: [] };
      groups.push(cur);
    }
  }

  const chars = dv.pages('"01_Core_Memory/Characters"').where(p => p.type === "character");
  const foes = dv.pages('"01_Core_Memory/Enemies"').where(p => p.type === "enemy");
  const tally = (pages) => {
    const m = {};
    for (const p of pages) {
      const r = p.race;
      if (!r) continue;
      const k = String(r).trim();
      m[k] = (m[k] || 0) + 1;
    }
    return m;
  };
  const cMap = tally(chars);
  const eMap = tally(foes);

  const rows = [];
  for (const g of groups) {
    let gc = 0, ge = 0;
    for (const r of g.races) {
      const c = cMap[r] || 0, e = eMap[r] || 0;
      gc += c; ge += e;
      rows.push([
        g.name,
        `[[WORLD-0025_种族表#${r}\\|${r}]]`,
        c || "—",
        e || "—",
      ]);
    }
    rows.push([`**${g.name} 小计（${g.races.length} 族）**`, "", `**${gc}**`, `**${ge}**`]);
  }

  dv.table(["属", "族", "人物", "敌人"], rows);

  const noC = chars.where(p => !p.race).length;
  const noE = foes.where(p => !p.race).length;
  dv.paragraph(
    `共 **${groups.length} 属 / ${rows.length - groups.length} 族**。` +
    `人物挂族 **${chars.length - noC}/${chars.length}**，敌人挂族 **${foes.length - noE}/${foes.length}**。` +
    `未挂族：${noC} 人、${noE} 敌人——清单见 [[待裁定事项]]，**不猜**。`
  );
}
```

## 口径

- **族名是唯一合法值**：档案里只写 35 族的具体族名（`人类`／`云鹏`／`应龙氏`），
  不写属名（`人族属`）。属由 `race_groups` 反查。
- **查不到就留空**：种族表里没有的族，`race` 留空进待裁定，不许猜。
- **各族详情**（天赋、弱点、寿命、风俗、族人名录）只在种族表里，本页不复制。
