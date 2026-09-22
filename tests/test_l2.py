"""L2 回答（実装設計 §4.2）。LLM は呼ばない（台本の読み手）。"""
from __future__ import annotations

import asyncio
import json
import random

from structural_distillation import l2
from structural_distillation.contracts import Axis, Budget, QuestionSet, Verdict
from structural_distillation.l0 import CachedPort, Meter, RawReply
from structural_distillation.prompts import PromptSet
from structural_distillation.units import segment

from conftest import ScriptedReader

P = PromptSet.builtin("ja")
UNITS = segment("甲は走った。乙は止まった。丙は笑った。")
IDS = {u.id for u in UNITS}


def qs(n=2, active=None):
    axes = [Axis(f"a{i:02d}", f"軸{i}", f"支持{i}", f"反証{i}") for i in range(1, n + 1)]
    return QuestionSet(axes=axes, active_ids=active or [a.id for a in axes], planner="p", prompt=P.version(),
                       budget=Budget())


def ans(verdict, ev):
    return json.dumps({"verdict": verdict, "evidence": ev}, ensure_ascii=False)


def run(c):
    return asyncio.run(c)


def test_normalize_ids_strips_brackets_spaces_and_quotes():
    assert l2.normalize_ids(["[s5]", " s2 ", "「s3」", "", "[ ]"]) == ["s5", "s2", "s3"]


def test_interpret_rules():
    v, raw, ev, ok, why = l2.interpret({"verdict": "述べている", "evidence": ["[s1]"]}, P, IDS)
    assert (v, ev, ok) == (Verdict.STATES, ["s1"], True) and raw == ["[s1]"]
    assert l2.interpret({"verdict": "否定している", "evidence": ["s2", "s3"]}, P, IDS)[3] is True
    # 述べている / 否定している は根拠が要り、すべて実在すること
    assert l2.interpret({"verdict": "述べている", "evidence": []}, P, IDS)[3] is False
    v, _, _, ok, why = l2.interpret({"verdict": "否定している", "evidence": ["s1", "s9"]}, P, IDS)
    assert v == Verdict.DENIES and not ok and why
    # 触れていない は根拠を要求しない
    assert l2.interpret({"verdict": "触れていない", "evidence": ["s9"]}, P, IDS)[3] is True


def test_answer_all_order_versions_and_raw():
    r = ScriptedReader("reader", lambda m, s, n, v: ans("述べている", ["s1"]) if "支持" in m[-1]["content"] else ans("触れていない", []))
    mx = run(l2.answer_all(r, UNITS, qs(), prompts=P, samples=2))
    assert [(a.axis_id, a.side, a.sample) for a in mx.answers] == [
        ("a01", "support", 0), ("a01", "support", 1), ("a01", "refute", 0), ("a01", "refute", 1),
        ("a02", "support", 0), ("a02", "support", 1), ("a02", "refute", 0), ("a02", "refute", 1)]
    assert [a.code for a in mx.answers[:4]] == ["STATES", "STATES", "SILENT", "SILENT"]
    assert mx.answers[0].raw == ans("述べている", ["s1"]) and mx.reader == "reader" and mx.samples == 2
    assert {c["version"] for c in r.calls} == {"p2"} and sorted({c["sample"] for c in r.calls}) == [0, 1]
    # 記述は指示に入る
    assert "## 記述\n支持1\n" in r.calls[0]["messages"][1]["content"]


def test_only_active_axes_are_answered():
    r = ScriptedReader("reader", [ans("触れていない", [])])
    mx = run(l2.answer_all(r, UNITS, qs(3, active=["a02"]), prompts=P, samples=1))
    assert {a.axis_id for a in mx.answers} == {"a02"} and len(r.calls) == 2


def test_failure_is_invalid_after_one_retry():
    r = ScriptedReader("reader", lambda m, s, n, v: None if v == "p2" else "散文")
    m = Meter()
    mx = run(l2.answer_all(r, UNITS, qs(1), prompts=P, samples=1, meter=m))
    a = mx.answers[0]
    assert a.verdict is None and not a.valid and a.error and a.code == "INVALID"
    assert [c["version"] for c in r.calls[:2]] == ["p2", "p2:retry"]
    assert m.cost.answer.live == 4


def test_missing_evidence_field_is_invalid_not_empty():
    """空撃ちは evidence 欠落を [] として受理した。ライブラリは受理しない（差分表 P3）。"""
    r = ScriptedReader("reader", ['{"verdict": "触れていない"}'])
    a = run(l2.answer_all(r, UNITS, qs(1), prompts=P, samples=1)).answers[0]
    assert not a.valid and a.verdict is None


def test_parallel_order_is_fixed_and_key_is_recorded(tmp_path):
    class Jitter:
        name, calibration = "reader", False

        async def complete(self, messages, schema, *, sample, version):
            await asyncio.sleep(random.random() / 200)
            return RawReply(True, ans("否定している", ["s2"]), None, {})
    port = CachedPort(Jitter(), tmp_path / "c.jsonl")

    async def go():
        return await l2.answer_all(port, UNITS, qs(4), prompts=P, samples=2, sem=asyncio.Semaphore(8))
    mx = run(go())
    assert [(a.axis_id, a.side, a.sample) for a in mx.answers] == sorted(
        [(a.axis_id, a.side, a.sample) for a in mx.answers], key=lambda t: (t[0], t[1] != "support", t[2]))
    assert all(a.key and not a.cached for a in mx.answers)
    again = run(l2.answer_all(port, UNITS, qs(4), prompts=P, samples=2))
    assert all(a.cached for a in again.answers)
