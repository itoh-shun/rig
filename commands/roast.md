---
description: "[experimental] rig/roast — 毒舌ロースト・レビュー。現在の変更を辛辣で笑える言い回しでレビューするが、指摘の中身は本物（AI 臭・可読性・過剰/不足・バグ）。笑いで批判のエゴ防御を下げて指摘を実際に読ませる adversarial-review の humor 変種。"
argument-hint: "[レビュー対象（省略可・既定は現在の変更）] [--plan]"
---

# rig/roast — 毒舌ロースト・レビュー 🌶️

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順・知識層注入）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。dev / magi と同じ engine をユーモア配送のレビューに使う。

起動後、`--recipe roast` を既定として次の引数を PARSE し、対象を毒舌レビューする:

```
$ARGUMENTS
```

引数が無ければ現在の作業ツリーの変更（`git diff`）を対象にする。

## やること

対象（diff / ファイル列）を `roast` recipe に渡す。手順本体（①変更収集 →② `roast-reviewer` を dispatch（ai-quirks を効かせる）→③ `review-gate` で集約）は `facets/instructions/roast` に従う。

- **中身は本物のレビュー**: 笑いは配送装置。判定・根拠・必須条件は素面で正確（`review-verdict` 準拠）。
- **`/rig:dev --adversarial` との違い**: 的は同じ（AI 臭・可読性）だが、roast は声を毒舌芸人に振る。真顔で受けたいなら `--adversarial`、笑って受けたいなら roast。
- 的は**コードであって人ではない**。書いた人は貶めない。笑わせるために重大な指摘は落とさない。
- 実作業（読解・評価）は reviewer subagent が回す（context-minimal）。長い diff を親に引き込まない。

## flag

- `--plan` … 構成を提示して停止（ドライラン）。

## 例

```
/rig:roast                       # 現在の変更を毒舌レビュー
/rig:roast ./src/auth.ts         # 特定ファイルを的に
/rig:roast --plan                # 構成だけ確認
```


## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない（可視化＝駆動の証拠）:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
