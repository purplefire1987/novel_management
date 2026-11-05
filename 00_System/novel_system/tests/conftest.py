"""验收测试夹具：每个测试在临时目录里复制一份 Vault，互不影响。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from novel_system import vault
from novel_system.config import Config
from novel_system.orchestrator import Orchestrator

def _find_vault_root(start: Path) -> Path:
    """向上查找 Vault 根目录，而不是硬编码目录层级。

    这样无论 novel_system 放在 Vault 外部、还是放进 00_System/ 内，测试都能自愈。
    """
    for candidate in (start, *start.parents):
        if (candidate / "00_System" / "Config" / "system_config.yaml").is_file():
            return candidate
    raise RuntimeError(
        f"未能在 {start} 的上级目录中找到 Vault 根（缺少 00_System/Config/system_config.yaml）"
    )


VAULT_SRC = _find_vault_root(Path(__file__).resolve())

# 复制 Vault 时必须排除的东西：运行产物 + 环境 + 本测试自己的临时目录。
# 尤其 .pytest_tmp：若 basetemp 落在 Vault 内（在代码目录跑测试时会这样），
# 不排除就会把临时目录复制进自身，导致无限递归。
COPY_IGNORE = (
    ".novel_system",
    ".git",
    ".obsidian",
    "__pycache__",
    ".pytest_tmp",
    ".pytest_cache",
    ".venv",
    "*.egg-info",
    # 仓库根上移后，根目录多了本地环境与回收站：
    # .workbuddy 有访问限制，rsync 遇到会以退出码 23 失败，必须排除
    ".workbuddy",
    ".trash_*",
)

# 本机环境注意：系统临时目录（/private/var/folders/...）在沙箱下不可写——每个 mkdir
# 都要过一次中间层，45 个用例直接卡在夹具上（实测 20 分钟不动）。`/tmp` 不受此限。
#
# 但 `pytest_configure` 写在**子目录** conftest 里是无效的：configure 阶段子目录的
# conftest 还没被加载，钩子来不及注册，pytest 仍会用自己的默认 temp root。
# 所以这里不靠钩子，直接自己建临时根目录，绕开 `tmp_path_factory`。
DEFAULT_TEMPROOT = Path("/tmp/nv_pytest")


def _temproot() -> Path:
    return Path(os.environ.get("NOVEL_TEST_TMP", str(DEFAULT_TEMPROOT)))


def pytest_configure(config: pytest.Config) -> None:
    """尽力而为：能改 basetemp 就改，改不了也不影响（夹具已不依赖 tmp_path）。"""
    root = _temproot()
    if getattr(config.option, "basetemp", None) or not root.parent.exists():
        return
    config.option.basetemp = str(root)


def _copy_tree(src: Path, dst: Path) -> None:
    """复制 Vault：优先走系统 rsync，回落到 shutil。

    Vault 现在有近千个 Markdown；Python 逐文件复制在受限宿主里每个 `open` 都要过一次
    中间层，整棵树要 20 秒以上，夹具直接超时。rsync 是原生进程，0.2 秒完成同样的活，
    且 `--exclude` 与 `shutil.ignore_patterns` 语义一致。

    `--inplace` 是必须的：不带它时 rsync 会先写 `.name.XXXXXX` 临时文件再改名，
    那一步会产生**删除**操作，在带文件守卫的环境里会被判成批量删除而中断整轮测试。
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    rsync = shutil.which("rsync")
    if rsync:
        args = [rsync, "-a", "--inplace"]
        for pattern in COPY_IGNORE:
            args += ["--exclude", pattern]
        args += [f"{src}/", f"{dst}/"]
        subprocess.run(args, check=True, capture_output=True)
        return
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*COPY_IGNORE))


