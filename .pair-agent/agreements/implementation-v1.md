---
sprint_id: 6ddf00eb-32f5-469d-b2e5-85acb583cfac
version: 1
status: executing
created_at: 2026-09-22T22:20:00+09:00
updated_at: 2026-09-22T22:20:00+09:00
domain_tags: [implementation, python-library, llm-judgment]
slice_size: M→L（コアの全層・触れる CLI・テスト・適合検査。受入は協議エンジン＝別コンテキスト）
change_count_premise: 0
change_count_improvement: 0
---

# スプリントゴール

実装設計 `doc/IMPLEMENTATION_DESIGN.md` v2 どおりにライブラリ v1（`structural_distillation`）を書き、
**測定 2 周目のキャッシュだけで `judge()` が記録を再現する**（適合検査）ところまで持っていく。

## 0. 師匠の言葉（原文・2026-09-22 22:18）

> 実装を開始してください

D1・D2 の読み（AI 判断・事後確認対象。`implementation-design.md` v3 に記録）: 標準ライブラリの Ollama アダプタで進め LLMProviderlib は読まない／実装は 1 スプリントに畳み、層ごとの完成検査をコミットで残し、CLI を触る人間ゲートは最後にまとめて提示する。

## タスク（内側から一層ずつ。各層の完成 ＝ テスト緑・触れる CLI・設計と一致。層ごとにコミット）

- [ ] A0 足場: `pyproject.toml`・`.venv`・`contracts.py`・`prompts.py` ＋ `prompt_sets/ja/set.json`（空撃ちの指示を逐語で）
- [ ] A1 L0: `l0.py`（三段構え・CachedPort・OllamaReader・FakeReader）＋ `test_l0.py`（鍵が out4 のキャッシュ行と一致）＋ `tools/l0_chat.py` ＋ 実接続 1 回（クラウド）
- [ ] A2 単位化: `units.py` ＋ `test_units.py` ＋ `tools/units_chat.py`
- [ ] B L1: `l1.py`（ハーネス検査・試行・交差検証）＋ `test_l1.py` ＋ `tools/l1_chat.py`
- [ ] C1 L2: `l2.py` ＋ `test_l2.py` ＋ `tools/l2_chat.py`
- [ ] C2 L3: `l3.py` ＋ `test_l3.py`（S0a 適合: out4 の 8 腕の全行・S4・S12）＋ `tools/l3_chat.py`
- [ ] C3 L4: `l4.py` ＋ `test_l4.py` ＋ `tools/l4_chat.py`
- [ ] D 合成: `judge.py`（judge / judge_sync / replay / 記録 / 費用）＋ `test_judge.py` ＋ `tools/judge_cli.py` ＋ `tools/conformance_out4.py`（① 交差検証なし ② 交差検証あり）
- [ ] E 実接続の端から端まで 1 題材（クラウドモデルだけ。GPU を使わない）
- [ ] F 設計 v3（実装で分かったことの書き戻し）・README「決まったこと／まだ決まっていないこと」
- [ ] G 受入: 協議エンジンに実装と設計を突き合わせさせ、成立した指摘を反映
- [ ] H 師匠に CLI を触ってもらう（人間ゲート）・振り返りを伺う（前 2 スプリント分と合わせて）

## スコープ

`structural_distillation/`・`tools/`・`tests/`・`pyproject.toml`・設計文書と README の書き戻し。
`.pair-agent/probes/` の道具と記録は**読むだけ**（凍結。テストも道具も書き込まない）。
LLM を呼ぶのはクラウドモデル（`-cloud`）だけ。ローカルモデル（GPU）は呼ばない。

## 完了条件

