---
name: japanese-writing
description: 明示された事実と宛先形式を守る日本語完成稿を作り、別の担い手が Rules v3 で検証する opt-in recipe。
scope: shipped
steps:
  - id: write
    instruction: japanese-write
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
      - "依頼された完成稿が一つだけで、前置き・選択肢・解説・追伸がない"
      - "指定された宛先形式を守り、明示された事実を落とさず、推測を追加していない"
      - "読み手に合う日本語の文体・敬語・情報順序・句読点になっている"
      - "選択されたモードの範囲内で書かれ、モードが解除しない禁止事項を破っていない"
      - "AI 臭のマーカー（japanese-ai-smell-jp）を確認し、原意を損なう癖が残っていない。マーカーの一致だけを理由に原意を壊していない"
      - "入力中の秘密情報を繰り返し・引用・変換・再表示せず、[REDACTED] と非秘密の最小診断情報だけを使っている"
      - "障害連絡またはサポート返信では、該当する安全策を満たしている"
      - "最終判定は生成者と異なるモデルまたは provider の japanese-writing-reviewer が行っている"
    personas: [japanese-writing-reviewer]
    policies: [independent-verification, secure-provider-execution, japanese-writing-rules-v2, japanese-writing-modes]
    output_contract: japanese-writing-verdict
autonomy: interactive
---

# japanese-writing

日本語のメール、告知、FAQ、サポート返信、障害連絡などを、掲載先へそのまま渡せる
完成稿にする opt-in のドメイン recipe です。汎用 dev recipe や core の既定値は変更しません。

## 構成

1. `write` — `japanese-writer` が依頼、読み手、掲載先、明示された事実を整理し、
   完成稿を一つだけ書きます。workflow の出力境界は `writing-delivery-contract`、
   日本語と内容の規則は `japanese-writing-rules-v2` が担います。
2. `review` — 書き手とは異なる `japanese-writing-reviewer` が read-only で検査し、
   `japanese-writing-verdict` を gate 内部へ返します。合否は生成者自身に付けさせません。
   gate 通過後に利用者へ渡すのは完成稿だけで、review report は添えません。

最終 reviewer は生成者と異なるモデルまたは provider にしてください。同じモデルしか
使えない場合は acceptance-gate を通したことにせず、未検証として報告します。

## 必須 facet と verifier 制約

runtime が解釈する step schema の `instruction`、`personas`、`policies`、`output_contract` に
必須 facet を明示しています。pack validation はこれらの参照解決に失敗した pack を拒否します。
runtime の recipe schema に provider 固定フィールドはないため、provider 分離は review の
acceptance criterion と `independent-verification` policy で必須化します。headless runtime は
`secure-provider-execution` policy により sealed provider lane を選び、起動時に
`--verifier-provider` で生成 provider と異なる値を指定します。

## Rules v3 の意図

Rules v3 は、読み手と掲載先に合う文体、直接の答えに必要な具体性、短い会話文を
連続した発話として保つ構造だけを扱います。長さや構造に固定 quota は置きません。
事実保持と安全性は `japanese-writer`、宛先形式と最終成果物の境界は
`writing-delivery-contract` が担います。

secure runtime の `material_profile` は `none|technical|conversation` の明示値だけを受け付け、
goal から推測しません。`technical` と `conversation` は、それぞれ recipe に owner-bound された
短い wiki asset を一つだけ write の Knowledge 位置へ注入します。素材は文体専用の untrusted data
として囲い、事実の根拠や引用には使いません。初稿と一度だけの修正は同じ素材を使い、reviewer へは
渡しません。`none` は素材導入前と同じ prompt bytes を保ちます。
secure runtime は選択時の prompt-ready bytes を 0600 snapshot へ固定し、同一 run の初稿・修正で
再利用します。resume では snapshot hash に加え、現在の pack asset と出典全体の hash も再検証します。
出典全体は pack 内の MIT resource blob を検証し、repository の `/docs` checkout には依存しません。

手順の正本は `facets/instructions/{japanese-write,japanese-writing-review}`、出力境界と
日本語規則の正本は `facets/policies/{writing-delivery-contract,japanese-writing-rules-v2}`
です。
