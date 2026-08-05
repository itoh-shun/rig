---
name: pr-review
description: 既存 PR を番号/URL で受け取り、GitHub MCP で取得して 3-way 並列レビュー(security/design/test)＋(任意)敵対レビューを回し、acceptance-gate で structured verdict に収束させる PR レビュー pack recipe。
scope: shipped
steps:
  - id: pr-review
    instruction: pr-review
    pattern: parallel-fanout
    gate: acceptance-gate
    acceptance:
      - "security / design / test の3観点すべてが判定済み"
      - "各 REJECT / 条件付き承認に「どのファイルの何を、なぜ、どう直すか」が分かる粒度"
      - "総合 verdict（APPROVE / APPROVE_WITH_CONDITIONS / REJECT）が出ている"
    personas: [security-reviewer, design-reviewer, test-reviewer]
    policies: [pre-push-review]
    output_contract: review-verdict
autonomy: interactive
---

# pr-review

> **モード pack 注記**: これは rig engine（`SKILL.md`）を dev / sales / talk / goal と**共用**する PR レビュー pack の recipe。engine は書き換えず、`pr-review` instruction を足すだけで成立する。レビュアー agent・persona・`review-verdict` output-contract は dev と共用する。

## dev のレビューとの違い

| | `/rig:dev --only review` | `/rig:pr <番号>` |
|---|---|---|
| 対象 | **自分の作業ツリーの差分**（`git diff`） | **既存 PR**（GitHub MCP で取得） |
| 入力 | ローカル変更 | PR 番号 / URL |
| 出力 | 着手判断 | structured verdict（任意で PR へコメント） |

## 使う場面

他人（または自分）の**既に open している PR** を、3観点＋必要なら敵対レビューで評価したい時。レビュー負荷の平準化・観点の取りこぼし防止。

## 展開手順

1. **PR 解決＋取得** — 番号/URL から PR を特定し、GitHub MCP（`pull_request_read` で diff/ファイル/メタdata）を read 系で取得する。長い diff は親 context に引き込まず subagent へ渡す。
2. **3-way 並列レビュー**（`parallel-fanout`）— security/design/test を1メッセージで同時 dispatch（reviewer agent 優先・persona フォールバック）。出力は `review-verdict` に従わせる。
3. **集約**（`acceptance-gate` 内で `review-gate`）— 3 verdict を集約し、上記 acceptance に収束させる。`--adversarial` 併用時は敵対レビュー step を追加する。
4. **報告（任意で PR コメント）** — 総合 verdict を提示する。**PR への書き込み（コメント/レビュー投稿）は影響あるアクションなので確認必須**（`--comment` 明示時のみ確認の上で投稿）。既定は user への提示のみ。

## flag

- `--adversarial` … 敵対的レビュー（AI の癖排除・人間可読性・不要コメント除去）step を追加。
- `--comment` … 結果を PR にコメント/レビューとして投稿（確認必須・既定は提示のみ）。
- `--plan` … 構成を提示して停止（ドライラン）。
