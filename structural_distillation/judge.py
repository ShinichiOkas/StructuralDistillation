"""合成（実装設計 §4.5）: 本文・命題・型 → 判定（読み手ごとの値と診断値・読み手間の要約・記録）。

変換の並びは依存関係だけで決まる: 入口の検査 → 単位化 → L1（問いの集合。渡されていれば飛ばす）→
読み手ごとに L2 → L3 → L4（読み手は 1 体ずつ、記述は workers 並列。I11）→ 読み手間の要約 → 記録。
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Sequence

from . import __version__, l1, l2, l3, l4
from .contracts import (AnswerMatrix, Budget, Cost, InputError, Judgment, Ordinal, OutputType, QuestionSet, Reading,
                        ReaderSummary, Thresholds, Unit, output_from_dict)
from .l0 import Meter, Reader
from .prompts import PromptSet, get_prompts
from .units import RULES, rule_version, segment

AGGREGATION_VERSION = "l3/v1"


def _check_input(text: str, proposition: str, output: OutputType, readers: Sequence[Reader], budget: Budget,
                 segmentation: str, question_set: QuestionSet | None) -> None:
    if not isinstance(text, str) or not text.strip():
        raise InputError("本文が空（F2）")
    if not isinstance(proposition, str) or not proposition.strip():
        raise InputError("命題が空（F2）")
    if len(text) > budget.max_chars:
        raise InputError(f"本文が {len(text)} 字で上限 {budget.max_chars} 字を超える（F1）。分割は判断を生成に委ねるので"
                         "ライブラリはしない")
    l4.check_output(output)
    if budget.axes < 1 or budget.lo < 1 or budget.lo > budget.hi:
        raise InputError(f"軸数の予算が不正: axes={budget.axes} 下限={budget.lo} 上限={budget.hi}（F2）")
    if budget.samples < 1 or budget.workers < 1 or budget.plan_retries < 0:
        raise InputError("標本数・並列数は 1 以上、再試行は 0 以上（F2）")
    if not readers:
        raise InputError("読み手が 0（F2）")
    names = [r.name for r in readers]
    if len(set(names)) != len(names):
        raise InputError(f"読み手の名前が重複している: {names}（記録と要約が読み手名で引くため）")
    if segmentation not in RULES:
        raise InputError(f"未知の単位化規則: {segmentation!r}")
    if question_set is not None:
        ids = {a.id for a in question_set.axes}
        if not set(question_set.active_ids) <= ids:
            raise InputError("問いの集合の active_ids が軸に無い")


def _summary(readings: dict[str, Reading], output: OutputType, t: Thresholds) -> ReaderSummary:
    s = l3.summarize_readers(list(readings.values()), t)
    rep = l4.type_value(s.representative.p, output) if s.representative else None
    levels_agree = None
    if isinstance(output, Ordinal) and len(s.readers) >= 2:
        levels = {readings[n].value.level for n in s.readers if readings[n].value}
        levels_agree = len(levels) == 1
    return replace(s, representative=rep, levels_agree=levels_agree)


def _read(matrix: AnswerMatrix, active_ids: list[str], retries: int, output: OutputType, t: Thresholds) -> Reading:
    r = l3.aggregate(matrix, active_ids, t, retries=retries)
    r.value = l4.to_value(r.p, r.label, output)
    return r


def _versions(p: PromptSet, segmentation: str) -> dict[str, str]:
    v = p.version()
    return {"library": __version__, "prompts": f"{v.set}:{v.plan}/{v.answer}/{v.orient}/{v.exclusive}",
            "units_rule": rule_version(segmentation), "aggregation": AGGREGATION_VERSION}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _append(path: str | os.PathLike, record: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def judge(text: str, proposition: str, output: OutputType, *,
                readers: Sequence[Reader], planner: Reader | None = None,
                verifiers: Sequence[Reader] | None = None,
                question_set: QuestionSet | None = None,
                budget: Budget = Budget(), thresholds: Thresholds = Thresholds(),
                prompts: str | PromptSet = "ja", segmentation: str = "ja-sentence",
                record_path: str | os.PathLike | None = None) -> Judgment:
    """命題を本文に当てて判定する。

    - readers: 問いに答える読み手（2 体以上を推奨。偽読み手も可。偽読み手は要約から除く）
    - planner: 問いを生成する読み手（既定は偽でない読み手の先頭）
    - verifiers: 向きの交差検証（H12）の検証役（既定は偽でない読み手。2 体未満なら交差検証しない）
    - question_set: 渡せば L1 を飛ばし、その問いの集合で答えさせる（上流 J7。反事実・読み手の差の測定）
    - record_path: 記録（JSONL）を追記する先
    """
    p = get_prompts(prompts)
    _check_input(text, proposition, output, readers, budget, segmentation, question_set)
    units = segment(text, segmentation)
    real = [r for r in readers if not r.calibration]
    meter = Meter()
    sem = asyncio.Semaphore(budget.workers)
    if question_set is None:
        gen = planner if planner is not None else (real[0] if real else None)
        if gen is None:
            raise InputError("生成器が要る（偽でない読み手が無いので既定が決まらない）")
        if gen.calibration:
            raise InputError(f"偽読み手 {gen.name} は生成器になれない（答えるだけ）")
        vs = list(verifiers) if verifiers is not None else real
        if any(v.calibration for v in vs):
            raise InputError("偽読み手は検証役になれない")
        qs = await l1.plan(gen, units, proposition, budget=budget, prompts=p, verifiers=vs, sem=sem, meter=meter)
    else:
        qs = replace(question_set, source="given")
    matrices: list[AnswerMatrix] = []
    readings: dict[str, Reading] = {}
    for r in readers:
        m = await l2.answer_all(r, units, qs, prompts=p, samples=budget.samples, sem=sem, meter=meter)
        matrices.append(m)
        readings[r.name] = _read(m, qs.active_ids, qs.retries, output, thresholds)
    j = Judgment(proposition=proposition, output=output, units=units, segmentation=segmentation, question_set=qs,
                 matrices=matrices, readings=readings, summary=_summary(readings, output, thresholds),
                 cost=meter.cost, versions=_versions(p, segmentation), budget=budget, thresholds=thresholds, at=_now())
    if record_path is not None:
        _append(record_path, j.to_record())
    return j


def judge_sync(*args, **kwargs) -> Judgment:
    """judge の同期版（asyncio.run で包む）。既に走っているイベントループの中では使えない。"""
    return asyncio.run(judge(*args, **kwargs))


def replay(record: dict, *, thresholds: Thresholds | None = None, output: OutputType | None = None,
           reparse: bool = False, prompts: str | PromptSet | None = None) -> Judgment:
    """記録から LLM を呼ばずに引き直す（上流 Q7）。既定は L3・L4 だけ。

    - thresholds / output: 変えて引き直す（省略時は記録の値）
    - reparse: 生応答（raw）から L2 の解釈（根拠 id の正規化と照合）も引き直す。prompts は記録の指示の集合（既定は記録の名前）
    """
    inp = record["input"]
    units = [Unit(**u) for u in inp["units"]]
    qs = QuestionSet.from_dict(record["question_set"])
    matrices = [AnswerMatrix.from_dict(m) for m in record["matrices"]]
    if reparse:
        p = get_prompts(prompts or qs.prompt.set)
        ids = {u.id for u in units}
        matrices = [replace(m, answers=[l2.reinterpret(a, p, ids) for a in m.answers]) for m in matrices]
    t = thresholds or Thresholds.from_dict(inp["thresholds"])
    out = output or output_from_dict(inp["output"])
    l4.check_output(out)
    readings = {m.reader: _read(m, qs.active_ids, qs.retries, out, t) for m in matrices}
    versions = dict(record.get("versions") or {})
    versions.update({"library": __version__, "aggregation": AGGREGATION_VERSION, "replayed_from": record.get("at", "")})
    return Judgment(proposition=inp["proposition"], output=out, units=units, segmentation=inp["segmentation"],
                    question_set=qs, matrices=matrices, readings=readings, summary=_summary(readings, out, t),
                    cost=Cost(), versions=versions, budget=Budget.from_dict(inp["budget"]), thresholds=t, at=_now())
