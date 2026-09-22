"""Structural Distillation — 命題を本文に当て、支持・反証の両側から立てた問いの答えの分布で判定する。

設計: doc/UPSTREAM_DESIGN.md（何を）・doc/IMPLEMENTATION_DESIGN.md（どう組むか）。
層: l0（読み手ポート）/ units（単位化）/ l1（問い生成）/ l2（回答）/ l3（集約）/ l4（型付け）/ judge（合成）。
コアは標準ライブラリだけに依存する。

公開名は遅延して読み込む（PEP 562）。`import structural_distillation` だけでは層を読み込まない。
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

__version__ = "0.1.0"

_EXPORTS: dict[str, str] = {
    "judge": ".judge", "judge_sync": ".judge", "replay": ".judge",
    "Reader": ".l0", "RawReply": ".l0", "CachedPort": ".l0", "OllamaReader": ".l0", "FakeReader": ".l0",
    "PromptSet": ".prompts",
}
for _n in ("Answer", "AnswerMatrix", "Attempt", "Axis", "AxisReading", "Budget", "Cost", "Counts", "CrossCheck",
           "Diagnostics", "InputError", "Judgment", "Label", "Ordinal", "PlanningFailed", "Probability",
           "PromptVersion", "QuestionSet", "Reading", "ReaderSummary", "Thresholds", "Unit", "Value", "Verdict"):
    _EXPORTS[_n] = ".contracts"

__all__ = ["__version__", *_EXPORTS]


def __getattr__(name: str) -> Any:
    mod = _EXPORTS.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(mod, __name__), name)
    globals()[name] = value
    return value


if TYPE_CHECKING:  # 静的解析のためだけ
    from .contracts import (  # noqa: F401
        Answer, AnswerMatrix, Attempt, Axis, AxisReading, Budget, Cost, Counts, CrossCheck, Diagnostics, InputError,
        Judgment, Label, Ordinal, PlanningFailed, Probability, PromptVersion, QuestionSet, Reading, ReaderSummary,
        Thresholds, Unit, Value, Verdict,
    )
    from .judge import judge, judge_sync, replay  # noqa: F401
    from .l0 import CachedPort, FakeReader, OllamaReader, RawReply, Reader  # noqa: F401
    from .prompts import PromptSet  # noqa: F401
