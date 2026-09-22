"""L2（回答）だけを触る最小の CLI。本文を読み込んだ読み手に、記述を 1 行ずつ当てて 3 値と根拠を見せる。

    python tools/l2_chat.py 本文.txt --reader qwen3.5:397b-cloud --cache scratch/l2.jsonl
    python tools/l2_chat.py 本文.txt --fake all_yes

⚠ クラウドモデル（-cloud）を使う。
"""
from __future__ import annotations

import argparse
import asyncio

from _cli import read_text, utf8_io

from structural_distillation import l2
from structural_distillation.contracts import Axis, Budget, QuestionSet
from structural_distillation.l0 import CachedPort, FakeReader, OllamaReader
from structural_distillation.prompts import PromptSet
from structural_distillation.units import render, segment


def main() -> int:
    utf8_io()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", help="本文のファイル")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--reader")
    g.add_argument("--fake", choices=FakeReader.KINDS)
    ap.add_argument("--samples", type=int, default=1)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--num-ctx", type=int, default=8192)
    args = ap.parse_args()

    p = PromptSet.builtin("ja")
    units = segment(read_text(args.text))
    reader = FakeReader(args.fake) if args.fake else OllamaReader(args.reader, num_ctx=args.num_ctx)
    # 1 軸の両側に同じ記述を置くので、同じ鍵の要求はキャッシュ（ファイルが無ければメモリだけ）で 1 回にまとまる
    reader = CachedPort(reader, args.cache)
    print(render(units))
    print(f"# 読み手 {reader.name}・標本 {args.samples}。記述を 1 行ずつ。空行で終わる。")
    while True:
        try:
            claim = input("記述> ")
        except EOFError:
            break
        if not claim.strip():
            break
        # 記述 1 つを支持側に置いた 1 軸の問いの集合として答えさせる（反証側は聞かない）
        axis = Axis("a01", "chat", claim, claim)
        qs = QuestionSet([axis], ["a01"], "chat", p.version(), Budget())
        mx = asyncio.run(l2.answer_all(reader, units, qs, prompts=p, samples=args.samples))
        for a in mx.answers:
            if a.side != "support":
                continue
            word = p.verdicts[a.verdict] if a.verdict else "—"
            state = "有効" if a.valid else f"無効（{a.error}）"
            src = "キャッシュ" if a.cached else "実"
            print(f"  標本 {a.sample}: {word}  根拠 {a.evidence or '—'}  {state}  [{src}]")
            for uid in a.evidence:
                body = next((u.text for u in units if u.id == uid), None)
                if body:
                    print(f"      [{uid}] {body}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
