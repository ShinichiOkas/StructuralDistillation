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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", nargs="+", required=True, help="基準走行の results.json（弱い 2 体・標本 2・指示 p3）。複数可（題材集合が分かれているとき）")
    ap.add_argument("--materials", nargs="+", required=True)
    ap.add_argument("--fit-ids", nargs="*", default=None, help="ω の当て先にする題材 id（既定: m01〜m10）")
    ap.add_argument("--extra-within", nargs="*", default=[], help="標本内ばらつきの母数に足す results.json（標本 4 の腕など）")
    ap.add_argument("--fallback", default="ι=0.3 κ=0.5 ρ=0.5 ω=0.5 Δ=0.2", help="仮置き（退化したときに残す値）")
    args = ap.parse_args()

    expected = {}
    for path in args.materials:
        for m in json.loads(Path(path).read_text(encoding="utf-8"))["materials"]:
            expected[m["id"]] = m.get("expected_lean")
    fit_ids = set(args.fit_ids) if args.fit_ids else {f"m{i:02d}" for i in range(1, 11)} | {"m01b"}

    rows = []  # (material, reader, agg)
    fake_contra = []
    for path in args.base:
        for r in json.loads(Path(path).read_text(encoding="utf-8")):
            for rd, agg in r["readers"].items():
                if rd == "fake:all_yes":
                    fake_contra.append(agg["contradiction_rate"])
                if rd.startswith("fake:"):
                    continue
                rows.append((r["id"], rd, agg))
    within = []
    for path in list(args.base) + list(args.extra_within):
        for r in json.loads(Path(path).read_text(encoding="utf-8")):
            for rd, agg in r["readers"].items():
                if rd.startswith("fake:"):
                    continue
                pbs = [p for p in agg["p_by_sample"] if p is not None]
                if len(pbs) >= 2:
                    within.append(max(pbs) - min(pbs))

    mats = sorted({m for m, _, _ in rows})
    print(f"母数: 命題 {len(mats)}・行（命題 × 読み手） {len(rows)}・標本内 {len(within)} 件\n")
    print(f"仮置き: {args.fallback}\n")
    out = []

    invalid = [a["invalid_rate"] for _, _, a in rows]
    hmax = max(invalid) if invalid else None
    if hmax is None or hmax == 0:
        out.append(("ι 無効率", "**仮置きのまま**（健全側が全 0 で退化。既知不良も無い）", f"健全側 max={fmt(hmax)} 95%={fmt(q(invalid, .95))}"))
    else:
        out.append(("ι 無効率", f"健全側 max {fmt(hmax)} の上（分離点は既知不良が無いので置けない）→ 仮置きのまま・併記", f"95%={fmt(q(invalid, .95))}"))

    contra = [a["contradiction_rate"] for _, _, a in rows]
    cmax = max(contra) if contra else None
    bad_min = min(fake_contra) if fake_contra else None
    if cmax is not None and bad_min is not None and bad_min > cmax:
        kappa = (cmax + bad_min) / 2
        out.append(("κ 矛盾率", f"分離点 **{kappa:.3f}**（健全側 max {fmt(cmax)}・偽読み手 min {fmt(bad_min)} の中点）", f"健全側 95%={fmt(q(contra, .95))} 中央値={fmt(statistics.median(contra))}"))
    else:
        out.append(("κ 矛盾率", "**仮置きのまま**（健全側と偽読み手が重なる、または偽読み手なし）", f"健全側 max={fmt(cmax)} 偽読み手 min={fmt(bad_min)}"))

    valid_none = [(a["A"]["s"] + a["A"]["r"]) / a["A"]["n"] for m, _, a in rows if expected.get(m) == "none" and a["A"]["n"]]
    valid_ev = [(a["A"]["s"] + a["A"]["r"]) / a["A"]["n"] for m, _, a in rows if expected.get(m) != "none" and a["A"]["n"]]
    if valid_none and valid_ev and max(valid_none) < min(valid_ev):
        rho = (max(valid_none) + min(valid_ev)) / 2
        out.append(("ρ 有効率", f"分離点 **{rho:.3f}**（根拠なし max {fmt(max(valid_none))}・根拠あり min {fmt(min(valid_ev))} の中点）", f"根拠あり 5%={fmt(q(valid_ev, .05))} n_none={len(valid_none)}"))
    else:
        out.append(("ρ 有効率", "**仮置きのまま**（根拠なしと根拠ありが重なる）", f"根拠なし max={fmt(max(valid_none) if valid_none else None)} 根拠あり min={fmt(min(valid_ev) if valid_ev else None)}"))

    def w_of(ids, want):
        return [a["A"]["w"] for m, _, a in rows if m in ids and expected.get(m) == want and a["A"]["w"] is not None]

    def w_lean(ids):
        return [a["A"]["w"] for m, _, a in rows if m in ids and expected.get(m) in ("support", "refute") and a["A"]["w"] is not None]

    fit_split, fit_lean = w_of(fit_ids, "split"), w_lean(fit_ids)
    check_ids = set(mats) - fit_ids
    chk_split, chk_lean = w_of(check_ids, "split"), w_lean(check_ids)
    if fit_split and fit_lean:
        cands = sorted(set(fit_split + fit_lean + [0.5]))
        best = min(cands, key=lambda o: sum(confusion(fit_split, fit_lean, o)))
        cf_fit = confusion(fit_split, fit_lean, best)
        cf_chk = confusion(chk_split, chk_lean, best) if (chk_split or chk_lean) else None
        cf_05 = confusion(fit_split, fit_lean, 0.5)
        out.append(("ω 幅", f"当て先で取り違え最小の値 **{best:.3f}**（偏り→割れる {cf_fit[0]}、割れる→偏り {cf_fit[1]}。仮置き 0.5 なら {cf_05[0]}/{cf_05[1]}）",
                    f"読み先（新規）での取り違え {cf_chk if cf_chk else '母数なし'}；割れる想定 w={[round(x,2) for x in fit_split]} 偏り想定 w={[round(x,2) for x in fit_lean]}"))
    else:
        out.append(("ω 幅", "**仮置きのまま**（当て先に両クラスが無い）", ""))

    d95 = q(within, .95)
    if d95 is None or d95 == 0:
        out.append(("Δ 閾値", "**仮置きのまま**（標本内ばらつきが全 0 で退化）", f"n={len(within)}"))
    else:
        out.append(("Δ 閾値", f"標本内 |p差| の 95% 分位 **{d95:.3f}**", f"中央値={fmt(statistics.median(within))} max={fmt(max(within))} n={len(within)}"))

    print("| 閾値 | 置く値 | 併記 |")
    print("|---|---|---|")
    for name, val, note in out:
        print(f"| {name} | {val} | {note} |")

    # 読み手間 Δ の分布（参考）
    by_m = {}
    for m, rd, a in rows:
        if a["A"]["p"] is not None:
            by_m.setdefault(m, []).append(a["A"]["p"])
    deltas = [max(v) - min(v) for v in by_m.values() if len(v) >= 2]
    if deltas:
        thr = d95 if d95 else 0.2
        print(f"\n読み手間 Δ: 中央値 {statistics.median(deltas):.3f}、95% {q(deltas, .95):.3f}、閾値 {thr:.3f} 超 {sum(1 for d in deltas if d > thr)}/{len(deltas)} 命題")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
