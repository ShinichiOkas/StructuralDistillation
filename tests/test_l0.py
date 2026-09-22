"""L0 読み手ポート（実装設計 §4.0）。LLM は呼ばない（台本の読み手と偽読み手）。"""
from __future__ import annotations

import asyncio
import json

import pytest

from structural_distillation import l0, prompts
from structural_distillation.l0 import CachedPort, FakeReader, Meter, RawReply, cache_key, strip_fence, structured, validate, with_schema
from structural_distillation.prompts import PromptSet
from structural_distillation.units import render, segment

from conftest import ScriptedReader

P = PromptSet.builtin("ja")
NOTICE = P.schema_notice
ANSWER_SCHEMA = prompts.answer_schema(P)


def test_answer_schema_is_the_measured_one():
    """回答スキーマの挿入順と enum の並びは空撃ちと同一（本文に埋め込まれるので鍵に効く）。"""
    assert json.dumps(ANSWER_SCHEMA, ensure_ascii=False) == (
        '{"type": "object", "properties": {"verdict": {"type": "string", "enum": ["述べている", "否定している", "触れていない"]}, '
        '"evidence": {"type": "array", "items": {"type": "string"}}}, "required": ["verdict", "evidence"]}')


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 純関数

def test_strip_fence_only_whole_single_fence():
    assert strip_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fence('  ```\n{"a": 1}\n```  ') == '{"a": 1}'
    assert strip_fence('{"a": 1}') == '{"a": 1}'
    # 部分抽出（修復）はしない
    assert strip_fence('前置き\n```json\n{"a": 1}\n```') == '前置き\n```json\n{"a": 1}\n```'


def test_with_schema_appends_notice_and_schema_to_last_user_only():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    out = with_schema(msgs, {"b": 1, "a": 2}, NOTICE)
    assert out[0] == {"role": "system", "content": "S"}
    # sort_keys 無し・既定のセパレータ（dict の挿入順がそのまま出る）
    assert out[1]["content"] == "U" + NOTICE + '{"b": 1, "a": 2}'
    assert msgs[1]["content"] == "U"  # 元を壊さない


@pytest.mark.parametrize("obj,ok", [
    ({"verdict": "述べている", "evidence": ["s1"]}, True),
    ({"verdict": "触れていない", "evidence": []}, True),
    ({"verdict": "たぶん", "evidence": []}, False),           # enum の外
    ({"verdict": "述べている"}, False),                        # 必須欠落（空撃ちは [] として受理した。差分表 P3）
    ({"verdict": "述べている", "evidence": [3]}, False),       # 型違い
    ({"verdict": "述べている", "evidence": "s1"}, False),
    (["述べている"], False),
    ("散文", False),
])
def test_validate_subset(obj, ok):
    assert (validate(obj, ANSWER_SCHEMA) == []) is ok


def test_validate_types():
    assert validate(3, {"type": "integer"}) == []
    assert validate(True, {"type": "integer"}) != []   # bool は整数として扱わない
    assert validate(2.5, {"type": "number"}) == []
    assert validate(True, {"type": "boolean"}) == []
    assert validate({"x": {"y": [1, "a"]}}, {"type": "object", "properties": {"x": {"type": "object", "properties": {
        "y": {"type": "array", "items": {"type": "integer"}}}}}}) != []


def test_cache_key_matches_the_measurement_cache(cache_cases):
    """鍵が測定 2 周目のキャッシュ行と一致する（実装判断 I3・適合検査の前提）。指示の逐語移植もここで固定される。"""
    for c in cache_cases:
        if c["role"] == "plan":
            msgs = P.plan_messages(render(segment(c["text"])), c["proposition"], c["n_axes"])
            schema = prompts.plan_schema()
        elif c["role"] == "answer":
            msgs = P.answer_messages(render(segment(c["text"])), c["claim"])
            schema = prompts.answer_schema(P)
        elif c["role"] == "orient":
            msgs = P.orient_messages(c["proposition"], c["claim"], c["reversed"])
            schema = prompts.orient_schema(P)
        else:
            msgs = P.exclusive_messages(c["claim_a"], c["claim_b"])
            schema = prompts.exclusive_schema(P)
        key = cache_key(c["model"], with_schema(msgs, schema, NOTICE), schema, c["sample"], c["version"])
        assert key == c["key"], c["role"]


