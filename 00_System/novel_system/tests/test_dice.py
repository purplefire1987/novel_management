"""骰子娘验收用例：解析 / 可复算 / 公平性 / 规则判定 / 台账防篡改。"""

from __future__ import annotations

import json

import pytest

from novel_system import dice


# --------------------------------------------------------------------- 表达式
def test_parse_expression_variants():
    expr = dice.parse_expression("1d100")
    assert (expr.count, expr.faces, expr.modifier) == (1, 100, 0)

    expr = dice.parse_expression("1d100+11")
    assert expr.modifier == 11 and expr.describe() == "1d100+11"

    expr = dice.parse_expression("2d100KH1")
    assert expr.count == 2 and expr.keep_mode == "kh" and expr.keep_n == 1

    expr = dice.parse_expression("3d6-2")
    assert (expr.count, expr.faces, expr.modifier) == (3, 6, -2)


@pytest.mark.parametrize("bad", ["", "d", "0d6", "1d1", "2d100kh3", "一百面骰"])
def test_parse_expression_rejects_bad_input(bad: str):
    with pytest.raises(dice.DiceError):
        dice.parse_expression(bad)


# --------------------------------------------------------------------- 可复算
def test_draws_are_deterministic_from_seed():
    seed = bytes(range(32))
    expr = dice.parse_expression("1d100+5")
    first = dice.derive_draws(dice.build_entropy(seed, "ROLL-1", expr.describe(), ["甲=37"]), "ROLL-1", expr)
    second = dice.derive_draws(dice.build_entropy(seed, "ROLL-1", expr.describe(), ["甲=37"]), "ROLL-1", expr)
    assert first == second


def test_witness_values_change_the_result():
    seed = bytes(range(32))
    expr = dice.parse_expression("1d100")
    a = dice.derive_draws(dice.build_entropy(seed, "ROLL-1", expr.describe(), ["甲=1"]), "ROLL-1", expr)
    b = dice.derive_draws(dice.build_entropy(seed, "ROLL-1", expr.describe(), ["甲=99"]), "ROLL-1", expr)
    assert a != b, "玩家见证值必须真正进入熵池"


def test_commitment_is_sha256_of_seed():
    import hashlib

    seed = bytes(range(32))
    assert dice.commitment_of(seed) == hashlib.sha256(seed).hexdigest()


# --------------------------------------------------------------------- 公平性
def test_d100_covers_full_range_and_is_roughly_uniform():
    seed = b"\x07" * 32
    expr = dice.parse_expression("1d100")
    seen: dict[int, int] = {}
    total = 0
    for i in range(3000):
        roll_id = f"ROLL-{i}"
        entropy = dice.build_entropy(seed, roll_id, expr.describe(), ["见证=42"])
        value = dice.combine(dice.derive_draws(entropy, roll_id, expr), expr)
        assert 1 <= value <= 100
        seen[value] = seen.get(value, 0) + 1
        total += value
    assert len(seen) == 100, "1–100 每一面都应出现"
    assert 47.0 <= total / 3000 <= 54.0, "期望值应接近 50.5"


def test_expression_combine_rules():
    expr = dice.parse_expression("3d6")
    assert dice.combine([2, 3, 4], expr) == 9

    expr = dice.parse_expression("2d100kh1")
    assert dice.combine([31, 88], expr) == 88

    expr = dice.parse_expression("2d100kl1")
    assert dice.combine([31, 88], expr) == 31


# --------------------------------------------------------------------- 规则判定
def test_judge_uses_crit_bands_and_dc():
    crit = dice.judge(raw=97, bonus=0, dc=200)
    assert crit.band == "大成功" and crit.success is True

    fumble = dice.judge(raw=3, bonus=30, dc=5)
    assert fumble.band == "大失败" and fumble.success is False

    ok = dice.judge(raw=50, bonus=10, dc=55)
    assert ok.band == "普通" and ok.success is True

    fail = dice.judge(raw=50, bonus=0, dc=55)
    assert fail.success is False


def test_advantage_takes_higher_of_two_d100(cfg):
    result = dice.roll(cfg, "1d100", mode="advantage", label="夹击", bonus=0)
    assert result["expression"] == "2d100kh1"
    assert result["raw"] == max(result["draws"])

    result = dice.roll(cfg, "1d100", mode="disadvantage", label="目盲")
    assert result["raw"] == min(result["draws"])
    assert result["mode"] == "disadvantage"


def test_dc_only_applies_to_d100(cfg):
    with pytest.raises(dice.DiceError):
        dice.roll(cfg, "3d6", dc=10)


def test_non_d100_sums_dice(cfg):
    result = dice.roll(cfg, "3d6", label="伤害")
    assert 3 <= result["total"] <= 18
    assert result["total"] == sum(result["draws"])
    assert result["success"] is None


def test_duel_reports_winner_and_crush(cfg):
    result = dice.duel(cfg, bonus_a=30, bonus_b=40, name_a="甲", name_b="乙", label="斗剑")
    assert result["winner"] in ("甲", "乙", "平手")
    assert result["gap"] == result["a"]["total"] - result["b"]["total"]
    assert result["crush"] is (abs(result["gap"]) > 20)
    assert result["verdict"]


# --------------------------------------------------------------------- 台账
def test_ledger_chain_and_verify(cfg):
    first = dice.roll(cfg, "1d100", bonus=5, dc=55, label="第一掷")
    second = dice.roll(cfg, "1d100", bonus=5, dc=55, label="第二掷")
    assert second["prev_hash"] == first["hash"], "台账必须成链"

    report = dice.verify(cfg)
    assert report["ok"] is True, report["problems"]
    assert report["checked"] == 2


def test_witness_is_recorded_and_replayable(cfg):
    result = dice.roll(cfg, "1d100", dc=55, witnesses=["甲=37", "乙=08"], label="辨识")
    assert result["witnesses"] == ["甲=37", "乙=08"]

    ledger = dice.DiceLedger(cfg)
    entry = ledger.entries()[0]
    expr = dice.parse_expression(entry["expression"])
    replay = dice.derive_draws(
        dice.build_entropy(
            bytes.fromhex(entry["server_seed"]), entry["roll_id"], entry["expression"], entry["witnesses"]
        ),
        entry["roll_id"],
        expr,
    )
    assert replay == entry["draws"]


def test_verify_detects_tampering(cfg):
    dice.roll(cfg, "1d100", dc=55, label="原始")
    ledger = dice.DiceLedger(cfg)

    rows = [json.loads(line) for line in ledger.path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows[0]["total"] += 10  # 偷偷改结果
    ledger.path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    report = dice.verify(cfg)
    assert report["ok"] is False
    assert any("改动" in problem or "复算不一致" in problem for problem in report["problems"])


def test_ledger_file_is_plain_jsonl(cfg):
    dice.roll(cfg, "1d100", label="落账")
    path = cfg.abs(dice.LEDGER_FILE)
    assert path.exists()
    entry = json.loads(path.read_text(encoding="utf-8").strip())
    for key in ("roll_id", "algorithm", "expression", "commitment", "server_seed", "hash", "prev_hash"):
        assert key in entry
