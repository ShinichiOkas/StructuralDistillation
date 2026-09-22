"""M4 閾値 — v2（協議エンジンの指摘 1・14・15 を反映）。

分位点を「健全側」からだけ引くと、全 0 の量では 0 に退化して全判定が「計器不良」になる。
閾値は**既知不良と健全側の分離点**に置く（LESSONS §6.1 の較正原理）:
  ι（無効率）: 健全側の最大 と 既知不良（無い） → 健全側が全 0 なら仮置きのまま
  κ（矛盾率）: 健全側の最大 と 偽読み手 all_yes（1.0）の中点。健全側 95% 分位を併記
  ρ（有効率）: 根拠なし題材（expected none）と根拠あり題材の分離点（中点）
  ω（幅）: 想定「割れる」と想定「偏り」の w を両方並べ、取り違えが最小の値。当て先（p3 の 11 本）と読み先（新規 10 本）を割る
  Δ の閾値: 標本内 |p差| の 95% 分位。全 0 なら仮置きのまま
基準走行は 1 つ（合意 M4）。母数は命題数と行数を併記する。
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

FALLBACK = {"iota": 0.3, "kappa": 0.5, "rho": 0.5, "omega": 0.5, "delta": 0.2}


def q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def fmt(x):
    return "—" if x is None else f"{x:.3f}"


def confusion(split_w, lean_w, omega):
    """ω 以下を「偏り」、超を「割れる」と読む。取り違え数を返す。"""
    lean_as_split = sum(1 for w in lean_w if w > omega)
    split_as_lean = sum(1 for w in split_w if w <= omega)
    return lean_as_split, split_as_lean


def load_expected(materials: list[str]) -> dict:
    expected = {}
    for path in materials:
        for m in json.loads(Path(path).read_text(encoding="utf-8"))["materials"]:
            expected[m["id"]] = m.get("expected_lean")
    return expected


def compute(base: list[str], materials: list[str], fit_ids: set | None = None, extra_within: list[str] | None = None,
            exclude: set | None = None) -> tuple[list[str], dict]:
    """判断規則 M4 を当てる。戻り値: (報告の行, 置いた値の dict。仮置きのままなら FALLBACK の値)
    exclude: 母数から外す題材 id（本文についての命題・測ったあとに想定を直した題材）。統計ごとに母数の扱いを変えない"""
    extra_within = list(extra_within or [])
    exclude = set(exclude or ())
    expected = load_expected(materials)
    fit_ids = (fit_ids or ({f"m{i:02d}" for i in range(1, 11)} | {"m01b"})) - exclude
    rows, fake_contra = [], []
    for path in base:
        for r in json.loads(Path(path).read_text(encoding="utf-8")):
            if r["id"] in exclude:
                continue
            for rd, agg in r["readers"].items():
                if rd == "fake:all_yes":
                    fake_contra.append(agg["contradiction_rate"])
                if rd.startswith("fake:"):
                    continue
                rows.append((r["id"], rd, agg))
    within = []
    for path in list(base) + list(extra_within):
        for r in json.loads(Path(path).read_text(encoding="utf-8")):
            if r["id"] in exclude:
                continue
            for rd, agg in r["readers"].items():
                if rd.startswith("fake:"):
                    continue
                pbs = [p for p in agg["p_by_sample"] if p is not None]
                if len(pbs) >= 2:
                    within.append(max(pbs) - min(pbs))
    mats = sorted({m for m, _, _ in rows})
    values = dict(FALLBACK)
    nz = sorted(x for x in within if x > 0)
    L = [f"母数: 命題 {len(mats)}・行（命題 × 読み手） {len(rows)}・標本内 {len(within)} 件（うち非ゼロ {len(nz)}: {[round(x, 2) for x in nz]}）"
         + (f"。除外: {sorted(exclude)}" if exclude else ""), ""]
    out = []

    invalid = [a["invalid_rate"] for _, _, a in rows]
    hmax = max(invalid) if invalid else None
    out.append(("ι 無効率", f"**仮置き {FALLBACK['iota']} のまま**（既知不良が無い。健全側 max={fmt(hmax)}）", f"95%={fmt(q(invalid, .95))}"))

    MIN_OBS = 3  # 合意 K13 ⑵: 分離に効く非ゼロ観測が両側に 3 件以上

    contra = [a["contradiction_rate"] for _, _, a in rows]
    cmax = max(contra) if contra else None
    cmax_row = next((f"{m}/{rd}" for m, rd, a in rows if a["contradiction_rate"] == cmax), "—") if contra else "—"
    bad_min = min(fake_contra) if fake_contra else None
    n_healthy_nz = sum(1 for c in contra if c > 0)
    n_bad = len(fake_contra)
    if cmax is not None and bad_min is not None and bad_min > cmax:
        cand = (cmax + bad_min) / 2
        if n_healthy_nz >= MIN_OBS and n_bad >= MIN_OBS and abs(cand - FALLBACK["kappa"]) > 1e-9:
            values["kappa"] = cand
            out.append(("κ 矛盾率", f"分離点 **{cand:.3f}**（健全側 max {fmt(cmax)}［{cmax_row}］・偽読み手 min {fmt(bad_min)} の中点）",
                        f"K13 ⑵: 健全側の非ゼロ {n_healthy_nz} 件・偽読み手 {n_bad} 件。健全側 95%={fmt(q(contra, .95))} 中央値={fmt(statistics.median(contra))}"))
        else:
            out.append(("κ 矛盾率", f"**仮置き {FALLBACK['kappa']} のまま**（分離は確認: 候補 {cand:.3f}。K13 ⑵ 未満か仮置きと同値）",
                        f"健全側の非ゼロ {n_healthy_nz} 件・偽読み手 {n_bad} 件（要 {MIN_OBS}）"))
    else:
        out.append(("κ 矛盾率", f"**仮置き {FALLBACK['kappa']} のまま**（健全側と偽読み手が重なる、または偽読み手なし）", f"健全側 max={fmt(cmax)} 偽読み手 min={fmt(bad_min)}"))

    valid_none = [(a["A"]["s"] + a["A"]["r"]) / a["A"]["n"] for m, _, a in rows if expected.get(m) == "none" and a["A"]["n"]]
    valid_ev = [(a["A"]["s"] + a["A"]["r"]) / a["A"]["n"] for m, _, a in rows if expected.get(m) != "none" and a["A"]["n"]]
    n_none_nz = sum(1 for v in valid_none if v > 0)          # 境界に効く: 根拠なし側で有効率が 0 でない行
    n_ev_lt1 = sum(1 for v in valid_ev if v < 1)              # 境界に効く: 根拠あり側で有効率が 1 でない行
    if valid_none and valid_ev and max(valid_none) < min(valid_ev):
        cand = (max(valid_none) + min(valid_ev)) / 2
        if n_none_nz >= MIN_OBS and n_ev_lt1 >= MIN_OBS and abs(cand - FALLBACK["rho"]) > 1e-9:
            values["rho"] = cand
            out.append(("ρ 有効率", f"分離点 **{cand:.3f}**（根拠なし max {fmt(max(valid_none))}・根拠あり min {fmt(min(valid_ev))} の中点）",
                        f"K13 ⑵: 根拠なし側の非ゼロ {n_none_nz} 件・根拠あり側の 1 未満 {n_ev_lt1} 件。根拠あり 5%={fmt(q(valid_ev, .05))} n_none={len(valid_none)}"))
        else:
            out.append(("ρ 有効率", f"**仮置き {FALLBACK['rho']} のまま**（分離は確認: 候補 {cand:.3f}。K13 ⑵ 未満か仮置きと同値）",
                        f"根拠なし側の非ゼロ {n_none_nz} 件（要 {MIN_OBS}）・根拠あり側の 1 未満 {n_ev_lt1} 件。根拠なし max={fmt(max(valid_none))} 根拠あり min={fmt(min(valid_ev))}"))
    else:
        out.append(("ρ 有効率", f"**仮置き {FALLBACK['rho']} のまま**（根拠なしと根拠ありが重なる）",
                    f"根拠なし max={fmt(max(valid_none) if valid_none else None)} 根拠あり min={fmt(min(valid_ev) if valid_ev else None)}"))

    def w_split(ids):
        return [a["A"]["w"] for m, _, a in rows if m in ids and expected.get(m) == "split" and a["A"]["w"] is not None]

    def w_lean(ids):
        return [a["A"]["w"] for m, _, a in rows if m in ids and expected.get(m) in ("support", "refute") and a["A"]["w"] is not None]

    fit_s, fit_l = w_split(fit_ids), w_lean(fit_ids)
    check_ids = set(mats) - fit_ids
    chk_s, chk_l = w_split(check_ids), w_lean(check_ids)
    if fit_s and fit_l:
        cands = sorted(set(fit_s + fit_l + [0.5]))
        scores = {o: sum(confusion(fit_s, fit_l, o)) for o in cands}
        best_score = min(scores.values())
        ties = [o for o, sc in scores.items() if sc == best_score]
        cf_05 = confusion(fit_s, fit_l, 0.5)
        if 0.5 in ties:
            # 仮置きと区別がつかない → 仮置きのまま。不感帯（同点の候補の範囲）を併記
            best = 0.5
            note = f"**仮置き 0.5 のまま**（当て先で取り違え最小の候補 {[round(t, 2) for t in ties]} に 0.5 が含まれ、区別がつかない。取り違え {cf_05[0]}/{cf_05[1]}）"
        else:
            best = ties[0]
            values["omega"] = best
            cf_fit = confusion(fit_s, fit_l, best)
            note = f"当て先で取り違え最小の値 **{best:.3f}**（偏り→割れる {cf_fit[0]}、割れる→偏り {cf_fit[1]}。仮置き 0.5 なら {cf_05[0]}/{cf_05[1]}）"
        cf_chk = confusion(chk_s, chk_l, best) if (chk_s or chk_l) else None
        out.append(("ω 幅", note,
                    f"読み先（新規）での取り違え（偏り→割れる, 割れる→偏り）= {cf_chk if cf_chk else '母数なし'}；割れる想定 w={[round(x, 2) for x in fit_s]} 偏り想定 w={[round(x, 2) for x in fit_l]}"))
    else:
        out.append(("ω 幅", f"**仮置き {FALLBACK['omega']} のまま**（当て先に両クラスが無い）", ""))

    d95 = q(within, .95)
    if d95 is None or d95 == 0:
        out.append(("Δ 閾値", f"**仮置き {FALLBACK['delta']} のまま**（標本内ばらつきが全 0 で退化）", f"n={len(within)}"))
    else:
        values["delta"] = d95
        out.append(("Δ 閾値", f"標本内 |p差| の 95% 分位 **{d95:.3f}**", f"中央値={fmt(statistics.median(within))} max={fmt(max(within))} n={len(within)}"))

    L += ["| 閾値 | 置く値 | 併記 |", "|---|---|---|"]
    for name, val, note in out:
        L.append(f"| {name} | {val} | {note} |")
    by_m = {}
    for m, rd, a in rows:
        if a["A"]["p"] is not None:
            by_m.setdefault(m, []).append(a["A"]["p"])
    deltas = [max(v) - min(v) for v in by_m.values() if len(v) >= 2]
    if deltas:
        thr = values["delta"]
        L.append(f"\n読み手間 Δ: 中央値 {statistics.median(deltas):.3f}、95% {q(deltas, .95):.3f}、閾値 {thr:.3f} 超 {sum(1 for d in deltas if d > thr)}/{len(deltas)} 命題")
    return L, values


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", nargs="+", required=True)
    ap.add_argument("--materials", nargs="+", required=True)
    ap.add_argument("--fit-ids", nargs="*", default=None)
    ap.add_argument("--extra-within", nargs="*", default=[])
    args = ap.parse_args()
    lines, values = compute(args.base, args.materials, set(args.fit_ids) if args.fit_ids else None, args.extra_within)
    print("\n".join(lines))
    print("\nvalues:", json.dumps(values, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
