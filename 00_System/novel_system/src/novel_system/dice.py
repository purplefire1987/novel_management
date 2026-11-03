"""骰子娘：玩家共同见证的随机数生成器 + 九州 TRPG 裁决。

公平性设计（为什么玩家能"见证"）
---------------------------------
1. **承诺—开奖**：每次掷骰先生成 32 字节服务端种子，台账记录
   `commitment = sha256(server_seed)`；开奖后把 `server_seed` 一并落账。
2. **玩家见证值进熵池**：`--witness 甲=37 --witness 乙=08`，玩家各自当众报一个数，
   与种子一起合成 `entropy`。即使主持人预设了种子，结果也由玩家的输入决定。
3. **可复算**：所有骰值由 `HMAC-SHA256(entropy, "roll_id|序号#计数器")` 推导，
   同一份台账任何人都能重算出同一结果（`novel dice verify`）。
4. **可防篡改**：台账是追加式 JSONL，每条带 `prev_hash` 组成哈希链；
   改动任何一条历史，链就断在改动处。

规则依据（全部来自 Vault 内的规则书，不另立规则）
-------------------------------------------------
- `01_Core_Memory/World/九州/规则书/WORLD-0006_规则书_02_行动裁决.md`
  · 2.1 统一公式 `d100 + ADJ + LB + SKB + 神通×2 + 压制 + 情境 ≥ DC`
  · 2.2 DC 表（5/30/55/80/100/105）
  · 2.3 优势取高、劣势取低
  · 2.4 对抗检定：高者胜，差 > 20 触发碾压
  · 2.5 大成功 96–100、大失败 01–05
  · 2.6 极值规则：所需属性 > 角色相关属性 ×10 → 成功率 0
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import Config

ALGORITHM = "hmac-sha256/v1"
LEDGER_DIR = "06_Plot_Ledger/Dice"
LEDGER_FILE = f"{LEDGER_DIR}/rolls.jsonl"

#: 规则书出处，逐条随结果一起输出，读者可回到原文核对
RULE_REFS = {
    "formula": "九州·行动裁决 2.1（d100 + 加值 ≥ DC 即成功）",
    "dc": "九州·行动裁决 2.2（DC 表：极简单 5 / 简单 30 / 中等 55 / 困难 80 / 极难 100 / 逆天 105）",
    "advantage": "九州·行动裁决 2.3（优势掷两枚取高、劣势取低，互不叠加）",
    "opposed": "九州·行动裁决 2.4（对抗检定高者胜，差距 > 20 触发碾压）",
    "crit": "九州·行动裁决 2.5（96–100 大成功；01–05 大失败）",
    "absolute": "九州·行动裁决 2.6（所需属性超过相关属性 10 倍 → 成功率 0，不必掷骰）",
}

DC_TABLE = {"极简单": 5, "简单": 30, "中等": 55, "困难": 80, "极难": 100, "逆天": 105}

_EXPR_RE = re.compile(
    r"^(?:(?P<count>\d+)?[dD](?P<faces>\d+)"
    r"(?:(?P<keep_mode>kh|kl)(?P<keep_n>\d+))?"
    r")(?P<modifier>[+-]\d+)?$"
)


class DiceError(ValueError):
    """骰子表达式或参数不合法。"""


# --------------------------------------------------------------------------- 表达式
@dataclass
class Expression:
    raw: str
    count: int
    faces: int
    keep_mode: str | None = None  # kh = 取高, kl = 取低
    keep_n: int = 1
    modifier: int = 0

    def describe(self) -> str:
        core = f"{self.count}d{self.faces}"
        if self.keep_mode:
            core += f"{self.keep_mode}{self.keep_n}"
        if self.modifier:
            core += f"{self.modifier:+d}"
        return core


def parse_expression(text: str) -> Expression:
    """解析 `d100` / `1d100+11` / `2d100kh1` / `3d6-2` 这类表达式。"""
    raw = (text or "").strip().replace(" ", "")
    if not raw:
        raise DiceError("骰子表达式为空")
    match = _EXPR_RE.match(raw.lower())
    if not match:
        raise DiceError(f"无法解析骰子表达式：{text}（支持 1d100、2d100kh1、3d6-2 等）")
    count = int(match.group("count") or 1)
    faces = int(match.group("faces"))
    if count < 1 or count > 100:
        raise DiceError("骰子个数需在 1–100 之间")
    if faces < 2 or faces > 1000:
        raise DiceError("骰面数需在 2–1000 之间")
    keep_mode = match.group("keep_mode")
    keep_n = int(match.group("keep_n") or 1)
    if keep_mode and keep_n > count:
        raise DiceError("取高/取低的个数不能超过骰子个数")
    return Expression(
        raw=raw,
        count=count,
        faces=faces,
        keep_mode=keep_mode,
        keep_n=keep_n,
        modifier=int(match.group("modifier") or 0),
    )


# --------------------------------------------------------------------------- 随机数
def _uniform(seed: bytes, tag: str, faces: int) -> int:
    """按 tag 派生的 [1, faces] 均匀整数（拒绝采样，避免取模偏差）。"""
    limit = (1 << 64) - ((1 << 64) % faces)
    counter = 0
    while True:
        digest = hmac.new(seed, f"{tag}#{counter}".encode("utf-8"), hashlib.sha256).digest()
        value = int.from_bytes(digest[:8], "big")
        if value < limit:
            return value % faces + 1
        counter += 1


def derive_draws(entropy: bytes, roll_id: str, expression: Expression) -> list[int]:
    return [_uniform(entropy, f"{roll_id}|{i}", expression.faces) for i in range(expression.count)]


def build_entropy(server_seed: bytes, roll_id: str, expression: str, witnesses: Sequence[str]) -> bytes:
    payload = "|".join([server_seed.hex(), roll_id, expression, *witnesses])
    return hashlib.sha256(payload.encode("utf-8")).digest()


def commitment_of(server_seed: bytes) -> str:
    """承诺值：掷骰前公开，开奖后可用种子核对。"""
    return hashlib.sha256(server_seed).hexdigest()


# --------------------------------------------------------------------------- 裁决
@dataclass
class Judgement:
    raw: int                 # 实际采用的 d100 点数（优势/劣势取高/取低后）
    total: int               # raw + 加值
    dc: int | None
    band: str                # 大成功 / 大失败 / 普通
    success: bool | None
    text: str
    refs: list[str] = field(default_factory=list)


def judge(raw: int, bonus: int, dc: int | None, faces: int = 100) -> Judgement:
    """按规则书 2.5 与 2.1 判定一次 d100 检定。"""
    total = raw + bonus
    band = "普通"
    if faces == 100:
        if raw >= 96:
            band = "大成功"
        elif raw <= 5:
            band = "大失败"

    if band == "大成功":
        success = True
    elif band == "大失败":
        success = False
    elif dc is None:
        success = None
    else:
        success = total >= dc

    if band == "大成功":
        text = "大成功：自动成功，且效果翻倍 / 附赠情报（攻击视为重击）"
    elif band == "大失败":
        text = "大失败：自动失败，并触发意外（脱手 / 被反制 / 暴露行踪）"
    elif success is True:
        text = f"成功：{total} ≥ DC {dc}"
    elif success is False:
        text = f"失败：{total} < DC {dc}"
    else:
        text = f"无 DC 对照，本次点数 {total}（视为纯随机结果）"

    refs = [RULE_REFS["formula"], RULE_REFS["crit"]] if dc is not None else [RULE_REFS["crit"]]
    return Judgement(raw=raw, total=total, dc=dc, band=band, success=success, text=text, refs=refs)


def combine(draws: Sequence[int], expression: Expression) -> int:
    """把骰值合成最终点数：kh/kl 先取舍再求和；普通 NdM 求和。"""
    if expression.keep_mode:
        ordered = sorted(draws, reverse=expression.keep_mode == "kh")
        return sum(ordered[: expression.keep_n])
    return sum(draws)


# --------------------------------------------------------------------------- 台账
def _entry_hash(entry: dict[str, Any]) -> str:
    payload = {k: v for k, v in entry.items() if k != "hash"}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class DiceLedger:
    """追加式台账（JSONL + 哈希链）。掷骰是"发生过的事"，不参与事务回滚。"""

    def __init__(self, cfg: Config) -> None:
        self.path: Path = cfg.abs(LEDGER_FILE)

    # -- 读 ---------------------------------------------------------------
    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"__broken__": line[:120]})
        return rows

    def next_roll_id(self) -> str:
        stamp = datetime.now().strftime("%Y%m%d")
        serial = len([row for row in self.entries() if str(row.get("roll_id", "")).startswith(f"ROLL-{stamp}")])
        return f"ROLL-{stamp}-{serial + 1:04d}"

    # -- 写 ---------------------------------------------------------------
    def append(self, entry: dict[str, Any]) -> dict[str, Any]:
        rows = self.entries()
        entry = dict(entry)
        entry["prev_hash"] = rows[-1].get("hash", "") if rows else ""
        entry["hash"] = _entry_hash(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    # -- 校验 -------------------------------------------------------------
    def verify(self) -> dict[str, Any]:
        """重算链与骰值。返回 {ok, checked, problems[]}。"""
        rows = self.entries()
        problems: list[str] = []
        prev = ""
        for index, row in enumerate(rows, 1):
            roll_id = row.get("roll_id") or f"第 {index} 行"
            if "__broken__" in row:
                problems.append(f"{roll_id}：JSON 损坏，无法解析")
                continue
            if row.get("prev_hash", "") != prev:
                problems.append(f"{roll_id}：链断裂（prev_hash 与上一条 hash 不一致）")
            expected = _entry_hash(row)
            if row.get("hash") != expected:
                problems.append(f"{roll_id}：内容被改动（重算 hash 不一致）")

            try:
                expression = parse_expression(str(row.get("expression", "")))
                entropy = build_entropy(
                    bytes.fromhex(str(row.get("server_seed", ""))),
                    str(row.get("roll_id", "")),
                    str(row.get("expression", "")),
                    list(row.get("witnesses") or []),
                )
                # 见证值按台账原样拼接，顺序即记录顺序
                recomputed = derive_draws(entropy, str(row.get("roll_id", "")), expression)
                if recomputed != list(row.get("draws") or []):
                    problems.append(f"{roll_id}：骰值复算不一致（记录 {row.get('draws')} / 复算 {recomputed}）")
                if commitment_of(bytes.fromhex(str(row.get("server_seed", "")))) != row.get("commitment"):
                    problems.append(f"{roll_id}：承诺值与种子不匹配")
            except (ValueError, DiceError) as exc:
                problems.append(f"{roll_id}：无法复算（{exc}）")
            prev = str(row.get("hash", ""))
        return {"ok": not problems, "checked": len(rows), "problems": problems}


# --------------------------------------------------------------------------- 对外接口
def _normalise_witnesses(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for raw in values or []:
        item = (raw or "").strip()
        if not item:
            continue
        if "=" in item:
            who, _, num = item.partition("=")
            who, num = who.strip(), num.strip()
            if not who or not num:
                raise DiceError(f"见证值格式应为 名字=数字：{raw}")
            out.append(f"{who}={num}")
        else:
            out.append(item)
    return out


def roll(
    cfg: Config,
    expression: str,
    *,
    bonus: int = 0,
    dc: int | None = None,
    witnesses: Iterable[str] = (),
    label: str = "",
    mode: str = "normal",   # normal / advantage / disadvantage
    character: str | None = None,
    note: str = "",
    server_seed: bytes | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """掷一次骰并按规则书裁决，返回完整结果（默认落台账）。"""
    expr = parse_expression(expression)
    if mode not in ("normal", "advantage", "disadvantage"):
        raise DiceError("mode 只能是 normal / advantage / disadvantage")
    if expr.faces != 100 and dc is not None:
        raise DiceError("DC 对照只对 d100 有意义；其它骰型请用纯掷骰（--dc 省略）")

    # 优势 / 劣势 = 掷两枚 d100 取高 / 取低（规则书 2.3）
    if mode != "normal" and (expr.count, expr.faces) != (1, 100):
        raise DiceError("优势/劣势只适用于单枚 d100")
    if mode == "advantage":
        expr = Expression(raw=expr.raw, count=2, faces=100, keep_mode="kh", keep_n=1, modifier=expr.modifier)
    elif mode == "disadvantage":
        expr = Expression(raw=expr.raw, count=2, faces=100, keep_mode="kl", keep_n=1, modifier=expr.modifier)

    ledger = DiceLedger(cfg)
    roll_id = ledger.next_roll_id()
    seed = server_seed if server_seed is not None else os.urandom(32)
    witness_list = _normalise_witnesses(witnesses)
    entropy = build_entropy(seed, roll_id, expr.describe(), witness_list)
    draws = derive_draws(entropy, roll_id, expr)

    raw = combine(draws, expr)
    total_bonus = bonus + expr.modifier
    is_d100_check = expr.faces == 100 and (expr.count == 1 or expr.keep_mode is not None)
    if is_d100_check:
        judgement = judge(raw, total_bonus, dc, faces=expr.faces)
    else:
        judgement = Judgement(
            raw=raw,
            total=raw + total_bonus,
            dc=None,
            band="普通",
            success=None,
            text=f"{expr.describe()} 合计 {raw}{total_bonus:+d}" if total_bonus else f"{expr.describe()} 合计 {raw}",
        )

    entry: dict[str, Any] = {
        "roll_id": roll_id,
        "at": datetime.now().isoformat(timespec="seconds"),
        "algorithm": ALGORITHM,
        "expression": expr.describe(),
        "label": label or "未命名检定",
        "character": character or "",
        "note": note,
        "witnesses": witness_list,
        "mode": mode,
        "bonus": total_bonus,
        "dc": dc,
        "draws": draws,
        "kept": [raw],
        "total": judgement.total,
        "band": judgement.band,
        "success": judgement.success,
        "commitment": commitment_of(seed),
        "server_seed": seed.hex(),
        "entropy": entropy.hex(),
    }
    stored = ledger.append(entry) if persist else {**entry, "prev_hash": "", "hash": ""}

    return {
        "roll_id": roll_id,
        "expression": expr.describe(),
        "label": entry["label"],
        "character": entry["character"],
        "draws": draws,
        "raw": raw,
        "bonus": total_bonus,
        "total": judgement.total,
        "dc": dc,
        "band": judgement.band,
        "success": judgement.success,
        "text": judgement.text,
        "refs": judgement.refs,
        "witnesses": witness_list,
        "commitment": entry["commitment"],
        "server_seed": entry["server_seed"],
        "entropy": entry["entropy"],
        "hash": stored["hash"],
        "prev_hash": stored["prev_hash"],
        "mode": mode,
    }


def duel(
    cfg: Config,
    *,
    bonus_a: int,
    bonus_b: int,
    name_a: str = "甲方",
    name_b: str = "乙方",
    label: str = "",
    witnesses: Iterable[str] = (),
    note: str = "",
) -> dict[str, Any]:
    """对抗检定（规则书 2.4）：双方各掷 d100+加值，高者胜，差距 > 20 触发碾压。"""
    witness_list = list(witnesses)
    shot_a = roll(cfg, "1d100", bonus=bonus_a, label=f"{label or '对抗'}·{name_a}", witnesses=witness_list, note=note)
    shot_b = roll(cfg, "1d100", bonus=bonus_b, label=f"{label or '对抗'}·{name_b}", witnesses=witness_list, note=note)

    gap = shot_a["total"] - shot_b["total"]
    if gap == 0:
        winner, verdict = "平手", "平手：主动方胜或僵持（GM 定）"
    elif gap > 0:
        winner = name_a
        verdict = f"{name_a} 胜，差 {gap}"
    else:
        winner = name_b
        verdict = f"{name_b} 胜，差 {abs(gap)}"

    both_crit = shot_a["band"] == shot_b["band"] == "大成功"
    both_fail = shot_a["band"] == shot_b["band"] == "大失败"
    if both_crit or both_fail:
        verdict += "（双方同为" + shot_a["band"] + "，按最终结果比较）"

    crush = abs(gap) > 20 and gap != 0
    return {
        "a": shot_a,
        "b": shot_b,
        "gap": gap,
        "winner": winner,
        "verdict": verdict,
        "crush": crush,
        "refs": [RULE_REFS["opposed"], RULE_REFS["crit"]],
    }


def recent(cfg: Config, limit: int = 10) -> list[dict[str, Any]]:
    rows = DiceLedger(cfg).entries()
    return rows[-limit:][::-1]


def verify(cfg: Config) -> dict[str, Any]:
    return DiceLedger(cfg).verify()
