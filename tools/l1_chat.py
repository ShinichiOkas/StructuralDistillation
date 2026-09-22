"""L1（問い生成）だけを触る最小の CLI。本文と命題から軸の対を作り、規則違反・試行・交差検証の票を見せる。

    python tools/l1_chat.py 本文.txt "ゲンは悪人である" --planner gemma4:31b-cloud \
        --verifiers qwen3.5:397b-cloud glm-5.2:cloud --cache scratch/l1.jsonl
    python tools/l1_chat.py 本文.txt "命題" --planner gemma4:31b-cloud --no-crosscheck

--extra に測定のキャッシュを渡すと読むだけで使う（書かない）。⚠ クラウドモデル（-cloud）を使う。
"""
from __future__ import annotations

import argparse
import asyncio

from _cli import guard_write_path, read_text, utf8_io

from structural_distillation import l1
from structural_distillation.contracts import Budget, PlanningFailed
from structural_distillation.l0 import CachedPort, Meter, OllamaReader
from structural_distillation.prompts import PromptSet
from structural_distillation.units import segment


def wrap(model: str, args) -> CachedPort | OllamaReader:
    r = OllamaReader(model, num_ctx=args.num_ctx)
    if args.cache or args.extra:
        return CachedPort(r, args.cache, extra=args.extra or (), cache_only=args.cache_only)
    return r


def main() -> int:
    utf8_io()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", help="本文のファイル（- で標準入力）")
    ap.add_argument("proposition")
    ap.add_argument("--planner", required=True)
    ap.add_argument("--verifiers", nargs="*", default=[])
    ap.add_argument("--axes", type=int, default=6)
    ap.add_argument("--no-crosscheck", action="store_true")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--extra", nargs="*", default=[], help="読むだけのキャッシュ（測定の記録など）")
    ap.add_argument("--cache-only", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--num-ctx", type=int, default=8192)
    args = ap.parse_args()
    guard_write_path(args.cache)

    p = PromptSet.builtin("ja")
    units = segment(read_text(args.text))
    budget = Budget(axes=args.axes, crosscheck=not args.no_crosscheck, workers=args.workers)
    meter = Meter()

    async def go():
        sem = asyncio.Semaphore(budget.workers)
        return await l1.plan(wrap(args.planner, args), units, args.proposition, budget=budget, prompts=p,
                             verifiers=[wrap(v, args) for v in args.verifiers], sem=sem, meter=meter)
    try:
        qs = asyncio.run(go())
    except PlanningFailed as e:
        print(f"生成に失敗（F3）: {e}")
        return 1
    print(f"# 命題「{args.proposition}」・単位 {len(units)}・生成器 {qs.planner}・指示 {qs.prompt.plan}")
    for a in qs.attempts:
        print(f"  試行 {a.index}: 軸 {a.n_axes}・違反 {list(a.violations) or 'なし'}{'・' + a.error if a.error else ''}")
    for a in qs.axes:
        mark = "  " if a.id in qs.active_ids else "✗ "
        print(f"{mark}{a.id} [{a.name}]\n     支持側: {a.claim_support}\n     反証側: {a.claim_refute}")
    if qs.crosscheck:
        cc = qs.crosscheck
        print(f"# 交差検証（{', '.join(cc.verifiers)}）: 外した {cc.flagged or 'なし'}・弱 {cc.weak or 'なし'}・非排他 {cc.nonexclusive or 'なし'}")
        for v in cc.votes:
            summ = "  ".join(f"{name}: 支持側 {c['support']['majority']} / 反証側 {c['refute']['majority']}"
                             for name, c in v["checkers"].items())
            print(f"  {v['id']}  {summ}  排他 {v['exclusivity']}")
    else:
        print("# 交差検証なし")
    c = meter.cost
    print(f"# 呼び出し: 生成 実 {c.plan.live}・キャッシュ {c.plan.cached}／交差検証 実 {c.crosscheck.live}・キャッシュ {c.crosscheck.cached}"
          f"／外れ {c.missed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
