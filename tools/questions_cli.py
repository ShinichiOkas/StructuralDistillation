"""問いの保存庫を見る CLI。LLM は呼ばない。

    python tools/questions_cli.py scratch/questions list          # 保存されている問いの集合の一覧
    python tools/questions_cli.py scratch/questions show 3f2a     # 鍵の先頭で 1 つを表示（軸・交差検証）

保存庫のファイルは 1 本文 × 1 命題 ＝ 1 JSON。直接開いて読んでも、直してもよい
（直した問いはそのまま再利用される。ハーネスの規則は検めない）。
"""
from __future__ import annotations

import argparse

from _cli import utf8_io

from structural_distillation.contracts import StoreError
from structural_distillation.store import QuestionStore


def main() -> int:
    utf8_io()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("store", help="保存庫のディレクトリ（judge_cli の --questions と同じ）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sh = sub.add_parser("show")
    sh.add_argument("key", help="鍵（先頭の何文字かでよい）")
    args = ap.parse_args()
    s = QuestionStore(args.store)
    if not s.root.is_dir():
        print(f"保存庫 {args.store} が無い（judge_cli に --questions {args.store} を付けて判定すると作られる）")
        return 0 if args.cmd == "list" else 1
    try:
        if args.cmd == "list":
            es = s.entries()
            if not es:
                print(f"保存庫 {args.store} は空")
            for e in es:
                if e["error"]:
                    print(f"✗ {e['key']}: {e['error']}")
                    continue
                head = e["first_unit"][:24] + ("…" if len(e["first_unit"]) > 24 else "")
                gens = f"  作り直し {e['generations'] - 1} 回" if e.get("generations", 1) > 1 else ""
                print(f"{e['key'][:12]}  {e['created_at']}  命題「{e['proposition']}」  軸 {e['n_axes']}（有効 {e['n_active']}）"
                      f"  生成器 {e['planner']}  本文 {e['n_units']} 文「{head}」{gens}")
            return 0
        key = s.resolve(args.key)
        qs_obj = s.load(key)                      # 形を検めてから見せる（壊れていれば StoreError）
        d = s.entry(key)
        print(f"# 鍵 {key}\n# 作成 {d.get('created_at')}・ライブラリ {d.get('library')}・単位化 {d.get('units_rule')}"
              f"・生成器 {qs_obj.planner}・生成の指示 {qs_obj.prompt.plan}\n# 命題「{d.get('proposition')}」"
              f"・本文 {len(d.get('units') or [])} 文・作り直し {s.generations(key) - 1} 回")
        for a in qs_obj.axes:
            mark = " " if a.id in qs_obj.active_ids else "✗"
            print(f"{mark} {a.id} [{a.name}]\n     支持側: {a.claim_support}\n     反証側: {a.claim_refute}")
        cc = qs_obj.crosscheck
        if cc:
            print(f"# 交差検証（{', '.join(cc.verifiers)}）: 外した {cc.flagged or 'なし'}・弱 {cc.weak or 'なし'}"
                  f"・非排他 {cc.nonexclusive or 'なし'}")
        else:
            print("# 交差検証なし")
        print(f"# ファイル {s.path(key)}")
        return 0
    except StoreError as e:
        print(f"読めない: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