@pytest.fixture(scope="session")
def test_root() -> Path:
    """本次测试专用的临时根目录，**不删旧目录**。

    带文件守卫的宿主里，session 结束时的清理（rmtree）会被判成批量删除而中断，
    所以改成「每次运行一个新目录」，旧目录由使用者在方便时手动清：
        rm -rf /tmp/nv_pytest
    用 PID 区分并发运行，避免两个进程互相覆盖。
    """
    root = _temproot() / f"run-{os.getpid()}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _seed_index(src_root: Path, dst_root: Path) -> None:
    """把现成的索引库带进临时 Vault，省掉每个用例一次全量重建。

    全量索引要逐份读 800 多个 Markdown；在当前宿主里单次读盘约 18ms，
    一次重建就是 15 秒以上，60 个用例各自重建等于十几分钟空转。
    索引本身是派生层、可随时重建，复制它是安全的：`files` 表存了
    `(mtime 整秒, size)`，而 rsync 与 copy2 都保留秒级 mtime，指纹自然对得上。

    注意：`_copy_tree` 的排除表里有 `.novel_system`（避免把运行产物和临时目录
    递归复制进去），所以每个用例复制完必须**再单独补种**，不能只种在模板上。
    """
    src_dir = src_root / ".novel_system"
    if not src_dir.is_dir():
        return
    dst_dir = dst_root / ".novel_system"
    dst_dir.mkdir(parents=True, exist_ok=True)
    # 只复制主库与 WAL：`-shm` 是进程间的共享内存索引，跨进程复制反而可能
    # 让 SQLite 读到与主库不一致的页映射，交给它自己重建。
    for name in ("index.sqlite", "index.sqlite-wal"):
        source = src_dir / name
        if source.is_file():
            shutil.copy2(source, dst_dir / name)


@pytest.fixture(scope="session")
def vault_template(test_root: Path) -> Path:
    dst = test_root / "template" / "NovelVault"
    _copy_tree(VAULT_SRC, dst)
    _seed_index(VAULT_SRC, dst)
    return dst


@pytest.fixture
def tmp_path(test_root: Path, request: pytest.FixtureRequest) -> Path:
    """覆盖 pytest 内置的 `tmp_path`，不再依赖系统临时目录。

    内置实现走 `tmp_path_factory`，而它要在 `tempfile.gettempdir()` 下建
    `pytest-of-<user>`。受限宿主里那个目录会被安全策略拦掉，报
    `FileExistsError: mkdir '/private/var/.../pytest-of-unknown'`，
    于是「只想拿个空目录」的用例全数变 ERROR。这里直接开在 `test_root` 下，
    和 `vault_dir` 用同一套路径规则，行为与环境无关。
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in request.node.name)[:80]
    path = test_root / "tmp" / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def vault_dir(test_root: Path, vault_template: Path, request: pytest.FixtureRequest) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in request.node.name)[:80]
    dst = test_root / "cases" / safe / "NovelVault"
    _copy_tree(vault_template, dst)
    _seed_index(vault_template, dst)
    return dst


@pytest.fixture
def cfg(vault_dir: Path) -> Config:
    return Config.load(vault_dir)


@pytest.fixture
def orch(cfg: Config):
    instance = Orchestrator(cfg)
    try:
        yield instance
    finally:
        instance.close()


# --------------------------------------------------------------------- 辅助


def write_file(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def entity_path(root: Path, prefix: str = "CHAR") -> Path:
    """取一份现存的实体文件。

    测试不该假设某个具体人物一定在库里：示例项目「白骨渡」归档后，
    原先硬编码的 `CHAR-0001_沈砚.md` 就不存在了，用例会连带全挂。
    """
    for path in sorted((root / "01_Core_Memory").rglob(f"{prefix}-[0-9][0-9][0-9][0-9]_*.md")):
        if ".tmp_" not in path.name:
            return path
    raise AssertionError(f"临时 Vault 里找不到 {prefix} 实体，测试夹具失效")


def ensure_states(root: Path, ids: tuple[str, ...] = ("CHAR-0001", "CHAR-0002")) -> None:
    """给测试用人物补一份「当前状态」记录。

    连续性审计要求出场人物有状态底账；示例项目归档后这些 STATE 文件随之移走，
    完整闭环用例会因此从 CLEAN 掉到 NEEDS_ATTENTION。这里按需补，已存在则不动。
    """
    for cid in ids:
        path = root / f"02_Hot_Memory/Characters/STATE-{cid}.md"
        if path.exists():
            continue
        text = f"""---
