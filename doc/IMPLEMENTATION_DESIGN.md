# Structural Distillation 実装設計

- 版: v3（2026-09-22）。差分は末尾の変更履歴と、合意ドキュメント `.pair-agent/agreements/implementation-design.md`・`implementation-v1.md`
- 上流: [`UPSTREAM_DESIGN.md`](UPSTREAM_DESIGN.md) v3.5。**ドライバ・契約・規則はそちらが正。** この文書は「どう組むか」だけを書く
- ⚠ **この文書は仮説である。** 実装と実測で崩れたらこの文書を直す。上流設計と食い違ったら上流設計を優先し、食い違いを変更履歴に書く
- **実装 v1 は済**（`structural_distillation/`・`tools/`・`tests/`）。範囲は上流設計の L0〜L4 と合成（`judge()`）。蒸留（L5）は後段。
  実装で分かったことは §14 に。本文はその結果に合わせて直してある
- ⚠ **ライブラリは空撃ちの道具の移植である。** 空撃ちと違えるところは §13 の差分表に「何を・なぜ・置けない理由か意図的か」を全部書く

## 0. 何を根拠に書いているか

| 根拠 | 何を決めるか |
|---|---|
| 上流設計 v3.5（§3 ドライバ・§4 契約・§5 層・§6 ハーネス・§7 集約・§8 記録・§9 品質シナリオ） | 各層の IN → OUT、規則、失敗の扱い、検査すべきこと。付録 A に項目ごとの対応 |
| 空撃ちの道具 `.pair-agent/probes/upstream-probe-1/{probe,probe3,probe4_run,probe4_crosscheck}.py` | **動いた実物。ライブラリはこれの移植（port）**。測定 2 周目のキャッシュ 15,614 行（腕をまたぐ重複を含む。`out4/*/llm_cache.jsonl`）と空撃ち 1〜2 周目 2,379 行（`out/`・`out3/`）で検めた挙動（指示の文面・鍵・集約の式）を忠実に移す。**probe*.py は今後編集しない**（凍結された記録。生きた写しはライブラリ側） |
| 師匠の規則 | Python／他のプロジェクトを参照しない／依存は一方向・利用側を import しない／テストは単体で走る／値はすべて仮説 |
| ペア固有の confirmed process Skill | **1 層 ＝ 1 ファイル**（層番号がファイル名）／**内側から一層ずつ完成**／**各層に触れる最小 CLI**／指示は外部データ・規則はコード／用途で変わる値だけ外へ出す／**コアの依存は最小・道具は自由**／生応答キャッシュ → 再集計／LLM 呼び出しは非同期／移植は原典どおり・変えるのは置けないものだけ／TDD／venv |

どれにも辿れない項目は「⚠ I-番号」（実装判断。§11 に一覧。事後確認対象）。

## 1. 実装が満たすこと（ドライバの写像）

| # | 品質特性（上流 §3.1） | 実装上の要求 | どこで | どう検査するか |
|---|---|---|---|---|
| Q1 | 読み手非依存 | 読み手は差し替え可能な **口（Protocol）**。読み手ごとに独立に集約し、読み手間の差を一級で返す | `l0.Reader`・`l3.summarize_readers` | 台本の読み手 2 体で Δ が出る（`test_judge`） |
| Q2 | 型の閉包 | 値は L4 が型から**構成的に**作る。L0 は三段構えで形を守り、失敗は型付き | `l0.structured`・`l4.to_value` | S2: 壊れた応答を多数流して型外が 0 件（`test_l0`・`test_l4`） |
| Q3 | 偏りの打ち消し・検出 | 矛盾 u₄ を数え κ で計器不良 | `l3` | S11: 全問 Yes の偽読み手 → 計器不良（`test_judge`） |
| Q4 | 幅と確からしさ | w = 2·min(s,r)/(s+r)。診断値は数えた量をそのまま並べる | `l3` | S12: 軸を 2 倍に複製しても w 不変（`test_l3`） |
| Q5 | 検証可能性 | 記録に単位列・軸・回答（生応答つき）・根拠 id・読み手名・指示の版を残す | `judge`（記録） | 記録から `replay()` で同じ判定が出る（`test_judge`） |
| Q6 | 汎用性 | コアは標準ライブラリだけで、利用側も他のプロジェクトも import しない。ドメイン語は入力（本文・命題・型）と指示の集合にしか無い | パッケージ全体 | コアの import を AST で走査し標準ライブラリ以外が 0（`test_judge`） |
| Q7 | 再計算可能 | 生応答は L0 のキャッシュと記録に、判定は記録に残る。集約は純関数 | `l0.CachedPort`・`judge.replay` | S0a: 測定 2 周目の記録（`out4/*/results.json`）を `l3` で引き直して全一致（`test_l3`） |
| Q8 | 費用の可視性 | 役割（生成／交差検証／回答）別に 実呼び出し／キャッシュ命中 を数える。偽読み手は別枠 | `judge`（`Cost`） | S8: 軸 6・読み手 2・標本 1 で呼び出し数が式どおり（`test_judge`） |
| Q9 | 本文追従 | 本文は引数だけ。アダプタは URL 取得も検索もしない | `l0`（設計上の制約） | コードレビュー（機械検査なし） |

## 2. 配置（パッケージ構成）

```
structural_distillation/          # コア。標準ライブラリだけ（⚠ I1）
  __init__.py                     # 公開 API の再輸出・__version__
  contracts.py                    # 契約: データ型・列挙・例外・既定値。層ではない（層の間の共有語彙）
  prompts.py                      # 指示の集合（PromptSet）の読み込みと描画、4 つのスキーマの持ち主。層ではない（facility）
  store.py                        # 問いの保存庫: 同じ命題と本文なら過去の問いの集合を再利用する（§4.S）。層ではない（facility）
  l0.py                           # 読み手ポート: Reader / structured()（三段構え）/ validate() / CachedPort / OllamaReader / FakeReader
  units.py                        # 単位化（決定論・規則は版付き）。上流設計で番号の無い層
  l1.py                           # 問い生成: plan()・ハーネス検査（H1〜H11）・向きの交差検証（H12）
  l2.py                           # 回答: answer_all()・根拠 id の正規化と照合
  l3.py                           # 集約: side_evidence() / direction_sample() / majority() / aggregate() / label() / summarize_readers()。純関数
  l4.py                           # 型付け: to_value()。純関数
  compose.py                      # 合成: judge() / judge_sync() / replay() / 記録の書き出し（公開名 judge と同名にしない。受入 M4）
  prompt_sets/ja/set.json         # 指示（外部データ・版付き）。pyproject の package-data に入れる
tools/                            # 触れる CLI と道具。パッケージには入れない。依存は自由（v1 は標準ライブラリだけ）
  l0_chat.py units_chat.py l1_chat.py l2_chat.py l3_chat.py l4_chat.py   # 各層だけを叩く最小 CLI
  judge_cli.py                    # 本文ファイル・命題・型を渡して 1 判定（--questions で問いの保存庫を使う）
  questions_cli.py                # 問いの保存庫を見る（list / show）
  conformance_out4.py             # 適合検査: 測定 2 周目のキャッシュだけで judge() を回し、記録と一致するか（読み取り専用）
  probe_records.py                # 空撃ちの記録 → ライブラリの型（S0a と適合検査が使う。読むだけ）
  mutation_check.py               # 変異試験: 実装を 1 箇所ずつ壊し、テストが落ちるか
  _cli.py                         # CLI の共通部品（UTF-8・札の日本語名）
tests/                            # 層ごとに 1 ファイル ＋ 実接続 1 ファイル
  conftest.py test_l0.py test_l0_live.py test_units.py test_l1.py test_l2.py test_l3.py test_l4.py test_judge.py
  fixtures/                       # out4 から抜いた固定データ（キャッシュ行・記録の 1 題材）。テストは .pair-agent/probes/ に書かない
pyproject.toml                    # 実行時依存なし。開発依存は pytest だけ。package-data に prompt_sets/**
```

依存の向き（矢印の先しか import しない）:

    compose → l1, l2, l3, l4, units, l0, prompts, contracts
    l1, l2 → l0, units, prompts, contracts
    l3, l4 → contracts
    l0 → contracts
    prompts, units → contracts
    contracts → （標準ライブラリだけ）

- `l3`・`l4` は LLM にも I/O にも触れない純関数の層。ここに判断の式が全部ある
- `l0` だけが外の世界（HTTP・ファイル）に触れる。ツールモジュールの 3 層（純粋整形 ／ I/O ／ 公開）に従い、
  整形（指示へのスキーマ明記・フェンス剥がし・検証・鍵）は純関数、HTTP は `OllamaReader` の中だけ
- ファイル名は上流設計の層番号。単位化は上流で番号が無いので役割名（`units.py`）（⚠ I8）
- 契約のファイルは `contracts.py`（標準の `types` との衝突を避ける）。指示のデータは `prompt_sets/`（モジュール `prompts.py` と同名にしない）
- パッケージの公開名（`judge`・`CachedPort` など）は `__init__.py` が遅延して読み込む（PEP 562）。`import structural_distillation` だけでは層を読まない（層を 1 つずつ作ってテストするため。v3）

## 3. 契約（`contracts.py`）

すべて `dataclass`（`frozen` は値オブジェクトのみ）。JSON に往復できる（`to_dict` / `from_dict`）。列挙は `str` を継承した `Enum`。

### 3.1 入力

| 型 | 項目 | 上流 |
|---|---|---|
| `Budget` | `axes: int = 6`（生成器に頼む軸数）・`axes_min: int | None`・`axes_max: int | None`（None なら `axes` と同じ ＝ ちょうど。⚠ I7）・`samples: int = 1`・`plan_retries: int = 2`（初回 ＋ 再試行 2 ＝ 3 試行。空撃ちと同じ）・`workers: int = 1`（同時に飛ばす呼び出し数）・`crosscheck: bool = True`（H12 既定 ON）・`max_chars: int = 12000`（F1 の門。⚠ I16 仮置き） | §4.1 予算 |
| `Thresholds` | `iota = 0.3`・`kappa = 0.667`・`rho = 0.5`・`omega = 0.5`・`delta = 0.2`。docstring に出所（κ のみ昇格、他は仮置き。合意 K13） | §7.4 |
| `OutputType` | `Probability()` ／ `Ordinal(k: int, labels: tuple[str, ...] | None = None, bounds: tuple[float, ...] | None = None)`。`bounds` は昇順の k−1 個の切れ目（省略時は K 等分。⚠ I9）。`labels` は長さ k | §4.1 型・§7.6 |
| `PromptSet` | §5。`"ja"` の名前か、ファイルから読んだ実体 | §6 J9 |

### 3.2 中間の産物

