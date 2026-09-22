# 実装 v1 の実接続の記録（2026-09-22）

ライブラリ v0.1.0 を、キャッシュに無い新しい本文 2 本でクラウドモデルに通した記録。GPU は使っていない。
走行はリポジトリの外（スクラッチ）で行い、終わってから写した。

| ファイル | 中身 |
|---|---|
| `fresh1.txt` | 新しい物語（佐藤の資料）。命題「佐藤は不誠実である」 |
| `report1.txt` | 障害報告。命題「この障害報告は再現に必要な情報が揃っている」 |
| `live_records.jsonl` | `judge()` の記録 2 行（schema_version 1。本文の単位列・問いの集合・回答（生応答つき）・集約・費用） |
| `live_judge.jsonl` | その生応答のキャッシュ（170 行） |
| `live_l1.jsonl` / `live_l2.jsonl` | `l1_chat` / `l2_chat` の生応答のキャッシュ |

走らせたコマンド（リポジトリの根で）:

```bash
python tools/judge_cli.py fresh1.txt "佐藤は不誠実である" --ordinal 5 --planner gemma4:31b-cloud \
    --readers qwen3.5:397b-cloud glm-5.2:cloud --fake all_yes all_undetermined --workers 6 \
    --cache live_judge.jsonl --record live_records.jsonl
python tools/judge_cli.py report1.txt "この障害報告は再現に必要な情報が揃っている" --probability \
    --planner gemma4:31b-cloud --readers qwen3.5:397b-cloud glm-5.2:cloud --workers 6 \
    --cache live_judge.jsonl --record live_records.jsonl
python tools/l3_chat.py --record live_records.jsonl --line 0 --omega 0.3     # LLM を呼ばずに閾値を変えて引き直す
```

結果:

| 本文 | 読み手 | 値 | 札 | p | w |
|---|---|---|---|---|---|
| 物語 | qwen3.5:397b-cloud | 段 2 | 偏り（反証） | 0.25 | 0.50 |
| 物語 | glm-5.2:cloud | 段 2 | 偏り（反証） | 0.25 | 0.50 |
| 物語 | fake:all_yes | 値なし | 計器不良 | — | — |
| 物語 | fake:all_undetermined | 値なし | 本文に根拠が無い | — | — |
| 障害報告 | qwen3.5:397b-cloud | 0.83 | 偏り（支持） | 0.83 | 0.33 |
| 障害報告 | glm-5.2:cloud | 0.83 | 偏り（支持） | 0.83 | 0.33 |

- どちらも読み手間 Δ 0.00。呼び出しは 1 判定 86 回（生成 1・交差検証 60・回答 24）、約 65 秒
- 障害報告は ∀ 型の命題（「揃っている」）なので多数決の度合いでは表せない（上流 §10）。軸ごとの向きでは a06「コンソールログが添付されていない」だけが反証側。利用側が必須軸の min で集約すれば「揃っていない」になる
- `l3_chat` で ω を 0.3 にして引き直すと、物語の 2 体は「割れる」に変わる（記録だけで引き直せることの確認）