# ---------------------------------------------------------------- structured（三段構え）

def test_structured_accepts_fenced_and_counts():
    r = ScriptedReader("m", ['```json\n{"verdict": "述べている", "evidence": ["s1"]}\n```'])
    s = run(structured(r, [{"role": "user", "content": "U"}], ANSWER_SCHEMA, version="p2", sample=0, notice=NOTICE))
    assert s.ok and s.obj == {"verdict": "述べている", "evidence": ["s1"]}
    assert (s.live, s.cached, s.attempts) == (1, 0, 1)
    # 読み手に渡したのはスキーマ明記済みの指示と、版・標本
    assert r.calls[0]["messages"][0]["content"].startswith("U" + NOTICE)
    assert r.calls[0]["version"] == "p2" and r.calls[0]["sample"] == 0


@pytest.mark.parametrize("bad", ["散文です", '{"verdict": "たぶん", "evidence": []}', '{"verdict": "述べている"}', None, "[1, 2]"])
def test_structured_never_turns_garbage_into_a_value(bad):
    """S2: 壊れた応答は再送後も駄目なら ok=False。型の外の値に化けない。"""
    r = ScriptedReader("m", [bad, bad])
    s = run(structured(r, [{"role": "user", "content": "U"}], ANSWER_SCHEMA, version="p2", sample=0, notice=NOTICE, retry=True))
    assert not s.ok and s.obj is None and s.error
    assert [c["version"] for c in r.calls] == ["p2", "p2:retry"]


def test_structured_retry_recovers_and_retry_off_does_not_resend():
    good = '{"verdict": "触れていない", "evidence": []}'
    r = ScriptedReader("m", ["散文", good])
    s = run(structured(r, [{"role": "user", "content": "U"}], ANSWER_SCHEMA, version="p2", sample=1, notice=NOTICE, retry=True))
    assert s.ok and s.attempts == 2 and s.version == "p2:retry"
    r2 = ScriptedReader("m", ["散文", good])
    s2 = run(structured(r2, [{"role": "user", "content": "U"}], ANSWER_SCHEMA, version="p3", sample=0, notice=NOTICE))
    assert not s2.ok and len(r2.calls) == 1   # 生成と交差検証は再送しない（I18）


# ---------------------------------------------------------------- CachedPort

def test_cached_port_hit_append_and_failures_not_written(tmp_path):
    path = tmp_path / "c.jsonl"
    inner = ScriptedReader("m", ['{"verdict": "述べている", "evidence": ["s1"]}', None])
    port = CachedPort(inner, path)
    msgs = [{"role": "user", "content": "U"}]
    a = run(port.complete(msgs, ANSWER_SCHEMA, sample=0, version="p2"))
    b = run(port.complete(msgs, ANSWER_SCHEMA, sample=0, version="p2"))
    assert a.ok and not a.meta["cached"] and b.meta["cached"] and b.content == a.content
    assert (port.calls_live, port.calls_cached) == (1, 1) and len(inner.calls) == 1
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert set(row) == {"key", "model", "sample", "version", "payload"} and row["payload"]["ok"] is True
    assert row["key"] == a.meta["key"]
    # 失敗は書かない（I19）
    c = run(port.complete(msgs, ANSWER_SCHEMA, sample=1, version="p2"))
    assert not c.ok and len(path.read_text(encoding="utf-8").splitlines()) == 1
    # 書いたものは次のインスタンスでも読める
    port2 = CachedPort(ScriptedReader("m", []), path, cache_only=True)
    assert run(port2.complete(msgs, ANSWER_SCHEMA, sample=0, version="p2")).meta["cached"]


