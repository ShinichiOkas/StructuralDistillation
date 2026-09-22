"""上流設計の空撃ち（器を作る前に本物を通す）。

ライブラリではない。設計 doc/UPSTREAM_DESIGN.md v2 の L0〜L4 を最小のスクリプトで真似て、
未決 U1（対立の核）・U3（読み手が割れたとき）と、品質シナリオ S10（本文追従）・S11（偽読み手）を測る。

依存: 標準ライブラリだけ。LLM は localhost の Ollama（/api/chat, format=JSON Schema）。
生応答は out/llm_cache.jsonl に追記のみで残し、集約は再計算で行う。

使い方:
  python probe.py --dry-run                 # m01 だけ・読み手 1 体・標本 1
  python probe.py --fake                    # 全題材・既定の読み手 2 体・標本 2・偽読み手つき
  python probe.py --cache-only              # LLM を呼ばず、キャッシュから再集約だけ

p1 → p2 の変更（空撃ち 1 周目の計器の検めで発覚）:
  - 問いを「〜か」の疑問文から平叙文の記述に変え、答えを 述べている／否定している／触れていない の 3 値にした。
    否定形の疑問文への Yes/No は日本語で二通りに読め、対の矛盾率が言語慣習で汚れていた
  - 根拠 id を照合前に正規化する（読み手が「[s5]」と角括弧付きで返す。捏造ではない）
  - 回答が空／JSON でないときは 1 回だけ再送する（設計 L0「再送 1 回」）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLLAMA = "http://localhost:11434/api/chat"

# `ollama list` に実在する名前だけを使う（2026-09-21 確認）
DEFAULT_PLANNER = "gemma4:12b"
DEFAULT_READERS = ["qwen3.5:4b", "gemma3:4b"]
AXES_PER_SIDE = 4
PROMPT_VERSION = "p2"

# ---------------------------------------------------------------- 単位化（決定論）

_SENT_SPLIT = re.compile(r"(?<=[。！？])")


def segment(text: str) -> list[tuple[str, str]]:
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p.strip()]
    return [(f"s{i + 1}", p) for i, p in enumerate(parts)]


def render_units(units: list[tuple[str, str]]) -> str:
    return "\n".join(f"[{uid}] {body}" for uid, body in units)


# ---------------------------------------------------------------- L0 読み手ポート（キャッシュ付き）

SCHEMA_IN_PROMPT = True  # 設計 L0 の三段構え: ネイティブ指定 ＋ 指示への明記 ＋ 受信検証。クラウドモデルは format= を守らない（2026-09-22 実測）


def _with_schema(messages: list[dict], schema: dict) -> list[dict]:
    """最後の user メッセージにスキーマを明記する（ネイティブ指定を信用しない）。"""
    if not SCHEMA_IN_PROMPT:
        return messages
    msgs = [dict(m) for m in messages]
    for m in reversed(msgs):
        if m["role"] == "user":
            m["content"] = (m["content"] + "\n\n## 出力形式\n次の JSON Schema に厳密に一致する JSON オブジェクトだけを出力する。"
                            "キー名はスキーマのとおり。コードフェンス・前置き・後書きは禁止。\n" + json.dumps(schema, ensure_ascii=False))
            break
    return msgs


def strip_fence(content: str) -> str:
    """全文が単一のコードフェンスならフェンスだけ剥がす。部分抽出（修復）はしない。"""
    s = content.strip()
    if s.startswith("```") and s.endswith("```"):
        lines = s.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip()
    return s


class Port:
    def __init__(self, cache_path: Path, cache_only: bool = False):
        import threading
        self.cache_path = cache_path
        self.cache_only = cache_only
        self.cache: dict[str, dict] = {}
        self.calls_live = 0
        self.calls_cached = 0
        self._lock = threading.Lock()
        if cache_path.exists():
            for line in cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.cache[row["key"]] = row["payload"]

    @staticmethod
    def _key(model: str, messages: list[dict], schema: dict, sample: int, version: str) -> str:
        blob = json.dumps({"m": model, "msgs": messages, "schema": schema, "sample": sample, "v": version},
                          ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()

    def _post(self, model: str, messages: list[dict], schema: dict, think: bool | None) -> dict:
        body = {"model": model, "messages": messages, "stream": False, "format": schema,
                "options": {"num_ctx": 8192}}
        if think is not None:
            body["think"] = think
        req = urllib.request.Request(OLLAMA, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def chat(self, model: str, messages: list[dict], schema: dict, sample: int, version: str) -> dict:
        """戻り値: {"ok": bool, "content": str|None, "error": str|None, "cached": bool}"""
        messages = _with_schema(messages, schema)
        key = self._key(model, messages, schema, sample, version)
        with self._lock:
            if key in self.cache:
                self.calls_cached += 1
                return {**self.cache[key], "cached": True}
        if self.cache_only:
            return {"ok": False, "content": None, "error": "cache-only miss", "cached": False}
        payload: dict
        try:
            raw = None
            for attempt in range(4):  # クラウドの 429 / 5xx は少し待って再送（最大 3 回）
                try:
                    try:
                        raw = self._post(model, messages, schema, think=False)
                    except urllib.error.HTTPError as e:  # 思考を持たないモデルは think を拒むことがある
                        if e.code == 400 and "think" in e.read().decode("utf-8", "ignore"):
                            raw = self._post(model, messages, schema, think=None)
                        else:
                            raise
                    break
                except urllib.error.HTTPError as e:
                    if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                        time.sleep(5 * (attempt + 1))
                        continue
                    raise
            assert raw is not None
            content = strip_fence((raw.get("message") or {}).get("content") or "")
            payload = {"ok": True, "content": content, "error": None,
                       "eval_count": raw.get("eval_count"), "total_duration": raw.get("total_duration")}
        except Exception as e:  # noqa: BLE001 — 生応答の失敗も記録に残す
            payload = {"ok": False, "content": None, "error": f"{type(e).__name__}: {e}"}
        with self._lock:
            self.calls_live += 1
            with self.cache_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "model": model, "sample": sample, "version": version,
                                    "payload": payload}, ensure_ascii=False) + "\n")
            self.cache[key] = payload
        return {**payload, "cached": False}


# ---------------------------------------------------------------- L1 問い生成（ハーネス付き）

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "axes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "axis": {"type": "string"},
                    "viewpoint": {"type": "string", "enum": ["support", "refute"]},
                    "stmt": {"type": "string"},
                    "stmt_neg": {"type": "string"},
                },
                "required": ["axis", "viewpoint", "stmt", "stmt_neg"],
            },
        }
    },
    "required": ["axes"],
}


def plan_prompt(units_text: str, proposition: str, per_side: int) -> list[dict]:
    sys_msg = (
        "あなたは、命題を検証するための「記述」を設計する係です。文章を書く係ではありません。"
        "出力は指定の JSON だけを返してください。"
    )
    user = f"""以下の本文について、命題「{proposition}」を検証するための記述を作ってください。

