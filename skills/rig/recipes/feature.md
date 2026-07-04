---
name: feature
description: workbench 既定の feature フロー（inspect→clarify_requirements→design→implement→test→update_docs_if_needed→review_diff→acceptance）。隔離 worktree ＋ machine-gate な acceptance-check で締める。`/rig "<機能追加>"` から自動選択される。
scope: shipped
autonomy: interactive
steps:
  - id: inspect
    instruction: intake
    pattern: serial
    personas: [orchestrator]
    policies: [branch-strategy]
  - id: clarify_requirements
    instruction: intake
    pattern: serial
    personas: [orchestrator]
  - id: design
    instruction: design
    pattern: serial
    personas: [implementer]
    policies: [risk-based-testing]
  - id: implement
    instruction: implement
    pattern: serial
    personas: [implementer]
    policies: [risk-based-testing, ci-cost]
  - id: test
    instruction: verify
    pattern: serial
    personas: [implementer]
    policies: [risk-based-testing, ci-cost]
  - id: update_docs_if_needed
    instruction: update-docs
    pattern: serial
    personas: [implementer]
  - id: review_diff
    instruction: parallel-review
    pattern: parallel-fanout
    gate: review-gate
    personas: [security-reviewer, design-reviewer, test-reviewer]
    output_contract: review-verdict
  - id: acceptance
    instruction: acceptance-check
    pattern: serial
    gate: acceptance-gate
    max_retries: 2
    acceptance:
      - "task_intent_satisfied — 依頼の意図が満たされている"
      - "no_unrelated_diff — 依頼と無関係な差分が含まれていない"
      - "diff_summary_written — diff.md に差分の要約が書かれている"
      - "risk_summary_written — リスクサマリが書かれている"
      - "tests_pass_or_explained — テストが green か、失敗の合理的説明がある"
      - "no_type_errors_or_explained — 型エラーがないか、あれば説明がある"
      - "no_secret_leak — secret の混入がない"
      - "no_destructive_operation — 破壊的操作を含まない"
      - "requirement_summary_written — 要件のサマリが書かれている"
      - "implementation_matches_requirement — 実装が要件と一致している"
      - "tests_added_or_explained — テストを追加したか、理由を説明した"
      - "public_api_changes_documented — 公開 API 変更が説明されている"
      - "migration_or_backward_compatibility_considered — 移行・後方互換性を検討した"
    personas: [implementer]
---

# feature

## 使う場面

`/rig "<機能追加の依頼>"` から `task_type: feature` として自動選択される workbench 既定 recipe。`recipes/design-first`（design 品質最優先・design-reviewer 常駐）より軽量で、`recipes/release-flow`（size-aware で design/review が条件付き）より一貫して厚い——**「普通の機能追加」に過不足のない既定**を狙う。

## 展開手順

1. **inspect** — 何/なぜ/どこ/どこまでを確定する。
2. **clarify_requirements** — AC（完了条件）を明文化する。曖昧な場合はここでユーザーに問い直す（`facets/instructions/intake` ①のスコープ確認を要件確定に特化して再適用）。
3. **design** — 最低限の設計ドキュメント（目的・方針・AC・除外事項）を作成する（`facets/instructions/design`）。size が S でも省略しない（feature は常に design を1段挟む——size-aware の重い step 自動 OFF は verify/review 側の話であり、feature recipe 自体は既定で design を含む）。
4. **implement** — 設計に従って実装する。
5. **test** — build/lint/test を実行する。
6. **update_docs_if_needed** — 公開挙動・API・設定を変えた場合のみドキュメントを更新する（無関係なら skip・`no_unrelated_diff` を守る）。
7. **review_diff** — security/design/test の3観点並列レビュー。
8. **acceptance** — 13基準（standard 8 + feature 5）の acceptance-check（`max_retries: 2`）。

## bugfix との違い

`reproduce`/`plan`（原因調査）の代わりに `clarify_requirements`/`design`（要件・設計の合意形成）を挟む。実装対象が「直すべき既知の挙動」か「新しく作る挙動」かで、確定すべき情報の性質が違うため。gate も bugfix プリセット（原因/最小性/回帰）ではなく feature プリセット（要件一致/互換性）を使う。
