---
description: "rig/goal — ゴール駆動ループ。高レベルな目標を渡すと、それを受け入れ基準に変換し「現状把握→次手→既存フローへ委譲→受け入れ照合」を達成まで回す。詰まったら停止。工程を選ばずゴールだけ宣言したい時の入口。"
argument-hint: "[達成したい目標（自由記述）] [--autonomous] [--plan] [--capture]"
---

# rig/goal — ゴール駆動ループ

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順・知識層注入）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。dev / sales / talk と同じ engine を goal モードで使う。

起動後、`--recipe goal-loop` を既定として次の引数を PARSE し、ゴールを達成までループで回す:

```
$ARGUMENTS
```

引数が空ならゴールを一言促してから開始する（捏造しない）。

## やること

引数（達成したい目標）を `goal-loop` recipe に渡す。手順本体（①ゴールを受け入れ基準へ変換 →②現状把握 →③gap を縮める最小の1手を決定 →④該当 `/rig:*` へ委譲 →⑤acceptance-gate で照合 →⑥充足で停止／未達は次周回／進捗ゼロ2回で停止しエスカレーション）は `facets/instructions/goal-loop` に従い、`goal-driver` 人格で駆動する。

- goal は**周回のドライバ**。実装・レビュー・調査は委譲先（`/rig:dev` 等）と subagent が回す（context-minimal）。
- **基準を満たしたら止める**（過剰実装しない）。**進捗ゼロが2回続いたら止めて user に委ねる**（無限ループ禁止）。

## flag

- `--autonomous` … 周回ゲートを省き `patterns/autonomous-loop`（`ScheduleWakeup`）で自走。ただし **capture ゲートは解除されない**。
- `--plan` … 受け入れ基準＋想定ループ構成を提示して停止（ドライラン）。RUN しない。
- `--capture` … ループから得た学び（詰まりの原因・決定記録）を承認ダイアログなしで知識層へ（提案表示・事後報告は省略しない）。

## 例

```
/rig:goal "ログイン不具合を回帰込みで直して review 通過まで"   # 達成まで回す
/rig:goal --plan "この Issue を解決可能な状態にする"           # 基準＋ループ構成をドライラン確認
/rig:goal --autonomous "機能Xを実装して PR を出せる状態に"     # 周回ゲートなしで自走（capture は確認）
```


## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない（可視化＝駆動の証拠）:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
