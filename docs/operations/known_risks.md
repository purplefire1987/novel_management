# 已知风险与未解决问题

按 V4.0 §18 第 13 条要求披露。**未解决就是未解决，不写成「后续优化」。**
每条给出：是什么、影响、为什么现在不解决、怎么缓解。

## 一、能力缺口

### R-01 旧系统 30 条命令未全部迁移

- **现状**：V4.0 的 16 项命令已实现；旧系统 30 条的去向已逐条记录在
  [legacy_commands.md](legacy_commands.md)：15 条该迁移、7 条已覆盖、3 条部分覆盖、5 条不建议迁移。
- **影响**：日常写作主链可用，但「按名字查实体」（`find`）、「新建实体」（`new`）、
  「全文检索」（`search`）、「写前资料包」（`preflight`）这四项缺了会立刻卡住。
- **不解决的原因**：这些命令的语义与 V4.0 的数据模型不完全对齐（旧系统用 SQLite、
  `canon_status`、`REL` 前缀等），照搬会引入与 ADR-002/003 冲突的概念。
- **缓解**：主链先稳；缺口逐条重新设计，不复制旧实现。

### R-02 地图生成器已重编码（2026-11-20 关闭）

- `build_jiuzhou_map.py` / `import_map_admin.py` 原先写死 `private/00_Canon/...`，已随旧公仓归档。
- **已处置**：搬进 `src/novel_system/maps/`，路径由 `MapPaths` 注入，CLI 为
  `maps paths` / `maps import-admin` / `maps build`。
- **验证**：真实 Vault 上两项预演均报「0 个文件改动」，节点数 108/111 与库内一致；
  说明换路径来源没有改变任何输出。
- **新约束**：默认预演，`--confirm` 才写。旧脚本「跑起来就改文件」的行为不再存在。

### R-03 `08_Reviews/` 没有写入通道

- **「正式区」的定义**：`publish` 允许写入的目录，由 `paths.PUBLISH_TARGETS` 唯一定义，共六个根——
  `01_Canon`、`03_Manuscripts`、`05_Ledgers`、`06_Memory`、`02_Planning`、`04_Story_Assets`。
  `07_Staging`、`08_Reviews`、`90_Archive` **都不在其中**；`is_control_path()` 的判据也只有这六个。
- **`08_Reviews` 不该进正式区**（2026-11-20 作者质询「为什么要进正式区」后，撤回先前建议）：
  审阅意见不是作品内容。进正式区意味着它必须过 `approve` + `publish` 才能落盘，
  还会被算进签字基线（签字绑正文 sha256），把「编辑的意见」与「作者的正文」绑死。
- **现状**：编辑意见只能留在 `07_Staging/<TASK-ID>/reviews/`。
- **待办**：给 `08_Reviews` 一条**独立于 publish 的轻量写通道**（`novel note` 或同等）。
  不扩 `PUBLISH_TARGETS`，也不让审阅去走签字链。

## 二、数据问题（真实 Vault 内）

### R-04 `ASSET-0001` ID 冲突（成因已查明，待改号）

- **位置**：`04_Story_Assets/Settings/ASSET-0001_示例设定档案.md`（2026-11-04，
  `import_batch: OUTLINE-20261104-0044`）与 `04_Story_Assets/Battles/ASSET-0001_示例战斗档案.md`
  （2026-11-05，`canon_status: deprecated`）。
- **成因**（旧系统 `99_Archive/legacy_v1/novel_system/src/novel_system/ids.py`）：
  `next_id()` 只做一件事——取索引里同 type 已有最大号 + 1（`next_serial`）。
  索引走 `entities_by_type()` 的 SQLite 查询，而 `entities` 表是 `PRIMARY KEY (id, type)`，
  **id 单列不唯一**，两条同 ID 记录能同时入表且无人报错。
  时间线：11-04 那份单篇先拿 0001；11-05 那批从 0001 重新起编，一路排到 0020
  （Battles 4 + Dialogue 3 + Emotional_Beats 4 + Highlights 4 + Sensory 4 + Settings 1 = 20 份，
  号段 0001–0020 严丝合缝），第一份就撞上 11-04 的 0001。
