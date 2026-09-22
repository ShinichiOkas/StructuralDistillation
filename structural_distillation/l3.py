"""L3 集約（実装設計 §4.3）: 回答行列 → 読み手ごとの（度合い・幅・札・診断値）＋ 読み手をまたぐ要約。純関数。

空撃ち probe3.aggregate・probe.majority・probe.label と同一の規則（S0a が測定の記録で固定する）:
- 標本 1 つの向き: 支持側に証拠 ＝ 支持側が「述べている」か反証側が「否定している」。反証側も対称。
  無効な回答は、その側の証拠にならないだけ（軸を丸ごと 0 にしない。上流 v3.5 脚注）
- 軸の向きは標本の多数決。同数なら 0（tie）
- 率の定義は空撃ちと同一（無効率は回答ベース、矛盾率・沈黙率は軸ごとの標本割合の平均。実装判断 I4）
- 札の順序: ι（無効）→ κ（矛盾）→ ρ（根拠）→ ω（幅）
"""
from __future__ import annotations

import statistics
from collections import Counter
from typing import Sequence

from .contracts import (NO_VALUE_LABELS, SIDES, AnswerMatrix, AxisReading, Counts, Diagnostics, Label, Reading,
                        ReaderSummary, Thresholds, Value)

_ZERO_PRIORITY = ("contradiction", "invalid", "silent")   # 0 票の種類が同数のときの優先順位
_ZERO_FIELD = {"silent": "u1", "invalid": "u2", "tie": "u3", "contradiction": "u4"}


def side_evidence(vs: str, vr: str) -> tuple[bool, bool]:
    """(支持側に証拠があるか, 反証側に証拠があるか)。vs / vr は STATES / DENIES / SILENT / INVALID。"""
    return vs == "STATES" or vr == "DENIES", vr == "STATES" or vs == "DENIES"


def direction_sample(vs: str, vr: str) -> tuple[int, str | None]:
    """標本 1 つの向きと、0 のときの種類（contradiction / invalid / silent）。"""
    sup, ref = side_evidence(vs, vr)
    if sup and ref:
        return 0, "contradiction"
    if sup:
        return 1, None
    if ref:
        return -1, None
    if "INVALID" in (vs, vr):
        return 0, "invalid"
    return 0, "silent"


def majority(vals: list[int]) -> tuple[int, float]:
    """多数決（最多が一意でなければ 0）と、最多票の割合。空撃ち probe.majority と同一。"""
    if not vals:
        return 0, 0.0
    c = Counter(vals)
    top, n = c.most_common(1)[0]
    if list(c.values()).count(n) > 1:
        return 0, n / len(vals)
    return top, n / len(vals)


def _p_w(s: int, r: int) -> tuple[float | None, float | None]:
    if s + r == 0:
        return None, None
    return s / (s + r), 2 * min(s, r) / (s + r)


def label(s: int, r: int, n: int, w: float | None, p: float | None, *, invalid_rate: float,
          contradiction_rate: float, t: Thresholds) -> Label:
    """札（上流 §7.4）。順序 ι → κ → ρ → ω。"""
    if invalid_rate >= t.iota:
        return Label.INSTRUMENT_FAULT
    if contradiction_rate >= t.kappa:
        return Label.INSTRUMENT_FAULT
    if n == 0 or (s + r) / n < t.rho:
        return Label.NO_EVIDENCE
    assert w is not None and p is not None
    if w <= t.omega and p > 0.5:
        return Label.LEAN_SUPPORT
    if w <= t.omega and p < 0.5:
        return Label.LEAN_REFUTE
    return Label.SPLIT