## 規則
1. 記述は、本文と突き合わせて「本文がそう述べているか、否定しているか、触れていないか」を判定できる平叙文 1 文にする
   （例: 「太郎は手紙を出した」）。疑問文にしない。
2. 「悪い」「正しい」「優れている」のような評価語で書かない。本文中の出来事・発話・記述の有無で決まる記述だけにする。
3. 命題そのものの言い換えにしない。
4. viewpoint が "support" の記述は、本文がそう述べていれば命題を支持する記述。
   viewpoint が "refute" の記述は、本文がそう述べていれば命題を反証する記述。
5. "support" をちょうど {per_side} 件、"refute" をちょうど {per_side} 件。合計 {per_side * 2} 件。
6. それぞれの記述は別の事実を扱う。同じ事実を言い換えて重ねない。
7. stmt は肯定形の記述。stmt_neg は同じ事実を否定した記述
   （例: stmt「太郎は手紙を出した」→ stmt_neg「太郎は手紙を出さなかった」）。
8. axis は記述が扱う事実の短い名前。

## 本文
{units_text}
"""
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]


def _norm(s: str) -> str:
    return re.sub(r"[\s、。「」・,.?？!！]", "", s)


def _bigram_jaccard(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    gb = {b[i:i + 2] for i in range(len(b) - 1)}
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def check_plan(axes: list[dict], proposition: str, per_side: int) -> list[str]:
    """ハーネス規則の機械検査。違反の一覧を返す（空なら合格）。"""
    v: list[str] = []
    n_s = sum(1 for a in axes if a["viewpoint"] == "support")
    n_r = sum(1 for a in axes if a["viewpoint"] == "refute")
    if n_s != per_side or n_r != per_side:
        v.append(f"H1 両観点同数: support={n_s} refute={n_r} (期待 {per_side}/{per_side})")
    seen = set()
    for a in axes:
        if not a["stmt"].strip() or not a["stmt_neg"].strip():
            v.append(f"H7 空の記述: {a.get('axis')}")
        key = _norm(a["stmt"])
        if key in seen:
            v.append(f"H4 非重複: {a['stmt']}")
        seen.add(key)
        if _bigram_jaccard(a["stmt"], proposition) >= 0.6:
            v.append(f"H3 非自明（命題の言い換え）: {a['stmt']}")
        if _norm(a["stmt"]) == _norm(a["stmt_neg"]):
            v.append(f"H7 肯定形と否定形が同一: {a['stmt']}")
    return v


def plan(port: Port, planner: str, units_text: str, proposition: str, per_side: int, max_try: int = 3) -> dict:
    attempts = []
    last_axes = None  # 上限到達（F3）時に「違反付き」で続けるための最後の試行（空撃ちなので落とさず観察する）
    for t in range(max_try):
        r = port.chat(planner, plan_prompt(units_text, proposition, per_side), PLAN_SCHEMA, sample=t,
                      version=PROMPT_VERSION)
        if not r["ok"]:
            attempts.append({"try": t, "error": r["error"], "violations": ["L0 失敗"]})
            continue
        try:
            axes = json.loads(r["content"])["axes"]
        except Exception as e:  # noqa: BLE001
            attempts.append({"try": t, "error": f"parse: {e}", "violations": ["H7 形式"]})
            continue
        viol = check_plan(axes, proposition, per_side)
        attempts.append({"try": t, "violations": viol, "n_axes": len(axes)})
        for i, a in enumerate(axes):
            a["id"] = f"q{i + 1:02d}"
        if not viol:
            return {"ok": True, "axes": axes, "attempts": attempts}
        last_axes = axes
    return {"ok": False, "axes": last_axes, "attempts": attempts}


# ---------------------------------------------------------------- L2 回答

VERDICTS = ["述べている", "否定している", "触れていない"]
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": VERDICTS},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "evidence"],
}
_VERDICT_TO_ANSWER = {"述べている": "Yes", "否定している": "No", "触れていない": "判定不能"}
_VERDICT_TO_NEG = {"述べている": "n/a", "否定している": "explicit", "触れていない": "absent"}


def answer_prompt(units_text: str, statement: str) -> list[dict]:
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
- evidence の id は、本文の [ ] の中の文字列（例: s3）だけを使う。
"""
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]


