# Structural Distillation 実装設計

- 版: v1（2026-09-22）。差分は末尾の変更履歴と、合意ドキュメント `.pair-agent/agreements/implementation-design.md`
- 上流: [`UPSTREAM_DESIGN.md`](UPSTREAM_DESIGN.md) v3.4。**ドライバ・契約・規則はそちらが正。** この文書は「どう組むか」だけを書く
- ⚠ **この文書は仮説である。** 実装と実測で崩れたらこの文書を直す。上流設計と食い違ったら上流設計を優先し、食い違いを変更履歴に書く
- ⚠ コードはまだ無い。v1 の範囲は上流設計の L0〜L4 と合成（`judge()`）。蒸留（L5）は後段

## 0. 何を根拠に書いているか

| 根拠 | 何を決めるか |
|---|---|
| 上流設計 v3.4（§3 ドライバ・§4 契約・§5 層・§6 ハーネス・§7 集約・§8 記録・§9 品質シナリオ） | 各層の IN → OUT、規則、失敗の扱い、検査すべきこと |
| 空撃ちの道具 `.pair-agent/probes/upstream-probe-1/{probe,probe3,probe4_run,probe4_crosscheck}.py` | **動いた実物。ライブラリはこれの移植（port）**。約 8,000 回の LLM 呼び出しで検めた挙動（指示の文面・キャッシュの鍵・集約の式）を忠実に移す。変えるなら変更履歴に理由を書き、再測定の対象にする |
| 師匠の規則 | Python／他のプロジェクトを参照しない／依存は一方向・利用側を import しない／テストは単体で走る／値はすべて仮説 |
| ペア固有の confirmed process Skill | **1 層 ＝ 1 ファイル**（層番号がファイル名）／**内側から一層ずつ完成**／**各層に触れる最小 CLI**／指示は外部データ・規則はコード／用途で変わる値だけ外へ出す／**コアの依存は最小・道具は自由**／生応答キャッシュ → 再集計／LLM 呼び出しは非同期／TDD／venv |

どれにも辿れない項目は「⚠ I-番号」（実装判断。§11 に一覧。事後確認対象）。

## 1. 実装が満たすこと（ドライバの写像）

上流設計の品質特性を、実装上の要求と検査に写す。

| # | 品質特性（上流 §3.1） | 実装上の要求 | どこで | どう検査するか |
|---|---|---|---|---|
| Q1 | 読み手非依存 | 読み手は差し替え可能な **口（Protocol）**。読み手ごとに独立に集約し、読み手間の差を一級で返す | `l0.Reader`・`l3.summarize_readers` | 偽読み手 2 体で Δ が出る（`test_judge`） |
| Q2 | 型の閉包 | 値は L4 が型から**構成的に**作る。L0 は三段構えで形を守り、失敗は型付き | `l0.structured`・`l4.to_value` | S2: 壊れた応答を多数流して型外が 0 件（`test_l0`・`test_l4`） |
| Q3 | 偏りの打ち消し・検出 | 矛盾 u₄ を数え κ で計器不良 | `l3` | S11: 全問 Yes の偽読み手 → 計器不良（`test_judge`） |
| Q4 | 幅と確からしさ | w = 2·min(s,r)/(s+r)。診断値は数えた量をそのまま並べる | `l3` | S12: 軸を 2 倍に複製しても w 不変（`test_l3`） |
| Q5 | 検証可能性 | 記録に単位列・軸・回答・根拠 id・読み手名・指示の版を残す | `judge`（記録） | 記録から `replay()` で同じ判定が出る（`test_judge`） |
| Q6 | 汎用性 | コアは標準ライブラリだけで、利用側も他のプロジェクトも import しない。ドメイン語は入力（本文・命題・型）と指示の集合にしか無い | パッケージ全体 | コアの import を AST で走査し標準ライブラリ以外が 0（`test_judge`） |
| Q7 | 再計算可能 | 生応答は L0 のキャッシュに、判定は記録に残る。集約は純関数 | `l0.CachedPort`・`judge.replay` | S0a: 測定 2 周目の記録（`out4/*/results.json`）を `l3` で引き直して全一致（`test_l3`） |
| Q8 | 費用の可視性 | 役割（生成／交差検証／回答）別に 実呼び出し／キャッシュ命中 を数える | `judge`（`Cost`） | S8: 軸 6・読み手 2・標本 1 で呼び出し数が式どおり（`test_judge`） |
| Q9 | 本文追従 | 本文は引数だけ。アダプタは URL 取得も検索もしない | `l0`（設計上の制約） | コードレビュー（機械検査なし） |

## 2. 配置（パッケージ構成）

```
structural_distillation/          # コア。標準ライブラリだけ（⚠ I1）
  __init__.py                     # 公開 API の再輸出・__version__
  types.py                        # 契約: データ型・列挙・例外・既定値。層ではない（層の間の共有語彙）
  prompts.py                      # 指示の集合（PromptSet）の読み込みと描画。層ではない（facility）
  l0.py                           # 読み手ポート: Reader / structured()（三段構え）/ validate() / CachedPort / OllamaReader / FakeReader
  units.py                        # 単位化（決定論・規則は版付き）。上流設計で番号の無い層
  l1.py                           # 問い生成: plan()・ハーネス検査（H1〜H11）・向きの交差検証（H12）
  l2.py                           # 回答: answer_all()・根拠 id の正規化と照合
  l3.py                           # 集約: direction() / aggregate() / label() / summarize_readers()。純関数
  l4.py                           # 型付け: to_value()。純関数
  judge.py                        # 合成: judge() / judge_sync() / replay() / 記録の書き出し
  prompts/ja/                     # 指示（外部データ・版付き）: plan.md / answer.md / orient.md / exclusive.md / set.json
tools/                            # 触れる CLI と道具。パッケージには入れない。依存は自由（v1 は標準ライブラリだけ）
  l0_chat.py l1_chat.py l2_chat.py l3_chat.py l4_chat.py   # 各層だけを叩く最小 CLI
  judge_cli.py                    # 本文ファイル・命題・型を渡して 1 判定
  conformance_out4.py             # 適合検査: 測定 2 周目のキャッシュだけで judge() を回し、記録と一致するか
tests/                            # 層ごとに 1 ファイル ＋ 実接続 1 ファイル
  conftest.py test_l0.py test_l0_live.py test_units.py test_l1.py test_l2.py test_l3.py test_l4.py test_judge.py
pyproject.toml                    # 実行時依存なし。開発依存は pytest だけ
```

