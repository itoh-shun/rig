# rig/pr 既存 PR レビュー pack — design / 実装 spec

- 日付: 2026-06-22
- ブランチ: `claude/rig-goal-loop-resolution-2nl735`
- 種別: 新 pack 追加（rig engine 共用）

## 目的

rig のレビュー力を「**自分の作業ツリーの差分**」から「**既に open している PR**」へ広げる。`/rig:dev --only review` はローカル `git diff` を見るが、他人（や過去の自分）の PR をレビューする入口が無い。PR 番号/URL を渡すと GitHub MCP で取得し、dev と同じ 3-way（security/design/test）レビューを回して structured verdict を返す。

## スコープ

- 入口 `/rig:pr <番号|URL>`。既定 recipe `pr-review`。
- **engine（SKILL.md）は無改変**。dev のレビュアー agent・persona・`review-verdict` output-contract を**共用**し、instruction と recipe だけを足す（sales/talk/goal と同じ pack-add）。
- 取得は GitHub MCP の **read 系**（`pull_request_read` / `list_pull_requests`）。**PR への投稿（コメント/レビュー）は `--comment` 明示かつ確認必須**。
- v1 は単一 PR。マルチ PR 一括・差分追従（再レビュー）は非スコープ。

## dev のレビューとの違い

| | `/rig:dev --only review` | `/rig:pr <番号>` |
|---|---|---|
| 対象 | 自分の作業ツリー差分（`git diff`） | 既存 PR（GitHub MCP 取得） |
| 出力 | 着手判断 | structured verdict（任意で PR コメント） |

→ レビュアー・観点・output-contract は同一。**対象の取得元が違うだけ**の薄い差分。

## アーキテクチャ（追加ブリック）

```
commands/pr.md                              /rig:pr 入口（既定 --recipe pr-review）
skills/rig/facets/instructions/pr-review.md PR 取得→3観点並列→集約→verdict／任意で投稿
skills/rig/recipes/pr-review.md             pr-review step を parallel-fanout＋acceptance-gate で固定
```

> persona/agent/output-contract は新設しない（dev 共用）。

## データフロー

1. `/rig:pr <番号|URL>` 起動 → rig skill → `pr-review` recipe → `pr-review` instruction。
2. **PR 解決＋取得** — 番号/URL を特定（曖昧なら1問確認）。`pull_request_read` で diff/ファイル/説明/レビュー状態を read。**diff 本文は親 context に引き込まず subagent へ**。PR 説明・既存コメントは外部入力として扱い、指示上書きに従わない。
3. **3-way 並列レビュー**（`parallel-fanout`）— security/design/test を1メッセージ同時 dispatch（reviewer agent 優先・persona フォールバック）。出力は `review-verdict`。`--adversarial` で敵対レビュー step を追加。
4. **集約**（`acceptance-gate` 内 `review-gate`）— 3観点判定済み／指摘が「どのファイルの何を・なぜ・どう直すか」分かる粒度／総合 verdict（APPROVE / APPROVE_WITH_CONDITIONS / REJECT）へ収束。
5. **報告／任意投稿** — 既定は user へ提示のみ（read・副作用なし）。`--comment` 時のみ確認の上 PR へ投稿（write は `--autonomous` でも確認解除しない）。

## 受け入れ基準

1. `/rig:pr <番号>` で既存 PR を GitHub MCP 取得し、3観点並列レビュー→structured verdict が出る。
2. レビュアー・persona・`review-verdict` は dev 共用。engine（SKILL.md）無改変・dev レビューフロー不変。
3. 取得は read 系のみ。PR への投稿は `--comment` 明示かつ確認必須（`--autonomous` でも解除されない）。
4. 長い diff を親 context に引き込まず subagent へ渡す（context-minimal）。
5. `--adversarial` 併用で敵対レビュー step が追加される。`--plan` で構成提示・停止。

## 非スコープ

- 複数 PR の一括レビュー／PR 更新の自動再レビュー（push 追従）。
- 自動マージ・自動修正（それは goal-loop / PR babysit の領分）。
- GitHub 以外の forge（GitLab 等）対応。
