---
description: "rig/tasks — 依頼を細粒度の検証可能タスクに割ってから実装する。plan(各タスクに検証つき)→implement(タスク順・--tdd可)→verify→review。大きく曖昧に実装せず「小さく割って・確かめながら・順に潰す」。承認を取ってから実行。"
argument-hint: "[\"<やりたいこと>\"] [--plan] [--tdd] [--orchestrate]"
---

# rig/tasks — 細粒度プランニング 🧩

**まず `rig:engine` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・acceptance-gate）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。

起動後、`--recipe task-plan` を既定として次の引数を PARSE する:

```
$ARGUMENTS
```

## やること

依頼を `task-plan` recipe に渡す。手順本体（①分解 →②実行 →③レビュー →④収束）は `facets/instructions/task-plan`、分解の作法は `facets/personas/planner` に従う。

- **細粒度に割る**: 1タスク＝数分・少数ファイル。曖昧な大タスクを作らない。
- **各タスクに検証**: 「どうなったら完了か」をコマンド/テスト/grep/観察で（検証の無いタスクは作らない）。
- **未確定は先出し**: 仕様の穴・前提不明は捏造でタスク化せず「要調査」に出して先に潰す。
- **承認してから実行**: 計画を提示し、OK をもらってから実装に移る（大きく作ってからの方針転換を避ける）。
- **上から潰す**: 依存順に実装→各タスクの検証→次へ。独立タスクは `--orchestrate` で並列可。

## goal との違い

- `/rig:tasks`＝**事前に全タスクを見渡す計画**（段取りして潰す）。
- `/rig:goal`＝**反応的に次の一手**（達成まで収束）。

## flag

- `--plan` … 計画（タスク表＋未確定）を提示して停止（実行しない）。
- `--tdd` … 各タスクを red-green-refactor で実装。
- `--orchestrate` … 独立タスクを別プロセスで並列実行（DAG）。

## 例

```
/rig:tasks "JWT のリフレッシュを追加"            # 細粒度に割って実装まで
/rig:tasks --plan "決済画面のリファクタ"          # 計画だけ先に確認
/rig:tasks --tdd "バリデーションを strict に"     # 各タスクを TDD で
```


## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない（可視化＝駆動の証拠）:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