_ID_CLEAN = re.compile(r"[\[\]\s「」]")


def normalize_ids(ev: list) -> list[str]:
    out = []
    for e in ev:
        if not isinstance(e, str):
            continue
        e2 = _ID_CLEAN.sub("", e)
        if e2:
            out.append(e2)
    return out


def answer(port: Port, reader: str, units: list[tuple[str, str]], units_text: str, statement: str,
           sample: int) -> dict:
    """戻り値: {"answer": Yes|No|判定不能|無効, "negation_type": ..., "evidence": [...], "valid": bool, "raw_answer": ..., "why": ...}"""
    if reader.startswith("fake:"):
        return fake_answer(reader, units)
    msgs = answer_prompt(units_text, statement)
    obj = None
    last_err = None
    for attempt in range(2):  # L0: 再送 1 回
        ver = PROMPT_VERSION if attempt == 0 else f"{PROMPT_VERSION}:retry"
        r = port.chat(reader, msgs, ANSWER_SCHEMA, sample=sample, version=ver)
        if not r["ok"]:
            last_err = r["error"]
            continue
        try:
            o = json.loads(r["content"])
            if o.get("verdict") not in VERDICTS:
                raise ValueError(f"verdict 不正: {o.get('verdict')!r}")
            obj = o
            break
        except Exception as e:  # noqa: BLE001
            last_err = f"parse: {e} / content={(r['content'] or '')[:80]!r}"
    if obj is None:
        return {"answer": "無効", "negation_type": None, "evidence": [], "valid": False, "raw_answer": None,
                "why": f"L0 失敗（再送後）: {last_err}"}
    verdict = obj["verdict"]
    ev_raw = list(obj.get("evidence") or [])
    ev = normalize_ids(ev_raw)
    ids = {u for u, _ in units}
    raw = {"verdict": verdict, "evidence": ev_raw}
    ans = _VERDICT_TO_ANSWER[verdict]
    neg = _VERDICT_TO_NEG[verdict]
    if ans in ("Yes", "No"):
        if not ev or any(e not in ids for e in ev):
            return {"answer": "無効", "negation_type": neg, "evidence": ev, "valid": False, "raw_answer": raw,
                    "why": "根拠 id が無い/実在しない"}
    return {"answer": ans, "negation_type": neg, "evidence": ev, "valid": True, "raw_answer": raw, "why": None}


