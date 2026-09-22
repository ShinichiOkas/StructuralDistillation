"""契約: 層の間で受け渡すデータ型・列挙・例外・既定値（実装設計 §3）。層ではない。

- すべて JSON に往復できる（`to_dict` / `from_dict`）。列挙は `str` を継承した `Enum`
- 既定値はすべて仮説（師匠 2026-09-22「値は全て仮設」）。出所は各 docstring
"""
from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Literal

Side = Literal["support", "refute"]
SIDES: tuple[Side, Side] = ("support", "refute")


# ---------------------------------------------------------------- 列挙

class Verdict(str, enum.Enum):
    """本文と記述の関係の 3 値（上流 §5.2）。LLM に見せる語は指示の集合が持つ（実装判断 I5）。"""
    STATES = "STATES"    # 述べている
    DENIES = "DENIES"    # 否定している
    SILENT = "SILENT"    # 触れていない


class Label(str, enum.Enum):
    """読み手ごとの札（上流 §7.4 の 5 値）。"""
    LEAN_SUPPORT = "LEAN_SUPPORT"          # 偏り（支持）
    LEAN_REFUTE = "LEAN_REFUTE"            # 偏り（反証）
    SPLIT = "SPLIT"                        # 割れる
    NO_EVIDENCE = "NO_EVIDENCE"            # 本文に根拠が無い
    INSTRUMENT_FAULT = "INSTRUMENT_FAULT"  # 計器不良


NO_VALUE_LABELS = frozenset({Label.NO_EVIDENCE, Label.INSTRUMENT_FAULT})


# ---------------------------------------------------------------- 例外

class InputError(ValueError):
    """入力の契約違反（上流 F1・F2）。LLM を 1 回も呼ばずに拒否する。"""


class PlanningFailed(RuntimeError):
    """生成器が再試行上限までにハーネスの規則を満たせない（上流 F3）。`attempts` に違反した規則が残る。"""

    def __init__(self, attempts: list[Attempt]):
        self.attempts = attempts
        tail = "; ".join(f"試行 {a.index}: {a.violations or a.error}" for a in attempts)
        super().__init__(f"問いの生成が規則を満たせなかった（{len(attempts)} 試行）: {tail}")


# ---------------------------------------------------------------- 直列化の補助

