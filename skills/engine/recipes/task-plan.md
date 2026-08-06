---
name: task-plan
description: 依頼を細粒度の検証可能タスクに割ってから実装する recipe。plan(planner→各タスクに検証つき)→implement(タスク順・--tdd可)→verify→review の前段付き dev。大きく曖昧に実装せず「小さく割って・確かめながら・順に潰す」。goal(反応的な次の一手)の対＝事前に全タスクを見渡す計画。
scope: shipped
steps:
  - id: plan
    instruction: task-plan
    pattern: serial
    personas: [planner]
    output_contract: task-plan
    gate: acceptance-gate
    acceptance:
      - "各タスクが細粒度（数分・少数ファイル）で独立に検証できる"
      - "各タスクに検証手順（コマンド/テスト/grep/観察）がある"
      - "未確定は捏造せず『未確定/要調査』に出ている"
  - id: implement
    instruction: implement
    pattern: serial
    personas: [implementer]
    policies: [risk-based-testing, ci-cost]
  - id: verify
    instruction: verify
    pattern: serial
    gate: acceptance-gate
    acceptance: ["build が成功", "lint 0 件", "関連テスト green", "計画の各タスクの検証が満たされている"]
    personas: [implementer]
    policies: [risk-based-testing, ci-cost]
  - id: review
    instruction: parallel-review
    pattern: parallel-fanout
    gate: acceptance-gate
    acceptance: ["3-way review に REJECT が無い", "APPROVE_WITH_CONDITIONS のマージ前必須条件をすべて反映済み"]
    personas: [security-reviewer, design-reviewer, test-reviewer]
    policies: [pre-push-review]
    output_contract: review-verdict
autonomy: interactive
---

# task-plan

> **モード pack 注記**: rig engine（`SKILL.md`）を dev / goal と**共用**する planning pack の recipe。engine は書き換えず、`planner` persona・`task-plan` instruction・`task-plan` output-contract を足すだけで成立する（実装・検証・レビューは dev の既存 step を再利用）。`/rig:tasks` から起動。

## 使う場面

**大きく曖昧に実装に突っ込む前に、小さく割って段取りしたい**時。例:

- 「この機能、何から手を付ければいいか分からない」→ 細粒度タスクに割る
- 「大きく作ってから『方針が違った』を避けたい」→ 計画を承認してから実行
- 「各ステップが終わったか曖昧」→ 各タスクに検証をつける

## goal との対（事前計画 vs 反応的）

| | task-plan | goal |
|---|---|---|
| いつ割るか | **事前に全タスクを見渡す** | 周回ごとに次の一手 |
| 出力 | 検証つきタスク表 | gap を縮める1手 |
| 向く | 段取りして潰す | 達成まで収束させる |

## 展開

1. **plan** — `planner` が依頼を `task-plan`（細粒度・検証つき・未確定を先出し）へ分解。**承認を取ってから**実行（`--plan` で計画提示・停止）。
2. **implement** — タスク順（依存順）に実装。1タスク＝1 subagent（context-minimal）・`--tdd` で red-green-refactor。独立タスクは `--orchestrate` の DAG 並列に渡せる。
3. **verify** — 計画の各タスクの検証（テスト/コマンド/観察）＋ build/lint/test を acceptance-gate で。
4. **review** — security/design/test の並列レビュー（2段目）を review-gate で。

手順本体は `facets/instructions/task-plan`、分解の作法は `planner`、出力は `output-contracts/task-plan` に従う。配線・PR・マージまで要るなら `/rig:dev`（release-flow）へ接続。

## ガード

- **大きく曖昧なタスク／検証の無いタスクを作らない**。**未確定は捏造で埋めず要調査へ**。
- **計画は承認を取ってから実行**（実装してから方針転換を避ける）。
- 計画は dev フローの前段＝engine の再定義ではない。実装・検証・レビューは委譲先と subagent が回す（context-minimal）。
