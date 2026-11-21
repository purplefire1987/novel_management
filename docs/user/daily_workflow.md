# 日常创作：六个动作

日常只有六件事会动到文件。搞清楚哪件是「我改」、哪件是「Agent 改」，就不会把稿子弄丢。

## 1. 写前：拿资料包

```bash
novel task create --type preflight --assignee researcher --mode read_only
```

资料官产出的是**资料包**，不是正文。它应该回答：这一章要用到的人物当前状态、地点、前文伏笔。
资料包进 staging 供你查看，不进 Canon。

判断资料包够不够用的标准：**凡是正文里会写到的具体事实，都能在里面找到出处**。
找不到就要它补，不允许「大致符合」。

## 2. 写作：主笔产出到 staging

```bash
novel task create --type draft --assignee writer
novel stage --task TASK-000002 --from 初稿.md \
            --to 03_Manuscripts/Drafts/CH-0007.md --as writer
```

注意 `--to` 指向的是**正式路径**，但文件实际落在 `07_Staging/<TASK-ID>/` 里。
这个设计是为了让 `publish` 知道该往哪放，同时保证现在还没动你的稿子。

初稿和修订稿分开：`Drafts/` 放初稿，`Revisions/` 放修订，别覆盖。

## 3. 审阅：只看 diff

```bash
novel diff --task TASK-000002                # 概览
novel diff --task TASK-000002 --full         # 不截断
novel diff --task TASK-000002 --path 03_Manuscripts/Drafts/CH-0007.md
```

diff 会标出三类东西：

- **新增**：目标文件原本不存在；
- **修改**：有旧版本，显示行级差异；
- **冲突**：staging 生成之后，正式文件被改过——这时不要直接批准。

冲突的含义是「你可能在 Agent 写稿期间自己改了同一个文件」。处理方式二选一：
要么让 Agent 基于你改后的版本重写，要么 `approve --force` 明确承担覆盖风险。

## 4. 入库：看一眼 → 批准 → 发布

```bash
novel inspect TASK-000002 --as author        # 批准前看这七项
novel approve artifact TASK-000002 --hash <inspect 报的 sha256> --as author --note "第 7 章通过"
novel publish --task TASK-000002 --as author --confirm
```

`inspect` 要看的七项：对象 ID、内容 hash、与正式版的差异、风险等级、阻断问题、
来源、所需批准范围（PRD §20.3）。其中两项最容易漏：

- **drift**：stage 之后 staging 里的文件被直接改过。哈希按**磁盘现算**，
  所以批的一定是你此刻看到的字节；
- **BLOCKER**：有未解决的阻断问题时，`approve` 与 `publish` 都会拒绝。
  阻塞级不能豁免——只有「修掉后 resolve」或「缩范围」两条路。

`approve` 记下每个文件的 sha256。`publish` 会重新算一遍再比对——所以「批准的是 A、
发的是 B」在这条链路上不可能发生。**改一个字节也要重批**，没有「差不多就行」。

写设定（`01_Canon/`）要额外加：

```bash
novel publish --task TASK-000003 --as author --confirm --confirm-canon
```

想改已经发生过的设定（RETCON），走的是另一条路：

```bash
novel change create --type RETCON --reason "父亲其实没死" --target 01_Canon/Characters/CHAR-0001.md
novel impact CR-000001                        # 看这次改动会波及哪些章
novel approve change CR-000001 --hash <报告的 sha256> --rewrite-chapters --as author
```

`impact` 会列出受影响的正文。只要列出正文、而你没有承诺改写（`--rewrite-chapters`）
或明确缩小范围（`--narrow-scope`），批准就会被拒绝——PRD §19.3 不允许
「只改档案就宣称 RETCON 完成」。

旧版本会自动复制到 `90_Archive/publish_archive/<TASK-ID>/<时间戳>/`，不用自己备份。

## 5. 你自己改了文件：adopt

这是**最常用**的一条命令，比 publish 常用得多。

你随时可以在 Obsidian 或任何编辑器里直接改任何文件——这是系统承认的合法主路径。
改完之后：

```bash
novel adopt --as author --dry-run     # 先看改了什么
novel adopt --as author --note "手工修订第 3 段"
```

`adopt` 只做三件事：登记变化、推进基线、留痕。**它不会回滚你的修改，也不会覆盖它。**

为什么需要这一步：系统用「基线」判断「这个文件是不是被作者改过」。
不登记的话，下一次 `publish` 会把你的手改误判成冲突。

`adopt` 不接受 Agent 代办。Agent 说「我帮你登记一下」是越权。

## 6. 备份：动手之前

任何批量操作、搬迁、改名之前：

```bash
novel backup --label "改名之前"
```

备份带清单与逐文件 sha256，写完立即回读校验。校验不通过会明确报失败——
「备份成功但有损」这种事不会发生。

查历史备份：

```bash
novel backup --list
```

恢复（先预演）：

```bash
novel restore --backup-id BK-20261120-101500 --dry-run
novel restore --backup-id BK-20261120-101500 --confirm
```

恢复默认**不覆盖**现存文件，只补回缺失的。确实要覆盖得加 `--overwrite`，
并且覆盖前会另存一份当前内容。

## 一张图记住谁写哪里

```
你        →  任何文件（Obsidian 直接改）
Agent     →  只写 07_Staging/
CLI       →  正式区，且必须在 approve 之后
索引      →  Runtime，可删可重建
```

Agent 唯一能碰正式区的路径是 `publish`，而 `publish` 需要你的批准记录。
这不是流程繁琐，是因为「批的确实是发的」只能靠这条链路保证。

## 日常节奏建议

| 时机 | 动作 |
|---|---|
| 每章开写前 | `task create --type preflight` 拿资料包 |
| 每章写完 | `stage` → `diff` → `approve` → `publish` |
| 每章入库后 | 让记忆管理员提记忆更新，你批准后 `publish` |
| 每周 | `novel backup --label "周备份"`，并复制到外部硬盘 |
| 任何时候手改了文件 | `novel adopt --dry-run` 看一眼，再 `adopt` |
