"""1 判定を端から端まで回す CLI。本文ファイル・命題・型を渡し、読み手ごとの値と読み手間の要約を見せる。

    python tools/judge_cli.py 本文.txt "ゲンは悪人である" --ordinal 5 \
        --planner gemma4:31b-cloud --readers qwen3.5:397b-cloud glm-5.2:cloud \
        --cache scratch/judge.jsonl --record scratch/records.jsonl --workers 6
    python tools/judge_cli.py 本文.txt "命題" --probability --readers gemma4:31b-cloud --fake all_yes all_undetermined

⚠ クラウドモデル（-cloud）を使う。ローカルモデルは GPU を師匠と共有する。
"""
from __future__ import annotations

import argparse
import time

from _cli import LABEL_JA, fmt, read_text, utf8_io

from structural_distillation import judge_sync
from structural_distillation.contracts import Budget, InputError, Ordinal, PlanningFailed, Probability, Thresholds
from structural_distillation.l0 import CachedPort, FakeReader, OllamaReader


def main() -> int:
    utf8_io()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", help="本文のファイル（- で標準入力）")
    ap.add_argument("proposition")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--probability", action="store_true")
    g.add_argument("--ordinal", type=int, metavar="K")
    ap.add_argument("--labels", nargs="*", default=None)
    ap.add_argument("--bounds", nargs="*", type=float, default=None)
    ap.add_argument("--readers", nargs="+", required=True)
    ap.add_argument("--planner", default=None, help="既定は読み手の先頭")
    ap.add_argument("--verifiers", nargs="*", default=None, help="既定は読み手（2 体未満なら交差検証しない）")
    ap.add_argument("--fake", nargs="*", default=[], choices=FakeReader.KINDS)
    ap.add_argument("--axes", type=int, default=6)
    ap.add_argument("--samples", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-crosscheck", action="store_true")
    ap.add_argument("--num-ctx", type=int, default=8192)
    ap.add_argument("--cache", default=None, help="生応答のキャッシュ（JSONL・追記のみ）")
    ap.add_argument("--record", default=None, help="判定の記録（JSONL・追記のみ）")
    for k, v in Thresholds().to_dict().items():
        ap.add_argument(f"--{k}", type=float, default=v)
    args = ap.parse_args()

    def mk(model: str):
        r = OllamaReader(model, num_ctx=args.num_ctx)
        return CachedPort(r, args.cache) if args.cache else r

    readers = [mk(m) for m in args.readers] + [FakeReader(k) for k in args.fake]
    planner = mk(args.planner) if args.planner else None
    verifiers = [mk(m) for m in args.verifiers] if args.verifiers is not None else None
    output = Probability() if args.probability else Ordinal(args.ordinal, tuple(args.labels) if args.labels else None,
                                                             tuple(args.bounds) if args.bounds else None)
    t = Thresholds(**{k: getattr(args, k) for k in Thresholds().to_dict()})
    budget = Budget(axes=args.axes, samples=args.samples, workers=args.workers, crosscheck=not args.no_crosscheck)
    t0 = time.time()
    try:
        j = judge_sync(read_text(args.text), args.proposition, output, readers=readers, planner=planner,
                       verifiers=verifiers, budget=budget, thresholds=t, record_path=args.record)
    except (InputError, PlanningFailed) as e:
        print(f"判定できない: {type(e).__name__}: {e}")
        return 1
    qs = j.question_set
    print(f"# 命題「{j.proposition}」・単位 {len(j.units)}・生成器 {qs.planner}・軸 {len(qs.axes)}（有効 {len(qs.active_ids)}）"
          f"・型 {output}")
    if qs.crosscheck:
        cc = qs.crosscheck
        print(f"# 交差検証: 外した {cc.flagged or 'なし'}・弱 {cc.weak or 'なし'}・非排他 {cc.nonexclusive or 'なし'}")
    for a in qs.axes:
        mark = " " if a.id in qs.active_ids else "✗"
        print(f"{mark} {a.id} [{a.name}] 支持側「{a.claim_support}」／反証側「{a.claim_refute}」")
    print()
    for name, r in j.readings.items():
        v = r.value
        val = "値なし" if v is None else (f"p={fmt(v.p)}" + (f" 段={v.level}" if v.level else "") + (f"（{v.label}）" if v.label else ""))
        dirs = " ".join(f"{x.axis_id}{ {1: '＋', -1: '－', 0: '・'}[x.d] }" for x in r.axes)
        tag = "（偽読み手）" if r.calibration else ""
        d = r.diagnostics
        print(f"■ {name}{tag}: {val}・札 {LABEL_JA[r.label.value]}・p={fmt(r.p)} w={fmt(r.w)}")
        print(f"   {dirs}")
        print(f"   有効率 {fmt(d.valid_rate)} 沈黙率 {fmt(d.silent_rate)} 無効率 {fmt(d.invalid_rate)} 矛盾率 {fmt(d.contradiction_rate)}"
              f" 一致率 {fmt(d.agreement_mean)}")
    s = j.summary
    rep = s.representative
    print(f"\n# 読み手間: Δ={fmt(s.delta)}・割れた={s.readers_split}・段の一致={s.levels_agree}"
          f"・代表値 {('p=' + fmt(rep.p) + (f' 段={rep.level}' if rep.level else '')) if rep else 'なし'}・{s.note or ''}")
    c = j.cost
    print(f"# 呼び出し: 生成 実 {c.plan.live}／交差検証 実 {c.crosscheck.live}／回答 実 {c.answer.live}・キャッシュ {c.cached}"
          f"・偽読み手 {c.calibration_calls}・{time.time() - t0:.0f} 秒")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
