---
name: japanese-lint
description: 日本語文書を textlint-ja 相当のセンサーで機械的に検査し、error を直し、別の reviewer が報告と差分を突き合わせる opt-in recipe。
scope: shipped
autonomy: interactive
steps:
  - id: fix
    instruction: japanese-lint-fix
    pattern: serial
    personas: [japanese-lint-fixer]
    policies: [japanese-textlint-rules]
    max_retries: 2
    checks:
      - "rig-wb ja-lint --report .rig/ja-lint-report.json"
  - id: review
    instruction: japanese-lint-review
    pattern: serial
    gate: acceptance-gate
    max_retries: 1
    acceptance:
      - "報告ファイルの status が checked で、summary.errors が 0 である"
      - "直した箇所は書き方だけで、明示された事実・固有名詞・数値・手順が変わっていない"
      - "所見を消すために本文が削られておらず、規則が理由なく無効化されていない"
      - "warning は読まれており、直さなかったものには理由がある"
      - "検査が走らなかった場合（unchecked）は合格ではなく未検査として報告されている"
    acceptance_binding:
      - unobserved
      - unobserved
      - unobserved
      - unobserved
      - unobserved
    personas: [japanese-lint-reviewer]
    policies: [japanese-textlint-rules, independent-verification]
    output_contract: japanese-lint-verdict
---

# japanese-lint

Markdown やプレーンテキストの日本語文書を、**印象ではなく数え方で決まる規約**に合わせる
recipe です。一文の長さ、読点の数、二重否定の定型、冗長表現、誤用、ら抜き・い抜き、
敬体と常体の混在、表記ゆれ、用語。textlint-ja が JavaScript と kuromoji でやっている
検査のうち、辞書と正規表現で決まる部分を `rig-wb ja-lint` が Python 標準ライブラリだけで
再現します。汎用 dev recipe や core の既定値は変更しません。

## 構成

1. `fix` — `japanese-lint-fixer` が `rig-wb ja-lint` を走らせ、その報告を読み、error を
   **書き方だけ**直します。事実、固有名詞、数値、手順は変えません。step の `checks` が
   同じ command をもう一度走らせ、error が残っていれば step は落ちて戻ります。
2. `review` — 書き手とは別の `japanese-lint-reviewer` が、報告ファイルと差分を突き合わせ、
   `japanese-lint-verdict` を gate 内部へ返します。合否を直した本人に付けさせません。

## ホスト上で走るもの

`fix` step の checks は provider を呼ばず、ホスト上で `rig-wb ja-lint` を一行実行します。
検査対象は **`<repo>/.claude/ja-textlint.json` の `paths`** です。設定が無いか `paths` が
空なら、センサーは `unchecked`（exit 2）を返し、step は落ちます。走らなかったことを
合格にしないためです。雛形は `manifests/ja-textlint.template.json` にあります。

報告は `.rig/ja-lint-report.json` に書かれます。orchestrate の checks 実行系は stdout を
捨てるので、reviewer が読むのはこのファイルです。`.rig/` は gitignore 対象です。

## 何を直し、何を直さないか

規則の正本は `facets/policies/japanese-textlint-rules` です。要点は三つです。

- **error は直す。warning は読む。** 近似ルール（助詞の連続、ら抜き、敬体・常体）は
  warning で、偽陽性を含みます。消すために本文を歪めません。
- **固有名詞と引用は逃がしてよい。** ただし逃がし方は設定の `allow` / `ignore` に
  **宣言として**残します。規則そのものを `false` にするなら理由を `_readme` に書きます。
- **意味を変えない。** 「〜することができる」を「〜できる」にするのは書き方の変更です。
  「約 10 分ほど」の「ほど」を落とすのは幅の変更なので、「約」のほうを落とします。

## 独立検証について

最終判定は、直した担い手と異なるモデルまたは provider の `japanese-lint-reviewer` に
行わせてください。同じモデルしか使えない場合は acceptance-gate を通したことにせず、
`UNVERIFIED` として報告します。

`japanese-writing` recipe の完成稿にも同じセンサーを当てられます。そちらは reviewer の
機械プリパス（`facets/instructions/japanese-writing-review`）として、判定ではなく根拠に
使います。

手順の正本は `facets/instructions/{japanese-lint-fix,japanese-lint-review}`、規則の正本は
`facets/policies/japanese-textlint-rules` です。
