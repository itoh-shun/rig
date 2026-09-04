---
name: japanese-writing-revision
description: 既存下書きを事実を増やさずに修正し、元ファイルを上書きせず独立検証済みの別成果物を返す opt-in recipe。
scope: shipped
steps:
  - id: write
    instruction: japanese-revise-draft
    pattern: serial
    personas: [japanese-writer]
    policies: [writing-delivery-contract, japanese-writing-rules-v2, japanese-writing-modes]
    material_profiles:
      technical:
        inject: ["[[japanese-style-material-technical]]"]
      conversation:
        inject: ["[[japanese-style-material-conversation]]"]
  - id: review
    instruction: japanese-writing-review
    pattern: serial
    gate: acceptance-gate
    acceptance:
      - "下書きに明示された事実・条件・否定を保持し、入力にない前提を追加していない"
      - "選択されたモードの範囲内で修正され、モードが解除しない禁止事項を破っていない"
      - "AI 臭のマーカー（japanese-ai-smell-jp）を確認し、原意を損なう癖が残っていない。マーカーの一致だけを理由に原意を壊していない"
      - "秘密情報を再表示せず、修正済みの完成稿を一つだけ返している"
      - "元の下書きとは別の成果物として渡され、source file を編集・上書きしていない"
      - "最終判定は生成者と異なるモデルまたは provider の japanese-writing-reviewer が行っている"
    personas: [japanese-writing-reviewer]
    policies: [independent-verification, secure-provider-execution, japanese-writing-rules-v2, japanese-writing-modes]
    output_contract: japanese-writing-verdict
autonomy: interactive
---

# japanese-writing revision

既存の日本語下書きを opt-in で修正する recipe です。通常の会話や通常の
`japanese-writing` recipe の挙動は変更しません。

下書き本文は `--goal-stdin` から一度だけ読み、run-state へ永続化せず、生成 provider と
review provider の stdin へ各 canonical composer が untrusted data として囲って渡します。
review は下書きと修正版を照合して事実保持と推測なしを検査します。修正版は元ファイルと異なる
owner-only output に保存します。category と material profile は本文から推測せず、呼び出し時に明示します。

書き手は既存の `japanese-writer`、検証は既存の strict JSON contract を使う
`japanese-writing-reviewer` です。semantic rewrite は最大一回で、二度目の `REVISE`、parser invalid
exhaustion、provider/pin failure は未検証の成果物へ downgrade せず停止します。
