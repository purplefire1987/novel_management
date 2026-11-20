# 测试约定

## 怎么跑

```bash
pytest                       # 全量
pytest tests/unit            # 单元
pytest tests/security        # 路径越界与隐私
pytest -q --tb=short         # 精简
python3 scripts/selfcheck.py # 端到端冒烟，不依赖 pytest
```

`pyproject.toml` 已配 `pythonpath = ["src"]`，不用先 `pip install -e .` 也能跑。

**受限环境下 pytest 可能起不来**：某些沙箱会拦 `tempfile` 建目录、
拦 sqlite 的 `-journal` / `-shm` 文件。症状是 `disk I/O error` 或测试收集阶段就崩。
这时用 `scripts/selfcheck.py`——它只用标准库，自己在工作目录下建临时树，
能覆盖 init → task → stage → diff → approve → publish → adopt → backup → restore → index 全链路。

## 目录职责

```
tests/unit/          纯函数与配置解析，不碰文件系统以外的东西
tests/integration/   命令级链路：一个或多个命令的组合行为
tests/security/      路径越界、越权、隐私边界
tests/acceptance/    规范 §14 的验收清单，一条检查对应至少一个断言
tests/fixtures/      虚构夹具，不许出现任何真实作品内容
```

## 测试数据的铁律

- 只用 `examples/sample_novel` 或临时目录里现造的虚构内容；
- 不读取、不复制、不索引作者的真实 Vault；
- 夹具里的人名地名必须是明显虚构的（`CHAR-0001 林砚` 这种可以，真实人物不行）；
- 不要在测试里写死 `/Users/xxx` 这类路径——CI 上不存在。

## 一个测试要证明什么

好的测试能回答「如果实现退化了，这条会不会红」。

反例（不要写）：

```python
def test_publish_ok(...):
    assert main([...]) == 0
```

这条只证明「命令没崩」。它不会在「批准校验被删掉」时变红。

正例：

```python
def test_publish_rejects_tampered_staging(...):
    # 批准之后再改 staging
    target.write_text("被换了", encoding="utf-8")
    assert main(["publish", "--task", tid, "--as", "author", "--confirm"]) == 4
```

它钉住的是一个**拒绝**行为。安全类改动尤其要先写拒绝用例，再写实现。

## 必须覆盖的拒绝路径

| 场景 | 期望 |
|---|---|
| `stage --to ../外面` | 退出码 3 |
| `stage --to` 指向非正式区 | 退出码 3 |
| Agent 执行 `approve` / `publish` | 退出码 3 |
| 未批准就 `publish` | 退出码 4 |
| 批准后 staging 被改，再 `publish` | 退出码 4 |
| `publish` 写 Canon 缺 `--confirm-canon` | 退出码 3 |
| 同一任务发布两次 | 退出码 1 |
| 未指定项目就写 | 退出码 2 |
| `restore` 会覆盖现存文件 | 默认跳过，需 `--overwrite` |
| 公仓内出现私人目录形态 | 隐私扫描退出码非 0 |

## 夹具的写法

集成测试统一用 `tmp_path` 造一个最小 Vault，不重复手写结构：

```python
def test_xxx(tmp_path, monkeypatch):
    vault, runtime = tmp_path / "vault", tmp_path / "runtime"
    assert main(["init-author", "--vault", str(vault),
                 "--runtime", str(runtime), "--confirm"]) == 0
    assert main(["init-project", "--vault", str(vault), "--runtime", str(runtime),
                 "--id", "novel-001", "--name", "T", "--confirm"]) == 0
```

项目目录名由 `init-project` 决定（`<id>_<slug>`），测试里不要硬拼，
用 `next((vault / "10_Projects").iterdir())` 拿。

## 隐私扫描测试

`scripts/privacy_scan.py` 自身也要被测：故意造一个含 `/Users/` 绝对路径的文件，
确认扫描能报出来；再造一个干净的，确认退出码 0。扫描器漏报比不扫更危险。

## CI 上的三层

1. `privacy`：扫工作区 + 扫最近 50 条提交信息；
2. `test`：3.11 / 3.12 双版本跑 pytest + selfcheck + Schema JSON 可解析；
3. `guard`：grep 确认 `src/` 无硬编码家目录；`find` 确认仓库内无 `10_Projects/` 等私人目录形态。
