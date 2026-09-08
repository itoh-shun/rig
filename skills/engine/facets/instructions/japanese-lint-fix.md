# instruction: japanese-lint-fix

`rig-wb ja-lint` の報告を読み、日本語文書の書き方だけを直す step です。判断の規則は
`japanese-textlint-rules`、担い手は `japanese-lint-fixer` です。

## 手順

1. 設定 `<repo>/.claude/ja-textlint.json` を読みます。無ければ雛形
   `manifests/ja-textlint.template.json` を示し、`paths` に検査対象を書くよう求めます。
   値を推測して埋めません。
2. `rig-wb ja-lint --report .rig/ja-lint-report.json` を実行し、報告を読みます。
   `status` が `unchecked` なら、理由（設定の誤り、対象なし）を直してから進みます。
   本文には触りません。
3. error を一件ずつ直します。ルール名、行、直し方を短く記録します。
   - `sentence-length` / `max-ten` — 文を分けます。情報を落としません。
   - `ja-no-redundant-expression` / `ja-no-abusage` / `no-double-negative-ja` — 辞書の
     示す形に置き換えます。
   - `ja-no-mixed-period` — 「。」を補います。
   - `prh` — 宣言された表記にします。
   - `no-unmatched-pair` — 括弧を閉じます。
4. warning を読みます。直すものは直し、直さないものには理由を一行書きます。
   固有名詞は本文を変えず、設定の `allow` / `ignore` / `groups` に宣言します。
5. 同じ command をもう一度実行し、`summary.errors` が 0 であることを報告の行で確かめます。
6. 直した箇所の一覧、直さなかった warning と理由、設定を変えたならその差分を残します。

## ガード

- 明示された事実、固有名詞、数値、日時、条件、手順、引用を変えません。
- 所見を消すために文を削りません。分けます。
- 規則を `false` にするときは `_readme` に理由を書きます。理由のない無効化はしません。
- 報告に無い箇所を直しません。文章全体の書き直しはこの step の範囲外です。
- 検査が走らなかったときは、直したことにしません。未検査のまま次へ渡します。
