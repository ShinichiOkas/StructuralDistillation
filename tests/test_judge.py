"""合成 judge（実装設計 §4.5）。台本の読み手と偽読み手だけ（LLM は呼ばない）。"""
from __future__ import annotations

import ast
import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

import structural_distillation as sd
from structural_distillation import judge, judge_sync, replay
from dataclasses import replace

from structural_distillation.contracts import (Budget, InputError, Label, Ordinal, PlanningFailed, Probability, Reason,
                                               RetryAction, Thresholds)
from structural_distillation.l0 import CachedPort, FakeReader

from conftest import ScriptedReader

TEXT = "甲は朝に家を出た。甲は駅で財布を拾った。甲は財布を交番に届けなかった。甲は中身の金を使った。甲は後で謝った。"
PROP = "甲は悪い"
N = 6


def plan_json(n=N):
    return json.dumps({"axes": [{"axis": f"軸{i}", "claim_support": f"甲は{i}番目の悪事をした",
                                 "claim_refute": f"甲は{i}番目の悪事をしなかった"} for i in range(n)]}, ensure_ascii=False)


def planner(n=N):
    return ScriptedReader("planner", [plan_json(n)])


def claim_of(messages):
    m = re.search(r"## 記述\n(.*)\n", messages[-1]["content"])
    return m.group(1) if m else None


def reader(name, support_upto: int):
    """i < support_upto の軸は支持側が述べられ、それ以外は反証側が述べられている読み手。"""
    def script(messages, schema, sample, version):
        c = claim_of(messages)
        i = int(re.search(r"(\d+)番目", c).group(1))
        refute_form = c.endswith("しなかった")
        states = (i < support_upto) != refute_form
        return json.dumps({"verdict": "述べている" if states else "否定している", "evidence": ["s2"]}, ensure_ascii=False)
    return ScriptedReader(name, script)


def run(**kw):
    kw.setdefault("budget", Budget(crosscheck=False))
    return asyncio.run(judge(TEXT, PROP, kw.pop("output", Ordinal(5)), **kw))


# ---------------------------------------------------------------- 入口の検査（F1・F2）

@pytest.mark.parametrize("kw", [
    {"text": "   "}, {"proposition": ""}, {"output": Ordinal(1)}, {"output": Ordinal(3, labels=("a",))},
    {"text": "あ" * 13000},                                           # F1（既定 max_chars 12,000）
    {"budget": Budget(axes_min=5, axes_max=3)}, {"budget": Budget(samples=0)}, {"budget": Budget(workers=0)},
    {"readers": []}, {"segmentation": "word"},
])
def test_input_errors(kw):
    args = {"text": TEXT, "proposition": PROP, "output": Probability(), "readers": [reader("r", 3)], "planner": planner()}
    args.update(kw)
    with pytest.raises(InputError):
        asyncio.run(judge(args.pop("text"), args.pop("proposition"), args.pop("output"), **args))


@pytest.mark.parametrize("t", [Thresholds(rho=0.0), Thresholds(iota=1.5), Thresholds(kappa=-0.1), Thresholds(omega=0.0),
                               Thresholds(delta=-0.01)])
def test_threshold_range_is_checked(t, tmp_path):
    """閾値の値域（受入 M3: ρ = 0 で札の規則が落ちた）。judge の入口と replay の両方で。"""
    with pytest.raises(InputError):
        run(readers=[reader("r", 3)], planner=planner(), thresholds=t)
    path = tmp_path / "r.jsonl"
    run(readers=[reader("r", 3)], planner=planner(), record_path=path)
    with pytest.raises(InputError):
        replay(json.loads(path.read_text(encoding="utf-8")), thresholds=t)


def test_axes_must_lie_within_min_and_max():
    with pytest.raises(InputError):
        run(readers=[reader("r", 3)], planner=planner(), budget=Budget(axes=6, axes_min=2, axes_max=4, crosscheck=False))


def test_duplicate_verifiers_and_duplicate_axis_ids_are_rejected():
    with pytest.raises(InputError):
        run(readers=[reader("r", 3)], planner=planner(), verifiers=[reader("v", 1), reader("v", 1)], budget=Budget())
    j = run(readers=[reader("r", 3)], planner=planner())
    qs = j.question_set
    dup = type(qs)(axes=qs.axes + qs.axes[:1], active_ids=qs.active_ids, planner=qs.planner, prompt=qs.prompt,
                   budget=qs.budget)
    with pytest.raises(InputError):
        run(readers=[reader("r", 3)], question_set=dup)
    dup2 = type(qs)(axes=qs.axes, active_ids=qs.active_ids + qs.active_ids[:1], planner=qs.planner, prompt=qs.prompt,
                    budget=qs.budget)
    with pytest.raises(InputError):
        run(readers=[reader("r", 3)], question_set=dup2)


