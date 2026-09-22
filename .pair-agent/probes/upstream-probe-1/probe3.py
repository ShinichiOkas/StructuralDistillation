"""空撃ち 2 周目（p3）: 軸を「支持側の記述 / 反証側の記述」の互いに排他な対にする。

p2 の本走で分かったこと（前提崩壊）:
  H1「支持観点と反証観点の記述を同数」＋ 本文を読める生成器 ⇒ 両側とも「本文に述べられている真の事実」で
  埋まり、度合い p が構造的に 0.5 に固定される（11 題材 × 2 読み手のうち p=0.50 が 15 件）。
  本文が片側しか持たない題材では、足りない側の枠を埋めるために支持の事実へ「反証」の札が付く誤配置も起きた。

p3 の形:
  各軸 = { axis, claim_support, claim_refute }。2 つの記述は同じ事柄について互いに排他。
  読み手は各記述を独立に 述べている／否定している／触れていない で判定する。
  軸の向き: 支持側に証拠（claim_support を述べている or claim_refute を否定している）だけ → +1、
            反証側に証拠だけ → −1、両側に証拠 → 矛盾（0・数える）、どちらにも無い → 0（沈黙）。
  生成器は「両側の記述を持つ軸」を作るだけで、どちらが本文に述べられているかを釣り合わせる手段が無い。

probe.py の L0（Port）・単位化・回答（answer）・多数決・札・表示をそのまま使う。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import probe as P

PROMPT_VERSION = "p3"
DEFAULT_AXES = 6

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "axes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "axis": {"type": "string"},
                    "claim_support": {"type": "string"},
                    "claim_refute": {"type": "string"},
                },
                "required": ["axis", "claim_support", "claim_refute"],
            },
        }
    },
    "required": ["axes"],
}


def plan_prompt(units_text: str, proposition: str, n_axes: int) -> list[dict]:
    sys_msg = (
        "あなたは、命題を検証するための「観点」を設計する係です。文章を書く係ではありません。"
        "出力は指定の JSON だけを返してください。"
    )
    user = f"""以下の本文について、命題「{proposition}」を検証するための観点（軸）を {n_axes} 個作ってください。

## 規則
1. 各軸は、命題の真偽に関わる 1 つの事柄（観点）を扱う。axis はその短い名前。
2. 各軸には 2 つの記述を書く。
   - claim_support: その事柄について、本文がそう述べていれば命題を支持することになる平叙文。
   - claim_refute: 同じ事柄について、本文がそう述べていれば命題を反証することになる平叙文。
   2 つの記述は互いに排他（両方が同時に成り立つことはない）にする。
   例: 命題「旅は計画どおりに進んだ」・軸「訪れた町の数」→
       claim_support「計画していた町をすべて訪れた」／ claim_refute「計画していた町のうち訪れなかった町があった」
3. 記述は、本文と突き合わせて「述べている／否定している／触れていない」を判定できる平叙文にする。疑問文にしない。
4. 「悪い」「正しい」「優れている」のような評価語で書かない。本文中の出来事・発話・記述の有無で決まる記述だけにする。
5. 命題そのものの言い換えを記述にしない。
6. 本文に書かれているかどうかで軸を選ばない。命題の真偽を分ける事柄を選ぶ。本文が触れていない事柄が混ざってもよい。
7. 軸どうしは別の事柄を扱う。同じ事柄を言い換えて重ねない。