依存の向き（矢印の先しか import しない）:

    judge → l1, l2, l3, l4, units, l0, prompts, types
    l1, l2 → l0, units, prompts, types
    l3, l4 → types
    l0 → types
    prompts, units → types
    types → （標準ライブラリだけ）

- `l3`・`l4` は LLM にも I/O にも触れない純関数の層。ここに判断の式が全部ある
- `l0` だけが外の世界（HTTP・ファイル）に触れる。ツールモジュールの 3 層（純粋整形 ／ I/O ／ 公開）に従い、
  整形（指示へのスキーマ明記・フェンス剥がし・検証・鍵）は純関数、HTTP は `OllamaReader` の中だけ
- ファイル名は上流設計の層番号。単位化は上流で番号が無いので役割名（`units.py`）（⚠ I8）

## 3. 契約（`types.py`）

すべて `dataclass`（`frozen` は値オブジェクトのみ）。JSON に往復できる（`to_dict` / `from_dict`）。列挙は `str` を継承した `Enum`。

### 3.1 入力

| 型 | 項目 | 上流 |
|---|---|---|
| `Budget` | `axes: int = 6`（生成器に頼む軸数）・`axes_min: int | None`・`axes_max: int | None`（None なら `axes` と同じ ＝ ちょうど。⚠ I7）・`samples: int = 1`・`plan_retries: int = 2`（初回 ＋ 再試行 2 ＝ 3 回。空撃ちと同じ）・`workers: int = 1`（同時に飛ばす呼び出し数）・`crosscheck: bool = True`（H12 既定 ON）・`max_chars: int = 12000`（F1 の門。⚠ I16 仮置き） | §4.1 予算 |
| `Thresholds` | `iota = 0.3`・`kappa = 0.667`・`rho = 0.5`・`omega = 0.5`・`delta = 0.2`。docstring に出所（κ のみ昇格、他は仮置き。合意 K13） | §7.4 |
| `OutputType` | `Probability()` ／ `Ordinal(k: int, labels: tuple[str, ...] | None = None, bounds: tuple[float, ...] | None = None)`。`bounds` は昇順の k−1 個の切れ目（省略時は K 等分。⚠ I9） | §4.1 型・§7.6 |
| `PromptSet` | §5。`"ja"` の名前か、ディレクトリから読んだ実体 | §6 J9 |

### 3.2 中間の産物

| 型 | 項目 |
|---|---|
| `Unit` | `id: str`（`s1`…）・`text: str` |
| `Verdict` | `STATES`（述べている）／ `DENIES`（否定している）／ `SILENT`（触れていない）。**コードは英語の列挙、LLM に見せる語は指示の集合が持つ**（⚠ I5） |
| `Axis` | `id`（`a01`…）・`name`（人が読む軸名。測定に使わない）・`claim_support`・`claim_refute`・`origin: "initial" | "retry"`・`kind: "substantive"`（⚠ I12: 上流 §13 の検出項目・同義対を将来 `"detection"` / `"synonym"` として同じ記録に載せるための欄。v1 は `"substantive"` だけ） |
| `Attempt` | 生成の 1 試行: `try`・`violations: list[str]`（違反した規則 H の名前つき）・`n_axes`・`error` |
| `CrossCheck` | H12 の記録: `verifiers`・`votes`（軸 × 側 × 検証役 × 並び 2 版）・`flagged: list[str]`・`weak: list[str]`（「どちらとも言えない」が出た軸）・`nonexclusive: list[str]`（両立しうると言われた対。診断のみ） |
| `QuestionSet` | `axes: list[Axis]`・`active_ids: list[str]`（交差検証で外した後）・`planner: str`・`prompt: PromptVersion`・`budget: Budget`・`attempts: list[Attempt]`・`crosscheck: CrossCheck | None` |
| `Answer` | `axis_id`・`side: "support" | "refute"`・`sample: int`・`verdict: Verdict | None`・`evidence_raw: list[str]`（LLM が返したまま）・`evidence: list[str]`（正規化後）・`valid: bool`・`error: str | None`・`cached: bool` |
| `AnswerMatrix` | `reader: str`・`calibration: bool`（偽読み手か）・`prompt: PromptVersion`・`samples: int`・`answers: list[Answer]` |

### 3.3 出力

