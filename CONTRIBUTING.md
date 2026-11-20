# 贡献指南

本仓库是长篇小说管理系统 V4.0 的公共部分。它**不包含任何作品内容**，也不接受含作品内容的贡献。

## 一、红线：什么绝对不进这个仓库

提交前请自查，命中任何一条都必须撤回：

- 小说正文、章节草稿、未发表内容、大纲与细纲；
- 真实世界观、人物、组织、地名、剧情线索；
- 真实 Vault 的文件名、目录树、索引、绝对路径；
- 备份包、数据库文件、日志、缓存；
- API Key、Token、密码、Cookie、私钥、带签名的 URL；
- 从私人文件生成的、能反推出作品内容的摘要或向量；
- **用真名、真地名、真书名做示例**——哪怕只出现一次、哪怕只是「说明格式用」。

`.gitignore` 只是第二道防线。判据是「这段内容能不能让陌生人还原出作品」。
扫描脚本只认形状（目录名、文件名、路径、凭据），认不出内容，
所以「扫描通过」不等于「干净」：提交前请自己再读一遍改动。

## 二、开发环境

```bash
git clone <this-repo> novel-management
cd novel-management
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,yaml]"
pytest                       # 全量测试
python3 scripts/selfcheck.py # 端到端冒烟，不依赖 pytest
python3 scripts/privacy_scan.py --repo .   # 隐私扫描
```

支持 Python 3.11 及以上。除 `pytest` 与可选的 `PyYAML` 外不引入运行时依赖——
这个系统要在作者机器上长期运行，依赖越少越不容易烂。

## 三、提交前必过四项

1. `pytest` 全绿；
2. `python3 scripts/selfcheck.py` 全绿；
3. `python3 scripts/privacy_scan.py --repo .` 退出码 0；
4. `python3 -m novel_system validate --vault <临时 Vault>` 通过。

数据安全相关的改动（`storage/`、`release/`、`approvals/`、`paths.py`）必须同时补测试，
并且测试要能**先失败再通过**——只加一条跟着实现走的断言不算。

## 四、提交信息

```
<类型>: <一句话说明>

<为什么这么做，而不是做了什么——做了什么看 diff>
```

类型取 `feat` / `fix` / `docs` / `test` / `refactor` / `chore` / `security`。
涉及行为变化时，在正文里写清兼容性影响与迁移提示。

## 五、架构决策

改变以下任一事项，必须新增或修订 ADR（`docs/architecture/adr/`）：

- 三处目录的边界（公仓 / Vault / Runtime）；
- 事实源的权威顺序；
- 写入链路上的关口数量或语义；
- Agent 权限矩阵；
- 备份与恢复策略；
- 许可证与对外分发。

ADR 一旦「已接受」就不改结论；要改就写一篇新的并把它标为「被 ADR-XXX 取代」。

## 六、Schema 与版本

- `schemas/` 与 `config/config.schema.json` 的字段变化必须有版本号与迁移说明；
- 升级不得自动修改作者 Vault；
- 任何 Canon 格式迁移必须先 `--dry-run` 并出差异报告。

## 七、行为准则

参与即表示同意 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
安全问题请走 [SECURITY.md](SECURITY.md)，不要开公开 Issue。
