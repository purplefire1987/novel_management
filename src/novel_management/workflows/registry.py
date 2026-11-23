"""声明式工作流目录（V1.0 §10）。

**为什么要有这一层**：V1.0 §10 第一句就是「工作流必须使用声明式定义，不把流程
只写在 Agent Prompt 中」。写进 Prompt 的流程有三个问题——改一次要改九份文件、
没有任何东西能检查它是否完整、也没法回答「WF-14 到底该有几步」。
放在 YAML 里，`novel workflow validate` 能逐字段查。

**目录在哪**：`workflows/catalog.yaml`，和 `schemas/` 一样在仓库根，
不在包内（它是数据，不是代码）。探测顺序：`NOVEL_WORKFLOWS` 环境变量 →
包内 `workflows/` → 仓库根 `workflows/` → 当前目录。这一条和 `schema.py`
的 `_find_root` 是同一个套路，差别只在文件名。

**两级结构**：catalog 声明「有哪些步、卡在哪儿」，`workflows/<slug>/workflow.yaml`
写「这一步具体怎么判」。`detail_ref` 把两者连起来。两份都写细则迟早对不上，
所以 catalog 里只放一步一句的 `action`，不复制细则。

**实例**：`run/pause/resume` 是**记账**，不是执行器。它们只把「谁在什么时候
把哪条工作流推到哪一步」写到 `<runtime>/workflows/`，不替 Agent 干活。
理由：真执行器要能中断、能续跑、能回滚，那需要事务——而 22 步发布协议已经
有事务了，再写一个平行的第二套只会让「到底以谁的状态为准」变成新的谜。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..config import ConfigError
from ..domain.errors import UsageError

CATALOG_NAME = "catalog.yaml"

# V1.0 §10 要求每个工作流定义「至少包含」的字段。
# 少一个就报错，不给默认值——默认值会让「漏写」看起来像「写了个空的」。
REQUIRED_FIELDS = (
    "id",
    "name",
    "version",
    "description",
    "trigger",
    "preconditions",
    "input_schema",
    "output_schema",
    "agents",
    "tool_permissions",
    "steps",
    "quality_gates",
    "failure_handling",
    "pause_resume",
    "retry_policy",
    "idempotency",
    "audit_events",
    "acceptance",
)

# V1.0 §10 目录的 20 个编号，顺序固定。用来查「有没有漏一个」。
EXPECTED_IDS = tuple(f"WF-{n:02d}" for n in range(20))

INSTANCE_STATES = ("running", "paused", "completed", "failed", "cancelled")


def catalog_path(explicit: Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    env = os.environ.get("NOVEL_WORKFLOWS")
    if env:
        candidates.append(Path(env).expanduser() / CATALOG_NAME)
    candidates.extend(
        (
            Path(__file__).resolve().parent / CATALOG_NAME,
            Path(__file__).resolve().parents[3] / "workflows" / CATALOG_NAME,
            Path.cwd() / "workflows" / CATALOG_NAME,
        )
    )
    for path in candidates:
        if path.is_file():
            return path
    raise ConfigError(
        "找不到工作流目录 catalog.yaml。依次找过："
        + "、".join(str(p) for p in candidates)
        + "。可用 NOVEL_WORKFLOWS 指定目录。"
    )


def load_catalog(explicit: Path | None = None) -> dict:
    from .. import config as cfg_mod

    path = catalog_path(explicit)
    data = cfg_mod.read_yaml(path) or {}
    if not isinstance(data, dict) or "workflows" not in data:
        raise ConfigError(f"{path} 不是工作流目录：缺少顶层 workflows 列表")
    return data


def workflows(explicit: Path | None = None) -> list[dict]:
    return [wf for wf in (load_catalog(explicit).get("workflows") or []) if isinstance(wf, dict)]


def find(workflow_id: str, explicit: Path | None = None) -> dict:
    wanted = (workflow_id or "").strip().upper()
    for wf in workflows(explicit):
        if str(wf.get("id", "")).strip().upper() == wanted:
            return wf
    known = ", ".join(str(wf.get("id")) for wf in workflows(explicit))
    raise UsageError(f"未知工作流 {workflow_id!r}。known：{known}")


def detail_file(wf: dict, explicit: Path | None = None) -> Path | None:
    """`detail_ref` 指向的分步细则文件。没有就返回 None（不是错误）。"""
    slug = str(wf.get("detail_ref") or "").strip()
    if not slug:
        return None
    base = catalog_path(explicit).parent
    path = base / slug / "workflow.yaml"
    return path if path.is_file() else None


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate(explicit: Path | None = None) -> list[str]:
    """返回问题清单。空列表 = 通过。

    查六类：编号齐不齐、必填字段缺不缺、引用有没有指到空处、
    步骤依赖有没有指到不存在的步、工具权限里的命令名认不认得、幂等键有没有。
    """
    from ..agents import registry as agents_mod

    problems: list[str] = []
    catalog = load_catalog(explicit)
    entries = workflows(explicit)

    seen: dict[str, int] = {}
    for wf in entries:
        wid = str(wf.get("id") or "").strip()
        if not wid:
            problems.append("有一条工作流没有 id")
            continue
        seen[wid] = seen.get(wid, 0) + 1
    for wid, n in seen.items():
        if n > 1:
            problems.append(f"{wid} 出现了 {n} 次；同一编号只能有一条定义")

    missing = [wid for wid in EXPECTED_IDS if wid not in seen]
    if missing:
        problems.append(
            f"V1.0 §10 的 20 条工作流缺 {len(missing)} 条：{', '.join(missing)}"
        )
    extra = [wid for wid in seen if wid not in EXPECTED_IDS]
    if extra:
        problems.append(f"编号不在 V1.0 §10 目录里：{', '.join(sorted(extra))}")

    for wf in entries:
        wid = str(wf.get("id") or "?")
        for field in REQUIRED_FIELDS:
            if field not in wf:
                problems.append(f"{wid} 缺字段 {field}（V1.0 §10 要求「至少包含」）")

        # 步骤依赖指空是静默死链：图上看不出来，跑的时候才知道。
        step_ids = {str(s.get("id")) for s in (wf.get("steps") or []) if isinstance(s, dict)}
        for step in wf.get("steps") or []:
            if not isinstance(step, dict):
                problems.append(f"{wid} 的 steps 里有非映射项")
                continue
            for dep in step.get("depends_on") or []:
                if dep not in step_ids:
                    problems.append(f"{wid} 的步骤 {step.get('id')} 依赖 {dep}，但该步不存在")

        for role in wf.get("agents") or []:
            if role not in agents_mod.ROLE_IDS:
                problems.append(
                    f"{wid} 的 agents 里有未知角色 {role!r}；九角色是 "
                    + "、".join(agents_mod.ROLE_IDS)
                )

        perms = wf.get("tool_permissions")
        if not isinstance(perms, dict) or not perms:
            problems.append(f"{wid} 的 tool_permissions 不是非空映射")
        elif not (perms.get("deny") or []):
            problems.append(
                f"{wid} 的 tool_permissions.deny 是空的；任何工作流都不该有「什么都允许」的权限面"
            )

        retry = wf.get("retry_policy")
        if not isinstance(retry, dict) or "max_attempts" not in retry:
            problems.append(f"{wid} 的 retry_policy 缺 max_attempts")

        idem = wf.get("idempotency")
        if not isinstance(idem, dict) or not str(idem.get("key") or "").strip():
            problems.append(f"{wid} 的 idempotency 缺 key（重跑靠什么判「同一次」）")

        slug = str(wf.get("detail_ref") or "").strip()
        if slug and detail_file(wf, explicit) is None:
            problems.append(f"{wid} 的 detail_ref={slug} 指向的分步细则不存在")

    if catalog.get("version") is None:
        problems.append("catalog 缺 version")
    return problems


# ---------------------------------------------------------------------------
# 实例记账
# ---------------------------------------------------------------------------


class InstanceStore:
    """`<runtime>/workflows/<wf_id>/<instance>.json`。

    一个实例 = 一次「把某条工作流推到某一步」的账。没有执行器，只有记账——
    理由是「谁在跑」这件事已经由任务（`novel task`）回答了，这里只补
    「跑到哪一步、为什么停」。
    """

    def __init__(self, runtime: Path | None):
        if runtime is None:
            raise UsageError("未配置 Runtime：工作流实例必须留痕，不能在没有 Runtime 的情况下开跑")
        self.root = Path(runtime) / "workflows"

    def _dir(self, workflow_id: str) -> Path:
        return self.root / workflow_id

    def list(self, workflow_id: str | None = None) -> list[dict]:
        if not self.root.is_dir():
            return []
        roots = [self._dir(workflow_id)] if workflow_id else sorted(
            p for p in self.root.iterdir() if p.is_dir()
        )
        out: list[dict] = []
        for directory in roots:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                try:
                    out.append(json.loads(path.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError):
                    continue
        return out

    def active(self, workflow_id: str) -> dict | None:
        for inst in self.list(workflow_id):
            if inst.get("state") == "running":
                return inst
        return None

    def open(self, wf: dict, actor: str, note: str = "") -> dict:
        """开一个实例。**同一工作流已有 running 实例时拒绝**。

        这条不是洁癖：同一流程并行跑两份，写到同一个目录，谁覆盖谁全看运气。
        """
        wid = str(wf["id"])
        current = self.active(wid)
        if current is not None:
            raise UsageError(
                f"{wid} 已有运行中的实例 {current.get('instance_id')}（{current.get('actor')} 于 "
                f"{current.get('started_at')}）。先 pause/complete 它，或确认那份是废的再 --force。"
            )
        stamp = time.strftime("%Y%m%d-%H%M%S")
        seq = len(self.list(wid)) + 1
        inst = {
            "instance_id": f"{wid}-{stamp}-{seq:02d}",
            "workflow_id": wid,
            "workflow_version": wf.get("version"),
            "state": "running",
            "current_step": (wf.get("steps") or [{}])[0].get("id"),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "actor": actor or "unknown",
            "paused_at": None,
            "paused_reason": None,
            "note": note,
            "history": [{"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": "run", "actor": actor}],
        }
        self._write(inst)
        return inst

    def set_state(self, instance_id: str, state: str, actor: str, reason: str = "") -> dict:
        if state not in INSTANCE_STATES:
            raise UsageError(f"未知实例状态 {state}（可用 {', '.join(INSTANCE_STATES)}）")
        for inst in self.list():
            if inst.get("instance_id") != instance_id:
                continue
            inst["state"] = state
            if state == "paused":
                inst["paused_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                inst["paused_reason"] = reason
            elif state == "running":
                inst["paused_at"] = None
                inst["paused_reason"] = None
            inst.setdefault("history", []).append(
                {
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "event": state,
                    "actor": actor,
                    "reason": reason,
                }
            )
            self._write(inst)
            return inst
        raise UsageError(f"找不到实例 {instance_id}")

    def _write(self, inst: dict) -> None:
        directory = self._dir(str(inst["workflow_id"]))
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{inst['instance_id']}.json"
        path.write_text(json.dumps(inst, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