| 型 | 項目 |
|---|---|
| `Unit` | `id: str`（`s1`…）・`text: str` |
| `Verdict` | `STATES`（述べている）／ `DENIES`（否定している）／ `SILENT`（触れていない）。**コードは英語の列挙、LLM に見せる語は指示の集合が持つ**（⚠ I5） |
| `Axis` | `id`（`a01`…）・`name`（人が読む軸名。測定に使わない）・`claim_support`・`claim_refute`・`origin: "initial" | "retry"`・`kind: "substantive"`（⚠ I12: 上流 §13 の検出項目・同義対を将来 `"detection"` / `"synonym"` として同じ記録に載せるための欄。v1 は `"substantive"` だけ） |
| `Attempt` | 生成の 1 試行: `index: int`（JSON では `"try"`。予約語なので欄名は `index`）・`violations: list[str]`（違反した規則 H の名前つき）・`n_axes: int | None`・`error: str | None` |
| `CrossCheck` | H12 の記録: `verifiers: list[str]`・`votes`（軸 × 側 × 検証役 × 並び 2 版の生の票と多数決）・`flagged: list[str]`・`weak: list[str]`（「どちらとも言えない」が出た軸）・`nonexclusive: list[str]`（両立しうると言われた対。診断のみ） |
| `QuestionSet` | `axes: list[Axis]`・`active_ids: list[str]`（交差検証で外した後）・`planner: str`・`prompt: PromptVersion`・`budget: Budget`・`attempts: list[Attempt]`・`crosscheck: CrossCheck | None`・`source: "generated" | "given" | "stored"`（`judge()` に渡されたものなら `given`、保存庫から再利用したら `stored`）・`store_key: str | None`（保存庫の鍵） |
| `Answer` | `axis_id`・`side: "support" | "refute"`・`sample: int`・`verdict: Verdict | None`・`evidence_raw: list[str]`（LLM が返したまま）・`evidence: list[str]`（正規化後）・`valid: bool`・`error: str | None`・`raw: str | None`（フェンスを剥がした後の応答本文。上流 §8「生応答」）・`key: str | None`（キャッシュの鍵）・`cached: bool` |
| `AnswerMatrix` | `reader: str`・`calibration: bool`（偽読み手か）・`prompt: PromptVersion`・`samples: int`・`answers: list[Answer]` |

### 3.3 出力

| 型 | 項目 | 上流 |
|---|---|---|
| `Counts` | `s`・`r`・`u1`（沈黙）・`u2`（無効）・`u3`（標本同数）・`u4`（矛盾）・`n`。すべて**軸ベース**（d = 0 の軸を `zero_kind` で分けた数。§4.3） | §7.1 |
| `AxisReading` | `axis_id`・`d: -1 | 0 | +1`・`zero_kind: "silent" | "invalid" | "tie" | "contradiction" | None`・`agreement: float`（最多票の割合）・`d_samples: list[int]`・`v_support`・`v_refute`（標本ごとの答え。`Verdict | "invalid"`）・`contradiction: float`（両側に証拠が出た標本の割合）・`invalid: int`（無効な回答数。両側・全標本）・`silent: int`（両側とも SILENT の標本数） | §7.5 軸ごとの向き |
| `Diagnostics` | `valid_rate`・`valid_rate_by_side: {support, refute}`・`silent_rate`・`invalid_rate`・`agreement_mean`・`contradiction_rate`（有効な軸が 0 本なら率はすべて `None`。測って 0 だったのと区別する。受入 M5）・`retries`（生成の再試行回数 ＝ 採用した試行の `index`）・`p_by_sample: list[float | None]` | §7.5 |
| `Label` | `LEAN_SUPPORT`／`LEAN_REFUTE`／`SPLIT`／`NO_EVIDENCE`／`INSTRUMENT_FAULT` | §7.4 の 5 値 |
| `Reading` | 読み手 1 体の判定: `reader`・`calibration`・`counts`・`p`・`w`・`label`・`value: Value | None`・`diagnostics`・`axes: list[AxisReading]`・`reason: Reason | None`（札がその値になった理由。偏り・割れるでは `None`） | §4.2 読み手ごと |
| `Reason` | `NO_ACTIVE_AXES`（軸 0 本）／`INVALID_EVIDENCE`（無効率 ≥ ι）／`CONTRADICTORY_AXES`（矛盾率 ≥ κ）／`NO_DEFINITE_AXIS`（s + r = 0）／`LOW_VALID_RATE`（有効率 < ρ）。機械が読む符号。人が読む文は CLI と `RetryHint.message` | §4.2 理由・師匠決定 2026-09-23 |
| `RetryHint` | `reason`・`scope`（`"question_set"`）・`action`（`REGENERATE` / `REPLAN` / `SUPPLY_QUESTION_SET`）・`message`（人が読む一言）・`details`（`flagged`・`n_axes`・`verifiers`・`store_key`・`source`・`votes_in`・`readers`）。**ライブラリは自動で作り直さない**（上流 J20） | §4.4 F4′ |
| `Value` | `Probability` なら `p`、`Ordinal` なら `level: int`（1..K）と `label: str | None` | §7.6 |
| `ReaderSummary` | `delta: float | None`（実読み手のうち**値を持つ**ものの p の最大 − 最小）・`levels_agree: bool | None`（順序尺度で値を持つ読み手が 2 体以上のときだけ）・`readers_split: bool | None`（Δ > δ）・`representative: Value | None`（⚠ I10: 同じ読み手集合の p の平均。U3 仮置き）・`readers: list[str]`（要約の対象にした読み手）・`note: str | None`（`"single reader"` / `"no reader with a value"`） | §4.2 要約 |
| `Cost` | 役割別 `{plan, crosscheck, answer}` × `{live, cached, missed}`（missed ＝ cache-only で外れた。LLM は呼んでいない）と合計。`calibration_calls`（偽読み手の呼び出し。LLM は呼んでいない） | §4.2 費用・Q8 |
| `Judgment` | `proposition`・`output`・`units`・`segmentation: str`・`question_set`・`matrices: list[AnswerMatrix]`・`readings: dict[str, Reading]`（鍵は読み手名。一意性は入口で検査）・`summary: ReaderSummary`・`cost`・`versions: {library, prompts, units_rule, aggregation}`・`budget`・`thresholds`・`at`・`notes: list[str]`・`retry: RetryHint | None`・`to_record() -> dict` | §4.2・§8 |

### 3.4 例外

| 例外 | いつ | 上流 |
|---|---|---|
| `InputError` | 本文が空・命題が空・K < 2・`labels` の長さ ≠ K・`bounds` の長さ ≠ K−1 または非昇順または (0,1) の外・`axes_min > axes_max`・本文が `max_chars` を超える・読み手が 0・読み手名の重複・生成器が偽読み手 | F1・F2 |
| `PlanningFailed(attempts)` | 生成が `plan_retries` まで規則を満たせない。`attempts` に違反した規則が残る | F3 |
| `StoreError` | 問いの保存庫のファイルが読めない（壊れた JSON・版の違い・中身と鍵の食い違い・軸 id の重複）。上書きせずに止める | §4.S |

LLM の失敗（HTTP エラー・形式崩れ）は**例外にしない**。L0 が `ok=False` で返し、L1 は試行として数え、L2 は無効として数える（F6）。

## 4. 各層（IN → OUT と内部規則）

### 4.0 L0 読み手ポート（`l0.py`）

**IN** (指示, 回答スキーマ, 読み手) → **OUT** スキーマ準拠の JSON オブジェクト **または** 型付きの失敗。

```python
class Reader(Protocol):
    name: str                 # 記録と鍵に使う。モデル名（"gemma4:31b-cloud"）や "fake:all_yes"
    calibration: bool         # 偽読み手なら True（Δ と代表値から除く。費用は別枠）
    async def complete(self, messages: list[dict], schema: dict, *, sample: int, version: str) -> RawReply
        # RawReply(ok, content, error, meta)。content はフェンスを剥がした後。例外を投げない
        # meta: {"cached": bool, "key": str | None, "eval_count", "total_duration", "prompt_eval_count"}
```

`version` は鍵の材料（指示の版。`p3` / `p2` / `p2:retry` / `xc2`）。素の `OllamaReader` は使わないが口には必ず入れる（`CachedPort` が同じ口で包むため）。

| 部品 | 責務 | 性質 |
|---|---|---|
| `with_schema(messages, schema, notice) -> messages` | **最後の user メッセージ**の末尾に `notice` ＋ `json.dumps(schema, ensure_ascii=False)`（**sort_keys 無し・既定セパレータ**。dict の挿入順がそのまま出る）を足す。他のメッセージは触らない | 純関数 |
| `strip_fence(text) -> text` | 全文が単一のコードフェンスならフェンスだけ剥がす。部分抽出（修復）はしない | 純関数 |
| `validate(obj, schema) -> list[str]` | JSON Schema の**部分集合**を検査: `type`（object / array / string / integer / number / boolean）・`properties`・`required`・`enum`・`items`。それ以外のキーワードは使わない（⚠ I14）。⚠ `additionalProperties` は使わない（スキーマに足すと鍵が変わる） | 純関数 |
| `cache_key(model, messages, schema, sample, version) -> str` | `sha1(json.dumps({"m": model, "msgs": messages, "schema": schema, "sample": sample, "v": version}, ensure_ascii=False, sort_keys=True))`。**`messages` は `with_schema` 適用後**。`sample` は int。**空撃ちと同一**（⚠ I3） | 純関数 |
| `structured(reader, messages, schema, *, version, sample, notice, retry=False) -> Structured` | **三段構え**: ① `schema` を読み手に渡す（ネイティブ指定。信用しない）② `with_schema` で指示にも明記 ③ 受信で `strip_fence → json.loads → validate`（アダプタも剥がして返すが、利用側の `Reader` が剥がさなくても通るよう冪等にもう一度。v3）。`retry=True` のときだけ、失敗なら `version + ":retry"` で**再送 1 回**（空撃ちで再送するのは回答だけ。⚠ I18）。`Structured(ok, obj, content, error, attempts, cached, key)` | I/O を呼ぶが自分は判断しない |
| `CachedPort(reader, path, *, cache_only=False, read_only=False, extra=())` | `Reader` を包む `Reader`（`name` は素通し。鍵の `m` はモデル名。`path=None` ならメモリだけ）。鍵で引き、無ければ `reader.complete` → **成功した応答だけ**を追記のみ JSONL に `{"key","model","sample","version","payload"}`（行の形は空撃ちと同一。⚠ I19: 空撃ちは失敗も書いたが、失敗を永続化すると再走でも直らない）。`extra` は読むだけの追加キャッシュ（複数の記録を合わせて読む）。`read_only` なら一切書かない。同じ鍵の同時要求は 1 回にまとめる（`asyncio.Future` を共有）。`cache_only` で外れたら `ok=False, error="cache-only miss"`。`calls_live` / `calls_cached` | I/O（ファイル） |
| `OllamaReader(model, *, host="http://localhost:11434", num_ctx=8192, think=False, timeout=600, retries=3)` | `/api/chat` に `format=schema`・`options.num_ctx`・`think`。`think` を拒む（400 に "think"）モデルには `think` 無しで再送。429 / 5xx は 5·(k+1) 秒待って**初回 ＋ 再送 3 ＝ 最大 4 試行**。`content` は `strip_fence` 後。`urllib` を `asyncio.to_thread` で呼ぶ（⚠ I1: 依存を足さない）。`meta` に `eval_count`・`total_duration`・`prompt_eval_count`（文脈長の切り詰めを事後に見るため） | I/O（HTTP） |
| `FakeReader(kind, *, seed=0)` | `kind ∈ {all_yes, all_no, all_undetermined, random}`（名前は空撃ちと同じ）。`name = "fake:<kind>"`、`calibration=True`。回答スキーマ（`verdict` を持つ）にだけ答える: `{"verdict": <kind の語>, "evidence": ["s1"]}`（`s1` は単位列の先頭 id なので常に実在。`SILENT` は `[]`）。`random` は `sha1(seed, messages, sample)` で 3 値を決める（**呼び出し順に依存しない**。並列でも同じ）。他のスキーマ（生成・向き）には `ok=False` | 決定論 |

