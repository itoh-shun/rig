---
name: review-only
description: 現在の変更に 3-way 並列レビュー(security/design/test)だけを実行するテンプレ workflow。
scope: shipped
steps:
  - id: review
    instruction: parallel-review
    pattern: parallel-fanout
    gate: review-gate
    personas: [security-reviewer, design-reviewer, test-reviewer]
    output_contract: review-verdict
autonomy: interactive
---

# review-only

> **スキーマ注記**: recipe step の完全スキーマ（`condition` / `policies` / `output_contract` 等の省略可能キーを含む）は `SKILL.md § 3.5` に定義されている。本 recipe は最小サブセットのみを使用する。

## 使う場面
実装は済んでいて、本番影響の確認だけしたい / `--only review` 相当を recipe で固定したい時。

## 展開
1. 変更収集（`git diff` / 対象ファイル列）。
2. `parallel-review` instruction に従い security/design/test を並列起動（reviewer agent 優先）。
3. `review-gate` で集約し判定を提示。REJECT があれば停止し user へ。
