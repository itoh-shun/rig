---
name: loop
description: 一定間隔 or 自己ペースで対象（コマンド/rig フロー/タスク）を繰り返す recurring driver recipe。停止条件（--until/--times/明示停止）と安全上限つき。goal（達成まで収束）の対極＝「いつまた回すか」を担う watch/poll/repeat。
scope: shipped
steps:
  - id: loop
    instruction: loop-driver
    pattern: serial
    personas: [orchestrator]
autonomy: interactive
---

# loop

> **モード pack 注記**: rig engine（`SKILL.md`）を goal / dev と**共用**する recurring モードの recipe。engine は書き換えず、`loop-driver` instruction を足すだけで成立する。スケジューリングは既存の `patterns/autonomous-loop`（`ScheduleWakeup`）を再利用する。`/rig:loop` から起動。

## 使う場面

**終わりのない仕事＝見張り・定期実行・ポーリング**を回したい時。`/rig:goal`（終端のある仕事＝達成まで収束）の対極。例:

- 「この PR の CI を緑になるまで見張って」（`--until` で停止条件）
- 「10 分ごとにデプロイ状況を確認して、失敗したら直す」
- 「現在の変更を3回レビューし直して」（`--times 3`）
- 「毎朝レポートを集計」（自己ペースの定期チョア）

## goal との対（収束 vs 繰り返し）

| | loop | goal |
|---|---|---|
| 軸 | **いつまた回すか**（時間/間隔） | **どうなったら終わるか**（達成基準） |
| 各周回 | 同じ対象を再実行 | gap を縮める「次の最小1手」 |
| 終端 | 停止条件・回数・明示停止 | 受け入れ基準の充足 |
| 向く | 監視・ポーリング・定期実行 | 機能完成・不具合を直しきる |

`/rig:goal` を `/rig:loop --every 1h` で定期キックする、のように**重ねて**使える（loop が外側のスケジューラ、goal が中身の収束）。

## 仕組み（autonomous-loop の再利用）

新しい制御は発明しない。**`patterns/autonomous-loop`（`ScheduleWakeup`）で次の tick を予約する**だけ。各 tick で対象を委譲実行し、停止条件を判定して継続/終了する。

手順本体（①対象・間隔・停止条件の確定 →②1 tick 実行 →③停止判定 →④次 tick 予約/終了）は `facets/instructions/loop-driver` に従う。

## ガード

- **停止条件 or 上限が必須**（`--until` / `--times` / 明示の停止合意）。無いまま「無限監視」に入らない＝暴走防止（goal の詰まりガードと同じ精神）。
- **各 tick を報告**する（沈黙で回り続けない）。
- 時間駆動（`--every`）は `ScheduleWakeup` の `delaySeconds` 規約に従う（キャッシュ温なら 270 秒・冷なら 1200 秒以上・**300 秒は禁忌**）。
- 書込/push/merge を伴う対象は tick ごとに委譲先の step ゲートで確認（`--autonomous` でも capture ゲートは解除されない）。
