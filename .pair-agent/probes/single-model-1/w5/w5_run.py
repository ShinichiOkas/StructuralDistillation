"""W5: 強い生成器が作った軸を、弱い読み手 2 体に当てる（受入 C1・C2 の分離の腕）。

受入の指摘: W1（弱いモデル 1 つで生成器・読み手・検証役を兼ねる）だけでは、
「壊れるのは生成器」と言い切る腕が無い。生成器だけを強いものに替えて、読み手を弱いまま残せば、
生成器の寄与と読み手の寄与が分かれる:

  A 強い軸 × 強い読み手 …… out4/base21（既に測定済み。キャッシュから引き直す。呼び出し 0）
  B 強い軸 × 弱い読み手 …… この走行（LLM を呼ぶ。標本 2）
  C 弱い軸 × 弱い読み手 …… .pair-agent/probes/single-model-1（測定済み。標本 1）

B と C の差が生成器の寄与、A と B の差が読み手の寄与。
標本数の食い違い（受入 M1）は、B を標本 2 で走らせたあと標本 1 でも引き直し（キャッシュ命中・呼び出し 0）、
A も標本 1 で引き直して、標本 1 の 3 つ組で比べることで消す。

⚠ GPU は師匠と共有。workers=1（並列なし）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
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
sys.path.insert(0, str(REPO / "tools"))

from probe_records import OUT4, load_materials, load_results        # noqa: E402

from structural_distillation import judge                            # noqa: E402
from structural_distillation.contracts import Axis, Budget, Probability, QuestionSet, Thresholds  # noqa: E402
from structural_distillation.l0 import CachedPort, OllamaReader      # noqa: E402
from structural_distillation.prompts import PromptSet                # noqa: E402

RUN_TIME = Thresholds(iota=0.3, kappa=0.5, rho=0.5, omega=0.5)      # 記録の札はこの閾値で付いた
STRONG_READERS = ["qwen3.5:397b-cloud", "glm-5.2:cloud"]
STRONG_PLANNER = "gemma4:31b-cloud"


def weak(model: str, name: str, cache: Path, *, cache_only: bool = False) -> CachedPort:
    return CachedPort(OllamaReader(model, name=name, num_ctx=8192), cache,
                      cache_only=cache_only, read_only=cache_only)


def strong(model: str) -> CachedPort:
    return CachedPort(OllamaReader(model), None, cache_only=True, read_only=True,
                      extra=[OUT4 / "base21" / "llm_cache.jsonl"])


def reading_row(j) -> dict:
    return {n: {"p": r.p, "w": r.w, "label": r.label.value, "reason": r.reason.value if r.reason else None,
                "s": r.counts.s, "r": r.counts.r, "n": r.counts.n,
                "u": {k: getattr(r.counts, k) for k in ("u1", "u2", "u3", "u4")},
                "valid_rate": r.diagnostics.valid_rate, "invalid_rate": r.diagnostics.invalid_rate,
                "silent_rate": r.diagnostics.silent_rate, "contradiction_rate": r.diagnostics.contradiction_rate,
                "d": [x.d for x in r.axes]}
            for n, r in j.readings.items()}


def question_set(rec: dict, budget: Budget) -> QuestionSet:
    axes = [Axis(a["id"], a["axis"], a["claim_support"], a["claim_refute"]) for a in rec["plan"]["axes"]]
    return QuestionSet(axes=axes, active_ids=[a.id for a in axes], planner=STRONG_PLANNER,
                       prompt=PromptSet.builtin("ja").version(), budget=budget)


async def main_async(args) -> int:
    p = PromptSet.builtin("ja")
    mats = load_materials()
    recs = {r["id"]: r for r in load_results("base21")}
    if args.only:
        recs = {k: v for k, v in recs.items() if k in set(args.only)}
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = out_dir / "local_cache.jsonl"
    res_path = out_dir / "w5.json"
    out = json.loads(res_path.read_text(encoding="utf-8")) if res_path.exists() else {}
    for i, mid in enumerate(recs, 1):
        if mid in out and not args.force:
            print(f"[{mid}] 済み", flush=True)
            continue
        m = mats[mid]
        t0 = time.time()
        row = {"id": mid, "proposition": m["proposition"], "expected_lean": m.get("expected_lean"),
               "axes": [{"id": a["id"], "support": a["claim_support"], "refute": a["claim_refute"]}
                        for a in recs[mid]["plan"]["axes"]]}
        for samples in (2, 1):          # 標本 2 を走らせてから、同じキャッシュで標本 1 を引き直す（呼び出し 0）
            budget = Budget(samples=samples, workers=1, crosscheck=False)
            readers = [weak(args.model, f"{args.model}#a", cache), weak(args.model, f"{args.model}#b", cache)]
            j = await judge(m["text"], m["proposition"], Probability(), readers=readers,
                            question_set=question_set(recs[mid], budget), budget=budget, thresholds=RUN_TIME,
                            prompts=p, record_path=out_dir / "records.jsonl")
            row[f"weak_readers_s{samples}"] = reading_row(j)
            row[f"delta_s{samples}"] = j.summary.delta
            row[f"cost_s{samples}"] = {"live": j.cost.live, "cached": j.cost.cached, "missed": j.cost.missed}
            row["notes"] = j.notes
        # A 強い軸 × 強い読み手を標本 1・2 で引き直す（out4 のキャッシュ。呼び出し 0。受入 3 回目 m-2: 定数で埋めない）
        for samples in (1, 2):
            b = Budget(samples=samples, workers=8, crosscheck=False)
            a = await judge(m["text"], m["proposition"], Probability(), readers=[strong(r) for r in STRONG_READERS],
                            question_set=question_set(recs[mid], b), budget=b, thresholds=RUN_TIME, prompts=p)
            row[f"strong_readers_s{samples}"] = reading_row(a)
            row[f"strong_delta_s{samples}"] = a.summary.delta
            row[f"strong_cost_s{samples}"] = {"live": a.cost.live, "cached": a.cost.cached, "missed": a.cost.missed}
        row["seconds"] = round(time.time() - t0, 1)
        out[mid] = row
        res_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        ps = {n.split("#")[-1]: v["p"] for n, v in row["weak_readers_s2"].items()}
        sp = {n.split(":")[0]: v["p"] for n, v in row["strong_readers_s1"].items()}
        print(f"[{mid}] {i}/{len(recs)} 想定={row['expected_lean']} 弱い読み手 p={ps} 強い読み手 p={sp}"
              f" 呼び出し={row['cost_s2']['live']}＋{row['cost_s1']['live']}（外れ {row['strong_cost_s1']['missed']}）"
              f"（{row['seconds']}s）", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:4b")
    ap.add_argument("--out", required=True)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