- `pytest`（既定・LLM を呼ばない）が全部緑。skip は理由つきで数を報告する
- 適合検査 ①: base21 のキャッシュだけで 21 題材の主走行と反事実を `judge()` に通し、**live 0 件**で `p`・`w`・札・軸ごとの `d` が記録と一致（札は走行時の閾値 κ=0.5 で比べる）
- 適合検査 ②: base21 ＋ xc2 のキャッシュで交差検証ありを回し、**live 0 件**で `flagged`・`weak`・`nonexclusive` が `crosscheck.json` と一致
- コアの import が標準ライブラリだけ（テストで AST 走査）
- 各層に触れる CLI があり、1 回ずつ動かした出力を記録した
- 実接続（クラウド）で 1 題材を端から端まで通した記録がある
- 受入で critical 0

## 前提

- 実装設計 v2・上流設計 v3.5 を正とする。実装で崩れたら「前提崩壊」として数え、設計を直す
- ライブラリは空撃ちの移植。差分は設計 §13 に全部載せる。載っていない差分が見つかったら、直すか表に足す

## 既知リスク

- 指示の逐語移植が 1 文字ずれると鍵が合わない → 鍵の一致テスト（A1）と適合検査（D）が検出する
- Ollama は師匠の常駐物（0.34.1）。止めない・再起動しない
- クラウドの 429 / 5xx → アダプタが待って再送（最大 4 試行）

## 実行中に採る判断（AI の判断。事後確認対象）

- 協議エンジンの評価は設計 v1 で済（critical 2・major 13 を反映して v2）。**実装合意は設計の写しなので評価を省き、受入（G）で実装と設計の突き合わせに回す**
- 設計の記述が実装で曖昧と分かったら、空撃ちの挙動に合わせて決め、設計 v3 と差分表に書く
- S0a の札の比較は走行時の閾値（κ=0.5）で行う（記録の札はその値で付いている。mono の m17 の 1 行が κ 0.5〜0.667 の帯にある）

## 層ごとの完成の記録

| 層 | テスト | 触れる CLI（動かした結果） | 設計との差 |
|---|---|---|---|
| A0・A1 L0・A2 単位化 | `test_l0` 30・`test_units` 6 緑。**鍵が out4 の実キャッシュ行（p3 1・p2 2・xc2 3）と一致**＝指示の逐語移植を固定。実接続 1 回（gemma4:31b-cloud）緑 | `units_chat`: 3 単位に割れる。`l0_chat --fake all_no`: 否定している＋ s1。`l0_chat --model gemma4:31b-cloud --cache`: 同じ行の 2 回目はキャッシュ命中 | ① スキーマの持ち主は `prompts.py`（語が指示の集合から来るので。設計は l1 / l2 と書いていた）② `structured` もフェンスを剥がす（冪等。利用側の Reader が剥がさなくても通る）③ パッケージの公開名は遅延読み込み（層を 1 つずつ作ってテストするため） |
| B L1 | `test_l1` 18 緑（規則を 1 件ずつ破る・試行・交差検証の過半数／同数／weak／nonexclusive／検証役 1 体／票の失敗） | `l1_chat` m01 を測定のキャッシュだけで: **実 0・外れ 0**、外した／弱／非排他が `crosscheck.json` と一致（[] / a01 a03 a04 / []）。新しい本文（佐藤の資料・「佐藤は不誠実である」）で実接続: 生成 1・交差検証 60、67 秒。a01 の反証側「共同名義**または**他者の名義で提出した」を検証役 2 体とも支持側と読み、外した | ④ 同一の両側は空撃ちと同じく H4 にも当たる（テストで固定） |
| C1 L2 | `test_l2` 7 緑（正規化・照合・触れていないは根拠不要・再送 → 無効・evidence 欠落は無効・並列でも順序固定・鍵の記録） | `l2_chat` 新しい本文・qwen3.5:397b-cloud で実接続: 「名前を出さなかった」→ 述べている [s3]、「すべて自分で作った」→ 否定している [s2]、「上司に叱られた」→ 否定している [s3]（褒められた） | ⑤ `reinterpret`（記録の生応答から引き直す）を足した（replay の reparse 用） |

## 協議ログ

- v1 [2026-09-22 22:20] 起草

## 変更ログ

- v1 [2026-09-22T22:20:00+09:00]: 初版
