---
name: fast-bugfix
description: 小粒バグ修正向けの軽量フロー（implement→test→acceptance）。素の Codex/Claude に近い速度で、最小限の gate と漏れ検出だけ残す。
scope: shipped
autonomy: interactive
steps:
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
  - id: acceptance
    instruction: acceptance-check
    pattern: serial
    gate: acceptance-gate
    max_retries: 1
    acceptance:
      - "task_intent_satisfied — 依頼の意図が満たされている"
      - "no_unrelated_diff — 依頼と無関係な差分が含まれていない"
      - "tests_pass_or_explained — テストが green か、失敗の合理的説明がある"
      - "no_secret_leak — secret の混入がない"
      - "fix_is_minimal — 修正が最小限である"
      - "existing_behavior_preserved — 既存の正常系挙動を壊していない"
    personas: [implementer]
---

# fast-bugfix

## 使う場面

既に問題が小さく、再現手順や設計が入力から十分に明らかなバグ修正。`bugfix` の
`inspect` / `reproduce` / `plan` / `review-diff` を省略し、素の LLM に近い速度で
最小限の品質ゲートだけ残す。

## 通常 `bugfix` との違い

| recipe | 使いどころ | step |
|---|---|---|
| `fast-bugfix` | 小粒・低リスク・ベンチ比較 | implement → test → acceptance |
| `bugfix` | 通常のバグ修正 | inspect → reproduce → plan → implement → test → review-diff → acceptance |
| `hotfix` | 本番障害などの緊急 PR 化 | intake → implement → verify → pr |

## 注意

`review-diff` を省略するため、セキュリティ・設計・テスト観点の独立レビューは入らない。
リスクが高い変更、原因が曖昧な変更、外部入力・認可・データ破壊を含む変更では
通常の `bugfix` または `debug` を使う。
