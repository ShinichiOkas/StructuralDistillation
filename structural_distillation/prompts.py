"""指示の集合（PromptSet）: 読み込みと描画（実装設計 §5）。層ではない（facility）。

指示は用途（言語・文体）で変わる値なので外に出す。規則（スキーマ・検査・集約）はコードに残す（上流 J9）。
1 集合 ＝ 1 ファイルの JSON（`prompt_sets/<name>/set.json`。実装判断 I20）。

- 3 値・向き・排他性の語は、この集合が唯一の持ち主。テンプレートにも回答スキーマの enum にも、ここから埋める
- 鍵に入るのは各役割の版の文字列（`p3` / `p2` / `xc2`）。digest は記録にだけ残る
- テンプレートの digest が合わなければ警告する（版を上げ忘れた編集がキャッシュに黙って混ざるのを防ぐ。実装判断 I15）
"""
from __future__ import annotations

import hashlib
import json
import logging
import string
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .contracts import PromptVersion, Verdict

log = logging.getLogger("structural_distillation.prompts")

ORIENT_CODES = ("SUPPORT", "REFUTE", "NEITHER")
EXCL_CODES = ("COMPATIBLE", "EXCLUSIVE")
# 各テンプレートの user が必ず持つ差し込み口（無いと本文や記述が指示に入らない。受入 m7）
REQUIRED = {"plan": {"units_text", "proposition", "n_axes"}, "answer": {"units_text", "claim"},
            "orient": {"proposition", "claim", "options"}, "exclusive": {"claim_a", "claim_b"}}


def _digest(*parts: str) -> str:
    return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PromptTemplate:
    version: str
    system: str
    user: str
    digest: str
    options: tuple[str, ...] = ()

    def fresh_digest(self) -> str:
        return _digest(self.system, self.user, *self.options)


@dataclass(frozen=True)
class PromptSet:
    name: str
    verdicts: dict[Verdict, str]
    orientations: dict[str, str]
    exclusivity: dict[str, str]
    schema_notice: str
    plan: PromptTemplate
    answer: PromptTemplate
    orient: PromptTemplate
    exclusive: PromptTemplate
    digest: str

    # ------------------------------------------------ 読み込み

    @classmethod
    def from_json(cls, text: str, *, origin: str = "<string>") -> PromptSet:
        d = json.loads(text)

        def tpl(key: str) -> PromptTemplate:
            t = d[key]
            pt = PromptTemplate(version=t["version"], system=t["system"], user=t["user"],
                                digest=t.get("digest", ""), options=tuple(t.get("options") or ()))
            if pt.digest and pt.digest != pt.fresh_digest():
                log.warning("指示 %s:%s が版（%s）を上げずに変更されている（digest 不一致）。キャッシュの鍵は版だけで引くので、"
                            "古い応答が混ざる。版を上げること", origin, key, pt.version)
            for s in (pt.system, pt.user, *pt.options):
                if not string.Template(s).is_valid():
                    raise ValueError(f"指示 {origin}:{key} に不正な $ がある")
            missing = REQUIRED[key] - set(string.Template(pt.user).get_identifiers())
            if missing:
                raise ValueError(f"指示 {origin}:{key} の user に差し込み口 {sorted(missing)} が無い")
            return pt

        verdicts = {Verdict[k]: v for k, v in d["verdicts"].items()}
        if set(verdicts) != set(Verdict):
            raise ValueError(f"指示 {origin}: verdicts は STATES/DENIES/SILENT の 3 つ")
        if set(d["orientations"]) != set(ORIENT_CODES) or set(d["exclusivity"]) != set(EXCL_CODES):
            raise ValueError(f"指示 {origin}: orientations / exclusivity の鍵が足りない")
        for group in (list(verdicts.values()), list(d["orientations"].values()), list(d["exclusivity"].values())):
            if len(set(group)) != len(group):
                raise ValueError(f"指示 {origin}: 語が重複している {group}（語から符号を一意に引けない）")
        if len(d["orient"].get("options") or ()) != 3:
            raise ValueError(f"指示 {origin}: orient.options は SUPPORT・REFUTE・NEITHER の 3 行")
        return cls(name=d["name"], verdicts=verdicts, orientations=dict(d["orientations"]),
                   exclusivity=dict(d["exclusivity"]), schema_notice=d["schema_notice"],
                   plan=tpl("plan"), answer=tpl("answer"), orient=tpl("orient"), exclusive=tpl("exclusive"),
                   digest=hashlib.sha1(text.encode("utf-8")).hexdigest())

    @classmethod
    def load(cls, path: str | Path) -> PromptSet:
        p = Path(path)
        if p.is_dir():
            p = p / "set.json"
        return cls.from_json(p.read_text(encoding="utf-8"), origin=str(p))

    @classmethod
    def builtin(cls, name: str = "ja") -> PromptSet:
        ref = resources.files(__package__).joinpath("prompt_sets", name, "set.json")
        return cls.from_json(ref.read_text(encoding="utf-8"), origin=f"builtin:{name}")

    def version(self) -> PromptVersion:
        return PromptVersion(set=self.name, plan=self.plan.version, answer=self.answer.version,
                             orient=self.orient.version, exclusive=self.exclusive.version, digest=self.digest)

    # ------------------------------------------------ 語

    def verdict_words(self) -> list[str]:
        """回答スキーマの enum（STATES, DENIES, SILENT の順。空撃ちと同じ並び）。"""
        return [self.verdicts[Verdict.STATES], self.verdicts[Verdict.DENIES], self.verdicts[Verdict.SILENT]]

    def verdict_of(self, word: str) -> Verdict | None:
        for v, w in self.verdicts.items():
            if w == word:
                return v
        return None

    def orientation_words(self) -> list[str]:
        return [self.orientations[c] for c in ORIENT_CODES]

    def orientation_of(self, word: str) -> str | None:
        for c, w in self.orientations.items():
            if w == word:
                return c
        return None

    def exclusivity_words(self) -> list[str]:
        return [self.exclusivity[c] for c in EXCL_CODES]

    def exclusivity_of(self, word: str) -> str | None:
        for c, w in self.exclusivity.items():
            if w == word:
                return c
        return None

    # ------------------------------------------------ 描画（messages は system と user の 2 つだけ）

    @staticmethod
    def _msgs(t: PromptTemplate, **kw) -> list[dict]:
        return [{"role": "system", "content": string.Template(t.system).substitute(**kw)},
                {"role": "user", "content": string.Template(t.user).substitute(**kw)}]

    def plan_messages(self, units_text: str, proposition: str, n_axes: int) -> list[dict]:
        return self._msgs(self.plan, units_text=units_text, proposition=proposition, n_axes=n_axes)

    def answer_messages(self, units_text: str, claim: str) -> list[dict]:
        return self._msgs(self.answer, units_text=units_text, claim=claim,
                          v_states=self.verdicts[Verdict.STATES], v_denies=self.verdicts[Verdict.DENIES],
                          v_silent=self.verdicts[Verdict.SILENT])

    def orient_messages(self, proposition: str, claim: str, reversed_order: bool) -> list[dict]:
        o = self.orientations
        opts = [string.Template(x).substitute(o_support=o["SUPPORT"], o_refute=o["REFUTE"], o_neither=o["NEITHER"])
                for x in self.orient.options]
        if reversed_order:
            opts = opts[::-1]
        return self._msgs(self.orient, proposition=proposition, claim=claim, options="\n".join(opts))

    def exclusive_messages(self, claim_a: str, claim_b: str) -> list[dict]:
        x = self.exclusivity
        return self._msgs(self.exclusive, claim_a=claim_a, claim_b=claim_b,
                          x_compatible=x["COMPATIBLE"], x_exclusive=x["EXCLUSIVE"])