def fake_answer(reader: str, units: list[tuple[str, str]]) -> dict:
    kind = reader.split(":", 1)[1]
    first = units[0][0]
    if kind == "all_yes":
        return {"answer": "Yes", "negation_type": "n/a", "evidence": [first], "valid": True, "raw_answer": None, "why": "fake"}
    if kind == "all_no":
        return {"answer": "No", "negation_type": "explicit", "evidence": [first], "valid": True, "raw_answer": None, "why": "fake"}
    if kind == "all_undetermined":
        return {"answer": "判定不能", "negation_type": "absent", "evidence": [], "valid": True, "raw_answer": None, "why": "fake"}
    raise ValueError(reader)


# ---------------------------------------------------------------- L3 集約（決定論）

def direction(viewpoint: str, ans: str, negated_form: bool) -> int:
    orient = 1 if viewpoint == "support" else -1
    if ans == "Yes":
        base = 1
    elif ans == "No":
        base = -1
    else:
        return 0
    if negated_form:  # 否定形の記述を「述べている」＝ 肯定形を「否定している」
        base = -base
    return orient * base


def majority(vals: list[int]) -> tuple[int, float]:
    """多数決（同数なら 0）と一致率。"""
    if not vals:
        return 0, 0.0
    c = Counter(vals)
    top, n = c.most_common(1)[0]
    if list(c.values()).count(n) > 1:
        return 0, n / len(vals)
    return top, n / len(vals)


def aggregate(axes: list[dict], answers: dict[tuple[str, str, int], dict], samples: int) -> dict:
    """answers: (axis_id, form["affirm"|"negate"], sample) -> answer dict。読み手 1 体ぶん。"""
    per_axis = []
    for a in axes:
        aff = [answers[(a["id"], "affirm", s)] for s in range(samples)]
        neg = [answers[(a["id"], "negate", s)] for s in range(samples)]
        d_aff_samples = [direction(a["viewpoint"], x["answer"], False) for x in aff]
        d_neg_samples = [direction(a["viewpoint"], x["answer"], True) for x in neg]
        d_aff, agree_aff = majority(d_aff_samples)
        d_neg, agree_neg = majority(d_neg_samples)
        contradiction = d_aff != 0 and d_neg != 0 and d_aff != d_neg
        consistent = d_aff != 0 and d_neg != 0 and d_aff == d_neg
        per_axis.append({
            "id": a["id"], "viewpoint": a["viewpoint"], "axis": a["axis"],
            "d_aff": d_aff, "d_neg": d_neg, "agree_aff": agree_aff, "agree_neg": agree_neg,
            "contradiction": contradiction, "consistent": consistent,
            "aff_answers": [x["answer"] for x in aff], "neg_answers": [x["answer"] for x in neg],
            "aff_invalid": sum(1 for x in aff if not x["valid"]), "neg_invalid": sum(1 for x in neg if not x["valid"]),
            "aff_undet": sum(1 for x in aff if x["answer"] == "判定不能"),
            "d_aff_samples": d_aff_samples,
        })

    def counts(dirs: list[int]) -> dict:
        s = sum(1 for d in dirs if d == 1)
        r = sum(1 for d in dirs if d == -1)
        n = len(dirs)
        p = s / (s + r) if s + r else None
        w = 2 * min(s, r) / (s + r) if s + r else None
        return {"s": s, "r": r, "u": n - s - r, "n": n, "p": p, "w": w}

    # A: 肯定形だけ（現設計）
    mode_a = counts([x["d_aff"] for x in per_axis])
    # B: 対が一致した軸だけ
    mode_b = counts([x["d_aff"] for x in per_axis if x["consistent"]])
    both_nonzero = [x for x in per_axis if x["d_aff"] != 0 and x["d_neg"] != 0]
    contradiction_rate = (sum(1 for x in both_nonzero if x["contradiction"]) / len(both_nonzero)) if both_nonzero else None
    # 標本ごとの p（標本内のばらつき）
    p_by_sample = []
    for s_idx in range(samples):
        dirs = [x["d_aff_samples"][s_idx] for x in per_axis]
        c = counts(dirs)
        p_by_sample.append(c["p"])
    invalid_rate = sum(x["aff_invalid"] + x["neg_invalid"] for x in per_axis) / (2 * samples * len(per_axis))
    undet_rate = sum(x["aff_undet"] for x in per_axis) / (samples * len(per_axis))
    by_side = {}
    for side in ("support", "refute"):
        xs = [x for x in per_axis if x["viewpoint"] == side]
        by_side[side] = sum(1 for x in xs if x["d_aff"] != 0) / len(xs) if xs else None
    return {
        "A": mode_a, "B": mode_b, "contradiction_rate": contradiction_rate, "n_pairs_both_nonzero": len(both_nonzero),
        "p_by_sample": p_by_sample, "invalid_rate": invalid_rate, "undet_rate": undet_rate, "valid_rate_by_side": by_side,
        "label": label(mode_a, invalid_rate), "per_axis": per_axis,
    }


