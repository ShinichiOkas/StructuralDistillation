"""適合検査（実装設計 §9）: 測定 2 周目のキャッシュだけで judge() を回し、記録と一致するかを見る。LLM は呼ばない。

① base21 のキャッシュだけ（交差検証なし）: 21 題材の主走行と反事実を judge() に通し、
   実呼び出し 0・外れ 0 で、生成された軸・p・w・札・軸ごとの向きが results.json と一致するか
② base21 ＋ xc2 のキャッシュ（交差検証あり）: 外した・弱・非排他が crosscheck.json と一致し、外した後の p・w が一致するか

測定の記録は読み取り専用で開く（.pair-agent/probes/ には一切書かない）。

    python tools/conformance_out4.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys

from _cli import utf8_io
from probe_records import LABEL, OUT4, load_materials, load_results

from structural_distillation import judge
from structural_distillation.contracts import Budget, Probability, QuestionSet, Thresholds
from structural_distillation.l0 import CachedPort, FakeReader, OllamaReader

PLANNER = "gemma4:31b-cloud"
READERS = ["qwen3.5:397b-cloud", "glm-5.2:cloud"]
VERIFIERS = ["glm-5.2:cloud", "qwen3.5:397b-cloud"]
FAKES = ["all_yes", "all_no", "all_undetermined"]
RUN_TIME = Thresholds(iota=0.3, kappa=0.5, rho=0.5, omega=0.5)   # 記録の札はこの閾値で付いた


def port(model: str, caches: list) -> CachedPort:
    return CachedPort(OllamaReader(model), None, cache_only=True, read_only=True, extra=caches)


def same(a, b) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=0, abs_tol=1e-12)


def compare_reading(reading, agg: dict) -> list[str]:
    errs = []
    if not same(reading.p, agg["A"]["p"]) or not same(reading.w, agg["A"]["w"]):
        errs.append(f"p/w {reading.p}/{reading.w} ≠ {agg['A']['p']}/{agg['A']['w']}")
    if reading.label != LABEL[agg["label"]]:
        errs.append(f"札 {reading.label.value} ≠ {agg['label']}")
    got = [(x.axis_id, x.d) for x in reading.axes]
    want = [(x["id"], x["d"]) for x in agg["per_axis"]]
    if got != want:
        errs.append(f"軸の向き {got} ≠ {want}")
    return errs


async def run_one(m: dict, rec: dict, caches: list, crosscheck: bool, xc_row: dict | None, report: list) -> dict:
    planner = port(PLANNER, caches)
    readers = [port(r, caches) for r in READERS] + [FakeReader(k) for k in FAKES if f"fake:{k}" in rec["readers"]]
    verifiers = [port(v, caches) for v in VERIFIERS]
    budget = Budget(axes=6, samples=2, workers=16, crosscheck=crosscheck)
    j = await judge(m["text"], m["proposition"], Probability(), readers=readers, planner=planner, verifiers=verifiers,
                    budget=budget, thresholds=RUN_TIME)
    errs: list[str] = []
    want_axes = [(a["claim_support"], a["claim_refute"]) for a in rec["plan"]["axes"]]
    got_axes = [(a.claim_support, a.claim_refute) for a in j.question_set.axes]
    if got_axes != want_axes:
        errs.append("生成された軸が記録と違う")
    live, missed, cached = j.cost.live, j.cost.missed, j.cost.cached
    if not crosscheck:
        for name, reading in j.readings.items():
            errs += [f"{name}: {e}" for e in compare_reading(reading, rec["readers"][name])]
        cf = m.get("counterfactual")
        if cf and rec.get("counterfactual"):
            qs: QuestionSet = j.question_set
            k = await judge(cf["text"], m["proposition"], Probability(), readers=[port(r, caches) for r in READERS],
                            question_set=qs, budget=budget, thresholds=RUN_TIME)
            live += k.cost.live
            missed += k.cost.missed
            cached += k.cost.cached
            for name, reading in k.readings.items():
                errs += [f"反事実 {name}: {e}" for e in compare_reading(reading, rec["counterfactual"]["readers"][name])]
    else:
        cc = j.question_set.crosscheck
        for key, want in (("flagged", xc_row["flagged_agree"]), ("weak", xc_row["weak"]), ("nonexclusive", xc_row["nonexclusive"])):
            if sorted(getattr(cc, key)) != sorted(want):
                errs.append(f"{key} {getattr(cc, key)} ≠ {want}")
        for name in READERS:
            after = xc_row["readers"][name]["after"]
            r = j.readings[name]
            if not same(r.p, after["p"]) or not same(r.w, after["w"]):
                errs.append(f"{name}: 外した後の p/w {r.p}/{r.w} ≠ {after['p']}/{after['w']}")
    if live or missed:
        errs.append(f"実呼び出し {live}・外れ {missed}（0 のはず）")
    report.append({"id": m["id"], "crosscheck": crosscheck, "errors": errs, "live": live, "missed": missed,
                   "cached": cached})
    return j


async def main_async(args) -> int:
    mats = load_materials()
    recs = {r["id"]: r for r in load_results("base21")}
    xc_rows = {r["id"]: r for r in json.loads((OUT4 / "xc2" / "crosscheck.json").read_text(encoding="utf-8"))
               if r["planner"].startswith("この走行")}
    base = [OUT4 / "base21" / "llm_cache.jsonl"]
    both = base + [OUT4 / "xc2" / "llm_cache.jsonl"]
    report: list[dict] = []
    for mid in recs:
        await run_one(mats[mid], recs[mid], base, False, None, report)
        if not args.skip_crosscheck:
            await run_one(mats[mid], recs[mid], both, True, xc_rows[mid], report)
    bad = [r for r in report if r["errors"]]
    for part, flag in (("① 交差検証なし（主走行＋反事実）", False), ("② 交差検証あり", True)):
        rows = [r for r in report if r["crosscheck"] is flag]
        if not rows:
            continue
        ok = sum(1 for r in rows if not r["errors"])
        print(f"{part}: 一致 {ok}/{len(rows)} 題材・実呼び出し {sum(r['live'] for r in rows)}・外れ {sum(r['missed'] for r in rows)}"
              f"・キャッシュ命中 {sum(r['cached'] for r in rows)}")
    for r in bad:
        print(f"  ✗ {r['id']}（交差検証 {'あり' if r['crosscheck'] else 'なし'}）")
        for e in r["errors"]:
            print(f"      {e}")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    return 1 if bad else 0


def main() -> int:
    utf8_io()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-crosscheck", action="store_true")
    ap.add_argument("--json-out", default=None, help="結果の JSON（記録の外に置くこと）")
    args = ap.parse_args()
    if not OUT4.exists():
        print("測定 2 周目の記録が無い:", OUT4)
        return 2
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