- **CLI 确实失职**：`Index.exists()` 这个查重方法**写好了但全库无人调用**（`.exists(` 的调用点全是
  文件系统路径判断）。任何写入路径都没做过「该 ID 已存在」的校验；`next_id()` 只保证
  「比索引里的最大号大」，而索引没收录的文件等于不存在。
- **新 CLI 曾经也看不见（已修，2026-11-20）**：`validate` 原先只扫 `CANON_KINDS` 六类，
  `04_Story_Assets` 不在范围，真实 Vault 上输出「重复 0 份」——是漏报不是干净。
  现已把查重扩到 `04_Story_Assets`（跨子目录一起看，跳过 `id: {{id}}` 占位符），
  实测输出「实体 986 份（Canon 966 / 素材 21），重复 1 份」，退出码 1。
  **副作用**：真实 Vault 的 `validate` 从此失败，直到改号——这是它该有的样子。
- **改号方案（待作者批准）**：把 Battles 那份重编为 `ASSET-0021`，连带改文件名。
  选它的理由是引用成本为零：全库对 `ASSET-0001` 的引用共 3 处
  （`VOL-0001_示例卷`、`CH-0001_示例章节` 章纲、`FORESHADOW-0004`），全部写成
  `[[ASSET-0001_示例设定档案|…]]` 这种带完整文件名的形式，指向的都是 Settings 那份；
  Battles 那份无人引用。
  **注意写入通道**：`04_Story_Assets` 属正式区，按硬约定不能手改，
  需要一条走 CLI 的改号入口（`novel renumber`）或作者手改后 `novel adopt` 收编。
- **已补的洞（2026-11-20，0.5.0）**：① 查重范围已扩到素材库 ✅；
  ② **分配前查存在性 + 加锁 + 审计**已到位（`novel allocate-id`）：
  取号同时看现存最大号与审计历史最大号（删档不补号），本前缀有重号直接拒绝发号，
  锁文件防并发（PRD §8.1 禁止「无锁扫描最大号加一」）。
- **重号实测（2026-11-20，`IdRegistry.collisions()`）**：真实 Vault 上是 **2 个**：
  - `ASSET-0001`：`Battles/ASSET-0001_示例战斗档案.md` 与
    `Settings/ASSET-0001_示例设定档案.md`——两处都在正式区，`validate` 报 **FAIL**。
  - `ASSET-0002`：`Battles/ASSET-0002_示例战斗档案二…` 与 `90_Archive/Original_Draft/旧稿章节对照表.md`
    ——一处是归档里的历史稿引用，`validate` 报 **WARN**，不计失败。
- **一次自我更正（重要）**：早先我用文件名前缀当 ID 兜底，把 `02_Planning/Volume_Outlines/`
  下五份 `VOL-0001_*.md` 判成同一个 ID 重复，还把它写进了报告说要「待裁定改不改规则」。
  查 frontmatter 才发现五份的 id 各不相同（`VOL-0001-S1-DETAIL`、`VOL-0001-PLAN`、
  `VOL-0001`、`VOL-0001-S1-REWRITE`、`VOL-0001-DESIGN`）——**是五个对象，不是重复**。
  假重复比漏报更能误导人，所以 `IdRegistry` 已改为「frontmatter 优先，没有才用文件名」，
  文件名与身份不符只记进 `mismatched`（当前 4 处），不再额外占号。

### R-05 两章草稿已删（2026-11-20 关闭）

- 按作者指令删除 `03_Manuscripts/Drafts/CH-0001.draft.md`（390 行）与 `CH-0002.draft.md`（418 行）；
  全库无引用，`Drafts/` 现在只剩 `.gitkeep`。
- **残留的 11 条断链不是这次删除造成的**：断链全部是
  `02_Planning/Scene_Beats/*` 指向 `[[CH-0001]]` / `[[CH-0002]]`，而草稿的文件名是
  `CH-0001.draft.md`（stem 为 `CH-0001.draft`），从来就不是 `[[CH-0001]]` 的解析目标。
  断链的实质是**这两章正文还没写**，`03_Manuscripts/{Final,Drafts}` 目前都是空的。