def label(c: dict, invalid_rate: float, iota: float = 0.3, rho: float = 0.5, omega: float = 0.5,
          contradiction_rate: float = 0.0, kappa: float = 0.5) -> str:
    """設計 §7.4 の札。順序: ι（無効）→ κ（矛盾）→ ρ（根拠）→ ω（幅）。
    ⚠ 測定 2 周目の再評価で、κ を当てていなかった（全問 Yes の偽読み手が ρ で「本文に根拠が無い」に落ちていた）ことが分かり、κ を足した。"""
    if invalid_rate >= iota:
        return "計器不良"
    if contradiction_rate >= kappa:
        return "計器不良"
    if c["n"] == 0 or (c["s"] + c["r"]) / c["n"] < rho:
        return "本文に根拠が無い"
    if c["w"] <= omega and c["p"] > 0.5:
        return "偏り（支持）"
    if c["w"] <= omega and c["p"] < 0.5:
        return "偏り（反証）"
    return "割れる"


# ---------------------------------------------------------------- 走行

def _answer_all(port: Port, reader: str, axes: list[dict], units, units_text: str, samples: int) -> dict:
    answers = {}
    for a in axes:
        for form, stmt in (("affirm", a["stmt"]), ("negate", a["stmt_neg"])):
            for s in range(samples):
                answers[(a["id"], form, s)] = answer(port, reader, units, units_text, stmt, s)
    return answers


