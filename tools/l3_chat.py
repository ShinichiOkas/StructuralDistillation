"""L3（集約）だけを触る最小の CLI。記録の回答行列を集約し直し、読み手ごとの p・w・札・診断値を見せる。LLM は呼ばない。

    python tools/l3_chat.py --probe base21 --id m01                 # 測定 2 周目の記録
    python tools/l3_chat.py --probe base21 --id m01 --cf            # その反事実
    python tools/l3_chat.py --record records.jsonl --line -1        # judge() の記録
    python tools/l3_chat.py --probe base21 --id m01 --kappa 0.5 --omega 0.4   # 閾値を変えて引き直す
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace

from _cli import LABEL_JA, fmt, utf8_io
from probe_records import iter_rows, load_results, matrix_from_probe

from structural_distillation import l3
from structural_distillation.contracts import AnswerMatrix, QuestionSet, Thresholds


def show(reading) -> None:
    c, d = reading.counts, reading.diagnostics
    tag = "（偽読み手）" if reading.calibration else ""
    print(f"■ {reading.reader}{tag}: p={fmt(reading.p)} w={fmt(reading.w)} 札={LABEL_JA[reading.label.value]}")
    print(f"   s={c.s} r={c.r} 沈黙 u1={c.u1} 無効 u2={c.u2} 同数 u3={c.u3} 矛盾 u4={c.u4} / n={c.n}")
    print(f"   有効率 {fmt(d.valid_rate)}（支持側 {fmt(d.valid_rate_by_side['support'])}・反証側 {fmt(d.valid_rate_by_side['refute'])}）"
          f" 沈黙率 {fmt(d.silent_rate)} 無効率 {fmt(d.invalid_rate)} 矛盾率 {fmt(d.contradiction_rate)} 一致率 {fmt(d.agreement_mean)}"
          f" 標本ごとの p {[fmt(p) for p in d.p_by_sample]}")
    for a in reading.axes:
        arrow = {1: "＋", -1: "－", 0: "・"}[a.d]
        print(f"   {a.axis_id} {arrow} {a.zero_kind or '':13s} 支持側 {a.v_support}  反証側 {a.v_refute}")


def main() -> int:
    utf8_io()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", help="out4 の腕（base21 など）")
    ap.add_argument("--id", help="題材 id（--probe のとき）")
    ap.add_argument("--cf", action="store_true", help="反事実の行（--probe のとき）")
    ap.add_argument("--record", help="judge() の記録（JSONL）")
    ap.add_argument("--line", type=int, default=-1)
    for k, v in Thresholds().to_dict().items():
        ap.add_argument(f"--{k}", type=float, default=None, help=f"既定 {v}")
    args = ap.parse_args()

    t = Thresholds()
    if args.probe:
        t = Thresholds(kappa=0.5)   # 測定 2 周目の記録の札は κ=0.5 で付いている
    over = {k: getattr(args, k) for k in Thresholds().to_dict() if getattr(args, k) is not None}
    t = replace(t, **over)
    print(f"# 閾値 {t.to_dict()}")
    readings = []
    if args.probe:
        want = "cf" if args.cf else "main"
        for mid, scope, rd, agg in iter_rows(load_results(args.probe)):
            if mid == args.id and scope == want:
                r = l3.aggregate(matrix_from_probe(rd, agg["per_axis"]), [x["id"] for x in agg["per_axis"]], t)
                readings.append(r)
                show(r)
                print(f"   （記録の札: {agg['label']}）")
    elif args.record:
        lines = [x for x in open(args.record, encoding="utf-8").read().splitlines() if x.strip()]
        rec = json.loads(lines[args.line])
        qs = QuestionSet.from_dict(rec["question_set"])
        print(f"# 命題「{rec['input']['proposition']}」")
        for m in rec["matrices"]:
            r = l3.aggregate(AnswerMatrix.from_dict(m), qs.active_ids, t, retries=qs.retries)
            readings.append(r)
            show(r)
    else:
        ap.error("--probe か --record のどちらか")
    s = l3.summarize_readers(readings, t)
    print(f"# 読み手間: Δ={fmt(s.delta)} 割れた={s.readers_split} 代表値 p={fmt(s.representative.p if s.representative else None)}"
          f" 対象 {s.readers} {s.note or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
