"""L2 回答（実装設計 §4.2）: (読み手, 単位列, 問いの集合, 指示) → 回答行列。

- 仕事の単位は (有効な軸, 側, 標本) で 1 呼び出し（上流 J13）。交差検証で外した軸には答えさせない（I6）
- 失敗は版に ":retry" を付けて再送 1 回（空撃ちで再送するのは回答だけ。I18）
- 根拠 id は正規化（角括弧・空白・鉤括弧を除く）してから単位列と照合する（上流 J16）
- 述べている／否定している は根拠が 1 つ以上あり、すべて実在すれば有効。触れていない は根拠を要求しない
- 結果の並びは (軸, 側, 標本) の順に固定（並列でも記録が同じ）
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re

from .contracts import SIDES, Answer, AnswerMatrix, QuestionSet, Unit, Verdict
from .l0 import Meter, Reader, structured, validate
from .prompts import PromptSet, answer_schema
from .units import render

_ID_CLEAN = re.compile(r"[\[\]\s「」]")


def normalize_ids(ev: list) -> list[str]:
    out = []
    for e in ev:
        if not isinstance(e, str):
            continue
        e2 = _ID_CLEAN.sub("", e)
        if e2:
            out.append(e2)
    return out


def interpret(obj: dict, prompts: PromptSet, unit_ids: set[str]) -> tuple[Verdict | None, list, list[str], bool, str | None]:
    """スキーマ準拠の回答 → (答え, 根拠（生）, 根拠（正規化後）, 有効か, 無効の理由)。純関数。"""
    verdict = prompts.verdict_of(obj["verdict"])
    raw = list(obj.get("evidence") or [])
    ev = normalize_ids(raw)
    if verdict is None:
        return None, raw, ev, False, f"verdict が語の外: {obj['verdict']!r}"
    if verdict in (Verdict.STATES, Verdict.DENIES) and (not ev or any(e not in unit_ids for e in ev)):
        return verdict, raw, ev, False, "根拠 id が無い／実在しない"
    return verdict, raw, ev, True, None


def reinterpret(answer: Answer, prompts: PromptSet, unit_ids: set[str]) -> Answer:
    """記録の生応答（raw）から引き直す（replay の reparse）。raw が無い（L0 失敗）ものはそのまま。"""
    if answer.raw is None:
        return answer
    try:
        obj = json.loads(answer.raw)
    except ValueError:
        return answer
    if validate(obj, answer_schema(prompts)):
        return Answer(answer.axis_id, answer.side, answer.sample, None, [], [], False, "schema", answer.raw,
                      answer.key, answer.cached)
    v, raw, ev, ok, why = interpret(obj, prompts, unit_ids)
    return Answer(answer.axis_id, answer.side, answer.sample, v, raw, ev, ok, why, answer.raw, answer.key, answer.cached)


async def answer_all(reader: Reader, units: list[Unit], question_set: QuestionSet, *, prompts: PromptSet,
                     samples: int, sem: asyncio.Semaphore | None = None, meter: Meter | None = None) -> AnswerMatrix:
    meter = meter or Meter()
    units_text = render(units)
    unit_ids = {u.id for u in units}
    schema = answer_schema(prompts)
    jobs = [(a, side, s) for a in question_set.active_axes for side in SIDES for s in range(samples)]

    async def one(axis, side, sample) -> Answer:
        async with (sem if sem is not None else contextlib.nullcontext()):
            st = await structured(reader, prompts.answer_messages(units_text, axis.claim(side)), schema,
                                  version=prompts.answer.version, sample=sample, notice=prompts.schema_notice, retry=True)
        meter.add("answer", st, calibration=reader.calibration)
        cached = st.cached > 0 and st.live == 0
        if not st.ok:
            return Answer(axis.id, side, sample, None, [], [], False, st.error, st.content, st.key, cached)
        v, raw, ev, ok, why = interpret(st.obj, prompts, unit_ids)
        return Answer(axis.id, side, sample, v, raw, ev, ok, why, st.content, st.key, cached)

    answers = await asyncio.gather(*[one(a, side, s) for a, side, s in jobs])
    return AnswerMatrix(reader=reader.name, calibration=reader.calibration, prompt=prompts.version(),
                        samples=samples, answers=list(answers))
