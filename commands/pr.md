---
description: "rig/pr — 既存 PR レビュー。PR 番号/URL を渡すと GitHub MCP で取得し、security/design/test の3観点(＋任意で敵対レビュー)を並列評価して structured verdict を返す。自分の作業ツリーでなく「既に open している PR」を見る入口。"
argument-hint: "[PR 番号 or URL] [--adversarial] [--comment] [--plan]"
---

# rig/pr — 既存 PR レビュー

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順・知識層注入）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。dev / sales / talk / goal と同じ engine を PR レビューに使う。

起動後、`--recipe pr-review` を既定として次の引数を PARSE し、対象 PR を3観点で並列レビューする:

```
$ARGUMENTS
```

引数に PR 番号/URL が無ければ一言だけ確認する（捏造しない）。

## やること

引数（PR 番号 or URL）を `pr-review` recipe に渡す。手順本体（①GitHub MCP で PR 取得 →②security/design/test を `parallel-fanout` で並列レビュー →③`acceptance-gate`＋`review-gate` で集約 →④総合 verdict 提示／任意で PR コメント）は `facets/instructions/pr-review` に従う。

- **`/rig:dev --only review` との違い**: dev は**自分の作業ツリーの差分**、pr は**既に open している PR**（GitHub MCP 取得）。
- 実作業（読解・評価）は reviewer subagent が回す（context-minimal）。長い diff を親に引き込まない。

## flag

- `--adversarial` … 敵対的レビュー（AI の癖排除・人間可読性・不要コメント除去）step を追加。
- `--comment` … 結果を PR にコメント/レビューとして投稿（**書き込み＝確認必須**。`--autonomous` でも解除されない。既定は提示のみ）。
- `--plan` … 構成を提示して停止（ドライラン）。

## 例

```
/rig:pr 1234                 # PR #1234 を3観点でレビュー
/rig:pr 1234 --adversarial   # 敵対レビューも併せて
/rig:pr 1234 --comment       # レビュー結果を PR に投稿（確認の上）
/rig:pr --plan 1234          # レビュー構成をドライラン確認
```
