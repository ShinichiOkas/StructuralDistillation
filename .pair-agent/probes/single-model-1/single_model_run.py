"""単一モデル運用の測定（合意 single-model-operation v2 の W1〜W4）。ライブラリ v0.1.0 を計器として使う。

腕:
  W1 弱い単一モデル: 生成器・読み手・検証役をすべて同じ弱いモデル（既定 qwen3.5:4b）
  W2 見かけの複数体: 同じモデルを別名の 2 体の読み手として差す（W1 の走行に同居）
  W3 検出項目: 本文の 1 文（明らかにある）と作り話（絶対に無い）の対を、別の問いの集合として当てる
  W4 検証役の比較: W1 の弱い軸に、クラウド 2 体の交差検証を当てる

⚠ GPU は師匠と共有。ローカルは workers=1（並列なし）。走行はスクラッチで、終わってから記録をリポジトリへ写す。
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

from structural_distillation import judge, l1                       # noqa: E402
from structural_distillation.contracts import Axis, Budget, PlanningFailed, Probability, QuestionSet  # noqa: E402
from structural_distillation.l0 import CachedPort, FakeReader, Meter, OllamaReader  # noqa: E402
from structural_distillation.prompts import PromptSet               # noqa: E402
from structural_distillation.store import QuestionStore             # noqa: E402
from structural_distillation.units import segment                   # noqa: E402

PROBES = REPO / ".pair-agent" / "probes" / "upstream-probe-1"
CLOUD_VERIFIERS = ["glm-5.2:cloud", "qwen3.5:397b-cloud"]
ABSENT_CLAIM = "この出来事は月面の基地で起きた"   # どの題材にも無い（機械的に作る。LLM には作らせない）


def weak(model: str, name: str, cache: Path, extra=()) -> CachedPort:
    """同じモデルを別名の読み手として差す（見かけの複数体）。鍵は名前で分かれるので、独立に呼ばれる。

    ⚠ 包み（自前のラッパ）にしない。下のモデル名を宣言しない読み手だと、判定が「全部同じモデル」に
      気づけず注意（Judgment.notes）が出なかった（受入 C4）。name と model を分けて宣言する。
    """
    return CachedPort(OllamaReader(model, name=name, num_ctx=8192), cache, extra=extra, **CACHE_MODE)


def cloud(model: str, cache: Path, extra=()) -> CachedPort:
    return CachedPort(OllamaReader(model), cache, extra=extra, **CACHE_MODE)


CACHE_MODE: dict = {}      # --cache-only のとき {"cache_only": True, "read_only": True}


def detection_axis(units) -> Axis:
    """本文の 1 文そのまま（明らかにある）と作り話（絶対に無い）の対。"""
    present = max(units, key=lambda u: len(u.text)).text.rstrip("。！？")
    return Axis(id="d01", name="検出項目", claim_support=present, claim_refute=ABSENT_CLAIM, kind="detection")


async def run_material(m: dict, args, out: dict) -> dict:
    p = PromptSet.builtin("ja")
    units = segment(m["text"])
    lcache, ccache = Path(args.out) / "local_cache.jsonl", Path(args.out) / "cloud_cache.jsonl"
    xl = [Path(x) / "local_cache.jsonl" for x in args.extra]
    xc = [Path(x) / "cloud_cache.jsonl" for x in args.extra]
    store = QuestionStore(Path(args.out) / "questions")
    readers = [weak(args.model, f"{args.model}#a", lcache, xl), weak(args.model, f"{args.model}#b", lcache, xl),
               FakeReader("all_yes"), FakeReader("all_no"), FakeReader("all_undetermined")]
    verifiers = [weak(args.model, f"{args.model}#v1", lcache, xl), weak(args.model, f"{args.model}#v2", lcache, xl)]
    budget = Budget(axes=6, samples=1, workers=1, crosscheck=True)
    t0 = time.time()
    try:
        j = await judge(m["text"], m["proposition"], Probability(), readers=readers,
                        planner=weak(args.model, f"{args.model}#plan", lcache, xl), verifiers=verifiers,
                        budget=budget, prompts=p, question_store=store,
                        record_path=Path(args.out) / "records.jsonl")
    except PlanningFailed as e:
        return {"id": m["id"], "planning_failed": [list(a.violations) or a.error for a in e.attempts],
                "seconds": round(time.time() - t0, 1)}
    row = {"id": m["id"], "proposition": m["proposition"], "expected_lean": m.get("expected_lean"),
           "kind": m.get("kind"), "seconds": round(time.time() - t0, 1),
           "axes": [{"id": a.id, "support": a.claim_support, "refute": a.claim_refute} for a in j.question_set.axes],
           "active": list(j.question_set.active_ids),
           "self_crosscheck": {"flagged": j.question_set.crosscheck.flagged, "weak": j.question_set.crosscheck.weak,
                               "nonexclusive": j.question_set.crosscheck.nonexclusive} if j.question_set.crosscheck else None,
           "readings": {n: {"p": r.p, "w": r.w, "label": r.label.value, "reason": r.reason.value if r.reason else None,
                            "valid_rate": r.diagnostics.valid_rate, "invalid_rate": r.diagnostics.invalid_rate,
                            "silent_rate": r.diagnostics.silent_rate,
                            "contradiction_rate": r.diagnostics.contradiction_rate,
                            "d": [x.d for x in r.axes]}
                        for n, r in j.readings.items()},
           "delta": j.summary.delta, "readers_split": j.summary.readers_split,
           "cost": {"plan": j.cost.plan.live, "crosscheck": j.cost.crosscheck.live, "answer": j.cost.answer.live}}
    # W3 検出項目（同じ読み手 2 体に当てる。判定の軸とは別の問いの集合）
    det_qs = QuestionSet(axes=[detection_axis(units)], active_ids=["d01"], planner="mechanical",
                         prompt=p.version(), budget=Budget(samples=1, workers=1, crosscheck=False))
    det = await judge(m["text"], m["proposition"], Probability(), readers=readers[:2], question_set=det_qs,
                      budget=Budget(samples=1, workers=1, crosscheck=False), prompts=p)
    row["detection"] = {"present": det_qs.axes[0].claim_support, "absent": ABSENT_CLAIM,
                        "answers": {mx.reader: {a.side: {"verdict": a.verdict.value if a.verdict else None,
                                                         "valid": a.valid, "evidence": a.evidence}
                                                for a in mx.answers} for mx in det.matrices}}
    # W4 同じ弱い軸に、クラウドの検証役で交差検証
    if not args.skip_cloud:
        meter = Meter()
        cc = await l1.crosscheck([cloud(v, ccache, xc) for v in CLOUD_VERIFIERS], m["proposition"], j.question_set.axes,
                                 prompts=p, sem=asyncio.Semaphore(6), meter=meter)
        row["cloud_crosscheck"] = {"flagged": cc.flagged, "weak": cc.weak, "nonexclusive": cc.nonexclusive,
                                   "calls": meter.cost.crosscheck.live}
    out[m["id"]] = row
    return row


async def main_async(args) -> int:
    mats = []
    for f in ("materials.json", "materials2.json"):
        mats += json.loads((PROBES / f).read_text(encoding="utf-8"))["materials"]
    if args.only:
        mats = [m for m in mats if m["id"] in set(args.only)]
    Path(args.out).mkdir(parents=True, exist_ok=True)
    res_path = Path(args.out) / "single_model.json"
    out = json.loads(res_path.read_text(encoding="utf-8")) if res_path.exists() else {}
    for i, m in enumerate(mats, 1):
        if m["id"] in out and not args.force:
            print(f"[{m['id']}] 済み", flush=True)
            continue
        row = await run_material(m, args, out)
        res_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        if "planning_failed" in row:
            print(f"[{m['id']}] 生成に失敗: {row['planning_failed']}（{row['seconds']}s）", flush=True)
            continue
        ps = {n: r["p"] for n, r in row["readings"].items() if not n.startswith("fake:")}
        print(f"[{m['id']}] {i}/{len(mats)} p={ps} Δ={row['delta']} 自己で外した={row['self_crosscheck']['flagged']}"
              f" 雲で外した={row.get('cloud_crosscheck', {}).get('flagged')} "
              f"呼び出し={row['cost']}（{row['seconds']}s）", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:4b")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--skip-cloud", action="store_true")
    ap.add_argument("--extra", nargs="*", default=[], help="読むだけの追加キャッシュ（前の走行の out ディレクトリ）")
    ap.add_argument("--cache-only", action="store_true", help="外れても呼ばない（記録の作り直し。LLM の費用 0）")
    args = ap.parse_args()
    if args.cache_only:
        CACHE_MODE.update(cache_only=True, read_only=True)
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
