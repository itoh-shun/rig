---
description: "rig/brainstorm — ラフな着想を質問→代替案→セクション合意で固める壁打ち。実装/タスク分解の前段＝「何を作るか/なぜ/どの順か」を先に固め、曖昧なまま実装に突っ込むのを防ぐ。design-brief に収束し /rig:tasks・/rig:dev へ接続。"
argument-hint: "[\"<ぼんやりした要望>\"] [--plan]"
---

# rig/brainstorm — 設計の壁打ち 💭

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。

起動後、`--recipe brainstorm` を既定として次の引数を PARSE する:

```
$ARGUMENTS
```

## やること

要望を `brainstorm` recipe に渡す。手順本体（①発散 →②代替案 →③セクション合意 →④収束 →⑤接続）は `facets/instructions/brainstorm`、作法は `facets/personas/brainstormer` に従う。

- **決め打ちしない・先に問う**: 不明点・前提・制約・成功条件を質問で潰す（当て推量で設計を進めない）。
- **発散→収束**: 2〜3の代替案＋トレードオフを出し、推しを1つに収束（根拠つき）。1案で済ませない。
- **セクションで合意**: 設計を節（データ/UI/失敗時/移行 等）に分け、1つずつ承認/修正を取る。一気に決めない。
- **未解決は隠さない**: 決まらない点は「未解決の問い」に出す（捏造で埋めない）。
- **実装には踏み込まない**: 何を・なぜ・どの順、まで。次段（`/rig:tasks`・`/rig:dev`）へ渡す。
- **終了時に次段を1つ推薦**: 固めた内容から最適な次段（規模大→`/rig:tasks`／小さく明確→`/rig:dev`／未解決が重い→調査）を理由つきで提示し、「これで進める？」と確認してから渡す（無断 auto-chain しない）。

## 前段の位置

`/rig:brainstorm`（何を作る/なぜ）→ `/rig:tasks`（どう割る）→ `/rig:dev`（どう実装）。`/rig:goal`（達成まで収束）とも繋げられる。

## flag

- `--plan` … design-brief の草案を提示して停止（合意プロセスに入る前のドライラン）。

## 例

```
/rig:brainstorm "通知機能を作りたい"            # 設計から壁打ち
/rig:brainstorm --plan "課金まわりの再設計"       # 草案だけ先に見る
/rig:brainstorm "検索が遅い。どう直すか相談したい"
```


## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない（可視化＝駆動の証拠）:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
