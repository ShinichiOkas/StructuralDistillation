"""測定 2 周目の報告係。各腕の記録に合意 §判断規則（v4）をそのまま当てる。無い腕は「未取得」と書く。

  python probe4_report.py --root <腕の親ディレクトリ> --out3 out3/results.json --out report.md
"""
from __future__ import annotations

import argparse
import json
import statistics
from itertools import combinations
from pathlib import Path

import probe as P
import probe4_thresholds as T

DELTA_CONFOUND = 0.05
IMPROVEMENT_MARGIN = 2


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def real(readers: dict) -> dict:
    return {rd: agg for rd, agg in readers.items() if not rd.startswith("fake:")}


def hit(expected: str, ps: list[float], labels: list[str]) -> bool:
    if expected == "none":
        return bool(labels) and all(lb == "本文に根拠が無い" for lb in labels)
    if not ps:
        return False
    pm = statistics.mean(ps)
    return {"support": pm > 0.6, "refute": pm < 0.4, "split": 0.3 <= pm <= 0.7}.get(expected, False)


def s0a(results: list[dict]) -> tuple[int, int]:
    """記録の per_axis から A を再計算して一致するか（集約の決定性）。"""
    ok = n = 0
    for r in results:
        for agg in r["readers"].values():
            n += 1
            dirs = [x["d"] for x in agg["per_axis"]]
            s, rr = sum(1 for d in dirs if d == 1), sum(1 for d in dirs if d == -1)
            ok += int(s == agg["A"]["s"] and rr == agg["A"]["r"])
    return ok, n


def cf_follow(results: list[dict], only_single_fact: set | None = None) -> tuple[int, int]:
    ok = n = 0
    for r in results:
        cf = r.get("counterfactual")
        if not cf or (only_single_fact is not None and r["id"] not in only_single_fact):
            continue
        for _, c in cf["readers"].items():
            po, pc = c["p_orig"], c["p_cf"]
            if po is None or pc is None:
                continue
            n += 1
            ok += int((pc > po) if cf["expected_change"] == "support_up" else (pc < po))
    return ok, n


