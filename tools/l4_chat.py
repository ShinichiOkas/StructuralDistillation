"""L4（型付け）だけを触る最小の CLI。度合い p を打つと、宣言した型の値を見せる。LLM は呼ばない。

    python tools/l4_chat.py --ordinal 5
    python tools/l4_chat.py --ordinal 3 --labels 低 中 高 --bounds 0.3 0.7
    python tools/l4_chat.py --probability
    python tools/l4_chat.py --ordinal 5 --label NO_EVIDENCE       # 値なしの札
"""
from __future__ import annotations

import argparse

from _cli import utf8_io

from structural_distillation import l4
from structural_distillation.contracts import InputError, Label, Ordinal, Probability


def main() -> int:
    utf8_io()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ordinal", type=int, metavar="K")
    g.add_argument("--probability", action="store_true")
    ap.add_argument("--labels", nargs="*", default=None)
    ap.add_argument("--bounds", nargs="*", type=float, default=None)
    ap.add_argument("--label", default="SPLIT", choices=[x.value for x in Label], help="札（既定 SPLIT）")
    args = ap.parse_args()
    out = Probability() if args.probability else Ordinal(args.ordinal, tuple(args.labels) if args.labels else None,
                                                          tuple(args.bounds) if args.bounds else None)
    try:
        l4.check_output(out)
    except InputError as e:
        print(f"型が不正: {e}")
        return 1
    print(f"# 型 {out}・札 {args.label}。p を 1 行ずつ（0〜1）。空行で終わる。")
    while True:
        try:
            line = input("p> ")
        except EOFError:
            break
        if not line.strip():
            break
        try:
            p = float(line)
        except ValueError:
            print("  数を打つ")
            continue
        if not 0.0 <= p <= 1.0:
            print("  度合いは [0, 1]")
            continue
        v = l4.to_value(p, Label(args.label), out)
        print("  値なし" if v is None else f"  p={v.p}" + (f" 段={v.level}" if v.level else "") + (f"（{v.label}）" if v.label else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
