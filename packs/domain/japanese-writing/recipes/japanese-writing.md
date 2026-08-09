---
name: japanese-writing
description: 明示された事実と宛先形式を守る日本語完成稿を作り、別の担い手が Rules v2.4 で検証する opt-in recipe。
scope: shipped
steps:
  - id: write
    instruction: japanese-write
    pattern: serial
    personas: [japanese-writer]
    policies: [writing-delivery-contract, japanese-writing-rules-v2]
  - id: review
    instruction: japanese-writing-review
    pattern: serial
    gate: acceptance-gate
    acceptance:
      - "依頼された完成稿が一つだけで、前置き・選択肢・解説・追伸がない"
      - "指定された宛先形式を守り、明示された事実を落とさず、推測を追加していない"
      - "読み手に合う日本語の文体・敬語・情報順序・句読点になっている"
      - "入力中の秘密情報を繰り返し・引用・変換・再表示せず、[REDACTED] と非秘密の最小診断情報だけを使っている"
      - "障害連絡またはサポート返信では、該当する安全策を満たしている"
      - "最終判定は生成者と異なるモデルまたは provider の japanese-writing-reviewer が行っている"
    personas: [japanese-writing-reviewer]
    policies: [independent-verification, secure-provider-execution, japanese-writing-rules-v2]
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

## Rules v2.4 の意図

Rules v2.4 は、文章の人間らしさを検出器の点数や語数制限で作る規則ではありません。
実測で効いた失敗境界を、事実保持、宛先形式、敬語、文の焦点、情報順序、句読点、
障害連絡・サポート返信の安全策として明文化したものです。固定文字数、文数、句読点数、
検出器回避、同一モデルの自己採点は採用しません。

手順の正本は `facets/instructions/{japanese-write,japanese-writing-review}`、出力境界と
日本語規則の正本は `facets/policies/{writing-delivery-contract,japanese-writing-rules-v2}`
です。
