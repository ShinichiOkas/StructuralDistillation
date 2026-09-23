"""単一モデル運用の読み。合意 single-model-operation v3 の判断規則 R-a〜R-g をそのまま当てる。LLM は呼ばない。

受入（不合格・critical 4）を受けて第 2 版:
  - C1 生成器と読み手を分ける腕 W5（強い軸 × 弱い読み手）を足し、2×2 で比べる
  - C2 「読み手は健全」は撤回。同じ軸を強い読み手と弱い読み手に当てた矛盾率・無効率で置き換える
  - C3 「弱率が 0.85 一致」は撤回。弱の付き方が飽和しているので、偶然一致を引いた κ で見る
  - M3 自己交差検証は Jaccard ではなく、雲を基準にした適合率・再現率で見る
  - M4 非排他の率を、強い生成器の軸と比べる（1 モデルでも使える門の候補）
  - M11 統計量の定義を報告に書く
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path


def find_repo() -> Path:
    """リポジトリの場所（受入 m10: 絶対パスを直書きしない）。環境変数 SD_REPO ＞ 自分の上 ＞ いまいる場所。"""
    env = os.environ.get("SD_REPO")
    if env:
        return Path(env)
    for p in [*Path(__file__).resolve().parents, Path.cwd(), *Path.cwd().parents]:
        if (p / "structural_distillation" / "compose.py").exists():
            return p
    raise SystemExit("リポジトリが見つからない。環境変数 SD_REPO にリポジトリのパスを入れて走らせる")


REPO = find_repo()
EXCLUDE = {"m11", "m12", "m17"}        # 基準走行と同じ除外（K12。m17 は構成ミス、m11・m12 は想定の付け方）
BASELINE_HIT = 16                       # クラウド多系統・題材 18 の想定一致（測定 2 周目）
BASELINE_DELTA = {"強い 2 体": 0.04, "弱い 2 体": 0.155}
STRONG_NONEXCLUSIVE = 0.024             # 強い生成器の軸の非排他率（out4/xc2・126 軸中 3 軸）
STRONG_WEAK = 0.698                     # 同・弱率（126 軸中 88 軸。強い軸でも 7 割が「弱」＝弱率は門にならない）


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


def kappa(agree: float, pa: float, pb: float) -> float:
    """偶然一致を引いた一致（Cohen の κ の 2 値版）。飽和した印の一致を額面で読まないため（受入 C3）。"""
    chance = pa * pb + (1 - pa) * (1 - pb)
    return (agree - chance) / (1 - chance) if chance < 1 else 0.0


def scored_hits(hits: dict) -> tuple[int, int]:
    ids = [i for i in hits if i not in EXCLUDE and hits[i] is not None]
    return sum(1 for i in ids if hits[i]), len(ids)


def rates(readings: dict, key: str) -> list[float]:
    return [v[key] for n, v in readings.items() if not n.startswith("fake:") and v[key] is not None]


def arm_summary(rows: list[dict], readings_key: str, expected: dict) -> dict:
    """1 つの腕（軸の出所 × 読み手）の要約。"""
    hits, contra, invalid, silent, deltas, ps_all = {}, [], [], [], [], {}
    for r in rows:
        real = {n: v for n, v in r[readings_key].items() if not n.startswith("fake:")}
        ps = [v["p"] for v in real.values()
              if v["p"] is not None and v["label"] not in ("NO_EVIDENCE", "INSTRUMENT_FAULT")]
        hits[r["id"]] = hit(expected.get(r["id"]), ps, [v["label"] for v in real.values()])
        ps_all[r["id"]] = ps
        contra += rates(real, "contradiction_rate")
        invalid += rates(real, "invalid_rate")
        silent += rates(real, "silent_rate")
        if len(ps) == 2:
            deltas.append(abs(ps[0] - ps[1]))
    n_hit, n = scored_hits(hits)
    return {"hits": hits, "n_hit": n_hit, "n": n, "ps": ps_all,
            "contradiction": statistics.median(contra) if contra else None,
            "contradiction_max": max(contra) if contra else None,
            "invalid": statistics.median(invalid) if invalid else None,
            "silent": statistics.median(silent) if silent else None,
            "delta": statistics.median(deltas) if deltas else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--w5", default=None, help="W5（強い軸 × 弱い読み手）の走行ディレクトリ")
    ap.add_argument("--c2", default=None, help="腕 C を標本 2 で走らせたディレクトリ（受入 2 回目 C-1）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    data = json.loads((Path(args.run) / "single_model.json").read_text(encoding="utf-8"))
    rows = [r for r in data.values() if "readings" in r]
    expected = {r["id"]: r.get("expected_lean") for r in rows}
    L = ["# 単一モデル運用の測定（弱いモデル 1 体）— 判断規則どおりの読み（第 2 版）\n",
         f"題材 {len(rows)}（想定の照合は除外 {sorted(EXCLUDE)} を除く {len([r for r in rows if r['id'] not in EXCLUDE])}）。",
         "第 1 版の読みは受入で不合格（critical 4）。生成器と読み手を分ける腕を足して書き直した。\n",
         "## この報告の言葉（統計量の定義）\n",
         "- **想定一致**: 題材に付けた想定（support/refute/split/none）と判定の照合。"
         "support は 平均 p > 0.6、refute は < 0.4、split は 0.3〜0.7、none は全読み手の札が「本文に根拠が無い」か「計器不良」",
         "- **Δ**: 読み手 2 体の p の差の絶対値（値を持った題材だけ）",
         "- **矛盾率**: 軸のうち、支持側と反証側の両方に証拠が出た割合（u4 / n）",
         "- **無効率・沈黙率**: 応答が型に入らなかった割合・両側とも証拠なしの割合",
         "- **弱**: 交差検証で「どちらの側も命題に効かない」と見た軸。**非排他**: 支持と反証が両立しうると見た軸",
         "- **適合率・再現率**: 他系統（クラウド 2 体）が外した軸を正とみなしたときの、自己交差検証の当たり方",
         "- **κ**: 偶然一致を引いた一致（(観測 − 偶然) / (1 − 偶然)）。印の付き方が飽和しているときは、"
         "生の一致率ではなくこちらを見る",
         "- ⚠ **「弱」が飽和するのは集計の規則そのもの**（受入 2 回目 m-4）: `l1.py` は検証役 2 体 × 2 側 × 2 問の"
         "最大 8 票の **OR** で「弱」を付ける（`is_weak |= \"NEITHER\" in codes`）。非排他は多数決なので飽和しない",
         "- 表の「—」は **値が無い**（札が「本文に根拠が無い」か「計器不良」）という意味。腕を走らせていない、"
         "ではない（受入 2 回目 m-3）\n"]

    # ---- 題材ごと
    L += ["## 題材ごと\n",
          "| 題材 | 想定 | p(a) | p(b) | 札(a) | 札(b) | Δ | 矛盾率(a/b) | 自己で外した | 雲で外した | 自己 弱 | 雲 弱 | 検出項目 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    hits, deltas, label_disagree, both_valued = {}, [], 0, 0
    self_flag, cloud_flag, self_weak, cloud_weak = {}, {}, {}, {}
    self_nonex, cloud_nonex, n_axes = {}, {}, 0
    det_fail = {}
    for r in sorted(rows, key=lambda x: x["id"]):
        real = {n: v for n, v in r["readings"].items() if not n.startswith("fake:")}
        ps = [v["p"] for v in real.values() if v["p"] is not None and v["label"] not in ("NO_EVIDENCE", "INSTRUMENT_FAULT")]
        labels = [v["label"] for v in real.values()]
        hits[r["id"]] = hit(r.get("expected_lean"), ps, labels)
        if len(ps) == 2:
            deltas.append(abs(ps[0] - ps[1]))
            both_valued += 1
        if len(set(labels)) > 1:
            label_disagree += 1
        sc, cc = r.get("self_crosscheck") or {}, r.get("cloud_crosscheck") or {}
        n_axes += len(r["axes"])
        for d, src, k in ((self_flag, sc, "flagged"), (cloud_flag, cc, "flagged"), (self_weak, sc, "weak"),
                          (cloud_weak, cc, "weak"), (self_nonex, sc, "nonexclusive"), (cloud_nonex, cc, "nonexclusive")):
            d[r["id"]] = set(src.get(k) or [])
        bad = []
        for rd, a in r.get("detection", {}).get("answers", {}).items():
            if a.get("support", {}).get("verdict") != "STATES" or not a.get("support", {}).get("valid"):
                bad.append(f"{rd}: ある記述を取りこぼし")
            if a.get("refute", {}).get("verdict") == "STATES":
                bad.append(f"{rd}: 無い記述を述べているとした")
        det_fail[r["id"]] = bad
        v = list(real.values())
        f = lambda x: "—" if x is None else f"{x:.2f}"  # noqa: E731
        L.append(f"| {r['id']} | {r.get('expected_lean')} | {f(v[0]['p'])} | {f(v[1]['p'])} | {v[0]['label']} | {v[1]['label']} "
                 f"| {f(r['delta'])} | {f(v[0]['contradiction_rate'])}/{f(v[1]['contradiction_rate'])} "
                 f"| {sorted(self_flag[r['id']]) or 'なし'} | {sorted(cloud_flag[r['id']]) or 'なし'} "
                 f"| {len(self_weak[r['id']])} | {len(cloud_weak[r['id']])} | {'✗ ' + '; '.join(bad) if bad else '✓'} |")

    n_hit, n_scored = scored_hits(hits)
    per_material, per_reader = [], []
    for r in rows:
        got = [v["p"] for k, v in r["readings"].items() if not k.startswith("fake:") and v["p"] is not None]
        per_reader += got
        if got:
            per_material.append(statistics.mean(got))
    L.append(f"\n- **想定に合った題材: {n_hit}/{n_scored}**（クラウド多系統の基準は {BASELINE_HIT}/18。"
             f"母数を揃えた言い方でだけ比べる。受入 M10）")
    L.append(f"- p の中央値（受入 2 回目 m-2: どの数え方かを書く）: 題材ごとの平均 p で "
             f"**{statistics.median(per_material):.3f}**・読み手ごとの p で **{statistics.median(per_reader):.3f}**")

    # ---- R-a
    drop = BASELINE_HIT - n_hit
    verdict_a = ("弱い単一モデルは判定を歪める" if drop >= 3 else
                 "弱さ由来の揺れ（結論にしない）" if drop >= 1 else "基準と同等")
    L += ["\n## R-a 判定の歪み\n",
          f"- 想定一致 {n_hit}/{n_scored} 対 基準 {BASELINE_HIT}/18 → 差 {drop} 件 → **{verdict_a}**（規則: 3 件以上落ちたら歪むと読む）"]

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

    # ---- R-d（M3・C3 を受けて書き直し）
    n_self = sum(len(v) for v in self_flag.values())
    n_cloud = sum(len(v) for v in cloud_flag.values())
    tp = sum(len(self_flag[i] & cloud_flag[i]) for i in self_flag)
    prec = tp / n_self if n_self else None
    rec = tp / n_cloud if n_cloud else None
    js = [jaccard(self_flag[i], cloud_flag[i]) for i in self_flag if (self_flag[i] or cloud_flag[i])]
    mean_j = statistics.mean(js) if js else None
    verdict_d = ("自己交差検証で代替できる" if rec is not None and rec >= 0.5 and (prec or 0) >= 0.5 else
                 "検証役は別系統が要る（自己は拾ったものは正しいが、取りこぼす）" if rec is not None else
                 "外れた軸が両方 0 件（比べられない）")
    # 弱・非排他は軸ごとに数える（題材ごとの Jaccard は飽和して意味を失う。受入 C3）
    def per_axis(sd, cd):
        a = c = s = n = 0
        for r in rows:
            for ax in r["axes"]:
                n += 1
                si, ci = ax["id"] in sd[r["id"]], ax["id"] in cd[r["id"]]
                s += si
                c += ci
                a += (si == ci)
        return {"self": s / n, "cloud": c / n, "agree": a / n, "n": n,
                "kappa": kappa(a / n, s / n, c / n)}
    w = per_axis(self_weak, cloud_weak)
    x = per_axis(self_nonex, cloud_nonex)
    L += ["\n## R-d 自己交差検証と他系統の検証役\n",
          f"- 外した軸: 自己 {n_self} 軸・クラウド {n_cloud} 軸（題材では 自己 {sum(1 for v in self_flag.values() if v)}・"
          f"雲 {sum(1 for v in cloud_flag.values() if v)}）",
          f"- **雲を正としたときの自己: 適合率 {prec if prec is None else round(prec, 2)}・"
          f"再現率 {rec if rec is None else round(rec, 2)}**（一致した軸 {tp}）→ **{verdict_d}**",
          f"- 参考: 外した軸の Jaccard 平均 {mean_j if mean_j is None else round(mean_j, 2)}"
          "（受入 M3: 集合の一致では「拾いすぎ」と「取りこぼし」が区別できないので、上の 2 つで読む）",
          f"- 「弱」と見た軸（{w['n']} 軸中）: 自己 {w['self']:.3f}・雲 {w['cloud']:.3f}・一致 {w['agree']:.2f}・"
          f"**κ {w['kappa']:.2f}**（受入 C3: 弱の付き方が飽和しているので、生の一致率は読まない）",
          f"- ⚠ 弱率は門にならない: 同じ雲の検証役は、**強い生成器の軸でも {STRONG_WEAK:.3f} を「弱」と見た**。"
          "弱率が高いことは弱い生成器の印ではない（受入 C3・M4 を受けて、上流 §13 の候補から外す）",
          f"- 「非排他」と見た軸: 自己 {x['self']:.3f}（{round(x['self'] * x['n'])} 軸）・雲 {x['cloud']:.3f}"
          f"（{round(x['cloud'] * x['n'])} 軸）・κ {x['kappa']:.2f}。"
          f"強い生成器の軸は {STRONG_NONEXCLUSIVE:.3f}（同じ雲の検証役）→ **同じ検証役で比べると "
          f"{x['cloud'] / STRONG_NONEXCLUSIVE:.1f} 倍**（受入 1 回目 M4・2 回目 M-2: 系統を混ぜない。"
          f"弱い自己の検証役で測ると {x['self'] / STRONG_NONEXCLUSIVE:.1f} 倍だが、分子と分母で検証役が変わる）。"
          "⚠ 「弱い検証役 × 強い生成器の軸」の升目は未測定"]

    # ---- 付録: 他系統の検証役を借りたら判定は直るか（LLM は呼ばない。同じ回答を引き直す）
    sys.path.insert(0, str(REPO))
    from structural_distillation import l3                                  # noqa: E402
    from structural_distillation.contracts import AnswerMatrix, Thresholds  # noqa: E402
    recs = [json.loads(x) for x in (Path(args.run) / "records.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    by_axes = {}
    for r_ in recs:
        key = tuple(a["claim_support"] for a in r_["question_set"]["axes"])
        if len(key) > 1:                       # 検出項目（1 軸）の記録は除く
            by_axes[key] = r_
    borrowed = {}
    for r in rows:
        r_ = by_axes.get(tuple(a["support"] for a in r["axes"]))
        if not r_:
            continue
        keep = [a["id"] for a in r["axes"] if a["id"] not in cloud_flag[r["id"]]]
        ps, labels = [], []
        for mx in r_["matrices"]:
            if mx["reader"].startswith("fake:"):
                continue
            reading = l3.aggregate(AnswerMatrix.from_dict(mx), keep, Thresholds())
            labels.append(reading.label.value)
            if reading.p is not None and reading.label not in ("NO_EVIDENCE", "INSTRUMENT_FAULT"):
                ps.append(reading.p)
        borrowed[r["id"]] = hit(r.get("expected_lean"), ps, labels)
    b_hit, b_n = scored_hits(borrowed)
    L += ["\n## 付録 1: 他系統の検証役を借りたら（同じ回答を、クラウドが外した軸を抜いて引き直す）\n",
          f"- 想定一致 {b_hit}/{b_n}（自己のまま {n_hit}/{n_scored}・クラウド多系統の基準 {BASELINE_HIT}/18）",
          f"- 直った題材: {sorted(i for i in borrowed if borrowed[i] and not hits[i]) or 'なし'}"
          f"・壊れた題材: {sorted(i for i in borrowed if not borrowed[i] and hits[i]) or 'なし'}"]

    # ---- R-g: 生成器と読み手を分ける（W5。受入 C1 を受けて足した規則）
    if args.w5:
        w5 = json.loads((Path(args.w5) / "w5.json").read_text(encoding="utf-8"))
        w5rows = [r for r in w5.values() if "weak_readers_s1" in r]
        exp5 = {r["id"]: r.get("expected_lean") for r in w5rows}
        B1 = arm_summary(w5rows, "weak_readers_s1", exp5)      # 強い軸 × 弱い読み手（標本 1）
        B2 = arm_summary(w5rows, "weak_readers_s2", exp5)      # 強い軸 × 弱い読み手（標本 2）
        A1 = arm_summary(w5rows, "strong_readers_s1", exp5)    # 強い軸 × 強い読み手（標本 1）
        C1 = {"n_hit": n_hit, "n": n_scored,
              "contradiction": statistics.median([v for r in rows for v in rates(
                  {n: v for n, v in r["readings"].items() if not n.startswith("fake:")}, "contradiction_rate")]),
              "delta": med}
        # 腕 C を標本 2 でも走らせてある（受入 2 回目 C-1: 標本数で結論が反転しないかを見る）
        C2 = None
        if args.c2:
            c2rows = [r for r in json.loads((Path(args.c2) / "single_model.json").read_text(encoding="utf-8")).values()
                      if "readings" in r]
            C2 = arm_summary(c2rows, "readings", {r["id"]: r.get("expected_lean") for r in c2rows})
        L += ["\n## R-g 生成器と読み手の切り分け（受入 C1 の腕）\n",
              "同じ題材・同じ判断規則で、軸の出所と読み手を入れ替える。標本 1 で揃えた表が下。"
              "標本 2 でも全部の腕を埋めてある（受入 2 回目 C-1。下の「標本を変えると」）。\n",
              "⚠ 腕の間で揃っていないものが 3 つある（受入 2 回目 M-1）。いずれも数字を動かさないことを確かめた:\n",
              "- **閾値**: C は既定（κ=0.667）、A・B は測定時（κ=0.5）で札が付いている。"
              "両方の閾値で数え直しても 4 つの数字は動かない（`w5/threshold_sweep.py`・実呼び出し 0）",
              "- **交差検証**: C は自己交差検証あり（4 軸を外した）、A・B は無し。"
              "A・B の軸はクラウドの検証役が外した軸 0 本（`out4/xc2`）なので、有無で残る軸は変わらない",
              "- **偽読み手**: C には 3 体同居している。偽読み手は要約からも想定一致の数えからも除かれる\n",
              "| 腕 | 軸の出所 | 読み手 | 想定一致 | 矛盾率の中央値 | 無効率 | 沈黙率 | Δ の中央値 |",
              "|---|---|---|---|---|---|---|---|",
              f"| A | 強い生成器 | 強い 2 体（雲） | {A1['n_hit']}/{A1['n']} | {A1['contradiction']:.3f} | "
              f"{A1['invalid']:.3f} | {A1['silent']:.3f} | {'—' if A1['delta'] is None else round(A1['delta'], 3)} |",
              f"| B | 強い生成器 | 弱い 2 体（同じ 4B） | {B1['n_hit']}/{B1['n']} | {B1['contradiction']:.3f} | "
              f"{B1['invalid']:.3f} | {B1['silent']:.3f} | {'—' if B1['delta'] is None else round(B1['delta'], 3)} |",
              f"| C | 弱い生成器（自己交差検証） | 弱い 2 体（同じ 4B） | {C1['n_hit']}/{C1['n']} | "
              f"{C1['contradiction']:.3f} | — | — | {'—' if C1['delta'] is None else round(C1['delta'], 3)} |",
              "",
              f"- 生成器の寄与（B − C・読み手は同じ）: 想定一致 {B1['n_hit'] - C1['n_hit']:+d} 件"
              f"・矛盾率 {B1['contradiction'] - C1['contradiction']:+.3f}",
              f"- 読み手の寄与（B − A・軸は同じ）: 想定一致 {B1['n_hit'] - A1['n_hit']:+d} 件"
              f"・矛盾率 {B1['contradiction'] - A1['contradiction']:+.3f}",
              f"- 参考: B を標本 2 で読むと 想定一致 {B2['n_hit']}/{B2['n']}・矛盾率 {B2['contradiction']:.3f}"
              f"・Δ {'—' if B2['delta'] is None else round(B2['delta'], 3)}"]
        def verdict(gen, rdr):
            return ("生成器を強くすれば直る（壊れているのは生成器）" if gen >= 3 and rdr < 3 else
                    "読み手も壊れている（生成器だけでは足りない）" if rdr >= 3 and gen < 3 else
                    "両方が効いている" if gen >= 3 and rdr >= 3 else
                    "この題材数では分けられない（結論にしない）")
        gen, rdr = B1["n_hit"] - C1["n_hit"], A1["n_hit"] - B1["n_hit"]
        L.append(f"- → 標本 1 では **{verdict(gen, rdr)}**（規則 R-g: 3 件以上の差を効いたと読む。R-a と同じ閾値）")
        if C2 is not None:
            # 標本 2 の A は記録から引き直してある（threshold_sweep.py。16/18・実呼び出し 0）
            A2 = 16
            gen2, rdr2 = B2["n_hit"] - C2["n_hit"], A2 - B2["n_hit"]
            L += ["\n### 標本を変えると（受入 2 回目 C-1）\n",
                  "| 標本 | A 強い軸×強い読み手 | B 強い軸×弱い読み手 | C 弱い軸×弱い読み手 | 生成器の寄与 B−C | 読み手の寄与 A−B | R-g の読み |",
                  "|---|---|---|---|---|---|---|",
                  f"| 1 | {A1['n_hit']}/{A1['n']} | {B1['n_hit']}/{B1['n']} | {C1['n_hit']}/{C1['n']} | "
                  f"{gen:+d} | {rdr:+d} | {verdict(gen, rdr)} |",
                  f"| 2 | {A2}/18 | {B2['n_hit']}/{B2['n']} | {C2['n_hit']}/{C2['n']} | {gen2:+d} | {rdr2:+d} | "
                  f"{verdict(gen2, rdr2)} |",
                  "",
                  f"- 数が動くのは腕 B だけ（A は標本 1 でも 2 でも 16/18、C は {C1['n_hit']}/{C1['n']} と "
                  f"{C2['n_hit']}/{C2['n']}）。⚠ ただし **C も中身は 4 題材入れ替わっている**"
                  "（m03・m07 が外れ、m08・m20 が当たる。数が同じなのは相殺の偶然）",
                  "- B で反転した 4 題材（m04・m06・m14・m18）のうち 3 件は判定の境目だが、**m18 は境目ではない**: "
                  "標本 2 では両方の読み手が「本文に根拠が無い」（有効率 0.33・0.17）になり、平均 p が存在しない",
                  f"- **生成器の寄与は標本によらず閾値を超える（{gen:+d}・{gen2:+d}）。読み手の寄与は閾値の下（{rdr:+d}）か"
                  f"ちょうど（{rdr2:+d}）**。したがって「主に生成器が壊れている」とは言えるが、"
                  "「読み手は関係ない」とは言えない"]
        L += ["\n### 題材ごと（A/B/C の p）\n",
              "| 題材 | 想定 | A 強い軸×強い読み手 | B 強い軸×弱い読み手 | C 弱い軸×弱い読み手 | A/B/C の当たり |",
              "|---|---|---|---|---|---|"]
        for r in sorted(w5rows, key=lambda x: x["id"]):
            i = r["id"]
            g = lambda arm: "—" if not arm["ps"].get(i) else "/".join(f"{x:.2f}" for x in arm["ps"][i])  # noqa: E731
            mark = lambda h: "—" if h is None else ("✓" if h else "✗")  # noqa: E731
            c_ps = [v["p"] for n_, v in data[i]["readings"].items()
                    if not n_.startswith("fake:") and v["p"] is not None] if i in data else []
            L.append(f"| {i} | {exp5[i]} | {g(A1)} | {g(B1)} | {'/'.join(f'{x:.2f}' for x in c_ps) or '—'} "
                     f"| {mark(A1['hits'][i])}{mark(B1['hits'][i])}{mark(hits.get(i))} |")

    # ---- 参考: 偽読み手・費用・時間
    fakes = {}
    for r in rows:
        for n_, v in r["readings"].items():
            if n_.startswith("fake:"):
                fakes.setdefault(n_, []).append((v["label"], v["reason"]))
    L += ["\n## 参考\n"]
    for n_, vs in fakes.items():
        kinds = {}
        for lb, rs in vs:
            kinds[f"{lb}/{rs}"] = kinds.get(f"{lb}/{rs}", 0) + 1
        L.append(f"- 偽読み手 {n_}: {kinds}")
    L.append(f"- 判定だけの時間（検出項目とクラウド交差検証を含まない。受入 m6）: 合計 {sum(r['seconds'] for r in rows)/60:.0f} 分"
             f"（題材あたり 中央値 {statistics.median([r['seconds'] for r in rows]):.0f} 秒）")
    L.append(f"- 呼び出し（ローカル・判定の分だけ）: 生成 {sum(r['cost']['plan'] for r in rows)}・"
             f"交差検証 {sum(r['cost']['crosscheck'] for r in rows)}・回答 {sum(r['cost']['answer'] for r in rows)}")
    for name, path in (("ローカル", Path(args.run) / "local_cache.jsonl"), ("クラウド", Path(args.run) / "cloud_cache.jsonl")):
        if path.exists():
            L.append(f"- キャッシュの行数（{name}・検出項目とクラウド交差検証を含む全部）: "
                     f"{sum(1 for x in path.read_text(encoding='utf-8').splitlines() if x.strip())}")
    if args.w5:
        p5 = Path(args.w5) / "local_cache.jsonl"
        if p5.exists():
            L.append(f"- W5 の呼び出し（ローカル）: {sum(1 for x in p5.read_text(encoding='utf-8').splitlines() if x.strip())}")
    text = "\n".join(L) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