- **不算风险，只作记录**：等正文落进 `Final/`，断链会自行消失。

## 三、工程与流程

### R-06 本机 pytest 受沙箱限制

- 沙箱拦 `tempfile`，pytest 默认 basetemp 不可用；tempfile 还会回退到**当前目录**落随机名
  4 字节文件（内容 `blat`），跑一次脏一次。
- **可用跑法**：`PYTHONPATH=src python3 -m pytest -q -s -p no:cacheprovider --basetemp=.tmp/pt-<新名>`。
  两个坑：① `--basetemp` 必须是**尚不存在**的路径——pytest 启动时会 `rmtree` 已存在的 basetemp，
  而 rmdir 被沙箱拦，会直接 `PermissionError` 收集失败；② `-s` 不能省，否则 tempfile 探测提前炸。
- **2026-11-20 结果**：126 条用例全过（0 失败），含新增 `tests/integration/test_maps.py` 4 条。
- **待办**：真机终端再跑一次**不带** `--basetemp` 的完整 `pytest`，确认这套脚手架不是必需的。

### R-07 公仓推送通道

- 沙箱内的 HTTPS 代理无法连 GitHub（`CONNECT tunnel failed, 502`）。
- **已处置（2026-11-20）**：远端由 HTTPS 改为 SSH（`git@github.com:...`），
  `ssh -T git@github.com` 认证通过为 `purplefire1987`；`known_hosts` 里
  `github.com` 的主机公钥已存在且与密钥扫描结果一致。
- **残留**：SSH 只解决认证，不绕过耗时。若一次推送内容过大仍可能超时，
  可分两次推（先 `main`，再标签）。

### R-08 许可证已定

- 2026-11-20 作者拍板：**GPL-3.0-only**。`LICENSE` 为 gnu.org 官方全文，
  `pyproject.toml` 与 README 已同步，ADR-010 记录选择理由与未采纳方案。
- **影响**：代码可自由使用与商用；分发衍生作品须以同一许可开源。

### R-09 obsidian-git 未配置

- Vault 没有 `data.json`，即 obsidian-git 从未配置过，autoSave 未启用。
- **影响**：作者手改不会自动提交，需要手动 `git add/commit`，或者干脆不依赖 Git。
- **不擅自配置的原因**：自动提交会把「作者手改」与「CLI 写入」混进同一批提交，
  反而让 `adopt` 的基线判断变模糊。

## 四、结构性风险（设计取舍，不是 bug）

### R-10 备份与原件同机

- `novel backup` 落在同机磁盘，抗不了整机损坏。见 ADR-009。
- **缓解**：作者需自行复制到外部介质；系统不假装做了这件事。

### R-11 本地 Git 不算备份

- 私人 Vault 的 Git 只用于本地 diff 与单文件回滚，历史可被 `reset` 改写。

### R-12 断链检查依赖文件名索引

- `validate` 用文件名（stem）兜底解析双链，同名文件会产生歧义。
- **影响**：可能漏报真实断链，也可能把「指向另一个同名文件」当成已解析。
- **缓解**：`validate` 会同时报告重复 ID，范围是 **Canon 六类 + `04_Story_Assets`**（0.4.1 起）。
  仍不在范围内的目录（`02_Planning`、`05_Ledgers` 等若将来带 `id`）不会被查重——
  「重复 0 份」只等于「这两个范围里没有重号」，不等于全库无重号。

### R-13 隐私扫描只判形状，不判内容

- `scripts/privacy_scan.py` 能认出「私人目录形态」「真实档案命名」「本机路径」「凭据」，
  **认不出**「这段设定写的是我的书」。一段手打的人物小传只要不落在这些形状里，它扫不到。
- **实证**：本轮扫过全仓 0 发现之后，人工复核仍在三份文档里发现真实人物名与书名
  （拿来说明「id 与文件名错位」的例子直接用了真名）。已改成虚构的 `示例名` / `林砚`。
