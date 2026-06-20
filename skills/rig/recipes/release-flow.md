---
name: release-flow
description: 標準リリースフロー。intake→design?→implement→verify→review?→pr→merge の全工程。サイズ感に応じて design / review を自動 ON/OFF。
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
    personas: [orchestrator, implementer]
    condition: "--design または size L+"
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
    condition: "--review または size L+"
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

# release-flow

## 使う場面

変更を最初から本番マージまで一気通貫で進めたい時。機能追加・バグ修正・リファクタリングなど、通常の開発サイクル全般に適用できる汎用フロー。

## サイズ感による自動調整（size-aware）

`intake` ステップで見積もったサイズに応じて、下記の条件ステップを自動 ON/OFF する。

| サイズ | design ステップ | review ステップ |
|--------|----------------|----------------|
| S（小さな修正・1ファイル未満相当） | **OFF**（既定） | **OFF**（既定） |
| M（複数ファイル・中規模変更） | **OFF**（既定） | **OFF**（既定） |
| L 以上（機能追加・設計変更を含む） | **ON** | **ON** |

- `--design` フラグを明示した場合は size 問わず design ステップが有効になる。
- `--review` フラグを明示した場合は size 問わず review ステップが有効になる。
- S/M サイズで両フラグを省略した場合の最短パスは `intake → implement → verify → pr → merge`（5ステップ）。

## 展開手順

1. **intake** — 依頼の「何を／なぜ／どこ／どこまで」を確定し、サイズを見積もる。ここで得た size が以降のステップ選択を決定する。
2. **design**（条件付き）— `--design` または size L+ の場合のみ実行。設計ドキュメントを作成し要件をピン留めする。
3. **implement** — 実装。`risk-based-testing` / `ci-cost` ポリシーに従い TDD か直接実装かを選択する。
4. **verify** — ビルド・lint・テストを実行し、変更が壊れていないことを確認する。
5. **review**（条件付き）— `--review` または size L+ の場合のみ実行。security / design / test を `parallel-fanout` で並列評価し、`review-gate` で集約する。REJECT があれば停止しユーザーへ報告する。
6. **pr** — `pr-hygiene` / `branch-strategy` に従い push してプルリクエストを開く。
7. **merge** — CI 通過を確認してマージし後片付けを行う。
