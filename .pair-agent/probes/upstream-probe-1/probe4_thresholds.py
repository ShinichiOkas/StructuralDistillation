"""M4 閾値の分位点。複数の走行の results.json（p3 形式）から、判断規則どおりに分位点を出す。

  ι = 無効率の 95% 分位
  κ = 矛盾率の 95% 分位（偽読み手を除く）
  ρ = 有効率 (s+r)/n の 5% 分位
  ω = 想定「割れる」の題材の w の 5% 分位
  Δ の閾値 = 標本内 |p差| の 95% 分位
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", nargs="+", required=True)
    ap.add_argument("--materials", nargs="+", required=True)
    args = ap.parse_args()
    expected = {}
    for path in args.materials:
        for m in json.loads(Path(path).read_text(encoding="utf-8"))["materials"]:
            expected[m["id"]] = m.get("expected_lean")

    invalid, contra, valid, w_split, within, deltas = [], [], [], [], [], []
    n_rows = 0
    for path in args.results:
        for r in json.loads(Path(path).read_text(encoding="utf-8")):
            real = {rd: agg for rd, agg in r["readers"].items() if not rd.startswith("fake:")}
            ps = []
            for agg in real.values():
                n_rows += 1
                A = agg["A"]
                invalid.append(agg["invalid_rate"])
                contra.append(agg["contradiction_rate"])
                valid.append((A["s"] + A["r"]) / A["n"] if A["n"] else None)
                if expected.get(r["id"]) == "split" and A["w"] is not None:
                    w_split.append(A["w"])
                pbs = [p for p in agg["p_by_sample"] if p is not None]
                if len(pbs) >= 2:
                    within.append(max(pbs) - min(pbs))
                if A["p"] is not None:
                    ps.append(A["p"])
            if len(ps) >= 2:
                deltas.append(max(ps) - min(ps))
    print(f"母数: 題材×読み手 {n_rows} 行、読み手間 Δ {len(deltas)} 件、標本内 {len(within)} 件、割れる想定の w {len(w_split)} 件\n")
    rows = [
        ("ι 無効率 95%", q(invalid, 0.95), invalid),
        ("κ 矛盾率 95%", q(contra, 0.95), contra),
        ("ρ 有効率 5%", q(valid, 0.05), valid),
        ("ω 割れる想定の w 5%", q(w_split, 0.05), w_split),
        ("Δ 閾値 = 標本内 |p差| 95%", q(within, 0.95), within),
    ]
    print("| 閾値 | 分位点 | 中央値 | 平均 | n |")
    print("|---|---|---|---|---|")
    for name, val, xs in rows:
        xs2 = [x for x in xs if x is not None]
        print(f"| {name} | {val if val is None else round(val, 3)} | {round(statistics.median(xs2), 3) if xs2 else '—'} "
              f"| {round(statistics.mean(xs2), 3) if xs2 else '—'} | {len(xs2)} |")
    if deltas:
        print(f"\n読み手間 Δ: 中央値 {statistics.median(deltas):.3f}、95% {q(deltas, 0.95):.3f}、超過（Δ > 閾値）の割合は閾値確定後に数える")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
