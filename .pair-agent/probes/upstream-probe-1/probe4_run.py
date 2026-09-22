"""測定 2 周目の走行係。probe3（排他な対の軸）を、読み手・題材・文脈長・読み手の指示を差し替えて回す。

  --meta-rule      読み手の指示に「『本文に X が書かれている』という記述は、書かれていなければ否定している」を足す（設計 S14）
  --num-ctx N      Ollama の文脈長（羅生門は 16384）
  --materials ...  題材ファイル（複数可）
  --readers ...    読み手（`ollama list` に実在する名前）
  --out DIR        出力先（長い走行はリポジトリ外）

キャッシュは out/llm_cache.jsonl（追記のみ）。生成器の指示は p3 のままなので、同じ題材の軸はキャッシュから再利用される。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import probe as P
import probe3 as P3


class PortCtx(P.Port):
    """文脈長を差し替えられる読み手ポート。"""

    def __init__(self, cache_path: Path, cache_only: bool = False, num_ctx: int = 8192):
        super().__init__(cache_path, cache_only)
        self.num_ctx = num_ctx

    def _post(self, model, messages, schema, think):
        body = {"model": model, "messages": messages, "stream": False, "format": schema,
                "options": {"num_ctx": self.num_ctx}}
        if think is not None:
            body["think"] = think
        req = urllib.request.Request(P.OLLAMA, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(resp.read().decode("utf-8"))


def meta_answer_prompt(units_text: str, statement: str) -> list[dict]:
    sys_msg = (
        "あなたは、与えられた本文だけを根拠に、記述が本文とどう関係するかを判定する係です。"
        "本文に書かれていないことを、自分の知識や推測で補ってはいけません。"
        "出力は指定の JSON だけを返してください。"
    )
    user = f"""## 本文（各文に [id] が付いています）
{units_text}

## 記述
{statement}

## 判定の仕方
- 本文がこの記述の内容を述べているなら verdict は "述べている"。evidence に根拠の文の id を 1 つ以上入れる。
- 本文がこの記述の内容を明示的に否定している（反対のことを述べている）なら verdict は "否定している"。evidence に根拠の文の id を入れる。
- 本文にこの内容についての記述が無い、または本文からは決められないなら verdict は "触れていない"。evidence は空にする。
- ⚠ 記述が「本文（この報告・この議事録・この契約書など）に X が書かれている／記載されている／含まれている」という、本文そのものについての形のときは:
  本文に X が書かれていれば "述べている"、書かれていなければ "否定している"（"触れていない" にしない）。
  evidence には、X が書かれている文の id、または X が書かれているべき箇所に最も近い文の id を入れる。
- evidence の id は、本文の [ ] の中の文字列（例: s3）だけを使う。
"""
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]


MEMORY_TITLE = ""
_CURRENT = {"id": None}  # --axes-from のとき、いま処理中の題材 id


def memory_answer(port: P.Port, reader: str, units, units_text: str, statement: str, sample: int) -> dict:  # noqa: ARG001
    """本文を渡さない腕（M5）。読み手の記憶だけで記述を判定させる。根拠は取らない。"""
    if reader.startswith("fake:"):
        return P.fake_answer(reader, units)
    sys_msg = "あなたは、自分の知識だけで、記述が作品の内容と合っているかを判定する係です。本文は与えられません。出力は指定の JSON だけを返してください。"
    user = f"""作品: 『{MEMORY_TITLE}』

## 記述
{statement}

## 判定の仕方
- あなたの知識で、作品がこの記述の内容を述べているなら verdict は "述べている"。
- 作品が明示的に否定している（反対のことを述べている）なら "否定している"。
- 作品にこの内容についての記述が無い、または分からないなら "触れていない"。
- evidence は常に空にする。
"""
    msgs = [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]
    r = port.chat(reader, msgs, P.ANSWER_SCHEMA, sample=sample, version="p4mem")
    if not r["ok"]:
        return {"answer": "無効", "negation_type": None, "evidence": [], "valid": False, "raw_answer": None, "why": r["error"]}
    try:
        v = json.loads(r["content"])["verdict"]
        if v not in P.VERDICTS:
            raise ValueError(v)
    except Exception as e:  # noqa: BLE001
        return {"answer": "無効", "negation_type": None, "evidence": [], "valid": False, "raw_answer": None, "why": f"parse: {e}"}
    return {"answer": P._VERDICT_TO_ANSWER[v], "negation_type": P._VERDICT_TO_NEG[v], "evidence": [], "valid": True,
            "raw_answer": {"verdict": v}, "why": "memory"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--materials", nargs="+", default=[str(P.HERE / "materials.json"), str(P.HERE / "materials2.json")])
    ap.add_argument("--axes-from", default=None, help="軸を生成せず、この results.json の軸を題材 id で引いて使う（記憶腕・別読み手で同じ軸を使う）")
    ap.add_argument("--no-text", action="store_true", help="本文を渡さない腕（M5）。--axes-from が必須")
    ap.add_argument("--no-cf", action="store_true", help="反事実を回さない（標本内ばらつきの腕など）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--planner", default=P.DEFAULT_PLANNER)
    ap.add_argument("--readers", nargs="*", default=P.DEFAULT_READERS)
    ap.add_argument("--samples", type=int, default=2)
    ap.add_argument("--axes", type=int, default=P3.DEFAULT_AXES)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--cache-only", action="store_true")
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--meta-rule", action="store_true")
    ap.add_argument("--num-ctx", type=int, default=8192)
    args = ap.parse_args()

    if args.meta_rule:
        P.answer_prompt = meta_answer_prompt
        P.PROMPT_VERSION = "p4m"
    if args.axes_from:
        stored = {r["id"]: r["plan"] for r in json.loads(Path(args.axes_from).read_text(encoding="utf-8"))}

        def plan_from_store(*_args, **_kwargs):
            pl = stored[_CURRENT["id"]]
            return {"ok": pl["ok"], "axes": pl["axes"], "attempts": [{"from": args.axes_from}]}
        P3.plan = plan_from_store
    if args.no_text:
        if not args.axes_from:
            ap.error("--no-text には --axes-from が要る")
        P.answer = memory_answer

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    port = PortCtx(out / "llm_cache.jsonl", cache_only=args.cache_only, num_ctx=args.num_ctx)
    mats = []
    for path in args.materials:
        mats += json.loads(Path(path).read_text(encoding="utf-8"))["materials"]
    if args.only:
        mats = [m for m in mats if m["id"] in set(args.only)]
    readers = list(args.readers)
    if args.fake:
        readers = readers + ["fake:all_yes", "fake:all_undetermined"]
    log_path = out / "run.log"

    def log(msg: str):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    log(f"=== 開始 p4 planner={args.planner} readers={readers} samples={args.samples} axes={args.axes} "
        f"meta_rule={args.meta_rule} num_ctx={args.num_ctx} 題材={[m['id'] for m in mats]}")
    results = []
    for m in mats:
        if args.axes_from:
            _CURRENT["id"] = m["id"]
        if args.no_text:
            global MEMORY_TITLE
            MEMORY_TITLE = m["title"]
            m = {**m, "counterfactual": None}  # 記憶腕に反事実は無い
        if args.no_cf:
            m = {**m, "counterfactual": None}
        results.append(P3.run_material(port, m, args.planner, readers, args.samples, args.axes, log))
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = P3.summarize(results, readers, args.samples, args.axes)
    (out / "summary.md").write_text(summary, encoding="utf-8")
    log(f"=== 終了 live={port.calls_live} cached={port.calls_cached}")
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
