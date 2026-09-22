"""L1 問い生成（実装設計 §4.1）。LLM は呼ばない（台本の読み手）。"""
from __future__ import annotations

import asyncio
import json

import pytest

from structural_distillation import l1
from structural_distillation.contracts import Budget, PlanningFailed
from structural_distillation.l0 import Meter
from structural_distillation.prompts import PromptSet
from structural_distillation.units import segment

from conftest import ScriptedReader

P = PromptSet.builtin("ja")
PROP = "ゲンは悪人である"
UNITS = segment("ゲンは船員を助けた。ゲンは反物を売った。")


def ax(name, s, r):
    return {"axis": name, "claim_support": s, "claim_refute": r}


GOOD = [ax(f"軸{i}", f"ゲンは{i}番目の行いで人を傷つけた", f"ゲンは{i}番目の行いで人を傷つけなかった") for i in range(6)]


def run(c):
    return asyncio.run(c)


# ---------------------------------------------------------------- ハーネス検査（各規則を 1 件ずつ破る）

def test_good_plan_has_no_violation():
    assert l1.check_harness(GOOD, PROP, 6, 6) == []


def test_h6_axis_count():
    v = l1.check_harness(GOOD[:5], PROP, 6, 6)
    assert v == ["H6 軸数: 5 (期待 6)"]
    assert l1.check_harness(GOOD[:5], PROP, 4, 6) == []
    assert l1.check_harness(GOOD[:3], PROP, 4, 6) == ["H6 軸数: 3 (期待 4〜6)"]


def test_h1_empty_side_skips_the_other_checks_for_that_axis():
    bad = GOOD[:5] + [ax("空", "  ", "ゲンは悪人である")]   # 反証側は命題の言い換えだが、空の軸は他を検査しない
    assert l1.check_harness(bad, PROP, 6, 6) == ["H1 空の記述: 空"]


def test_h1_identical_sides():
    bad = GOOD[:5] + [ax("同", "ゲンは嘘をついた。", "ゲンは 嘘をついた")]
    # 空撃ちと同じく、同一の両側は H4（同じ記述の 2 回目）にも当たる
    assert l1.check_harness(bad, PROP, 6, 6) == ["H1 両側が同一: ゲンは嘘をついた。", "H4 非重複: ゲンは 嘘をついた"]


def test_h4_duplicate_across_axes():
    bad = GOOD[:5] + [ax("重", GOOD[0]["claim_support"], "別の記述")]
    assert l1.check_harness(bad, PROP, 6, 6) == [f"H4 非重複: {GOOD[0]['claim_support']}"]


def test_h3_paraphrase_of_the_proposition():
    bad = GOOD[:5] + [ax("言", "ゲンは悪人である", "ゲンは善人であると村人が言った")]
    assert l1.check_harness(bad, PROP, 6, 6) == ["H3 非自明（命題の言い換え）: ゲンは悪人である"]


@pytest.mark.parametrize("q", ["ゲンは盗んだか", "ゲンは盗んだ？", "ゲンは盗んだ?"])
def test_h11_question_form(q):
    bad = GOOD[:5] + [ax("問", q, "ゲンは何も盗まなかった")]
    assert l1.check_harness(bad, PROP, 6, 6) == [f"H11 疑問文: {q}"]


# ---------------------------------------------------------------- 試行

def test_plan_accepts_first_valid_attempt_and_keeps_claims_verbatim():
    raw = [dict(a) for a in GOOD]
    raw[0]["claim_support"] = " 前後に空白 "   # 検査は strip して見るが、記述は返されたまま残す（鍵に入る）
    r = ScriptedReader("planner", [json.dumps({"axes": raw}, ensure_ascii=False)])
    qs = run(l1.plan(r, UNITS, PROP, budget=Budget(crosscheck=False), prompts=P))
    assert [a.id for a in qs.axes] == [f"a{i:02d}" for i in range(1, 7)]
    assert qs.axes[0].claim_support == " 前後に空白 " and qs.axes[0].origin == "initial"
    assert qs.active_ids == [a.id for a in qs.axes] and qs.crosscheck is None and qs.source == "generated"
    assert qs.planner == "planner" and qs.prompt.plan == "p3" and qs.retries == 0
    assert r.calls[0]["version"] == "p3" and r.calls[0]["sample"] == 0


def test_plan_moves_to_next_attempt_on_failure_and_violation():
    replies = [None, json.dumps({"axes": GOOD[:4]}, ensure_ascii=False), json.dumps({"axes": GOOD}, ensure_ascii=False)]
    r = ScriptedReader("planner", replies)
    m = Meter()
    qs = run(l1.plan(r, UNITS, PROP, budget=Budget(crosscheck=False), prompts=P, meter=m))
    assert [c["sample"] for c in r.calls] == [0, 1, 2]           # 再送ではなく次の試行（I18）
    assert [c["version"] for c in r.calls] == ["p3", "p3", "p3"]
    assert qs.attempts[0].violations == ("L0 失敗",) and qs.attempts[0].error
    assert qs.attempts[1].violations == ("H6 軸数: 4 (期待 6)",)
    assert qs.attempts[2].violations == () and qs.retries == 2
    assert all(a.origin == "retry" for a in qs.axes)
    assert m.cost.plan.live == 3