鍵を同一にするために守ること（適合検査 §9 が落ちたらここを疑う）:

| 要素 | 空撃ちの値 |
|---|---|
| `notice` | `"\n\n## 出力形式\n次の JSON Schema に厳密に一致する JSON オブジェクトだけを出力する。キー名はスキーマのとおり。コードフェンス・前置き・後書きは禁止。\n"`（先頭の `\n\n` と末尾の `\n` を含む）。指示の集合が持つ |
| スキーマの挿入順 | 生成: `type, properties{axes{type, items{type, properties{axis, claim_support, claim_refute}, required}}}, required`。回答: `type, properties{verdict{type, enum}, evidence{type, items{type}}}, required`。向き: `type, properties{orientation{type, enum}}, required`。排他性: `type, properties{compatible{type, enum}}, required`。`enum` の順は指示の集合の並び（STATES, DENIES, SILENT ／ SUPPORT, REFUTE, NEITHER ／ COMPATIBLE, EXCLUSIVE） |
| `messages` | `[{"role":"system","content":…},{"role":"user","content":…}]` だけ。余分なキーを足さない。user 本文は**末尾改行 1 つ**で終わる |
| `sample` の意味 | 生成 ＝ 試行番号（0..）／回答 ＝ 標本番号／向き ＝ 選択肢の並び反転フラグ（0 = 元、1 = 反転）／排他性 ＝ 常に 0 |
| 鍵に**入らない**もの | `num_ctx`・`think`・`timeout`・`host`。⚠ 帰結: 文脈長を変えて回した応答が同じ鍵を共有する（羅生門は 16384 で回した）。`prompt_eval_count` を meta に残し、切り詰めは事後に見る |

失敗は `ok=False` で返す。**投げるのはプログラムの誤り（引数の型違いなど）だけ。**
縮退する箇所（JSON でない・スキーマ違反・再送で回復・cache-only で外れた・think を外した・429 / 5xx で待った・失敗の行を読み飛ばした・キャッシュに書けなかった）は `logging`（`structural_distillation.l0`）に残す。
フェンスを剥がしたことはログに残さない（クラウドの読み手では常態で、ログが埋まる）。

`Structured` の欄: `ok`・`obj`・`content`（最後の試行の応答。失敗なら None のこともある）・`error`・`attempts`（呼んだ回数）・`live`・`cached`・`missed`・`key`（最後の試行の鍵）・`version`（最後に使った版）。
生応答と鍵は同じ試行のものを組にする（受入 m3）。役割別に数えるのは `Meter`（`l0.py`。`judge` が 1 つ持ち、各層に渡す）。

### 4.U 単位化（`units.py`）

**IN** 本文 → **OUT** 単位列 `list[Unit]`。決定論。

| 規則名 | 割り方 | 版 |
|---|---|---|
| `ja-sentence`（既定。⚠ I8） | `。！？` の直後で割る（`re.split(r"(?<=[。！？])")`）。前後の空白を落とし、空を捨てる。id は `s1`… | `v1`（空撃ちと同一） |
| `paragraph` | 空行で割る | `v1` |
| `line` | 改行で割る | `v1` |

`render(units) -> str` は `[s1] 本文…` を改行で並べる（指示に埋める形。空撃ちと同一。末尾に改行は付けない）。
規則名と版は `Judgment.versions.units_rule` に残る。

### 4.1 L1 問い生成（`l1.py`）

**IN** (生成器, 単位列, 命題, 予算, 指示の集合, 検証役) → **OUT** `QuestionSet` **または** `PlanningFailed`。

生成は LLM、規則の検査はコード（上流 §6）。スキーマは**コードが持つ**（契約。挿入順は §4.0 の表）。
4 つのスキーマの持ち主は `prompts.py`（`plan_schema` / `answer_schema` / `orient_schema` / `exclusive_schema`。enum の語が指示の集合から来るので。v3）。

試行: `index = 0 .. plan_retries`、`sample = index` で `structured(planner, plan_prompt, retry=False)` → L0 が失敗（`ok=False`。HTTP 失敗・形式崩れ）なら違反 `["L0 失敗"]` の試行として次へ（空撃ちと同じ。再送しない）→ `check_harness` → 違反が無ければ採用。全試行が違反なら `PlanningFailed(attempts)`。
⚠ 空撃ちは上限到達時に「違反付きの最後の軸」で続行した（観察のため）。ライブラリは上流 F3 のとおり**拒否**する（差分表 §13）。

`check_harness(axes, proposition, lo, hi) -> list[str]`（違反の一覧。空なら合格。空撃ち `probe3.check_plan` と同じ規則・同じ順。違反の先頭の語が規則のコード `H1`〜`H11`・`L0`）:

| 規則 | 検査 | 違反の表記 |
|---|---|---|
| H6 予算 | `axes_min ≤ len(axes) ≤ axes_max` | `H6 軸数` |
| H1 両側を持つ | `claim_support`・`claim_refute` が空でない（空なら**その軸の他の検査を飛ばす**）。正規化して同一でない（同一の両側は、2 回目の記述として H4 にも当たる。空撃ちと同じ） | `H1 空の記述` / `H1 両側が同一`（空撃ちの表記は `H7 …`。規則の名前を上流に合わせた） |
| H4 非重複 | 正規化した記述（両側とも）が、それまでに見た記述と同一でない | `H4 非重複` |
| H3 非自明 | 記述と命題の文字 2-gram Jaccard ≥ 0.6 なら言い換え | `H3 非自明` |
| H11 平叙文 | 記述の末尾が `か` / `？` / `?` でない | `H11 疑問文` |
| H7 形式 | スキーマ照合は L0 が済ませている。違反は `L0 失敗` として試行に残る（`H7 形式` という表記は出ない） | — |

正規化は空撃ちと同じ（`[\s、。「」・,.?？!！]` を除く）。H2（観察可能）は検査しない（上流 §10）。
⚠ H3 の 0.6・正規化の文字集合・H11 の終端・単位化の `。！？` は日本語依存だが、v1 は空撃ちどおり**コードに置く**（§12）。

id は採用した試行の順に `a01`…。`origin` は `index == 0` で採用なら `"initial"`、それ以外は `"retry"`。

**交差検証（H12）**: `budget.crosscheck` が真で検証役が 2 体以上のときだけ回す（1 体以下なら回さず `crosscheck=None`、`logging` に「未検証」）。
空撃ち `probe4_crosscheck.py` v2 の規則をそのまま（`version = "xc2"`、再送なし）:

- 軸 × 側 × 検証役 × 並び 2 版（`sample = 0` 元の並び、`sample = 1` 反転）で「この記述が本文に述べられていたら命題は支持されるか」を聞く（本文は見せない）
- 検証役ごと・側ごとに版 2 つの多数決を票にする。**版が割れたら `同数` で、反対には数えない。** どちらかの側で票が期待と反対（支持側の記述に `反証`、反証側の記述に `支持`）なら、その検証役は「反対」
- **反対の検証役が過半数**（`反対の数 × 2 > 検証役の数`）なら `flagged`
- 「どちらとも言えない」が 1 票でも出た軸は `weak`（外さない。診断）
- 対の排他性（「両方が同時に述べられうるか」。`sample = 0`）を検証役ごとに聞き、**有効な回答（`None` を除く）の過半数**が「両立しうる」なら `nonexclusive`（外さない。診断）
- `active_ids = 全 id − flagged`。全部外れたら空のまま返す（各読み手は F4 で値なしになる。`logging` に残す）

### 4.2 L2 回答（`l2.py`）

**IN** (読み手, 単位列, `QuestionSet`, 予算, 指示の集合) → **OUT** `AnswerMatrix`。

- 仕事の単位は **(有効な軸, 側, 標本)** で 1 呼び出し（上流 J13。⚠ I6: 交差検証で外した軸には答えさせない。1 記述 1 呼び出しなので、外してから答えても、答えてから外しても同じ回答行列になる）
- `budget.workers` の `asyncio.Semaphore` で同時数を絞り、`gather` で回す。**結果の並びは (軸, 側, 標本) の順に固定**（並列でも記録が同じ）
- 回答スキーマはコードが持つ: `{"verdict": enum[指示の集合の 3 語], "evidence": [string]}`
- `structured(reader, answer_prompt(units_text, claim), version=answer の版, sample=標本, retry=True)` → `verdict` の語を `Verdict` に引く → `evidence_raw` を正規化（`[\[\]\s「」]` を除き、空を捨てる。上流 J16）→ `STATES` / `DENIES` は根拠が 1 つ以上あり**すべて単位列に実在**すれば `valid`、それ以外は `valid=False`（F6）。`SILENT` は根拠を要求しない
- L0 が `ok=False`（再送後）なら `verdict=None, valid=False, error=…`
- ⚠ `validate` は `evidence` の欠落・非文字列を受理しない（空撃ちは欠落を `[]`、非文字列を無視して受理した）。型の閉包のための意図的な逸脱（差分表 §13）

### 4.3 L3 集約（`l3.py`）

**IN** `AnswerMatrix`（＋ `QuestionSet`・`Thresholds`）→ **OUT** `Reading`（値なし。値は L4）。**純関数。** 上流 §5.3・§7 の式を、**空撃ち（`probe3.aggregate`・`probe.majority`・`probe.label`）と同一の規則**で。

標本 1 つの向き（`side_evidence` → `direction_sample`。空撃ち `probe3.side_evidence` と同一。⚠ 上流 §5.3 の「無効を含む → d = 0」とは違う。上流 v3.5 の脚注参照）:

    支持側に証拠 ＝ v_support == STATES または v_refute == DENIES
    反証側に証拠 ＝ v_refute == STATES または v_support == DENIES
    （無効な回答は、その側の証拠にならない。軸を丸ごと 0 にはしない）
    両方                          → 0（contradiction）
    支持側だけ / 反証側だけ       → +1 / −1
    どちらも無く、無効を含む      → 0（invalid）
    どちらも無く、無効を含まない  → 0（silent）

軸の向き `d` は標本の多数決（`+1 / −1 / 0` の票。最多が一意ならそれ、同数なら 0 で `zero_kind="tie"`）。`agreement` は最多票の割合（同数のときも最多票の割合。空撃ち `majority` と同一）。
`d = 0` が多数決で決まったときの `zero_kind` は 0 票の種類の最多。同数なら **contradiction ＞ invalid ＞ silent** の順に取る（tie は「最多が一意でない」で先に決まる）。