def test_public_judge_is_the_function_whatever_the_import_order():
    """受入 M4: 合成を judge.py に置いていたとき、judge_sync を先に import すると judge がモジュールに化けた。"""
    import subprocess
    code = ("from structural_distillation import judge_sync, replay, judge; assert callable(judge), type(judge); "
            "import structural_distillation as sd; assert callable(sd.judge); "
            "import structural_distillation.compose as c; assert c.judge is judge")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(Path(__file__).parent))
    assert r.returncode == 0, r.stderr


def test_all_axes_flagged_is_an_instrument_fault_with_a_reason_and_retry_hint():
    """交差検証で全軸が外れたとき（師匠決定 2026-09-23）: 札は計器不良・値なし・理由つき。
    さらに、上位が作り直せるだけの材料（どこが壊れたか・次に取る手・外れた軸・票の在りか）を返す。"""
    def against(messages, schema, sample, version):
        if "orientation" in schema["properties"]:
            c = re.search(r"記述: 「(.*)」", messages[-1]["content"]).group(1)
            return json.dumps({"orientation": "支持" if c.endswith("しなかった") else "反証"}, ensure_ascii=False)
        return json.dumps({"compatible": "両立しない"}, ensure_ascii=False)
    j = asyncio.run(judge(TEXT, PROP, Probability(), readers=[reader("r1", 4)], planner=planner(),
                          verifiers=[ScriptedReader("v1", against), ScriptedReader("v2", against)]))
    r = j.readings["r1"]
    assert j.question_set.active_ids == [] and r.label == Label.INSTRUMENT_FAULT and r.value is None
    assert r.reason == Reason.NO_ACTIVE_AXES and r.diagnostics.valid_rate is None
    assert j.matrices[0].answers == [] and j.cost.answer.live == 0
    h = j.retry
    assert h.reason == Reason.NO_ACTIVE_AXES and h.scope == "question_set" and h.action == RetryAction.REPLAN
    assert h.details["flagged"] == [f"a{i:02d}" for i in range(1, N + 1)] and h.details["n_axes"] == N
    assert h.details["verifiers"] == ["v1", "v2"] and h.details["votes_in"] == "question_set.crosscheck.votes"
    assert h.details["source"] == "generated" and h.details["store_key"] is None and h.details["readers"] == ["r1"]
    assert h.details["planner"] == "planner" and h.details["weak"] == [] and h.details["plan_from_cache"] is False
    assert "全 6 軸が外れた" in h.message and "judge をもう一度呼ぶ" in h.message
    # 票は記録に残るので、上位は「どの軸がなぜ外れたか」を見てから作り直せる
    assert len(j.question_set.crosscheck.votes) == N


def test_given_question_set_with_no_active_axes_tells_the_caller_to_supply_one():
    j = run(readers=[reader("r1", 4)], planner=planner())
    empty = replace(j.question_set, active_ids=[])
    k = run(readers=[reader("r1", 4)], question_set=empty)
    assert k.readings["r1"].label == Label.INSTRUMENT_FAULT and k.readings["r1"].reason == Reason.NO_ACTIVE_AXES
    assert k.retry.action == RetryAction.SUPPLY_QUESTION_SET and k.retry.details["flagged"] == []
    assert "軸 6" in k.retry.message and "全 6 軸が外れた" not in k.retry.message


def test_partly_flagged_but_emptied_by_the_caller_is_described_accurately():
    """受入 M2: 外れたのが一部でも「全 N 軸が外れた」と言っていた。"""
    def one_against(messages, schema, sample, version):
        if "orientation" in schema["properties"]:
            c = re.search(r"記述: 「(.*)」", messages[-1]["content"]).group(1)
            if c == "甲は0番目の悪事をした":
                return json.dumps({"orientation": "反証"}, ensure_ascii=False)
            return json.dumps({"orientation": "反証" if c.endswith("しなかった") else "支持"}, ensure_ascii=False)
        return json.dumps({"compatible": "両立しない"}, ensure_ascii=False)
    j = asyncio.run(judge(TEXT, PROP, Probability(), readers=[reader("r1", 4)], planner=planner(),
                          verifiers=[ScriptedReader("v1", one_against), ScriptedReader("v2", one_against)]))
    k = run(readers=[reader("r1", 4)], question_set=replace(j.question_set, active_ids=[]))
    assert "うち交差検証で外れたのは 1 本" in k.retry.message and k.retry.details["flagged"] == ["a01"]


