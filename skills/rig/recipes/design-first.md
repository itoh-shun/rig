---
name: design-first
description: 設計を前面に出すフロー。design（grill 強）→implement→verify→review→pr→merge。仕様が曖昧な機能追加・新規コンポーネント設計に最適。
scope: shipped
steps:
  - id: intake
    instruction: intake
    pattern: serial
    personas: [orchestrator]
    policies: [branch-strategy]
  - id: design
    instruction: design
    pattern: serial
    personas: [orchestrator, implementer, design-reviewer]
    policies: [risk-based-testing]
  - id: implement
    instruction: implement
    pattern: serial
    personas: [implementer]
    policies: [risk-based-testing, ci-cost]
  - id: verify
    instruction: verify
    pattern: serial
    personas: [implementer]
    policies: [risk-based-testing, ci-cost]
  - id: review
    instruction: parallel-review
    pattern: parallel-fanout
    gate: review-gate
    personas: [security-reviewer, design-reviewer, test-reviewer]
    policies: [pre-push-review]
    output_contract: review-verdict
  - id: pr
    instruction: pr
    pattern: serial
    personas: [orchestrator]
    policies: [pr-hygiene, branch-strategy]
  - id: merge
    instruction: merge
    pattern: serial
    personas: [orchestrator]
    policies: [branch-strategy, ci-cost]
autonomy: interactive
---

# design-first

## 使う場面

仕様が曖昧・インターフェース設計が重要・新規コンポーネントや新機能追加など、**設計の品質が実装コストを左右する**場面。`design` ステップを強く前置きし、合意が取れてから実装に入る。

## design ステップの強化点

通常の `release-flow` と比べて design ステップが強化されている。

- `design-reviewer` ペルソナを設計フェーズに参加させ、合意前に設計品質を grilling する。
- 設計ドキュメントへのユーザー承認（インタラクティブ確認）を経てから次ステップへ進む。
- `risk-based-testing` ポリシーにより、設計段階でテスト戦略も決定する。

## 展開手順

1. **intake** — 依頼の「何を／なぜ／どこ／どこまで」を確定する。
2. **design** — 設計ドキュメントを作成する。`design-reviewer` が設計を grilling し、ユーザーが承認するまで設計フェーズを反復する。承認後に次ステップへ進む。
3. **implement** — 承認済み設計に従って実装する。
4. **verify** — ビルド・lint・テストを実行し、変更が壊れていないことを確認する。
5. **review** — security / design / test を `parallel-fanout` で並列評価し、`review-gate` で集約する。REJECT があれば停止しユーザーへ報告する。
6. **pr** — `pr-hygiene` / `branch-strategy` に従い push してプルリクエストを開く。
7. **merge** — CI 通過を確認してマージし後片付けを行う。
