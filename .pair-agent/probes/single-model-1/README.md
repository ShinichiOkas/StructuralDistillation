# 単一モデル運用の測定（2026-09-23）

合意 `.pair-agent/agreements/single-model-operation.md` v2 の W1〜W4。**計器はライブラリ v0.1.0**（空撃ちのスクリプトではない）。
走行はスクラッチで回し、終わってから写した。

| ファイル | 中身 |
|---|---|
| `single_model_run.py` | 走行器（W1〜W4） |
| `single_model_report.py` | 判断規則 R-a〜R-d をそのまま当てる読み取り器。LLM を呼ばない |
| `single_model.json` | 題材ごとの結果（軸・札・p・診断値・自己と雲の交差検証・検出項目） |
| `records.jsonl` | `judge()` の記録（schema_version 2。本文の単位列・問い・回答（生応答つき）・集約） |
| `local_cache.jsonl` / `cloud_cache.jsonl` | 生応答のキャッシュ。LLM を呼ばずに引き直せる |
| `questions/` | 問いの保存庫（1 本文 × 1 命題 ＝ 1 JSON）。W1 の軸を W2〜W4 で使い回した |
| `report.md` | 読み（規則どおり） |

## 構成

- 弱いモデル ＝ `qwen3.5:4b`（ローカル・GPU。師匠の承認を得て走らせた。並列なし・4B 1 体だけ常駐）
- 生成器・読み手 2 体（同じモデルを別名で）・検証役 2 体（同じモデルを別名で）＝ すべて同じモデル
- 題材 21（`materials.json` ＋ `materials2.json`）・標本 1・軸 6・交差検証あり
- 比較: クラウド多系統の基準走行（`out4/base21`・想定一致 16/18）と、その軸へのクラウド交差検証（`out4/xc2`）
- 走行 71 分・ローカル 1,772 呼び出し（生成 24・交差検証 1,260・回答 488）・クラウド 1,260 呼び出し

## 再現

```bash
python single_model_report.py --run .           # 読みだけ引き直す（LLM を呼ばない）
```