def aggregate(matrix: AnswerMatrix, axis_ids: Sequence[str], thresholds: Thresholds, *, retries: int = 0) -> Reading:
    """読み手 1 体の回答行列を集約する（値は付けない。値は L4）。axis_ids は有効な軸の並び。"""
    idx = matrix.index()
    samples = matrix.samples
    axes: list[AxisReading] = []
    counts = Counts()
    side_valid = {"support": 0, "refute": 0}
    for aid in axis_ids:
        try:
            vs_list = [idx[(aid, "support", s)].code for s in range(samples)]
            vr_list = [idx[(aid, "refute", s)].code for s in range(samples)]
        except KeyError as e:
            raise ValueError(f"回答行列に {e.args[0]} が無い（読み手 {matrix.reader}）") from e
        dirs, kinds = [], []
        for vs, vr in zip(vs_list, vr_list):
            d, k = direction_sample(vs, vr)
            dirs.append(d)
            kinds.append(k)
        d, agree = majority(dirs)
        zero_kind: str | None = None
        if d == 0:
            c = Counter(dirs)
            if list(c.values()).count(c.most_common(1)[0][1]) > 1:
                zero_kind = "tie"
            else:
                kc = Counter(k for dd, k in zip(dirs, kinds) if dd == 0)
                top = max(kc.values())
                zero_kind = next(k for k in _ZERO_PRIORITY if kc.get(k) == top)
        if d == 1:
            counts.s += 1
        elif d == -1:
            counts.r += 1
        else:
            f = _ZERO_FIELD[zero_kind or "silent"]
            setattr(counts, f, getattr(counts, f) + 1)
        for side, vals in (("support", vs_list), ("refute", vr_list)):
            side_valid[side] += sum(1 for v in vals if v in ("STATES", "DENIES"))
        axes.append(AxisReading(
            axis_id=aid, d=d, zero_kind=zero_kind, agreement=agree, d_samples=dirs, v_support=vs_list, v_refute=vr_list,
            contradiction=sum(1 for k in kinds if k == "contradiction") / samples,
            invalid=sum(1 for v in vs_list + vr_list if v == "INVALID"),
            silent=sum(1 for vs, vr in zip(vs_list, vr_list) if vs == "SILENT" and vr == "SILENT")))
    n = len(axes)
    p, w = _p_w(counts.s, counts.r)
    if n:
        invalid_rate = sum(a.invalid for a in axes) / (2 * samples * n)
        silent_rate = sum(a.silent for a in axes) / (samples * n)
        contradiction_rate = statistics.mean(a.contradiction for a in axes)
        agreement_mean = statistics.mean(a.agreement for a in axes)
        valid_rate = (counts.s + counts.r) / n
        by_side = {side: side_valid[side] / (samples * n) for side in SIDES}
    else:
        invalid_rate = silent_rate = contradiction_rate = agreement_mean = valid_rate = 0.0
        by_side = {side: 0.0 for side in SIDES}
    p_by_sample = []
    for i in range(samples):
        ds = [a.d_samples[i] for a in axes]
        p_by_sample.append(_p_w(ds.count(1), ds.count(-1))[0])
    diag = Diagnostics(valid_rate=valid_rate, valid_rate_by_side=by_side, silent_rate=silent_rate,
                       invalid_rate=invalid_rate, agreement_mean=agreement_mean, contradiction_rate=contradiction_rate,
                       retries=retries, p_by_sample=p_by_sample)
    lab = label(counts.s, counts.r, n, w, p, invalid_rate=invalid_rate, contradiction_rate=contradiction_rate,
                t=thresholds)
    return Reading(reader=matrix.reader, calibration=matrix.calibration, counts=counts, p=p, w=w, label=lab,
                   value=None, diagnostics=diag, axes=axes)


def summarize_readers(readings: Sequence[Reading], thresholds: Thresholds) -> ReaderSummary:
    """読み手をまたぐ要約（上流 §4.2・§7.4）。偽読み手と値なし（根拠なし・計器不良）の読み手は除く。
    代表値は p の平均（上流 U3・仮置き。実装判断 I10）。段の一致と型付きの代表値は judge が L4 で埋める。"""
    valued = [r for r in readings if not r.calibration and r.label not in NO_VALUE_LABELS and r.p is not None]
    names = [r.reader for r in valued]
    if not valued:
        return ReaderSummary(None, None, None, None, names, "no reader with a value")
    ps = [r.p for r in valued]
    rep = Value(p=statistics.mean(ps))
    if len(valued) == 1:
        return ReaderSummary(None, None, None, rep, names, "single reader")
    delta = max(ps) - min(ps)
    return ReaderSummary(delta, None, delta > thresholds.delta, rep, names, None)
