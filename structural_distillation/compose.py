"""合成（実装設計 §4.5）: 本文・命題・型 → 判定（読み手ごとの値と診断値・読み手間の要約・記録）。

公開名 judge と同じ名前のモジュールにしない（__init__ の注記。受入 M4）。

変換の並びは依存関係だけで決まる: 入口の検査 → 単位化 → L1（問いの集合。渡されていれば、または保存庫にあれば飛ばす）→
読み手ごとに L2 → L3 → L4（読み手は 1 体ずつ、記述は workers 並列。I11）→ 読み手間の要約 → 記録。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Sequence

from . import __version__, l1, l2, l3, l4
from .contracts import (AnswerMatrix, Budget, Cost, InputError, Judgment, Ordinal, OutputType, QuestionSet, Reading,
                        ReaderSummary, Reason, RetryAction, RetryHint, Thresholds, Unit, output_from_dict)
from .l0 import Meter, Reader
from .prompts import PromptSet, get_prompts
from .store import QuestionStore, question_key
from .units import RULES, rule_version, segment

AGGREGATION_VERSION = "l3/v1"
log = logging.getLogger("structural_distillation.compose")


def check_thresholds(t: Thresholds) -> None:
    """閾値の値域（F2）。ι・κ・ρ・ω は (0, 1]、δ は 0 以上。ρ = 0 のような値は札の規則を壊す（受入 M3）。"""
    for k in ("iota", "kappa", "rho", "omega"):
        v = getattr(t, k)
        if not isinstance(v, (int, float)) or not 0.0 < v <= 1.0:
            raise InputError(f"閾値 {k} は (0, 1] の中: {v!r}")
    if not isinstance(t.delta, (int, float)) or t.delta < 0.0:
        raise InputError(f"閾値 delta は 0 以上: {t.delta!r}")


def _check_input(text: str, proposition: str, output: OutputType, readers: Sequence[Reader], budget: Budget,
                 segmentation: str, question_set: QuestionSet | None, thresholds: Thresholds) -> None:
    if not isinstance(text, str) or not text.strip():
        raise InputError("本文が空（F2）")
    if not isinstance(proposition, str) or not proposition.strip():
        raise InputError("命題が空（F2）")
    if len(text) > budget.max_chars:
        raise InputError(f"本文が {len(text)} 字で上限 {budget.max_chars} 字を超える（F1）。分割は判断を生成に委ねるので"
                         "ライブラリはしない")
    l4.check_output(output)
    check_thresholds(thresholds)
    if budget.axes < 1 or budget.lo < 1 or budget.lo > budget.hi or not budget.lo <= budget.axes <= budget.hi:
        raise InputError(f"軸数の予算が不正: axes={budget.axes} 下限={budget.lo} 上限={budget.hi}（F2。"
                         "頼む軸数が下限と上限の間に無いと、生成は必ず規則違反になる）")
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
        ids = [a.id for a in question_set.axes]
        if len(set(ids)) != len(ids) or len(set(question_set.active_ids)) != len(question_set.active_ids):
            raise InputError("問いの集合の軸 id か active_ids が重複している（集約で二重に数える）")
        if not set(question_set.active_ids) <= set(ids):
            raise InputError("問いの集合の active_ids が軸に無い")


def _summary(readings: dict[str, Reading], output: OutputType, t: Thresholds) -> ReaderSummary:
    s = l3.summarize_readers(list(readings.values()), t)
    rep = l4.type_value(s.representative.p, output) if s.representative else None
    levels_agree = None
    if isinstance(output, Ordinal) and len(s.readers) >= 2:
        levels = {readings[n].value.level for n in s.readers if readings[n].value}
        levels_agree = len(levels) == 1
    return replace(s, representative=rep, levels_agree=levels_agree)


def _read(matrix: AnswerMatrix, qs: QuestionSet, output: OutputType, t: Thresholds) -> Reading:
    r = l3.aggregate(matrix, qs.active_ids, t, retries=qs.retries)
    r.value = l4.to_value(r.p, r.label, output)
    return r


def _retry_hint(qs: QuestionSet, readings: dict[str, Reading], store_used: bool) -> RetryHint | None:
    """作り直しの材料（師匠 2026-09-23「上位がリトライできるだけの情報を返す」）。ライブラリは自動でリトライしない。"""
    if qs.active_ids:
        return None
    flagged = list(qs.crosscheck.flagged) if qs.crosscheck else []
    if qs.source == "given":
        action = RetryAction.SUPPLY_QUESTION_SET
        how = "渡した問いの集合に有効な軸が無い。作り直した問いの集合を渡す"
    elif store_used:
        action = RetryAction.REGENERATE
        how = "judge(..., regenerate=True) で問いを作り直す（保存庫の古い問いは別名で残る）"
    else:
        action = RetryAction.REPLAN
        how = "judge をもう一度呼べば問いは作り直される（生成器を替えるのも手）"
    why = (f"向きの交差検証で全 {len(qs.axes)} 軸が外れた（検証役 {', '.join(qs.crosscheck.verifiers)}）"
           if flagged else f"判定に使える軸が 0 本（軸 {len(qs.axes)}）")
    return RetryHint(reason=Reason.NO_ACTIVE_AXES, scope="question_set", action=action,
                     message=f"{why}。判定は計器不良（値なし）。{how}",
                     details={"flagged": flagged, "n_axes": len(qs.axes),
                              "verifiers": list(qs.crosscheck.verifiers) if qs.crosscheck else [],
                              "store_key": qs.store_key, "source": qs.source,
                              "votes_in": "question_set.crosscheck.votes",
                              "readers": sorted(readings)})


def _note_differences(stored: QuestionSet, p: PromptSet, budget: Budget, want_planner: str | None,
                      n_verifiers: int) -> list[str]:
    """再利用する問いの集合が、今の生成の条件と違えば知らせる（合意 question-store Q2: 違っても再利用する）。
    違いは WARNING のログと Judgment.notes（記録・CLI に出る）の両方に出す。"""
    notes = []
    if want_planner is not None and stored.planner != want_planner:
        notes.append(f"保存された問いは生成器 {stored.planner} が作った（今回の指定は {want_planner}）")
    if stored.prompt.set != p.name or stored.prompt.plan != p.plan.version:
        notes.append(f"保存された問いは指示 {stored.prompt.set}:{stored.prompt.plan} で作った（今は {p.name}:{p.plan.version}）")
    if not budget.lo <= len(stored.axes) <= budget.hi:
        want = f"{budget.lo}" if budget.lo == budget.hi else f"{budget.lo}〜{budget.hi}"
        notes.append(f"保存された問いは軸 {len(stored.axes)} 本（今回の予算は {want} 本）")
    if stored.crosscheck is None and budget.crosscheck and n_verifiers >= 2:
        notes.append("保存された問いは交差検証していない（今回は交差検証を求めているが、保存された問いをそのまま使う）")
    if not stored.active_ids:
        notes.append("保存された問いは有効な軸が 0 本（作り直すなら regenerate）")
    for n in notes:
        log.warning("問いの保存庫: %s。再利用する", n)
    return notes


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
                question_store: QuestionStore | str | os.PathLike | None = None, regenerate: bool = False,
                budget: Budget = Budget(), thresholds: Thresholds = Thresholds(),
                prompts: str | PromptSet = "ja", segmentation: str = "ja-sentence",
                record_path: str | os.PathLike | None = None) -> Judgment:
    """命題を本文に当てて判定する。

    - readers: 問いに答える読み手（2 体以上を推奨。偽読み手も可。偽読み手は要約から除く）
    - planner: 問いを生成する読み手（既定は偽でない読み手の先頭）
    - verifiers: 向きの交差検証（H12）の検証役（既定は偽でない読み手。2 体未満なら交差検証しない）
    - question_set: 渡せば L1 を飛ばし、その問いの集合で答えさせる（上流 J7。反事実・読み手の差の測定）。保存庫は見ない
    - question_store: 問いの保存庫（ディレクトリ）。同じ命題と本文の問いの集合があれば再利用し（生成も交差検証も呼ばない）、
      無ければ作って保存する（師匠 2026-09-23）。regenerate=True なら作り直して保存する（古いものは別名で残る）
    - record_path: 記録（JSONL）を追記する先
    """
    p = get_prompts(prompts)
    _check_input(text, proposition, output, readers, budget, segmentation, question_set, thresholds)
    if regenerate and question_store is None:
        raise InputError("regenerate は question_store と一緒に使う（作り直した問いの置き場が無い）")
    units = segment(text, segmentation)
    real = [r for r in readers if not r.calibration]
    meter = Meter()
    sem = asyncio.Semaphore(budget.workers)
    notes: list[str] = []
    store: QuestionStore | None = None
    key: str | None = None
    stored: QuestionSet | None = None
    if question_set is None and question_store is not None:
        store = question_store if isinstance(question_store, QuestionStore) else QuestionStore(question_store)
        key = question_key(units, rule_version(segmentation), proposition)
        if not regenerate:
            stored = store.load(key, units=units, proposition=proposition)
            if stored is not None:
                want_planner = planner.name if planner is not None else (real[0].name if real else None)
                n_verifiers = len(verifiers) if verifiers is not None else len(real)
                notes += _note_differences(stored, p, budget, want_planner, n_verifiers)
        if stored is None:
            store.check_writable()   # 生成（LLM の費用）の前に、保存できる置き場所かを確かめる
    if question_set is not None:
        qs = replace(question_set, source="given", store_key=None)
    elif stored is not None:
        qs = replace(stored, source="stored", store_key=key)
    else:
        gen = planner if planner is not None else (real[0] if real else None)
        if gen is None:
            raise InputError("生成器が要る（偽でない読み手が無いので既定が決まらない）")
        if gen.calibration:
            raise InputError(f"偽読み手 {gen.name} は生成器になれない（答えるだけ）")
        vs = list(verifiers) if verifiers is not None else real
        if any(v.calibration for v in vs):
            raise InputError("偽読み手は検証役になれない")
        if len({v.name for v in vs}) != len(vs):
            raise InputError("検証役の名前が重複している（1 体が過半数を作ってしまう）")
        # 作り直しは、前の生成と標本番号の起点をずらす（同じ鍵だと生応答のキャッシュに当たり、前と同じ問いが返る。受入 M3）
        base = store.generations(key) * (budget.plan_retries + 1) if (store is not None and key is not None and regenerate) else 0
        qs = await l1.plan(gen, units, proposition, budget=budget, prompts=p, verifiers=vs, sem=sem, meter=meter,
                           sample_base=base)
        if store is not None and key is not None:
            qs = replace(qs, store_key=key)
            path, old = store.save(key, qs, units=units, units_rule=rule_version(segmentation), proposition=proposition)
            log.info("問いの保存庫: 新しい問いの集合を %s に保存した", path.name)
            if old is not None:
                notes.append(f"作り直した。前の問いの集合は {old.name} に残した")
            if regenerate and meter.cost.plan.live == 0 and meter.cost.plan.cached > 0:
                notes.append("作り直したが、生成の応答はキャッシュから引いた（前と同じ問いの可能性がある）")
    matrices: list[AnswerMatrix] = []
    readings: dict[str, Reading] = {}
    for r in readers:
        m = await l2.answer_all(r, units, qs, prompts=p, samples=budget.samples, sem=sem, meter=meter)
        matrices.append(m)
        readings[r.name] = _read(m, qs, output, thresholds)
    j = Judgment(proposition=proposition, output=output, units=units, segmentation=segmentation, question_set=qs,
                 matrices=matrices, readings=readings, summary=_summary(readings, output, thresholds),
                 cost=meter.cost, versions=_versions(p, segmentation), budget=budget, thresholds=thresholds, at=_now(),
                 notes=notes, retry=_retry_hint(qs, readings, store is not None))
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
        if prompts is None:
            try:
                p = PromptSet.builtin(qs.prompt.set)
            except FileNotFoundError as e:
                raise InputError(f"記録の指示の集合 {qs.prompt.set!r} は組み込みに無い。prompts= で渡すこと") from e
        else:
            p = get_prompts(prompts)
        if p.digest != qs.prompt.digest:
            log.warning("replay(reparse): 指示の集合の digest が記録と違う（記録 %s / 今 %s）。語が変わっていれば解釈も変わる",
                        qs.prompt.digest[:8], p.digest[:8])
        ids = {u.id for u in units}
        matrices = [replace(m, answers=[l2.reinterpret(a, p, ids) for a in m.answers]) for m in matrices]
    t = thresholds or Thresholds.from_dict(inp["thresholds"])
    check_thresholds(t)
    out = output or output_from_dict(inp["output"])
    l4.check_output(out)
    readings = {m.reader: _read(m, qs, out, t) for m in matrices}
    store_used = bool(qs.store_key)
    versions = dict(record.get("versions") or {})
    versions.update({"library": __version__, "aggregation": AGGREGATION_VERSION, "replayed_from": record.get("at", "")})
    return Judgment(proposition=inp["proposition"], output=out, units=units, segmentation=inp["segmentation"],
                    question_set=qs, matrices=matrices, readings=readings, summary=_summary(readings, out, t),
                    cost=Cost(), versions=versions, budget=Budget.from_dict(inp["budget"]), thresholds=t, at=_now(),
                    notes=list(record.get("notes") or []), retry=_retry_hint(qs, readings, store_used))
