# instruction: japanese-lint-review

報告ファイルと差分を突き合わせ、受け入れてよいかだけを判定する step です。判定は
`japanese-lint-reviewer` へ委譲します。

## 手順

1. `.rig/ja-lint-report.json` を読み、`status` が `checked` で `summary.errors` が 0 で
   あることを確かめます。`unchecked` なら `UNVERIFIED` です。
2. 差分を読み、直した箇所が書き方だけであることを確かめます。数値、固有名詞、日時、
   条件、手順が変わっていれば `REVISE` です。
3. 短くなった文が、分かれたのか削られたのかを確かめます。
4. 設定の差分を読み、`false` / `allow` / `ignore` / `groups` に入ったものに理由が
   あるかを確かめます。
5. 直さなかった warning に理由があるかを確かめます。
6. `japanese-lint-verdict` の形で判定だけを返します。

## ガード

- 文書を直しません。read-only です。
- 文体、構成、事実関係の良し悪しを指摘しません。範囲を広げません。
- 印象を根拠にしません。根拠は報告ファイルの行と差分の行です。
- 直した担い手と同じモデルしか使えないときは `UNVERIFIED` とし、gate を通しません。
