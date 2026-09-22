"""L0 だけを触る最小の CLI。1 行打つと、その行を user として読み手に送り、スキーマ準拠の応答を見せる。

    python tools/l0_chat.py --model gemma4:31b-cloud
    python tools/l0_chat.py --model gemma4:31b-cloud --cache scratch/l0.jsonl      # 同じ入力は 2 回目からキャッシュ
    python tools/l0_chat.py --fake all_yes --schema answer                         # 偽読み手（LLM を呼ばない）

スキーマ: answer（3 値＋根拠）/ free（{"answer": string}）/ ファイルのパス（JSON Schema）。
⚠ ローカルモデル（GPU）は師匠と共有。クラウドモデル（-cloud）を使う。
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from _cli import utf8_io

from structural_distillation import prompts
from structural_distillation.l0 import CachedPort, FakeReader, OllamaReader, structured
from structural_distillation.prompts import PromptSet

FREE = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


def main() -> int:
    utf8_io()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--model", help="Ollama のモデル名（クラウドモデルを推奨）")
    g.add_argument("--fake", choices=FakeReader.KINDS, help="偽読み手")
    ap.add_argument("--schema", default="free", help="answer / free / JSON Schema のファイル")
    ap.add_argument("--system", default="出力は指定の JSON だけを返してください。")
    ap.add_argument("--cache", default=None, help="キャッシュの JSONL（追記のみ）")
    ap.add_argument("--retry", action="store_true", help="失敗なら版に :retry を付けて 1 回再送する")
    ap.add_argument("--num-ctx", type=int, default=8192)
    ap.add_argument("--sample", type=int, default=0, help="標本番号（鍵に入る。同じ入力・同じ標本ならキャッシュが当たる）")
    args = ap.parse_args()

    p = PromptSet.builtin("ja")
    schema = {"answer": prompts.answer_schema(p), "free": FREE}.get(args.schema)
    if schema is None:
        schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    reader = FakeReader(args.fake) if args.fake else OllamaReader(args.model, num_ctx=args.num_ctx)
    if args.cache:
        reader = CachedPort(reader, args.cache)
    print(f"読み手 {reader.name}・スキーマ {args.schema}。空行で終わる。")
    while True:
        try:
            line = input("> ")
        except EOFError:
            break
        if not line.strip():
            break
        msgs = [{"role": "system", "content": args.system}, {"role": "user", "content": line}]
        s = asyncio.run(structured(reader, msgs, schema, version="chat", sample=args.sample, notice=p.schema_notice,
                                   retry=args.retry))
        state = "ok" if s.ok else f"失敗: {s.error}"
        kind = "偽読み手" if reader.calibration else f"実 {s.live}・キャッシュ {s.cached}"
        print(f"  [{state}] 呼び出し {s.attempts}（{kind}）版 {s.version}")
        print("  " + (json.dumps(s.obj, ensure_ascii=False) if s.ok else repr(s.content)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
