"""L4 型付け（実装設計 §4.4）: (度合い, 札, 出力型) → 値 または 値なし。純関数。

値は型から構成的に作るので、型の外に出る経路が無い（上流 Q2）。
- 札が「本文に根拠が無い」「計器不良」、または p が未定義 → 値なし
- 真偽確率 → p
- 順序尺度 → 段。既定は K 等分（空撃ち level5 と同じ式。上流 J5・仮置き）。切れ目を渡されたらそれで切る
"""
from __future__ import annotations

from .contracts import NO_VALUE_LABELS, InputError, Label, Ordinal, OutputType, Probability, Value


def check_output(output: OutputType) -> None:
    """出力型の形を検査する（上流 F2）。不正なら InputError。"""
    if isinstance(output, Probability):
        return
    if not isinstance(output, Ordinal):
        raise InputError(f"出力型は Probability か Ordinal: {output!r}")
    if not isinstance(output.k, int) or output.k < 2:
        raise InputError(f"順序尺度の段数は 2 以上: k={output.k!r}")
    if output.labels is not None and len(output.labels) != output.k:
        raise InputError(f"段のラベルは {output.k} 個: {len(output.labels)} 個")
    if output.bounds is not None:
        b = list(output.bounds)
        if len(b) != output.k - 1:
            raise InputError(f"段の切れ目は {output.k - 1} 個: {len(b)} 個")
        if any(not 0.0 < x < 1.0 for x in b) or any(x >= y for x, y in zip(b, b[1:])):
            raise InputError(f"段の切れ目は (0, 1) の中で狭義の昇順: {b}")


def type_value(p: float, output: OutputType) -> Value:
    """p を型の値にする（札は見ない。代表値にも使う）。"""
    if isinstance(output, Probability):
        return Value(p=p)
    if output.bounds is None:
        level = min(output.k - 1, int(p * output.k)) + 1
    else:
        level = 1 + sum(1 for b in output.bounds if b <= p)
    return Value(p=p, level=level, label=output.labels[level - 1] if output.labels else None)


def to_value(p: float | None, label: Label, output: OutputType) -> Value | None:
    if p is None or label in NO_VALUE_LABELS:
        return None
    return type_value(p, output)