def test_replan_says_the_cache_would_return_the_same_questions(tmp_path):
    """受入 M1: 生応答のキャッシュを使っていると「もう一度呼べば作り直される」は嘘になる。"""
    def against(messages, schema, sample, version):
        if "orientation" in schema["properties"]:
            c = re.search(r"記述: 「(.*)」", messages[-1]["content"]).group(1)
            return json.dumps({"orientation": "支持" if c.endswith("しなかった") else "反証"}, ensure_ascii=False)
        return json.dumps({"compatible": "両立しない"}, ensure_ascii=False)
    cache = tmp_path / "c.jsonl"
    args = dict(readers=[reader("r1", 4)], verifiers=[ScriptedReader("v1", against), ScriptedReader("v2", against)])
    first = asyncio.run(judge(TEXT, PROP, Probability(), planner=CachedPort(planner(), cache), **args))
    assert first.retry.details["plan_from_cache"] is False and "もう一度呼ぶ" in first.retry.message
    again = asyncio.run(judge(TEXT, PROP, Probability(), planner=CachedPort(planner(), cache), **args))
    assert again.retry.details["plan_from_cache"] is True
    assert "同じ問いになる" in again.retry.message and "question_store" in again.retry.message


def test_retry_hint_and_reason_survive_the_record(tmp_path):
    def against(messages, schema, sample, version):
        if "orientation" in schema["properties"]:
            c = re.search(r"記述: 「(.*)」", messages[-1]["content"]).group(1)
            return json.dumps({"orientation": "支持" if c.endswith("しなかった") else "反証"}, ensure_ascii=False)
        return json.dumps({"compatible": "両立しない"}, ensure_ascii=False)
    path = tmp_path / "r.jsonl"
    asyncio.run(judge(TEXT, PROP, Probability(), readers=[reader("r1", 4)], planner=planner(),
                      verifiers=[ScriptedReader("v1", against), ScriptedReader("v2", against)], record_path=path))
    rec = json.loads(path.read_text(encoding="utf-8"))
    assert rec["readings"]["r1"]["reason"] == "no_active_axes" and rec["readings"]["r1"]["label"] == "INSTRUMENT_FAULT"
    assert rec["retry"]["action"] == "replan" and rec["retry"]["details"]["n_axes"] == N
    again = replay(rec)
    assert again.retry.action == RetryAction.REPLAN and again.readings["r1"].reason == Reason.NO_ACTIVE_AXES


def test_duplicate_reader_names_and_fake_planner_are_rejected():
    with pytest.raises(InputError):
        run(readers=[reader("r", 3), reader("r", 4)], planner=planner())
    with pytest.raises(InputError):
        run(readers=[reader("r", 3)], planner=FakeReader("all_yes"))
    with pytest.raises(InputError):
        run(readers=[FakeReader("all_yes")])     # 生成器の既定になれる実読み手がいない


def test_planning_failure_propagates():
    with pytest.raises(PlanningFailed):
        run(readers=[reader("r", 3)], planner=ScriptedReader("planner", [plan_json(2)]))


# ---------------------------------------------------------------- 端から端まで

def test_end_to_end_with_fake_readers_s11():
    fakes = [FakeReader("all_yes"), FakeReader("all_no"), FakeReader("all_undetermined")]
    j = run(readers=[reader("r1", 4), reader("r2", 3), *fakes], planner=planner())
    r1, r2 = j.readings["r1"], j.readings["r2"]
    assert (r1.p, r1.label, r1.value.level) == (pytest.approx(4 / 6), Label.SPLIT, 4)
    assert (r2.p, r2.value.level) == (pytest.approx(0.5), 3)
    assert j.readings["fake:all_yes"].label == Label.INSTRUMENT_FAULT and j.readings["fake:all_yes"].value is None
    assert j.readings["fake:all_no"].label == Label.INSTRUMENT_FAULT
    assert j.readings["fake:all_undetermined"].label == Label.NO_EVIDENCE
    s = j.summary
    assert s.readers == ["r1", "r2"] and s.delta == pytest.approx(1 / 6) and s.readers_split is False
    assert s.levels_agree is False and s.representative.p == pytest.approx(7 / 12) and s.representative.level == 3
    assert j.question_set.source == "generated" and j.question_set.planner == "planner"
    assert j.cost.calibration_calls == 3 * N * 2


