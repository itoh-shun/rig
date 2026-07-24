---
description: "rig/loop — 一定間隔 or 自己ペースで対象（コマンド/rig フロー/タスク）を繰り返す recurring driver。停止条件（--until/--times/明示）と安全上限つき。goal（達成まで収束）の対極＝見張り・ポーリング・定期実行。"
argument-hint: "[\"繰り返す対象（コマンド/タスク）\"] [--every <dur>] [--until \"<check>\"] [--times N] [--plan]"
---

# rig/loop — 繰り返し・監視ループ 🔁

**まず `rig:engine` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。スケジューリングは既存の `patterns/autonomous-loop`（`ScheduleWakeup`）を再利用する。

起動後、`--recipe loop` を既定として次の引数を PARSE する:

```
$ARGUMENTS
```

## やること

対象を `loop` recipe に渡す。手順本体（①対象・間隔・停止条件の確定 →②1 tick 実行 →③停止判定 →④次 tick 予約/終了）は `facets/instructions/loop-driver` に従う。

- **繰り返す対象**: `/rig:dev`・`/rig:pr` 等の rig フロー、または任意コマンド（CI 確認・集計等）。
- **いつまた回すか**: `--every <dur>`（時間駆動・例 `10m`）／省略時は自己ペース（合図やイベントで次へ）。
- **どこで止まるか（必須）**: `--until "<check>"`（機械検証で停止条件）／`--times N`（回数）／明示停止（この場合は安全上限を確認）。停止条件も上限も無いまま無限監視に入らない。
- 各 tick を**報告**する。書込/push/merge を伴う対象は tick ごとに委譲先の step ゲートで確認。

## goal との違い（重ねて使える）

- `/rig:goal`＝**達成まで収束**（終端＝受け入れ基準）。終わりのある仕事。
- `/rig:loop`＝**繰り返す/見張る**（終端＝停止条件・回数）。終わりのない仕事。
- `/rig:loop --every 1h /rig:goal "…"` のように、loop で goal を定期キックできる（loop が外側のスケジューラ、goal が中身の収束）。

## flag

- `--every <dur>` … 時間駆動の間隔（例 `5m`/`1h`）。`ScheduleWakeup` 規約（270/1200・**300 禁忌**）に従う。省略時は自己ペース。
- `--until "<check>"` … 停止条件（shell exit 0 / GitHub MCP status / grep 等で「終わったか」を判定）。
- `--times <N>` … N 回で終了。
- `--plan` … 対象・間隔・停止条件を提示して停止（ドライラン）。

## 例

```
/rig:loop --every 10m --until "CI が green" /rig:pr 1234     # PR の CI を緑になるまで見張る
/rig:loop --times 3 /rig:dev --only review                  # レビューを3回回す
/rig:loop --every 1h /rig:goal "Issue を review 通過まで"     # goal を1時間ごとに定期キック
/rig:loop "毎朝レポートを集計"                                # 自己ペースの定期チョア
/rig:loop --plan --every 10m --until "デプロイ成功" ...        # 構成だけ先に確認
```

## 代表的な合成例

```
/rig:loop --until "PR #123 が MERGED または CLOSED" "/rig:pr 123"   # PR 常駐（babysit）
/rig:loop --every 7d "/rig:import --check-updates"                  # skill-dependabot（上流差分の定期検知）
```


## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない（可視化＝駆動の証拠）:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
