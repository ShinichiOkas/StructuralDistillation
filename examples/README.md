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
- 保存された問いと今回の指定（生成器・軸数・交差検証の有無）が違えば「# 注意:」の行に出る。違っても保存された問いを使う
- 作り直したいときは `--regenerate`。`--cache` と一緒でも生成器を呼び直す。古い問いは消えずに `<鍵>.superseded-<時刻>.json` として残る
- 保存庫のファイルを直接直してもよい。直した問いがそのまま使われる（生成の規則は検めない）

## 判定できなかったとき

交差検証で全部の軸が外れると、値は返らない。札は「計器不良」、理由は「判定に使える軸が 0 本」になる。
そのとき CLI は、どこが壊れたか・次に何を打てばよいかを最後に出し、終了コード 2 で終わる。

```
# 判定できなかった（no_active_axes）: 向きの交差検証で全 6 軸が外れた（検証役 …）。判定は計器不良（値なし）。…
# 外れた軸 ['a01', …] / 軸 6・検証役の票は記録の question_set.crosscheck.votes にある
# 作り直すなら:
    …\python.exe tools/judge_cli.py … --questions scratch/questions --regenerate
```

作り直すかどうかはこちらの判断で、ライブラリは自動では作り直さない。`--record` を付けておけば、
どの軸がなぜ外れたか（検証役の票）が記録に残るので、作り直す前に中身を見られる。

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
