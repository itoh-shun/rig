# knowledge: loop-engineering

「自分で回り続けるループ」を設計するときの**観点カタログ**（事実）。判断は `goal-driver` / `orchestrator` が持つ。ここは「ループ1ターンを何に分解するか・どこで事故るか」だけを並べる。

> Loop Engineering＝harness の **1つ上の層**。harness（どう作業するか）を組むのが harness engineering なら、loop engineering は**作業者をループから外す**（誰の入力も待たずに次の仕事を見つけ、委譲し、検証し、状態を保ち、次を予約する）。rig では `recipes/goal-loop` がこの層に当たる。

## ループ1ターンの5つの動き

ループの1周回は、次の5つに分解できる。rig は新しい制御を発明せず、**既存の仕組みにこの5つを割り当てる**。

| 動き | 何をするか | rig の担い手 |
|---|---|---|
| **discovery** | 現状を把握し、ゴールとの gap から「次にやる最小の1手」を見つける | goal-loop ②現状把握 / ③次手決定 |
| **handoff** | その1手を、自分でやらず subagent / 既存フローへ委譲する | goal-loop ④委譲（`/rig:*`・context-minimal） |
| **verification** | 成果が受け入れ基準を満たしたかを**独立に**検証する | `patterns/acceptance-gate` ＋ `policies/independent-verification` |
| **persistence** | 圧縮・再起動を跨いで goal・基準・現 gap を失わない | run-continuity（PreCompact フック）／`<<autonomous-loop-dynamic>>` の5要素 |
| **scheduling** | 次の周回をいつ起こすかを決め、予約する | `patterns/autonomous-loop`（`ScheduleWakeup`・270/1200秒） |

- この5つが1つでも欠けると事故る：discovery が無ければ空回り、handoff が無ければ親が肥大、**verification が甘ければ間違ったまま収束**、persistence が無ければ圧縮後に再出発、scheduling が雑なら 300 秒禁忌でコスト悪化。

## 最大の事故：自己採点バイアス（self-grading bias）

ループ設計でいちばん壊れやすいのが **verification** だ。理由は単純で、**エージェントは自分の出力を採点すると甘く付ける**（自分を褒める）。生成直後は意図が頭に残っていて、行間を補完して読むからだ。

- **症状**：ループが「達成した」と自己判定して止まるが、実際は基準を満たしていない（過大評価のまま収束）。
- **直す**：verification を**生成者と別の担い手**にする（→ `policies/independent-verification`）。生成（handoff 先）と検証（独立 checker）を切り離す。自己採点は参考値に留め、最終合否にしない。
- これは goal-loop だけの話ではない。de-ai-smell の採点・scenario の検閲・review-gate も同じ＝**作って・自分で合格、を禁止**。

## 暴走を止める（loop は止まれて初めて使える）

- **詰まりガード**：未達かつ進捗ゼロの周回が続いたら停止してユーザーへ（無限ループ禁止・goal-loop の K 規約）。
- **opt-in**：自走（autonomous-loop）は `--autonomous` 明示時のみ。確認なしに回し始めない。
- **過剰実装しない**：全基準を満たしたら止まる（基準を超えて作り続けない）。
