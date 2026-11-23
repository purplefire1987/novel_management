"""稳定 ID 分配（V1.3 §27.2）。

形态：**`{TYPE}-{ULID}`**，例如 `ENT-01J8ZK4Q7M3X2VBN6YTR5WQ9PA`。
大写类型前缀 + 一个连字符 + 26 位 Crockford Base32。

**这已是第三套形态**，三代都记在这里，因为每一代都留下了守卫代码：

- V4.0：`CHAR-0001`（一类一前缀、连字符、4→6 位序号）。
- V1.0 §9.2：`ent_000001`（单前缀收实体、下划线、六位序号）。
  2026-11-22 作者裁定全库重排，1046 份迁到此形态。
- V1.3 §27.2：`ENT-<ULID>`。2026-11-23 迁移；活跃区已迁，
  冻结区（审阅报告、归档）保留旧号作为历史证据。

前两代形态现在只用于**识别并报告**，不再发号。识别能力不能拆：
数据面干净不等于守卫可以拆——`validate` 靠它发现「库里混进了旧号」。

规则逐条落在这里：

- 分配后不变，不因重命名、移动目录、合并章节或状态变化而改变。
- 不得用文件名、标题、章节序号或数组下标作为主键。
- 已使用 ID 不得重新分配，删除对象的 ID 不得复用。
- 不得使用 `ent_xxxxx`、`novel_xxxxx` 之类的占位格式。

**换形之后，三样东西一起消失了，不是被删掉而是失去存在理由：**

1. 「读全库最大号 + 1」这种发号方式。80 位随机部分保证不撞，
   发号不再需要先扫全库、也不需要跨进程互斥才能算对。
2. `BAND_LIMITS`。旧形态里 `ch` 必须留 999 上限把旧稿挡在 1001+ 之外；
   ULID 没有号段，这个上限自然作废。
3. `_historical_max`（「删档后不许回填号」）。序号会被复用才需要它；
   ULID 每次都是新值，回填在物理上不可能发生。

**锁仍然保留**：不为发号，为审计日志的追加写不互相覆盖——
两行 JSON 交错写入会让 `id-allocations.jsonl` 变成废文件。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from ..infrastructure.filesystem import frontmatter as fm
from .errors import ConflictError, UsageError
from ..infrastructure.filesystem.storage import iter_files
from . import ulid as ulid_mod

# V1.3 §27.2.1 的类型前缀注册表。**大写**，与 ID 里的一致。
#
# 规范用「例如」列了 18 个；`OUT`/`PLAN`/`AST`/`IDE`/`BKP`/`WFR` 是注册表扩展
# （§27.2.1 允许「由系统注册表定义」），因为库里有这些对象而规范没给前缀。
# 扩展必须写在这张表里，不能在代码别处临时拼——否则「有哪些前缀」会有两份真相。
PREFIXES: dict[str, str] = {
    # ---- 规范明确列出的 ----
    "NOV": "小说项目",
    "ENT": "世界知识实体（类别由 type 字段承载）",
    "REL": "关系",
    "EVT": "事件",
    "STC": "状态变化",
    "FSP": "伏笔",
    "SUS": "悬念",
    "MYS": "谜题",
    "SRC": "来源／导入批次",
    "RTP": "研究主题",
    "MEM": "记忆记录",
    "CH": "章节正文",
    "VOL": "卷",
    "SCN": "场景节拍",
    "TSK": "任务",
    "PRP": "提案／变更请求",
    "APR": "审批",
    "VER": "版本／发布",
    # ---- 注册表扩展（§27.2.1 允许）----
    "OUT": "纲要（总纲／细纲／分章表）",
    "PLAN": "规划文档（尚未归到 OUT/VOL/SCN 的）",
    "AST": "素材卡",
    "IDE": "灵感",
    "BKP": "备份",
    "WFR": "工作流运行",
}

# 旧形态的前缀。**只留名字用于拒绝，不再发号。**
#
# 两代混在一起收：V4.0 的大写（`CHAR`、`PLAN-VOL`）与 V1.0 的小写
# （`ent`、`plan`）。分开收没有意义——它们对调用方的作用相同：告诉人
# 「这不是规范形态，别再用」。
#
# **「拒绝发旧号」这一条不能丢**：数据面干净不等于守卫可以拆。
LEGACY_PREFIXES: frozenset[str] = frozenset(
    {
        "CHAR", "LOC", "FAC", "ITEM", "RULE", "SCENE", "FORESHADOW", "PLAN-VOL", "PLAN-CH",
        "ent", "ch", "plan", "scene", "fs", "evt", "rel", "sc", "task", "prop",
        "ver", "src", "bkp", "idea", "novel", "asset", "wf",
    }
)

# CLI 的类型名 → 前缀。大小写、单复数都收，少一次「凭记忆拼前缀」。
#
# V1.3 下世界知识依旧一律 `ENT`——区别只在 frontmatter 的 `type` 字段。
# 这条**没有变**：单前缀收实体是 V1.0 就定下的，V1.3 只是把下划线换成连字符、
# 把序号换成 ULID。曾经有人以为 V1.3 会把 `CHAR`/`LOC` 恢复成独立前缀，
# 那是误读：§27.2.1 的 18 个前缀里只有一个 `ENT`。
TYPE_TO_PREFIX: dict[str, str] = {
    # ---- 世界知识实体：一律 ENT ----
    "CHARACTER": "ENT",
    "CHAR": "ENT",
    "PERSON": "ENT",
    "LOCATION": "ENT",
    "PLACE": "ENT",
    "FACTION": "ENT",
    "ORGANIZATION": "ENT",
    "ORG": "ENT",
    "ITEM": "ENT",
    "OBJECT": "ENT",
    "SKILL": "ENT",
    "CREATURE": "ENT",
    "WORLD": "ENT",
    "FATE": "ENT",
    "DESTINY": "ENT",
    "RULE": "ENT",
    "ENTITY": "ENT",
    "ENT": "ENT",
    # ---- 其余 ----
    "CHAPTER": "CH",
    "TEXT": "CH",
    "MANUSCRIPT": "CH",
    "VOLUME": "VOL",
    "OUTLINE": "OUT",
    "PLAN": "PLAN",
    "SCENE": "SCN",
    "BEAT": "SCN",
    "FORESHADOW": "FSP",
    "FORESHADOWING": "FSP",
    "ASSET": "AST",
    "MATERIAL": "AST",
    "EVENT": "EVT",
    "EVT": "EVT",
    "RELATIONSHIP": "REL",
    "REL": "REL",
    "RELATION": "REL",
    "STATE_CHANGE": "STC",
    "STATECHANGE": "STC",
    "SUSPENSE": "SUS",
    "MYSTERY": "MYS",
    "TASK": "TSK",
    "IDEA": "IDE",
    "WORKFLOW": "WFR",
    "PROPOSAL": "PRP",
    "ISSUE": "PRP",
    "CHANGE_REQUEST": "PRP",
    "APPROVAL": "APR",
    "VERSION": "VER",
    "RELEASE": "VER",
    "BACKUP": "BKP",
    "SOURCE": "SRC",
    "IMPORT": "SRC",
    "RESEARCH_TOPIC": "RTP",
    "MEMORY": "MEM",
    "PROJECT": "NOV",
    "NOVEL": "NOV",
}

# 合法 ID = 大写前缀 + 连字符 + 26 位 Crockford Base32（V1.3 §27.2 / §27.2.2）。
#
# 前缀宽度放到 2..8：规范的 18 个前缀是 2–3 位，但注册表扩展（§27.2.1）可能
# 出现更长的，写死 3 位会让扩展前缀在**校验时**被判成非法——那时人要改的是
# 校验器，不是数据。
ID_RE = re.compile(r"^(?P<prefix>[A-Z][A-Z0-9]{1,7})-(?P<ulid>[0-9A-HJKMNP-TV-Z]{26})$")

# 前两代形态。**只用于识别**，`validate` 拿它报「库里还有旧号」。
#
# V1.0：`ent_000001`（小写前缀 + 下划线 + 六位序号）。
V1_SEQ_RE = re.compile(r"^(?P<prefix>[a-z]+)_(?P<num>\d{6})$")
# V4.0：`CHAR-0001`、`VOL-0001-S1`、`EVT-CH-0001-001`。
#
# 尾部那截 `(?:-[A-Z0-9]+)*` 是补的：`is_compound_id` 的 docstring 一直声明收
# 三类，第三类是「编号后面挂后缀」，但原来的正则以 `\d{3,6}$` 收尾，这类
# 根本匹配不上——声明与实现不符。放宽只影响「认出来并报告」，不影响发号。
V4_SEQ_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d{3,6}(?:-[A-Z0-9]+)*$")

ID_ALLOCATION_LOG = "id-allocations.jsonl"


def make_id(prefix: str, timestamp_ms: int | None = None) -> str:
    """按 V1.3 形态拼一个 ID。**唯一的拼接口**。"""
    return f"{prefix}-{ulid_mod.new_ulid(timestamp_ms)}"


def resolve_prefix(kind: str) -> str:
    """`ENT` / `ent` / `entity` / `ENTITY` 都认，返回大写前缀。

    `CHARACTER` 得到 `ENT` 而不是 `CHAR`——这是**故意的**：类型信息归
    frontmatter 的 `type` 字段，ID 只表达「这是一个世界知识实体」。
    返回大写是因为 ID 里就是大写，再让调用方 `.upper()` 一遍会在某个
    分支上漏掉，然后发出 `ent-01J8...` 这种混形 ID。
    """
    raw = (kind or "").strip()
    if not raw:
        raise UsageError("未指定 ID 类型")
    up = raw.upper()
    if up in PREFIXES:
        return up
    if up in TYPE_TO_PREFIX:
        return TYPE_TO_PREFIX[up]
    if up in LEGACY_PREFIXES:
        tgt = TYPE_TO_PREFIX.get(up)
        hint = f"。改用 --type {tgt}" if tgt else ""
        raise UsageError(f"前缀 {up} 属于旧形态（V4.0 / V1.0），已废弃{hint}")
    known = ", ".join(sorted(PREFIXES))
    raise UsageError(f"未知 ID 类型 {raw!r}。可用：{known}")


@dataclass(frozen=True)
class IdRecord:
    id: str
    prefix: str
    ulid: str
    source: str  # 项目内相对路径
    origin: str  # frontmatter / filename
    area: str = "formal"  # formal / pending / archive

    def __str__(self) -> str:
        return f"{self.id}@{self.source}[{self.area}]"


def split_id(raw: str) -> tuple[str, str] | None:
    """`ENT-01J8ZK...` → `("ENT", "01J8ZK...")`。不是当前形态一律 `None`。"""
    match = ID_RE.match((raw or "").strip())
    if not match:
        return None
    return match.group("prefix"), match.group("ulid")


def is_id(raw: str) -> bool:
    return split_id(raw) is not None


def legacy_form(raw: str) -> str | None:
    """旧号属于哪一代：`"v1_seq"` / `"v4_seq"` / `None`（当前形态或不是 ID）。

    分出代际是为了让报错能说清「这是上一轮迁移漏的」还是「这是更早的历史」
    ——两者的处置完全不同：前者是 bug，后者是要不要动的取舍。
    """
    text = (raw or "").strip()
    if not text or split_id(text):
        return None
    if V1_SEQ_RE.match(text):
        return "v1_seq"
    if V4_SEQ_RE.match(text):
        return "v4_seq"
    return None


def is_compound_id(raw: str) -> bool:
    """看着像 ID、但不是 V1.3 形态的旧号。三类都要收：

    - `EVT-CH-0001-001`：前缀里夹了数字段（V4.0 时代的复合号）
    - `PLAN-VOL-000001`：两段大写前缀
    - `VOL-0001-S1`：编号后面挂后缀

    判定用 `V4_SEQ_RE` 而不是「有没有连字符」——**它防的是「隐身」**：
    这些号既不计入任何统计，也不该被当成「不是 ID」而忽略，
    忽略的结果是它们在查重时完全看不见。
    """
    return legacy_form(raw) == "v4_seq" and (raw or "").strip().count("-") >= 2


def id_from_filename(stem: str) -> str | None:
    """文件名 → ID。两代三种命名都能认，返回**原文里的那个 ID 串**。

    这里**只能有一个实现**。之前 V1.0 时代这段逻辑抄了两份（`IdRegistry._collect`
    与 `inspect_cmd`），改形态时漏了一处，于是 `doctor` 说「id 与文件名错位 40 份」
    而 `validate` 说 0 份——两个命令对同一批文件给出相反结论。

    - V1.3：`ENT-01J8ZK..._怒涛府` → 取第一段（V1.3 ID 里没有下划线）
    - V1.0：`ent_000339_浑天-地动仪` → 取前两段（不能按 `-` 切，名字里有连字符）
    - V4.0：`VOL-0001_细纲`  / `VOL-0001-S1-DETAIL_细纲` → 取第一段
    """
    clean = (stem or "").strip()
    if not clean:
        return None
    parts = clean.split("_")
    head = parts[0].strip()
    if split_id(head) or legacy_form(head):
        return head
    if len(parts) >= 2:
        joined = f"{parts[0]}_{parts[1]}".strip()
        if split_id(joined) or legacy_form(joined):
            return joined
    return None


class IdRegistry:
    """扫全 Vault 收集 ID。只读，不缓存跨调用结果。

    **查重是分级的**，因为「同一个 ID 出现在两处」有三种完全不同的来由：

    - 正式区里出现两次：真撞号。按 ID 取对象永远有歧义，必须拦住（FAIL）。
    - 正式区一份 + 待发布区（staging/审阅）一份：**同一个对象在途**。
      拷贝是发布流程的正常中间态，判成撞号等于「发布一次就再也发不出号」。
    - 正式区一份 + 归档区一份：多半是历史归档没删干净，只告警（WARN）。

    区划由 `areas` 传入（`ProjectPaths.area_roots()`），不在这里写死目录名。
    **不传 areas 时全库一律按正式区处理**——宁可从严，也不能因为调用方忘了
    传参就悄悄放松查重。
    """

    def __init__(self, vault: Path, include_hidden: bool = False, areas: dict | None = None):
        self.vault = vault
        self.include_hidden = include_hidden
        self._records: list[IdRecord] | None = None
        self._legacy: list[tuple[str, str, str]] | None = None
        self._mismatched: list[tuple[str, str, str]] | None = None
        self._areas = self._normalise_areas(areas)

    def _normalise_areas(self, areas: dict | None) -> dict[str, list[Path]]:
        out: dict[str, list[Path]] = {"formal": [], "pending": [], "archive": []}
        for name, roots in (areas or {}).items():
            if name not in out:
                raise UsageError(f"未知 ID 区划：{name}（可用 formal / pending / archive）")
            out[name] = [Path(r).resolve() for r in roots]
        self._has_areas = any(out.values())
        if not self._has_areas:
            out["formal"] = [self.vault.resolve()]
        return out

    def area_of(self, path: Path) -> str:
        """formal / pending / archive / other。

        没给区划时全库都是 formal（从严）。给了区划时，落在三区之外的路径记 `other`
        ——它们不是正式区，不能算撞号；通常是 Vault 根的旧归档（如 `99_Archive/legacy_v1`），
        把那里的历史副本算成「正式区有两份」会让今天一个号都发不出去。
        """
        try:
            target = path.resolve()
        except OSError:
            return "formal"
        for name in ("formal", "pending", "archive"):
            for root in self._areas[name]:
                if target == root or root in target.parents:
                    return name
        return "other" if self._has_areas else "formal"

    def _collect(self) -> list[IdRecord]:
        records: list[IdRecord] = []
        legacy: list[tuple[str, str, str]] = []
        mismatched: list[tuple[str, str, str]] = []
        for path in iter_files(self.vault, skip_hidden=not self.include_hidden):
            if path.suffix.lower() != ".md":
                continue
            try:
                rel = path.relative_to(self.vault).as_posix()
            except ValueError:
                rel = str(path)
            area = self.area_of(path)
            file_id = id_from_filename(path.stem)
            from_name = split_id(file_id) if file_id else None

            meta = fm.read(path)
            raw_id = str(meta.get("id") or "").strip()
            if "{{" in raw_id:
                raw_id = ""  # 模板占位符不是身份

            if raw_id:
                # frontmatter 是权威。文件名与它不符只记下来，不额外占一个号——
                # 无条件双记会制造假重复：同一卷的五份文档各是不同对象。
                from_meta = split_id(raw_id)
                if from_meta:
                    records.append(
                        IdRecord(raw_id, from_meta[0], from_meta[1], rel, "frontmatter", area)
                    )
                else:
                    gen = legacy_form(raw_id)
                    if gen:
                        legacy.append((raw_id, rel, gen))
                if from_name and file_id != raw_id:
                    mismatched.append((rel, raw_id, file_id or ""))
                continue

            # 没有 frontmatter id 才退回文件名。
            if from_name:
                records.append(
                    IdRecord(file_id, from_name[0], from_name[1], rel, "filename", area)
                )
            elif file_id:
                gen = legacy_form(file_id)
                if gen:
                    legacy.append((file_id, rel, gen))
        self._legacy = legacy
        self._mismatched = mismatched
        return records

    @property
    def records(self) -> list[IdRecord]:
        if self._records is None:
            self._records = self._collect()
        return self._records

    @property
    def legacy(self) -> list[tuple[str, str, str]]:
        """旧形态 ID：(id, 出处, 代际 `v1_seq`/`v4_seq`)。只报告，不参与查重。

        **不能因为「冻结区里本来就有旧号」就不报**：报出来才能一眼看出
        活跃区还剩几份没迁。把这个列表整个吞掉，剩下的就只是「感觉迁完了」。
        """
        if self._legacy is None:
            self._records = self._collect()
        return self._legacy or []

    @property
    def compounds(self) -> list[tuple[str, str]]:
        """不规范的复合 ID：(id, 出处)。PRD 禁止，待迁移。"""
        return [(raw, src) for raw, src, _gen in self.legacy if is_compound_id(raw)]

    @property
    def mismatched(self) -> list[tuple[str, str, str]]:
        """(出处, frontmatter id, 文件名 id)。文件名与身份不符，值得提示但不等于重号。"""
        if self._mismatched is None:
            self._records = self._collect()
        return self._mismatched or []

    def for_prefix(self, prefix: str) -> list[IdRecord]:
        return [r for r in self.records if r.prefix == prefix]

    def count(self, prefix: str) -> int:
        """该前缀已发放多少个。V1.3 没有「最大号」这个概念了。"""
        return len(self.for_prefix(prefix))

    def latest(self, prefix: str) -> str | None:
        """该前缀已用的最后一个 ID（按 ULID 时间序）。

        用途从「算下一个号」变成「给人看一眼当前水位」——ULID 不靠上一个值
        推算下一个值，所以这里**只用于显示**，任何发号路径都不许依赖它。
        """
        got = sorted((r.ulid, r.id) for r in self.for_prefix(prefix))
        return got[-1][1] if got else None

    def collisions(self) -> dict[str, list[IdRecord]]:
        """同一 ID 出现在多处（不分区域）。键是 ID，值是所有出处。"""
        buckets: dict[str, list[IdRecord]] = {}
        for record in self.records:
            buckets.setdefault(record.id, []).append(record)
        return {k: v for k, v in buckets.items() if len({r.source for r in v}) > 1}

    def conflicts(self) -> dict[str, list[IdRecord]]:
        """**正式区内**同一 ID 的两份以上。这是真撞号，发号必须停。"""
        return {
            eid: recs
            for eid, recs in self.collisions().items()
            if len({r.source for r in recs if r.area == "formal"}) > 1
        }

    def tainted(self) -> dict[str, list[IdRecord]]:
        """正式区一份 + 非正式区也有一份（归档或区划外）。只告警，不拦发号。

        归档区里躺着 `legacy_v1` 这种整库快照时，这里会一次报出很多条，
        所以它是「报告用」的口径，**不参与发号拦截**。
        """
        out: dict[str, list[IdRecord]] = {}
        for eid, recs in self.collisions().items():
            areas = {r.area for r in recs}
            if "formal" in areas and ("archive" in areas or "other" in areas):
                out[eid] = recs
        return out

    def pending_copies(self) -> dict[str, list[IdRecord]]:
        """正式区一份 + 待发布区也有一份：同一对象在途。按设计如此，不是问题。"""
        out: dict[str, list[IdRecord]] = {}
        for eid, recs in self.collisions().items():
            areas = {r.area for r in recs}
            if "formal" in areas and "pending" in areas and not ({"archive", "other"} & areas):
                out[eid] = recs
        return out

    def stray_compound(self, prefix: str) -> list[tuple[str, str]]:
        """`EVT-CH-0001-001` 这类复合型，按「首段前缀」归到 `EVT` 名下。

        PRD 禁止这种形态，但历史区里可能有——发号时要提醒作者「这个前缀看着是空的，
        其实已经有一批旧号」。
        """
        probe = prefix.upper() + "-"
        return [(raw, src) for raw, src in self.compounds if raw.upper().startswith(probe)]


class IdAllocator:
    """分配入口。所有正式 ID 都应经这里发出。"""

    #: 撞号重试上限。26 位 ULID 撞号概率可忽略，设上限只为「假设随机源坏掉时
    #: 不要死循环」——真撞满 5 次说明随机源有问题，该报错而不是继续转。
    _COLLISION_RETRIES = 5

    def __init__(
        self,
        vault: Path,
        runtime: Path | None,
        actor: str = "",
        lock_timeout: float = 5.0,
        areas: dict | None = None,
    ):
        self.vault = vault
        self.runtime = runtime
        self.actor = actor or "unknown"
        self.lock_timeout = lock_timeout
        self.areas = areas

    # ---- 锁 ----------------------------------------------------------
    def _lock_path(self) -> Path:
        if self.runtime is None:
            raise UsageError(
                "未配置 Runtime：ID 分配必须留下审计记录，不能在没有 Runtime 的情况下发号"
            )
        return self.runtime / "locks" / "id-allocate.lock"

    def _holder_state(self, path: Path) -> tuple[str, bool | None]:
        """读出锁的持有者描述，并判断它是否还活着。

        返回 `(描述, 是否已死)`；`None` 表示**判断不了**（内容残缺、拿不到 pid、
        或者 pid 归别的用户）。判断不了时一律当活锁处理——把活的锁误判成死的
        会让两个进程同时追加审计行，代价比多等 2 秒大得多。
        """
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            return "未知", None
        m = re.search(r"pid=(\d+)", raw)
        if not m:
            # 写锁的进程可能刚 O_CREAT 完还没写内容就被杀了，文件是空的。
            # 这种「空锁」是不是陈旧，静态判断不了，交给超时提示人工确认。
            return raw or "未知（锁文件为空）", None
        pid = int(m.group(1))
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return raw, True
        except PermissionError:
            return raw, False
        except OSError:
            return raw, None
        return raw, False

    def _acquire(self, timeout: float | None = None):
        path = self._lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + (self.lock_timeout if timeout is None else timeout)
        stale_cleared = False
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                holder, dead = self._holder_state(path)
                # 陈旧锁自动接管：进程已经不在，锁上的审计行谁都写不进去。
                # 只接管一次，且必须确认 pid 已死——避免「两个进程互相判定对方陈旧」。
                if dead is True and not stale_cleared:
                    stale_cleared = True
                    try:
                        path.unlink()
                    except OSError:
                        raise ConflictError(
                            f"陈旧锁删不掉（锁 {path}，持有者 {holder} 已不在运行）。"
                            "请手工删除该文件后重试。"
                        ) from None
                    continue
                if time.monotonic() > deadline:
                    hint = (
                        "持有进程已不在运行，锁是陈旧残留——删掉该锁文件即可继续。"
                        if dead is True
                        else "确认没有别的 novel 在跑后可删除该锁文件。"
                    )
                    raise ConflictError(
                        f"另一个进程正在分配 ID（锁 {path}，持有者 {holder}）。{hint}"
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
            # 锁删不掉不该让已成功的分配变成失败。留下的锁里记着自己的 pid，
            # 本进程退出后 `_holder_state` 会认出它已死，下次发号自动接管。
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
        registry = IdRegistry(self.vault, areas=self.areas)

        # 只拦本前缀的**正式区内**重号。理由分两层：
        # 1. 只拦本前缀：AST 撞号不该让 ENT 也发不出号，全库重号由 validate 报。
        # 2. 只看正式区：staging 里那一份是同一对象的在途副本（T07 导入就是
        #    「先入 staging 再发布」），把它算成撞号等于发布一次就断掉后续发号。
        dirty = {
            eid: recs
            for eid, recs in registry.conflicts().items()
            if (split_id(eid) or (None,))[0] == prefix
        }
        if dirty and not allow_dirty:
            detail = "；".join(
                f"{eid}（{'、'.join(sorted({r.source for r in recs if r.area == 'formal'}))}）"
                for eid, recs in sorted(dirty.items())[:5]
            )
            raise ConflictError(
                f"{prefix} 前缀在正式区已有 {len(dirty)} 个重复 ID，先修完再发号：{detail}\n"
                "重复存在时继续发号，会让「按 ID 取对象」永远有歧义。"
                "确认要带病发号请加 --allow-dirty。"
            )

        lock = None if dry_run else self._acquire()
        try:
            # 锁内取号。**不再读全库最大号**——旧形态那是必须的（序号要从上一个
            # 值加一），现在只需要「别跟已有 ID 撞」。仍然要扫一遍库，因为撞号
            # 是唯一需要避免的事，而它只有对比现存记录才知道。
            taken = {r.id for r in IdRegistry(self.vault, areas=self.areas).records}
            issued: list[str] = []
            for _attempt in range(self._COLLISION_RETRIES):
                candidate = [make_id(prefix) for _ in range(count)]
                if not set(candidate) & taken and len(set(candidate)) == count:
                    issued = candidate
                    break
            if not issued:
                raise ConflictError(
                    f"连续 {self._COLLISION_RETRIES} 次生成的 ID 都与库内冲突（前缀 {prefix}）。\n"
                    "26 位 ULID 正常不会撞——请检查随机源（`domain/ulid.py`）是否被替换过。"
                )
            if not dry_run:
                self._record(prefix, issued, reason)
            return issued
        finally:
            if lock is not None:
                self._release(lock)

    def _record(self, prefix: str, ids: list[str], reason: str) -> None:
        assert self.runtime is not None
        path = self.runtime / ID_ALLOCATION_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "prefix": prefix,
                "ids": ids,
                "actor": self.actor,
                "reason": reason,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

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