数える（読み手 1 体・軸 n。すべて軸ベース）: `s`（d=+1）・`r`（d=−1）・`u1`（silent）・`u2`（invalid）・`u3`（tie）・`u4`（contradiction）・`n = s+r+u1+u2+u3+u4`。`p = s/(s+r)`、`w = 2·min(s,r)/(s+r)`（`s+r = 0` なら両方 `None`）。

⚠ **札に使う率の定義は空撃ちと同一にする**（⚠ I4）。閾値 κ 0.667 はこの定義で置かれた:

| 率 | 定義（空撃ち `probe3.aggregate` と同一） | 上流 §7.4 の表記 |
|---|---|---|
| `invalid_rate` | 無効な回答 ÷ 全回答（2 × 標本 × 軸） | u₂ / n（軸ベース。`Counts.u2` で別に返す） |
| `contradiction_rate` | 軸ごとの「両側に証拠が出た標本の割合」の平均 | u₄ / n |
| `silent_rate` | 軸ごとの「両側とも SILENT の標本の割合」の平均 | u₁ / n |
| `valid_rate` | (s + r) ÷ n | 同じ |
| `valid_rate_by_side` | 支持側: `v_support ∈ {STATES, DENIES}` の (軸, 標本) の割合。反証側: `v_refute` の同。対の形での「片側性」（上流 §7.1「観点ごと」の対の形での定義。上流 v3.5 脚注） | 観点ごとの有効率 |
| `agreement_mean` | 軸ごとの `agreement` の平均 | 標本間の一致率 |

標本 1 のとき `contradiction_rate`・`silent_rate` は上流の表記と一致する。`invalid_rate` は回答ベースなので軸ベースの u₂/n と一致しない。
上流の表記に合わせ直すなら ι の値も引き直す（K13）。

札と理由（`label` は `(Label, Reason | None)` を返す。順序 軸 0 → ι → κ → ρ → ω。空撃ち `probe.label` に軸 0 本の行を足したもの）:

    n == 0                     → INSTRUMENT_FAULT / NO_ACTIVE_AXES（師匠決定 2026-09-23・上流 F4′）
    invalid_rate ≥ ι           → INSTRUMENT_FAULT / INVALID_EVIDENCE
    contradiction_rate ≥ κ     → INSTRUMENT_FAULT / CONTRADICTORY_AXES
    s + r == 0                 → NO_EVIDENCE / NO_DEFINITE_AXIS（ρ に関わらず。受入 M3）
    (s+r)/n < ρ                → NO_EVIDENCE / LOW_VALID_RATE
    w ≤ ω and p > 0.5          → LEAN_SUPPORT
    w ≤ ω and p < 0.5          → LEAN_REFUTE
    それ以外                    → SPLIT

⚠ 軸 0 本の行は空撃ちには無い（空撃ちは軸 0 本で落ちる）。測定の記録は軸 6 本なので S0a・適合検査に影響しない。

⚠ `out4/*/results.json` の `label` は走行時の既定 κ = 0.5 で付いている（`probe3.aggregate` が κ を渡していない）。
矛盾率が [0.5, 0.667) の行は **1 行ある**（mono の m17・除外題材。v2 の「無い」は誤り）。S0a は**走行時の閾値（κ = 0.5）で比べる**（v3）。

`summarize_readers(readings, thresholds) -> ReaderSummary`: 実読み手（`calibration=False`）のうち**値を持つ**ものの p から Δ（最大 − 最小）、段の一致、`readers_split = Δ > δ`、代表値 ＝ p の平均（⚠ I10）。値を持つ実読み手が 1 体なら Δ は `None`、`note="single reader"`、0 体なら代表値も `None`、`note="no reader with a value"`（F5。0 で埋めない）。
⚠ 空撃ちの要約表は札に関係なく p を使っていた。値なしの札の p を使わないのは合意 K12 の訂正と同じ判断（差分表 §13）。

### 4.4 L4 型付け（`l4.py`）

**IN** (`p`, `label`, `OutputType`) → **OUT** `Value | None`。純関数。

- `label ∈ {NO_EVIDENCE, INSTRUMENT_FAULT}` または `p is None` → `None`
- `Probability` → `Value(p=p)`
- `Ordinal(k, labels, bounds)`: `bounds` 省略時は `level = min(k−1, int(p·k)) + 1`（空撃ち `level5` と同一の式。K 等分）。`bounds` があれば `level = 1 + #{b ∈ bounds : b ≤ p}`（`p = 1.0` は最上段）。`labels` があれば `Value.label = labels[level−1]`
- 値は型から構成するので型の外に出る経路が無い。`k < 2`・`labels` の長さ・`bounds` の形は `check_output` が検査し、`judge` の入口で `InputError`
- `type_value(p, output)` は札を見ずに型の値にする（読み手間の代表値に使う。v3）

### 4.5 合成（`compose.py`）

```python
async def judge(text: str, proposition: str, output: OutputType, *,
                readers: Sequence[Reader], planner: Reader | None = None,
                verifiers: Sequence[Reader] | None = None,
                question_set: QuestionSet | None = None,        # 渡せば L1 を飛ばし固定する（上流 J7・S1・S3・S10）
                question_store: QuestionStore | str | os.PathLike | None = None,  # 同じ命題と本文なら再利用（§4.S）
                regenerate: bool = False,                       # 保存庫にあっても作り直す
                budget: Budget = Budget(), thresholds: Thresholds = Thresholds(),
                prompts: str | PromptSet = "ja", segmentation: str = "ja-sentence",
                record_path: str | os.PathLike | None = None) -> Judgment

def judge_sync(...同じ...) -> Judgment          # asyncio.run で包む。既に走っているループの中では使えない
def replay(record: dict, *, thresholds=None, output=None, reparse=False, prompts=None) -> Judgment
    # 記録から LLM を呼ばずに引き直す。既定は L3・L4 だけ。reparse=True なら生応答（raw）から L2 の解釈（正規化・照合）も
```

変換の並び（依存関係だけで決まる）:

1. 入口の検査（F1・F2・型の整合・閾値の値域・読み手名の一意性・`regenerate` には保存庫が要る）→ `InputError`。
   生成器と検証役の検査（偽読み手でない・検証役名の重複）は**生成するときだけ**行う（渡された問い・保存庫の問いを使うときは生成器が要らない）
2. `units.segment`
3. `question_set` があればそのまま（`source="given"`。`active_ids` もそのまま。保存庫は見ない）。
   無くて `question_store` があれば、同じ命題と本文の問いの集合を引く。当たれば L1 を飛ばす（`source="stored"`。生成も交差検証も呼ばない）。
   どちらでもなければ `l1.plan`（`source="generated"`）。保存庫があれば作った問いの集合を保存する。生成器の既定は実読み手の先頭。検証役の既定は実読み手（偽読み手を除く）。2 体未満なら交差検証なし
4. 読み手ごとに `l2.answer_all` → `l3.aggregate` → `l4.to_value`。⚠ I11: **読み手は 1 体ずつ順に**、1 体の中の記述は `workers` 並列（ローカルの読み手を 2 体同時に走らせると GPU を取り合う）
5. `l3.summarize_readers`
5.5 判定に使える軸が 0 本なら `RetryHint` を作る（上流 F4′）。`action` は問いの出どころで決まる:
   保存庫を使っていれば `REGENERATE`（`regenerate=True` で作り直す）、使っていなければ `REPLAN`（もう一度 `judge` を呼ぶ）、
   `question_set` を渡されていれば `SUPPLY_QUESTION_SET`（渡す側が作り直す）。`details` に外れた軸・検証役・軸数・保存庫の鍵・票の在りかを入れる。
   **自動では作り直さない**（何回試すか・生成器を替えるかは用途ごとの判断）
6. `Cost`: `judge` が `structured()` の戻り（`cached`）を役割別に数える（`plan` / `crosscheck` / `answer`）。偽読み手の呼び出しは `calibration_calls` に別枠で数える（LLM ではない）
7. `record_path` があれば `Judgment.to_record()` を JSONL に追記（§6）

## 5. 指示の集合（`prompts.py`・`prompt_sets/<name>/set.json`）

指示は**用途（言語・文体）で変わる値**なので外に出す。規則（スキーマ・検査・集約）はコードに残す（上流 J9・Skill「外に出すのは用途で変動する値だけ」）。

1 集合 ＝ 1 ファイル（JSON）。改行・空白が編集ソフトに丸められないよう、Markdown ではなく JSON 文字列で持つ:

```json
{"name": "ja",
 "verdicts":     {"STATES": "述べている", "DENIES": "否定している", "SILENT": "触れていない"},
 "orientations": {"SUPPORT": "支持", "REFUTE": "反証", "NEITHER": "どちらとも言えない"},
 "exclusivity":  {"COMPATIBLE": "両立しうる", "EXCLUSIVE": "両立しない"},
 "schema_notice": "\n\n## 出力形式\n次の JSON Schema に…禁止。\n",
 "plan":      {"version": "p3",  "system": "…", "user": "…$proposition…$n_axes…$units_text\n", "digest": "<sha1>"},
 "answer":    {"version": "p2",  "system": "…", "user": "…$units_text…$claim…$v_states…$v_denies…$v_silent…\n", "digest": "<sha1>"},
 "orient":    {"version": "xc2", "system": "…", "user": "…$proposition…$claim…$options\n",
               "options": ["- 述べられていることで命題が正しい方向に傾くなら \"支持\"", "- …\"反証\"", "- …\"どちらとも言えない\""], "digest": "<sha1>"},
 "exclusive": {"version": "xc2", "system": "…", "user": "…$claim_a…$claim_b…\n", "digest": "<sha1>"}}
```

- テンプレートは `string.Template`（`$name`）。`{}` を含む JSON 例を書いても壊れない
- **3 値の語は `verdicts` が唯一の持ち主。** テンプレートには `$v_states` 等で埋め、回答スキーマの `enum` も同じ値・同じ順から作る（同じ線引きを 2 箇所に持たない）。向き・排他性の語も同様
- `orient.options` は元の並び。反転版は**行順の反転**（`sample = 1`）。`$options` は改行で結合
- `PromptVersion = {set: "ja", plan: "p3", answer: "p2", orient: "xc2", exclusive: "xc2", digest: <set.json 全体の sha1>}`。
  **鍵に入るのは版の文字列**（空撃ちと同一。`p2` の再送は `p2:retry`）。`digest` は記録にだけ残る
