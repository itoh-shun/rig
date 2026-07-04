---
name: bugfix
description: workbench 既定の bugfix フロー（inspect→reproduce→plan→implement→test→review_diff→acceptance）。隔離 worktree ＋ machine-gate な acceptance-check で締める。`/rig "<バグ修正>"` から自動選択される。
scope: shipped
autonomy: interactive
steps:
  - id: inspect
    instruction: intake
    pattern: serial
    personas: [orchestrator]
    policies: [branch-strategy]
  - id: reproduce
    instruction: intake
    pattern: serial
    personas: [debugger]
  - id: plan
    instruction: implement
    pattern: serial
    personas: [debugger]
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
      - "no_unrelated_diff — 依頼と無関係な差分が含まれていない"
      - "tests_pass_or_reasonable_explanation — テストが green か、失敗の合理的説明がある"
      - "no_type_errors — 型エラーなし"
      - "no_lint_errors — lint エラーなし"
      - "behavior_summary_written — 挙動変更のサマリが書かれている"
      - "risk_summary_written — リスクサマリが書かれている"
      - "implementation_matches_request — 実装が依頼内容と一致している"
      - "tests_added_or_existing_tests_confirmed — テストを追加したか、既存テストで担保されることを確認した"
      - "public_api_changes_documented — 公開 API 変更が説明されている"
      - "no_unrelated_refactor — 依頼にない広範なリファクタが混ざっていない"
      - "no_secret_leak — secret の混入がない"
      - "no_destructive_operation — 破壊的操作を含まない"
    personas: [implementer]
---

# bugfix

## 使う場面

`/rig "<バグ修正の依頼>"` から `task_type: bugfix` として自動選択される workbench 既定 recipe。`recipes/debug`（原因不明のバグの調査重視）・`recipes/hotfix`（速度最優先で reproduce/plan/review を省略）とは異なり、**隔離 worktree ＋ 3-way review ＋ machine-gate な acceptance-check** をフルで通す「通常のバグ修正」の既定パス。

| recipe | 特徴 |
|---|---|
| `hotfix` | 最短パス。design/review を省略。verify の gate も軽量（build/lint のみ） |
| `debug` | 原因不明時の調査重視（isolate で仮説列挙） |
| **bugfix**（本 recipe） | 通常のバグ修正の既定。review_diff（3-way）＋ 12項目の acceptance-check まで通す |

## 展開手順

1. **inspect** — 依頼の何/なぜ/どこ/どこまでを確定する（`intake` 委譲）。
2. **reproduce** — バグを再現する手順を確定する（`debugger` persona）。再現できないまま次へ進まない。
3. **plan** — 根拠に基づき修正方針を立てる（`implement` instruction を読解・仮説列挙モードで使用。コード変更はまだ行わない — `recipes/debug` の isolate step と同じ考え方）。
4. **implement** — 最小限の修正を実施する。
5. **test** — build/lint/test を実行する。
6. **review_diff** — security/design/test の3観点並列レビュー（`review-gate`）。
7. **acceptance** — `facets/instructions/acceptance-check` が12基準（standard 6 + implementation 6）を判定し `scripts/workbench.py gate` に記録する。`fail` があれば `max_retries: 2` まで収束、超えたら user へエスカレーション。

## isolated worktree との関係

本 recipe は `facets/instructions/workbench`（`/rig` 統一入口）から起動された場合、`patterns/isolated-worktree` に従い専用 worktree で実行される。`--recipe bugfix` で直接起動した場合は worktree なしの従来 RUN になる（workbench.py を挟むかどうかは呼び出し側の選択）。