| 型 | 項目 | 上流 |
|---|---|---|
| `Counts` | `s`・`r`・`u1`（沈黙）・`u2`（無効）・`u3`（標本同数）・`u4`（矛盾）・`n` | §7.1 |
| `AxisReading` | `axis_id`・`d: -1 | 0 | +1`・`zero_kind: "silent" | "invalid" | "tie" | "contradiction" | None`・`agreement: float`（標本の一致率）・`d_samples`・`v_support`・`v_refute`（標本ごとの答え）・`contradiction: float`（両側に証拠が出た標本の割合） | §7.5 軸ごとの向き |
| `Diagnostics` | `valid_rate`・`valid_rate_by_side: {support, refute}`・`silent_rate`・`invalid_rate`・`agreement_mean`・`contradiction_rate`・`retries`（生成の再試行回数）・`p_by_sample: list[float | None]` | §7.5 |
| `Label` | `LEAN_SUPPORT`／`LEAN_REFUTE`／`SPLIT`／`NO_EVIDENCE`／`INSTRUMENT_FAULT` | §7.4 の 5 値 |
| `Reading` | 読み手 1 体の判定: `reader`・`calibration`・`counts`・`p`・`w`・`label`・`value: Value | None`・`diagnostics`・`axes: list[AxisReading]` | §4.2 読み手ごと |
| `Value` | `Probability` なら `p`、`Ordinal` なら `level: int`（1..K）と `label: str | None` | §7.6 |
| `ReaderSummary` | `delta: float | None`（実読み手の p の最大 − 最小）・`levels_agree: bool | None`・`readers_split: bool | None`（Δ > δ）・`representative: Value | None`（⚠ I10: 実読み手の p の平均。U3 仮置き）・`note: str | None`（`"single reader"` など） | §4.2 要約 |
| `Cost` | 役割別 `{plan, crosscheck, answer}` × `{live, cached}` と合計 | §4.2 費用・Q8 |
| `Judgment` | `proposition`・`output`・`units`・`segmentation: str`・`question_set`・`matrices: list[AnswerMatrix]`・`readings: dict[str, Reading]`・`summary: ReaderSummary`・`cost`・`versions: {library, prompts, units_rule, aggregation}`・`to_record() -> dict` | §4.2・§8 |

### 3.4 例外

| 例外 | いつ | 上流 |
|---|---|---|
| `InputError` | 本文が空・命題が空・K < 2・`axes_min > axes_max`・本文が `max_chars` を超える・読み手が 0 | F1・F2 |
| `PlanningFailed(attempts)` | 生成が `plan_retries` まで規則を満たせない。`attempts` に違反した規則が残る | F3 |

LLM の失敗（HTTP エラー・形式崩れ）は**例外にしない**。L0 が `ok=False` で返し、L1 は試行として数え、L2 は無効として数える（F6）。

## 4. 各層（IN → OUT と内部規則）

### 4.0 L0 読み手ポート（`l0.py`）

**IN** (指示, 回答スキーマ, 読み手) → **OUT** スキーマ準拠の JSON オブジェクト **または** 型付きの失敗。

```python
class Reader(Protocol):
    name: str                 # 記録と鍵に使う。モデル名（"gemma4:31b-cloud"）や "fake:all_yes"
    calibration: bool         # 偽読み手なら True（Δ と代表値から除く）
    async def complete(self, messages: list[dict], schema: dict, *, sample: int) -> RawReply
        # RawReply(ok, content, error, meta)。例外を投げない
```

| 部品 | 責務 | 性質 |
|---|---|---|
| `with_schema(messages, schema, notice) -> messages` | 最後の user メッセージ末尾に `notice`（「次の JSON Schema に厳密に一致する JSON だけを出力…」。文言は指示の集合が持つ）＋ スキーマ JSON を足す | 純関数 |
| `strip_fence(text) -> text` | 全文が単一のコードフェンスならフェンスだけ剥がす。部分抽出（修復）はしない | 純関数 |
| `validate(obj, schema) -> list[str]` | JSON Schema の**部分集合**を検査: `type`（object / array / string / integer / number / boolean）・`properties`・`required`・`enum`・`items`・`additionalProperties: false`。それ以外のキーワードは使わない（⚠ I14。使えるスキーマの範囲をここで固定する） | 純関数 |
| `cache_key(model, messages, schema, sample, version) -> str` | `sha1(json.dumps({"m","msgs","schema","sample","v"}, sort_keys, ensure_ascii=False))`。**空撃ちと同一**（⚠ I3: 測定 2 周目のキャッシュがそのまま使える） | 純関数 |
| `structured(reader, messages, schema, *, version, sample, notice) -> Structured` | **三段構え**: ① `schema` を読み手に渡す（ネイティブ指定。信用しない）② `with_schema` で指示にも明記 ③ 受信で `strip_fence → json.loads → validate`。失敗なら `version + ":retry"` で**再送 1 回** → それでも駄目なら `ok=False`。`Structured(ok, obj, content, error, attempts, cached)` | I/O を呼ぶが自分は判断しない |
| `CachedPort(reader, path, *, cache_only=False)` | `Reader` を包む `Reader`。鍵で引き、無ければ `reader.complete` → 追記のみ JSONL に `{"key","model","sample","version","payload"}`（**行の形も空撃ちと同一**）。同じ鍵の同時要求は 1 回にまとめる（`asyncio` の Future を共有）。`cache_only` で外れたら `ok=False, error="cache-only miss"`。`calls_live` / `calls_cached` を数える | I/O（ファイル） |
| `OllamaReader(model, *, host="http://localhost:11434", num_ctx=8192, think=False, timeout=600, retries=3)` | `/api/chat` に `format=schema`・`options.num_ctx`・`think`。`think` を拒む（400 に "think"）モデルには `think` 無しで再送。429 / 5xx は 5·(k+1) 秒待って最大 3 回。`urllib` を `asyncio.to_thread` で呼ぶ（⚠ I1: 依存を足さない） | I/O（HTTP） |
| `FakeReader(kind, *, seed=0)` | `kind ∈ {all_yes, all_no, all_silent, random}`。回答スキーマ（`verdict` を持つ）にだけ答える: `{"verdict": <kind の語>, "evidence": ["s1"]}`（`s1` は単位列の先頭 id なので常に実在。`SILENT` は `[]`）。`random` は seed 付きで 3 値を等確率。他のスキーマ（生成・向き）には `ok=False`。`calibration=True` | 決定論 |