- **影响**：扫描通过 ≠ 干净。CI 通过只说明「没有命中已知形状」。
- **缓解**：形状类规则交给扫描器；内容类只能靠提交前人工过一遍，
  尤其是有没有用真名、真地名、真书名做示例。这一条无法自动化，不要假装能。

---

## 五、与 PRD v2.1 的已知偏离（T02 引入，均有明确选择理由）

### R-14 编号位数：PRD 六位，实现四位

- PRD §8.1 的表与示例写 `CHAR-000001`（六位）；库内 986 份档案实际是 `CHAR-0001`（四位），
  `task_id` 例外，代码里就是 `TASK-%06d`。
- **选择**：新号一律**四位**，schema 的 pattern 放宽到 `[0-9]{4,6}` 以同时接受两种写法。
- **理由**：改六位要动全库文件名、frontmatter、所有 `[[双链]]`、canvas 节点，收益为零。
- **代价**：schema 不再能拦住「四/六位混用」。混用本身无害（ID 是字符串），
  有害的是「同一个号两种写法」，那由查重负责。

### R-15 通用元数据字段名未迁移

- PRD §8.2 要求 `artifact_type` / `artifact_status`；库内 966 份用的是 `type` / `canon_status`。
  PRD §8.3 还明令禁止再用一个 `canon_status` 混表产物状态／信息性质／审核状态。
- **选择**：`entity` schema 以现状字段（`type`/`canon_status`/`subtype`/`world`）为主，
  PRD 字段名作为可选字段并存，不强制择一。
- **理由**：T02 是「定义契约」，不是「迁移数据」。若按 PRD 严格实现，
  966 份档案会全部非法，`validate` 从「报 1 个重号」变成「报 966 份不合契约」——
  那是把治理问题伪装成验收失败。
- **代价**：两套字段名并存期间，schema 无法阻止有人只写 `canon_status` 不写 `artifact_status`。
  迁移属 T05/T07 的范围，做完才能收紧。

### R-16 两个模型的字段是推断出来的

- `transaction_journal` 与 `execution_receipt`：PRD §8.4 只列了类型名，
  §17.4（22 步执行序列）与 §18.2（崩溃恢复）给了语义，但**没有字段表**。
- **做法**：按 §17.4 的步骤号、§18.2 的「恢复程序基于 active_release / manifest /
  事务日志 / 内容哈希交叉验证」反推字段，并在两份 schema 的 `$comment` 里写明「推断」。
- **影响**：这两份契约可能与作者心里的模型不一致。T06 实现发布内核时应以实际需要为准，
  那时改 schema 是正常的，不算推翻本 ADR。
- 同理 `change_request`：§19.2 只给了中文条目，字段名由本实现命名；
  `revelation_mode` 因 PRD 只示例了 `EXPLICIT`，故意不设枚举。

### R-17 `02_Planning` 里 33 个 ID 形态不合法

- `IdRegistry.compounds` 在真实 Vault 上数出 **33 个**「看着像 ID、形态却不合 PRD」的标识：
  `EVT-CH-0001-001`（7 个事件）、`SCENE-CH-0001-001`（场景卡）、
  `VOL-0001-S1-DETAIL`（卷规划文档）、`LORE-20261104-000000`（导入批次）等。
- **影响**：它们不计入任何前缀的号段。`allocate-id --type EVENT` 会从 `EVT-0001` 起编，
  与既有的 `EVT-CH-0001-001` 不冲突（不同字符串），但**语义上是两套编号并存**。
  命令会提示「另有 N 个复合型 ID 以 EVT- 开头」，不会闷声发号。
- **不改的理由**：改号要动文件名 + frontmatter + 所有引用；而 PRD §8.1 同时禁止
  「仅以章节＋事件序号推导长期稳定 ID」，也就是这两种形态里只有一种能留。
  迁移属 T03（索引）与 T07（导入）的范围，那时按新规则一次性换掉更省事。
- **不要把它们当「不是 ID」**：一旦忽略，这些对象在查重时完全隐身。
  `is_compound_id()` 专门收这两类，就是为了让它们可见。

---

**披露原则**：以上每一条都可以被推翻，但推翻要有依据、要留痕，不能靠「应该没问题」。
