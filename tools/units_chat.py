"""単位化だけを触る最小の CLI。本文を単位列にして、指示に埋める形で見せる。

    python tools/units_chat.py 本文.txt
    python tools/units_chat.py 本文.txt --rule paragraph
    echo 甲。乙！丙？ | python tools/units_chat.py -
"""
from __future__ import annotations

import argparse

from _cli import read_text, utf8_io

from structural_distillation.units import RULES, render, rule_version, segment


def main() -> int:
    utf8_io()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", help="本文のファイル（- で標準入力）")
    ap.add_argument("--rule", default="ja-sentence", choices=list(RULES))
    args = ap.parse_args()
    units = segment(read_text(args.text), args.rule)
    print(f"# 規則 {rule_version(args.rule)}・単位 {len(units)}")
    print(render(units))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