失敗は `ok=False` で返す。**投げるのはプログラムの誤り（引数の型違いなど）だけ。**
黙って縮退する箇所（フェンスを剥がした・再送した・cache-only で外れた・think を外した）は `logging`（`structural_distillation.l0`）に残す。

### 4.U 単位化（`units.py`）

**IN** 本文 → **OUT** 単位列 `list[Unit]`。決定論。

| 規則名 | 割り方 | 版 |
|---|---|---|
| `ja-sentence`（既定。⚠ I8） | `。！？` の直後で割る。前後の空白を落とし、空を捨てる。id は `s1`… | `v1`（空撃ちと同一） |
| `paragraph` | 空行で割る | `v1` |
| `line` | 改行で割る | `v1` |

`render(units) -> str` は `[s1] 本文…` を改行で並べる（指示に埋める形。空撃ちと同一）。
規則名と版は `Judgment.versions.units_rule` に残る。

### 4.1 L1 問い生成（`l1.py`）

**IN** (生成器, 単位列, 命題, 予算, 指示の集合, 検証役) → **OUT** `QuestionSet` **または** `PlanningFailed`。

生成は LLM、規則の検査はコード（上流 §6）。スキーマは**コードが持つ**（契約）:

```json
{"type":"object","properties":{"axes":{"type":"array","items":{"type":"object",
 "properties":{"axis":{"type":"string"},"claim_support":{"type":"string"},"claim_refute":{"type":"string"}},
 "required":["axis","claim_support","claim_refute"]}}},"required":["axes"]}
```

試行: `sample = 0 .. plan_retries` で `structured(planner, plan_prompt)` → `check_harness` → 違反が無ければ採用。全試行が違反なら `PlanningFailed(attempts)`。
⚠ 空撃ちは上限到達時に「違反付きの最後の軸」で続行した（観察のため）。ライブラリは上流 F3 のとおり**拒否**する。

`check_harness(axes, proposition, budget) -> list[str]`（違反の一覧。空なら合格。空撃ち `probe3.check_plan` と同じ規則）:

| 規則 | 検査 | 違反の表記 |
|---|---|---|
| H6 予算 | `axes_min ≤ len(axes) ≤ axes_max` | `H6 軸数` |
| H1 両側を持つ | `claim_support`・`claim_refute` が空でなく、正規化して同一でない | `H1 空の記述` / `H1 両側が同一` |
| H4 非重複 | 正規化した記述（両側とも）が他の記述と同一でない | `H4 非重複` |
| H3 非自明 | 記述と命題の文字 2-gram Jaccard ≥ 0.6 なら言い換え | `H3 非自明` |
| H11 平叙文 | 記述の末尾が `か` / `？` / `?` でない | `H11 疑問文` |
| H7 形式 | スキーマ照合は L0 が済ませている（違反は `L0 失敗` として試行に残る） | `H7 形式` |

正規化は空撃ちと同じ（`[\s、。「」・,.?？!！]` を除く）。H2（観察可能）は検査しない（上流 §10）。

id は採用した試行の順に `a01`…。`origin` は初回採用なら `"initial"`、再試行で採用なら `"retry"`。

**交差検証（H12）**: `budget.crosscheck` が真で検証役が 2 体以上のときだけ回す（1 体以下なら回さず `crosscheck=None`、`logging` に「未検証」）。
空撃ち `probe4_crosscheck.py` v2 の規則をそのまま:

- 軸 × 側 × 検証役 × 並び 2 版（選択肢の並びを反転した版は `sample=1`）で「この記述が本文に述べられていたら命題は支持されるか」を聞く（本文は見せない）
- 検証役ごとに版の多数決を票にし、どちらかの側で票が期待と反対なら「反対」。**反対の検証役が過半数**なら `flagged`
- 「どちらとも言えない」が出た軸は `weak`（外さない。診断）
- 対の排他性（「両方が同時に述べられうるか」）を検証役ごとに聞き、過半数が「両立しうる」なら `nonexclusive`（外さない。診断）
- `active_ids = 全 id − flagged`。全部外れたら空のまま返す（各読み手は F4 で値なしになる。`logging` に残す）

### 4.2 L2 回答（`l2.py`）

**IN** (読み手, 単位列, `QuestionSet`, 予算, 指示の集合) → **OUT** `AnswerMatrix`。

- 仕事の単位は **(有効な軸, 側, 標本)** で 1 呼び出し（上流 J13。⚠ I6: 交差検証で外した軸には答えさせない。1 記述 1 呼び出しなので、外してから答えても、答えてから外しても同じ回答行列になる）
- `budget.workers` の `asyncio.Semaphore` で同時数を絞り、`gather` で回す
- 回答スキーマはコードが持つ: `{"verdict": enum[指示の集合の 3 語], "evidence": [string]}`
- `structured(reader, answer_prompt(units_text, claim))` → `verdict` の語を `Verdict` に引く → `evidence_raw` を正規化（`[\[\]\s「」]` を除き、空を捨てる。上流 J16）→ `STATES` / `DENIES` は根拠が 1 つ以上あり**すべて単位列に実在**すれば `valid`、それ以外は `valid=False`（F6）。`SILENT` は根拠を要求しない
- L0 が `ok=False`（再送後）なら `verdict=None, valid=False, error=…`

### 4.3 L3 集約（`l3.py`）

**IN** `AnswerMatrix`（＋ `QuestionSet`・`Thresholds`）→ **OUT** `Reading`（値なし。値は L4）。**純関数。** 上流 §5.3・§7 の式をそのまま。

標本 1 つの向き（`side_evidence` → `direction_sample`）:

    支持側に証拠 ＝ v_support == STATES または v_refute == DENIES
    反証側に証拠 ＝ v_refute == STATES または v_support == DENIES
    どちらかの回答が無効          → 0（invalid）      ⚠ 空撃ちは無効を SILENT と同様に「証拠なし」で 0 にしていた。分類は増やすが d は変わらない
    両方                          → 0（contradiction）
    支持側だけ / 反証側だけ       → +1 / −1
    どちらも無い                  → 0（silent）

