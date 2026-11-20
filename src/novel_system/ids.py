"""稳定 ID 分配（V4.0 §8.1）。

规则来自 PRD，逐条落在这里：

- 分配后不变，一经分配不得复用（**删除留下的空号保留**，不从洞里补）。
- 不允许「扫描最大 ID 后无锁加一」——所以有锁文件，见 `_alloc_lock`。
- 分配结果写入任务日志（这里写 `<runtime>/id-allocations.jsonl`）。
- 失败产生的空号可以保留。
- 事件不因章节重排改 ID。本模块只发号，不承担「改号」职责。

**位数按现状取 4 位**（`CHAR-0001`）。PRD §8.1 示例写的是 6 位
（`CHAR-000001`），而库内 986 份档案全是 4 位。改六位要动全库文件名与所有
引用，收益为零、风险极大，所以这里跟随现状，并在 `schema` 的 pattern 上
放宽到 4–6 位以兼容两种写法。**这是与 PRD 的一处已知偏离，已记入
docs/operations/known_risks.md。**

前缀表分两类：PRD 规范的 24 个，和 v1.0 已在用、PRD 未列的前缀。后者不是
「合法扩展」，是待迁移的包袱——保留它们是为了让分配器能认出现有号段，
不至于把 `SKL-0001` 当成未知前缀而重新从 1 开始发号。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import frontmatter as fm
from .errors import ConflictError, UsageError
from .storage import iter_files

MIN_WIDTH = 4
MAX_WIDTH = 6

# PRD §8.1 的正式前缀。
PRD_PREFIXES: dict[str, str] = {
    "CHAR": "人物",
    "LOC": "地点",
    "FAC": "势力",
    "ITEM": "物品",
    "RULE": "世界规则",
    "CH": "正文",
    "PLAN-SERIES": "全书规划",
    "PLAN-VOL": "卷规划",
    "PLAN-CH": "章节规划",
    "SCENE": "场景",
    "EVT": "事件",
    "AST": "断言",
    "CHANGE": "状态变化",
    "KNOW": "人物认知",
    "REV": "读者揭示",
    "FORESHADOW": "伏笔",
    "MISSION": "剧情内任务",
    "TASK": "执行任务",
    "ISSUE": "问题",
    "CR": "变更请求",
    "APR": "审批",
    "REL": "发布",
    "IDX": "索引",
    "IMP": "导入",
}

# v1.0 在用、PRD 未列。发号时认得出，但不鼓励新增。
LEGACY_PREFIXES: dict[str, str] = {
    "SKL": "技能（v1.0 遗留，PRD 无此前缀）",
    "CREATURE": "生物（PRD 视为人物／势力／生物角色的标签，非独立实体）",
    "WORLD": "世界条目（PRD 归入 RULE／LOC，待迁移）",
    "FATE": "命途（v1.0 遗留）",
    "ASSET": "素材（PRD 归入 ITEM 或资产库，待迁移）",
    "VOL": "卷（PRD 用 PLAN-VOL）",
    "RELSHIP": "关系档案（v1.0 遗留）",
    "REVIEW": "审阅记录（PRD 未定义 ID 类型）",
    "PREFLIGHT": "写前资料包（PRD 用任务承载）",
    "MEMORY": "记忆条目（PRD 用 KNOW／摘要）",
    "OUTLINE": "大纲（PRD 用 PLAN-*）",
    "LORE": "设定条目（v1.0 遗留）",
    "INGEST": "导入批次（PRD 用 IMP）",
    "RB": "回滚点（PRD 用 REL）",
}

PREFIXES: dict[str, str] = {**PRD_PREFIXES, **LEGACY_PREFIXES}

# CLI 的类型名 → 前缀。大小写、单复数都收，少一次「凭记忆拼前缀」。
TYPE_TO_PREFIX: dict[str, str] = {
    "CHARACTER": "CHAR",
    "CHAR": "CHAR",
    "PERSON": "CHAR",
    "LOCATION": "LOC",
    "PLACE": "LOC",
    "FACTION": "FAC",
    "ORGANIZATION": "FAC",
    "ORG": "FAC",
    "ITEM": "ITEM",
    "OBJECT": "ITEM",
    "RULE": "RULE",
    "CHAPTER": "CH",
    "TEXT": "CH",
    "VOLUME": "VOL",
    "SCENE": "SCENE",
    "EVENT": "EVT",
    "ASSERTION": "AST",
    "STATE_CHANGE": "CHANGE",
    "STATECHANGE": "CHANGE",
    "KNOWLEDGE": "KNOW",
    "REVELATION": "REV",
    "FORESHADOWING": "FORESHADOW",
    "FORESHADOW": "FORESHADOW",
    "MISSION": "MISSION",
    "TASK": "TASK",
    "ISSUE": "ISSUE",
    "CHANGE_REQUEST": "CR",
    "APPROVAL": "APR",
    "RELEASE": "REL",
    "INDEX": "IDX",
    "IMPORT": "IMP",
    "SKILL": "SKL",
    "CREATURE": "CREATURE",
    "WORLD": "WORLD",
    "FATE": "FATE",
    "ASSET": "ASSET",
    "RELATIONSHIP": "RELSHIP",
}

# 合法 ID = 字母段前缀 + **单个**数字段。前缀可多段（PLAN-CH），但每段必须是字母。
# `EVT-CH-0001-001` 这种「前缀里夹数字」的复合型 PRD §8.1 明令禁止，
# 这里直接判为不合法，让它落进 compound 清单而不是被误读成前缀 `EVT-CH-0001`。
ID_RE = re.compile(r"^(?P<prefix>[A-Z][A-Z]*(?:-[A-Z]+)*)-(?P<num>\d{3,6})$")
# 「看着像 ID 但形态不对」：多段大写/数字拼起来，且至少含一个数字。
LOOSE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")

ID_ALLOCATION_LOG = "id-allocations.jsonl"


def resolve_prefix(kind: str) -> str:
    """`CHARACTER` / `character` / `char` / `CHAR` 都认。未知前缀直接拒绝。"""
    raw = (kind or "").strip()
    if not raw:
        raise UsageError("未指定 ID 类型")
    upper = raw.upper()
    if upper in TYPE_TO_PREFIX:
        return TYPE_TO_PREFIX[upper]
    if upper in PREFIXES:
        return upper
    known = ", ".join(sorted(PREFIXES))
    raise UsageError(f"未知 ID 类型 {raw!r}。可用：{known}")


@dataclass(frozen=True)
class IdRecord:
    id: str
    prefix: str
    number: int
    source: str  # 项目内相对路径
    origin: str  # frontmatter / filename


def split_id(raw: str) -> tuple[str, int] | None:
    match = ID_RE.match(raw.strip())
    if not match:
        return None
    return match.group("prefix"), int(match.group("num"))


def is_compound_id(raw: str) -> bool:
    """看着像 ID、但不符合规范形态。两类都要收：

    - `EVT-CH-0001-001`：前缀里夹了数字段
    - `VOL-0001-S1-DETAIL`：编号后面挂后缀

    它们既不计入号段（取最大号会取到脏值），也不该被当成「不是 ID」而忽略——
    忽略的结果是这些对象在查重时完全隐身。
    """
    text = raw.strip()
    if split_id(text):
        return False
    return bool(LOOSE_ID_RE.match(text)) and any(ch.isdigit() for ch in text)


class IdRegistry:
    """扫全 Vault 收集 ID。只读，不缓存跨调用结果。"""

    def __init__(self, vault: Path, include_hidden: bool = False):
        self.vault = vault
        self.include_hidden = include_hidden
        self._records: list[IdRecord] | None = None
        self._compounds: list[tuple[str, str]] | None = None
        self._mismatched: list[tuple[str, str, str]] | None = None

    def _collect(self) -> list[IdRecord]:
        records: list[IdRecord] = []
        compounds: list[tuple[str, str]] = []
        mismatched: list[tuple[str, str, str]] = []
        for path in iter_files(self.vault, skip_hidden=not self.include_hidden):
            if path.suffix.lower() != ".md":
                continue
            try:
                rel = path.relative_to(self.vault).as_posix()
            except ValueError:
                rel = str(path)
            head = path.stem.split("_")[0].strip()
            from_name = split_id(head)

            meta = fm.read(path)
            raw_id = str(meta.get("id") or "").strip()
            if "{{" in raw_id:
                raw_id = ""  # 模板占位符不是身份

            if raw_id:
                # frontmatter 是权威。文件名与它不符只记下来，不额外占一个号——
                # 无条件双记会制造假重复：VOL-0001_<细纲>.md 的 id 是
                # VOL-0001-S1-DETAIL，同一卷的五份文档各是不同对象，却被文件名判成同一个。
                from_meta = split_id(raw_id)
                if from_meta:
                    records.append(IdRecord(raw_id, from_meta[0], from_meta[1], rel, "frontmatter"))
                elif is_compound_id(raw_id):
                    compounds.append((raw_id, rel))
                if from_name and head != raw_id:
                    mismatched.append((rel, raw_id, head))
                continue

            # 没有 frontmatter id 才退回文件名。
            if from_name:
                records.append(IdRecord(head, from_name[0], from_name[1], rel, "filename"))
            elif is_compound_id(head):
                compounds.append((head, rel))
        self._compounds = compounds
        self._mismatched = mismatched
        return records

    @property
    def records(self) -> list[IdRecord]:
        if self._records is None:
            self._records = self._collect()
        return self._records

    @property
    def compounds(self) -> list[tuple[str, str]]:
        """不规范的复合 ID：(id, 出处)。PRD 禁止，待迁移。"""
        if self._compounds is None:
            self._records = self._collect()
        return self._compounds or []

    @property
    def mismatched(self) -> list[tuple[str, str, str]]:
        """(出处, frontmatter id, 文件名 id)。文件名与身份不符，值得提示但不等于重号。"""
        if self._mismatched is None:
            self._records = self._collect()
        return self._mismatched or []

    def for_prefix(self, prefix: str) -> list[IdRecord]:
        return [r for r in self.records if r.prefix == prefix]

    def max_number(self, prefix: str) -> int:
        numbers = [r.number for r in self.for_prefix(prefix)]
        return max(numbers) if numbers else 0

    def collisions(self) -> dict[str, list[IdRecord]]:
        """同一 ID 出现在多处。键是 ID，值是所有出处。"""
        buckets: dict[str, list[IdRecord]] = {}
        for record in self.records:
            buckets.setdefault(record.id, []).append(record)
        return {k: v for k, v in buckets.items() if len({r.source for r in v}) > 1}

    def stray_compound(self, prefix: str) -> list[tuple[str, str]]:
        """`EVT-CH-0001-001` 这类复合型，按「首段前缀」归到 EVT 名下。

        PRD 禁止这种形态，但库里有——发号时要提醒作者「这个前缀看着是空的，
        其实已经有一批旧号」。它们不参与取号，所以新号从 0001 开始不会撞。
        """
        return [(raw, src) for raw, src in self.compounds if raw.startswith(prefix + "-")]


class IdAllocator:
    """分配入口。所有正式 ID 都应经这里发出。"""

    def __init__(self, vault: Path, runtime: Path | None, actor: str = "", lock_timeout: float = 5.0):
        self.vault = vault
        self.runtime = runtime
        self.actor = actor or "unknown"
        self.lock_timeout = lock_timeout

    # ---- 锁 ----------------------------------------------------------
    def _lock_path(self) -> Path:
        if self.runtime is None:
            raise UsageError(
                "未配置 Runtime：ID 分配必须留下审计记录，不能在没有 Runtime 的情况下发号"
            )
        return self.runtime / "locks" / "id-allocate.lock"

    def _acquire(self, timeout: float | None = None):
        path = self._lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + (self.lock_timeout if timeout is None else timeout)
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if time.monotonic() > deadline:
                    holder = ""
                    try:
                        holder = path.read_text(encoding="utf-8").strip()
                    except OSError:
                        pass
                    raise ConflictError(
                        f"另一个进程正在分配 ID（锁 {path}，持有者 {holder or '未知'}）。"
                        "确认没有别的 novel 在跑后可删除该锁文件。"
                    ) from None
                time.sleep(0.05)
                continue
            os.write(fd, f"pid={os.getpid()} at={time.time():.0f}".encode("utf-8"))
            os.close(fd)
            return path

    def _release(self, path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            # 锁删不掉不该让已成功的分配变成失败；下次会因「持有者」提示人工处理。
            pass

    # ---- 分配 --------------------------------------------------------
    def allocate(
        self,
        kind: str,
        count: int = 1,
        reason: str = "",
        dry_run: bool = False,
        allow_dirty: bool = False,
    ) -> list[str]:
        if count < 1:
            raise UsageError("--count 至少为 1")
        prefix = resolve_prefix(kind)
        registry = IdRegistry(self.vault)

        # 只拦本前缀的重号。ASSET 撞号不该让 CHAR 也发不出号——那是两件事，
        # 全库重号由 validate 负责报。带病发号只在**本前缀**有歧义时才危险。
        dirty = {
            eid: recs
            for eid, recs in registry.collisions().items()
            if (split_id(eid) or (None,))[0] == prefix
        }
        if dirty and not allow_dirty:
            detail = "；".join(
                f"{eid}（{'、'.join(sorted({r.source for r in recs}))}）"
                for eid, recs in sorted(dirty.items())[:5]
            )
            raise ConflictError(
                f"{prefix} 前缀已有 {len(dirty)} 个重复 ID，先修完再发号：{detail}\n"
                "重复存在时继续发号，会让「按 ID 取对象」永远有歧义。"
                "确认要带病发号请加 --allow-dirty。"
            )

        lock = None if dry_run else self._acquire()
        try:
            # 取号要同时看两处：现存文件里的最大号，和审计里发过的最大号。
            # 只看前者会在删档后从洞里补号——那等于让旧引用指到另一个对象上。
            base = max(registry.max_number(prefix), self._historical_max(prefix))
            issued = [f"{prefix}-{n:0{MIN_WIDTH}d}" for n in range(base + 1, base + count + 1)]
            # 锁内复查：别的进程可能刚发过号。
            taken = {r.id for r in IdRegistry(self.vault).records}
            clash = [i for i in issued if i in taken]
            if clash:
                raise ConflictError(f"分配结果与现有 ID 冲突：{clash}")
            if not dry_run:
                self._record(prefix, issued, base, reason)
            return issued
        finally:
            if lock is not None:
                self._release(lock)

    def _record(self, prefix: str, ids: list[str], base: int, reason: str) -> None:
        assert self.runtime is not None
        path = self.runtime / ID_ALLOCATION_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "prefix": prefix,
                "ids": ids,
                "previous_max": base,
                "actor": self.actor,
                "reason": reason,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _historical_max(self, prefix: str) -> int:
        """审计里该前缀发到过的最大号。文件被删了，号也不能回到前面的洞里。"""
        best = 0
        for row in self.history(limit=100_000):
            if row.get("prefix") != prefix:
                continue
            for raw in row.get("ids") or []:
                parsed = split_id(str(raw))
                if parsed and parsed[0] == prefix:
                    best = max(best, parsed[1])
        return best

    def history(self, limit: int = 20) -> list[dict]:
        if self.runtime is None:
            return []
        path = self.runtime / ID_ALLOCATION_LOG
        if not path.exists():
            return []
        rows: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows[-limit:]
