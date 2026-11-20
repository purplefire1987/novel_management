# 代码结构

## 分层

```
cli/          只做参数解析与输出格式化，不写业务判断
  ├── __init__.py   parser 与命令分发
  ├── read.py       doctor / status / validate / rebuild-index
  ├── init.py       init-author / init-project
  ├── flow.py       task / stage / diff / approve / adopt / publish
  └── backup_cmd.py backup / restore
config/       五级配置解析 + Vault_Config 兼容折算
paths.py      ProjectPaths：项目内目录解析与边界校验
logs.py       运行日志：写 <runtime>/logs/novel.log，与 audit 的审计日志是两件事
storage/      哈希、原子写、不覆盖写、可验证复制
audit/        操作日志（ops.jsonl）与操作者校验
tasks/        任务记录与状态流转
approvals/    批准记录（绑定 sha256）
staging/      staging 落盘与 manifest
release/      发布四道闸与旧版归档
baseline/     作者编辑基线（adopt）
backup/       备份、校验、恢复
adapters/     模型适配层；当前只有 MockLLM（确定性、不联网、不需密钥）
indexing/ validation/ workflows/ agents/ core/   预留扩展位（T03–T09）
```

依赖方向严格单向，越往下越不知道上层：

```
cli → release / backup / staging / tasks / approvals / baseline
    → paths / storage / audit / config
    → errors
```

`storage/` 与 `errors.py` 不 import 任何业务模块。这条约束让底层可以被单独测试，
也让「换掉写入实现」不需要改动业务代码。

## 三条不可动摇的约定

**1. 路径只能来自配置。** 代码里不出现 `01_Canon`、`10_Projects` 这类目录字面量。
需要目录名就从 `ProjectPaths` 取，或者从 `project.yaml` 读。

**2. 写入必须过边界校验。** 任何落盘前 `ensure_inside(target, vault)`。
`..`、绝对路径、符号链接逃逸三种情况都在 `tests/security/` 里有对应用例。

**3. 业务模块不 print。** 输出只在 `cli/` 层。业务层返回结构化结果（`PublishResult` 之类），
由 CLI 决定怎么显示。这样同一套逻辑可以被测试直接调用，不必解析 stdout。

**4. 日志不写 stdout。** 运行日志只进 `<runtime>/logs/novel.log`（要看终端加
`NOVEL_LOG_CONSOLE=1`，走 stderr）。stdout 是给作者的输出契约，混进日志就没法读了。
写不进去（Runtime 只读、磁盘满、路径被普通文件占住）必须降级为空操作，**不许让命令失败**——
日志是旁路，不能反过来决定命令成败。

## 几个容易踩的实现细节

**写入不能假设 `os.replace` 可用。** 某些受限环境会拦 rename，此时临时文件删不掉、
目标文件也写不进去。`storage.write_atomic` 因此按目录记忆「能不能 rename」，失败就直接写，
每个目录最多留一个 `.novel-tmp`。

**`stage` 的 `--to` 是正式路径，不是 staging 路径。** 文件实际落在
`07_Staging/<TASK-ID>/<分栏>/<正式路径>`，同时记下目标正式文件当时的 `base_sha256`——
这是后面判断「提案生成后作者是否改过文件」的依据。

**批准记录存的是逐文件 sha256，不是任务级哈希。** 任务级哈希做不到「只批了一部分」的粒度，
也没法在发布时定位到底是哪个文件被动了。

**frontmatter 解析不强制 PyYAML。** 没有 PyYAML 时降级为纯标准库实现，
已知差异：数字变字符串、嵌套映射读不到。这两个限制必须在文档里写明，不能让它变成静默的坑。

**隐藏目录判定用 Vault 内相对路径。** 用绝对路径判会把 `.tmp/vault` 这种临时目录整树误判，
导致索引 0 条——这个 bug 真实发生过一次。

## 加一个新命令的步骤

1. 业务逻辑写在新模块或已有模块里，返回结构化结果，不 print；
2. `cli/__init__.py` 加 parser 与 `HANDLERS` 映射；
3. 需要写盘的话，先确认走的是 `storage` 的安全写入；
4. 写测试：一条成功路径、一条拒绝路径（越界 / 越权 / 缺确认，按命令性质选）；
5. 更新 `docs/operations/cli_reference.md` 与 README 的命令表。

## 加一个新目录

目录名不写进代码，写进 `templates/project/project.yaml` 与 `init-project` 的骨架定义。
若某个目录需要特殊语义（例如「不得进索引」），改 `paths.py` 的常量并补测试，
不要在各处散落字符串判断。
