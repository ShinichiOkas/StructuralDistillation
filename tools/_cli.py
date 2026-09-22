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
    if path in (None, "-"):
        return sys.stdin.read()
    with open(path, encoding="utf-8") as f:
        return f.read()