def to_jsonable(obj: Any) -> Any:
    """dataclass・列挙・タプルを JSON に載る形へ。`Attempt.index` は `"try"` に写す（空撃ちの記録と同じ鍵）。"""
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, Attempt):
        return {"try": obj.index, "violations": list(obj.violations), "n_axes": obj.n_axes, "error": obj.error}
    if is_dataclass(obj) and not isinstance(obj, type):
        out = {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
        kind = getattr(obj, "KIND", None)
        if kind:
            out = {"type": kind, **out}
        return out
    if isinstance(obj, dict):
        return {(k.value if isinstance(k, enum.Enum) else k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    return obj


# ---------------------------------------------------------------- 入力

@dataclass(frozen=True)
class Budget:
    """予算（上流 §4.1）。既定は測定 2 周目の構成（軸 6・ちょうど・再試行 2・交差検証 ON）。

    - axes_min / axes_max: None なら axes と同じ（ちょうど。実装判断 I7）
    - plan_retries: 初回に加える再試行の数（初回 ＋ 2 ＝ 3 試行。空撃ちと同じ）
    - workers: 同時に飛ばす呼び出し数（既定 1: ローカルの読み手に安全側）
    - max_chars: 本文の上限（F1。仮置き 12,000。実装判断 I16）
    """
    axes: int = 6
    axes_min: int | None = None
    axes_max: int | None = None
    samples: int = 1
    plan_retries: int = 2
    workers: int = 1
    crosscheck: bool = True
    max_chars: int = 12000

    @property
    def lo(self) -> int:
        return self.axes if self.axes_min is None else self.axes_min

    @property
    def hi(self) -> int:
        return self.axes if self.axes_max is None else self.axes_max

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, d: dict) -> Budget:
        return cls(**{f.name: d[f.name] for f in fields(cls) if f.name in d})


@dataclass(frozen=True)
class Thresholds:
    """札の閾値（上流 §7.4）。κ だけが測定 2 周目で昇格（K13）。ι・ρ・ω・δ は仮置き。すべて仮説。"""
    iota: float = 0.3     # 無効率 ≥ ι → 計器不良（仮置き。既知不良が無く昇格できない）
    kappa: float = 0.667  # 矛盾率 ≥ κ → 計器不良（健全側の最大 0.333 と全問 Yes 1.0 の中点）
    rho: float = 0.5      # 有効率 < ρ → 本文に根拠が無い（仮置き。K13 ⑵ 未満）
    omega: float = 0.5    # 幅 ≤ ω → 偏り（仮置き）
    delta: float = 0.2    # 読み手間 Δ > δ → 読み手が割れた（仮置き）

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, d: dict) -> Thresholds:
        return cls(**{f.name: d[f.name] for f in fields(cls) if f.name in d})


@dataclass(frozen=True)
class Probability:
    """出力型: 真偽確率 [0, 1]。値は度合い p そのもの。"""
    KIND = "probability"


@dataclass(frozen=True)
class Ordinal:
    """出力型: 順序尺度（K 段）。

    - labels: 段のラベル（長さ K。任意）
    - bounds: 昇順の K−1 個の切れ目（(0, 1) の中。任意）。省略時は K 等分（上流 J5・仮置き）
    """
    k: int
    labels: tuple[str, ...] | None = None
    bounds: tuple[float, ...] | None = None
    KIND = "ordinal"


OutputType = Probability | Ordinal


def output_to_dict(o: OutputType) -> dict:
    return to_jsonable(o)


def output_from_dict(d: dict) -> OutputType:
    if d.get("type") == "probability":
        return Probability()
    if d.get("type") == "ordinal":
        return Ordinal(k=d["k"], labels=tuple(d["labels"]) if d.get("labels") else None,
                       bounds=tuple(d["bounds"]) if d.get("bounds") else None)
    raise ValueError(f"未知の出力型: {d!r}")


@dataclass(frozen=True)
class PromptVersion:
    """指示の版。鍵に入るのは各役割の版の文字列（空撃ちと同一）。digest は記録にだけ残る。"""
    set: str
    plan: str
    answer: str
    orient: str
    exclusive: str
    digest: str

    @classmethod
    def from_dict(cls, d: dict) -> PromptVersion:
        return cls(**{f.name: d[f.name] for f in fields(cls)})


# ---------------------------------------------------------------- 中間の産物

@dataclass(frozen=True)
class Unit:
    id: str
    text: str


@dataclass(frozen=True)
class Axis:
    """軸 ＝ 互いに排他な記述の対（上流 §5.1・U1）。記述は LLM が返したまま（空白も落とさない。鍵に入る）。"""
    id: str
    name: str
    claim_support: str
    claim_refute: str
    origin: Literal["initial", "retry"] = "initial"
    kind: str = "substantive"   # 実装判断 I12: 検出項目・同義対を将来同じ記録に載せる欄

    def claim(self, side: Side) -> str:
        return self.claim_support if side == "support" else self.claim_refute

    @classmethod
    def from_dict(cls, d: dict) -> Axis:
        return cls(**{f.name: d[f.name] for f in fields(cls) if f.name in d})


@dataclass(frozen=True)
class Attempt:
    """生成の 1 試行。欄名は index（`try` は予約語）、JSON では "try"。"""
    index: int
    violations: tuple[str, ...] = ()
    n_axes: int | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> Attempt:
        return cls(index=d.get("try", d.get("index", 0)), violations=tuple(d.get("violations") or ()),
                   n_axes=d.get("n_axes"), error=d.get("error"))


@dataclass
class CrossCheck:
    """向きの交差検証（H12）の記録。flagged だけが軸を外す。weak・nonexclusive は診断。"""
    verifiers: list[str]
    votes: list[dict]
    flagged: list[str]
    weak: list[str]
    nonexclusive: list[str]

    @classmethod
    def from_dict(cls, d: dict) -> CrossCheck:
        return cls(**{f.name: d[f.name] for f in fields(cls)})


@dataclass
class QuestionSet:
    """問いの集合（L1 の OUT）。source は生成したか（generated）、利用側が渡したか（given）。"""
    axes: list[Axis]
    active_ids: list[str]
    planner: str
    prompt: PromptVersion
    budget: Budget
    attempts: list[Attempt] = field(default_factory=list)
    crosscheck: CrossCheck | None = None
    source: Literal["generated", "given"] = "generated"

    @property
    def active_axes(self) -> list[Axis]:
        keep = set(self.active_ids)
        return [a for a in self.axes if a.id in keep]

    @property
    def retries(self) -> int:
        """採用した試行の番号（＝ 再試行の回数）。渡された問いの集合は元の試行の記録を引き継ぐ。記録が無ければ 0。"""
        ok = [a.index for a in self.attempts if not a.violations and not a.error]
        return ok[-1] if ok else 0

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, d: dict) -> QuestionSet:
        return cls(axes=[Axis.from_dict(a) for a in d["axes"]], active_ids=list(d["active_ids"]),
                   planner=d["planner"], prompt=PromptVersion.from_dict(d["prompt"]),
                   budget=Budget.from_dict(d["budget"]), attempts=[Attempt.from_dict(a) for a in d.get("attempts") or []],
                   crosscheck=CrossCheck.from_dict(d["crosscheck"]) if d.get("crosscheck") else None,
                   source=d.get("source", "generated"))


