"""適合検査（実装設計 §9）: 測定 2 周目のキャッシュだけで judge() を回し、記録と一致するかを見る。LLM は呼ばない。

① base21 のキャッシュだけ（交差検証なし）: 21 題材の主走行と反事実を judge() に通し、
   外れ 0 で、生成された軸・p・w・札・軸ごとの向きが results.json と一致するか
② base21 ＋ xc2 のキャッシュ（交差検証あり）: 外した・弱・非排他が crosscheck.json と一致し、外した後の p・w が一致するか。
   ⚠ この 21 題材は強い生成器の軸なので、外された軸は 0（受入 M1）。軸を外す経路は ③ が通す
③ 弱い生成器（p3・gemma4:12b）の軸 11 題材 × xc2 のキャッシュで交差検証し、外した軸（4 題材・5 軸）を含めて
   外した・弱・非排他が crosscheck.json と一致するか。さらにクラウドの読み手 3 体の回答（m3arm のキャッシュ）で、
   外す前と外した後の p・w が一致するか

cache_only なので実呼び出しは構造的に 0。意味のある指標は「外れ 0」（キャッシュに鍵が当たった＝指示と鍵が逐語）。
測定の記録は読み取り専用で開く（.pair-agent/probes/ には一切書かない）。

    python tools/conformance_out4.py
    python tools/conformance_out4.py --negative-control     # 回答の版を変える。全題材が外れて不一致になるはず
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import math
import sys

from _cli import guard_write_path, utf8_io
from probe_records import LABEL, OUT4, PROBE_DIR, load_materials, load_results

from structural_distillation import judge, l1
from structural_distillation.contracts import Axis, Budget, PlanningFailed, Probability, QuestionSet, Thresholds
from structural_distillation.l0 import CachedPort, FakeReader, Meter, OllamaReader
from structural_distillation.prompts import PromptSet

PLANNER = "gemma4:31b-cloud"
READERS = ["qwen3.5:397b-cloud", "glm-5.2:cloud"]
M3_READERS = ["gemma4:31b-cloud", "qwen3.5:397b-cloud", "glm-5.2:cloud"]
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


def compare_cc(cc, row: dict) -> list[str]:
    errs = []
    for key, want in (("flagged", row["flagged_agree"]), ("weak", row["weak"]), ("nonexclusive", row["nonexclusive"])):
        if sorted(getattr(cc, key)) != sorted(want):
            errs.append(f"{key} {getattr(cc, key)} ≠ {want}")
    return errs


class Tally:
    def __init__(self):
        self.live = self.missed = self.cached = 0

    def add(self, cost):
        self.live += cost.live
        self.missed += cost.missed
        self.cached += cost.cached


async def run_base(m: dict, rec: dict, caches: list, crosscheck: bool, xc_row: dict | None, prompts: PromptSet) -> dict:
    t = Tally()
    errs: list[str] = []
    planner = port(PLANNER, caches)
    readers = [port(r, caches) for r in READERS] + [FakeReader(k) for k in FAKES if f"fake:{k}" in rec["readers"]]
    budget = Budget(axes=6, samples=2, workers=16, crosscheck=crosscheck)
    try:
        j = await judge(m["text"], m["proposition"], Probability(), readers=readers, planner=planner,
                        verifiers=[port(v, caches) for v in VERIFIERS], budget=budget, thresholds=RUN_TIME, prompts=prompts)
    except PlanningFailed as e:
        return {"errors": [f"生成がキャッシュから再現できない: {e}"], "live": 0, "missed": 1, "cached": 0}
    t.add(j.cost)
    if [(a.claim_support, a.claim_refute) for a in j.question_set.axes] != \
            [(a["claim_support"], a["claim_refute"]) for a in rec["plan"]["axes"]]:
        errs.append("生成された軸が記録と違う")
    if not crosscheck:
        for name, reading in j.readings.items():
            errs += [f"{name}: {e}" for e in compare_reading(reading, rec["readers"][name])]
        cf = m.get("counterfactual")
        if cf and rec.get("counterfactual"):
            k = await judge(cf["text"], m["proposition"], Probability(), readers=[port(r, caches) for r in READERS],
                            question_set=j.question_set, budget=budget, thresholds=RUN_TIME, prompts=prompts)
            t.add(k.cost)
            for name, reading in k.readings.items():
                errs += [f"反事実 {name}: {e}" for e in compare_reading(reading, rec["counterfactual"]["readers"][name])]
    else:
        errs += compare_cc(j.question_set.crosscheck, xc_row)
        for name in READERS:
            after = xc_row["readers"][name]["after"]
            r = j.readings[name]
            if not same(r.p, after["p"]) or not same(r.w, after["w"]):
                errs.append(f"{name}: 外した後の p/w {r.p}/{r.w} ≠ {after['p']}/{after['w']}")
    if t.live or t.missed:   # 値が未定義どうしでも、外れがあれば一致とは言わない
        errs.append(f"外れ {t.missed}・実呼び出し {t.live}（0 のはず）")
    return {"errors": errs, "live": t.live, "missed": t.missed, "cached": t.cached}


async def run_removal(m: dict, axes_rec: list[dict], row: dict, prompts: PromptSet) -> dict:
    """③ 弱い生成器の軸で、軸を外す経路を実データで通す。"""
    t = Tally()
    meter = Meter()
    axes = [Axis(a["id"], a["axis"], a["claim_support"], a["claim_refute"]) for a in axes_rec]
    xc_cache = [OUT4 / "xc2" / "llm_cache.jsonl"]
    cc = await l1.crosscheck([port(v, xc_cache) for v in VERIFIERS], m["proposition"], axes, prompts=prompts,
                             sem=asyncio.Semaphore(16), meter=meter)
    t.add(meter.cost)
    errs = compare_cc(cc, row)
    m3 = [OUT4 / "m3arm" / "llm_cache.jsonl"]
    budget = Budget(samples=2, workers=16, crosscheck=False)
    for which, active in (("before", [a.id for a in axes]), ("after", [a.id for a in axes if a.id not in cc.flagged])):
        qs = QuestionSet(axes=axes, active_ids=active, planner="gemma4:12b", prompt=prompts.version(), budget=budget,
                         crosscheck=cc)
        j = await judge(m["text"], m["proposition"], Probability(), readers=[port(r, m3) for r in M3_READERS],
                        question_set=qs, budget=budget, thresholds=RUN_TIME, prompts=prompts)
        t.add(j.cost)
        for name in M3_READERS:
            want = row["readers"][name][which]
            r = j.readings[name]
            if not same(r.p, want["p"]) or not same(r.w, want["w"]):
                errs.append(f"{name}: {'外す前' if which == 'before' else '外した後'}の p/w {r.p}/{r.w} ≠ {want['p']}/{want['w']}")
    if t.live or t.missed:
        errs.append(f"外れ {t.missed}・実呼び出し {t.live}（0 のはず）")
    return {"errors": errs, "live": t.live, "missed": t.missed, "cached": t.cached, "flagged": cc.flagged}


async def main_async(args) -> int:
    prompts = PromptSet.builtin("ja")
    if args.negative_control:
        prompts = dataclasses.replace(prompts, answer=dataclasses.replace(prompts.answer, version="p2-negative-control"))
    mats = load_materials()
    recs = {r["id"]: r for r in load_results("base21")}
    xc_all = json.loads((OUT4 / "xc2" / "crosscheck.json").read_text(encoding="utf-8"))
    xc_rows = {r["id"]: r for r in xc_all if r["planner"].startswith("この走行")}
    p3_rows = {r["id"]: r for r in xc_all if not r["planner"].startswith("この走行")}
    p3_axes = {r["id"]: r["plan"]["axes"] for r in json.loads((PROBE_DIR / "out3" / "results.json").read_text(encoding="utf-8"))}
    base = [OUT4 / "base21" / "llm_cache.jsonl"]
    both = base + [OUT4 / "xc2" / "llm_cache.jsonl"]
    parts: dict[str, list[tuple[str, dict]]] = {"①": [], "②": [], "③": []}
    for mid in recs:
        parts["①"].append((mid, await run_base(mats[mid], recs[mid], base, False, None, prompts)))
        if not args.skip_crosscheck:
            parts["②"].append((mid, await run_base(mats[mid], recs[mid], both, True, xc_rows[mid], prompts)))
    if not args.skip_crosscheck:
        for mid, row in p3_rows.items():
            parts["③"].append((mid, await run_removal(mats[mid], p3_axes[mid], row, prompts)))
    names = {"①": "① 交差検証なし（主走行＋反事実・21 題材）", "②": "② 交差検証あり（強い生成器の軸・21 題材・外れる軸 0）",
             "③": "③ 軸を外す経路（弱い生成器の軸・11 題材）"}
    bad = 0
    for key, rows in parts.items():
        if not rows:
            continue
        ok = sum(1 for _, r in rows if not r["errors"])
        flagged = sum(len(r.get("flagged") or []) for _, r in rows)
        extra = f"・外した軸 {flagged}（{sum(1 for _, r in rows if r.get('flagged'))} 題材）" if key == "③" else ""
        print(f"{names[key]}: 一致 {ok}/{len(rows)}・外れ {sum(r['missed'] for _, r in rows)}"
              f"・キャッシュ命中 {sum(r['cached'] for _, r in rows)}・実呼び出し {sum(r['live'] for _, r in rows)}{extra}")
        for mid, r in rows:
            if r["errors"]:
                bad += 1
                if not args.quiet:
                    print(f"  ✗ {mid}")
                    for e in r["errors"][:6]:
                        print(f"      {e}")
    if args.json_out:
        with open(guard_write_path(args.json_out), "w", encoding="utf-8") as f:
            json.dump({k: [{"id": mid, **r} for mid, r in v] for k, v in parts.items()}, f, ensure_ascii=False, indent=1)
    return 1 if bad else 0


def main() -> int:
    utf8_io()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-crosscheck", action="store_true", help="① だけ")
    ap.add_argument("--negative-control", action="store_true", help="回答の版を変えて走らせる（落ちることを確かめる）")
    ap.add_argument("--quiet", action="store_true", help="不一致の中身を出さない")
    ap.add_argument("--json-out", default=None, help="結果の JSON（測定の記録の外に置くこと）")
    args = ap.parse_args()
    if not OUT4.exists():
        print("測定 2 周目の記録が無い:", OUT4)
        return 2
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