- 各テンプレートの `digest` は `sha1(system + "\x00" + user [+ "\x00" + options…])`。読み込み時に突き合わせ、違えば `logging` に警告「指示が版を上げずに変更されている」（⚠ I15。エラーにはしない）
- 読み込み時に、各 user テンプレートの必須の差し込み口（生成: `units_text`・`proposition`・`n_axes`／回答: `units_text`・`claim`／向き: `proposition`・`claim`・`options`／排他性: `claim_a`・`claim_b`）と、語の一意性（語から符号を一意に引けること）を検査し、欠ければ `ValueError`（受入 m7）
- `replay(reparse=True)` は記録の集合の名前で組み込みを引く。組み込みに無い集合は `prompts=` で渡す。記録の digest と違えば警告
- **v1 の `ja` は空撃ちの指示を逐語で移す**: 生成 ＝ `probe3.plan_prompt`（p3）、回答 ＝ `probe.answer_prompt`（p2）、向き ＝ `probe4_crosscheck.orient_prompt`、排他性 ＝ `probe4_crosscheck.excl_prompt`（xc2）。**user 本文は末尾改行 1 つで終わる**（f-string の三重引用の末尾）。これで測定 2 周目のキャッシュが鍵ごと再利用でき、適合検査（§9）が LLM を呼ばずに成立する
- 利用側は `PromptSet.load(path)` で自分の集合を渡せる（言語を変える・文体を変える）。変えたら別の計器なので閾値は引き直しの対象
- 配布: `pyproject.toml` の package-data に `prompt_sets/**` を入れ、`importlib.resources` で読む（editable でない install でも動く）

## 6. 記録とキャッシュ

2 つは別の物:

| | キャッシュ（L0） | 記録（judge） |
|---|---|---|
| 何 | 生応答。鍵 → payload | 1 判定の全体（入力・軸・回答行列（生応答つき）・集約・費用・版） |
| 目的 | 再開・費用ゼロの比較実験 | 監査（Q5）・再計算（Q7）・蒸留の素材（§8） |
| 形 | JSONL 追記のみ。`{"key","model","sample","version","payload":{"ok":true,"content","error":null,"eval_count","total_duration","prompt_eval_count"}}`（`prompt_eval_count` はライブラリで足す欄。空撃ちの行には無い。読むときは無くてよい） | JSONL 追記のみ。1 行 = `Judgment.to_record()` |
| 誰が読むか | `CachedPort` | `replay()`・蒸留（後段）・人 |

記録の形（`schema_version: 1`）:

```
{"schema_version":1, "at":<ISO8601>,
 "input":{"proposition","output","budget","thresholds","segmentation","units":[{"id","text"}]},
 "question_set":{...QuestionSet（source を含む）...},
 "matrices":[{"reader","calibration","prompt","samples","answers":[{...Answer（raw・key を含む）...}]}],
 "readings":{"<reader>":{...Reading（label と reason）...}},
 "summary":{...ReaderSummary...},
 "cost":{...}, "versions":{"library","prompts","units_rule","aggregation"},
 "notes":[...], "retry":{...RetryHint...} または null}
```

- 本文は**単位列として丸ごと**残す（ハッシュだけでは再計算も第三者検証もできない。上流 §8）
- 回答は `raw`（フェンスを剥がした応答本文）を持つ。根拠 id の正規化規則や `validate` を変えても、記録だけから L2 を引き直せる
- `replay(record, thresholds=…, output=…)` は `matrices` から `l3`・`l4` を引き直す。規則の版（`aggregation`）が変わっていれば記録の値と違ってよく、それが再計算の目的。`question_set` を固定して**別の本文や読み手で再判定する**のは `replay` ではなく `judge(question_set=…)`（LLM を呼ぶ）
- 測定の道具（`out4/*/results.json`）の形は**移さない**。あれは空撃ちの記録。適合検査（§9）が両者を突き合わせる

### 4.S 問いの保存庫（`store.py`・2026-09-23）

師匠の言葉（原文）: 「データをJSONで保持して同じ命題と本文の場合は過去に作成した問を再利用できる仕組みにして。」
判断の理由は合意 `.pair-agent/agreements/question-store.md` の Q1〜Q9。

| 項目 | 決め |
|---|---|
| 置き方 | 1 本文 × 1 命題 ＝ 1 JSON ファイル `<鍵>.json`（`indent` 付き・UTF-8）。人が開いて読める・直せる |
| 鍵 | `sha1({v: KEY_VERSION, units_rule, proposition.strip(), 単位の本文の列})`。文の前後の空白・改行の違いは同じ本文。命題は前後の空白だけ除く。文の中の改行や空白、Unicode 正規化の違いは別の本文。生成器・軸数・指示の版は鍵に入れない。`KEY_VERSION`（鍵の作り方の版）と `SCHEMA_VERSION`（ファイルの形の版）は別 |
| 中身 | `schema_version`・`key`・`created_at`・`library`・`proposition`・`units_rule`・`units`（id と本文）・`question_set`（軸・有効な軸・交差検証の結果・生成器・指示の版・予算・試行） |
| 再利用 | 交差検証の結果ごと。外した軸も含めて同じ問いの集合で答えさせる。当たれば生成器が無くても判定できる |
| 条件の違い | 生成器・指示の集合と版・軸数（予算の範囲外）・交差検証の有無（保存された問いが未検証で、今回は求めている）・有効な軸 0 本を、`Judgment.notes`（記録と CLI の「# 注意:」）と WARNING のログに出す。違っても再利用する（合意 Q2） |
| 作り直し | `regenerate=True`（保存庫が要る。無ければ `InputError`）。古いファイルは `<鍵>.superseded-<時刻>[-n].json` に**写して**残す（物理削除しない。同じ時刻でも番号を足して衝突させない）。生成の標本番号の起点を「これまでの世代数 × 試行数」ずらすので、生応答のキャッシュ越しでも生成器を呼び直す。以後は新しい方を再利用 |
| 読めないとき | `StoreError` で止める。上書きしない。読み込み時に検めるのは形（版・鍵・命題と本文の一致・軸 id の重複・`active_ids` が軸にあること）だけ。人が直した問いの規則違反は検めない |
| 書き込み | 保存庫が無い・ファイルだったら、**生成の前に** `StoreError`（LLM の費用を払ってから保存に失敗しない）。鍵ごとのロックファイル（`<鍵>.lock`。`lock_wait` 秒待つ、`lock_stale` 秒より古いものは残骸として外す）で直列化し（ペア固有 Skill「書き込みはロックで直列化する」）、一時ファイル（`mkstemp`）→ 古いものを写す → `os.replace` の順。今のファイルは置き換えの瞬間まで在り続ける |
| 記録 | 判定の記録の `question_set` に `source` と `store_key`、`notes` に条件の違い。`question_set=` で渡した問いは `store_key=None` |

L0 のキャッシュとの違い: キャッシュは「同じ指示・同じモデル」の生応答を引く。生成器・軸数・指示の版のどれかが変われば外れ、交差検証も呼び直す。
保存庫は本文と命題だけで引き、問いの集合をそのまま返す。

## 7. 実行モデル

- **非同期が本体**（`async def judge`。Skill「LLM 呼び出しは非同期」）。同期の入口は `judge_sync`。ストリーミングは使わない（構造化出力を丸ごと検証するので流す意味が無い。⚠ I2）
- 並列は `budget.workers` の `Semaphore` 1 つ。既定 1（ローカルの読み手に安全側）。クラウドの読み手は 4〜8 で回した実績
- 読み手は順に、記述は並列（⚠ I11）。交差検証の質問も同じ `Semaphore`
- 時間制限は読み手のアダプタが持つ（`OllamaReader.timeout`）。`judge` 自体は持たない（`asyncio` のキャンセルで止める）
- スレッド: `OllamaReader` が `to_thread` で `urllib` を呼ぶ。`CachedPort` の書き込みはイベントループのスレッドで同期に 1 行ずつ（await を挟まないので同一ループ内の排他は要らない。合流して待つ側は書く前に放す）。**別プロセスからの同一キャッシュへの同時追記は守らない**（空撃ちと同じ。1 行 1 `write` の追記なので壊れにくいが保証はしない）
- ログ: `logging.getLogger("structural_distillation.<層>")`。既定でハンドラは付けない（ライブラリの作法）

## 8. 失敗の型（上流 §4.4 の写像）

| # | 失敗 | 実装 | どこで |
|---|---|---|---|
| F1 | 本文が文脈に入らない | `len(text) > budget.max_chars` → `InputError`。LLM を 1 回も呼ばない | `judge` 入口 |
| F2 | 本文・命題が空、K < 2、`labels` / `bounds` 不正、`axes_min > axes_max`、頼む軸数が下限と上限の間に無い、標本数・並列数 < 1、再試行 < 0、読み手 0、読み手名の重複、検証役名の重複、生成器・検証役が偽読み手、単位化規則が未知、閾値の値域外（ι・κ・ρ・ω ∉ (0, 1]、δ < 0。`replay` でも）、渡された問いの集合の軸 id・`active_ids` の重複や不在 | `InputError` | `judge` 入口・`replay` |
| F3 | 生成が規則を満たせない | `PlanningFailed(attempts)`。試行ごとの違反規則を持つ | `l1.plan` |
| F4 | 定まった軸が 0（s + r = 0） | `p=w=None`、札は `NO_EVIDENCE`（無効率・矛盾率が閾値を超えていれば `INSTRUMENT_FAULT`）、値なし | `l3`・`l4` |
| F4′ | 判定に使える軸そのものが 0 本（交差検証で全軸が外れた・渡された問いの集合が空） | 札 `INSTRUMENT_FAULT`・理由 `NO_ACTIVE_AXES`・値なし・率は `None`。`Judgment.retry` に作り直しの材料（師匠決定 2026-09-23） | `l3`・`compose` |
| F5 | 値を持つ実読み手が 1 体以下 | `delta=None`、`note="single reader"`（0 体なら `"no reader with a value"`） | `l3.summarize_readers` |
| F6 | 読み手ポートが再送後もスキーマに合わない | その回答は `valid=False`。判定は続行。`invalid_rate ≥ ι` で `INSTRUMENT_FAULT` | `l0`・`l2`・`l3` |
| F7 | 複合文の命題 | 検出しない | — |
| — | 交差検証の検証役が 2 体未満 | 交差検証なし（`crosscheck=None`）。`logging` | `l1` |
| — | 生成器の L0 失敗 | 再送せず次の試行（`L0 失敗` の試行として記録） | `l1` |

## 9. テスト（層ごと・純関数を CI に、実接続は別）