@dataclass
class Answer:
    """回答 1 件（問い × 側 × 標本）。raw はフェンスを剥がした応答本文（上流 §8「生応答」）。"""
    axis_id: str
    side: Side
    sample: int
    verdict: Verdict | None
    evidence_raw: list = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    valid: bool = False
    error: str | None = None
    raw: str | None = None
    key: str | None = None
    cached: bool = False

    @property
    def code(self) -> str:
        """集約で使う答えの符号: STATES / DENIES / SILENT / INVALID。"""
        if not self.valid or self.verdict is None:
            return "INVALID"
        return self.verdict.value

    @classmethod
    def from_dict(cls, d: dict) -> Answer:
        v = d.get("verdict")
        return cls(axis_id=d["axis_id"], side=d["side"], sample=d["sample"], verdict=Verdict(v) if v else None,
                   evidence_raw=list(d.get("evidence_raw") or []), evidence=list(d.get("evidence") or []),
                   valid=d.get("valid", False), error=d.get("error"), raw=d.get("raw"), key=d.get("key"),
                   cached=d.get("cached", False))


@dataclass
class AnswerMatrix:
    """読み手 1 体の回答行列（L2 の OUT）。"""
    reader: str
    calibration: bool
    prompt: PromptVersion | None
    samples: int
    answers: list[Answer]

    def index(self) -> dict[tuple[str, str, int], Answer]:
        return {(a.axis_id, a.side, a.sample): a for a in self.answers}

    @classmethod
    def from_dict(cls, d: dict) -> AnswerMatrix:
        return cls(reader=d["reader"], calibration=d["calibration"],
                   prompt=PromptVersion.from_dict(d["prompt"]) if d.get("prompt") else None,
                   samples=d["samples"], answers=[Answer.from_dict(a) for a in d["answers"]])


# ---------------------------------------------------------------- 出力

@dataclass
class Counts:
    """軸ベースの数え（上流 §7.1）。n = s + r + u1 + u2 + u3 + u4。"""
    s: int = 0
    r: int = 0
    u1: int = 0   # 沈黙（両側とも証拠なし・無効なし）
    u2: int = 0   # 無効（両側とも証拠なし・無効を含む）
    u3: int = 0   # 標本同数
    u4: int = 0   # 矛盾（両側に証拠）

    @property
    def n(self) -> int:
        return self.s + self.r + self.u1 + self.u2 + self.u3 + self.u4


