"""L3 集約（実装設計 §4.3）。純関数。S0a（測定の記録を引き直して全一致）・S4（相補性）・S12（軸数不変）。"""
from __future__ import annotations

import math
import random

import pytest

from structural_distillation import l3
from structural_distillation.contracts import Answer, AnswerMatrix, Label, Reason, Thresholds, Verdict

from conftest import need_out4
from probe_records import AGG_ARMS, LABEL, iter_rows, load_results, matrix_from_probe

T = Thresholds()
RUN_TIME = Thresholds(iota=0.3, kappa=0.5, rho=0.5, omega=0.5)   # 測定 2 周目の記録の札はこの閾値で付いた
CODES = ["STATES", "DENIES", "SILENT", "INVALID"]


def mx(rows: dict[str, list[tuple[str, str]]], reader="r", calibration=False) -> AnswerMatrix:
    """rows: 軸 id → 標本ごとの (支持側の符号, 反証側の符号)。"""
    answers = []
    samples = len(next(iter(rows.values()))) if rows else 1
    for aid, pairs in rows.items():
        for side, idx in (("support", 0), ("refute", 1)):
            for s, pair in enumerate(pairs):
                c = pair[idx]
                answers.append(Answer(aid, side, s, None if c == "INVALID" else Verdict(c), valid=c != "INVALID"))
    return AnswerMatrix(reader, calibration, None, samples, answers)


# ---------------------------------------------------------------- 標本 1 つの向き

@pytest.mark.parametrize("vs,vr,d,kind", [
    ("STATES", "DENIES", 1, None), ("STATES", "SILENT", 1, None), ("SILENT", "DENIES", 1, None),
    ("DENIES", "STATES", -1, None), ("SILENT", "STATES", -1, None), ("DENIES", "SILENT", -1, None),
    ("STATES", "STATES", 0, "contradiction"), ("DENIES", "DENIES", 0, "contradiction"),
    ("SILENT", "SILENT", 0, "silent"),
    # 無効な回答はその側の証拠にならないだけ。他方に証拠があれば向きは立つ（空撃ちと同一。上流 v3.5 脚注）
    ("STATES", "INVALID", 1, None), ("INVALID", "DENIES", 1, None), ("INVALID", "STATES", -1, None),
    ("INVALID", "SILENT", 0, "invalid"), ("INVALID", "INVALID", 0, "invalid"),
])
def test_direction_sample(vs, vr, d, kind):
    assert l3.direction_sample(vs, vr) == (d, kind)


def test_majority_and_tie():
    assert l3.majority([1, 1, -1]) == (1, 2 / 3)
    assert l3.majority([1, -1]) == (0, 0.5)          # 同数は 0
    assert l3.majority([0, 0, 1]) == (0, 2 / 3)
    assert l3.majority([]) == (0, 0.0)


def test_zero_kind_priority():
    # 標本 2 の [contradiction, silent] は多数決で 0（0 票どうし）→ 種類の同数は contradiction ＞ invalid ＞ silent
    r = l3.aggregate(mx({"a": [("STATES", "STATES"), ("SILENT", "SILENT")]}), ["a"], T)
    assert r.axes[0].d == 0 and r.axes[0].zero_kind == "contradiction"
    r = l3.aggregate(mx({"a": [("INVALID", "SILENT"), ("SILENT", "SILENT")]}), ["a"], T)
    assert r.axes[0].zero_kind == "invalid"
    r = l3.aggregate(mx({"a": [("STATES", "SILENT"), ("DENIES", "SILENT")]}), ["a"], T)
    assert r.axes[0].zero_kind == "tie" and r.counts.u3 == 1


def test_counts_rates_and_label():
    rows = {"a1": [("STATES", "DENIES")], "a2": [("STATES", "SILENT")], "a3": [("SILENT", "STATES")],
            "a4": [("SILENT", "SILENT")], "a5": [("STATES", "STATES")], "a6": [("INVALID", "SILENT")]}
    r = l3.aggregate(mx(rows), list(rows), T)
    c = r.counts
    assert (c.s, c.r, c.u1, c.u2, c.u3, c.u4, c.n) == (2, 1, 1, 1, 0, 1, 6)
    assert r.p == pytest.approx(2 / 3) and r.w == pytest.approx(2 / 3)
    d = r.diagnostics
    assert d.valid_rate == pytest.approx(0.5)
    assert d.invalid_rate == pytest.approx(1 / 12)        # 回答ベース（I4）
    assert d.silent_rate == pytest.approx(1 / 6) and d.contradiction_rate == pytest.approx(1 / 6)
    # 支持側で述べている／否定している: a1 a2 a5（a6 は無効）。反証側: a1 a3 a5
    assert d.valid_rate_by_side == {"support": pytest.approx(3 / 6), "refute": pytest.approx(3 / 6)}
    assert r.label == Label.SPLIT                           # w 0.67 > ω 0.5
    assert r.value is None                                  # 値は L4


