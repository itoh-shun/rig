---
name: max-bugfix
description: もっとも堅いバグ修正フロー（inspect→reproduce→plan→implement→test→review-diff→acceptance）。通常の bugfix をベースに、implement/test を計算的センサーで縛って no-op を許さない。
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
    checks:
      - "test -n \"$(git diff --name-only -- '*.py')\""
      - "git diff --check"
    personas: [implementer]
    policies: [risk-based-testing, ci-cost]
  - id: test
    instruction: verify
    pattern: serial
    checks:
      - "python3 -m pytest --tb=no -q"
    personas: [implementer]
    policies: [risk-based-testing, ci-cost]
  - id: review-diff
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
    checks:
      - "test -n \"$(git diff --name-only -- '*.py')\""
      - "git diff --check"
      - "python3 -m pytest --tb=no -q"
    acceptance:
      - "task_intent_satisfied — 依頼の意図が満たされている"
      - "no_unrelated_diff — 依頼と無関係な差分が含まれていない"
      - "diff_summary_written — diff.md に差分の要約が書かれている"
      - "risk_summary_written — リスクサマリが書かれている"
      - "tests_pass_or_explained — テストが green か、失敗の合理的説明がある"
      - "no_type_errors_or_explained — 型エラーがないか、あれば説明がある"
      - "no_secret_leak — secret の混入がない"
      - "no_destructive_operation — 破壊的操作を含まない"
      - "bug_cause_identified — 原因を特定した"
      - "fix_is_minimal — 修正が最小限である"
      - "regression_test_added_or_explained — 回帰テストを追加したか、不要な理由を説明した"
      - "existing_behavior_preserved — 既存の正常系挙動を壊していない"
      - "no_unrelated_refactor — 依頼にない広範なリファクタが混ざっていない"
    personas: [implementer]
---

# max-bugfix

## 使う場面

`bugfix` の形を保ちながら、実装の空振りとテスト未実行を確実に弾きたいときの強い既定。`fast-bugfix` より遅いが、検証可能性と再現性を優先する。

## `bugfix` との差分

- `implement`: diff と `git diff --check` を強制
- `test`: `pytest` を強制
- `review-diff`: 通常の bugfix と同じ 3-way review
- `acceptance`: 13 基準を維持しつつ、diff/whitespace/test を機械チェックして `max_retries: 2`

## 使い分け

- `fast-bugfix`: 小粒・低リスク・速度重視
- `max-bugfix`: 小粒でも確実性を最優先
- `bugfix`: 標準パス