def test_plan_fails_after_retries():
    r = ScriptedReader("planner", [json.dumps({"axes": GOOD[:2]}, ensure_ascii=False)])
    with pytest.raises(PlanningFailed) as e:
        run(l1.plan(r, UNITS, PROP, budget=Budget(crosscheck=False, plan_retries=1), prompts=P))
    assert len(e.value.attempts) == 2 and len(r.calls) == 2


# ---------------------------------------------------------------- 交差検証（H12）

def verifier(name, table):
    """table: 記述 → 向きの語（元の並び, 反転の並び）。表に無い記述は「傷つけた」なら支持、それ以外は反証（生成器と同意）。
    排他性は、記述に「並存」を含む対だけ「両立しうる」。"""
    def script(messages, schema, sample, version):
        user = messages[-1]["content"]
        if "orientation" in schema["properties"]:
            for claim, (a, b) in table.items():
                if f"記述: 「{claim}」" in user:
                    return json.dumps({"orientation": a if sample == 0 else b}, ensure_ascii=False)
            return json.dumps({"orientation": "支持" if "傷つけた" in user else "反証"}, ensure_ascii=False)
        return json.dumps({"compatible": "両立しうる" if "並存" in user else "両立しない"}, ensure_ascii=False)
    return ScriptedReader(name, script)


def plan_with(verifiers, axes=GOOD):
    r = ScriptedReader("planner", [json.dumps({"axes": axes}, ensure_ascii=False)])
    return run(l1.plan(r, UNITS, PROP, budget=Budget(), prompts=P, verifiers=verifiers))


def test_crosscheck_flags_axis_when_majority_of_verifiers_oppose():
    c0 = GOOD[0]["claim_support"]
    v1 = verifier("v1", {c0: ("反証", "反証")})
    v2 = verifier("v2", {c0: ("反証", "反証")})
    qs = plan_with([v1, v2])
    assert qs.crosscheck.flagged == ["a01"] and "a01" not in qs.active_ids and len(qs.active_ids) == 5
    assert qs.crosscheck.verifiers == ["v1", "v2"]
    # 軸 × 側 × 検証役 × 並び 2 版 ＋ 排他性 軸 × 検証役
    assert len(v1.calls) == 6 * 2 * 2 + 6
    assert sorted({c["sample"] for c in v1.calls if "orientation" in c["schema"]["properties"]}) == [0, 1]
    assert {c["version"] for c in v1.calls} == {"xc2"}


def test_crosscheck_one_opposing_verifier_is_not_a_majority():
    c0 = GOOD[0]["claim_support"]
    qs = plan_with([verifier("v1", {c0: ("反証", "反証")}), verifier("v2", {})])
    assert qs.crosscheck.flagged == [] and len(qs.active_ids) == 6


def test_crosscheck_split_versions_are_a_tie_not_opposition():
    c0 = GOOD[0]["claim_support"]
    qs = plan_with([verifier("v1", {c0: ("反証", "支持")}), verifier("v2", {c0: ("反証", "支持")})])
    assert qs.crosscheck.flagged == []
    vote = qs.crosscheck.votes[0]["checkers"]["v1"]["support"]
    assert vote["votes"] == ["REFUTE", "SUPPORT"] and vote["majority"] == "TIE"


def test_crosscheck_weak_and_nonexclusive_are_diagnostics_only():
    c1 = GOOD[1]["claim_refute"]
    axes = [dict(a) for a in GOOD]
    axes[2] = ax("並", "ゲンは並存の場で人を傷つけた", "ゲンは並存の場で人を傷つけなかった")
    qs = plan_with([verifier("v1", {c1: ("どちらとも言えない", "反証")}), verifier("v2", {})], axes)
    assert qs.crosscheck.weak == ["a02"] and qs.crosscheck.nonexclusive == ["a03"]
    assert len(qs.active_ids) == 6


def test_crosscheck_needs_two_verifiers():
    qs = plan_with([verifier("v1", {})])
    assert qs.crosscheck is None and len(qs.active_ids) == 6


def test_crosscheck_failed_votes_are_dropped():
    def broken(messages, schema, sample, version):
        return None
    qs = plan_with([ScriptedReader("v1", broken), ScriptedReader("v2", broken)])
    assert qs.crosscheck.flagged == [] and qs.crosscheck.votes[0]["checkers"]["v1"]["support"]["majority"] is None