def test_default_planner_and_verifiers_are_the_real_readers():
    def both(messages, schema, sample, version):
        if "axes" in schema["properties"]:
            return plan_json()
        if "orientation" in schema["properties"]:
            c = re.search(r"記述: 「(.*)」", messages[-1]["content"]).group(1)
            return json.dumps({"orientation": "反証" if c.endswith("しなかった") else "支持"}, ensure_ascii=False)
        if "compatible" in schema["properties"]:
            return json.dumps({"compatible": "両立しない"}, ensure_ascii=False)
        return reader("x", 4)._script(messages, schema, sample, version)
    a, b = ScriptedReader("a", both), ScriptedReader("b", both)
    j = asyncio.run(judge(TEXT, PROP, Probability(), readers=[a, b, FakeReader("all_yes")]))
    assert j.question_set.planner == "a"
    assert j.question_set.crosscheck.verifiers == ["a", "b"] and j.question_set.crosscheck.flagged == []
    assert j.cost.crosscheck.live == N * 2 * 2 * 2 + N * 2


def test_missing_or_self_crosscheck_is_reported(monkeypatch):
    """単一モデル運用: 交差検証が効いていないことを黙って済ませない（測定 2026-09-23）。"""
    one = run(readers=[reader("r1", 4)], planner=planner(), budget=Budget())      # 検証役が 1 体
    assert any("交差検証をしていない" in n for n in one.notes) and one.question_set.crosscheck is None

    def both(messages, schema, sample, version):
        if "axes" in schema["properties"]:
            return plan_json()
        if "orientation" in schema["properties"]:
            c = re.search(r"記述: 「(.*)」", messages[-1]["content"]).group(1)
            return json.dumps({"orientation": "反証" if c.endswith("しなかった") else "支持"}, ensure_ascii=False)
        if "compatible" in schema["properties"]:
            return json.dumps({"compatible": "両立しない"}, ensure_ascii=False)
        return reader("x", 4)._script(messages, schema, sample, version)
    # 同じモデルを別名で 2 体の検証役・2 体の読み手として差す（名前は分かれるが model は同じ）
    def solo(n):
        return ScriptedReader(f"qwen:4b#{n}", both, model="qwen:4b")
    self_cc = asyncio.run(judge(TEXT, PROP, Probability(), readers=[solo("a"), solo("b")], planner=solo("plan"),
                                verifiers=[solo("v1"), solo("v2")], budget=Budget()))
    assert self_cc.question_set.crosscheck is not None
    assert any("検証役が生成器と同じモデル" in n for n in self_cc.notes)
    assert any("読み手が全部同じモデル" in n for n in self_cc.notes)
    # 別のモデルなら言わない
    two = asyncio.run(judge(TEXT, PROP, Probability(), readers=[ScriptedReader("m1", both), ScriptedReader("m2", both)],
                            planner=ScriptedReader("m3", both), budget=Budget()))
    assert two.notes == []


def test_flagged_axes_are_neither_answered_nor_aggregated():
    """交差検証で外した軸は回答も集約もしない（変異試験で見つかった穴）。"""
    def verifier(messages, schema, sample, version):
        if "orientation" in schema["properties"]:
            c = re.search(r"記述: 「(.*)」", messages[-1]["content"]).group(1)
            if c == "甲は0番目の悪事をした":
                return json.dumps({"orientation": "反証"}, ensure_ascii=False)   # 生成器の向きと反対
            return json.dumps({"orientation": "反証" if c.endswith("しなかった") else "支持"}, ensure_ascii=False)
        return json.dumps({"compatible": "両立しない"}, ensure_ascii=False)
    j = asyncio.run(judge(TEXT, PROP, Probability(), readers=[reader("r1", 4)], planner=planner(),
                          verifiers=[ScriptedReader("v1", verifier), ScriptedReader("v2", verifier)]))
    assert j.question_set.crosscheck.flagged == ["a01"] and "a01" not in j.question_set.active_ids
    assert {a.axis_id for a in j.matrices[0].answers} == {f"a{i:02d}" for i in range(2, N + 1)}
    r = j.readings["r1"]
    assert r.counts.n == N - 1 and [x.axis_id for x in r.axes] == [f"a{i:02d}" for i in range(2, N + 1)]
    assert r.p == pytest.approx(3 / 5)


