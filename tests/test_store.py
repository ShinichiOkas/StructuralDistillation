"""問いの保存庫（store.py）と judge(question_store=…)。台本の読み手だけ（LLM は呼ばない）。

師匠（2026-09-23）: データをJSONで保持して同じ命題と本文の場合は過去に作成した問を再利用できる仕組みにして。
"""
from __future__ import annotations

import asyncio
import json
import re

import pytest

from structural_distillation import judge
from structural_distillation.contracts import Budget, Label, PlanningFailed, Probability, StoreError
from structural_distillation.l0 import FakeReader
from structural_distillation.store import QuestionStore, question_key
from structural_distillation.units import rule_version, segment

from conftest import ScriptedReader
from test_judge import N, PROP, TEXT, plan_json, planner, reader

RULE = rule_version("ja-sentence")


def run(store, *, prop=PROP, text=TEXT, gen=None, readers=None, **kw):
    kw.setdefault("budget", Budget(crosscheck=False))
    return asyncio.run(judge(text, prop, Probability(), readers=readers or [reader("r1", 4)],
                             planner=gen or planner(), question_store=store, **kw))


# ---------------------------------------------------------------- 鍵

def test_key_ignores_whitespace_around_sentences_but_not_the_words():
    a = question_key(segment(TEXT), RULE, PROP)
    spaced = TEXT.replace("。", "。\n  ")
    assert question_key(segment(spaced), RULE, " " + PROP + " ") == a
    assert question_key(segment(TEXT), RULE, PROP + "。") != a                     # 命題の 1 文字違いは別
    assert question_key(segment(TEXT.replace("財布", "鞄")), RULE, PROP) != a       # 本文の違いは別
    assert question_key(segment(TEXT), rule_version("paragraph"), PROP) != a      # 単位化規則の違いは別


# ---------------------------------------------------------------- 再利用

def test_second_judgment_reuses_the_questions_without_calling_generator_or_verifiers(tmp_path):
    first = run(tmp_path)
    key = question_key(segment(TEXT), RULE, PROP)
    assert first.question_set.source == "generated" and first.question_set.store_key == key
    assert (tmp_path / f"{key}.json").exists() and first.cost.plan.live == 1
    gen2 = planner()
    second = run(tmp_path, gen=gen2, readers=[reader("r1", 4), reader("r2", 2)])
    assert gen2.calls == [] and second.cost.plan.live == 0 and second.cost.crosscheck.live == 0
    assert second.question_set.source == "stored" and second.question_set.store_key == key
    assert [a.claim_support for a in second.question_set.axes] == [a.claim_support for a in first.question_set.axes]
    assert second.readings["r1"].p == first.readings["r1"].p and second.readings["r2"].p == pytest.approx(2 / 6)
    assert second.cost.answer.live == N * 2 * 2   # 答えは読み手ごとに取る


def test_different_proposition_or_text_makes_new_questions(tmp_path):
    run(tmp_path)
    g = planner()
    run(tmp_path, prop="甲は正直である", gen=g)
    assert len(g.calls) == 1
    g2 = planner()
    run(tmp_path, text=TEXT + "甲は翌日も駅に行った。", gen=g2)
    assert len(g2.calls) == 1
    assert len(QuestionStore(tmp_path).entries()) == 3


def test_crosscheck_result_is_stored_and_reused_as_is(tmp_path):
    """交差検証で外した軸も含めて同じ問いの集合を返す（合意 Q4）。検証役は呼ばない。"""
    def verifier(messages, schema, sample, version):
        if "orientation" in schema["properties"]:
            c = re.search(r"記述: 「(.*)」", messages[-1]["content"]).group(1)
            if c == "甲は0番目の悪事をした":
                return json.dumps({"orientation": "反証"}, ensure_ascii=False)
            return json.dumps({"orientation": "反証" if c.endswith("しなかった") else "支持"}, ensure_ascii=False)
        return json.dumps({"compatible": "両立しない"}, ensure_ascii=False)
    v1, v2 = ScriptedReader("v1", verifier), ScriptedReader("v2", verifier)
    first = run(tmp_path, verifiers=[v1, v2], budget=Budget())
    assert first.question_set.crosscheck.flagged == ["a01"]
    w1, w2 = ScriptedReader("v1", verifier), ScriptedReader("v2", verifier)
    second = run(tmp_path, verifiers=[w1, w2], budget=Budget())
    assert w1.calls == [] and w2.calls == [] and second.cost.crosscheck.live == 0
    assert second.question_set.active_ids == first.question_set.active_ids
    assert second.question_set.crosscheck.flagged == ["a01"]