軸の向き `d` は標本の多数決（`+1 / −1 / 0` の票。最多が一意ならそれ、同数なら 0 で `zero_kind="tie"`）。`d=0` が多数決で決まったときの `zero_kind` は 0 票の中で最多の種類。`agreement` は最多票の割合。

数える（読み手 1 体・軸 n）: `s`・`r`・`u1..u4`・`n`。`p = s/(s+r)`、`w = 2·min(s,r)/(s+r)`（`s+r = 0` なら両方 `None`）。

⚠ **率の定義は空撃ちと同一にする**（⚠ I4）。閾値 κ 0.667 はこの定義で置かれた:

| 率 | 定義（空撃ち `probe3.aggregate` と同一） | 上流 §7.4 の表記 |
|---|---|---|
| `invalid_rate` | 無効な回答 ÷ 全回答（2 × 標本 × 軸） | u₂ / n（軸ベース） |
| `contradiction_rate` | 軸ごとの「両側に証拠が出た標本の割合」の平均 | u₄ / n |
| `silent_rate` | 軸ごとの「両側とも SILENT の標本の割合」の平均 | u₁ / n |
| `valid_rate` | (s + r) ÷ n | 同じ |

標本 1 のとき `contradiction_rate`・`silent_rate` は上流の表記と一致する。`invalid_rate` は回答ベースなので軸ベースの u₂/n と一致しない。
上流の表記に合わせ直すなら ι の値も引き直す（K13）。v1 は空撃ちの定義を採り、`Counts` の `u2` は軸ベースで別に返す。

札（`label`。順序 ι → κ → ρ → ω。空撃ち `probe.label` と同一）:

    invalid_rate ≥ ι           → INSTRUMENT_FAULT
    contradiction_rate ≥ κ     → INSTRUMENT_FAULT
    n == 0 or (s+r)/n < ρ      → NO_EVIDENCE
    w ≤ ω and p > 0.5          → LEAN_SUPPORT
    w ≤ ω and p < 0.5          → LEAN_REFUTE
    それ以外                    → SPLIT

`summarize_readers(readings, thresholds) -> ReaderSummary`: 実読み手（`calibration=False`）で値を持つものの p から Δ（最大 − 最小）、段の一致、`readers_split = Δ > δ`、代表値 ＝ p の平均（⚠ I10）。実読み手が 1 体なら Δ は `None`、`note="single reader"`（F5。0 で埋めない）。

### 4.4 L4 型付け（`l4.py`）

**IN** (`p`, `label`, `OutputType`) → **OUT** `Value | None`。純関数。

- `label ∈ {NO_EVIDENCE, INSTRUMENT_FAULT}` または `p is None` → `None`
- `Probability` → `Value(p=p)`
- `Ordinal(k, labels, bounds)`: `bounds` 省略時は K 等分 `level = min(k−1, floor(p·k)) + 1`（空撃ち `level5` と同一）。`bounds` があれば `level = 1 + (p 以下の切れ目の数)`（`p = 1.0` は最上段）。`labels` があれば `Value.label = labels[level−1]`
- 値は型から構成するので型の外に出る経路が無い。`Ordinal` の `k < 2`、`bounds` の長さ違い・非昇順は `InputError`（`judge` の入口で検査）

### 4.5 合成（`judge.py`）

```python
async def judge(text: str, proposition: str, output: OutputType, *,
                readers: Sequence[Reader], planner: Reader | None = None,
                verifiers: Sequence[Reader] | None = None,
                budget: Budget = Budget(), thresholds: Thresholds = Thresholds(),
                prompts: str | PromptSet = "ja", segmentation: str = "ja-sentence",
                record_path: str | os.PathLike | None = None) -> Judgment

def judge_sync(...同じ...) -> Judgment          # asyncio.run で包む。既に走っているループの中では使えない
def replay(record: dict, *, thresholds=None, output=None) -> Judgment   # 記録から LLM を呼ばずに引き直す
```

変換の並び（依存関係だけで決まる）:

1. 入口の検査（F1・F2・型の整合）→ `InputError`
2. `units.segment`
3. `l1.plan`。生成器の既定は実読み手の先頭。検証役の既定は実読み手（偽読み手を除く）。2 体未満なら交差検証なし
4. 読み手ごとに `l2.answer_all` → `l3.aggregate` → `l4.to_value`。⚠ I11: **読み手は 1 体ずつ順に**、1 体の中の記述は `workers` 並列（ローカルの読み手を 2 体同時に走らせると GPU を取り合う）
5. `l3.summarize_readers`
6. `Cost` を L0 のカウンタから役割別に集計（`plan` / `crosscheck` / `answer`）
7. `record_path` があれば `Judgment.to_record()` を JSONL に追記（§6）

## 5. 指示の集合（`prompts.py`・`prompts/<name>/`）

指示は**用途（言語・文体）で変わる値**なので外に出す。規則（スキーマ・検査・集約）はコードに残す（上流 J9・Skill「外に出すのは用途で変動する値だけ」）。

```
prompts/ja/
  set.json      # {"name":"ja","verdicts":{"STATES":"述べている","DENIES":"否定している","SILENT":"触れていない"},
                #  "orientations":{"SUPPORT":"支持","REFUTE":"反証","NEITHER":"どちらとも言えない"},
                #  "exclusivity":{"COMPATIBLE":"両立しうる","EXCLUSIVE":"両立しない"},
                #  "schema_notice":"## 出力形式\n次の JSON Schema に厳密に一致する JSON オブジェクトだけを出力する。…",
                #  "digests":{"plan.md":"<sha1>", ...}}
  plan.md       # version: p3  ／ ## system ／ ## user（$proposition $units_text $n_axes）
  answer.md     # version: p2  ／ ## system ／ ## user（$units_text $claim $v_states $v_denies $v_silent）
  orient.md     # version: xc2 ／ ## system ／ ## user（$proposition $claim $options）
  exclusive.md  # version: xc2 ／ ## system ／ ## user（$claim_a $claim_b $compatible $exclusive）
```

