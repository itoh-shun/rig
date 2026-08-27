---
name: review-only
description: 現在の変更に 4-way 並列レビュー(security/design/test/behavioral-correctness)だけを実行するテンプレ workflow。
scope: shipped
steps:
  - id: review
    instruction: parallel-review
    pattern: parallel-fanout
    gate: review-gate
    personas: [security-reviewer, design-reviewer, test-reviewer, behavioral-correctness-reviewer]
    output_contract: review-verdict
autonomy: interactive
---

# review-only

> **スキーマ注記**: recipe step の完全スキーマ（`condition` / `policies` / `output_contract` 等の省略可能キーを含む）は `SKILL.md § 3.5` に定義されている。本 recipe は最小サブセットのみを使用する。

## 使う場面
実装は済んでいて、本番影響の確認だけしたい / `--only review` 相当を recipe で固定したい時。

## 展開
1. 変更収集（`git diff` / 対象ファイル列）。
2. `parallel-review` instruction に従い security/design/test/behavioral-correctness を並列起動（reviewer agent 優先）。
3. `review-gate` で集約し判定を提示。REJECT があれば停止し user へ。

## route の証拠 owner

`security_review` route の拘束ゲートは review 5件 + security 5件の計10件。
この flow は worktree を作らないため、diff worktree を要求する
`scan-secrets` / `scan-injection` / `scan-destructive` はこの route の producer ではない。
現行の `review-verdict` と persona は10件すべてへの回答も `wb gate` への記録も
強制しないため、10件はいずれも operator が reviewer 出力を照合して明示的に記録する
（step 0 / sensor 0 / manual 10）。「reviewer が出力した」という結論だけを producer
扱いにはしない。route ごとの機械可読な正本は
`rig_workbench.workbench.capabilities.ROUTE_PRODUCERS` に置く。