def test_given_question_set_skips_generation():
    j = run(readers=[reader("r1", 4)], planner=planner())
    p2 = planner()
    k = run(readers=[reader("r1", 2)], planner=p2, question_set=j.question_set)
    assert p2.calls == [] and k.question_set.source == "given"
    assert [a.claim_support for a in k.question_set.axes] == [a.claim_support for a in j.question_set.axes]
    assert k.readings["r1"].p == pytest.approx(2 / 6)


def test_s8_cost_and_cache(tmp_path):
    """S8: 軸 6・読み手 2・標本 1・交差検証なし → 生成 1 ＋ 回答 24。キャッシュ越しの 2 回目は実 0。"""
    cache = tmp_path / "c.jsonl"
    j = run(readers=[CachedPort(reader("r1", 4), cache), CachedPort(reader("r2", 3), cache)],
            planner=CachedPort(planner(), cache))
    assert (j.cost.plan.live, j.cost.answer.live, j.cost.live, j.cost.cached) == (1, N * 2 * 2, 25, 0)
    k = run(readers=[CachedPort(reader("r1", 4), cache), CachedPort(reader("r2", 3), cache)],
            planner=CachedPort(planner(), cache))
    assert (k.cost.live, k.cost.cached) == (0, 25)
    assert k.readings["r1"].p == j.readings["r1"].p


def test_samples_and_workers():
    j = run(readers=[reader("r1", 4)], planner=planner(), budget=Budget(crosscheck=False, samples=3, workers=5))
    assert j.readings["r1"].diagnostics.p_by_sample == [pytest.approx(4 / 6)] * 3
    assert len(j.matrices[0].answers) == N * 2 * 3


# ---------------------------------------------------------------- 記録と replay

def test_record_and_replay(tmp_path):
    path = tmp_path / "rec" / "records.jsonl"
    j = run(readers=[reader("r1", 4), reader("r2", 3), FakeReader("all_yes")], planner=planner(), record_path=path)
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["schema_version"] == 2 and rec["input"]["units"][0] == {"id": "s1", "text": "甲は朝に家を出た。"}
    assert rec["question_set"]["attempts"][0]["try"] == 0
    assert rec["matrices"][0]["answers"][0]["raw"] and rec["matrices"][0]["answers"][0]["verdict"] == "STATES"
    assert rec["readings"]["r1"]["label"] == "SPLIT" and rec["cost"]["live"] == 1 + N * 2 * 2
    r = replay(rec)
    for name in j.readings:
        a, b = j.readings[name], r.readings[name]
        assert (a.p, a.w, a.label, a.value) == (b.p, b.w, b.label, b.value)
    assert r.summary == j.summary and r.cost.live == 0
    # 閾値を変えて引き直す（ω を上げると割れるが偏りになる）
    r2 = replay(rec, thresholds=Thresholds(omega=0.7))
    assert r2.readings["r1"].label == Label.LEAN_SUPPORT
    # 生応答から L2 を引き直しても同じ
    r3 = replay(rec, reparse=True)
    assert r3.readings["r1"].p == j.readings["r1"].p
    # 型を変えて引き直す
    assert replay(rec, output=Probability()).readings["r1"].value.level is None


def test_judge_sync():
    j = judge_sync(TEXT, PROP, Probability(), readers=[reader("r1", 4)], planner=planner(),
                   budget=Budget(crosscheck=False))
    assert j.readings["r1"].value.p == pytest.approx(4 / 6)


def test_single_reader_note():
    j = run(readers=[reader("r1", 4)], planner=planner())
    assert j.summary.delta is None and j.summary.note == "single reader"


# ---------------------------------------------------------------- Q6: コアは標準ライブラリだけ

def test_core_imports_only_the_standard_library():
    pkg = Path(sd.__file__).resolve().parent
    bad = []
    for f in pkg.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            for n in names:
                top = n.split(".")[0]
                if top not in sys.stdlib_module_names and top != "__future__":
                    bad.append(f"{f.name}: {n}")
    assert bad == []
