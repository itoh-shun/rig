---
name: goal-loop
description: 高レベルな目標を受け取り、ゴールを受け入れ基準に変換して「現状把握→次手決定→既存フローへ委譲→受け入れ照合」を達成まで回す goal-seeking ループ recipe。詰まったら停止。
scope: shipped
steps:
  - id: goal-loop
    instruction: goal-loop
    pattern: serial
    gate: acceptance-gate
    acceptance:
      - "ゴールから導出した受け入れ基準（goal-loop ①）をすべて満たす"
      - "各周回で gap が縮む（進捗が観測できる）"
      - "未達かつ進捗ゼロの周回が2回続いたら停止しユーザーへエスカレーション（無限ループ禁止）"
    personas: [goal-driver, orchestrator]
    policies: [branch-strategy, pr-hygiene]
autonomy: interactive
---

# goal-loop

> **モード pack 注記**: これは rig engine（`SKILL.md`）を dev / sales / talk と**共用**する goal モードの recipe。engine は書き換えず、`goal-driver` persona と `goal-loop` instruction を足すだけで成立する（engine 不変の継続実証）。

## 使う場面

工程（intake → design → … → merge）を自分で選びたくない時。**ゴールだけ宣言**して「達成まで rig に回しきってほしい」時。例:「この Issue を解決可能な状態にして」「ログイン不具合を回帰込みで直して」「この機能を review 通過まで持っていって」。

## 仕組み（acceptance-gate ＋ autonomous-loop の合成）

goal-loop は新しい制御を発明しない。**既存の2パターンを組む**だけ:

- `patterns/acceptance-gate` — 受け入れ基準＝**ユーザーのゴール**。各周回の「再生成」は**既存フローへの委譲**（gap を縮める最小の1手）。基準を満たすまで収束させる。
- `patterns/autonomous-loop` — `--autonomous` 時の周回駆動（`ScheduleWakeup`）。既定は周回ゲートで確認しながら回す。

手順本体（①基準化 →②現状把握 →③次手決定 →④委譲 →⑤照合 →⑥周回/停止）は `facets/instructions/goal-loop` に従う。

## 展開手順

1. **基準化** — ゴールを機械/観点で照合できる受け入れ基準へ落とす（`acceptance`）。曖昧なら1問だけ確認。`--plan` なら基準＋想定ループ構成を提示して停止。
2. **周回ループ**（acceptance-gate）— 「現状把握 → 最小の1手を決定 → `/rig:*` へ委譲 → 基準照合」を回す。実作業は委譲先と subagent（context-minimal）。
3. **収束 / 停止** — 全基準充足で停止（過剰実装しない）。未達かつ進捗ゼロが2回続けば停止して user へエスカレーション。

## autonomy

- 既定 `interactive` — 各周回後に gap と次手を提示して確認。影響あるアクションは委譲先 step ゲートで確認。
- `--autonomous` — `autonomous-loop` で自走（周回ゲート省略）。ただし **capture ゲートは解除されない**。

## GitHub 連動ゴール（任意）

ゴールが「PR を出せる状態に」「マージ可能まで」を含む場合、受け入れ基準に GitHub MCP で照合できる項目を据えられる — 例 `PR が open` / `PR の CI が green` / `対象 Issue がクローズ可能` / `未解決レビュースレッドが無い`。push・PR 作成・マージ自体は委譲先（`/rig:dev` の pr/merge step、`pr-hygiene`/`branch-strategy` 準拠）が行い、goal-loop は GitHub MCP の read 系で**状態を照合するだけ**（CI pending は次周回で再照合、無限待ちは詰まりガードで停止）。これで「ゴール宣言だけで PR/マージ可能状態まで」を1フローで回せる。

## K（周回上限）

acceptance-gate の K 規約に従う。既定は進捗ゼロ2回で停止。厳しいゴールほど**回数を増やすより基準を明確化**する方が収束は速い。K と基準は recipe / manifest で調整可能。
