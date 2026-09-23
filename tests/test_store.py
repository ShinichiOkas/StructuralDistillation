"""問いの保存庫（store.py）と judge(question_store=…)。台本の読み手だけ（LLM は呼ばない）。

師匠（2026-09-23）: データをJSONで保持して同じ命題と本文の場合は過去に作成した問を再利用できる仕組みにして。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone

import pytest

from structural_distillation import judge
from structural_distillation.contracts import (Budget, InputError, Label, PlanningFailed, Probability, RetryAction,
                                               StoreError)
from structural_distillation.l0 import CachedPort, FakeReader
from structural_distillation.store import QuestionStore, question_key
from structural_distillation.units import rule_version, segment

from conftest import ScriptedReader
from test_judge import N, PROP, TEXT, plan_json, planner, reader

RULE = rule_version("ja-sentence")


def run(store, *, prop=PROP, text=TEXT, gen=None, readers=None, **kw):
    kw.setdefault("budget", Budget(crosscheck=False))
    return asyncio.run(judge(text, prop, Probability(), readers=readers or [reader("r1", 4)],
                             planner=gen if gen is not None else planner(), question_store=store, **kw))


# ---------------------------------------------------------------- 鍵

def test_key_ignores_whitespace_around_sentences_but_not_the_words():
    a = question_key(segment(TEXT), RULE, PROP)
    spaced = TEXT.replace("。", "。\n  ")
    assert question_key(segment(spaced), RULE, " " + PROP + " ") == a
    assert question_key(segment(TEXT), RULE, PROP + "。") != a                     # 命題の 1 文字違いは別
    assert question_key(segment(TEXT.replace("財布", "鞄")), RULE, PROP) != a       # 本文の違いは別
    assert question_key(segment(TEXT), rule_version("paragraph"), PROP) != a      # 単位化規則の違いは別


# ---------------------------------------------------------------- 再利用

def test_second_judgment_reuses_the_questions_without_calling_the_generator(tmp_path):
    first = run(tmp_path)
    key = question_key(segment(TEXT), RULE, PROP)
    assert first.question_set.source == "generated" and first.question_set.store_key == key
    assert (tmp_path / f"{key}.json").exists() and first.cost.plan.live == 1
    gen2 = planner()
    second = run(tmp_path, gen=gen2, readers=[reader("r1", 4), reader("r2", 2)])
    assert gen2.calls == [] and second.cost.plan.live == 0 and second.cost.crosscheck.live == 0
    assert second.question_set.source == "stored" and second.question_set.store_key == key
    assert [a.__dict__ for a in second.question_set.axes] == [a.__dict__ for a in first.question_set.axes]
    assert second.question_set.active_ids == first.question_set.active_ids
    assert second.readings["r1"].p == first.readings["r1"].p and second.readings["r2"].p == pytest.approx(2 / 6)
    assert second.cost.answer.live == N * 2 * 2   # 答えは読み手ごとに取る
    assert second.notes == []                      # 条件が同じなら知らせることは無い


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


def test_regenerating_twice_in_the_same_clock_tick_keeps_both_old_sets(tmp_path):
    """受入 C1: 退けるファイルの名前が同じ時刻で衝突すると、前に退けた問いを黙って失っていた。"""
    fixed = datetime(2026, 9, 23, 1, 0, 0, tzinfo=timezone.utc)
    store = QuestionStore(tmp_path, clock=lambda: fixed)
    run(store, gen=ScriptedReader("g1", [plan_json().replace("悪事", "一回目")]))
    run(store, gen=ScriptedReader("g2", [plan_json().replace("悪事", "二回目")]), regenerate=True)
    run(store, gen=ScriptedReader("g3", [plan_json().replace("悪事", "三回目")]), regenerate=True)
    olds = sorted(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.superseded-*.json"))
    assert len(olds) == 2 and any("一回目" in t for t in olds) and any("二回目" in t for t in olds)
    assert "三回目" in (tmp_path / f"{question_key(segment(TEXT), RULE, PROP)}.json").read_text(encoding="utf-8")


def test_regenerate_asks_the_generator_again_even_through_the_cache(tmp_path):
    """受入 M3: 作り直しで同じ標本番号を使うと、生応答のキャッシュに当たって前と同じ問いが返っていた。"""
    def gen_script(messages, schema, sample, version):
        return plan_json().replace("悪事", f"標本{sample}の悪事")
    cache = tmp_path / "cache.jsonl"
    store = tmp_path / "q"
    first = run(store, gen=CachedPort(ScriptedReader("g", gen_script), cache))
    assert "標本0の" in first.question_set.axes[0].claim_support
    inner = ScriptedReader("g", gen_script)
    again = run(store, gen=CachedPort(inner, cache), regenerate=True)
    assert [c["sample"] for c in inner.calls] == [3] and again.cost.plan.live == 1   # 起点 ＝ 世代 1 × 3 試行
    assert "標本3の" in again.question_set.axes[0].claim_support
    assert any("前の問いの集合は" in n for n in again.notes)


def test_differences_from_the_stored_conditions_are_reported(tmp_path, caplog):
    """受入 M1: 生成器・軸数・交差検証の違いは、ログ（WARNING）と Judgment.notes（記録・CLI）に出す。"""
    run(tmp_path)                                                     # 生成器 planner・軸 6・交差検証なし
    other = ScriptedReader("other-planner", [plan_json()])
    v = [ScriptedReader("v1", lambda *a: None), ScriptedReader("v2", lambda *a: None)]
    caplog.clear()                                                     # 1 回目の生成でも注意は出る（読み手 1 体）
    with caplog.at_level("WARNING", logger="structural_distillation.compose"):
        j = run(tmp_path, gen=other, verifiers=v, budget=Budget(axes=8, crosscheck=True))
    text = " ".join(j.notes)
    assert "生成器 planner" in text and "other-planner" in text and "軸 6 本" in text and "交差検証していない" in text
    assert "読み手が 1 体" in text                                     # 受入 M13: 読み手 1 体も知らせる
    assert "この問いの集合を作った生成器は planner" in text            # 受入 2 回目 C-2: 作った生成器を名前で告げる
    assert other.calls == [] and v[0].calls == [] and len(caplog.records) == len(j.notes) == 5
    assert j.question_set.source == "stored"


def test_given_question_set_does_not_touch_the_store(tmp_path):
    j = run(tmp_path / "a")
    store = tmp_path / "b"
    k = asyncio.run(judge(TEXT, PROP, Probability(), readers=[reader("r1", 4)], question_set=j.question_set,
                          question_store=store))
    assert k.question_set.source == "given" and not store.exists()
    assert k.question_set.store_key is None      # 渡した問いは保存庫のものではない（受入 m4）


def test_regenerate_needs_a_store():
    with pytest.raises(InputError):
        asyncio.run(judge(TEXT, PROP, Probability(), readers=[reader("r1", 4)], planner=planner(), regenerate=True))


def test_unwritable_store_fails_before_spending_on_generation(tmp_path):
    """受入 m2: 置き場所がファイルだと、生成（LLM の費用）を済ませてから落ちていた。"""
    f = tmp_path / "not_a_dir"
    f.write_text("x", encoding="utf-8")
    g = planner()
    with pytest.raises(StoreError):
        run(f, gen=g)
    assert g.calls == []


def test_regenerate_that_fails_to_plan_keeps_the_current_set(tmp_path):
    j = run(tmp_path)
    p = tmp_path / f"{j.question_set.store_key}.json"
    before = p.read_text(encoding="utf-8")
    with pytest.raises(PlanningFailed):
        run(tmp_path, gen=ScriptedReader("bad", [plan_json(2)]), regenerate=True)
    assert p.read_text(encoding="utf-8") == before and list(tmp_path.glob("*.superseded-*")) == []


def test_reusing_a_set_with_no_active_axes_says_so(tmp_path):
    j = run(tmp_path)
    p = tmp_path / f"{j.question_set.store_key}.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    d["question_set"]["active_ids"] = []
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    k = run(tmp_path)
    assert k.readings["r1"].label == Label.INSTRUMENT_FAULT
    assert k.retry.action == RetryAction.REGENERATE and k.retry.details["store_key"] == j.question_set.store_key
    assert k.retry.details["generations"] == 1 and "regenerate=True" in k.retry.message
    assert not any("有効な軸が 0 本" in n for n in k.notes)   # 同じことを 2 つの欄で言わない（受入 m13）


def test_a_held_lock_is_waited_for_and_a_stale_one_is_broken(tmp_path):
    j = run(tmp_path)
    key = j.question_set.store_key
    lock = tmp_path / f"{key}.lock"
    lock.write_text("123", encoding="utf-8")
    busy = QuestionStore(tmp_path, lock_wait=0.2)
    with pytest.raises(StoreError):
        run(busy, gen=planner(), regenerate=True)
    assert lock.exists()                          # 他人のロックは外さない
    old = time.time() - 3600
    os.utime(lock, (old, old))
    run(QuestionStore(tmp_path, lock_stale=60), gen=planner(), regenerate=True)
    assert not lock.exists() and len(list(tmp_path.glob("*.superseded-*"))) == 1


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


@pytest.mark.parametrize("breakage", ["broken json", "schema", "key", "text", "proposition", "dup", "not in axes",
                                      "units not dicts", "attempts not dicts"])
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
        elif breakage == "proposition":
            d["proposition"] = "甲は正直である"
        elif breakage == "dup":
            d["question_set"]["active_ids"].append(d["question_set"]["active_ids"][0])
        elif breakage == "not in axes":
            d["question_set"]["active_ids"].append("a99")
        elif breakage == "units not dicts":
            d["units"] = ["甲は朝に家を出た。"]
        else:
            d["question_set"]["attempts"] = ["試行"]
        body = json.dumps(d, ensure_ascii=False)
    p.write_text(body, encoding="utf-8")
    with pytest.raises(StoreError):
        run(tmp_path)
    assert p.read_text(encoding="utf-8") == body


def test_entries_and_resolve(tmp_path):
    assert QuestionStore(tmp_path / "missing").entries() == []
    s = QuestionStore(tmp_path)
    assert s.entries() == []
    with pytest.raises(StoreError):
        s.resolve("*")                                   # glob の文字は受け付けない（受入 m13）
    j = run(tmp_path)
    (tmp_path / "junk.json").write_text("{", encoding="utf-8")
    es = s.entries()
    good = [e for e in es if not e["error"]]
    assert len(good) == 1 and good[0]["n_axes"] == N and good[0]["proposition"] == PROP
    assert [e for e in es if e["error"]][0]["key"] == "junk"
    assert s.resolve(j.question_set.store_key[:8]) == j.question_set.store_key
    with pytest.raises(StoreError):
        s.resolve("zzzz")