def test_cached_port_reads_measurement_rows_read_only(tmp_path, cache_cases):
    c = cache_cases[1]
    src = tmp_path / "base.jsonl"
    src.write_text(json.dumps({"key": c["key"], "model": c["model"], "sample": c["sample"], "version": c["version"],
                               "payload": {"ok": True, "content": c["content"], "error": None}}, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    before = src.read_bytes()
    port = CachedPort(ScriptedReader(c["model"], ['{"verdict": "述べている", "evidence": ["s1"]}']), src, read_only=True)
    msgs = with_schema(P.answer_messages(render(segment("甲。")), "乙"), ANSWER_SCHEMA, NOTICE)
    r = run(port.complete(msgs, ANSWER_SCHEMA, sample=0, version="p2"))   # 外れ → 呼ぶが書かない
    assert r.ok and src.read_bytes() == before


def test_cached_port_cache_only_miss_and_extra(tmp_path):
    extra = tmp_path / "x.jsonl"
    msgs = [{"role": "user", "content": "U"}]
    k = cache_key("m", msgs, ANSWER_SCHEMA, 0, "p2")
    extra.write_text(json.dumps({"key": k, "model": "m", "sample": 0, "version": "p2",
                                 "payload": {"ok": True, "content": "{}", "error": None}}) + "\n", encoding="utf-8")
    port = CachedPort(ScriptedReader("m", []), None, cache_only=True, extra=[extra])
    assert run(port.complete(msgs, ANSWER_SCHEMA, sample=0, version="p2")).meta["cached"]
    miss = run(port.complete(msgs, ANSWER_SCHEMA, sample=1, version="p2"))
    assert not miss.ok and miss.error == "cache-only miss" and miss.meta["cache_miss"] and port.calls_missed == 1


def test_cached_port_coalesces_concurrent_requests(tmp_path):
    class Slow:
        name, calibration = "m", False

        def __init__(self):
            self.n = 0

        async def complete(self, messages, schema, *, sample, version):
            self.n += 1
            await asyncio.sleep(0.01)
            return RawReply(True, "{}", None, {})
    inner = Slow()
    port = CachedPort(inner, tmp_path / "c.jsonl")
    msgs = [{"role": "user", "content": "U"}]

    async def go():
        return await asyncio.gather(*[port.complete(msgs, {}, sample=0, version="v") for _ in range(5)])
    outs = run(go())
    assert inner.n == 1 and all(o.ok for o in outs) and port.calls_live == 1 and port.calls_cached == 4


# ---------------------------------------------------------------- FakeReader

@pytest.mark.parametrize("kind,word,ev", [("all_yes", "述べている", ["s1"]), ("all_no", "否定している", ["s1"]),
                                          ("all_undetermined", "触れていない", [])])
def test_fake_reader_answers_the_answer_schema(kind, word, ev):
    f = FakeReader(kind)
    assert f.name == f"fake:{kind}" and f.calibration
    r = run(f.complete([{"role": "user", "content": "U"}], ANSWER_SCHEMA, sample=0, version="p2"))
    assert r.ok and json.loads(r.content) == {"verdict": word, "evidence": ev}
    assert not run(f.complete([], {"type": "object", "properties": {"axes": {}}}, sample=0, version="p3")).ok


def test_fake_random_is_deterministic_regardless_of_order():
    f = FakeReader("random", seed=3)
    msgs = [[{"role": "user", "content": f"U{i}"}] for i in range(30)]

    async def go(order):
        return {i: (await f.complete(msgs[i], ANSWER_SCHEMA, sample=0, version="p2")).content for i in order}
    a = run(go(range(30)))
    b = run(go(reversed(range(30))))
    assert a == b and len({json.loads(v)["verdict"] for v in a.values()}) == 3


def test_meter_counts_by_role_and_calibration():
    m = Meter()
    m.add("answer", l0.Structured(True, {}, "{}", None, 2, 1, 1, 0, "k", "p2:retry"), calibration=False)
    m.add("answer", l0.Structured(True, {}, "{}", None, 1, 1, 0, 0, "k", "p2"), calibration=True)
    assert (m.cost.answer.live, m.cost.answer.cached, m.cost.calibration_calls) == (1, 1, 1)