def run_material(port: Port, m: dict, planner: str, readers: list[str], samples: int, per_side: int,
                 log) -> dict:
    units = segment(m["text"])
    units_text = render_units(units)
    log(f"[{m['id']}] {m['title']} — 単位 {len(units)} 文。問い生成 ({planner}) …")
    pl = plan(port, planner, units_text, m["proposition"], per_side)
    if pl["axes"] is None:
        log(f"[{m['id']}] 問い生成に失敗（F3）: {pl['attempts']}")
        return {"id": m["id"], "title": m["title"], "plan": pl, "readers": {}, "counterfactual": None}
    log(f"[{m['id']}] 記述 {len(pl['axes'])} 件 (ok={pl['ok']}, 試行 {len(pl['attempts'])})")
    result = {"id": m["id"], "title": m["title"], "proposition": m["proposition"], "expected_lean": m.get("expected_lean"),
              "n_units": len(units), "plan": pl, "readers": {}, "counterfactual": None}
    for reader in readers:
        t0 = time.time()
        answers = _answer_all(port, reader, pl["axes"], units, units_text, samples)
        agg = aggregate(pl["axes"], answers, samples)
        agg["seconds"] = round(time.time() - t0, 1)
        result["readers"][reader] = agg
        log(f"[{m['id']}] {reader}: p={fmt(agg['A']['p'])} w={fmt(agg['A']['w'])} 札={agg['label']} "
            f"矛盾率={fmt(agg['contradiction_rate'])} 判定不能率={agg['undet_rate']:.2f} 無効率={agg['invalid_rate']:.2f} "
            f"({agg['seconds']}s)")
    cf = m.get("counterfactual")
    if cf:
        cunits = segment(cf["text"])
        ctext = render_units(cunits)
        result["counterfactual"] = {"changed": cf["changed"], "expected_change": cf["expected_change"], "readers": {}}
        for reader in readers:
            if reader.startswith("fake:"):
                continue
            answers = _answer_all(port, reader, pl["axes"], cunits, ctext, samples)
            agg = aggregate(pl["axes"], answers, samples)
            orig = result["readers"][reader]
            changed_axes = sum(1 for x, y in zip(orig["per_axis"], agg["per_axis"]) if x["d_aff"] != y["d_aff"])
            result["counterfactual"]["readers"][reader] = {"A": agg["A"], "label": agg["label"], "changed_axes": changed_axes,
                                                           "p_orig": orig["A"]["p"], "p_cf": agg["A"]["p"],
                                                           "per_axis": agg["per_axis"]}
            log(f"[{m['id']}] 反事実 {reader}: p {fmt(orig['A']['p'])} → {fmt(agg['A']['p'])}、方向が変わった軸 {changed_axes}/{len(pl['axes'])}")
    return result


def fmt(x) -> str:
    return "—" if x is None else f"{x:.2f}"


