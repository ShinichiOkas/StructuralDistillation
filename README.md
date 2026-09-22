# Structural Distillation

**汎用 LLM の「多角的な判定プロセス」を正解ラベルとして抽出し、
型安全で高速な判定関数へ蒸留するためのライブラリ。**

> **English**: Structural Distillation extracts the *structure* of a general-purpose LLM's
> judgment — not just its final answer, but the distribution over independent decision axes —
> and distills it into a small, typed classifier. The goal is a judgment function that is
> fast, cheap, and **cannot emit a value outside its declared type**.
> Japanese documentation below.

---

## ⚠ ステータス: v0.1.0（レイヤー 1・2 の実装。蒸留はまだ）

レイヤー 1（問いの生成）と 2（回答と集約）が `structural_distillation/` にあります。レイヤー 3（蒸留）はまだです。
閾値の既定値はすべて**仮説**で、題材が増えるたびに引き直します。
README が先にあったのは、**境界を最初に決めておくため**です（下記「設計上の約束」）。

---

## これは何か

LLM に「判断」をさせるとき、ふつうは**文章を生成させて**その中から答えを取り出します。
しかし分類・評価・スコアリングのような仕事では、**文章は途中の産物**であって目的ではありません。

Structural Distillation は、判断を次の形に変えます:

```
非構造テキスト  →  [型に閉じた判定器]  →  離散値 / 数値 / 真偽確率（＋確信度）
```

出力は**宣言した型の中にしか存在しません**。だから形式が崩れず、パースも要りません。

## 解こうとしている問題

| 問題 | 中身 |
|---|---|
| **生成税** | 判定しかしないのに、文章生成の計算コストを毎回払っている |
| **揺らぎ** | 形式崩れ・型外の値・モデルを変えると答えの向きまで変わる |
| **根拠のブラックボックス化** | 結論しか残らず、なぜそう判断したかを後から検証できない |

⚠ 3 つ目が、このライブラリが単なる高速化と違う点です。
**判断に至る「判定軸の分布」を残す**ので、蒸留後の小型モデルの出力も事後に検証できます。

## 仕組み — 3 つのレイヤー

### 1. プランニング（汎用 LLM）

判定したい命題を、**独立した複数の Yes/No 判定**へ分解します。
軸が重複しないこと（直交性）を設計目標に置きます。

### 2. エグゼキューション（分布の抽出）

分解した判定軸を走らせ、`[Yes, No, Yes, ...]` という**状態ベクトル**を得ます。
単一の結論ではなく**分布**を取ることが要点です。
矛盾するラベルの同時出現を検知して、データの純度（確信度）を定量化します。

### 3. 蒸留（特化モデルへの継承）

「分布 → 結論」のペアを学習データとして、小型モデルに**直線の写像**を学ばせます。
汎用 LLM が分布を経て到達した結論を、小型モデルが一撃で射出できるようにします。

⚠ **1 と 2 だけでも単体で有用です**（分布が取れれば検証も較正もできる）。
3 は学習基盤を必要とするので、段階を分けて進めます。

## 使いどころ

- 高頻度の分類（問い合わせの仕分け、重大度評価）
- 生成の**ガードレール**: 生成 LLM の出力直後に判定器を置き、型で弾く
- ⚠ **構造的フィードバック**: 不合格のとき「どの判定軸で落ちたか」を生成側へ返し、
  ピンポイントに直させる。単なる拒絶で終わらせない
- **高密度フィルタリング**: 判定が生成より圧倒的に安いことを使い、
  候補を多数生成して判定を回し切り、合格確率を最大化する

## 向いていないこと

- 文章の作成
- 複雑な多段階推論
- ⚠ **型の中で間違えること**は防げません。型外は出ませんが、**判断そのものの正しさは別問題**です。
  確信度が較正されている保証もないので、そこは測ってください

## 設計上の約束（境界）

このライブラリを汎用に保つための制約です。**実装より先にここを決めています。**

1. **依存は一方向。** 本ライブラリは利用側を import しません
2. **ドメイン知識は利用側が持つ。** 「何を判定したいか」（命題・判定軸の語彙・ラベル集合）は
   利用側が型として渡します。⚠ これが漏れた時点で汎用ライブラリではなくなります
3. **蒸留した重みは利用側のもの。** 仕組みは汎用でも、学習結果は用途ごとです
4. **テストは本ライブラリ単体で走ること。** 利用側を起動しないと測れなくなったら、
   それは癒着が始まった合図です

## 最初の利用者

個人用の記憶・対話システムが最初の利用者です（非公開）。
そこでの実測が、このライブラリを作る動機になりました:

- 自己モデルが **支持 22 件 / 反証 53 件**（1 : 2.4 で反証優位）を蓄積していたのに、
  注入されたのは**ラベル 1 行だけ**で、量も向きも渡っていなかった
- 結果、読み手の LLM を変えると答えが割れた ——
  一方は「どちらとも言えない」、もう一方は「**うまくいったほうが圧倒的に多い**」（**実測の逆**）
- ⚠ **空白は空白のまま残らず、受け手が自前の前提で埋めた**

判断を生成に委ねている限り、**受け手の性分の差がそのまま出力の差**になります。
判断を生成から剥がすこと —— それがこのライブラリの目的です。

## 使い方

依存は Python 3.11 以上の標準ライブラリだけです。LLM への接続は同梱の Ollama アダプタか、自前の読み手（`Reader` の口を実装したもの）で差します。

