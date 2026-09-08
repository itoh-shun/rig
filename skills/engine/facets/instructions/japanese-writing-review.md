# instruction: japanese-writing-review

`japanese-write` の成果物を独立検証する routing です。書き手とは別の
`japanese-writing-reviewer` を read-only で起動し、`japanese-writing-verdict` の判定だけを
acceptance-gate に渡します。

## 手順

1. 元の依頼、明示された事実、掲載先の指定、完成稿を reviewer に渡します。
2. **機械プリパス（任意）**：完成稿を `rig-wb ja-lint -` に stdin で渡し、報告を reviewer への
   入力に添えます。本文を shell の引数や一時ファイルに書かず、stdin で一度だけ渡します。
   規則は `japanese-textlint-rules` にあります。これは**書き方の規約**の根拠であって判定では
   ありません。error（一文の長さ、読点の数、二重否定、冗長表現、誤用）は reviewer が
   `readability` のアンカーに使えます。warning は近似なので、一致だけを理由に REVISE しません。
3. reviewer は `japanese-writing-rules-v2` の各境界を入力と完成稿の具体的な箇所へ
   アンカーして検査します。秘密情報の値はアンカーへ引用せず、「入力中の秘密情報」のように
   指し示します。文章全体の代筆はさせません。
4. 生成者と異なるモデルまたは provider の reviewer を使います。同じモデルしか使えない
   場合は `UNVERIFIED` とし、acceptance-gate を通しません。
5. `REVISE` なら、根拠と最小の修正条件だけを `japanese-write` へ返します。修正後は別の
   reviewer process で再検証します。
6. verdict は gate 内部の記録です。`APPROVE` 後に利用者へ返すのは検証済みの完成稿だけとし、
   verdict、採点、修正履歴を完成稿の前後へ付けません。

## ガード

- 同一モデルによる「書いた直後の自己レビュー」を最終判定に使いません。
- detector の点数や「AI らしさ」を合否条件にしません。`ja-lint` の warning 件数も同様です。
- 入力にない正解を reviewer 自身が発明しません。確認不能は `UNKNOWN` とします。
