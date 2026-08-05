---
name: design-first
description: 設計を前面に出すフロー。release-flow を継承し design を強化（design-reviewer 参加・常時 grilling）、review を常時 ON にする。仕様が曖昧な機能追加・新規コンポーネント設計に最適。
scope: shipped
extends: release-flow
steps:
  - id: design
    instruction: design
    pattern: serial
    personas: [orchestrator, implementer, design-reviewer]
    policies: [risk-based-testing]
  - id: review
    instruction: parallel-review
    pattern: parallel-fanout
    gate: acceptance-gate
    acceptance: ["3-way review に REJECT が無い", "APPROVE_WITH_CONDITIONS のマージ前必須条件をすべて反映済み"]
    personas: [security-reviewer, design-reviewer, test-reviewer]
    policies: [pre-push-review]
    output_contract: review-verdict
autonomy: interactive
---

# design-first

## 使う場面

仕様が曖昧・インターフェース設計が重要・新規コンポーネントや新機能追加など、**設計の品質が実装コストを左右する**場面。`release-flow` を継承し、`design` を強く前置きして合意が取れてから実装に入る。

## release-flow との差分（extends）

`extends: release-flow`（§4.2.2）で全 step を継承し、以下 2 step だけを上書きする。intake / implement / **verify** / pr / merge は release-flow のまま継承するため、**verify の `acceptance-gate`（build 成功・lint 0・テスト green）も自動で効く**。これにより design-first 固有の gate ズレは構造的に発生しない。

- **design** — `design-reviewer` を設計フェーズに参加させ、合意前に設計品質を grilling する。`risk-based-testing` でテスト戦略も決める。release-flow と違い **condition を持たず常時 ON**。
- **review** — release-flow では size L+ / `--review` 条件付きだが、design-first では **常時 ON**。集約は `acceptance-gate`（「REJECT が無い」へ収束）。

## 展開手順

1. **intake** — 依頼の「何を／なぜ／どこ／どこまで」を確定する（継承）。
2. **design** — 設計ドキュメントを作成する。`design-reviewer` が grilling し、ユーザーが承認するまで反復してから次へ進む。
3. **implement** — 承認済み設計に従って実装する（継承）。
4. **verify** — build / lint / test を `acceptance-gate` で受け入れ基準まで収束させる（継承）。
5. **review** — security / design / test を `parallel-fanout` で並列評価し、`acceptance-gate` で「REJECT が無い」へ収束させる。
6. **pr** — `pr-hygiene` / `branch-strategy` に従い push して PR を開く（継承）。
7. **merge** — CI 通過を確認してマージし後片付けを行う（継承）。
