"""依赖失效：只有真正变了的那条依赖，才让产物失效。

AC-44 的原话是「产物依赖变化 → STALE，其他不受影响」。这句话的重点在后半句：
一个 `RULE-000003` 改了一个字，不该让全书每一份档案都变成「需要复核」——
那样 STALE 会多到没人看，等于没有。

## 四级判定

| 情形 | 结果 | 为什么 |
|---|---|---|
| 依赖目标的内容哈希与记录一致 | `VALID` | 没变 |
| 哈希不一致（HARD） | `STALE` | 依赖的规则变了，产物可能已经不成立 |
| 哈希不一致（SOFT） | `NEEDS_REVIEW` | 软依赖只影响观感，不该一票判死 |
| 没记 `content_hash`，或目标找不到 | `NEEDS_REVIEW` | **判不了不等于没问题** |

传递性只走一级：A 依赖 B、B 自己 `STALE` 时，A 记 `NEEDS_REVIEW` 而不是
`STALE`。因为 B 的失效是「它依赖的东西变了」，不等于 B 的内容变了；
把这种不确定性直接升级成 STALE，会让一次局部改动沿着引用链炸开。

## 权威副本在哪

`dependencies` 表是索引里的派生视图，权威在档案 frontmatter 的 `depends_on`。
所以 `check()` 直接读 Markdown，不读索引——索引可能是上一轮的。
`builder` 负责把同一份数据填进表里供查询用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import markdown as md
from ..errors import UsageError
from ..paths import ProjectPaths
from ..storage import sha256_file

VALID = "VALID"
STALE = "STALE"
NEEDS_REVIEW = "NEEDS_REVIEW"

HARD = "HARD"
SOFT = "SOFT"

SCAN_ROOTS = ("canon", "planning", "manuscripts", "assets", "ledger", "memory")


@dataclass
class DepRef:
    artifact_id: str
    content_hash: str = ""
    dependency_type: str = HARD

    def as_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "content_hash": self.content_hash,
            "dependency_type": self.dependency_type,
        }


@dataclass
class Record:
    artifact_id: str
    path: str
    depends_on: list[DepRef] = field(default_factory=list)


@dataclass
class State:
    artifact_id: str
    path: str
    status: str
    reasons: list[str] = field(default_factory=list)
    depends_on: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "path": self.path,
            "status": self.status,
            "reasons": list(self.reasons),
            "depends_on": list(self.depends_on),
        }


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
def scan(pp: ProjectPaths) -> dict[str, tuple[str, str]]:
    """项目里的产物：`id -> (项目内相对路径, sha256)`。

    只收有 `id` 的档案。没有 id 的东西无法被依赖，也无从失效。
    """
    out: dict[str, tuple[str, str]] = {}
    for key in SCAN_ROOTS:
        root = getattr(pp, key)
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            try:
                doc = md.parse(path.read_text(encoding="utf-8"), str(path))
            except Exception:
                continue
            artifact_id = str(doc.meta.get("id") or "").strip()
            if not artifact_id or "{{" in artifact_id:
                continue
            if artifact_id in out:
                # 同一 ID 两份（known_risks R-04 的真库现状）：先到先得，
                # 但不静默——`validate` 会把重号报出来，这里不替它掩盖。
                continue
            out[artifact_id] = (_rel(pp, path), sha256_file(path))
    return out


def records(pp: ProjectPaths) -> list[Record]:
    """读出所有声明了依赖的产物。"""
    out: list[Record] = []
    for key in SCAN_ROOTS:
        root = getattr(pp, key)
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            try:
                doc = md.parse(path.read_text(encoding="utf-8"), str(path))
            except Exception:
                continue
            artifact_id = str(doc.meta.get("id") or "").strip()
            refs = _refs(doc.meta)
            if not artifact_id or not refs:
                continue
            out.append(Record(artifact_id=artifact_id, path=_rel(pp, path), depends_on=refs))
    return out


def _refs(meta: dict) -> list[DepRef]:
    raw = meta.get("depends_on")
    if raw is None:
        raw = meta.get("dependencies")
    if not raw:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[DepRef] = []
    for item in raw:
        if isinstance(item, str):
            out.append(DepRef(artifact_id=item.strip()))
            continue
        if not isinstance(item, dict):
            continue
        target = str(item.get("artifact_id") or item.get("id") or "").strip()
        if not target:
            continue
        kind = str(item.get("dependency_type") or item.get("type") or HARD).strip().upper()
        out.append(
            DepRef(
                artifact_id=target,
                content_hash=str(item.get("content_hash") or item.get("sha256") or "").strip(),
                dependency_type=SOFT if kind == SOFT else HARD,
            )
        )
    return out


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
def check(pp: ProjectPaths) -> list[State]:
    """逐条判依赖是否还成立。一次扫描、一次比对，不做 N 次 IO。"""
    index = scan(pp)
    found = records(pp)

    # 先算出「谁的依赖真的变了」，再统一做一级传递。
    direct: dict[str, State] = {}
    for record in found:
        reasons: list[str] = []
        status = VALID
        for ref in record.depends_on:
            target = index.get(ref.artifact_id)
            if target is None:
                status = _worse(status, NEEDS_REVIEW)
                reasons.append(f"依赖目标不存在：{ref.artifact_id}")
                continue
            current = target[1]
            if not ref.content_hash:
                status = _worse(status, NEEDS_REVIEW)
                reasons.append(f"{ref.artifact_id} 没记 content_hash，无法判断是否变化")
                continue
            if current != ref.content_hash:
                status = _worse(status, STALE if ref.dependency_type == HARD else NEEDS_REVIEW)
                reasons.append(
                    f"{ref.artifact_id} 内容已变（{ref.dependency_type}）："
                    f"{ref.content_hash[:12]}… → {current[:12]}…"
                )
        direct[record.artifact_id] = State(
            artifact_id=record.artifact_id,
            path=record.path,
            status=status,
            reasons=reasons,
            depends_on=[ref.as_dict() for ref in record.depends_on],
        )

    # 一级传递：依赖的对象自己已经 STALE，本产物记 NEEDS_REVIEW。
    for state in direct.values():
        if state.status != VALID:
            continue
        for ref in state.depends_on:
            upstream = direct.get(str(ref["artifact_id"]))
            if upstream is not None and upstream.status == STALE:
                state.status = NEEDS_REVIEW
                state.reasons.append(f"上游 {upstream.artifact_id} 处于 STALE，本产物需复核")
                break

    return sorted(direct.values(), key=lambda item: (item.status != VALID, item.artifact_id))


def _worse(current: str, candidate: str) -> str:
    order = {VALID: 0, NEEDS_REVIEW: 1, STALE: 2}
    return candidate if order[candidate] > order[current] else current


def stale_ids(pp: ProjectPaths) -> set[str]:
    return {state.artifact_id for state in check(pp) if state.status == STALE}


def dependencies_of(pp: ProjectPaths, artifact_id: str) -> State | None:
    for state in check(pp):
        if state.artifact_id == artifact_id:
            return state
    return None


def _rel(pp: ProjectPaths, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(pp.root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def parse_ref(text: str) -> DepRef:
    """命令行 `ID@HASH[:HARD|SOFT]` 的解析。给 `deps add` 用。"""
    raw = (text or "").strip()
    if not raw:
        raise UsageError("依赖写法：`RULE-000003@<sha256>:HARD`（哈希可省略，但省略会判 NEEDS_REVIEW）")
    kind = HARD
    if ":" in raw:
        head, _, tail = raw.rpartition(":")
        if tail.strip().upper() in (HARD, SOFT):
            raw, kind = head, tail.strip().upper()
    target, _, digest = raw.partition("@")
    if not target.strip():
        raise UsageError(f"依赖缺少 artifact_id：{text}")
    if digest and not re.fullmatch(r"[0-9a-f]{64}", digest.strip()):
        raise UsageError(f"content_hash 必须是 64 位十六进制：{digest}")
    return DepRef(artifact_id=target.strip(), content_hash=digest.strip(), dependency_type=kind)


__all__ = [
    "HARD",
    "NEEDS_REVIEW",
    "SOFT",
    "STALE",
    "VALID",
    "DepRef",
    "Record",
    "State",
    "check",
    "dependencies_of",
    "parse_ref",
    "records",
    "scan",
    "stale_ids",
]