def test_label_order_and_reason():
    """札と理由（師匠決定 2026-09-23: 軸 0 本は計器不良・理由つき）。順序は 軸 0 → ι → κ → ρ → ω。"""
    t = T
    f = l3.label
    assert f(0, 0, 0, None, None, invalid_rate=0.0, contradiction_rate=0.0, t=t) == (Label.INSTRUMENT_FAULT, Reason.NO_ACTIVE_AXES)
    assert f(5, 0, 6, 0.0, 1.0, invalid_rate=0.3, contradiction_rate=0.0, t=t) == (Label.INSTRUMENT_FAULT, Reason.INVALID_EVIDENCE)
    assert f(5, 0, 6, 0.0, 1.0, invalid_rate=0.0, contradiction_rate=0.7, t=t) == (Label.INSTRUMENT_FAULT, Reason.CONTRADICTORY_AXES)
    assert f(0, 0, 6, None, None, invalid_rate=0.0, contradiction_rate=0.0, t=t) == (Label.NO_EVIDENCE, Reason.NO_DEFINITE_AXIS)
    assert f(2, 0, 6, 0.0, 1.0, invalid_rate=0.0, contradiction_rate=0.0, t=t) == (Label.NO_EVIDENCE, Reason.LOW_VALID_RATE)
    assert f(4, 1, 6, 0.4, 0.8, invalid_rate=0.0, contradiction_rate=0.0, t=t) == (Label.LEAN_SUPPORT, None)
    assert f(1, 4, 6, 0.4, 0.2, invalid_rate=0.0, contradiction_rate=0.0, t=t) == (Label.LEAN_REFUTE, None)
    assert f(3, 3, 6, 1.0, 0.5, invalid_rate=0.0, contradiction_rate=0.0, t=t) == (Label.SPLIT, None)


def test_no_axes_is_an_instrument_fault_and_rates_are_not_measured():
    """判定に使える軸が 0 本 ＝ 計器（問いの集合）の側の問題（師匠決定 2026-09-23）。"""
    r = l3.aggregate(mx({}), [], T)
    assert r.p is None and r.w is None and r.counts.n == 0
    assert r.label == Label.INSTRUMENT_FAULT and r.reason == Reason.NO_ACTIVE_AXES
    d = r.diagnostics   # 測って 0 だったのと区別する（受入 M5）
    assert d.valid_rate is None and d.invalid_rate is None and d.contradiction_rate is None and d.silent_rate is None
    assert d.valid_rate_by_side == {"support": None, "refute": None}


@pytest.mark.parametrize("rho", [0.0, 1e-9])
def test_no_definite_axis_is_no_evidence_whatever_rho(rho):
    """s + r = 0 は ρ に関わらず NO_EVIDENCE（受入 M3: ρ = 0 で AssertionError だった）。"""
    rows = {"a": [("SILENT", "SILENT")], "b": [("INVALID", "SILENT")]}
    assert l3.aggregate(mx(rows), list(rows), Thresholds(rho=rho, iota=1.0)).label == Label.NO_EVIDENCE


def test_reader_failure_and_fabricated_evidence_get_different_reasons():
    """受入 M3: どちらも「無効」だが、読み手が答えられなかったのか、根拠が使えないのかで次の手が違う。"""
    def matrix(with_error: bool) -> AnswerMatrix:
        answers = []
        for i in range(4):
            for side in ("support", "refute"):
                answers.append(Answer(f"a{i}", side, 0, None, valid=False,
                                      error="HTTPError: 500" if with_error else None))
        return AnswerMatrix("r", False, None, 1, answers)
    ids = [f"a{i}" for i in range(4)]
    failed = l3.aggregate(matrix(True), ids, T)
    assert failed.label == Label.INSTRUMENT_FAULT and failed.reason == Reason.READER_FAILED
    assert failed.diagnostics.error_rate == 1.0 and failed.diagnostics.invalid_rate == 1.0
    bogus = l3.aggregate(matrix(False), ids, T)
    assert bogus.label == Label.INSTRUMENT_FAULT and bogus.reason == Reason.INVALID_EVIDENCE
    assert bogus.diagnostics.error_rate == 0.0


def test_fake_like_readers():
    all_yes = {f"a{i}": [("STATES", "STATES")] for i in range(6)}
    assert l3.aggregate(mx(all_yes), list(all_yes), T).label == Label.INSTRUMENT_FAULT
    all_silent = {f"a{i}": [("SILENT", "SILENT")] for i in range(6)}
    assert l3.aggregate(mx(all_silent), list(all_silent), T).label == Label.NO_EVIDENCE


def _random_rows(rng, n_axes, samples):
    return {f"a{i}": [(rng.choice(CODES), rng.choice(CODES)) for _ in range(samples)] for i in range(n_axes)}


def test_s4_complement_swapping_all_sides():
    """S4: 全軸の支持側と反証側を入れ替えると p' = 1 − p、w' = w（上流 §9）。"""
    rng = random.Random(1)
    for _ in range(300):
        rows = _random_rows(rng, rng.randint(1, 8), rng.randint(1, 4))
        swapped = {k: [(b, a) for a, b in v] for k, v in rows.items()}
        a = l3.aggregate(mx(rows), list(rows), T)
        b = l3.aggregate(mx(swapped), list(rows), T)
        if a.p is None:
            assert b.p is None
        else:
            assert b.p == pytest.approx(1 - a.p) and b.w == pytest.approx(a.w)


