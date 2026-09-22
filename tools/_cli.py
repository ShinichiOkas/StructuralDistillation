"""CLI の共通部品（道具。パッケージには入れない）。Windows のコンソールでも落ちないよう入出力を UTF-8 に固定する。"""
from __future__ import annotations

import sys
from pathlib import Path

LABEL_JA = {
    "LEAN_SUPPORT": "偏り（支持）",
    "LEAN_REFUTE": "偏り（反証）",
    "SPLIT": "割れる",
    "NO_EVIDENCE": "本文に根拠が無い",
    "INSTRUMENT_FAULT": "計器不良",
}


def utf8_io() -> None:
    for s in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(s, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except ValueError:
                pass


REASON_JA = {
    "no_active_axes": "判定に使える軸が 0 本",
    "invalid_evidence": "根拠 id が実在しない回答が多い",
    "contradictory_axes": "両側に証拠が出た軸が多い",
    "no_definite_axis": "向きの定まった軸が 0",
    "low_valid_rate": "向きの定まった軸の割合が低い",
}

PROBES = Path(__file__).resolve().parents[1] / ".pair-agent" / "probes"


def guard_write_path(path: str | None) -> str | None:
    """測定の記録（.pair-agent/probes/）の下には書かせない（凍結物。受入 m13）。"""
    if path is None:
        return None
    p = Path(path).resolve()
    if p == PROBES or PROBES in p.parents:
        raise SystemExit(f"{path} は測定の記録の下。書き込み先には使えない（読むだけなら --extra で渡す）")
    return path


def fmt(x) -> str:
    return "—" if x is None else f"{x:.2f}"


def read_text(path: str | None) -> str:
    """本文を読む（- で標準入力）。見つからなければトレースバックではなく一言で止める。"""
    if path in (None, "-"):
        return sys.stdin.read()
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"本文のファイルが見つからない: {path}（今いるディレクトリ: {Path.cwd()}）。"
                         f"試すなら examples/ の本文を使う（examples/README.md）")
    try:
        # utf-8-sig: BOM 付きで保存した本文も同じ本文として読む（BOM があると 1 文目が変わり、問いが再利用されない。受入 m8）
        return p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise SystemExit(f"本文のファイルが UTF-8 ではない: {path}") from None
