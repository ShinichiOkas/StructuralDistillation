# Structural Distillation — プロジェクト固有の指示

⚠ この文書は `GEMINI.md` と同一内容を保つ（一方を直したら他方にコピーする）。

## プロジェクトの規則（師匠の宣言・2026-09-21）

- 実装言語は **Python**
- **基本的なロジックの設計は他のプロジェクトを参照せずに行う。**
  実際の生きたデータを用いて何か実験したいなどの場合においては、他のプロジェクトを参照してもよい
- 概念の正典は `doc/structural_distillation.md`、先行する実測は `doc/LESSONS_FROM_THE_FIRST_CONSUMER.md`、
  上流設計は `doc/UPSTREAM_DESIGN.md`。設計は仮説であり、実測と師匠の言葉が優先する

## 師匠の宣言（2026-09-23）

> D1は増やさないこの場はLLM非依存のライブラリとしたい。
> LLMプロバイダ対応はライブラリを利用する側の責務。

- **このライブラリは LLM 非依存に保つ。** 接続先を増やさない（同梱の Ollama アダプタは参照実装）
- **LLM プロバイダ対応は利用側の責務。** 利用側が `Reader` の口を実装して差す
- 他プロジェクト（`LLMProviderlib` など）は参照しない。アダプタのために取り込まない

## Pair Agent の作業ディレクトリ

- 合意ドキュメント: `.pair-agent/agreements/<goal-slug>.md`
- スプリントの経過（正）: `.pair-agent/sprints/<sprint_id>.json`
- プロジェクト固有 Skill: `.pair-agent/skills/`（Antigravity 用は `.agents/skills/`）

## スプリントの現在地は作業ツリーごと

「いま何のスプリントか」は全チェックアウト共通の 1 ファイルに置かない。
**チェックアウト 1 つ ＝ 現在地 1 つ**。

- 現在地（ポインタ）: `.pair-agent/current/<鍵>-<末尾ディレクトリ名>.json`
- 鍵: `ホスト名 + git rev-parse --show-toplevel` の SHA-1 先頭 8 桁。計算コマンド（Git Bash）:

  ```bash
  printf '%s%s' "$(hostname)" "$(git rev-parse --show-toplevel)" | sha1sum | cut -c1-8
  ```

- 経過（`経過` 配列）は最初から `.pair-agent/sprints/<sprint_id>.json` に書く。ポインタには書かない。
  `status` はポインタにも置くが、正はジャーナル側
- セッション開始時: 鍵を計算 → ポインタが無ければ新しい作業ツリーとして `idle` で作り、
  師匠に「何を始めますか？」と問う。他の実体のスプリントを自分の現在地として読まない
- `.pair-agent/current-sprint.json` は道標（`"status": "moved"`）。中身は書かない
