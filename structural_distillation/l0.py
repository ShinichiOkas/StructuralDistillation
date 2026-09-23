"""L0 読み手ポート（実装設計 §4.0）: (指示, 回答スキーマ, 読み手) → スキーマ準拠の JSON または型付きの失敗。

LLM は確率的。形式は決定論で守る（三段構え）:
  ① スキーマを読み手に渡す（ネイティブ指定。信用しない）
  ② 指示の末尾にもスキーマを明記する（with_schema）
  ③ 受信で strip_fence → json.loads → validate。失敗なら（回答だけ）版に ":retry" を付けて再送 1 回

ツールモジュールの 3 層: 純粋整形（with_schema / strip_fence / validate / cache_key）・I/O（CachedPort / OllamaReader）・公開。
失敗は ok=False で返す。投げるのはプログラムの誤りだけ。鍵と行の形は空撃ちと同一（実装判断 I3）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable

from .contracts import Cost, RoleCost

log = logging.getLogger("structural_distillation.l0")


# ---------------------------------------------------------------- 口

@dataclass
class RawReply:
    """読み手の生応答。content はフェンスを剥がした後。
    meta: cached（キャッシュ命中）・key（鍵）・cache_miss（cache-only で外れた）・eval_count ほか"""
    ok: bool
    content: str | None
    error: str | None = None
    meta: dict = field(default_factory=dict)


@runtime_checkable
class Reader(Protocol):
    """読み手（問いに答える LLM）・生成器・検証役・偽読み手が共有する口（上流 §3.2「同じ口に差せる」）。

    version は鍵の材料（指示の版: p3 / p2 / p2:retry / xc2）。例外を投げず、失敗は ok=False で返す。

    name は記録と鍵に使う名前。同じモデルを 2 体として差すときは名前を分ける（鍵が分かれて独立に呼ばれる）。
    ⚠ `model`（任意）は**下にいるモデル**の名前。名前を分けても model は同じままにする。
    これが無いと、合成は「読み手も検証役も実は同じモデル」を見分けられない（単一モデル運用の測定 2026-09-23）。
    省略した実装では name をモデル名とみなす（`model_of()` で引く。口の必須項目にはしない）。
    """
    name: str
    calibration: bool

    async def complete(self, messages: list[dict], schema: dict, *, sample: int, version: str) -> RawReply: ...


# ---------------------------------------------------------------- 純粋整形

def with_schema(messages: list[dict], schema: dict, notice: str) -> list[dict]:
    """最後の user メッセージの末尾に notice ＋ スキーマ JSON（sort_keys 無し・既定セパレータ）を足す。元は壊さない。"""
    msgs = [dict(m) for m in messages]
    for m in reversed(msgs):
        if m["role"] == "user":
            m["content"] = m["content"] + notice + json.dumps(schema, ensure_ascii=False)
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


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


def validate(obj: Any, schema: dict, path: str = "$") -> list[str]:
    """JSON Schema の部分集合（type・properties・required・enum・items）で検査し、違反の一覧を返す（実装判断 I14）。"""
    errs: list[str] = []
    t = schema.get("type")
    if t is not None:
        if t == "integer":
            ok = isinstance(obj, int) and not isinstance(obj, bool)
        elif t == "number":
            ok = isinstance(obj, (int, float)) and not isinstance(obj, bool)
        elif t in _TYPES:
            ok = isinstance(obj, _TYPES[t])
        else:
            raise ValueError(f"validate が扱わない型: {t}")
        if not ok:
            return [f"{path}: {t} ではない（{type(obj).__name__}）"]
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: enum の外 {obj!r}")
    if isinstance(obj, dict):
        for k in schema.get("required", ()):
            if k not in obj:
                errs.append(f"{path}.{k}: 必須が無い")
        for k, sub in (schema.get("properties") or {}).items():
            if k in obj:
                errs += validate(obj[k], sub, f"{path}.{k}")
    if isinstance(obj, list) and "items" in schema:
        for i, x in enumerate(obj):
            errs += validate(x, schema["items"], f"{path}[{i}]")
    return errs


def model_of(reader: Reader) -> str:
    """読み手の下にいるモデルの名前。宣言していなければ名前をモデル名とみなす。"""
    return getattr(reader, "model", None) or reader.name


def cache_key(model: str, messages: list[dict], schema: dict, sample: int, version: str) -> str:
    """空撃ち Port._key と同一。messages は with_schema 適用後。num_ctx・think・host は鍵に入らない。"""
    blob = json.dumps({"m": model, "msgs": messages, "schema": schema, "sample": sample, "v": version},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 三段構え

@dataclass
class Structured:
    ok: bool
    obj: Any
    content: str | None
    error: str | None
    attempts: int       # 読み手を呼んだ回数（再送を含む）
    live: int           # うち実際に LLM を呼んだ回数
    cached: int         # うちキャッシュ命中
    missed: int         # うち cache-only で外れた
    key: str | None
    version: str | None


async def structured(reader: Reader, messages: list[dict], schema: dict, *, version: str, sample: int,
                     notice: str, retry: bool = False) -> Structured:
    """三段構え。retry=True のときだけ失敗で version+":retry" の再送 1 回（空撃ちで再送するのは回答だけ。I18）。"""
    msgs = with_schema(messages, schema, notice)
    live = cached = missed = attempts = 0
    last_err: str | None = None
    last_content: str | None = None
    key = used = None
    for v in ([version, f"{version}:retry"] if retry else [version]):
        reply = await reader.complete(msgs, schema, sample=sample, version=v)
        attempts += 1
        used = v
        key = reply.meta.get("key")          # 生応答と鍵は同じ試行のものを組にする（受入 m3）
        last_content = None
        if reply.meta.get("cached"):
            cached += 1
        elif reply.meta.get("cache_miss"):
            missed += 1
        else:
            live += 1
        if not reply.ok:
            last_err = reply.error or "失敗"
            continue
        # アダプタも剥がして返すが、利用側の Reader が剥がさなくても通るようにもう一度。
        # ⚠ 入れ子のフェンスは空撃ちと違って通る（差分表 P17）
        content = strip_fence(reply.content or "")
        last_content = content
        try:
            obj = json.loads(content)
        except ValueError as e:
            last_err = f"parse: {e} / content={content[:80]!r}"
            log.info("L0: JSON でない応答（%s・%s）: %s", reader.name, v, last_err)
            continue
        errs = validate(obj, schema)
        if errs:
            last_err = "schema: " + "; ".join(errs[:3])
            log.info("L0: スキーマ違反（%s・%s）: %s", reader.name, v, last_err)
            continue
        if v != version:
            log.info("L0: 再送で回復（%s・%s）", reader.name, v)
        return Structured(True, obj, content, None, attempts, live, cached, missed, key, used)
    return Structured(False, None, last_content, last_err, attempts, live, cached, missed, key, used)


class Meter:
    """呼び出しを役割（plan / crosscheck / answer）別に数える。偽読み手は calibration_calls に別枠。"""

    def __init__(self) -> None:
        self.cost = Cost()

    def add(self, role: str, s: Structured, *, calibration: bool) -> None:
        if calibration:
            self.cost.calibration_calls += s.attempts
            return
        rc: RoleCost = getattr(self.cost, role)
        rc.live += s.live
        rc.cached += s.cached
        rc.missed += s.missed


# ---------------------------------------------------------------- キャッシュ

class CachedPort:
    """Reader を包む Reader。生応答を鍵で引き、外れたら呼んで追記する（追記のみの JSONL。行の形は空撃ちと同一）。

    - 成功した応答だけ書く（実装判断 I19。失敗を永続化すると再走でも直らない）
    - extra: 読むだけの追加キャッシュ（複数の記録を合わせて読む）
    - read_only: 一切書かない（測定の記録を開くとき）
    - cache_only: 外れたら呼ばずに ok=False（"cache-only miss"）
    - 同じ鍵の同時要求は 1 回にまとめる（合流した側は cached として数える）
    ⚠ 別プロセスからの同一ファイルへの同時追記は守らない
    """

    def __init__(self, reader: Reader, path: str | Path | None = None, *, cache_only: bool = False,
                 read_only: bool = False, extra: Iterable[str | Path] = ()):
        self.reader = reader
        self.name = reader.name
        self.calibration = reader.calibration
        self.model = model_of(reader)
        self.path = Path(path) if path is not None else None
        self.cache_only = cache_only
        self.read_only = read_only
        self._cache: dict[str, dict] = {}
        for p in [*extra, *([self.path] if self.path else [])]:
            self._load(Path(p))
        self._inflight: dict[str, asyncio.Future] = {}
        self.calls_live = self.calls_cached = self.calls_missed = 0

    def _load(self, p: Path) -> None:
        if not p.exists():
            return
        skipped = 0
        with p.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    if not row["payload"].get("ok"):
                        skipped += 1          # 失敗は当たりにしない（I19。空撃ちのキャッシュには失敗の行がある。受入 m1）
                        continue
                    self._cache[row["key"]] = row["payload"]
        if skipped:
            log.info("L0: %s の失敗の行 %d を読み飛ばした", p, skipped)

    @property
    def size(self) -> int:
        """読み込んでいる応答の数。⚠ __len__ にしない（空のキャッシュが偽になり、`planner or 既定` で黙って差し替わる）"""
        return len(self._cache)

    def _append(self, key: str, sample: int, version: str, payload: dict) -> None:
        """1 行を同期で追記する。単一のイベントループでは await を挟まないので排他は要らない。書けなければログに残して続ける。"""
        if self.path is None or self.read_only:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "model": self.name, "sample": sample, "version": version,
                                    "payload": payload}, ensure_ascii=False) + "\n")
        except OSError as e:
            log.warning("L0: キャッシュに書けなかった（%s）: %s", self.path, e)

    async def complete(self, messages: list[dict], schema: dict, *, sample: int, version: str) -> RawReply:
        key = cache_key(self.name, messages, schema, sample, version)
        hit = self._cache.get(key)
        if hit is not None:
            self.calls_cached += 1
            meta = {k: hit.get(k) for k in ("eval_count", "total_duration", "prompt_eval_count") if k in hit}
            return RawReply(bool(hit.get("ok")), hit.get("content"), hit.get("error"), {**meta, "cached": True, "key": key})
        if self.cache_only:
            self.calls_missed += 1
            log.info("L0: cache-only で外れた（%s・%s・sample %s）", self.name, version, sample)
            return RawReply(False, None, "cache-only miss", {"cached": False, "cache_miss": True, "key": key})
        fut = self._inflight.get(key)
        if fut is not None:
            reply = await asyncio.shield(fut)
            self.calls_cached += 1
            return RawReply(reply.ok, reply.content, reply.error, {**reply.meta, "cached": True, "key": key})
        fut = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            try:
                reply = await self.reader.complete(messages, schema, sample=sample, version=version)
            except Exception as e:  # noqa: BLE001 — 口の約束違反も失敗として返す
                reply = RawReply(False, None, f"{type(e).__name__}: {e}", {})
            self.calls_live += 1
            fut.set_result(reply)             # 合流して待っている側を先に放す（追記の失敗で巻き込まない。受入 m2）
            if reply.ok:
                payload = {"ok": True, "content": reply.content, "error": None,
                           "eval_count": reply.meta.get("eval_count"), "total_duration": reply.meta.get("total_duration"),
                           "prompt_eval_count": reply.meta.get("prompt_eval_count")}
                self._cache[key] = payload
                self._append(key, sample, version, payload)
            return RawReply(reply.ok, reply.content, reply.error, {**reply.meta, "cached": False, "key": key})
        finally:
            if not fut.done():
                fut.cancel()
            self._inflight.pop(key, None)


# ---------------------------------------------------------------- アダプタ

class OllamaReader:
    """Ollama の /api/chat（標準ライブラリの urllib を別スレッドで。実装判断 I1）。

    - format=スキーマ（ネイティブ指定。クラウドモデルは守らないので三段構えの ② ③ が要る）
    - think を拒むモデル（400 に "think"）には think 無しで再送
    - 429 / 5xx は backoff·(k+1) 秒待って再送（初回 ＋ retries 回）
    - content はフェンスを剥がして返す（キャッシュ行の content も剥がした後。空撃ちと同一）
    """
    calibration = False

    def __init__(self, model: str, *, name: str | None = None, host: str = "http://localhost:11434", num_ctx: int = 8192,
                 think: bool | None = False, timeout: float = 600, retries: int = 3, backoff: float = 5.0):
        # name を変えると鍵が分かれ、同じモデルを別々の読み手として差せる（model は同じまま）
        self.name = name or model
        self.model = model
        self.host = host.rstrip("/")
        self.num_ctx = num_ctx
        self.think = think
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff

    def _post(self, messages: list[dict], schema: dict, think: bool | None) -> dict:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False, "format": schema,
                                "options": {"num_ctx": self.num_ctx}}
        if think is not None:
            body["think"] = think
        req = urllib.request.Request(f"{self.host}/api/chat", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _call(self, messages: list[dict], schema: dict) -> RawReply:
        try:
            raw = None
            for attempt in range(self.retries + 1):
                try:
                    try:
                        raw = self._post(messages, schema, self.think)
                    except urllib.error.HTTPError as e:
                        if e.code == 400 and self.think is not None and "think" in e.read().decode("utf-8", "ignore"):
                            log.info("L0: %s は think を受け付けない。think 無しで再送", self.name)
                            raw = self._post(messages, schema, None)
                        else:
                            raise
                    break
                except urllib.error.HTTPError as e:
                    if e.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                        log.info("L0: %s が %s。%.0f 秒待って再送", self.name, e.code, self.backoff * (attempt + 1))
                        time.sleep(self.backoff * (attempt + 1))
                        continue
                    raise
            assert raw is not None
            content = strip_fence((raw.get("message") or {}).get("content") or "")
            return RawReply(True, content, None, {"eval_count": raw.get("eval_count"),
                                                  "total_duration": raw.get("total_duration"),
                                                  "prompt_eval_count": raw.get("prompt_eval_count")})
        except Exception as e:  # noqa: BLE001 — 通信の失敗も型付きの失敗として返す
            return RawReply(False, None, f"{type(e).__name__}: {e}", {})

    async def complete(self, messages: list[dict], schema: dict, *, sample: int, version: str) -> RawReply:  # noqa: ARG002
        return await asyncio.to_thread(self._call, messages, schema)


class FakeReader:
    """較正用の決定論の読み手（上流 S11）。回答スキーマ（verdict を持つ）にだけ答える。calibration=True。

    kind: all_yes（全問 述べている）/ all_no（全問 否定している）/ all_undetermined（全問 触れていない）/ random。
    根拠は常に "s1"（単位列の先頭 id。どの単位化規則でも実在する）。
    random は sha1(seed, messages, sample) で決める（呼び出し順に依存しない）。
    """
    calibration = True
    KINDS = ("all_yes", "all_no", "all_undetermined", "random")

    def __init__(self, kind: str, *, seed: int = 0):
        if kind not in self.KINDS:
            raise ValueError(f"未知の偽読み手: {kind!r}（{', '.join(self.KINDS)}）")
        self.kind = kind
        self.seed = seed
        self.name = self.model = f"fake:{kind}"

    async def complete(self, messages: list[dict], schema: dict, *, sample: int, version: str) -> RawReply:  # noqa: ARG002
        verdict = (schema.get("properties") or {}).get("verdict") or {}
        words = verdict.get("enum") or []
        if len(words) != 3:
            return RawReply(False, None, "偽読み手は回答スキーマにだけ答える", {})
        states, denies, silent = words
        if self.kind == "random":
            h = hashlib.sha1(json.dumps([self.seed, messages, sample], ensure_ascii=False).encode("utf-8")).digest()
            word = words[h[0] % 3]
        else:
            word = {"all_yes": states, "all_no": denies, "all_undetermined": silent}[self.kind]
        ev = [] if word == silent else ["s1"]
        return RawReply(True, json.dumps({"verdict": word, "evidence": ev}, ensure_ascii=False), None, {})