```python
from structural_distillation import judge_sync, Ordinal, OllamaReader, CachedPort, Budget

readers = [CachedPort(OllamaReader(m), "cache.jsonl") for m in ("qwen3.5:397b-cloud", "glm-5.2:cloud")]
j = judge_sync(本文, "老婆は悪人である", Ordinal(5), readers=readers,
               planner=CachedPort(OllamaReader("gemma4:31b-cloud"), "cache.jsonl"),
               budget=Budget(workers=6), record_path="records.jsonl")
for name, r in j.readings.items():
    print(name, r.value, r.label, r.p, r.w)      # 読み手ごとの値・札・度合い・幅
print(j.summary.delta, j.summary.representative)  # 読み手間の差と代表値
```

- 読み手ごとに値を返し、読み手間の差（Δ）を隠しません。札が「本文に根拠が無い」「計器不良」のときは値を返しません
- 記録（`record_path`）には本文の単位列・問いの集合・回答（生応答つき）・集約が残り、`replay()` で LLM を呼ばずに引き直せます
- 問いの保存庫（`question_store="questions"`）を渡すと、同じ命題と本文では過去に作った問いの集合を再利用します。
  生成器も交差検証も呼ばず、同じ問いで答えさせます。保存庫の中身は 1 本文 × 1 命題 ＝ 1 JSON で、開いて読めます
- 各層を単独で触る CLI が `tools/` にあります（`l0_chat.py`・`units_chat.py`・`l1_chat.py`・`l2_chat.py`・`l3_chat.py`・`l4_chat.py`・`judge_cli.py`）。
  試すための本文とそのまま打てるコマンドは [`examples/README.md`](examples/README.md) にあります

開発:

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m pytest                      # LLM を呼ばない
.venv/Scripts/python tools/conformance_out4.py      # 測定の記録と突き合わせる（LLM を呼ばない）
```

## 取り込み方（予定）

- 開発中は **editable install**（`pip install -e`）
- 利用側へは **git submodule** として取り込む
  ⚠ 本リポジトリを public にしてあるのはこのためです。private だと
  `git clone` だけでは submodule が埋まらず、空ディレクトリのまま気づけません

## 決まったこと（上流設計・2026-09-22）

上流設計は [`doc/UPSTREAM_DESIGN.md`](doc/UPSTREAM_DESIGN.md) にあります。要点:

- **判定軸の分解（レイヤー 1）は本ライブラリが LLM で生成します。** 生成の規則とハーネスも本ライブラリの範囲です
- 軸は「支持側の記述／反証側の記述」の**互いに排他な対**で、どちらを本文が述べているかで向きが決まります。
  ⚠ 支持と反証の記述を同数並べる形は、生成器が両側とも本文にある事実で埋めるため度合いが 0.5 に固定されました（空撃ちの実測）
- **判定に必要な全文を入力します。** LLM の知識に頼りません
- 出力は「度合い ＋ 幅」を読み手（モデル）ごとに返し、読み手間の差を隠しません。確からしさは数えられる診断値の組で、1 つの数には畳みません
- 生成器が付けた向きは、検証役に交差検証させ、過半数が反対した軸を外します（既定で有効）。
  既定の検証役は偽でない読み手全員で、生成器を含むこともあります（測定では生成器自身を検証役にしても検出数は同等でした）。
  検証役が 2 体未満なら交差検証はせず、ログに残します
- 実装言語は Python。ライセンスは MIT（下記）

実装設計は [`doc/IMPLEMENTATION_DESIGN.md`](doc/IMPLEMENTATION_DESIGN.md) にあります（2026-09-22）。要点:

- パッケージは `structural_distillation`。公開 API は `judge()`（非同期）・`judge_sync()`・`replay()`
- 層ごとに 1 ファイル（`l0.py` 読み手ポート／`units.py` 単位化／`l1.py` 問い生成／`l2.py` 回答／`l3.py` 集約／`l4.py` 型付け／`compose.py` 合成）
- ライブラリは設計を検めた空撃ちの道具の移植です。測定のキャッシュだけで `judge()` を回すと、21 題材の記録と一致します（キャッシュの外れ 0）。
  交差検証で軸を外す経路も、別の 11 題材の記録と一致します

設計を検めた空撃ち（創作の短文 21 本・クラウドの LLM 3 家系・偽読み手・反事実）の記録は `.pair-agent/probes/` にあります。

## ⚠ まだ決まっていないこと

- 蒸留（レイヤー 3）に使う学習基盤
- Ollama 以外の接続のアダプタ（利用側が `Reader` の口を実装すれば差せます）
- 閾値の既定値（すべて仮説。題材が増えるたびに引き直します）

## ライセンス

[MIT License](LICENSE). Copyright (c) 2026 ShinichiOkas.

自由に使ってください。許諾を求める必要はありません。

## 貢献について

⚠ **このリポジトリは個人用途を主目的としており、Issue / Pull Request は受け付けていません。**
方針・API・ライセンスは予告なく変わります。フォークはご自由にどうぞ。

> **Contributions**: This is a personal project. Issues and pull requests are not accepted.
> Direction, API and licensing may change without notice. Feel free to fork.

## 出典

概念は設計メモ [`doc/structural_distillation.md`](doc/structural_distillation.md)（2026-09-20）に基づきます。
上流設計は [`doc/UPSTREAM_DESIGN.md`](doc/UPSTREAM_DESIGN.md)、先行する実測は [`doc/LESSONS_FROM_THE_FIRST_CONSUMER.md`](doc/LESSONS_FROM_THE_FIRST_CONSUMER.md)。
⚠ 同じ内容がコピー元の側にも残っています（**二重管理**。書き換えるときは両方を見てください）。
