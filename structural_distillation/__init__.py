"""Structural Distillation — 命題を本文に当て、支持・反証の両側から立てた問いの答えの分布で判定する。

設計: doc/UPSTREAM_DESIGN.md（何を）・doc/IMPLEMENTATION_DESIGN.md（どう組むか）。
層: l0（読み手ポート）/ units（単位化）/ l1（問い生成）/ l2（回答）/ l3（集約）/ l4（型付け）/ compose（合成）。
コアは標準ライブラリだけに依存する。

公開名は遅延して読み込む（PEP 562）。`import structural_distillation` だけでは層を読み込まない。
⚠ 公開名と同じ名前のサブモジュールを作らないこと。サブモジュールを読み込むと、import の仕組みがパッケージの属性を
そのモジュールで上書きし、公開名（関数）がモジュールに化ける（受入 M4: 合成を judge.py に置いていたとき、
`from structural_distillation import judge_sync, judge` で judge がモジュールになった）。
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

__version__ = "0.1.0"

_EXPORTS: dict[str, str] = {
    "judge": ".compose", "judge_sync": ".compose", "replay": ".compose",
    "Reader": ".l0", "RawReply": ".l0", "CachedPort": ".l0", "OllamaReader": ".l0", "FakeReader": ".l0",
    "PromptSet": ".prompts",
    "QuestionStore": ".store", "question_key": ".store",
}
for _n in ("Answer", "AnswerMatrix", "Attempt", "Axis", "AxisReading", "Budget", "Cost", "Counts", "CrossCheck",
           "Diagnostics", "InputError", "Judgment", "Label", "Ordinal", "PlanningFailed", "Probability",
           "PromptVersion", "QuestionSet", "Reading", "ReaderSummary", "Reason", "RetryAction", "RetryHint",
           "StoreError", "Thresholds", "Unit", "Value", "Verdict"):
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
        Reason, RetryAction, RetryHint, StoreError, Thresholds, Unit, Value, Verdict,
    )
    from .store import QuestionStore, question_key  # noqa: F401
    from .compose import judge, judge_sync, replay  # noqa: F401
    from .l0 import CachedPort, FakeReader, OllamaReader, RawReply, Reader  # noqa: F401
    from .prompts import PromptSet  # noqa: F401
