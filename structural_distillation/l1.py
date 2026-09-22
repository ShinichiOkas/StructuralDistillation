"""L1 問い生成（実装設計 §4.1）: (生成器, 単位列, 命題, 予算, 指示, 検証役) → 問いの集合 または PlanningFailed。

生成は LLM、規則の検査はコード（上流 §6）。空撃ち probe3.plan / check_plan と probe4_crosscheck v2 の移植。
- 試行は sample = 0..plan_retries。L0 の失敗・規則違反は再送せず次の試行へ（I18）
- 上限まで満たせなければ PlanningFailed（上流 F3。空撃ちは違反付きで続行した。差分表 P1）
- 記述は LLM が返したまま残す（検査は strip して見る）。記述は回答の指示に入り、鍵に効く
- 交差検証（H12）: 検証役 2 体以上のときだけ。過半数が反対した軸を外す。weak・nonexclusive は診断
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections import Counter
from typing import Sequence

from .contracts import SIDES, Attempt, Axis, Budget, CrossCheck, PlanningFailed, QuestionSet, Unit
from .l0 import Meter, Reader, structured
from .prompts import PromptSet, exclusive_schema, orient_schema, plan_schema
from .units import render

log = logging.getLogger("structural_distillation.l1")

H3_JACCARD = 0.6                       # ⚠ 仮置き（空撃ちと同一）。日本語依存の定数（§12）
_NORM = re.compile(r"[\s、。「」・,.?？!！]")
_QUESTION_ENDS = ("か", "？", "?")
EXPECT = {"support": "SUPPORT", "refute": "REFUTE"}


def _norm(s: str) -> str:
    return _NORM.sub("", s)


def _bigram_jaccard(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    gb = {b[i:i + 2] for i in range(len(b) - 1)}
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def check_harness(axes: list[dict], proposition: str, lo: int, hi: int) -> list[str]:
    """ハーネス規則（H6・H1・H4・H3・H11）の機械検査。違反の一覧を返す（空なら合格）。空撃ち probe3.check_plan と同じ順。"""
    v: list[str] = []
    if not lo <= len(axes) <= hi:
        want = f"{lo}" if lo == hi else f"{lo}〜{hi}"
        v.append(f"H6 軸数: {len(axes)} (期待 {want})")
    seen: set[str] = set()
    for a in axes:
        cs, cr = a["claim_support"].strip(), a["claim_refute"].strip()
        if not cs or not cr:
            v.append(f"H1 空の記述: {a.get('axis')}")
            continue
        if _norm(cs) == _norm(cr):
            v.append(f"H1 両側が同一: {cs}")
        for c in (cs, cr):
            k = _norm(c)
            if k in seen:
                v.append(f"H4 非重複: {c}")
            seen.add(k)
            if _bigram_jaccard(c, proposition) >= H3_JACCARD:
                v.append(f"H3 非自明（命題の言い換え）: {c}")
            if c.endswith(_QUESTION_ENDS):
                v.append(f"H11 疑問文: {c}")
    return v


def _sem(sem: asyncio.Semaphore | None):
    return sem if sem is not None else contextlib.nullcontext()


async def plan(planner: Reader, units: list[Unit], proposition: str, *, budget: Budget, prompts: PromptSet,
               verifiers: Sequence[Reader] = (), sem: asyncio.Semaphore | None = None,
               meter: Meter | None = None) -> QuestionSet:
    meter = meter or Meter()
    units_text = render(units)
    schema = plan_schema()
    attempts: list[Attempt] = []
    accepted: list[Axis] | None = None
    for index in range(budget.plan_retries + 1):
        async with _sem(sem):
            s = await structured(planner, prompts.plan_messages(units_text, proposition, budget.axes), schema,
                                 version=prompts.plan.version, sample=index, notice=prompts.schema_notice, retry=False)
        meter.add("plan", s, calibration=planner.calibration)
        if not s.ok:
            attempts.append(Attempt(index, ("L0 失敗",), None, s.error))
            continue
        raw = s.obj["axes"]
        viol = check_harness(raw, proposition, budget.lo, budget.hi)
        attempts.append(Attempt(index, tuple(viol), len(raw), None))
        if viol:
            log.info("L1: 試行 %d が規則違反 %s", index, viol)
            continue
        accepted = [Axis(id=f"a{i + 1:02d}", name=a["axis"], claim_support=a["claim_support"],
                         claim_refute=a["claim_refute"], origin="initial" if index == 0 else "retry")
                    for i, a in enumerate(raw)]
        break
    if accepted is None:
        raise PlanningFailed(attempts)
    cc = None
    real_verifiers = list(verifiers)
    if budget.crosscheck and len(real_verifiers) >= 2:
        cc = await crosscheck(real_verifiers, proposition, accepted, prompts=prompts, sem=sem, meter=meter)
    elif budget.crosscheck:
        log.info("L1: 検証役が %d 体なので向きの交差検証（H12）はしない（未検証）", len(real_verifiers))
    flagged = set(cc.flagged) if cc else set()
    active = [a.id for a in accepted if a.id not in flagged]
    if not active:
        log.warning("L1: 交差検証ですべての軸が外れた。各読み手は有効な軸 0 で値なしになる")
    return QuestionSet(axes=accepted, active_ids=active, planner=planner.name, prompt=prompts.version(),
                       budget=budget, attempts=attempts, crosscheck=cc, source="generated")


def _majority(codes: list[str]) -> str | None:
    if not codes:
        return None
    cnt = Counter(codes)
    top = max(cnt.values())
    tops = [c for c, n in cnt.items() if n == top]
    return tops[0] if len(tops) == 1 else "TIE"


async def crosscheck(verifiers: Sequence[Reader], proposition: str, axes: list[Axis], *, prompts: PromptSet,
                     sem: asyncio.Semaphore | None = None, meter: Meter | None = None) -> CrossCheck:
    """向きの交差検証（H12）。空撃ち probe4_crosscheck v2 と同じ規則・同じ指示（xc2）。本文は見せない。"""
    meter = meter or Meter()
    o_schema, x_schema = orient_schema(prompts), exclusive_schema(prompts)

    async def ask_orient(v: Reader, claim: str, rev: bool) -> str | None:
        async with _sem(sem):
            s = await structured(v, prompts.orient_messages(proposition, claim, rev), o_schema,
                                 version=prompts.orient.version, sample=int(rev), notice=prompts.schema_notice)
        meter.add("crosscheck", s, calibration=v.calibration)
        return prompts.orientation_of(s.obj["orientation"]) if s.ok else None

    async def ask_excl(v: Reader, a: Axis) -> str | None:
        async with _sem(sem):
            s = await structured(v, prompts.exclusive_messages(a.claim_support, a.claim_refute), x_schema,
                                 version=prompts.exclusive.version, sample=0, notice=prompts.schema_notice)
        meter.add("crosscheck", s, calibration=v.calibration)
        return prompts.exclusivity_of(s.obj["compatible"]) if s.ok else None

    o_jobs = [(a.id, v.name, side, rev) for a in axes for v in verifiers for side in SIDES for rev in (False, True)]
    by_name = {v.name: v for v in verifiers}
    by_id = {a.id: a for a in axes}
    o_res = await asyncio.gather(*[ask_orient(by_name[vn], by_id[aid].claim(side), rev) for aid, vn, side, rev in o_jobs])
    x_jobs = [(a.id, v.name) for a in axes for v in verifiers]
    x_res = await asyncio.gather(*[ask_excl(by_name[vn], by_id[aid]) for aid, vn in x_jobs])
    orient = {(aid, vn, side, rev): r for (aid, vn, side, rev), r in zip(o_jobs, o_res)}
    excl: dict[str, list[str]] = {}
    for (aid, _vn), r in zip(x_jobs, x_res):
        if r is not None:
            excl.setdefault(aid, []).append(r)

    votes, flagged, weak, nonexcl = [], [], [], []
    for a in axes:
        checkers: dict[str, dict] = {}
        opposing = 0
        is_weak = False
        for v in verifiers:
            cv = {}
            for side in SIDES:
                codes = [c for c in (orient[(a.id, v.name, side, False)], orient[(a.id, v.name, side, True)]) if c is not None]
                is_weak |= "NEITHER" in codes
                cv[side] = {"votes": codes, "majority": _majority(codes), "expect": EXPECT[side]}
            if any(cv[s]["majority"] in ("SUPPORT", "REFUTE") and cv[s]["majority"] != cv[s]["expect"] for s in SIDES):
                opposing += 1
            checkers[v.name] = cv
        if opposing * 2 > len(verifiers):
            flagged.append(a.id)
        if is_weak:
            weak.append(a.id)
        ex = excl.get(a.id, [])
        if ex and ex.count("COMPATIBLE") * 2 > len(ex):
            nonexcl.append(a.id)
        votes.append({"id": a.id, "axis": a.name, "checkers": checkers, "exclusivity": ex})
    if flagged:
        log.info("L1: 交差検証で外した軸 %s", flagged)
    return CrossCheck(verifiers=[v.name for v in verifiers], votes=votes, flagged=flagged, weak=weak,
                      nonexclusive=nonexcl)
