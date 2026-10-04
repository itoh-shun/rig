---
name: share
description: メモや調査結果を 1 枚で完結する HTML にまとめ、センサーと別の reviewer を通してから Artifact として公開する opt-in recipe。
scope: shipped
autonomy: interactive
steps:
  - id: compose
    instruction: share-compose
    pattern: serial
    personas: [share-composer]
    policies: [share-page-rules]
    max_retries: 2
    checks:
      - "rig-wb share-check --report .rig/share-check-report.json share"
  - id: review
    instruction: share-review
    pattern: serial
    gate: acceptance-gate
    max_retries: 1
    acceptance:
      - "報告ファイルの status が checked で、errors が 0 である"
      - "ページの事実・数値・日付・固有名詞が、与えられた材料のどこかに出典を持つ"
      - "秘密情報・個人情報・社外に出せない識別子が、明示の許可なくページに入っていない"
      - "warning は読まれており、直さなかったものには理由がある"
      - "検査が走らなかった場合（unchecked）は合格ではなく未検査として報告されている"
    acceptance_binding:
      - unobserved
      - unobserved
      - unobserved
      - unobserved
      - unobserved
    personas: [share-page-reviewer]
    policies: [share-page-rules, independent-verification]
    output_contract: share-page-verdict
---

# share

エージェントが作った成果物を、**スマホでも読める 1 枚の HTML** にして共有するための
recipe です。置き場所は Claude の Artifact です。サーバーも独自ドメインも要りません。
汎用 dev recipe や core の既定値は変更しません。

## 構成

1. `compose` — `share-composer` が材料を読み、`share/<slug>.html` を 1 枚書きます。雛形は
   `manifests/share-page.template.html` です。step の `checks` が `rig-wb share-check` を
   走らせ、error が残っていれば step は落ちて戻ります。
2. `review` — 書き手とは別の `share-page-reviewer` が、報告ファイルとページと材料を
   突き合わせ、`share-page-verdict` を gate 内部へ返します。合否を書いた本人に付けさせません。
3. 公開 — recipe の外で、受け入れたあとの親 session が行います。Artifact tool を持つのは
   親 session だけなので、step にはしていません。手順は `commands/share` にあります。

## ホスト上で走るもの

`compose` step の checks は provider を呼ばず、ホスト上で `rig-wb share-check` を一行
実行します。対象は **`share/` 以下のすべての HTML** です。`share/` が無いか HTML が 1 枚も
無ければ、センサーは `unchecked`（exit 2）を返し、step は落ちます。走らなかったことを
合格にしないためです。

報告は `.rig/share-check-report.json` に書かれます。orchestrate の checks 実行系は stdout を
捨てるため、reviewer が根拠に引くのはこのファイルです。

## センサーが捕るもの、捕らないもの

捕るのは構文から機械的に決まるものです。外部資源の読み込み、ダークモードの定義漏れ、
埋め忘れのプレースホルダー、秘密情報の形をした文字列、alt の無い画像など。規則の一覧は
`rig_workbench/share_check.py` の docstring にあります。

捕らないのは内容です。数字が材料と合っているか、読み手にとって要らない経緯が混ざって
いないか、社外に出せない名前が入っていないか。これは reviewer が材料と突き合わせて
判定します。センサーが通ったことを、内容が正しいことの代わりにしません。

## 独立検証について

最終判定は、書き手と異なるモデルまたは provider の `share-page-reviewer` に行わせて
ください。同じモデルしか使えない場合は acceptance-gate を通したことにせず、
`UNVERIFIED` として報告します。

手順の正本は `facets/instructions/{share-compose,share-review}`、規則の正本は
`facets/policies/share-page-rules` です。
