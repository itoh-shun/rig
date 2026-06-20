---
name: adversarial-review
description: 敵対的レビューだけを回す。AI の癖排除・人間可読性・不要コメント除去を lazy-senior / cognitive-economist の2ペルソナで厳しく見る。
scope: shipped
steps:
  - id: adversarial
    instruction: adversarial-review
    pattern: parallel-fanout
    gate: acceptance-gate
    acceptance: ["AI-slop（自明コメント / 過剰防御 / 汎用命名 / 過抽象 / dead code）の指摘が無い", "人間可読性に REJECT が無い"]
    personas: [lazy-senior, cognitive-economist]
    output_contract: review-verdict
autonomy: interactive
---

# adversarial-review

## 使う場面

実装が済んだコードに「**AI の癖が残っていないか / 人間が最小の脳負荷で読めるか / 不要なコメント・dead code が無いか**」を厳しく見たい時。`--adversarial` 相当を recipe で固定して回す。

## 展開

1. 変更を収集する。
2. `lazy-senior`（怠惰な優秀シニア＝削除バイアス）と `cognitive-economist`（思考節約＝可読性）を `parallel-fanout` で並列起動。ai-quirks 知識を効かせて AI-slop を体系的に検出する。
3. `acceptance-gate` で「AI-slop 指摘 0・可読・不要コメント無し」へ収束させる（未達なら指摘反映で再走、収束しなければユーザーへ）。
