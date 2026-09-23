"""受入 2 回目 M-1: 腕ごとに閾値が違っていた（C は既定 κ=0.667、A・B は測定時の κ=0.5）。
記録から両方の閾値で数え直し、2×2 が動かないことを確かめる。LLM は呼ばない。"""
import json
import os
import statistics
import sys
from pathlib import Path

W5 = Path(__file__).resolve().parent            # .pair-agent/probes/single-model-1/w5
PROBE = W5.parent


def find_repo() -> Path:
    """リポジトリの場所（受入 m10: 絶対パスを直書きしない）。環境変数 SD_REPO ＞ 自分の上 ＞ いまいる場所。"""
    env = os.environ.get("SD_REPO")
    if env:
        return Path(env)
    for p in [*W5.parents, Path.cwd(), *Path.cwd().parents]:
        if (p / "structural_distillation" / "compose.py").exists():
            return p
    raise SystemExit("リポジトリが見つからない。環境変数 SD_REPO にリポジトリのパスを入れて走らせる")


sys.path.insert(0, str(find_repo()))
sys.stdout.reconfigure(encoding="utf-8")

from structural_distillation import l3                                  # noqa: E402
from structural_distillation.contracts import AnswerMatrix, Thresholds  # noqa: E402
EXCLUDE = {"m11", "m12", "m17"}
SETS = {"既定（κ=0.667）": Thresholds(), "測定時（κ=0.5）": Thresholds(iota=0.3, kappa=0.5, rho=0.5, omega=0.5)}


def hit(expected, ps, labels):
    if expected == "none":
        return all(lb in ("NO_EVIDENCE", "INSTRUMENT_FAULT") for lb in labels)
    if not ps:
        return False
    pm = statistics.mean(ps)
    return {"support": pm > 0.6, "refute": pm < 0.4, "split": 0.3 <= pm <= 0.7}.get(expected)


def read_rows(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def score(matrices, active, expected, th):
    ps, labels = [], []
    for m in matrices:
        if m["reader"].startswith("fake:"):
            continue
        r = l3.aggregate(AnswerMatrix.from_dict(m), active, th)
        labels.append(r.label.value)
        if r.p is not None and r.label not in ("NO_EVIDENCE", "INSTRUMENT_FAULT"):
            ps.append(r.p)
    return hit(expected, ps, labels)


c_rows = json.loads((PROBE / "single_model.json").read_text(encoding="utf-8"))
w5_rows = json.loads((W5 / "w5.json").read_text(encoding="utf-8"))
c_recs = {tuple(a["claim_support"] for a in r["question_set"]["axes"]): r
          for r in read_rows(PROBE / "records.jsonl") if len(r["question_set"]["axes"]) > 1}
w5_recs = read_rows(W5 / "records.jsonl")
by_key = {}
for r in w5_recs:
    key = (tuple(a["claim_support"] for a in r["question_set"]["axes"]), r["question_set"]["budget"]["samples"],
           all(m["reader"].startswith("qwen3.5:4b") for m in r["matrices"]))
    by_key[key] = r

print(f"{'腕':<34}", "  ".join(f"{k:<16}" for k in SETS))
for arm, samples, weak in (("B 強い軸 × 弱い読み手（標本 1）", 1, True),
                           ("B 強い軸 × 弱い読み手（標本 2）", 2, True)):   # A は記録が無いので下で引き直す
    line = []
    for name, th in SETS.items():
        n = d = 0
        for mid, row in w5_rows.items():
            if mid in EXCLUDE:
                continue
            rec = by_key.get((tuple(a["support"] for a in row["axes"]), samples, weak))
            if rec is None:
                continue
            h = score(rec["matrices"], [a["id"] for a in row["axes"]], row.get("expected_lean"), th)
            if h is not None:
                d += 1
                n += bool(h)
        line.append(f"{n}/{d}")
    print(f"{arm:<30}", "  ".join(f"{x:<18}" for x in line))
line = []
for name, th in SETS.items():
    n = d = 0
    for mid, row in c_rows.items():
        if mid in EXCLUDE:
            continue
        rec = c_recs.get(tuple(a["support"] for a in row["axes"]))
        h = score(rec["matrices"], row["active"], row.get("expected_lean"), th)
        if h is not None:
            d += 1
            n += bool(h)
    line.append(f"{n}/{d}")
print(f"{'C 弱い軸 × 弱い読み手（標本 1）':<30}", "  ".join(f"{x:<18}" for x in line))

# A は記録を残していない（強い読み手はキャッシュからの引き直し）。ここで両方の閾値で引き直す（呼び出し 0）
import asyncio  # noqa: E402

sys.path.insert(0, str(find_repo() / "tools"))
from probe_records import OUT4, load_materials, load_results          # noqa: E402

from structural_distillation import judge                              # noqa: E402
from structural_distillation.contracts import Axis, Budget, Probability, QuestionSet  # noqa: E402
from structural_distillation.l0 import CachedPort, OllamaReader        # noqa: E402
from structural_distillation.prompts import PromptSet                  # noqa: E402

mats = load_materials()
base = {r["id"]: r for r in load_results("base21")}
p = PromptSet.builtin("ja")


async def arm_a(th, samples):
    n = d = live = missed = 0
    for mid, rec in base.items():
        if mid in EXCLUDE:
            continue
        axes = [Axis(a["id"], a["axis"], a["claim_support"], a["claim_refute"]) for a in rec["plan"]["axes"]]
        budget = Budget(samples=samples, workers=8, crosscheck=False)
        qs = QuestionSet(axes=axes, active_ids=[a.id for a in axes], planner="gemma4:31b-cloud",
                         prompt=p.version(), budget=budget)
        readers = [CachedPort(OllamaReader(m), None, cache_only=True, read_only=True,
                              extra=[OUT4 / "base21" / "llm_cache.jsonl"])
                   for m in ("qwen3.5:397b-cloud", "glm-5.2:cloud")]
        j = await judge(mats[mid]["text"], mats[mid]["proposition"], Probability(), readers=readers,
                        question_set=qs, budget=budget, thresholds=th, prompts=p)
        live += j.cost.live
        missed += j.cost.missed
        ps = [r.p for r in j.readings.values() if r.p is not None
              and r.label.value not in ("NO_EVIDENCE", "INSTRUMENT_FAULT")]
        h = hit(mats[mid].get("expected_lean"), ps, [r.label.value for r in j.readings.values()])
        if h is not None:
            d += 1
            n += bool(h)
    return f"{n}/{d}", live, missed


for samples in (1, 2):
    out = []
    for name, th in SETS.items():
        s, live, missed = asyncio.run(arm_a(th, samples))
        out.append(f"{s}（実呼び出し {live}・外れ {missed}）")
    print(f"{'A 強い軸 × 強い読み手（標本 ' + str(samples) + '）':<30}", "  ".join(f"{x:<18}" for x in out))