## 本文
{units_text}
"""
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]


def check_plan(axes: list[dict], proposition: str, n_axes: int) -> list[str]:
    v: list[str] = []
    if len(axes) != n_axes:
        v.append(f"H6 軸数: {len(axes)} (期待 {n_axes})")
    seen = set()
    for a in axes:
        cs, cr = a["claim_support"].strip(), a["claim_refute"].strip()
        if not cs or not cr:
            v.append(f"H7 空の記述: {a.get('axis')}")
            continue
        if P._norm(cs) == P._norm(cr):
            v.append(f"H7 両側が同一: {cs}")
        for c in (cs, cr):
            k = P._norm(c)
            if k in seen:
                v.append(f"H4 非重複: {c}")
            seen.add(k)
            if P._bigram_jaccard(c, proposition) >= 0.6:
                v.append(f"H3 非自明（命題の言い換え）: {c}")
            if c.endswith("か") or c.endswith("？") or c.endswith("?"):
                v.append(f"H11 疑問文: {c}")
    return v


def plan(port: P.Port, planner: str, units_text: str, proposition: str, n_axes: int, max_try: int = 3) -> dict:
    attempts = []
    last_axes = None
    for t in range(max_try):
        r = port.chat(planner, plan_prompt(units_text, proposition, n_axes), PLAN_SCHEMA, sample=t, version=PROMPT_VERSION)
        if not r["ok"]:
            attempts.append({"try": t, "error": r["error"], "violations": ["L0 失敗"]})
            continue
        try:
            axes = json.loads(r["content"])["axes"]
        except Exception as e:  # noqa: BLE001
            attempts.append({"try": t, "error": f"parse: {e}", "violations": ["H7 形式"]})
            continue
        viol = check_plan(axes, proposition, n_axes)
        attempts.append({"try": t, "violations": viol, "n_axes": len(axes)})
        for i, a in enumerate(axes):
            a["id"] = f"a{i + 1:02d}"
        if not viol:
            return {"ok": True, "axes": axes, "attempts": attempts}
        last_axes = axes
    return {"ok": False, "axes": last_axes, "attempts": attempts}


# ---------------------------------------------------------------- 集約（対の両側から向きを決める）

def side_evidence(v_support: str, v_refute: str) -> tuple[bool, bool]:
    """(支持側に証拠があるか, 反証側に証拠があるか)。v_* は Yes(述べている)/No(否定している)/判定不能/無効。"""
    sup = v_support == "Yes" or v_refute == "No"
    ref = v_refute == "Yes" or v_support == "No"
    return sup, ref


def aggregate(axes: list[dict], answers: dict[tuple[str, str, int], dict], samples: int) -> dict:
    per_axis = []
    for a in axes:
        dirs, contra = [], []
        for s in range(samples):
            vs = answers[(a["id"], "support", s)]["answer"]
            vr = answers[(a["id"], "refute", s)]["answer"]
            sup, ref = side_evidence(vs, vr)
            if sup and ref:
                dirs.append(0)
                contra.append(True)
            elif sup:
                dirs.append(1)
                contra.append(False)
            elif ref:
                dirs.append(-1)
                contra.append(False)
            else:
                dirs.append(0)
                contra.append(False)
        d, agree = P.majority(dirs)
        per_axis.append({
            "id": a["id"], "axis": a["axis"], "d": d, "agree": agree, "d_samples": dirs,
            "contradiction": sum(contra) / samples,
            "v_support": [answers[(a["id"], "support", s)]["answer"] for s in range(samples)],
            "v_refute": [answers[(a["id"], "refute", s)]["answer"] for s in range(samples)],
            "invalid": sum(1 for s in range(samples) for side in ("support", "refute") if not answers[(a["id"], side, s)]["valid"]),
            "silent": sum(1 for s in range(samples) if answers[(a["id"], "support", s)]["answer"] == "判定不能" and answers[(a["id"], "refute", s)]["answer"] == "判定不能"),
        })

    def counts(dirs: list[int]) -> dict:
        s = sum(1 for d in dirs if d == 1)
        r = sum(1 for d in dirs if d == -1)
        n = len(dirs)
        return {"s": s, "r": r, "u": n - s - r, "n": n,
                "p": s / (s + r) if s + r else None, "w": 2 * min(s, r) / (s + r) if s + r else None}

    c = counts([x["d"] for x in per_axis])
    p_by_sample = [counts([x["d_samples"][i] for x in per_axis])["p"] for i in range(samples)]
    n_answers = 2 * samples * len(per_axis)
    invalid_rate = sum(x["invalid"] for x in per_axis) / n_answers
    silent_rate = sum(x["silent"] for x in per_axis) / (samples * len(per_axis))
    contradiction_rate = statistics.mean(x["contradiction"] for x in per_axis)
    return {"A": c, "p_by_sample": p_by_sample, "invalid_rate": invalid_rate, "silent_rate": silent_rate,
            "contradiction_rate": contradiction_rate,
            "label": P.label(c, invalid_rate, contradiction_rate=contradiction_rate), "per_axis": per_axis}


def _answer_all(port, reader, axes, units, units_text, samples):
    answers = {}
    for a in axes:
        for side, claim in (("support", a["claim_support"]), ("refute", a["claim_refute"])):
            for s in range(samples):
                answers[(a["id"], side, s)] = P.answer(port, reader, units, units_text, claim, s)
    return answers


def run_material(port, m, planner, readers, samples, n_axes, log) -> dict:
    units = P.segment(m["text"])
    units_text = P.render_units(units)
    log(f"[{m['id']}] {m['title']} — 単位 {len(units)} 文。軸生成 ({planner}) …")
    pl = plan(port, planner, units_text, m["proposition"], n_axes)
    if pl["axes"] is None:
        log(f"[{m['id']}] 軸生成に失敗（F3）: {pl['attempts']}")
        return {"id": m["id"], "title": m["title"], "plan": pl, "readers": {}, "counterfactual": None}
    log(f"[{m['id']}] 軸 {len(pl['axes'])} 件 (ok={pl['ok']}, 試行 {len(pl['attempts'])})")
    result = {"id": m["id"], "title": m["title"], "proposition": m["proposition"], "expected_lean": m.get("expected_lean"),
              "n_units": len(units), "plan": pl, "readers": {}, "counterfactual": None}
    for reader in readers:
        t0 = time.time()
        agg = aggregate(pl["axes"], _answer_all(port, reader, pl["axes"], units, units_text, samples), samples)
        agg["seconds"] = round(time.time() - t0, 1)
        result["readers"][reader] = agg
        log(f"[{m['id']}] {reader}: p={P.fmt(agg['A']['p'])} w={P.fmt(agg['A']['w'])} 札={agg['label']} "
            f"矛盾率={agg['contradiction_rate']:.2f} 沈黙率={agg['silent_rate']:.2f} 無効率={agg['invalid_rate']:.2f} ({agg['seconds']}s)")
    cf = m.get("counterfactual")
    if cf:
        cunits = P.segment(cf["text"])
        ctext = P.render_units(cunits)
        result["counterfactual"] = {"changed": cf["changed"], "expected_change": cf["expected_change"], "readers": {}}
        for reader in readers:
            if reader.startswith("fake:"):
                continue
            agg = aggregate(pl["axes"], _answer_all(port, reader, pl["axes"], cunits, ctext, samples), samples)
            orig = result["readers"][reader]
            changed = sum(1 for x, y in zip(orig["per_axis"], agg["per_axis"]) if x["d"] != y["d"])
            result["counterfactual"]["readers"][reader] = {"A": agg["A"], "label": agg["label"], "changed_axes": changed,
                                                           "p_orig": orig["A"]["p"], "p_cf": agg["A"]["p"], "per_axis": agg["per_axis"]}
            log(f"[{m['id']}] 反事実 {reader}: p {P.fmt(orig['A']['p'])} → {P.fmt(agg['A']['p'])}、向きが変わった軸 {changed}/{len(pl['axes'])}")
    return result


def summarize(results: list[dict], readers: list[str], samples: int, n_axes: int) -> str:
    L = [f"# 空撃ち 2 周目（p3・排他な対）結果（読み手 {', '.join(readers)} / 標本 {samples} / 軸 {n_axes}）\n",
         "## 題材ごと\n",
         "| 題材 | 想定 | 読み手 | p | w | 札 | 段(5) | 矛盾率 | 沈黙率 | 無効率 | 標本間 |p差| |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        for rd, agg in r["readers"].items():
            ps = [p for p in agg["p_by_sample"] if p is not None]
            spread = (max(ps) - min(ps)) if len(ps) >= 2 else None
            L.append(f"| {r['id']} | {r.get('expected_lean')} | {rd} | {P.fmt(agg['A']['p'])} | {P.fmt(agg['A']['w'])} | {agg['label']} | {P.level5(agg['A']['p'])} "
                     f"| {agg['contradiction_rate']:.2f} | {agg['silent_rate']:.2f} | {agg['invalid_rate']:.2f} | {P.fmt(spread)} |")
    real = [rd for rd in readers if not rd.startswith("fake:")]
    L += ["\n## 想定との照合（実読み手・p の平均）\n", "| 題材 | 想定 | p 平均 | 札（各読み手） | 判定 |", "|---|---|---|---|---|"]
    hit = tot = 0
    for r in results:
        ps = [r["readers"][rd]["A"]["p"] for rd in real if rd in r["readers"] and r["readers"][rd]["A"]["p"] is not None]
        labels = [r["readers"][rd]["label"] for rd in real if rd in r["readers"]]
        exp = r.get("expected_lean")
        pm = statistics.mean(ps) if ps else None
        if exp == "none":
            ok = all(lb == "本文に根拠が無い" for lb in labels)
        elif pm is None:
            ok = False
        elif exp == "support":
            ok = pm > 0.6
        elif exp == "refute":
            ok = pm < 0.4
        else:
            ok = 0.3 <= pm <= 0.7
        tot += 1
        hit += int(ok)
        L.append(f"| {r['id']} | {exp} | {P.fmt(pm)} | {' / '.join(labels)} | {'✓' if ok else '✗'} |")
    L.append(f"\n- 想定に合った題材: {hit}/{tot}（支持: p>0.6、反証: p<0.4、割れる: 0.3〜0.7、根拠なし: 札が「本文に根拠が無い」）")
    L += ["\n## 読み手間の差（U3）\n", "| 題材 | " + " | ".join(readers) + " | Δ | 段の一致 |", "|---|" + "---|" * (len(readers) + 2)]
    deltas, within = [], []
    for r in results:
        ps = {rd: r["readers"][rd]["A"]["p"] for rd in readers if rd in r["readers"]}
        vals = [ps[rd] for rd in real if ps.get(rd) is not None]
        d = (max(vals) - min(vals)) if len(vals) >= 2 else None
        if d is not None:
            deltas.append(d)
        for rd in real:
            if rd in r["readers"]:
                pbs = [p for p in r["readers"][rd]["p_by_sample"] if p is not None]
                if len(pbs) >= 2:
                    within.append(max(pbs) - min(pbs))
        levels = {P.level5(p) for p in vals}
        L.append(f"| {r['id']} | " + " | ".join(P.fmt(ps.get(rd)) for rd in readers) + f" | {P.fmt(d)} | {'一致' if len(levels) <= 1 else '不一致'} |")
    if deltas:
        L.append(f"\n- 読み手間 Δ: 平均 {statistics.mean(deltas):.2f}、最大 {max(deltas):.2f}（n={len(deltas)}）")
    if within:
        L.append(f"- 標本間 |p差|: 平均 {statistics.mean(within):.2f}、最大 {max(within):.2f}（n={len(within)}）")
    L.append("\n## 矛盾率（U1・両側に証拠があった軸の割合）\n")
    for rd in readers:
        rates = [r["readers"][rd]["contradiction_rate"] for r in results if rd in r["readers"]]
        if rates:
            L.append(f"- {rd}: 平均 {statistics.mean(rates):.2f}、最大 {max(rates):.2f}")
    L += ["\n## 本文追従（S10・反事実）\n", "| 題材 | 期待 | 読み手 | p 元 → 反事実 | 向きが変わった軸 |", "|---|---|---|---|---|"]
    ok_n = n = 0
    for r in results:
        cf = r.get("counterfactual")
        if not cf:
            continue
        for rd, c in cf["readers"].items():
            po, pc = c["p_orig"], c["p_cf"]
            ok = None
            if po is not None and pc is not None:
                ok = (pc > po) if cf["expected_change"] == "support_up" else (pc < po)
                n += 1
                ok_n += int(ok)
            L.append(f"| {r['id']} | {cf['expected_change']} | {rd} | {P.fmt(po)} → {P.fmt(pc)} {'✓' if ok else ('✗' if ok is False else '—')} | {c['changed_axes']} |")
    if n:
        L.append(f"\n- 期待した向きに動いた: {ok_n}/{n}")
    L.append("\n## ハーネス違反（軸生成）\n")
    for r in results:
        pl = r["plan"]
        v = [a for a in pl["attempts"] if a.get("violations")]
        L.append(f"- {r['id']}: 試行 {len(pl['attempts'])}、最終 ok={pl['ok']}" + (f"、違反 {v}" if v else ""))
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--materials", default=str(P.HERE / "materials.json"))
    ap.add_argument("--out", default=str(P.HERE / "out3"))
    ap.add_argument("--planner", default=P.DEFAULT_PLANNER)
    ap.add_argument("--readers", nargs="*", default=P.DEFAULT_READERS)
    ap.add_argument("--samples", type=int, default=2)
    ap.add_argument("--axes", type=int, default=DEFAULT_AXES)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--dry-run", action="store_true", help="m06 だけ・読み手の先頭 1 体・標本 1")
    ap.add_argument("--cache-only", action="store_true")
    ap.add_argument("--fake", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    port = P.Port(out / "llm_cache.jsonl", cache_only=args.cache_only)
    mats = json.loads(Path(args.materials).read_text(encoding="utf-8"))["materials"]
    readers = list(args.readers)
    samples = args.samples
    if args.dry_run:
        mats = [m for m in mats if m["id"] == "m06"]
        readers = readers[:1]
        samples = 1
    if args.only:
        mats = [m for m in mats if m["id"] in set(args.only)]
    if args.fake:
        readers = readers + ["fake:all_yes", "fake:all_undetermined"]
    log_path = out / "run.log"

    def log(msg: str):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    log(f"=== 開始 p3 planner={args.planner} readers={readers} samples={samples} axes={args.axes} 題材={[m['id'] for m in mats]}")
    results = []
    for m in mats:
        results.append(run_material(port, m, args.planner, readers, samples, args.axes, log))
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = summarize(results, readers, samples, args.axes)
    (out / "summary.md").write_text(summary, encoding="utf-8")
    log(f"=== 終了 live={port.calls_live} cached={port.calls_cached}")
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
