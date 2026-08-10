---
name: japanese-writing
description: 既存下書きを事実を増やさずに修正し、元ファイルを上書きせず独立検証済みの別成果物を返す opt-in recipe。
scope: shipped
steps:
  - id: write
    instruction: japanese-revise-draft
    pattern: serial
    personas: [japanese-writer]
    policies: [writing-delivery-contract, japanese-writing-rules-v2]
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
      - "秘密情報を再表示せず、修正済みの完成稿を一つだけ返している"
      - "元の下書きとは別の成果物として渡され、source file を編集・上書きしていない"
      - "最終判定は生成者と異なるモデルまたは provider の japanese-writing-reviewer が行っている"
    personas: [japanese-writing-reviewer]
    policies: [independent-verification, secure-provider-execution, japanese-writing-rules-v2]
    output_contract: japanese-writing-verdict
autonomy: interactive
---

# japanese-writing revision

既存の日本語下書きを opt-in で修正する recipe です。通常の会話や通常の
`japanese-writing` recipe の挙動は変更しません。

下書き本文は `--goal-stdin` から一度だけ渡し、生成 provider の stdin へ canonical composer が
untrusted data として囲って渡します。修正版は元ファイルと異なる owner-only output に保存します。
category と material profile は本文から推測せず、呼び出し時に明示します。

書き手は既存の `japanese-writer`、検証は既存の strict JSON contract を使う
`japanese-writing-reviewer` です。semantic rewrite は最大一回で、二度目の `REVISE`、parser invalid
exhaustion、provider/pin failure は未検証の成果物へ downgrade せず停止します。
