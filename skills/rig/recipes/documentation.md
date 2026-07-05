---
name: documentation
description: workbench 既定の documentation フロー（inspect→identify-audience→draft→verify-commands→acceptance）。コマンド例の実行確認までを acceptance-check で締める。`/rig "<READMEをわかりやすく>"` 等から自動選択される。
scope: shipped
autonomy: interactive
steps:
  - id: inspect
    instruction: intake
    pattern: serial
    personas: [orchestrator]
    policies: [branch-strategy]
  - id: identify-audience
    instruction: identify-audience
    pattern: serial
    personas: [orchestrator]
  - id: draft
    instruction: docs-draft
    pattern: serial
    personas: [implementer]
  - id: verify-commands
    instruction: verify-commands
    pattern: serial
    personas: [implementer]
  - id: acceptance
    instruction: acceptance-check
    pattern: serial
    gate: acceptance-gate
    max_retries: 2
    acceptance:
      - "task_intent_satisfied — 依頼した文書整備の意図が満たされている"
      - "no_unrelated_diff — 依頼と無関係な差分が含まれていない"
      - "diff_summary_written — 何を書き換えたかのサマリが diff.md に書かれている"
      - "risk_summary_written — 誤解を招く記述・古い情報が残っていないかのリスクサマリが書かれている"
      - "tests_pass_or_explained — コマンド例が実行確認済みか、未確認の理由が明記されている"
      - "no_type_errors_or_explained — （該当なしなら skipped。コード例に型注釈があれば整合を確認）"
      - "no_secret_leak — secret の混入がない"
      - "no_destructive_operation — 破壊的操作を含まない"
    personas: [implementer]
---

# documentation

## 使う場面

`/rig "<ドキュメント整備の依頼>"` から `task_type: documentation` として自動選択される workbench 既定 recipe。README・CHANGELOG・docs/*.md の新規作成・改稿に使う。他の workbench recipe（bugfix/feature/refactor）と異なり task_type 別プリセット（`bugfix`/`feature`/`refactor` 等コード実装前提の基準）は上乗せしない——`standard` preset の8基準のみ。

## 展開手順

1. **inspect** — 対象ドキュメント・目的・完了条件を確定する。
2. **identify-audience** — 新規ユーザー / 既存ユーザー / コントリビューターのどれに向けて書くかを確定する（`facets/instructions/identify-audience`）。
3. **draft** — 対象ドキュメントを起草・改稿する（`facets/instructions/docs-draft`）。「最初の成功体験」を機能一覧より先に見せる・AI 特有の定型表現を避ける、を意識する。
4. **verify-commands** — 本文中のコマンド例・コードブロックを実行確認する（`facets/instructions/verify-commands`）。「READMEのコマンド例が動かなくなる変更」（drill の種カタログにも載る典型的ドキュメント虚偽化）をここで機械的に防ぐ。
5. **acceptance** — standard 8基準の acceptance-check。

## review-diff を持たない理由

散文の質は `recipes/de-ai-smell`（AI 臭除去・5観点スコアゲート）が専門に担う別 recipe であり、本 recipe で再実装しない。大規模なドキュメント改稿で AI 臭のチェックも要る場合は `/rig:dev --recipe de-ai-smell` を別途走らせることを提案する（Native-first・重複実装しない）。