def levels3(p: float | None) -> str:
    if p is None:
        return "—"
    schemes = {
        "K 等分": [0.2, 0.4, 0.6, 0.8],
        "中央を広く": [0.1, 0.35, 0.65, 0.9],
        "外側を広く": [0.3, 0.45, 0.55, 0.7],
    }
    out = []
    for name, b in schemes.items():
        lv = 1 + sum(1 for x in b if p >= x)
        out.append(f"{name}={lv}")
    return " / ".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out3", default=str(P.HERE / "out3" / "results.json"))
    ap.add_argument("--materials", nargs="+", default=[str(P.HERE / "materials.json"), str(P.HERE / "materials2.json"), str(P.HERE / "materials_rashomon.json")])
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    root = Path(args.root)
    expected = T.load_expected(args.materials)
    kinds = {}
    for path in args.materials:
        for m in json.loads(Path(path).read_text(encoding="utf-8"))["materials"]:
            kinds[m["id"]] = m.get("kind") or ("evaluative" if m["id"] in ("m01", "m04", "m07", "m09", "r01", "r02") else "other")
    single_fact = {f"m{i:02d}" for i in range(1, 11)} | {"m01b", "m12"}

    base = load(root / "base21" / "results.json")
    m3 = load(root / "m3arm" / "results.json")
    meta2 = load(root / "meta2" / "results.json")
    s4 = load(root / "s4" / "results.json")
    rash = load(root / "rashomon" / "results.json")
    mem = load(root / "mem" / "results.json")
    mono = load(root / "mono" / "results.json")
    mono_r = load(root / "mono_rashomon" / "results.json")
    xc2 = load(root / "xc2" / "crosscheck.json")
    xcs = load(root / "xc_self" / "crosscheck.json")
    p3 = load(Path(args.out3))
    L = ["# 測定 2 周目 — 判断規則に照らした読み（自動生成。手で書き換えない）\n"]

    # ---- ゼロ点（S0a）
    L.append("## ゼロ点（S0a: 記録の per_axis から A を再計算して一致するか）\n")
    for name, res in (("base21", base), ("m3arm", m3), ("meta2", meta2), ("s4", s4), ("rashomon", rash), ("mem", mem), ("mono", mono), ("mono_rashomon", mono_r)):
        if res:
            ok, n = s0a(res)
            L.append(f"- {name}: {ok}/{n} 一致")
        else:
            L.append(f"- {name}: 未取得")

    # ---- M4 閾値
    L.append("\n## M4 閾値（基準走行 base21。標本内ばらつきは s4 を母数に足す）\n")
    values = dict(T.FALLBACK)
    if base:
        extra = [str(root / "s4" / "results.json")] if s4 else []
        lines, values = T.compute([str(root / "base21" / "results.json")], args.materials, None, extra)
        L += lines
        L.append(f"\n採る値: ι={values['iota']} κ={values['kappa']:.3f} ρ={values['rho']:.3f} ω={values['omega']:.3f} Δ={values['delta']:.3f}（仮置きのままの量は FALLBACK の値）")
    else:
        L.append("未取得")
    delta_thr = values["delta"]

    # ---- 基準走行の想定一致（腕ごと・本文についての命題は別表）
    def hit_table(res, title, exclude_meta=True):
        L.append(f"\n## {title}\n")
        L.append("| 題材 | 型 | 想定 | 読み手ごとの p | 札 | 判定 |")
        L.append("|---|---|---|---|---|---|")
        n = h = 0
        for r in res:
            k = kinds.get(r["id"], "other")
            if exclude_meta and k == "meta":
                continue
            rr = real(r["readers"])
            ps = [a["A"]["p"] for a in rr.values() if a["A"]["p"] is not None]
            labels = [a["label"] for a in rr.values()]
            ok = hit(expected.get(r["id"]), ps, labels)
            n += 1
            h += int(ok)
            L.append(f"| {r['id']} | {k} | {expected.get(r['id'])} | {' / '.join(P.fmt(a['A']['p']) for a in rr.values())} | {' / '.join(labels)} | {'✓' if ok else '✗'} |")
        L.append(f"\n- 想定に合った題材: {h}/{n}（本文についての命題は除く）")
        return h, n

    if base:
        hit_table(base, "基準走行（クラウド 2 家系）の想定一致")

    # ---- M3 読み手だけ替える腕（p3 の軸）
    L.append("\n## M3 強い読み手（p3 の軸をそのまま、読み手だけクラウド 3 家系）\n")
    if m3 and p3:
        readers = [rd for rd in real(m3[0]["readers"])]
        pair_d = {}
        within = []
        for r in m3:
            rr = real(r["readers"])
            for a in rr.values():
                pbs = [p for p in a["p_by_sample"] if p is not None]
                if len(pbs) >= 2:
                    within.append(max(pbs) - min(pbs))
            for x, y in combinations(readers, 2):
                px, py = rr[x]["A"]["p"], rr[y]["A"]["p"]
                if px is not None and py is not None:
                    pair_d.setdefault((x, y), []).append(abs(px - py))
        L.append("| 対 | Δ 平均 | Δ 最大 | n |")
        L.append("|---|---|---|---|")
        for (x, y), ds in pair_d.items():
            L.append(f"| {x} × {y} | {statistics.mean(ds):.3f} | {max(ds):.3f} | {len(ds)} |")
        contra = {rd: statistics.mean(r["readers"][rd]["contradiction_rate"] for r in m3) for rd in readers}
        L.append("\n矛盾率: " + ", ".join(f"{rd} {v:.3f}" for rd, v in contra.items()))
        h, n = 0, 0
        for r in m3:
            rr = real(r["readers"])
            ps = [a["A"]["p"] for a in rr.values() if a["A"]["p"] is not None]
            ok = hit(expected.get(r["id"]), ps, [a["label"] for a in rr.values()])
            n += 1
            h += int(ok)
        L.append(f"想定一致（3 読み手の平均 p）: {h}/{n}。反事実の追従: {'/'.join(map(str, cf_follow(m3, single_fact)))}")
        # p3 の弱い読み手
        p3_pairs = []
        for r in p3:
            rr = real(r["readers"])
            ps = [a["A"]["p"] for a in rr.values() if a["A"]["p"] is not None]
            if len(ps) >= 2:
                p3_pairs.append(max(ps) - min(ps))
        p3_contra = {rd: statistics.mean(r["readers"][rd]["contradiction_rate"] for r in p3 if rd in r["readers"]) for rd in ("qwen3.5:4b", "gemma3:4b")}
        L.append(f"参照点（p3・弱い 2 体・同じ軸）: Δ 平均 {statistics.mean(p3_pairs):.3f}、矛盾率 {', '.join(f'{k} {v:.3f}' for k, v in p3_contra.items())}")
        w95 = T.q(within, .95) or delta_thr
        nog = [ds for (x, y), ds in pair_d.items() if "gemma4" not in x and "gemma4" not in y]
        withg = [ds for (x, y), ds in pair_d.items() if "gemma4" in x or "gemma4" in y]
        d_nog = statistics.mean(sum(nog, [])) if nog else None
        d_withg = statistics.mean(sum(withg, [])) if withg else None
        confound = (d_nog is not None and d_withg is not None and abs(d_withg - d_nog) >= DELTA_CONFOUND)
        d_use = d_nog if d_nog is not None else d_withg
        band = "弱さ由来" if d_use <= w95 else ("中間" if d_use < 2 * w95 else "仕組み由来")
        L.append(f"\n規則: 標本内 95% 分位 {w95:.3f}。生成器と同家系（gemma4）を含む対 Δ {P.fmt(d_withg)} vs 含まない対 Δ {P.fmt(d_nog)} → "
                 f"{'交絡あり（含まない対を採る）' if confound else '交絡なし'}。採る Δ {d_use:.3f} → **{band}**（弱い 2 体の Δ {statistics.mean(p3_pairs):.3f} から）")
    else:
        L.append("未取得")

    # ---- M3b 単一系統
    L.append("\n## M3b 単一系統（gemma4:31b-cloud を生成器・読み手・検証役に）\n")
    if mono and base:
        bmap = {r["id"]: r for r in base}
        diffs, rows = [], []
        for r in mono:
            g = real(r["readers"])
            if not g:
                continue
            pm = next(iter(g.values()))["A"]["p"]
            b = bmap.get(r["id"])
            if not b or pm is None:
                continue
            for rd, a in real(b["readers"]).items():
                if a["A"]["p"] is not None:
                    diffs.append(abs(pm - a["A"]["p"]))
                    rows.append((r["id"], rd, pm, a["A"]["p"]))
        h = n = 0
        for r in mono:
            g = real(r["readers"])
            ps = [a["A"]["p"] for a in g.values() if a["A"]["p"] is not None]
            if kinds.get(r["id"]) == "meta":
                continue
            n += 1
            h += int(hit(expected.get(r["id"]), ps, [a["label"] for a in g.values()]))
        contra = statistics.mean(next(iter(real(r["readers"]).values()))["contradiction_rate"] for r in mono if real(r["readers"]))
        silent = statistics.mean(next(iter(real(r["readers"]).values()))["silent_rate"] for r in mono if real(r["readers"]))
        med = statistics.median(diffs) if diffs else None
        L.append(f"- 単一系統 vs 多系統の |p 差|: 中央値 {P.fmt(med)}、最大 {P.fmt(max(diffs) if diffs else None)}（n={len(diffs)}）。Δ の閾値 {delta_thr:.3f}")
        L.append(f"- 単一系統の想定一致 {h}/{n}、矛盾率 {contra:.3f}、沈黙率 {silent:.3f}、反事実の追従 {'/'.join(map(str, cf_follow(mono, single_fact)))}")
        big = [(m, rd, pm, pb) for m, rd, pm, pb in rows if abs(pm - pb) > delta_thr]
        if big:
            L.append("- 閾値を超えた題材: " + ", ".join(f"{m}({kinds.get(m)}) gemma4 {P.fmt(pm)} vs {rd} {P.fmt(pb)}" for m, rd, pm, pb in big))
        verdict1 = "判定は単一系統でも偏らない" if (med is not None and med <= delta_thr) else "判定が偏る（差の出た題材を上に列挙）"
        L.append(f"- 規則 ⑴ → **{verdict1}**")
        if xc2 and xcs:
            n2 = sum(len(row["flagged_agree"]) for row in xc2)
            ns = sum(len(row["flagged_agree"]) for row in xcs)
            L.append(f"- 検出の偏り: 他系統の検証役が flag した軸 {n2} vs gemma4 自身が flag した軸 {ns}（同じ軸集合）")
            verdict2 = "単一系統は自分の向きの誤りを見つけにくい（検出の偏り）" if n2 - ns >= 2 else "検出も偏らない"
            L.append(f"- 規則 ⑵ → **{verdict2}**（⚠ 差の軸に反事実で逆に動いた軸が含まれるかは summary_crosscheck.md で目視）")
        else:
            L.append("- 検出の偏り: 未取得（xc2 / xc_self）")
    else:
        L.append("未取得")

    # ---- M1 交差検証（要約ファイルをそのまま）
    L.append("\n## M1 向きの交差検証（他系統の検証役）\n")
    sc = root / "xc2" / "summary_crosscheck.md"
    if sc.exists():
        txt = sc.read_text(encoding="utf-8")
        for key in ("## 型ごとの食い違い率", "## 較正", "## 対の排他性", "## 札の変化", "## 反事実の追従"):
            i = txt.find(key)
            if i >= 0:
                j = txt.find("\n## ", i + 1)
                L.append(txt[i:j if j > 0 else None].strip())
                L.append("")
    else:
        L.append("未取得")

    # ---- M2 本文についての命題
    L.append("\n## M2 本文そのものについての命題（指示あり腕 meta2 vs 基準）\n")
    if meta2 and base:
        bmap = {r["id"]: r for r in base}
        L.append("| 題材 | 型 | 読み手 | p 基準→指示あり | 沈黙率 基準→指示あり | 札 基準→指示あり | 反事実 基準→指示あり |")
        L.append("|---|---|---|---|---|---|---|")
        leak_ok = True
        nonmeta_dp, nonmeta_ds = [], []
        meta_cf_after = []
        for r in meta2:
            b = bmap.get(r["id"])
            for rd, a in real(r["readers"]).items():
                ba = b["readers"].get(rd) if b else None
                cfm = (r.get("counterfactual") or {}).get("readers", {}).get(rd)
                cfb = (b.get("counterfactual") or {}).get("readers", {}).get(rd) if b else None

                def okc(c, exp):
                    if not c or c["p_orig"] is None or c["p_cf"] is None:
                        return "—"
                    return "✓" if ((c["p_cf"] > c["p_orig"]) if exp == "support_up" else (c["p_cf"] < c["p_orig"])) else "✗"
                exp = (r.get("counterfactual") or {}).get("expected_change")
                cfa, cfb_ = okc(cfm, exp), okc(cfb, exp)
                p_b = P.fmt(ba["A"]["p"]) if ba else "—"
                s_b = f"{ba['silent_rate']:.2f}" if ba else "—"
                l_b = ba["label"] if ba else "—"
                L.append(f"| {r['id']} | {kinds.get(r['id'])} | {rd} | {p_b}→{P.fmt(a['A']['p'])} | {s_b}→{a['silent_rate']:.2f} "
                         f"| {l_b}→{a['label']} | {cfb_}→{cfa} |")
                k = kinds.get(r["id"])
                if k == "meta" and cfa != "—":
                    meta_cf_after.append(cfa == "✓")
                if k == "none" and a["label"] != "本文に根拠が無い":
                    leak_ok = False
                if k not in ("meta", "none") and ba and a["A"]["p"] is not None and ba["A"]["p"] is not None:
                    nonmeta_dp.append(abs(a["A"]["p"] - ba["A"]["p"]))
                    nonmeta_ds.append(abs(a["silent_rate"] - ba["silent_rate"]))
        fakes_ok = all(r["readers"].get("fake:all_yes", {}).get("label") in ("計器不良", "本文に根拠が無い") and
                       r["readers"].get("fake:all_undetermined", {}).get("label") == "本文に根拠が無い" for r in meta2 if "fake:all_yes" in r["readers"])
        c1 = bool(meta_cf_after) and all(meta_cf_after)
        c4 = (not nonmeta_dp) or (max(nonmeta_dp) <= delta_thr and max(nonmeta_ds) <= delta_thr)
        L.append(f"\n- ⑴ 本文についての命題の反事実（指示あり）: {sum(meta_cf_after)}/{len(meta_cf_after)} ⑵ 根拠なし題材が「本文に根拠が無い」のまま: {leak_ok} "
                 f"⑶ 偽読み手の出力が保たれる: {fakes_ok} ⑷ 本文についてでない題材の |Δp| max {P.fmt(max(nonmeta_dp) if nonmeta_dp else None)}・|Δ沈黙率| max {P.fmt(max(nonmeta_ds) if nonmeta_ds else None)}（閾値 {delta_thr:.3f}）")
        verdict = "採用（命題の型を利用側が宣言したときだけ ON。既定 OFF）" if (c1 and leak_ok and fakes_ok and c4) else "不採用"
        L.append(f"- 規則 → **{verdict}**")
    else:
        L.append("未取得")

    # ---- M5 羅生門
    L.append("\n## M5 羅生門\n")
    if rash:
        for r in rash:
            rr = real(r["readers"])
            changed = (r.get("counterfactual") or {}).get("changed", "—")
            L.append(f"### {r['id']}（反事実: {changed}）")
            for rd, a in rr.items():
                L.append(f"- {rd}: p={P.fmt(a['A']['p'])} w={P.fmt(a['A']['w'])} 札={a['label']} 矛盾率={a['contradiction_rate']:.2f} 沈黙率={a['silent_rate']:.2f}")
            ok, n = cf_follow([r])
            L.append(f"- 反事実の追従: {ok}/{n}（⚠ 本文に従って動くことしか示さない）")
        if mem:
            mmap = {r["id"]: r for r in mem}
            L.append("\n### 本文を渡さない腕（記憶だけ）との差")
            memory_dominates = False
            for r in rash:
                m = mmap.get(r["id"])
                if not m:
                    continue
                for rd, a in real(r["readers"]).items():
                    pm = m["readers"].get(rd, {}).get("A", {}).get("p")
                    pt = a["A"]["p"]
                    d = abs(pt - pm) if (pt is not None and pm is not None) else None
                    L.append(f"- {r['id']} {rd}: 本文あり p={P.fmt(pt)} / 記憶 p={P.fmt(pm)} / 差 {P.fmt(d)}")
                    if d is not None and d <= delta_thr:
                        memory_dominates = True
            follow_ok, follow_n = cf_follow(rash)
            if follow_n and follow_ok == 0:
                L.append("\n規則 → **値は報告しない**（反事実が両方 ✗）")
            elif memory_dominates:
                L.append("\n規則 → **記憶が支配している疑い**。段は出さない（本文あり p と記憶 p の差が Δ の閾値以内の読み手がある）")
            else:
                L.append("\n規則 → 値を報告する。段は境界 3 通りで併記（仮置き。K 等分に意味は無い）:")
                for r in rash:
                    for rd, a in real(r["readers"]).items():
                        L.append(f"- {r['id']} {rd}: p={P.fmt(a['A']['p'])} → 段 {levels3(a['A']['p'])}")
        if mono_r:
            L.append("\n### 単一系統（gemma4）での羅生門")
            for r in mono_r:
                for rd, a in real(r["readers"]).items():
                    L.append(f"- {r['id']} {rd}: p={P.fmt(a['A']['p'])} w={P.fmt(a['A']['w'])} 札={a['label']} 反事実 {'/'.join(map(str, cf_follow([r])))}")
    else:
        L.append("未取得")

    text = "\n".join(L) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