| ファイル | 何を固定するか | LLM |
|---|---|---|
| `test_l0.py` | `strip_fence`・`validate`（部分集合の各キーワード）・`with_schema` の付加文がバイト単位で空撃ちと同じ・`cache_key` が out4 のキャッシュ行（`tests/fixtures/` に p3 1 行・p2 2 行・xc2 3 行を抜く）の `key` と一致・`structured` の三段構え（散文・フェンス付き・欠損・型外 → 型外が値に化けない: **S2**。`retry` の有無で呼び出し数が変わる）・`CachedPort` の命中／追記／失敗は書かない／cache-only／read_only／extra／同時要求の合流・`FakeReader` 4 種（`random` が並列でも同じ） | 台本の読み手（`ScriptedReader`: 呼び出し順に決めた content を返す。`name`・`calibration`・`complete` の口は本物と同じ） |
| `test_l0_live.py` | `OllamaReader` で 1 回、スキーマ準拠の JSON が返る。`format=` が効いているかをログで観測 | **実接続。** `@pytest.mark.integration` を付け、`SD_LIVE=1` と `SD_LIVE_MODEL` が無ければ skip（理由つき） |
| `test_units.py` | 3 規則の割り方・id・`render`・空本文 | なし |
| `test_l1.py` | `check_harness` の各規則を 1 件ずつ違反させて拾う（緑が偽物でないことを変異で確かめる）・試行と `PlanningFailed`・L0 失敗は次の試行・id と `origin`・交差検証の票の集約（過半数・並り 2 版・同数・weak・nonexclusive は有効票の過半数）・検証役 1 体なら未検証 | 台本 |
| `test_l2.py` | id の正規化（`[s5]`・空白・鉤括弧）・実在照合・`SILENT` は根拠不要・L0 失敗 → 無効・`evidence` 欠落 → 無効・並列でも順序が固定・`raw` と `key` が残る | 台本 |
| `test_l3.py` | `direction_sample` 全組合せ（無効を含む）・多数決と同数・`zero_kind` の優先順位・`Counts`・p / w・率の定義・札の順序・**S4**（全軸の側を入れ替えると p' = 1 − p、w' = w）・**S12**（軸を 2 倍に複製しても w 不変）・`summarize_readers`（Δ・単読み手・偽読み手と値なしの除外）・**S0a 適合**: `out4/{base21,s4,mono,meta2,m3arm,mem,rashomon,mono_rashomon}/results.json` の**主走行と反事実の全行**を `AnswerMatrix` に起こして `aggregate` → `p`・`w`・`label`・軸ごとの `d`・`d_samples`・`agree`・`contradiction`・`invalid`・`silent`・`p_by_sample`・`invalid_rate`・`silent_rate`・`contradiction_rate` が記録と全一致（記録ディレクトリが無ければ skip。偽読み手名は空撃ちと同じなので読み替え不要） | なし |
| `test_l4.py` | `Probability`・`Ordinal` の等分と `bounds`・端点（0, 1）・`labels`・値なしの札 → `None`。型外に出る入力が作れない（**S2**） | なし |
| `test_judge.py` | 入口の検査（F1・F2・重複名・偽の生成器）・偽読み手 2 体で端から端まで（**S11**: 全問 Yes → 計器不良、全問 触れていない → 根拠なし）・`question_set` を渡すと L1 を飛ばす・**S8** 呼び出し数（台本の読み手 2・軸 6・標本 1・交差検証なし → `plan.live 1`・`answer.live 24`、2 回目は `live 0`・`cached 25`。偽読み手は `calibration_calls`）・記録 → `replay` で同じ `Reading`・`judge_sync`・**Q6** コアの import が標準ライブラリだけ（AST 走査）・閾値の値域・検証役名と軸 id の重複・全軸が外れたときの札と注記・import の順に関わらず `judge` が関数（別プロセスで） | 台本・偽読み手 |
| `test_store.py` | 鍵（空白の違いは同じ・命題 / 本文 / 単位化規則の違いは別）・2 回目は生成器を呼ばず同じ軸と有効な軸・交差検証の結果ごと再利用（検証役を呼ばない）・生成器が無くても再利用できる・作り直し（古いものを残す・同じ時刻で 2 回でも失わない・キャッシュ越しでも生成器を呼び直す・生成が失敗したら今のものを保つ）・条件の違いの通知（notes と WARNING）・渡した問いは保存庫に触れず鍵も持たない・`regenerate` だけでは InputError・置き場所がファイルなら生成の前に StoreError・有効な軸 0 本の通知・ロック（他人のロックは待って StoreError、古いロックは外す）・記録に source と鍵・読める JSON・人が直した問いを使う・壊れたファイル 9 通りは上書きせず StoreError・一覧と鍵の解決 | 台本 |
| `test_tools.py` | 本文の読み込み（BOM を落とす・無いファイルは一言で止まる）・`questions_cli` の list / show / 無い保存庫 / 当たらない鍵 | 台本 |
| `tools/conformance_out4.py`（テストではなく道具） | ① `out4/base21` のキャッシュだけ（`cache_only=True, read_only=True`）で `judge()` を 21 題材（`materials.json` ＋ `materials2.json`）に回し、**live 0 件**かつ `p`・`w`・札・軸ごとの `d` が `results.json` と一致。生成器 `gemma4:31b-cloud`・読み手 `qwen3.5:397b-cloud` / `glm-5.2:cloud`・標本 2・軸 6・`num_ctx` 8192・**交差検証なし**（`run.log` の開始行）。指示の逐語移植と鍵の同一性の検査 ② `extra=[out4/xc2/llm_cache.jsonl]` を足し **交差検証あり**（検証役 ＝ 同じ 2 体）で回し、`flagged`・`weak`・`nonexclusive` が `out4/xc2/crosscheck.json` と一致（`orient` / `exclusive` の逐語性）。⚠ この 21 題材は強い生成器の軸で、外される軸は 0（受入 M1）③ **軸を外す経路**: 弱い生成器（p3・gemma4:12b）の軸 11 題材を `l1.crosscheck` に xc2 のキャッシュで通し、外した 5 軸（4 題材）を含めて `crosscheck.json` と一致。さらに m3arm のキャッシュ（クラウドの読み手 3 体・標本 2）で外す前と外した後の p・w が一致。cache_only なので実呼び出しは構造的に 0。意味のある指標は**外れ 0**。`--negative-control` で回答の版を変えると全部落ちる。**`.pair-agent/probes/` には一切書かない**（道具の書き込み先は `_cli.guard_write_path` が拒む） | なし（キャッシュ） |

方針:

- **TDD**。層ごとにテストを先に書き、緑にしてから次の層へ
- 純関数のテストは実応答の固定データ（out4 から抜いた行。`tests/fixtures/`）を持つ
- 実接続は `@pytest.mark.integration`（Skill `ai-dev-basics` の規約）＋ `SD_LIVE=1` のときだけ。既定の `pytest` は LLM を呼ばない。GPU を使うローカルモデルは既定にしない（師匠と共有）
- テストも道具も `.pair-agent/probes/` 配下に書き込まない（記録は git 管理下の凍結物）

## 10. 実装の順序と完成の定義

内側（最も基盤）から外へ、**一層ずつ完成させてから次へ**。各層の完成 ＝ ①テスト緑 ②触れる CLI が動く ③この文書の該当節と実装が一致。
**v1 は A〜D をこの順に 1 スプリントで済ませた**（師匠「実装を開始してください」。層ごとの完成の記録は `.pair-agent/agreements/implementation-v1.md`）。

| 切片 | 層（この順に 1 層ずつ） | 触れる CLI | 完成の検査 |
|---|---|---|---|
| A | `pyproject` ＋ `contracts` ＋ `prompts` → **L0** → **単位化** | `l0_chat.py`（モデル名・指示・スキーマを与えて構造化応答を見る。キャッシュの命中を表示）／`units_chat.py`（本文 → 単位列） | `test_l0`・`test_units`・実接続 1 回（クラウド） |
| B | **L1** | `l1_chat.py`（本文ファイル・命題 → 軸の対。違反と試行、交差検証の票を表示）| `test_l1`・実接続で題材 1 本 |
| C | **L2** → **L3** → **L4**（各層の CLI を層の完成ごとに出す） | `l2_chat.py`（本文・記述 → 3 値と根拠）／`l3_chat.py`（記録の回答行列 → p・w・札・診断値）／`l4_chat.py`（p・型 → 値） | `test_l2`・`test_l3`（S0a 適合・S4・S12）・`test_l4` |
| D | **合成** ＋ 記録 ＋ `replay` ＋ 費用 | `judge_cli.py`・`conformance_out4.py` | `test_judge`・適合検査 ①② で live 0 件・一致・README「決まったこと」の更新 |

- 切片 1 つ ＝ スプリント 1 つを推奨（各層の CLI を師匠が触る人間ゲートを切片の終わりに置く）。1 スプリントに畳むなら層ごとのゲートは残す
- 人間ゲートで問うのは**仕組みの決定**（次の切片に進むか・何を直すか）だけ。出力の値への感覚は問わない（規則）
- 外側の層（次の切片）は README と本文書のスケッチに留め、担当の順番が来てから確定する
- venv は `.venv`（`.gitignore` 済み）。`pip install -e .` で開発

## 11. 実装判断（⚠ AI 判断。事後確認対象。異議があれば直す）

| # | 判断 | 理由 | 覆すと |
|---|---|---|---|
| I1 | コアは標準ライブラリだけ。Ollama アダプタも `urllib`（`to_thread`）で書く。LLMProviderlib のアダプタは v1 に入れない | 師匠の規則「基本ロジックの設計中は他のプロジェクトを読まない」。LLMProviderlib は師匠の別プロジェクトで、読まずにアダプタは書けない。空撃ちは標準ライブラリだけで回った | 利用側が `Reader` を実装すればどの接続でも差せる。師匠が許せばアダプタを別ファイルで足す（コアは変わらない） |
| I2 | 非同期を本体にし、同期は `judge_sync`。ストリーミングは使わない | Skill「LLM 呼び出しは非同期」。構造化出力は丸ごと検証するので流す意味が無い | 同期本体にすると並列は `ThreadPool` になる（空撃ちの形）。動くが Skill に反する |
| I3 | キャッシュの鍵と行の形は空撃ちと同一 | 測定 2 周目のキャッシュ（out4 で 15,614 行）が鍵ごと使え、適合検査が LLM なしで成立する | 鍵を変えると適合検査は「結果の一致」だけになり、指示の逐語性が検査できない |
| I4 | 率の定義（無効率は回答ベース、矛盾率は標本割合の平均）と無効の向きの規則は空撃ちと同一 | 閾値 κ 0.667 はその定義で置かれ、偽読み手 3 種で当てて確かめた | 上流 §7.4 の軸ベースに揃えるなら ι・κ を引き直す（K13）。`Counts.u2` は軸ベースで別に返すので材料は残る |
| I5 | 3 値はコードでは英語の列挙、LLM に見せる語は指示の集合 | 汎用性（Q6）。言語を変えても集約の式は変わらない | 日本語をコードに埋めると指示の集合を差し替えても語が残る |
| I6 | 交差検証で外した軸には答えさせない | 費用（Q8）。1 記述 1 呼び出しなので結果は同じ | 答えさせて記録に残す形もある（監査には有利、費用は増える） |
| I7 | 軸数の既定は「ちょうど」（`axes_min = axes_max = axes`） | 空撃ちと同じ検査。適合検査で同じ試行になる | 幅を持たせると生成器の再試行が減る（費用減）。利用側が `axes_min/max` で緩められる |
| I8 | 単位化の既定は `ja-sentence`。ファイル名は `units.py` | 測定はすべてこの規則。上流で番号が無い層 | 既定を `paragraph` にすると英文にも通るが、測った規則と違う |
| I9 | 順序尺度の既定は K 等分 | 上流 J5（確定済み）。「段 3 とは何か」は利用側 | `bounds` で利用側が切れ目を渡せる |
| I10 | 代表値は実読み手（値あり）の p の平均 | 上流 U3（確定済み・仮置き） | 中央値・最小などに変えるなら `summarize_readers` の 1 箇所 |
| I11 | 読み手は順に、記述は並列 | ローカルの読み手 2 体を同時に走らせると GPU を取り合う（叱責記録 2026-09-22。一般化は保留の記録だが安全側に置く） | クラウドだけなら読み手も並列にできる。`Budget` に旗を足せば済む |
| I12 | 記録に `schema_version` と `Axis.kind`・`QuestionSet.source` を最初から置く | 上流 §13（検出項目・同義対・ブループリント）と問いの固定を同じ記録の形に載せるため | 無いと §13 で記録の形が変わり、蒸留の素材が割れる |
| I13 | `pyproject.toml`（setuptools）、`requires-python >= 3.11`、配布名 `structural-distillation`、`__version__ = "0.1.0"`、package-data に `prompt_sets/**` | 依存ゼロ。3.11 以上は `asyncio.TaskGroup`・`Self` のため。手元は 3.13 | — |
| I14 | `validate` は JSON Schema の部分集合。`additionalProperties` は使わない | 依存を足さずに済む。使うスキーマは 4 つで、キーワードは限られる。スキーマに語を足すと鍵が変わる | 利用側が凝ったスキーマを渡す口は無い（渡す口自体が無い） |
| I15 | 指示ごとの digest を `set.json` に持ち、違えば警告 | 版を上げ忘れた編集がキャッシュに黙って混ざるのを防ぐ | エラーにすると編集のたびに digest 更新が要る。警告に留める |
| I16 | `max_chars` の既定 12,000 | `num_ctx` 8192 で日本語の本文を記述ごとに送る空撃ちの経験則。羅生門（約 6,000 字）は 16,384 で回した。値は仮置きで、`prompt_eval_count` の実測で置き換える | 読み手のアダプタが文脈長を申告する口を足せば自動化できる |
| I17 | CLI は `tools/` に置き、パッケージに入れない。標準ライブラリだけ。入出力は UTF-8 に固定 | 道具は用途で変わる。Windows のコンソールで落ちない | — |
| I18 | 再送（`:retry`）は回答だけ。生成は次の試行へ、交差検証は再送なし | 空撃ちと同一（移植規則）。上流 F6 も回答の文脈 | 全役割で再送すると費用の式（S8）が変わり、鍵に `:retry` 行が増える |
| I19 | キャッシュには成功した応答だけ書く | 失敗を永続化すると再走でも直らない（Skill「生応答キャッシュ」の put は成功だけ）。out4 に失敗行は 0 なので適合検査には効かない | 空撃ちどおり失敗も書くと、一時的な HTTP 失敗が固定される |
| I20 | 指示の集合は 1 ファイルの JSON | Markdown だと見出し間の空白の扱いで逐語にならず、編集ソフトが末尾改行を丸める | 人が読み書きしにくい。CLI（`l0_chat`）で描画結果を見られるようにして補う |
| I21 | `validate` は空撃ちより厳しい（`evidence` の欠落・非文字列を受理しない） | 型の閉包（Q2）。out4 の p2 行は全部 `evidence` を持ち、非文字列は 0 件 | 受理すると散文に近い応答が値に化ける経路が残る |