@dataclass
class AxisReading:
    """軸ごとの向き（上流 §7.5）。v_support / v_refute は標本ごとの答えの符号（STATES/DENIES/SILENT/INVALID）。"""
    axis_id: str
    d: int
    zero_kind: str | None
    agreement: float
    d_samples: list[int]
    v_support: list[str]
    v_refute: list[str]
    contradiction: float
    invalid: int
    silent: int


@dataclass
class Diagnostics:
    """確からしさの材料（上流 §7.5）。1 つの数に畳まない（J6′）。率の定義は空撃ちと同一（実装判断 I4）。
    有効な軸が 0 本のときは率を None にする（測って 0 だったのと区別する。受入 M5）。"""
    valid_rate: float | None
    valid_rate_by_side: dict[str, float | None]
    silent_rate: float | None
    invalid_rate: float | None
    agreement_mean: float | None
    contradiction_rate: float | None
    retries: int
    p_by_sample: list[float | None]


@dataclass
class Value:
    """型の中の値（上流 §7.6）。真偽確率なら p だけ、順序尺度なら level（1..K）と label も。"""
    p: float
    level: int | None = None
    label: str | None = None


@dataclass
class Reading:
    """読み手 1 体の判定（上流 §4.2 読み手ごと）。"""
    reader: str
    calibration: bool
    counts: Counts
    p: float | None
    w: float | None
    label: Label
    value: Value | None
    diagnostics: Diagnostics
    axes: list[AxisReading]
    note: str | None = None   # "no_active_axes" / "all_axes_flagged"（札の原因が本文でなく問いの集合のとき）


@dataclass
class ReaderSummary:
    """読み手をまたぐ要約（上流 §4.2）。偽読み手と値なしの読み手は除く。"""
    delta: float | None
    levels_agree: bool | None
    readers_split: bool | None
    representative: Value | None
    readers: list[str]
    note: str | None = None


@dataclass
class RoleCost:
    live: int = 0      # 実際に LLM を呼んだ回数
    cached: int = 0    # キャッシュ命中（同時要求の合流を含む）
    missed: int = 0    # cache-only で外れた回数（LLM は呼んでいない）


@dataclass
class Cost:
    """費用（上流 §4.2・Q8）。偽読み手の呼び出しは calibration_calls に別枠（LLM ではない）。"""
    plan: RoleCost = field(default_factory=RoleCost)
    crosscheck: RoleCost = field(default_factory=RoleCost)
    answer: RoleCost = field(default_factory=RoleCost)
    calibration_calls: int = 0

    @property
    def live(self) -> int:
        return self.plan.live + self.crosscheck.live + self.answer.live

    @property
    def cached(self) -> int:
        return self.plan.cached + self.crosscheck.cached + self.answer.cached

    @property
    def missed(self) -> int:
        return self.plan.missed + self.crosscheck.missed + self.answer.missed


@dataclass
class Judgment:
    """判定結果（上流 §4.2）と、その記録（上流 §8）。"""
    proposition: str
    output: OutputType
    units: list[Unit]
    segmentation: str
    question_set: QuestionSet
    matrices: list[AnswerMatrix]
    readings: dict[str, Reading]
    summary: ReaderSummary
    cost: Cost
    versions: dict[str, str]
    budget: Budget
    thresholds: Thresholds
    at: str

    def to_record(self) -> dict:
        """追記のみの記録 1 行（実装設計 §6。schema_version 1）。"""
        return {
            "schema_version": 1,
            "at": self.at,
            "input": {"proposition": self.proposition, "output": output_to_dict(self.output),
                      "budget": self.budget.to_dict(), "thresholds": self.thresholds.to_dict(),
                      "segmentation": self.segmentation, "units": [asdict(u) for u in self.units]},
            "question_set": self.question_set.to_dict(),
            "matrices": to_jsonable(self.matrices),
            "readings": to_jsonable(self.readings),
            "summary": to_jsonable(self.summary),
            "cost": {**to_jsonable(self.cost), "live": self.cost.live, "cached": self.cost.cached,
                     "missed": self.cost.missed},
            "versions": dict(self.versions),
        }
