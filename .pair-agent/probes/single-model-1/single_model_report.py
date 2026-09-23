"""単一モデル運用の読み。合意 single-model-operation v2 の判断規則 R-a〜R-d をそのまま当てる。LLM は呼ばない。"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

EXCLUDE = {"m11", "m12", "m17"}        # 基準走行と同じ除外（K12。m17 は構成ミス、m11・m12 は想定の付け方）
BASELINE_HIT = 16                       # クラウド多系統・題材 18 の想定一致（測定 2 周目）
BASELINE_DELTA = {"強い 2 体": 0.04, "弱い 2 体": 0.155}


def hit(expected: str | None, ps: list[float], labels: list[str]) -> bool | None:
    """想定との照合（空撃ち・測定 2 周目と同じ規則）。"""
    if expected == "none":
        return all(lb in ("NO_EVIDENCE", "INSTRUMENT_FAULT") for lb in labels)
    if not ps:
        return False
    pm = statistics.mean(ps)
    if expected == "support":
        return pm > 0.6
    if expected == "refute":
        return pm < 0.4
    if expected == "split":
        return 0.3 <= pm <= 0.7
    return None


def jaccard(a: set, b: set) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    data = json.loads((Path(args.run) / "single_model.json").read_text(encoding="utf-8"))
    rows = [r for r in data.values() if "readings" in r]
    L = ["# 単一モデル運用の測定（弱いモデル 1 体）— 判断規則どおりの読み\n",
         f"題材 {len(rows)}（想定の照合は除外 {sorted(EXCLUDE)} を除く {len([r for r in rows if r['id'] not in EXCLUDE])}）\n"]

    # ---- 題材ごと
    L += ["## 題材ごと\n",
          "| 題材 | 想定 | p(a) | p(b) | 札(a) | 札(b) | Δ | 矛盾率(a/b) | 自己で外した | 雲で外した | 自己 弱 | 雲 弱 | 検出項目 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    hits = det_fail_ids, det_ok_ids = [], []
    hits = {}
    deltas, label_disagree, both_valued = [], 0, 0
    self_flag, cloud_flag, self_weak, cloud_weak = {}, {}, {}, {}
    det_fail = {}
    for r in sorted(rows, key=lambda x: x["id"]):
        real = {n: v for n, v in r["readings"].items() if not n.startswith("fake:")}
        names = sorted(real)
        ps = [v["p"] for v in real.values() if v["p"] is not None and v["label"] not in ("NO_EVIDENCE", "INSTRUMENT_FAULT")]
        labels = [v["label"] for v in real.values()]
        h = hit(r.get("expected_lean"), ps, labels)
        hits[r["id"]] = h
        if len(ps) == 2:
            deltas.append(abs(ps[0] - ps[1]))
            both_valued += 1
        if len(set(labels)) > 1:
            label_disagree += 1
        sf = set(r["self_crosscheck"]["flagged"]) if r.get("self_crosscheck") else set()
        cf = set((r.get("cloud_crosscheck") or {}).get("flagged") or [])
        sw = set(r["self_crosscheck"]["weak"]) if r.get("self_crosscheck") else set()
        cw = set((r.get("cloud_crosscheck") or {}).get("weak") or [])
        self_flag[r["id"]], cloud_flag[r["id"]], self_weak[r["id"]], cloud_weak[r["id"]] = sf, cf, sw, cw
        # 検出項目: 明らかにある記述に「述べている」・絶対に無い記述に「述べている」でない、を両方の読み手で
        det = r.get("detection", {}).get("answers", {})
        bad = []
        for rd, a in det.items():
            if a.get("support", {}).get("verdict") != "STATES" or not a.get("support", {}).get("valid"):
                bad.append(f"{rd}: ある記述を取りこぼし")
            if a.get("refute", {}).get("verdict") == "STATES":
                bad.append(f"{rd}: 無い記述を述べているとした")
        det_fail[r["id"]] = bad
        v = list(real.values())
        f = lambda x: "—" if x is None else f"{x:.2f}"  # noqa: E731
        L.append(f"| {r['id']} | {r.get('expected_lean')} | {f(v[0]['p'])} | {f(v[1]['p'])} | {v[0]['label']} | {v[1]['label']} "
                 f"| {f(r['delta'])} | {f(v[0]['contradiction_rate'])}/{f(v[1]['contradiction_rate'])} "
                 f"| {sorted(sf) or 'なし'} | {sorted(cf) or 'なし'} | {len(sw)} | {len(cw)} | {'✗ ' + '; '.join(bad) if bad else '✓'} |")

    scored = [i for i in hits if i not in EXCLUDE and hits[i] is not None]
    n_hit = sum(1 for i in scored if hits[i])
    L.append(f"\n- **想定に合った題材: {n_hit}/{len(scored)}**（クラウド多系統の基準は {BASELINE_HIT}/18）")

    # ---- R-a
    drop = BASELINE_HIT - n_hit
    verdict_a = ("弱い単一モデルは判定を歪める" if drop >= 3 else
                 "弱さ由来の揺れ（結論にしない）" if drop >= 1 else "基準と同等")
    L += ["\n## R-a 判定の歪み\n",
          f"- 想定一致 {n_hit}/{len(scored)} 対 基準 {BASELINE_HIT}/18 → 差 {drop} 件 → **{verdict_a}**（規則: 3 件以上落ちたら歪むと読む）"]

    # ---- R-b
    med = statistics.median(deltas) if deltas else None
    verdict_b = ("同じモデルを 2 体に見せても Q1 の計器にならない" if med is not None and med < 0.05 else
                 "標本の分割でも差は出る" if med is not None and med >= 0.15 else "どちらとも言えない（結論にしない）")
    L += ["\n## R-b 見かけの複数体\n",
          f"- 両方が値を持った題材 {both_valued}/{len(rows)}・Δ の中央値 {med if med is None else round(med, 3)}"
          f"（本物の Δ: 強い 2 体 {BASELINE_DELTA['強い 2 体']}・弱い 2 体 {BASELINE_DELTA['弱い 2 体']}）→ **{verdict_b}**",
          f"- 札が割れた題材（同じモデル・同じ問い・別の呼び出し）: {label_disagree}/{len(rows)}"]

    # ---- R-c
    failed = [i for i in hits if det_fail[i]]
    passed = [i for i in hits if not det_fail[i]]
    def miss_rate(ids):
        ids = [i for i in ids if i not in EXCLUDE and hits[i] is not None]
        return (sum(1 for i in ids if not hits[i]) / len(ids)) if ids else None
    mf, mp = miss_rate(failed), miss_rate(passed)
    if not failed:
        verdict_c = "この題材集合では検出項目が捕まえるものが無かった（弱いモデルでも検出項目は通る）"
    elif mp is not None and mf is not None and mp > 0 and mf >= 2 * mp:
        verdict_c = "検出項目は計器不良を捕まえる"
    else:
        verdict_c = "検出項目と想定外しは結びつかない"
    L += ["\n## R-c 検出項目\n",
          f"- 検出項目を外した題材 {len(failed)}/{len(rows)}（{sorted(failed) or 'なし'}）",
          f"- 想定を外す率: 検出項目に通った題材 {mp if mp is None else round(mp, 2)}・外した題材 {mf if mf is None else round(mf, 2)}"
          f" → **{verdict_c}**"]

    # ---- R-d
    js = [jaccard(self_flag[i], cloud_flag[i]) for i in self_flag if (self_flag[i] or cloud_flag[i])]
    mean_j = statistics.mean(js) if js else None
    verdict_d = ("自己交差検証で代替できる" if mean_j is not None and mean_j >= 0.5 else
                 "検証役は別系統が要る" if mean_j is not None else "外れた軸が両方 0 件（比べられない）")
    n_self = sum(len(v) for v in self_flag.values())
    n_cloud = sum(len(v) for v in cloud_flag.values())
    wj = [jaccard(self_weak[i], cloud_weak[i]) for i in self_weak if (self_weak[i] or cloud_weak[i])]
    L += ["\n## R-d 自己交差検証と他系統の検証役\n",
          f"- 外した軸: 自己 {n_self} 軸・クラウド {n_cloud} 軸（題材で見ると 自己 {sum(1 for v in self_flag.values() if v)}・"
          f"雲 {sum(1 for v in cloud_flag.values() if v)}）",
          f"- 外した軸の一致（Jaccard の平均・どちらかが外した題材だけ）: {mean_j if mean_j is None else round(mean_j, 2)} → **{verdict_d}**",
          f"- 「弱」と見た軸の一致（Jaccard の平均）: {statistics.mean(wj):.2f}（自己 {sum(len(v) for v in self_weak.values())} 軸・"
          f"雲 {sum(len(v) for v in cloud_weak.values())} 軸）" if wj else "- 「弱」は両方 0"]

    # ---- 付録: 他系統の検証役を借りたら判定は直るか（LLM は呼ばない。同じ回答を引き直す）
    import sys as _sys
    _sys.path.insert(0, r"S:/work/develop/StructuralDistillation")
    from structural_distillation import l3                                  # noqa: E402
    from structural_distillation.contracts import AnswerMatrix, Thresholds  # noqa: E402
    recs = [json.loads(x) for x in (Path(args.run) / "records.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    by_axes = {}
    for rec in recs:
        key = tuple(a["claim_support"] for a in rec["question_set"]["axes"])
        if len(key) > 1:                       # 検出項目（1 軸）の記録は除く
            by_axes[key] = rec
    borrowed = {}
    for r in rows:
        key = tuple(a["support"] for a in r["axes"])
        rec = by_axes.get(key)
        if not rec:
            continue
        keep = [a["id"] for a in r["axes"] if a["id"] not in set((r.get("cloud_crosscheck") or {}).get("flagged") or [])]
        ps, labels = [], []
        for m in rec["matrices"]:
            if m["reader"].startswith("fake:"):
                continue
            reading = l3.aggregate(AnswerMatrix.from_dict(m), keep, Thresholds())
            labels.append(reading.label.value)
            if reading.p is not None and reading.label not in ("NO_EVIDENCE", "INSTRUMENT_FAULT"):
                ps.append(reading.p)
        borrowed[r["id"]] = hit(r.get("expected_lean"), ps, labels)
    b_scored = [i for i in borrowed if i not in EXCLUDE and borrowed[i] is not None]
    b_hit = sum(1 for i in b_scored if borrowed[i])
    L += ["\n## 付録: 他系統の検証役を借りたら（同じ回答を、クラウドが外した軸を抜いて引き直す）\n",
          f"- 想定一致 {b_hit}/{len(b_scored)}（自己のまま {n_hit}/{len(scored)}・クラウド多系統の基準 {BASELINE_HIT}/18）",
          f"- 直った題材: {sorted(i for i in b_scored if borrowed[i] and not hits[i]) or 'なし'}"
          f"・壊れた題材: {sorted(i for i in b_scored if not borrowed[i] and hits[i]) or 'なし'}"]

    # ---- 参考: 偽読み手・費用・時間
    fakes = {}
    for r in rows:
        for n, v in r["readings"].items():
            if n.startswith("fake:"):
                fakes.setdefault(n, []).append((v["label"], v["reason"]))
    L += ["\n## 参考\n"]
    for n, vs in fakes.items():
        kinds = {}
        for lb, rs in vs:
            kinds[f"{lb}/{rs}"] = kinds.get(f"{lb}/{rs}", 0) + 1
        L.append(f"- 偽読み手 {n}: {kinds}")
    L.append(f"- 走行時間 合計 {sum(r['seconds'] for r in rows)/60:.0f} 分（題材あたり 中央値 "
             f"{statistics.median([r['seconds'] for r in rows]):.0f} 秒）")
    L.append(f"- 呼び出し（ローカル）: 生成 {sum(r['cost']['plan'] for r in rows)}・"
             f"交差検証 {sum(r['cost']['crosscheck'] for r in rows)}・回答 {sum(r['cost']['answer'] for r in rows)}")
    text = "\n".join(L) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
