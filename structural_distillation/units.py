"""単位化: 本文 → 単位列（実装設計 §4.U）。決定論。規則は版付き。上流設計で番号の無い層。

根拠は単位の id（`s1`…）で指す（上流 J11）。id はどの規則でも `s` ＋ 連番。
"""
from __future__ import annotations

import re

from .contracts import Unit

_SENT_SPLIT = re.compile(r"(?<=[。！？])")
_PARA_SPLIT = re.compile(r"\n[ \t　]*\n")

RULES: dict[str, str] = {
    "ja-sentence": "v1",   # 。！？ の直後で割る（空撃ちと同一。既定。実装判断 I8）
    "paragraph": "v1",     # 空行で割る
    "line": "v1",          # 改行で割る
}


def segment(text: str, rule: str = "ja-sentence") -> list[Unit]:
    if rule == "ja-sentence":
        parts = _SENT_SPLIT.split(text)
    elif rule == "paragraph":
        parts = _PARA_SPLIT.split(text)
    elif rule == "line":
        parts = text.splitlines()
    else:
        raise ValueError(f"未知の単位化規則: {rule!r}（{', '.join(RULES)}）")
    bodies = [p.strip() for p in parts if p.strip()]
    return [Unit(id=f"s{i + 1}", text=b) for i, b in enumerate(bodies)]


def render(units: list[Unit]) -> str:
    """指示に埋める形: `[s1] 本文` を改行で並べる（末尾に改行は付けない。空撃ちと同一）。"""
    return "\n".join(f"[{u.id}] {u.text}" for u in units)


def rule_version(rule: str) -> str:
    return f"{rule}/{RULES[rule]}"