- テンプレートは `string.Template`（`$name`）。`{}` を含む JSON 例を書いても壊れない
- **3 値の語は `set.json` が唯一の持ち主。** テンプレートには `$v_states` 等で埋め、回答スキーマの `enum` も同じ値から作る（同じ線引きを 2 箇所に持たない）
- `PromptVersion = {set: "ja", plan: "p3", answer: "p2", orient: "xc2", exclusive: "xc2", digest: <全ファイルの sha1>}`。
  **鍵に入るのは版の文字列**（空撃ちと同一。`p2` の再送は `p2:retry`）。`digest` は記録にだけ残す
- 読み込み時に `set.json` の `digests` と実ファイルの sha1 を突き合わせ、違えば `logging` に警告「指示が版を上げずに変更されている」（⚠ I15。版を上げ忘れた編集がキャッシュに黙って混ざるのを防ぐ。エラーにはしない）
- **v1 の `ja` は空撃ちの指示（p3 生成・p2 回答・xc2 交差検証）を逐語で移す。** これで測定 2 周目のキャッシュが鍵ごと再利用でき、適合検査（§9）が LLM を呼ばずに成立する
- 利用側は `PromptSet.load(path)` で自分の集合を渡せる（言語を変える・文体を変える）。変えたら別の計器なので閾値は引き直しの対象

## 6. 記録とキャッシュ

2 つは別の物:

| | キャッシュ（L0） | 記録（judge） |
|---|---|---|
| 何 | 生応答。鍵 → payload | 1 判定の全体（入力・軸・回答行列・集約・費用・版） |
| 目的 | 再開・再集計・費用ゼロの比較実験 | 監査（Q5）・再計算（Q7）・蒸留の素材（§8） |
| 形 | JSONL 追記のみ。`{"key","model","sample","version","payload":{"ok","content","error","eval_count","total_duration"}}` | JSONL 追記のみ。1 行 = `Judgment.to_record()` |
| 誰が読むか | `CachedPort` | `replay()`・蒸留（後段）・人 |

記録の形（`schema_version: 1`）:

```
{"schema_version":1, "at":<ISO8601>,
 "input":{"proposition","output","budget","thresholds","segmentation","units":[{"id","text"}]},
 "question_set":{...QuestionSet...},
 "matrices":[{"reader","calibration","prompt","samples","answers":[{...Answer...}]}],
 "readings":{"<reader>":{...Reading...}},
 "summary":{...ReaderSummary...},
 "cost":{...}, "versions":{"library","prompts","units_rule","aggregation"}}
```

- 本文は**単位列として丸ごと**残す（ハッシュだけでは再計算も第三者検証もできない。上流 §8）
- `replay(record, thresholds=…, output=…)` は `matrices` から `l3`・`l4` を引き直す。規則の版（`aggregation`）が変わっていれば記録の値と違ってよく、それが再計算の目的
- 測定の道具（`out4/*/results.json`）の形は**移さない**。あれは空撃ちの記録。適合検査（§9）が両者を突き合わせる

## 7. 実行モデル

- **非同期が本体**（`async def judge`。Skill「LLM 呼び出しは非同期」）。同期の入口は `judge_sync`。ストリーミングは使わない（構造化出力を丸ごと検証するので流す意味が無い。⚠ I2）
- 並列は `budget.workers` の `Semaphore` 1 つ。既定 1（ローカルの読み手に安全側）。クラウドの読み手は 4〜8 で回した実績
- 読み手は順に、記述は並列（⚠ I11）。交差検証の質問も同じ `Semaphore`
- 時間制限は読み手のアダプタが持つ（`OllamaReader.timeout`）。`judge` 自体は持たない（`asyncio` のキャンセルで止める）
- スレッド: `OllamaReader` が `to_thread` で `urllib` を呼ぶ。`CachedPort` の書き込みは `asyncio.Lock`（同一プロセス内）。**別プロセスからの同一キャッシュへの同時追記は守らない**（空撃ちと同じ。1 行 1 `write` の追記なので壊れにくいが保証はしない）
- ログ: `logging.getLogger("structural_distillation.<層>")`。既定でハンドラは付けない（ライブラリの作法）

## 8. 失敗の型（上流 §4.4 の写像）

| # | 失敗 | 実装 | どこで |
|---|---|---|---|
| F1 | 本文が文脈に入らない | `len(text) > budget.max_chars` → `InputError`。LLM を 1 回も呼ばない | `judge` 入口 |
| F2 | 本文・命題が空、K < 2、`axes_min > axes_max`、読み手 0、`bounds` 不正 | `InputError` | `judge` 入口 |
| F3 | 生成が規則を満たせない | `PlanningFailed(attempts)`。試行ごとの違反規則を持つ | `l1.plan` |
| F4 | 有効な軸が 0 | `p=w=None`、札は ρ で `NO_EVIDENCE`（無効率・矛盾率が閾値を超えていれば `INSTRUMENT_FAULT`）、値なし | `l3`・`l4` |
| F5 | 実読み手が 1 体 | `delta=None`、`note="single reader"` | `l3.summarize_readers` |
| F6 | 読み手ポートが再送後もスキーマに合わない | その回答は `valid=False`。判定は続行。`invalid_rate ≥ ι` で `INSTRUMENT_FAULT` | `l0`・`l2`・`l3` |
| F7 | 複合文の命題 | 検出しない | — |
| — | 交差検証の検証役が 2 体未満 | 交差検証なし（`crosscheck=None`）。`logging` | `l1` |
| — | 生成器が偽読み手 | `InputError`（偽読み手は答えるだけ） | `judge` 入口 |

