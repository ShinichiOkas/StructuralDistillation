"""問いの保存庫: 同じ命題と本文なら、過去に作った問いの集合を再利用する（師匠 2026-09-23）。層ではない（facility）。

師匠の言葉（原文）:
    データをJSONで保持して同じ命題と本文の場合は過去に作成した問を再利用できる仕組みにして。

- 1 本文 × 1 命題 ＝ 1 JSON ファイル（`<鍵>.json`）。人が開いて読める・直せる（合意 question-store.md Q3）
- 鍵 ＝ 単位化した後の単位列 ＋ 単位化規則の版 ＋ 命題（前後の空白を除く）の sha1（Q1）。
  文の前後の空白や改行の違いは同じ本文とみなす。生成器・軸数・指示の版は鍵に入れない（Q2）
- 交差検証の結果ごと保存し、外した軸も含めて同じ問いの集合を返す（Q4）
- 作り直すときは古いファイルを `<鍵>.superseded-<時刻>.json` に改名して残す（Q5。物理削除しない）
- 読めないファイルは上書きせず StoreError（Q6）
- 書き込みは一時ファイル → 置き換え（Q8。同じ鍵に同時に書いたら後勝ち）
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from . import __version__
from .contracts import QuestionSet, StoreError, Unit

log = logging.getLogger("structural_distillation.store")

SCHEMA_VERSION = 1
_SUPERSEDED = ".superseded-"


def question_key(units: list[Unit], units_rule: str, proposition: str) -> str:
    """同じ本文（単位列）・同じ単位化規則・同じ命題なら同じ鍵。units_rule は `units.rule_version()` の値。"""
    blob = json.dumps({"v": SCHEMA_VERSION, "units_rule": units_rule, "proposition": proposition.strip(),
                       "units": [u.text for u in units]}, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


class QuestionStore:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)

    def path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    # ------------------------------------------------ 読む

    def _read(self, p: Path) -> dict:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise StoreError(f"問いの保存庫のファイルが読めない: {p}: {e}") from e
        if not isinstance(d, dict) or d.get("schema_version") != SCHEMA_VERSION or "question_set" not in d:
            raise StoreError(f"問いの保存庫のファイルの形が違う（schema_version {SCHEMA_VERSION} ではない）: {p}")
        return d

    def load(self, key: str, *, units: list[Unit] | None = None, proposition: str | None = None) -> QuestionSet | None:
        """鍵の問いの集合。無ければ None。units / proposition を渡すと、中身が鍵と合っているかも確かめる。"""
        p = self.path(key)
        if not p.exists():
            return None
        d = self._read(p)
        if d.get("key") != key:
            raise StoreError(f"ファイル名の鍵と中身の鍵が違う: {p}")
        if proposition is not None and d.get("proposition") != proposition.strip():
            raise StoreError(f"保存された命題が違う: {p}")
        if units is not None and [u["text"] for u in d.get("units") or []] != [u.text for u in units]:
            raise StoreError(f"保存された本文が違う: {p}")
        try:
            qs = QuestionSet.from_dict(d["question_set"])
        except (KeyError, TypeError, ValueError) as e:
            raise StoreError(f"問いの集合として読めない: {p}: {e}") from e
        ids = [a.id for a in qs.axes]
        if len(set(ids)) != len(ids) or len(set(qs.active_ids)) != len(qs.active_ids) or not set(qs.active_ids) <= set(ids):
            raise StoreError(f"軸 id か active_ids が重複している、または軸に無い: {p}")
        return qs

    def entry(self, key: str) -> dict:
        """ファイルの中身そのもの（CLI の show 用）。"""
        return self._read(self.path(key))

    def entries(self) -> list[dict]:
        """保存されている問いの集合の一覧（作り直しで退けた古いファイルは除く）。読めないファイルは error を付けて返す。"""
        if not self.root.exists():
            return []
        out = []
        for p in sorted(self.root.glob("*.json")):
            if _SUPERSEDED in p.name:
                continue
            try:
                d = self._read(p)
                qs = d["question_set"]
                out.append({"key": d.get("key"), "created_at": d.get("created_at"), "proposition": d.get("proposition"),
                            "units_rule": d.get("units_rule"), "n_units": len(d.get("units") or []),
                            "first_unit": (d.get("units") or [{}])[0].get("text", ""), "planner": qs.get("planner"),
                            "n_axes": len(qs.get("axes") or []), "n_active": len(qs.get("active_ids") or []),
                            "path": str(p), "error": None})
            except StoreError as e:
                out.append({"key": p.stem, "path": str(p), "error": str(e)})
        return out

    def resolve(self, prefix: str) -> str:
        """鍵の先頭の何文字かから鍵を引く（CLI 用）。一意でなければ StoreError。"""
        keys = [p.stem for p in self.root.glob(f"{prefix}*.json") if _SUPERSEDED not in p.name]
        if len(keys) != 1:
            raise StoreError(f"鍵 {prefix!r} に当たるファイルが {len(keys)} 個")
        return keys[0]

    # ------------------------------------------------ 書く

    def save(self, key: str, qs: QuestionSet, *, units: list[Unit], units_rule: str, proposition: str) -> Path:
        """保存する。同じ鍵のファイルがあれば、消さずに別名にしてから書く（作り直し）。"""
        self.root.mkdir(parents=True, exist_ok=True)
        p = self.path(key)
        now = datetime.now().astimezone()
        if p.exists():
            old = self.root / f"{key}{_SUPERSEDED}{now.strftime('%Y%m%dT%H%M%S%f')}.json"
            os.replace(p, old)
            log.info("問いの保存庫: 作り直すので古い問いの集合を %s に退けた", old.name)
        record = {"schema_version": SCHEMA_VERSION, "key": key, "created_at": now.isoformat(timespec="seconds"),
                  "library": __version__, "proposition": proposition.strip(), "units_rule": units_rule,
                  "units": [{"id": u.id, "text": u.text} for u in units], "question_set": qs.to_dict()}
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
        return p
