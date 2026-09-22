"""問いの保存庫: 同じ命題と本文なら、過去に作った問いの集合を再利用する（師匠 2026-09-23）。層ではない（facility）。

師匠の言葉（原文）:
    データをJSONで保持して同じ命題と本文の場合は過去に作成した問を再利用できる仕組みにして。

- 1 本文 × 1 命題 ＝ 1 JSON ファイル（`<鍵>.json`）。人が開いて読める・直せる（合意 question-store.md Q3）
- 鍵 ＝ 単位化した後の単位列 ＋ 単位化規則の版 ＋ 命題（前後の空白を除く）の sha1（Q1）。
  文の前後の空白や改行の違いは同じ本文とみなす。生成器・軸数・指示の版は鍵に入れない（Q2）
- 交差検証の結果ごと保存し、外した軸も含めて同じ問いの集合を返す（Q4）
- 作り直すときは古いファイルを `<鍵>.superseded-<時刻>[-n].json` に写して残す（Q5。物理削除しない。名前は衝突させない）
- 読めないファイルは上書きせず StoreError（Q6）
- 書き込みは鍵ごとのロックファイルで直列化し、一時ファイル → 置き換えで一度に行う（Q8。ペア固有 Skill
  「書き込みはロックで直列化する」。受入で v1 の「排他なし」を改めた）
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Generator

from . import __version__
from .contracts import QuestionSet, StoreError, Unit

log = logging.getLogger("structural_distillation.store")

KEY_VERSION = 1       # 鍵の作り方の版（上げると全部の鍵が変わる。ファイルの版とは別）
SCHEMA_VERSION = 1    # ファイルの形の版（上げたら読み込みで StoreError になる）
_SUPERSEDED = ".superseded-"
_HEX = re.compile(r"[0-9a-f]{1,40}")


def question_key(units: list[Unit], units_rule: str, proposition: str) -> str:
    """同じ本文（単位列）・同じ単位化規則・同じ命題なら同じ鍵。units_rule は `units.rule_version()` の値。"""
    blob = json.dumps({"v": KEY_VERSION, "units_rule": units_rule, "proposition": proposition.strip(),
                       "units": [u.text for u in units]}, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


class QuestionStore:
    """clock はテストで時刻を注入する口（Windows の now() は約 15.6ms に量子化される）。
    lock_wait: 他の書き手のロックを待つ秒数。lock_stale: これより古いロックは残骸とみなして外す秒数。"""

    def __init__(self, root: str | os.PathLike, *, clock: Callable[[], datetime] | None = None,
                 lock_wait: float = 30.0, lock_stale: float = 120.0):
        self.root = Path(root)
        self._clock = clock or (lambda: datetime.now().astimezone())
        self.lock_wait = lock_wait
        self.lock_stale = lock_stale

    def path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    # ------------------------------------------------ 読む

    def _read(self, p: Path) -> dict:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise StoreError(f"問いの保存庫のファイルが読めない: {p}: {e}") from e
        if not isinstance(d, dict) or d.get("schema_version") != SCHEMA_VERSION or not isinstance(d.get("question_set"), dict):
            raise StoreError(f"問いの保存庫のファイルの形が違う（schema_version {SCHEMA_VERSION} ではない）: {p}")
        return d

    def load(self, key: str, *, units: list[Unit] | None = None, proposition: str | None = None) -> QuestionSet | None:
        """鍵の問いの集合。無ければ None。units / proposition を渡すと、中身が鍵と合っているかも確かめる。"""
        p = self.path(key)
        if not p.exists():
            return None
        d = self._read(p)
        try:
            if d.get("key") != key:
                raise StoreError(f"ファイル名の鍵と中身の鍵が違う: {p}")
            if proposition is not None and d.get("proposition") != proposition.strip():
                raise StoreError(f"保存された命題が違う: {p}")
            if units is not None and [u["text"] for u in d.get("units") or []] != [u.text for u in units]:
                raise StoreError(f"保存された本文が違う: {p}")
            qs = QuestionSet.from_dict(d["question_set"])
        except StoreError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise StoreError(f"問いの集合として読めない: {p}: {type(e).__name__}: {e}") from e
        ids = [a.id for a in qs.axes]
        if len(set(ids)) != len(ids) or len(set(qs.active_ids)) != len(qs.active_ids) or not set(qs.active_ids) <= set(ids):
            raise StoreError(f"軸 id か active_ids が重複している、または軸に無い: {p}")
        return qs

    def entry(self, key: str) -> dict:
        """ファイルの中身そのもの（CLI の show 用）。"""
        return self._read(self.path(key))

    def entries(self) -> list[dict]:
        """保存されている問いの集合の一覧（作り直しで退けた古いファイルは除く）。読めないファイルは error を付けて返す。"""
        if not self.root.is_dir():
            return []
        out = []
        for p in sorted(self.root.glob("*.json")):
            if _SUPERSEDED in p.name:
                continue
            try:
                d = self._read(p)
                qs = d["question_set"]
                units = d.get("units") or []
                out.append({"key": d.get("key"), "created_at": d.get("created_at"), "proposition": d.get("proposition"),
                            "units_rule": d.get("units_rule"), "n_units": len(units),
                            "first_unit": (units[0].get("text", "") if units else ""), "planner": qs.get("planner"),
                            "n_axes": len(qs.get("axes") or []), "n_active": len(qs.get("active_ids") or []),
                            "generations": self.generations(p.stem), "path": str(p), "error": None})
            except (StoreError, AttributeError, TypeError, IndexError, KeyError) as e:
                out.append({"key": p.stem, "path": str(p), "error": str(e)})
        return out

    def generations(self, key: str) -> int:
        """この鍵で作った問いの集合の数（今のもの ＋ 退けた古いもの）。"""
        if not self.root.is_dir():
            return 0
        return int(self.path(key).exists()) + len(list(self.root.glob(f"{key}{_SUPERSEDED}*.json")))

    def resolve(self, prefix: str) -> str:
        """鍵の先頭の何文字か（16 進）から鍵を引く（CLI 用）。一意でなければ StoreError。"""
        if not _HEX.fullmatch(prefix or ""):
            raise StoreError(f"鍵は 16 進の文字（0-9a-f）で指定する: {prefix!r}")
        keys = [p.stem for p in self.root.glob(f"{prefix}*.json") if _SUPERSEDED not in p.name] if self.root.is_dir() else []
        if len(keys) != 1:
            raise StoreError(f"鍵 {prefix!r} に当たるファイルが {len(keys)} 個")
        return keys[0]

    # ------------------------------------------------ 書く

    def check_writable(self) -> None:
        """書き込める置き場所か（生成の前に確かめる。LLM の費用を払ってから保存できないのを避ける）。"""
        if self.root.exists() and not self.root.is_dir():
            raise StoreError(f"問いの保存庫の置き場所がディレクトリではない: {self.root}")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise StoreError(f"問いの保存庫を作れない: {self.root}: {e}") from e

    @contextmanager
    def _lock(self, key: str) -> Generator[None, None, None]:
        lock = self.root / f"{key}.lock"
        start = time.monotonic()
        while True:
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    age = time.time() - lock.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age > self.lock_stale:
                    log.warning("問いの保存庫: 古いロック %s（%.0f 秒前）を残骸とみなして外す", lock.name, age)
                    try:
                        lock.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.monotonic() - start > self.lock_wait:
                    raise StoreError(f"問いの保存庫: 他の書き手のロックが外れない（{lock}）") from None
                time.sleep(0.05)
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            yield
        finally:
            try:
                lock.unlink()
            except FileNotFoundError:
                pass

    def _superseded_path(self, key: str, now: datetime) -> Path:
        base = f"{key}{_SUPERSEDED}{now.strftime('%Y%m%dT%H%M%S%f')}"
        p, n = self.root / f"{base}.json", 0
        while p.exists():                     # 同じ時刻に 2 回作り直しても消さない（受入 C1）
            n += 1
            p = self.root / f"{base}-{n}.json"
        return p

    def save(self, key: str, qs: QuestionSet, *, units: list[Unit], units_rule: str,
             proposition: str) -> tuple[Path, Path | None]:
        """保存する。同じ鍵のファイルがあれば、別名に写して残してから置き換える。(保存先, 退けた古いファイル) を返す。"""
        self.check_writable()
        now = self._clock()
        record = {"schema_version": SCHEMA_VERSION, "key": key, "created_at": now.isoformat(timespec="seconds"),
                  "library": __version__, "proposition": proposition.strip(), "units_rule": units_rule,
                  "units": [{"id": u.id, "text": u.text} for u in units], "question_set": qs.to_dict()}
        body = json.dumps(record, ensure_ascii=False, indent=1)
        p = self.path(key)
        with self._lock(key):
            fd, tmp = tempfile.mkstemp(dir=str(self.root), prefix=f".{key}.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(body)
                old = None
                if p.exists():
                    old = self._superseded_path(key, now)
                    shutil.copy2(p, old)       # 先に写す。今のファイルは置き換えの瞬間まで在り続ける
                os.replace(tmp, p)
            except BaseException:
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass
                raise
        if old is not None:
            log.info("問いの保存庫: 作り直したので古い問いの集合を %s に残した", old.name)
        return p, old
