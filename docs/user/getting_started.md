# 上手：从零建一个能用的系统

这份文档面向作者。做完之后你会得到三个互不干扰的目录，以及一条「Agent 提案 → 你看 diff → 你批准 → 入库」的链路。

## 一、先决定三个路径

系统要求这三处是**三个独立目录**，互不嵌套：

| 用途 | 放什么 | 能不能删 |
|---|---|---|
| 公仓 | 代码、模板、Schema | 能，重装即可 |
| Vault | 你的作品：设定、正文、记忆 | **不能**，删了就是丢稿 |
| Runtime | 索引、日志、staging、备份 | 索引能删，备份不能删 |

示例（换成你自己的路径）：

```
~/novel-management      公仓
~/MyVault               作者 Vault
~/novel-runtime         运行目录
```

不要把 Vault 建在公仓里面。`novel doctor` 会检查这一点，但你最好一开始就别这么干——
历史上靠 `.gitignore` 排除私人目录的做法已经出过事。

## 二、安装

```bash
git clone <repo> ~/novel-management
cd ~/novel-management
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[yaml]"
novel --version
```

`[yaml]` 可选。不装也能跑，但配置里的嵌套字段读不到，建议装上。

## 三、配置

写一份用户级配置，省得每条命令都带路径：

```yaml
# ~/.config/novel-system/config.yaml
paths:
  author_vault: "/Users/你的用户名/MyVault"
  runtime: "/Users/你的用户名/novel-runtime"
active_project:
  project_id: "novel-001"
```

公仓路径默认取代码所在仓库，不用配。

配完先跑一次体检：

```bash
novel doctor
```

它会打印「路径来自哪一层配置」。如果显示的是默认值而不是你的配置，说明上面这份文件没被读到。

## 四、建 Vault

```bash
novel init-author --confirm
novel init-project --id novel-001 --name "我的书" --confirm
```

`--confirm` 是必须的，没有它只做预演。建出来的结构：

```
MyVault/
├── 00_System/            作者档案、Vault 配置、项目管理、看板、策略
├── 10_Projects/
│   └── novel-001_我的书/
│       ├── 00_Project/project.yaml      项目元数据与五个根路径
│       ├── 01_Canon/                    设定事实源（六类）
│       ├── 02_Planning/                 粗纲、细纲、场景节拍
│       ├── 03_Manuscripts/              Drafts / Revisions / Final
│       ├── 04_Story_Assets/             素材
│       ├── 05_Ledgers/                  时间线、事件、关系、伏笔
│       ├── 06_Memory/                   Hot / Short_Term / Long_Term
│       ├── 07_Staging/                  Agent 提案落点
│       ├── 08_Reviews/                  审阅记录
│       └── 90_Archive/                  归档
├── 90_Shared_Assets/     跨项目素材与模板
└── 99_Archive/
```

确认一下：

```bash
novel status
novel validate
```

`validate` 是只读的，可以随时跑，不会改任何东西。

## 五、第一次「提案 → 入库」

先建一个任务，再让 Agent（或你自己）把产物写进 staging：

```bash
novel task create --type draft --assignee writer
# 输出 TASK-000001

novel stage --task TASK-000001 \
            --from /tmp/草稿.md \
            --to 03_Manuscripts/Drafts/CH-0001.md \
            --as writer
```

看差异：

```bash
novel diff --task TASK-000001
```

批准并发布：

```bash
novel approve --task TASK-000001 --as author
novel release prepare --task TASK-000001 --as author      # 生成发布计划
novel approve release REL-000001 --hash <计划哈希> --as author
novel publish --task TASK-000001 --as author --confirm
novel release status                                      # 判词 OK 才算落地
```

发布为什么是三步而不是一步：批准对象必须是一份**看得见的计划**。
少了 `release prepare`，批准就只能绑任务，于是「批的是 A、发的是 B」又开口子了。
详见 [ADR-014](architecture/adr/ADR-014-approval-binds-bytes.md) 与
[ADR-015](architecture/adr/ADR-015-release-protocol-and-recovery.md)。

四条硬规则：

- `approve` / `publish` / `adopt` / `restore` / `release execute` 只认 `--as author`，
  Agent 代办会被拒（退出码 3）；
- 批准绑定每个文件的 sha256，批准后再改 staging，发布会拒绝；
- 写 `01_Canon/` 还要加 `--confirm-canon`；
- 同一任务不能发布两次。

## 六、把现有稿子搬进来

两条路，按原稿的规整程度选。

**A. 有 `.md` / `.txt` 原稿，想让它自动切章**（0.10.0 起）：

```bash
novel backup --label "搬迁前"                              # 先备份，别跳
novel import scan ~/旧稿/ --batch-size 20 --as ingestor     # 保全原文 + 切章 + 抽取 + 抽样
novel import report IMP-000001 --chapters                   # 看它切成了什么
novel import stage IMP-000001 --batch 1 --as writer         # 第一批进 staging
# 之后就是第五节那套：approve → release prepare → approve release → publish
```

它**不替你决定**：切不出章就整篇一章并说明；没提到人名就报「待提取」（覆盖不完整
不阻断发布）；GBK 文件直接拒绝而不是猜编码。原稿原样存在
`90_Archive/Imports/<IMP>/raw/`，`import where IMP-000001 "旧稿.md#L3-L9"` 随时回读。

docx / epub / pdf 属 P1，本版明确不支持——先另存为 UTF-8 的 Markdown 或 TXT。

**B. 稿子是散的、结构要人来定**（照下面五步手工搬）：

1. 先 `novel backup --label "搬迁前"`；
2. 正文原样放进 `03_Manuscripts/Drafts/`，**不要改写、不要手工转录**（手抄会漏字）；
3. 设定逐类放进 `01_Canon/` 对应子目录；
4. 跑 `novel validate`，把断链与重复 ID 列出来，一条条处理；
5. 处理完 `novel adopt --as author` 登记基线，之后系统才知道哪些是你手改的。

两条路都不改你的字。区别只在「谁来决定章怎么切」：A 让系统切完给你看，
B 你自己摆好。

## 七、出问题怎么办

| 症状 | 先做什么 |
|---|---|
| 命令找不到文件 | `novel doctor` 看路径来源 |
| `validate` 报实体 0 份 | 多半是 `project.yaml` 的路径写错了，检查 `canon_root` |
| 发布被拒「缺批准」 | `novel task inspect TASK-xxx` 看批准状态 |
| 发布被拒「内容指纹不符」 | staging 在批准后被改过，重新 `approve` |
| 想撤销刚做的事 | `novel backup --list` 找最近备份，`restore --dry-run` 预演 |
| `import scan` 退出码 1 | 正常：有维度没达标（常见是某份文件非 UTF-8 被拒）。逐行看它标了哪一项 |
| 导入切出的章不对 | 整篇一章多半是没有章节标记，用 `--level N` 指定层级，或手工搬（上面 B 路） |
| `import stage` 说「换切分方式」 | 同一导入不能改 `--batch-size`。要改就 `import scan` 起一个新导入号 |

任何情况下都**不要**手工删除 `00_System/` 或 `01_Canon/` 里的文件来「修问题」。
先备份，再看报告。