def test_reuse_needs_no_generator(tmp_path):
    """保存庫に当たれば、生成器になれる読み手がいなくても判定できる。"""
    run(tmp_path)
    j = asyncio.run(judge(TEXT, PROP, Probability(), readers=[FakeReader("all_yes")], question_store=tmp_path))
    assert j.question_set.source == "stored" and j.readings["fake:all_yes"].label == Label.INSTRUMENT_FAULT


def test_regenerate_keeps_the_old_file(tmp_path):
    first = run(tmp_path)
    g = ScriptedReader("planner2", [plan_json().replace("悪事", "善行")])
    again = run(tmp_path, gen=g, regenerate=True)
    assert len(g.calls) == 1 and again.question_set.source == "generated"
    assert "善行" in again.question_set.axes[0].claim_support
    key = first.question_set.store_key
    old = [p for p in tmp_path.iterdir() if ".superseded-" in p.name]
    assert len(old) == 1 and "悪事" in old[0].read_text(encoding="utf-8")
    assert [e["key"] for e in QuestionStore(tmp_path).entries()] == [key]      # 一覧に古いものは出ない
    third = run(tmp_path, gen=planner())
    assert "善行" in third.question_set.axes[0].claim_support                 # 以後は作り直した方を再利用


def test_given_question_set_does_not_touch_the_store(tmp_path):
    j = run(tmp_path / "a")
    store = tmp_path / "b"
    k = asyncio.run(judge(TEXT, PROP, Probability(), readers=[reader("r1", 4)], question_set=j.question_set,
                          question_store=store))
    assert k.question_set.source == "given" and not store.exists()


def test_planning_failure_saves_nothing(tmp_path):
    with pytest.raises(PlanningFailed):
        run(tmp_path, gen=ScriptedReader("planner", [plan_json(2)]))
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_record_says_the_questions_were_reused(tmp_path):
    rec = tmp_path / "records.jsonl"
    run(tmp_path / "q", record_path=rec)
    run(tmp_path / "q", record_path=rec)
    lines = [json.loads(x) for x in rec.read_text(encoding="utf-8").splitlines()]
    assert [x["question_set"]["source"] for x in lines] == ["generated", "stored"]
    assert lines[0]["question_set"]["store_key"] == lines[1]["question_set"]["store_key"]


# ---------------------------------------------------------------- ファイル

def test_file_is_readable_json_with_the_text_and_questions(tmp_path):
    j = run(tmp_path)
    d = json.loads((tmp_path / f"{j.question_set.store_key}.json").read_text(encoding="utf-8"))
    assert d["schema_version"] == 1 and d["proposition"] == PROP and d["units_rule"] == RULE
    assert d["units"][0] == {"id": "s1", "text": "甲は朝に家を出た。"}
    assert d["question_set"]["axes"][0]["claim_support"] == "甲は0番目の悪事をした"
    assert d["question_set"]["planner"] == "planner" and d["created_at"]


def test_hand_edited_questions_are_reused(tmp_path):
    """保存した問いを人が直したら、直した問いで判定する（規則の検査はしない。合意の既知リスク）。"""
    j = run(tmp_path)
    p = tmp_path / f"{j.question_set.store_key}.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["question_set"]["axes"][0]["claim_support"] = "甲は0番目の悪事を二度した"
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    k = run(tmp_path)
    assert k.question_set.axes[0].claim_support == "甲は0番目の悪事を二度した"


@pytest.mark.parametrize("breakage", ["broken json", "schema", "key", "text", "dup"])
def test_unreadable_file_stops_without_overwriting(tmp_path, breakage):
    j = run(tmp_path)
    p = tmp_path / f"{j.question_set.store_key}.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    if breakage == "broken json":
        body = "{壊れている"
    else:
        if breakage == "schema":
            d["schema_version"] = 99
        elif breakage == "key":
            d["key"] = "0" * 40
        elif breakage == "text":
            d["units"][0]["text"] = "別の本文。"
        else:
            d["question_set"]["active_ids"].append(d["question_set"]["active_ids"][0])
        body = json.dumps(d, ensure_ascii=False)
    p.write_text(body, encoding="utf-8")
    with pytest.raises(StoreError):
        run(tmp_path)
    assert p.read_text(encoding="utf-8") == body


def test_entries_and_resolve(tmp_path):
    s = QuestionStore(tmp_path)
    assert s.entries() == []
    j = run(tmp_path)
    (tmp_path / "junk.json").write_text("{", encoding="utf-8")
    es = s.entries()
    good = [e for e in es if not e["error"]]
    assert len(good) == 1 and good[0]["n_axes"] == N and good[0]["proposition"] == PROP
    assert [e for e in es if e["error"]][0]["key"] == "junk"
    assert s.resolve(j.question_set.store_key[:8]) == j.question_set.store_key
    with pytest.raises(StoreError):
        s.resolve("zzzz")
