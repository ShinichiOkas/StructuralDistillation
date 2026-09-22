"""M1 向きの交差検証（設計 H12・S13）— v2（協議エンジンの指摘 2・3・16・17・20 を反映）。

p3 形式の記録（results.json）にある軸について、生成器とは別の読み手（検証役）に本文を見せずに
「この記述が本文に述べられていたら、命題は支持されるか、反証されるか」を答えさせ、
生成器が付けた向きと食い違う軸を数える。

v2 で足したもの:
  - 票の集約規則を先に固定: 既定は「検証役の過半数が反対の向き」（agree）。任意の 1 票（or）は副表
  - 選択肢の並びを反転した版も取り、位置の偏りを見る（各検証役 × 2 版 → 版の多数決を検証役の票にする）
  - 対の排他性: 「この 2 つの記述は同じ本文に同時に述べられうるか」を検証役に聞き、矛盾率を「対由来」と「読み手由来」に割る材料にする
  - 較正: 検証役が生成器の向きに同意した軸の割合（＝向きを入れ替えた軸をこの規則が flag する割合と同じ量）
  - 対照腕: flag した軸と同数の flag していない軸を無作為に外して追従を再計算（外すだけで上がるなら H12 の効果ではない）
  - 前後比較は対応のある行だけ。外した後に p が未定義になった行は ✗（軸を全部捨てたのは改善ではない）
  - 外した後の札と段の変化数
追加の呼び出しは検証役への向きの質問（軸 × 2 側 × 検証役 × 2 版）と排他性の質問（軸 × 検証役）だけ。
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

import probe as P

VERSION = "xc2"
ORIENT_SCHEMA = {"type": "object", "properties": {"orientation": {"type": "string", "enum": ["支持", "反証", "どちらとも言えない"]}},
                 "required": ["orientation"]}
EXCL_SCHEMA = {"type": "object", "properties": {"compatible": {"type": "string", "enum": ["両立しうる", "両立しない"]}},
               "required": ["compatible"]}
IMPROVEMENT_MARGIN = 2  # 対応のある行で「✗→✓」が「✓→✗」より何行多ければ改善と読むか（測る前に固定）


def orient_prompt(proposition: str, claim: str, reversed_order: bool) -> list[dict]:
    opts = ["- 述べられていることで命題が正しい方向に傾くなら \"支持\"",
            "- 述べられていることで命題が誤りの方向に傾くなら \"反証\"",
            "- 命題の真偽に関係しないなら \"どちらとも言えない\""]
    if reversed_order:
        opts = opts[::-1]
    sys_msg = "あなたは、記述と命題の論理的な関係を判定する係です。本文は見ません。出力は指定の JSON だけを返してください。"
    user = f"""命題: 「{proposition}」

次の記述が、ある本文に述べられていたとします。
記述: 「{claim}」

この記述が本文に述べられていたら、命題は支持されますか、反証されますか。
{chr(10).join(opts)}
"""
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]


def excl_prompt(a: str, b: str) -> list[dict]:
    sys_msg = "あなたは、2 つの記述の論理的な関係を判定する係です。本文は見ません。出力は指定の JSON だけを返してください。"
    user = f"""記述 A: 「{a}」
記述 B: 「{b}」