def summarize(results: list[dict], readers: list[str], samples: int) -> str:
    L = []
    L.append(f"# 空撃ち結果（読み手 {', '.join(readers)} / 標本 {samples} / 観点ごと {AXES_PER_SIDE} 軸 ＋ 記述の肯定形・否定形の対 / 指示 {PROMPT_VERSION}）\n")
    L.append("## 題材ごと\n")
    L.append("| 題材 | 想定 | 読み手 | p | w | 札 | 矛盾率 | 判定不能率 | 無効率 | 標本間 |p差| |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        for reader, agg in r["readers"].items():
            ps = [p for p in agg["p_by_sample"] if p is not None]
            spread = (max(ps) - min(ps)) if len(ps) >= 2 else None
            L.append(f"| {r['id']} | {r.get('expected_lean')} | {reader} | {fmt(agg['A']['p'])} | {fmt(agg['A']['w'])} | {agg['label']} "
                     f"| {fmt(agg['contradiction_rate'])} | {agg['undet_rate']:.2f} | {agg['invalid_rate']:.2f} | {fmt(spread)} |")
    L.append("\n## 読み手間の差（U3）\n")
    L.append("| 題材 | " + " | ".join(readers) + " | Δ(max−min) | 段(5)の一致 |")
    L.append("|---|" + "---|" * (len(readers) + 2))
    deltas, within = [], []
    real_readers = [rd for rd in readers if not rd.startswith("fake:")]
    for r in results:
        ps = {rd: r["readers"][rd]["A"]["p"] for rd in readers if rd in r["readers"]}
        vals = [ps[rd] for rd in real_readers if ps.get(rd) is not None]
        d = (max(vals) - min(vals)) if len(vals) >= 2 else None
        levels = {level5(p) for p in vals}
        if d is not None:
            deltas.append(d)
        for rd in real_readers:
            if rd in r["readers"]:
                pbs = [p for p in r["readers"][rd]["p_by_sample"] if p is not None]
                if len(pbs) >= 2:
                    within.append(max(pbs) - min(pbs))
        L.append(f"| {r['id']} | " + " | ".join(fmt(ps.get(rd)) for rd in readers) + f" | {fmt(d)} | {'一致' if len(levels) <= 1 else '不一致'} |")
    if deltas:
        L.append(f"\n- 読み手間 Δ（実読み手のみ）: 平均 {statistics.mean(deltas):.2f}、最大 {max(deltas):.2f}（n={len(deltas)}）")
    if within:
        L.append(f"- 標本間 |p差|（同じ読み手）: 平均 {statistics.mean(within):.2f}、最大 {max(within):.2f}（n={len(within)}）")
    L.append("\n## 対の矛盾率（U1）\n")
    for rd in readers:
        rates = [r["readers"][rd]["contradiction_rate"] for r in results if rd in r["readers"] and r["readers"][rd]["contradiction_rate"] is not None]
        if rates:
            L.append(f"- {rd}: 平均 {statistics.mean(rates):.2f}、最大 {max(rates):.2f}（題材 {len(rates)}）")
    L.append("\n## A（肯定形だけ）と B（対が一致した軸だけ）の段の違い\n")
    L.append("| 題材 | 読み手 | p_A | p_B | 段_A | 段_B | B の軸数 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in results:
        for rd, agg in r["readers"].items():
            pa, pb = agg["A"]["p"], agg["B"]["p"]
            L.append(f"| {r['id']} | {rd} | {fmt(pa)} | {fmt(pb)} | {level5(pa)} | {level5(pb)} | {agg['B']['s'] + agg['B']['r']} |")
    L.append("\n## 本文追従（S10・反事実）\n")
    L.append("| 題材 | 変えた事実 | 期待 | 読み手 | p 元 → 反事実 | 方向が変わった軸 |")
    L.append("|---|---|---|---|---|---|")
    follow_ok = follow_n = 0
    for r in results:
        cf = r.get("counterfactual")
        if not cf:
            continue
        for rd, c in cf["readers"].items():
            po, pc = c["p_orig"], c["p_cf"]
            ok = None
            if po is not None and pc is not None:
                ok = (pc > po) if cf["expected_change"] == "support_up" else (pc < po)
                follow_n += 1
                follow_ok += int(ok)
            L.append(f"| {r['id']} | {cf['changed']} | {cf['expected_change']} | {rd} | {fmt(po)} → {fmt(pc)} {'✓' if ok else ('✗' if ok is False else '—')} | {c['changed_axes']} |")
    if follow_n:
        L.append(f"\n- 期待した向きに動いた: {follow_ok}/{follow_n}")
    L.append("\n## ハーネス違反（問い生成）\n")
    for r in results:
        pl = r["plan"]
        v = [a for a in pl["attempts"] if a.get("violations")]
        L.append(f"- {r['id']}: 試行 {len(pl['attempts'])}、最終 ok={pl['ok']}" + (f"、違反 {v}" if v else ""))
    return "\n".join(L) + "\n"


def level5(p) -> str:
    if p is None:
        return "—"
    k = min(4, int(p * 5))
    return str(k + 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--materials", default=str(HERE / "materials.json"))
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--planner", default=DEFAULT_PLANNER)
    ap.add_argument("--readers", nargs="*", default=DEFAULT_READERS)
    ap.add_argument("--samples", type=int, default=2)
    ap.add_argument("--per-side", type=int, default=AXES_PER_SIDE)
    ap.add_argument("--only", nargs="*", default=None, help="題材 id を絞る")
    ap.add_argument("--dry-run", action="store_true", help="m01 だけ・読み手の先頭 1 体・標本 1")
    ap.add_argument("--cache-only", action="store_true")
    ap.add_argument("--fake", action="store_true", help="偽読み手（全問 Yes / 全問 判定不能）も差す")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    port = Port(out / "llm_cache.jsonl", cache_only=args.cache_only)
    mats = json.loads(Path(args.materials).read_text(encoding="utf-8"))["materials"]
    readers = list(args.readers)
    samples = args.samples
    if args.dry_run:
        mats = [m for m in mats if m["id"] == "m01"]
        readers = readers[:1]
        samples = 1
    if args.only:
        mats = [m for m in mats if m["id"] in set(args.only)]
    if args.fake:
        readers = readers + ["fake:all_yes", "fake:all_undetermined"]

    log_path = out / "run.log"

    def log(msg: str):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    log(f"=== 開始 planner={args.planner} readers={readers} samples={samples} per_side={args.per_side} 指示={PROMPT_VERSION} 題材={[m['id'] for m in mats]}")
    results = []
    for m in mats:
        results.append(run_material(port, m, args.planner, readers, samples, args.per_side, log))
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = summarize(results, readers, samples)
    (out / "summary.md").write_text(summary, encoding="utf-8")
    log(f"=== 終了 live={port.calls_live} cached={port.calls_cached}")
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
