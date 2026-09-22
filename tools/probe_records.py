"""空撃ちの記録（.pair-agent/probes/upstream-probe-1/out*/results.json）をライブラリの型に起こす道具。読むだけ。

S0a（集約の決定性）のテストと適合検査が使う。空撃ちの記録の形はライブラリに移さない（実装設計 §6）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from structural_distillation.contracts import Answer, AnswerMatrix, Label, Verdict

PROBE_DIR = Path(__file__).resolve().parents[1] / ".pair-agent" / "probes" / "upstream-probe-1"
OUT4 = PROBE_DIR / "out4"
AGG_ARMS = ("base21", "s4", "mono", "meta2", "m3arm", "mem", "rashomon", "mono_rashomon")

CODE = {"Yes": "STATES", "No": "DENIES", "判定不能": "SILENT", "無効": "INVALID"}
LABEL = {"偏り（支持）": Label.LEAN_SUPPORT, "偏り（反証）": Label.LEAN_REFUTE, "割れる": Label.SPLIT,
         "本文に根拠が無い": Label.NO_EVIDENCE, "計器不良": Label.INSTRUMENT_FAULT}


def matrix_from_probe(reader: str, per_axis: list[dict]) -> AnswerMatrix:
    """空撃ちの per_axis（v_support / v_refute は標本ごとの Yes / No / 判定不能 / 無効）→ 回答行列。"""
    answers: list[Answer] = []
    samples = len(per_axis[0]["v_support"]) if per_axis else 0
    for ax in per_axis:
        for side, k in (("support", "v_support"), ("refute", "v_refute")):
            for s, v in enumerate(ax[k]):
                code = CODE[v]
                if code == "INVALID":
                    answers.append(Answer(ax["id"], side, s, None, valid=False, error="記録: 無効"))
                else:
                    answers.append(Answer(ax["id"], side, s, Verdict(code), valid=True))
    return AnswerMatrix(reader=reader, calibration=reader.startswith("fake:"), prompt=None, samples=samples,
                        answers=answers)


def load_results(arm: str, root: Path = OUT4) -> list[dict]:
    return json.loads((root / arm / "results.json").read_text(encoding="utf-8"))


def iter_rows(results: list[dict]) -> Iterator[tuple[str, str, str, dict]]:
    """(題材 id, "main" | "cf", 読み手, 集約の記録) を主走行・反事実の全行について。"""
    for r in results:
        for rd, agg in (r.get("readers") or {}).items():
            yield r["id"], "main", rd, agg
        for rd, agg in ((r.get("counterfactual") or {}).get("readers") or {}).items():
            yield r["id"], "cf", rd, agg


def load_materials(root: Path = PROBE_DIR) -> dict[str, dict]:
    out = {}
    for name in ("materials.json", "materials2.json", "materials_rashomon.json"):
        p = root / name
        if p.exists():
            for m in json.loads(p.read_text(encoding="utf-8"))["materials"]:
                out[m["id"]] = m
    return out
