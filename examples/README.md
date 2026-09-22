# 試すための本文

CLI（`tools/`）を触るための短い本文。どれも創作。リポジトリの根で、venv を有効にしてから打つ。

| ファイル | 中身 | 試す命題の例 |
|---|---|---|
| `lighthouse.txt` | 灯台守のゲン（測定の題材 m01 と同じ本文） | 「ゲンは悪人である」 |
| `story_sato.txt` | 同僚の資料を自分の名前で出した佐藤 | 「佐藤は不誠実である」 |
| `bug_report.txt` | ログイン画面の障害報告 | 「この障害報告は再現に必要な情報が揃っている」 |

## 1 判定を端から端まで（クラウドモデル・約 1 分・LLM 呼び出し約 86 回）

```powershell
.venv/Scripts/python tools/judge_cli.py examples/lighthouse.txt "ゲンは悪人である" --ordinal 5 --planner gemma4:31b-cloud --readers qwen3.5:397b-cloud glm-5.2:cloud --workers 6 --cache scratch/cache.jsonl --record scratch/records.jsonl
```

- `--cache` を付けると、同じ本文・命題の 2 回目は LLM を呼ばずに一瞬で返る（`scratch/` は好きな場所でよい。`.pair-agent/probes/` の下は書き込み先に使えない）
- `--fake all_yes all_undetermined` を足すと、較正用の偽読み手の札（計器不良・本文に根拠が無い）も並ぶ
- `--probability` にすると段ではなく度合い p がそのまま値になる

## 問いを保存して再利用する

`--questions` に保存庫のディレクトリを渡すと、同じ命題と本文では 2 回目から過去の問いをそのまま使う。
生成器も交差検証も呼ばないので、読み手の回答だけで済む（生成器を替えても同じ問いになる）。

```powershell
.venv/Scripts/python tools/judge_cli.py examples/story_sato.txt "佐藤は不誠実である" --ordinal 5 --planner gemma4:31b-cloud --readers qwen3.5:397b-cloud glm-5.2:cloud --workers 6 --questions scratch/questions
.venv/Scripts/python tools/questions_cli.py scratch/questions list          # 保存されている問いの一覧
.venv/Scripts/python tools/questions_cli.py scratch/questions show e90b     # 鍵の先頭で 1 つを表示
```

- 出力の 2 行目に「保存庫の問いを再利用した」か「新しく作って保存庫に保存した」かが出る
- 作り直したいときは `--regenerate`。古い問いは消えずに `<鍵>.superseded-<時刻>.json` として残る
- 保存庫のファイルを直接直してもよい。直した問いがそのまま使われる（生成の規則は検めない）

## 記録を閾値を変えて引き直す（LLM を呼ばない）

```powershell
.venv/Scripts/python tools/l3_chat.py --record scratch/records.jsonl --omega 0.3
```

## 層ごとに触る

```powershell
.venv/Scripts/python tools/units_chat.py examples/lighthouse.txt
.venv/Scripts/python tools/l1_chat.py examples/lighthouse.txt "ゲンは悪人である" --planner gemma4:31b-cloud --verifiers qwen3.5:397b-cloud glm-5.2:cloud --cache scratch/cache.jsonl
.venv/Scripts/python tools/l2_chat.py examples/lighthouse.txt --reader qwen3.5:397b-cloud --cache scratch/cache.jsonl
.venv/Scripts/python tools/l4_chat.py --ordinal 5
.venv/Scripts/python tools/l0_chat.py --model gemma4:31b-cloud
```

`l2_chat`・`l4_chat`・`l0_chat` は 1 行ずつ打つ対話式。空行で終わる。
測定 2 周目の記録を集約し直すだけなら `.venv/Scripts/python tools/l3_chat.py --probe base21 --id m01`（LLM を呼ばない）。
