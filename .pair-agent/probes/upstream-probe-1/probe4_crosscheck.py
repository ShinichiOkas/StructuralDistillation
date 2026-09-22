"""M1 向きの交差検証（設計 H12・S13）。

p3 の記録（results.json）にある軸について、生成器とは別の読み手に
「この記述が本文に述べられていたら、命題は支持されるか、反証されるか」を答えさせ、
生成器が付けた向き（claim_support / claim_refute）と食い違う軸を数える。
食い違った軸を外して p と反事実の追従を再計算する（答えは記録済みなので追加の呼び出しは交差検証だけ）。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import probe as P

VERSION = "xc1"
SCHEMA = {
    "type": "object",
    "properties": {"orientation": {"type": "string", "enum": ["支持", "反証", "どちらとも言えない"]}},
    "required": ["orientation"],
}


def prompt(proposition: str, claim: str) -> list[dict]:
    sys_msg = "あなたは、記述と命題の論理的な関係を判定する係です。本文は見ません。出力は指定の JSON だけを返してください。"
    user = f"""命題: 「{proposition}」

次の記述が、ある本文に述べられていたとします。
記述: 「{claim}」

この記述が本文に述べられていたら、命題は支持されますか、反証されますか。
- 述べられていることで命題が正しい方向に傾くなら "支持"
- 述べられていることで命題が誤りの方向に傾くなら "反証"
- 命題の真偽に関係しないなら "どちらとも言えない"
"""
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]


def recount(per_axis: list[dict], keep: set[str]) -> dict:
    dirs = [x["d"] for x in per_axis if x["id"] in keep]
    s = sum(1 for d in dirs if d == 1)
    r = sum(1 for d in dirs if d == -1)
    return {"s": s, "r": r, "n": len(dirs), "p": s / (s + r) if s + r else None, "w": 2 * min(s, r) / (s + r) if s + r else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(P.HERE / "out3" / "results.json"))
    ap.add_argument("--materials", nargs="+", default=[str(P.HERE / "materials.json")])
    ap.add_argument("--checkers", nargs="*", default=["qwen3.5:4b", "gemma3:4b"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache-only", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    port = P.Port(out / "llm_cache.jsonl", cache_only=args.cache_only)
    results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    kinds = {}
    for path in args.materials:
        for m in json.loads(Path(path).read_text(encoding="utf-8"))["materials"]:
            kinds[m["id"]] = m.get("kind") or ("evaluative" if m["id"] in ("m01", "m04", "m07", "m09") else "other")

    rows = []
    for r in results:
        axes = r["plan"]["axes"]
        if not axes:
            continue
        flagged, weak = set(), set()
        detail = []
        for a in axes:
            votes = {}
            for side, claim, expect in (("support", a["claim_support"], "支持"), ("refute", a["claim_refute"], "反証")):
                for c in args.checkers:
                    resp = port.chat(c, prompt(r["proposition"], claim), SCHEMA, sample=0, version=VERSION)
                    try:
                        o = json.loads(resp["content"])["orientation"] if resp["ok"] else None
                    except Exception:  # noqa: BLE001
                        o = None
                    votes[(side, c)] = o
                    if o is not None and o != expect:
                        (flagged if o in ("支持", "反証") else weak).add(a["id"])
            detail.append({"id": a["id"], "axis": a["axis"], "votes": {f"{k[0]}/{k[1]}": v for k, v in votes.items()}})
        row = {"id": r["id"], "kind": kinds.get(r["id"], "other"), "proposition": r["proposition"],
               "n_axes": len(axes), "flagged": sorted(flagged), "weak": sorted(weak - flagged), "detail": detail, "readers": {}}
        keep = {a["id"] for a in axes} - flagged
        for rd, agg in r["readers"].items():
            if rd.startswith("fake:"):
                continue
            before = agg["A"]
            after = recount(agg["per_axis"], keep)
            entry = {"p_before": before["p"], "p_after": after["p"], "w_after": after["w"], "n_after": after["n"]}
            cf = (r.get("counterfactual") or {}).get("readers", {}).get(rd)
            if cf:
                cf_after = recount(cf["per_axis"], keep)
                exp = r["counterfactual"]["expected_change"]

                def ok(po, pc):
                    if po is None or pc is None:
                        return None
                    return (pc > po) if exp == "support_up" else (pc < po)

                entry["cf_before"] = {"p_orig": cf["p_orig"], "p_cf": cf["p_cf"], "ok": ok(cf["p_orig"], cf["p_cf"])}
                entry["cf_after"] = {"p_orig": after["p"], "p_cf": cf_after["p"], "ok": ok(after["p"], cf_after["p"])}
            row["readers"][rd] = entry
        rows.append(row)
        print(f"[{r['id']}] {row['kind']:10s} 食い違い {len(flagged)}/{len(axes)}  弱 {len(row['weak'])}", flush=True)

    (out / "crosscheck.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    L = [f"# M1 向きの交差検証（検証役 {', '.join(args.checkers)}・生成器 gemma4:12b の軸）\n",
         "| 題材 | 型 | 軸 | 食い違い | 弱 | 読み手 | p 前 → 後 | 反事実 前 | 反事実 後 |", "|---|---|---|---|---|---|---|---|---|"]
    by_kind = {}
    cf_before_ok = cf_before_n = cf_after_ok = cf_after_n = 0
    for row in rows:
        by_kind.setdefault(row["kind"], []).append(len(row["flagged"]) / row["n_axes"])
        for rd, e in row["readers"].items():
            cb = e.get("cf_before", {})
            ca = e.get("cf_after", {})
            def mark(x):
                return "—" if not x or x.get("ok") is None else ("✓" if x["ok"] else "✗")
            if cb and cb.get("ok") is not None:
                cf_before_n += 1
                cf_before_ok += int(cb["ok"])
            if ca and ca.get("ok") is not None:
                cf_after_n += 1
                cf_after_ok += int(ca["ok"])
            L.append(f"| {row['id']} | {row['kind']} | {row['n_axes']} | {len(row['flagged'])} | {len(row['weak'])} | {rd} "
                     f"| {P.fmt(e['p_before'])} → {P.fmt(e['p_after'])} | {P.fmt(cb.get('p_orig'))}→{P.fmt(cb.get('p_cf'))} {mark(cb)} "
                     f"| {P.fmt(ca.get('p_orig'))}→{P.fmt(ca.get('p_cf'))} {mark(ca)} |")
    L.append("\n## 型ごとの食い違い率（軸ベース）\n")
    for k, v in by_kind.items():
        L.append(f"- {k}: 平均 {statistics.mean(v):.2f}（題材 {len(v)}）")
    L.append(f"\n## 反事実の追従: 外す前 {cf_before_ok}/{cf_before_n} → 外した後 {cf_after_ok}/{cf_after_n}\n")
    L.append("## 食い違った軸の中身\n")
    for row in rows:
        for d in row["detail"]:
            if d["id"] in row["flagged"]:
                L.append(f"- {row['id']} {d['id']} {d['axis']}: {d['votes']}")
    (out / "summary_crosscheck.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"live={port.calls_live} cached={port.calls_cached}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