## 9. テスト（層ごと・純関数を CI に、実接続は別）

| ファイル | 何を固定するか | LLM |
|---|---|---|
| `test_l0.py` | `strip_fence`・`validate`（部分集合の各キーワード）・`cache_key` が空撃ちの鍵と一致（out4 のキャッシュ行 1 件を固定データに）・`structured` の三段構え（散文・フェンス付き・欠損・型外・再送 → 型外が値に化けない: **S2**）・`CachedPort` の命中／追記／cache-only／同時要求の合流・`FakeReader` 4 種 | 台本の読み手（`ScriptedReader`: 呼び出し順に決めた content を返す） |
| `test_l0_live.py` | `OllamaReader` で 1 回、スキーマ準拠の JSON が返る。`format=` が効いているかをログで観測 | **実接続。** `SD_LIVE=1` と `SD_LIVE_MODEL` が無ければ skip（理由つき） |
| `test_units.py` | 3 規則の割り方・id・`render`・空本文 | なし |
| `test_l1.py` | `check_harness` の各規則を 1 件ずつ違反させて拾う（緑が偽物でないことを変異で確かめる）・試行と `PlanningFailed`・id と `origin`・交差検証の票の集約（過半数・並び 2 版・weak・nonexclusive）・検証役 1 体なら未検証 | 台本 |
| `test_l2.py` | id の正規化（`[s5]`・空白・鉤括弧）・実在照合・`SILENT` は根拠不要・L0 失敗 → 無効・並列でも順序が固定 | 台本 |
| `test_l3.py` | `direction_sample` 全組合せ・多数決と同数・`Counts`・p / w・率の定義・札の順序・**S4**（全軸の側を入れ替えると p' = 1 − p、w' = w）・**S12**（軸を 2 倍に複製しても w 不変）・`summarize_readers`（Δ・単読み手・偽読み手の除外）・**S0a 適合**: `out4/{base21,s4,mono,meta2,m3arm,mem,rashomon,mono_rashomon}/results.json` の各行を `AnswerMatrix` に起こして `aggregate` → p・w・札・軸ごとの d が記録と全一致（記録ディレクトリが無ければ skip） | なし |
| `test_l4.py` | `Probability`・`Ordinal` の等分と `bounds`・端点（0, 1）・`labels`・値なしの札 → `None`。型外に出る入力が作れない（**S2**） | なし |
| `test_judge.py` | 入口の検査（F1・F2）・偽読み手 2 体で端から端まで（**S11**: 全問 Yes → 計器不良、全問 SILENT → 根拠なし）・**S8** 呼び出し数（軸 6・読み手 2・標本 1・交差検証なし → 生成 1 ＋ 回答 24。キャッシュ命中で live 0）・記録 → `replay` で同じ `Reading`・`judge_sync`・**Q6** コアの import が標準ライブラリだけ（AST 走査） | 台本・偽読み手 |
| `tools/conformance_out4.py`（テストではなく道具） | `out4/base21` のキャッシュだけ（`cache_only`）で `judge()` を 21 題材に回し、**live 0 件**かつ p・w・札が `results.json` と一致。指示の逐語移植と鍵の同一性の検査。生成器 `gemma4:31b-cloud`・読み手 `qwen3.5:397b-cloud` / `glm-5.2:cloud`・標本 2・軸 6・`num_ctx` 8192・交差検証なし（`run.log` の開始行） | なし（キャッシュ） |

方針:

- **TDD**。層ごとにテストを先に書き、緑にしてから次の層へ
- 純関数のテストは実応答の固定データ（out4 から抜いた行）を持つ
- 実接続は `SD_LIVE=1` のときだけ。既定の `pytest` は LLM を呼ばない。GPU を使うローカルモデルは既定にしない（師匠と共有）
- 台本の読み手 `ScriptedReader` は `tests/conftest.py` に置き、**本物の `Reader` と同じ口**（`name`・`calibration`・`complete`）を持つ

## 10. 実装の順序と完成の定義

内側（最も基盤）から外へ、**一層ずつ完成させてから次へ**。各層の完成 ＝ ①テスト緑 ②触れる CLI が動く ③この文書の該当節と実装が一致。

| 切片 | 層 | 触れる CLI | 完成の検査 |
|---|---|---|---|
| A | `pyproject` ＋ `types` ＋ `prompts` ＋ **L0** ＋ **単位化** | `l0_chat.py`（モデル名・指示・スキーマを与えて構造化応答を見る。キャッシュの命中を表示）| `test_l0`・`test_units`・実接続 1 回（クラウド） |
| B | **L1** | `l1_chat.py`（本文ファイル・命題 → 軸の対。違反と試行、交差検証の票を表示）| `test_l1`・実接続で題材 1 本 |
| C | **L2・L3・L4** | `l2_chat.py`（本文・記述 → 3 値と根拠）／`l3_chat.py`（記録の回答行列 → p・w・札・診断値）／`l4_chat.py`（p・型 → 値） | `test_l2`・`test_l3`（S0a 適合・S4・S12）・`test_l4` |
| D | **合成** ＋ 記録 ＋ `replay` ＋ 費用 | `judge_cli.py`・`conformance_out4.py` | `test_judge`・適合検査 live 0 件で一致・README「決まったこと」の更新 |

- 切片 1 つ ＝ スプリント 1 つを推奨（各層の CLI を師匠が触る人間ゲートを切片の終わりに置く）。1 スプリントに畳むなら層ごとのゲートは残す
- 外側の層（次の切片）は README と本文書のスケッチに留め、担当の順番が来てから確定する
- venv は `.venv`（`.gitignore` 済み）。`pip install -e .` で開発