同じ本文が、A と B の両方を同時に述べていることはありえますか。
- 両方が同時に成り立ちうるなら "両立しうる"
- 一方が成り立てば他方は成り立たないなら "両立しない"
"""
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]


def ask(port: P.Port, model: str, msgs: list[dict], schema: dict, key: str, sample: int = 0):
    r = port.chat(model, msgs, schema, sample=sample, version=VERSION)
    if not r["ok"]:
        return None
    try:
        return json.loads(r["content"])[key]
    except Exception:  # noqa: BLE001
        return None


def recount(per_axis: list[dict], keep: set[str], invalid_rate: float) -> dict:
    kept = [x for x in per_axis if x["id"] in keep]
    dirs = [x["d"] for x in kept]
    s = sum(1 for d in dirs if d == 1)
    r = sum(1 for d in dirs if d == -1)
    n = len(dirs)
    c = {"s": s, "r": r, "u": n - s - r, "n": n, "p": s / (s + r) if s + r else None, "w": 2 * min(s, r) / (s + r) if s + r else None}
    contra = statistics.mean(x["contradiction"] for x in kept) if kept else 0.0
    label = P.label(c, invalid_rate) if n else "本文に根拠が無い"
    if n and contra >= 0.5:
        label = "計器不良"
    return {**c, "contradiction_rate": contra, "label": label, "level": P.level5(c["p"])}


def cf_ok(exp: str, po, pc):
    if po is None or pc is None:
        return None
    return (pc > po) if exp == "support_up" else (pc < po)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", nargs="+", default=[str(P.HERE / "out3" / "results.json")])
    ap.add_argument("--materials", nargs="+", default=[str(P.HERE / "materials.json"), str(P.HERE / "materials2.json")])
    ap.add_argument("--checkers", nargs="*", default=["qwen3.5:9b", "qwen3.5:4b", "gemma3:4b"],
                    help="生成器（gemma4:12b）と別のモデル。先頭は読み手とも別のモデルにする")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache-only", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    port = P.Port(out / "llm_cache.jsonl", cache_only=args.cache_only)
    rng = random.Random(args.seed)
    results = []
    for path in args.results:
        results += json.loads(Path(path).read_text(encoding="utf-8"))
    kinds, single_fact = {}, {}
    for path in args.materials:
        for m in json.loads(Path(path).read_text(encoding="utf-8"))["materials"]:
            kinds[m["id"]] = m.get("kind") or ("evaluative" if m["id"] in ("m01", "m04", "m07", "m09") else "other")
            single_fact[m["id"]] = not m["id"].startswith("m1") or m["id"] == "m12"  # 新規 10 本は m12 以外「別本文」

    rows = []
    for r in results:
        axes = r["plan"]["axes"] or []
        if not axes:
            continue
        per_axis_votes = []
        flagged_agree, flagged_or, weak, nonexcl = set(), set(), set(), set()
        for a in axes:
            checker_votes = {}  # checker -> {side: 多数決の向き}
            any_opposite = False
            for c in args.checkers:
                cv = {}
                for side, claim, expect in (("support", a["claim_support"], "支持"), ("refute", a["claim_refute"], "反証")):
                    vs = [ask(port, c, orient_prompt(r["proposition"], claim, rev), ORIENT_SCHEMA, "orientation", sample=int(rev))
                          for rev in (False, True)]
                    vs = [v for v in vs if v is not None]
                    if any(v in ("支持", "反証") and v != expect for v in vs):
                        any_opposite = True
                    if any(v == "どちらとも言えない" for v in vs):
                        weak.add(a["id"])
                    maj = None
                    if vs:
                        cnt = {v: vs.count(v) for v in set(vs)}
                        top = max(cnt.values())
                        tops = [v for v, n in cnt.items() if n == top]
                        maj = tops[0] if len(tops) == 1 else "同数"
                    cv[side] = {"votes": vs, "majority": maj, "expect": expect}
                checker_votes[c] = cv
            # 検証役ごとの「反対」判定: どちらかの側で多数決が反対の向き
            opposite_by_checker = [any(cv[s]["majority"] in ("支持", "反証") and cv[s]["majority"] != cv[s]["expect"] for s in cv)
                                   for cv in checker_votes.values()]
            if sum(opposite_by_checker) * 2 > len(args.checkers):
                flagged_agree.add(a["id"])
            if any_opposite:
                flagged_or.add(a["id"])
            ex = [ask(port, c, excl_prompt(a["claim_support"], a["claim_refute"]), EXCL_SCHEMA, "compatible") for c in args.checkers]
            ex = [e for e in ex if e is not None]
            if ex and ex.count("両立しうる") * 2 > len(ex):
                nonexcl.add(a["id"])
            per_axis_votes.append({"id": a["id"], "axis": a["axis"], "checkers": checker_votes, "exclusivity": ex})

        all_ids = {a["id"] for a in axes}
        keep_agree = all_ids - flagged_agree
        # 対照腕: flag と同数の非 flag 軸を無作為に外す
        pool = sorted(all_ids - flagged_agree)
        k = min(len(flagged_agree), len(pool))
        control_removed = set(rng.sample(pool, k)) if k else set()
        keep_control = all_ids - control_removed
        row = {"id": r["id"], "kind": kinds.get(r["id"], "other"), "single_fact_cf": single_fact.get(r["id"], True),
               "proposition": r["proposition"], "n_axes": len(axes),
               "flagged_agree": sorted(flagged_agree), "flagged_or": sorted(flagged_or), "weak": sorted(weak),
               "nonexclusive": sorted(nonexcl), "control_removed": sorted(control_removed), "votes": per_axis_votes, "readers": {}}
        for rd, agg in r["readers"].items():
            if rd.startswith("fake:"):
                continue
            before = recount(agg["per_axis"], all_ids, agg["invalid_rate"])
            after = recount(agg["per_axis"], keep_agree, agg["invalid_rate"])
            ctrl = recount(agg["per_axis"], keep_control, agg["invalid_rate"])
            e = {"before": before, "after": after, "control": ctrl}
            cf = (r.get("counterfactual") or {}).get("readers", {}).get(rd)
            if cf:
                exp = r["counterfactual"]["expected_change"]
                cfb = recount(cf["per_axis"], all_ids, agg["invalid_rate"])
                cfa = recount(cf["per_axis"], keep_agree, agg["invalid_rate"])
                cfc = recount(cf["per_axis"], keep_control, agg["invalid_rate"])
                e["cf"] = {"before": cf_ok(exp, before["p"], cfb["p"]),
                           "after": cf_ok(exp, after["p"], cfa["p"]) if after["p"] is not None and cfa["p"] is not None else False,
                           "control": cf_ok(exp, ctrl["p"], cfc["p"]) if ctrl["p"] is not None and cfc["p"] is not None else False,
                           "p": {"before": (before["p"], cfb["p"]), "after": (after["p"], cfa["p"]), "control": (ctrl["p"], cfc["p"])}}
            row["readers"][rd] = e
        rows.append(row)
        print(f"[{r['id']}] {row['kind']:10s} 過半数で反対 {len(flagged_agree)}/{len(axes)}  1 票でも {len(flagged_or)}  非排他 {len(nonexcl)}", flush=True)

    (out / "crosscheck.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- 要約
    L = [f"# M1 向きの交差検証 v2（検証役 {', '.join(args.checkers)}・生成器 gemma4:12b の軸・並び 2 版）\n"]
    L += ["## 題材ごと\n", "| 題材 | 型 | 軸 | 過半数で反対 | 1 票でも | 弱 | 非排他 | 読み手 | p 前→後 (対照) | 札 前→後 | 段 前→後 | 反事実 前/後/対照 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    by_kind_agree, by_kind_or = {}, {}
    paired = []  # (row id, reader, before_ok, after_ok, control_ok, single_fact)
    label_changes = level_changes = n_rd = 0
    for row in rows:
        by_kind_agree.setdefault(row["kind"], []).append(len(row["flagged_agree"]) / row["n_axes"])
        by_kind_or.setdefault(row["kind"], []).append(len(row["flagged_or"]) / row["n_axes"])
        for rd, e in row["readers"].items():
            n_rd += 1
            b, a_, c = e["before"], e["after"], e["control"]
            label_changes += int(b["label"] != a_["label"])
            level_changes += int(b["level"] != a_["level"])
            cf = e.get("cf")
            mark = lambda v: "—" if v is None else ("✓" if v else "✗")  # noqa: E731
            cfs = f"{mark(cf['before'])}/{mark(cf['after'])}/{mark(cf['control'])}" if cf else "—"
            if cf and cf["before"] is not None:
                paired.append((row["id"], rd, cf["before"], cf["after"], cf["control"], row["single_fact_cf"]))
            L.append(f"| {row['id']} | {row['kind']} | {row['n_axes']} | {len(row['flagged_agree'])} | {len(row['flagged_or'])} | {len(row['weak'])} | {len(row['nonexclusive'])} "
                     f"| {rd} | {P.fmt(b['p'])}→{P.fmt(a_['p'])} ({P.fmt(c['p'])}) | {b['label']}→{a_['label']} | {b['level']}→{a_['level']} | {cfs} |")
    L.append("\n## 型ごとの食い違い率（軸ベース）\n")
    for k in by_kind_agree:
        L.append(f"- {k}: 過半数で反対 平均 {statistics.mean(by_kind_agree[k]):.2f}、1 票でも 平均 {statistics.mean(by_kind_or[k]):.2f}（題材 {len(by_kind_agree[k])}）")
    tot_axes = sum(r["n_axes"] for r in rows)
    L.append(f"\n## 較正: 検証役の過半数が生成器の向きに同意した軸 {tot_axes - sum(len(r['flagged_agree']) for r in rows)}/{tot_axes}"
             f"（＝向きを入れ替えた軸をこの規則が flag する割合と同じ量。検証役の偽陽性・偽陰性は分けられない）")
    L.append(f"\n## 対の排他性: 検証役の過半数が「両立しうる」とした軸 {sum(len(r['nonexclusive']) for r in rows)}/{tot_axes}")
    L.append(f"\n## 札の変化 {label_changes}/{n_rd} 行、段の変化 {level_changes}/{n_rd} 行（過半数規則で外した後）")
    L.append("\n## 反事実の追従（対応のある行。外した後に p が未定義なら ✗）\n")
    for name, filt in (("事実 1 箇所の反事実（p3 の 11 本＋m12）", lambda x: x[5]), ("別本文（新規 10 本）", lambda x: not x[5])):
        sub = [x for x in paired if filt(x)]
        if not sub:
            continue
        bef = sum(1 for x in sub if x[2])
        aft = sum(1 for x in sub if x[3])
        ctl = sum(1 for x in sub if x[4])
        gain = sum(1 for x in sub if (not x[2]) and x[3])
        loss = sum(1 for x in sub if x[2] and not x[3])
        gain_c = sum(1 for x in sub if (not x[2]) and x[4])
        loss_c = sum(1 for x in sub if x[2] and not x[4])
        verdict = "改善" if gain - loss >= IMPROVEMENT_MARGIN and (gain - loss) > (gain_c - loss_c) else "改善とは言えない"
        L.append(f"- {name}: n={len(sub)}。前 {bef} → 後 {aft}（✗→✓ {gain}、✓→✗ {loss}）／対照 {ctl}（✗→✓ {gain_c}、✓→✗ {loss_c}）→ **{verdict}**（規則: 純増 ≥ {IMPROVEMENT_MARGIN} かつ対照を上回る）")
    L.append("\n## 過半数で反対になった軸の中身\n")
    for row in rows:
        for v in row["votes"]:
            if v["id"] in row["flagged_agree"]:
                summ = {c: {s: cv[s]["majority"] for s in cv} for c, cv in v["checkers"].items()}
                L.append(f"- {row['id']} {v['id']} {v['axis']}: {summ}")
    (out / "summary_crosscheck.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"live={port.calls_live} cached={port.calls_cached}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