id: STATE-{cid}
type: character_state
name: 当前状态
character_id: {cid}
as_of_event: ""
canon_status: confirmed
updated_at: 2026-02-01
---

# 当前状态

## 客观状态
- 位置：白骨渡西岸
- 身体状态：正常
- 伤势：无
- 精神状态：清醒
- 能力状态：正常
- 持有物：无
- 客观生死：存活

## 认知边界（角色以为的世界，不等于事实）
- 已知信息：无
- 未知信息：祠内是否有人

## 当前目标
- 查清西岸废祠

## 与其他角色关系状态
- 与 CHAR-0002 同行
"""
        write_file(root, f"02_Hot_Memory/Characters/STATE-{cid}.md", text)


def make_chapter(root: Path, chapter_id: str = "CH-0003", title: str = "废祠") -> str:
    """在临时 Vault 里造一章可写的大纲 + 场景细纲。"""
    scene_id = f"SCENE-{chapter_id}-001"
    outline = f"""---
id: {chapter_id}
type: chapter_outline
title: {title}
name: {title}
volume_id: VOL-0001
status: outline
canon_status: confirmed
created_at: 2026-02-01
updated_at: 2026-02-01
---

# {chapter_id}｜{title}

## 章节目标
让沈砚上岸，第一次踏进西岸废祠。

## POV
沈砚（第三人称限知）

## 时间与地点
长夜第五日子时，LOC-0002 白骨渡西岸

## 核心冲突
废墟里有人来过，但沈砚不确定是不是敌人。

## 必须发生的剧情事实
- 沈砚与苏未晞登岸，进入废祠。
- 废祠里有没冷的灯油。

## 禁止发生的内容
- 不得确认邢咎的生死。

## 角色出场
- CHAR-0001
- CHAR-0002

## 关联伏笔

## 场景列表
1. {scene_id}

## 章节结尾钩子
灯油还是温的。

## 作者批准
- [x] 已批准写作
"""
    write_file(root, f"04_Outline/Chapters/{chapter_id}.md", outline)

    scene = f"""---
id: {scene_id}
type: scene_beat
name: 场景 1：废祠初见
chapter_id: {chapter_id}
sequence: 1
status: confirmed
canon_status: confirmed
---

# 场景 1：废祠初见

- 场景目标：让两人登岸并进入废祠。
- POV：沈砚
- 地点：LOC-0002 白骨渡西岸
- 时间：子时中
- 参与角色：CHAR-0001、CHAR-0002
- 场景开始状态：船搁浅在西岸滩头
- 冲突：祠内似有人迹
- 情绪变化：警惕 → 更警惕
- 关键动作：沈砚摸到灯台，灯油未冷
- 信息揭示：有人刚离开不久
- 场景结束状态：两人站在废祠中庭
- 与下一场景的连接：无（本章结束）
- 必须遵守的事实：不得出现第二件承载物
- 禁止擅自扩展：不得让邢咎出场
"""
    write_file(root, f"04_Outline/Scenes/{scene_id}.md", scene)
    ensure_states(root)
    return chapter_id


def compose_draft(
    root: Path,
    chapter_id: str,
    prose: str,
    annotations: list[str],
    title: str = "废祠",
) -> Path:
    """把 writer 的骨架草稿换成一段真正文（模拟主笔成稿 / 作者改写）。"""
    body = (
        "## 正文\n\n"
        + prose.strip()
        + "\n\n---\n\n## 作者备注（不进入正文）\n"
        + "\n".join(annotations)
        + "\n"
    )
    meta = {
        "id": chapter_id,
        "type": "manuscript",
        "title": title,
        "name": title,
        "volume_id": "VOL-0001",
        "status": "draft",
        "canon_status": "confirmed",
        "word_count": vault.count_words(prose),
        "outline_ref": chapter_id,
        "preflight_ref": f"PREFLIGHT-{chapter_id}",
        "created_at": "2026-02-01",
        "updated_at": "2026-02-01",
    }
    path = root / ".novel_system" / "staging" / "draft" / f"{chapter_id}.draft.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    vault.write_atomic(path, vault.render_doc(meta, body))
    return path