## 11. 実装判断（⚠ AI 判断。事後確認対象）

| # | 判断 | 理由 | 覆すと |
|---|---|---|---|
| I1 | コアは標準ライブラリだけ。Ollama アダプタも `urllib`（`to_thread`）で書く。LLMProviderlib のアダプタは v1 に入れない | 師匠の規則「基本ロジックの設計中は他のプロジェクトを読まない」。LLMProviderlib は師匠の別プロジェクトで、読まずにアダプタは書けない。空撃ちは標準ライブラリだけで 8,000 回回った | 利用側が `Reader` を実装すればどの接続でも差せる。師匠が許せばアダプタを別ファイルで足す（コアは変わらない） |
| I2 | 非同期を本体にし、同期は `judge_sync`。ストリーミングは使わない | Skill「LLM 呼び出しは非同期」。構造化出力は丸ごと検証するので流す意味が無い | 同期本体にすると並列は `ThreadPool` になる（空撃ちの形）。動くが Skill に反する |
| I3 | キャッシュの鍵と行の形は空撃ちと同一 | 測定 2 周目のキャッシュ（約 4 MB・約 6,000 応答）が鍵ごと使え、適合検査が LLM なしで成立する | 鍵を変えると適合検査は「結果の一致」だけになり、指示の逐語性が検査できない |
| I4 | 率の定義（無効率は回答ベース、矛盾率は標本割合の平均）は空撃ちと同一 | 閾値 κ 0.667 はその定義で置かれ、偽読み手 3 種で当てて確かめた | 上流 §7.4 の軸ベースに揃えるなら ι・κ を引き直す（K13）。`Counts.u2` は軸ベースで別に返すので材料は残る |
| I5 | 3 値はコードでは英語の列挙、LLM に見せる語は指示の集合 | 汎用性（Q6）。言語を変えても集約の式は変わらない | 日本語をコードに埋めると指示の集合を差し替えても語が残る |
| I6 | 交差検証で外した軸には答えさせない | 費用（Q8）。1 記述 1 呼び出しなので結果は同じ | 答えさせて記録に残す形もある（監査には有利、費用は増える） |
| I7 | 軸数の既定は「ちょうど」（`axes_min = axes_max = axes`） | 空撃ちと同じ検査。適合検査で同じ試行になる | 幅を持たせると生成器の再試行が減る（費用減）。利用側が `axes_min/max` で緩められる |
| I8 | 単位化の既定は `ja-sentence`。ファイル名は `units.py` | 測定はすべてこの規則。上流で番号が無い層 | 既定を `paragraph` にすると英文にも通るが、測った規則と違う |
| I9 | 順序尺度の既定は K 等分 | 上流 J5（仮置き）。「段 3 とは何か」は利用側 | `bounds` で利用側が切れ目を渡せる |
| I10 | 代表値は実読み手の p の平均 | 上流 U3（仮置き） | 中央値・最小などに変えるなら `summarize_readers` の 1 箇所 |
| I11 | 読み手は順に、記述は並列 | ローカルの読み手 2 体を同時に走らせると GPU を取り合う（叱責記録 2026-09-22） | クラウドだけなら読み手も並列にできる。`Budget` に旗を足せば済む |
| I12 | 記録に `schema_version` と `Axis.kind` を最初から置く | 上流 §13（検出項目・同義対・ブループリント）を同じ記録の形に載せるため | 無いと §13 で記録の形が変わり、蒸留の素材が割れる |
| I13 | `pyproject.toml`（setuptools）、`requires-python >= 3.11`、配布名 `structural-distillation`、`__version__ = "0.1.0"` | 依存ゼロ。3.11 以上は `asyncio.TaskGroup`・`Self` のため。手元は 3.13 | — |
| I14 | `validate` は JSON Schema の部分集合 | 依存を足さずに済む。使うスキーマは 3 つで、キーワードは限られる | 利用側が凝ったスキーマを渡す口は無い（渡す口自体が無い） |
| I15 | 指示ファイルの digest を `set.json` に持ち、違えば警告 | 版を上げ忘れた編集がキャッシュに黙って混ざるのを防ぐ | エラーにすると編集のたびに digest 更新が要る。警告に留める |
| I16 | `max_chars` の既定 12,000 | `num_ctx` 8192 で日本語の本文を記述ごとに送る空撃ちの経験則。羅生門（約 6,000 字）は 16,384 で回した | 読み手のアダプタが文脈長を申告する口を足せば自動化できる |
| I17 | CLI は `tools/` に置き、パッケージに入れない。標準ライブラリだけ。入出力は UTF-8 に固定 | 道具は用途で変わる。Windows のコンソールで落ちない | — |

## 12. 解かないこと（v1 の実装）

- 他の接続（LLMProviderlib・OpenAI 互換・Anthropic）のアダプタ。利用側が `Reader` を実装する
- 本文の分割・要約（F1 は拒否）
- ラベル集合の型・複合文の検出（上流 §10）
- 軸の**個別補充**（不足した側だけ足す）。v1 は生成全体の再試行だけ。`Diagnostics.retries` がその回数
- 全問 1 呼び出しの粒度（上流 J13 の「差は実験で測る」は未着手）
- 検出項目・同義対・ブループリント・分散成分（上流 §13）。記録の形に余地（`Axis.kind`）だけ残す
- 別プロセス間のキャッシュ排他
- 蒸留（L5）

## 変更履歴

- v1 [2026-09-22]: 初版。上流設計 v3.4 と空撃ちの道具（p2 / p3 / p4 / xc2）を根拠に、配置・契約・各層・指示の外部化・記録・実行モデル・失敗・テスト・順序・実装判断 I1〜I17 を置いた