# ---------------------------------------------------------------- スキーマ（契約。語は指示の集合から）
#
# スキーマは指示の末尾に JSON（sort_keys 無し）で埋め込まれるので、dict の挿入順と enum の並びがキャッシュの鍵に効く。
# 並びは空撃ち（probe3.PLAN_SCHEMA・probe.ANSWER_SCHEMA・probe4_crosscheck.ORIENT_SCHEMA / EXCL_SCHEMA）と同一。
# ⚠ additionalProperties などの語を足すと鍵が変わる（実装判断 I14）。スキーマの持ち主はここだけ。

def plan_schema() -> dict:
    return {"type": "object",
            "properties": {"axes": {"type": "array",
                                    "items": {"type": "object",
                                              "properties": {"axis": {"type": "string"},
                                                             "claim_support": {"type": "string"},
                                                             "claim_refute": {"type": "string"}},
                                              "required": ["axis", "claim_support", "claim_refute"]}}},
            "required": ["axes"]}


def answer_schema(p: PromptSet) -> dict:
    return {"type": "object",
            "properties": {"verdict": {"type": "string", "enum": p.verdict_words()},
                           "evidence": {"type": "array", "items": {"type": "string"}}},
            "required": ["verdict", "evidence"]}


def orient_schema(p: PromptSet) -> dict:
    return {"type": "object", "properties": {"orientation": {"type": "string", "enum": p.orientation_words()}},
            "required": ["orientation"]}


def exclusive_schema(p: PromptSet) -> dict:
    return {"type": "object", "properties": {"compatible": {"type": "string", "enum": p.exclusivity_words()}},
            "required": ["compatible"]}


def get_prompts(prompts: str | PromptSet) -> PromptSet:
    """名前（組み込み）か実体を受け取る。"""
    if isinstance(prompts, PromptSet):
        return prompts
    return PromptSet.builtin(prompts)
