"""ULID 生成与校验（V1.3 §27.2）。

ULID 是 128 位标识符：高 48 位毫秒时间戳 + 低 80 位随机数，
用 Crockford Base32 编成 26 个字符。

**为什么用它而不是自增序号**——V1.3 §27.2 要求的形态是 `{TYPE}-{ULID}`，
但选型上它比序号好在三点，值得写下来：

1. 不需要发号锁。序号必须读全库最大号再加一，并发下要么加锁要么撞号；
   ULID 的随机部分让两台机器同时生成也不会撞。
2. 天然按时间排序。前 48 位是时间戳，字典序即创建序——序号的这个能力
   要靠「不要有空洞」维持，而空洞是删档的必然结果。
3. 没有号段上限。序号体系里 `ch` 必须留 999 上限把旧稿挡在 1001+ 之外
   （见 `BAND_LIMITS`），ULID 不存在这个问题。

**Crockford Base32 的字母表去掉了 I、L、O、U**：前三个防手抄混淆，
U 是为了避免拼出脏词。校验时必须按这张表，不能直接用 A-Z。

ULID 规范要求同毫秒内单调递增。这里用「同毫秒内随机部分 +1」实现，
而不是记录全局上次值——记录上次值需要持久化状态，而状态一旦丢失
（比如 runtime 被删）就会退化成纯随机，反而更难排查。
"""

from __future__ import annotations

import os
import re
import time

# Crockford Base32：0-9 之后跳过 I L O U
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_LEN = 26
_TIME_LEN = 10  # 前 10 个字符承载 48 位时间戳（10 * 5 = 50 >= 48）
_RAND_LEN = 16

ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

_last_ms: int = -1
_last_rand: int = -1


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def _random_int() -> int:
    return int.from_bytes(os.urandom(10), "big")


def new_ulid(timestamp_ms: int | None = None) -> str:
    """生成一个 ULID。默认取当前时间。"""
    global _last_ms, _last_rand
    ms = int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms)
    if ms < 0 or ms >= (1 << 48):
        raise ValueError(f"时间戳超出 48 位范围：{ms}")
    if ms == _last_ms:
        # 同毫秒：随机部分递增，保证单调。溢出到下一毫秒是允许的，
        # 因为 ULID 规范只要求「同一毫秒内」有序。
        rand = (_last_rand + 1) & ((1 << 80) - 1)
        if rand == 0:
            ms += 1
            rand = _random_int()
    else:
        rand = _random_int()
    _last_ms, _last_rand = ms, rand
    return _encode(ms, _TIME_LEN) + _encode(rand, _RAND_LEN)


def compose(timestamp_ms: int, payload: int) -> str:
    """按给定时间戳与 80 位负载拼一个 ULID。**只给迁移用**。

    正常发号走 `new_ulid()`（随机负载）。这个函数存在的唯一理由是：
    把一批旧序号搬进 ULID 时，如果负载纯随机，「按 ID 排序」就不再等于
    「按原序号排序」——`ch_000001` 与 `ch_000002` 换形后可能倒序。
    章节顺序是这个库的硬约束，不能因为换 ID 形态就丢掉。

    所以迁移把旧序号放进负载，配合固定时间戳，得到「排序与旧号完全一致」
    的新 ID。**新发出的 ID 不走这条路**：它没有旧序号可继承，也不该继承。
    """
    ms = int(timestamp_ms)
    if ms < 0 or ms >= (1 << 48):
        raise ValueError(f"时间戳超出 48 位范围：{ms}")
    if payload < 0 or payload >= (1 << 80):
        raise ValueError(f"负载超出 80 位范围：{payload}")
    return _encode(ms, _TIME_LEN) + _encode(payload, _RAND_LEN)


def payload_of(value: str) -> int | None:
    """取出 ULID 的 80 位负载。迁移工具用它反查「这条是哪个旧序号变的」。"""
    text = (value or "").strip()
    if not is_ulid(text):
        return None
    total = 0
    for ch in text[_TIME_LEN:]:
        total = total * 32 + ALPHABET.index(ch)
    return total


def is_ulid(value: str) -> bool:
    return bool(ULID_RE.match((value or "").strip()))


def ulid_timestamp(value: str) -> int | None:
    """取出 ULID 里的毫秒时间戳；不合法返回 None。

    用途是「按创建时间排序」与「报告里显示生成时刻」，
    不用于判断 ID 的新旧——那要靠版本记录。
    """
    text = (value or "").strip()
    if not is_ulid(text):
        return None
    total = 0
    for ch in text[:_TIME_LEN]:
        total = total * 32 + ALPHABET.index(ch)
    return total


def ulid_sort_key(value: str) -> str:
    """排序键。ULID 本身字典序即时间序，这里只是显式化意图。"""
    return (value or "").strip()