## 12. 解かないこと（v1 の実装）

- 他の接続（LLMProviderlib・OpenAI 互換・Anthropic）のアダプタ。利用側が `Reader` を実装する
- 本文の分割・要約（F1 は拒否）
- ラベル集合の型・複合文の検出（上流 §10）
- 軸の**個別補充**（不足した側だけ足す）。v1 は生成全体の再試行だけ。`Diagnostics.retries` がその回数。上流 §7.5「追加生成された問いの判定不能率」は該当なし
- 全問 1 呼び出しの粒度（上流 J13 の「差は実験で測る」は未着手。`Budget` に粒度の欄は置かない）
- 検出項目・同義対・ブループリント・分散成分（上流 §13）。記録の形に余地（`Axis.kind`）だけ残す
- 日本語依存の定数（H3 の 0.6・正規化の文字集合・H11 の終端・単位化の `。！？` と段落の全角空白・根拠 id の正規化の鉤括弧「」）の言語パック化。v1 はコードに置く。言語を変えるとここも変わる
- 違反の文言と `Answer.error` は日本語の文のまま記録に入る。機械で読むときは先頭の規則コード（`H1`〜`H11`・`L0`）だけを見る
- 別プロセス間のキャッシュ排他
- 蒸留（L5）

## 13. 空撃ちとの差分表（移植で変えたところ。すべてここに書く）

| # | 何を | 空撃ち | ライブラリ | 種別 | 理由 |
|---|---|---|---|---|---|
| P1 | 生成の上限到達 | 違反付きの最後の軸で続行（観察のため） | `PlanningFailed` で拒否 | 置けない（上流 F3 が拒否と定める） | 生成器の失敗を値に化けさせない |
| P2 | キャッシュへの失敗の書き込み | 失敗も書く | 成功だけ書く | 意図的（I19） | 一時的な失敗を固定しない |
| P3 | 回答の `validate` の厳しさ | `evidence` 欠落を `[]`、非文字列を無視して**再送せず**受理 | スキーマ違反として**再送 1 回**、それでも合わなければ無効（呼び出し数が増えることも、再送で有効に変わることもある） | 意図的（I21） | 型の閉包。out4 に該当行 0 |
| P4 | Δ・代表値に使う p | 札に関係なく p | 値を持つ実読み手だけ | 意図的 | 合意 K12 の訂正と同じ判断。値なしの p は出力契約に無い |
| P5 | 偽読み手の経路 | `answer()` で短絡。Port を通らず数えない | `Reader` の口で差す。`calibration_calls` に別枠で数える | 置けない（上流 S11「同じ口に差せる」） | 較正用の読み手も同じ契約で扱う |
| P6 | 交差検証の時点 | 全軸に答えてから外す（別スクリプト） | 外してから答える | 意図的（I6） | 費用。1 記述 1 呼び出しなので、残った軸の回答は同じ（適合検査 ③ で確認） |
| P7 | 違反の表記 | `H7 空の記述` / `H7 両側が同一` / `H7 形式` | `H1 空の記述` / `H1 両側が同一` / `L0 失敗` | 意図的 | 規則の名前を上流 §6 に合わせる。検査の中身は同じ |
| P8 | `random` の偽読み手 | 無い | `sha1(seed, messages, sample)` で決める | 追加（上流 S11） | 並列でも再現する |
| P9 | `Counts` の u の分割 | `u = n − s − r` | `u1..u4` に分ける（`zero_kind`） | 追加（上流 §7.1） | d・p・w・札は変わらない |
| P10 | `valid_rate_by_side` | p3 には無い（p2 の観点別の名残） | 対の形で定義（§4.3） | 追加（上流 §7.5） | 片側性の診断値 |
| P11 | 記録 | `results.json`（道具の形） | `Judgment.to_record()`（生応答つき） | 置けない（上流 §8） | 監査・再計算・蒸留の素材 |
| P12 | キャッシュ行の `payload` | `eval_count`・`total_duration` | ＋ `prompt_eval_count` | 追加 | 文脈長の切り詰めを事後に見る。読むときは無くてよい |

| P13 | 偽読み手を生成器・検証役に | 経路が無い（偽読み手は answer で短絡） | `InputError` で拒む | 追加 | 同じ口に差せるようにした（P5）ので、答える以外の役に紛れ込む経路ができた。それを塞ぐ |