def test_s12_width_does_not_depend_on_axis_count():
    """S12: 軸を 2 倍に複製しても p・w・札・率は変わらない。"""
    rng = random.Random(2)
    for _ in range(300):
        rows = _random_rows(rng, rng.randint(1, 8), rng.randint(1, 4))
        doubled = {**rows, **{k + "b": v for k, v in rows.items()}}
        a = l3.aggregate(mx(rows), list(rows), T)
        b = l3.aggregate(mx(doubled), list(doubled), T)
        assert (a.p, a.w, a.label) == (b.p, b.w, b.label)
        assert b.diagnostics.contradiction_rate == pytest.approx(a.diagnostics.contradiction_rate)
        assert b.diagnostics.invalid_rate == pytest.approx(a.diagnostics.invalid_rate)
        assert b.diagnostics.silent_rate == pytest.approx(a.diagnostics.silent_rate)
        assert b.diagnostics.valid_rate_by_side == pytest.approx(a.diagnostics.valid_rate_by_side)


def test_summarize_readers_excludes_calibration_and_valueless():
    rows_s = {f"a{i}": [("STATES", "SILENT")] for i in range(3)} | {"a9": [("SILENT", "STATES")]}
    rows_r = {f"a{i}": [("SILENT", "STATES")] for i in range(4)}
    silent = {f"a{i}": [("SILENT", "SILENT")] for i in range(4)}
    r1 = l3.aggregate(mx(rows_s, "r1"), list(rows_s), T)
    r2 = l3.aggregate(mx(rows_r, "r2"), list(rows_r), T)
    r3 = l3.aggregate(mx(silent, "r3"), list(silent), T)
    fake = l3.aggregate(mx(rows_r, "fake:x", calibration=True), list(rows_r), T)
    s = l3.summarize_readers([r1, r2, r3, fake], T)
    assert s.readers == ["r1", "r2"] and s.delta == pytest.approx(0.75) and s.readers_split is True
    assert s.representative.p == pytest.approx(0.375)
    one = l3.summarize_readers([r1, r3, fake], T)
    assert one.delta is None and one.readers_split is None and one.note == "single reader"
    none = l3.summarize_readers([r3], T)
    assert none.representative is None and none.note == "no reader with a value"


# ---------------------------------------------------------------- S0a（測定の記録を引き直す）

def _check_row(agg: dict, reading, full: bool) -> None:
    assert reading.p == agg["A"]["p"] and reading.w == agg["A"]["w"]
    assert (reading.counts.s, reading.counts.r, reading.counts.n) == (agg["A"]["s"], agg["A"]["r"], agg["A"]["n"])
    assert reading.label == LABEL[agg["label"]]
    for got, want in zip(reading.axes, agg["per_axis"], strict=True):
        assert got.axis_id == want["id"] and got.d == want["d"] and got.d_samples == want["d_samples"]
        assert math.isclose(got.agreement, want["agree"]) and math.isclose(got.contradiction, want["contradiction"])
        assert got.invalid == want["invalid"] and got.silent == want["silent"]
    if full:
        d = reading.diagnostics
        assert d.p_by_sample == agg["p_by_sample"]
        assert math.isclose(d.invalid_rate, agg["invalid_rate"]) and math.isclose(d.silent_rate, agg["silent_rate"])
        assert math.isclose(d.contradiction_rate, agg["contradiction_rate"])


def test_s0a_fixture_m01(base21_m01):
    r = base21_m01["result"]
    for _mid, scope, rd, agg in iter_rows([r]):
        reading = l3.aggregate(matrix_from_probe(rd, agg["per_axis"]), [x["id"] for x in agg["per_axis"]], RUN_TIME)
        _check_row(agg, reading, full=scope == "main")


ROWS = {"base21": 143, "s4": 30, "mono": 103, "meta2": 59, "m3arm": 96, "mem": 10, "rashomon": 14, "mono_rashomon": 10}


@pytest.mark.parametrize("arm", AGG_ARMS)
def test_s0a_measurement_records(arm):
    """S0a: 測定 2 周目の 8 腕の主走行と反事実の全行（計 465）を引き直し、p・w・札・軸ごとの向きと率が記録と一致する。"""
    need_out4()
    n = 0
    for mid, scope, rd, agg in iter_rows(load_results(arm)):
        reading = l3.aggregate(matrix_from_probe(rd, agg["per_axis"]), [x["id"] for x in agg["per_axis"]], RUN_TIME)
        try:
            _check_row(agg, reading, full=scope == "main")
        except AssertionError as e:
            raise AssertionError(f"{arm} {mid} {scope} {rd}: {e}") from e
        n += 1
    assert n == ROWS[arm]   # 行数も固定する（受入 m9: 「465 行」はテストで固定されていなかった）
    assert sum(ROWS.values()) == 465