| P14 | 生成・向き・排他性の応答の検査 | 生成はスキーマを見ない（キーが欠けると `check_plan` が KeyError で落ちる）。交差検証は enum の外の語もそのまま票にし、多数決・同数・排他性の分母に入れる | どれも `validate` を通す。生成の違反は「L0 失敗」として次の試行へ。enum の外の票は `None` として落とす | 意図的（三段構え） | 型の閉包。out4 の xc2 に enum の外の応答は 0（向き 1,536・排他性 384 がすべて enum の中） |
| P15 | 交差検証の後の集約の母数 | `recount` は外す前の無効率をそのまま使った。軸 0 本で `probe3.aggregate` は落ちる | 残った軸だけで率を出す。軸 0 本なら率は `None`・札は NO_EVIDENCE・注記（F4′） | 置けない（上流 F4 が縮退を求める）＋意図的 | 外した軸に無効な回答があると、外した後の札が空撃ちと変わりうる（③ は p・w を比べ、札は比べない） |
| P16 | 閾値の値域 | 検査しない（ρ = 0 などは札の規則を壊す） | `InputError`。s + r = 0 は ρ に関わらず NO_EVIDENCE | 追加（上流 F4） | 受入 M3 |
| P17 | フェンス剥がし | アダプタ（Port）で 1 回 | アダプタと `structured` で 2 回。入れ子のフェンスは空撃ちでは解析に失敗し、ライブラリは通る | 意図的 | 利用側の `Reader` が剥がさなくても通るように。out4 に ``` で始まる応答は 0 |
| P18 | 失敗の行のあるキャッシュ | 当たりとして返す | 読み込み時に読み飛ばす（ログに残す） | 意図的（I19） | 空撃ちのキャッシュ（`out/`）に失敗の行が 1 つある |

変えていないこと（適合検査が守る）: 指示の文面・`notice`・スキーマの並び・鍵の式・行の形・単位化・ハーネスの検査・向きの規則（無効の扱いを含む）・多数決・率の定義（有効な軸が 1 本以上のとき）・札の順序（ρ > 0 のとき）・K 等分の式・交差検証の票の集約（enum の中の票に限る）。

## 14. 実装で分かったこと（v3・実装 v1）

| # | 何が分かったか | どう直したか |
|---|---|---|
| R1 | スキーマは enum の語が指示の集合から来るので、l1 / l2 に置くと L0 の鍵のテストが上の層に依存する | 4 つのスキーマの持ち主を `prompts.py` に（§4.1） |
| R2 | 層を 1 つずつ作ってテストするには、`__init__` が全層を import すると最初の層のテストが走らない | 公開名を遅延読み込み（§2） |
| R3 | 設計 v2 の「矛盾率が [0.5, 0.667) の行は無い」は誤り（mono の m17 に 1 行） | S0a は走行時の閾値で比べる（§4.3） |
| R4 | 変異試験 23 通りで 2 つ生き残った: H3 の閾値（テストが完全一致の言い換えしか見ていなかった）／judge が外した軸も集約する（外れる軸がある judge のテストが無かった） | テストを 2 本足して 0 に（`tools/mutation_check.py`） |
| R5 | 偽読み手を同じ口に差せるようにした結果、生成器・検証役に紛れ込む経路ができた | 入口で拒む（差分表 P13） |
| R6 | `python -m` はカレントディレクトリを `sys.path` の先頭に置くので、変異の写しを `PYTHONPATH` で読ませるには cwd を写しの側にする必要がある | 変異試験の道具に書いた |
| R7 | 受入 M4: 合成を `judge.py` に置くと、サブモジュールの読み込みがパッケージの属性 `judge` をモジュールで上書きし、`from … import judge_sync, judge` で `judge` がモジュールに化けた | `compose.py` に改名。別プロセスで import の順を変えて確かめるテスト |
| R8 | 受入 M3: ρ = 0 で `label` が AssertionError（s + r = 0 が ρ を素通りした） | s + r = 0 は ρ に関わらず NO_EVIDENCE。閾値の値域を入口と replay で検査 |
| R9 | 受入 M1: 適合検査 ② の 21 題材は外される軸が 0 で、外す経路を実データで通していなかった | ③ を足した（弱い生成器の軸で 5 軸を外す）。陰性対照で、値が未定義どうしの一致を外れの検出で落とすよう直した |
| R10 | 受入 M2: 空撃ちとの差のうち 5 つが差分表に無かった（生成・交差検証の検査、回答の再送、外した後の母数、フェンスの 2 回剥がし、失敗の行） | 差分表 P14〜P18。P3・P6 の文言を直した |
| R11 | 受入 M5: 交差検証で全軸が外れると、札が「本文に根拠が無い」で率が 0.0 に見えた | 率を `None` に、`Reading.note` で原因を区別（F4′）。札をどうするかは師匠に確認中 |
| R12 | 受入 m1〜m4・m7・m13: 失敗の行が当たりになる／合流した側が追記の失敗に巻き込まれる／生応答と鍵が別の試行の組になる／入口の検査の漏れ／指示の集合の検査が薄い／道具が測定の記録の下に書ける | それぞれ直してテストか変異で固定（変異試験 28 通り） |
| R13 | 師匠の依頼（2026-09-23）: 同じ命題と本文なら問いを再利用したい | 問いの保存庫（§4.S）。実接続で、2 回目は生成器を替えキャッシュも無しで回し、生成 0・交差検証 0・回答 24 で同じ問いの集合を使った（92 秒 → 18 秒） |
| R14 | 保存庫の受入（critical 1・major 4・minor 14）: 作り直しを同じ時刻に 2 回すると前に退けた問いを失う／キャッシュ越しの作り直しが前と同じ問いを返す／条件の違いが見えない／交差検証していない問いを黙って使う／排他なし（ペア固有 Skill に反する）ほか | 退ける名前に番号・標本の起点をずらす・`Judgment.notes`・ロックファイル・生成の前の置き場所の確認ほか（§4.S）。実接続で、キャッシュ越しの作り直しが生成器を呼び直し、別の生成器・別の軸数での再利用が「# 注意:」に出ることを確かめた |
| R15 | `CachedPort` に `__len__` があり、空のキャッシュが偽になって `planner or 既定` で黙って別の読み手に差し替わった（テストで踏んだ） | `__len__` をやめて `size` に |
| R16 | 師匠決定（2026-09-23・受入 M5 の決着）: 軸 0 本は「計器不良」＋理由＋上位が作り直せる材料 | `Reason`（5 種）・`RetryHint`・`Judgment.retry`・`label()` が理由も返す・CLI が理由と打てるコマンドを出して終了コード 2。自動では作り直さない（上流 J20） |

検査の結果（2026-09-22）:

| 検査 | 結果 |
|---|---|
| `pytest`（既定・LLM を呼ばない） | 193 緑・実接続 1 件は理由つきで skip（2026-09-23） |
| 鍵の一致（`test_l0`） | out4 の実キャッシュ行 6 件（生成・回答・向き・排他性）と一致 |
| S0a（`test_l3`） | 測定 2 周目の 8 腕・主走行と反事実の全 465 行が全一致 |
| 適合検査 ①（交差検証なし） | 21/21 題材・実呼び出し 0・外れ 0・キャッシュ命中 1,941 |
| 適合検査 ②（交差検証あり・外れる軸 0） | 21/21 題材・外れ 0・キャッシュ命中 2,289 |
| 適合検査 ③（軸を外す経路） | 11/11 題材・外した 5 軸（4 題材）・外す前と後の p・w が 3 体とも一致・外れ 0・キャッシュ命中 2,184 |
| 陰性対照（回答の版を変える） | ① 0/21・② 0/21・③ 0/11（外れ 3,840・2,016・3,048）。検査が落ちうることの確認 |
| 変異試験 | 41 通りすべてテストが落ちる（保存庫の 13 通りを含む。2026-09-23） |
| 実接続（クラウド・新しい本文 2 本） | 物語「佐藤は不誠実である」→ 2 体とも p 0.25・偏り（反証）・段 2。障害報告「再現に必要な情報が揃っている」→ 2 体とも p 0.83・偏り（支持）。どちらも 86 呼び出し・約 65 秒 |

## 付録 A. 上流設計の項目 → この文書の節

| 上流 | 項目 | ここ |
|---|---|---|
| §4.1 | 本文・命題 | §4.5 引数 `text`・`proposition`（上流 §4.3 の `material` は `text` に改名） |
| §4.1 | 型（真偽確率／順序尺度／ラベル集合） | §3.1 `OutputType`。ラベル集合は §12（対象外） |
| §4.1 | 読み手・生成器 | §4.5 `readers`・`planner`（既定は実読み手の先頭） |
| §4.1 | 予算（軸数の下限と上限・標本数・再試行上限・呼び出し粒度） | §3.1 `Budget`。粒度は §12（1 記述 1 呼び出しに固定。欄は置かない） |
| §4.1 | 判定の閾値 | §3.1 `Thresholds` |
| §4.2 | 読み手ごと（度合い・幅・札・値・診断値） | §3.3 `Reading`・`Diagnostics` |
| §4.2 | 要約（読み手間の差・代表値・監査記録・費用） | §3.3 `ReaderSummary`・`Judgment.to_record`・`Cost` |
| §4.3 | `judge()` 1 本 | §4.5（＋ `judge_sync`・`replay`。増やした理由: 同期の利用側と記録からの再計算） |
| §4.4 | F1〜F7 | §8 |
| §5 | L0 / 単位化 / L1 / L2 / L3 / L4 | §4.0 / §4.U / §4.1 / §4.2 / §4.3 / §4.4 |
| §5.1 | 問い（id・軸・支持側・反証側・出自） | §3.2 `Axis`（＋ `kind`） |
| §5.2 | 回答（答え・根拠・有効） | §3.2 `Answer`・§4.2 |
| §5.3 | 方向（矛盾・沈黙・無効・標本の多数決・読み手をまたがない） | §4.3（無効の扱いは上流 v3.5 脚注） |
| §6 H1 | 各軸が両側を持つ | §4.1 表 |
| §6 H2 | 観察可能 | 検査しない（§4.1・§12） |
| §6 H3 / H4 / H6 / H11 | 非自明／非重複／予算／平叙文 | §4.1 表 |
| §6 H5 | 三値 | §3.2 `Verdict`・§4.2 スキーマ |
| §6 H7 | 形式 | §4.0 `structured`（`validate`） |
| §6 H8 | 記録（生成器名・指示の版・予算・追加生成の回数） | §3.2 `QuestionSet`（`planner`・`prompt`・`budget`・`attempts`） |
| §6 H9 | 不在は No ではない | §3.2 `Verdict.SILENT` が独立の値 |
| §6 H10 | 根拠は単位 id | §4.2（正規化 → 実在照合） |
| §6 H12 | 向きの交差検証 | §4.1 交差検証・§3.2 `CrossCheck` |
| §7.1 | s・r・u₁〜u₄・n・観点ごと | §3.3 `Counts`・§4.3 `valid_rate_by_side` |
| §7.2 / §7.3 | p・w | §4.3 |
| §7.4 | 札・Δ | §4.3 `label`・`summarize_readers` |
| §7.5 | 診断値（有効率・判定不能率・無効率・一致率・追加生成・矛盾率・軸ごとの向き） | §3.3 `Diagnostics`・`AxisReading`。追加生成は §12（該当なし） |
| §7.6 | 型付け | §4.4 |
| §8 | 記録（単位列・命題・型・予算・閾値／問いの集合＋生成器＋版／回答行列＋読み手＋版＋生応答／集約＋版） | §6 記録の形 |
| §9 | S0a・S2・S4・S8・S11・S12 | §9 テスト。S0b・S1・S3・S5〜S7・S9・S10・S13・S14 は実装ではなく測定（`judge(question_set=…)` と偽読み手・記録で実行できる） |
| §11 | J1〜J19 | 実装で守る。J7（固定）は `question_set` 引数、J8 はキャッシュと `replay`、J9 は `prompt_sets`、J13 は 1 記述 1 呼び出し、J14 は読み手ごとの `Reading` |

## 変更履歴

- v3.4 [2026-09-23]: 師匠決定（上流 F4′・J20）: 軸 0 本は計器不良・理由つき・作り直しの材料つき（R16）。`Reason`・`RetryHint`・`Judgment.retry`・`Reading.reason`（`note` を統合）・`label()` の戻りが `(Label, Reason | None)`
- v3.3 [2026-09-23]: 保存庫の受入を反映（R14・R15）。退ける名前の衝突・キャッシュ越しの作り直し（`l1.plan(sample_base=)`）・`Judgment.notes`・ロックファイル・
  生成の前の置き場所の確認・`KEY_VERSION` と `SCHEMA_VERSION` の分離・§4.5 手順 1 の記述・§9 に `test_store` / `test_tools`・`CachedPort.size`
- v3.2 [2026-09-23]: 問いの保存庫（§4.S・`store.py`・`judge(question_store=, regenerate=)`・`QuestionSet.source="stored"` と `store_key`・`StoreError`・`questions_cli`）
- v3.1 [2026-09-22]: 受入（協議エンジン: critical 0・major 5・minor 16）を反映。合成を `compose.py` に改名（M4）、s + r = 0 と閾値の値域（M3）、
  適合検査 ③（M1）、差分表 P14〜P18（M2）、全軸が外れたときの率 `None` と注記（M5・F4′。札は師匠に確認中）、
  キャッシュの失敗の行・合流・生応答と鍵の組・入口の検査・指示の集合の検査・道具の書き込み先（m1〜m4・m7・m13）、記述の食い違いの訂正（m5・m6・m12）
- v3 [2026-09-22]: 実装 v1 の結果を書き戻した（§14）。スキーマの持ち主を `prompts.py` に、公開名の遅延読み込み、`structured` もフェンスを剥がす（冪等）、
  `CachedPort` のメモリだけの形、`Cost.missed`、`ReaderSummary.readers`、`Judgment` に予算・閾値・時刻、`replay(reparse=)`、
  `check_output` / `type_value`、偽読み手を生成器・検証役に使えない（P13）、S0a の κ の注記の訂正、道具 3 つ（`probe_records` / `mutation_check` / `_cli`）
- v2 [2026-09-22]: 協議エンジンの評価（critical 2・major 13・minor 18）を反映。**前提崩壊 1**（無効な回答の向きの規則が上流 §5.3 と空撃ちで違った → 空撃ちに揃え、上流 v3.5 に脚注）。`Reader.complete` に `version`、鍵を同一にする要素の表、再送は回答だけ（I18）、`question_set` 引数、費用の数え方、`random` の決定論、記録に生応答、`contracts.py` / `prompt_sets/`、適合検査の読み取り専用と交差検証の第 2 走行、`zero_kind` の優先順位、`valid_rate_by_side` の定義、差分表 §13、付録 A、I18〜I21、`units_chat`、切片 C の層順
- v1 [2026-09-22]: 初版。上流設計 v3.4 と空撃ちの道具（p2 / p3 / p4 / xc2）を根拠に、配置・契約・各層・指示の外部化・記録・実行モデル・失敗・テスト・順序・実装判断 I1〜I17 を置いた
