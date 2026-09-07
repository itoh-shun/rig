# 入力欄コンポーネント仕様

フォームの 1 項目を表す部品。ラベル・入力・補足・エラーの 4 要素を持つ。

## 使用トークン

| 用途 | 参照 |
|---|---|
| ラベルの文字色 | `token(color.text-body)` |
| 補足文の文字色 | `token(color.text-muted)` |
| エラーの文字色 | `token(color.danger)` |
| 要素の間 | `token(spacing.xs)` |
| 項目同士の間 | `token(spacing.sm)` |
| 書体 | `token(font.body)` |

## 構成

`<Field>` はラベルと入力を内包する。エラーは `<Toast>` ではなく項目の直下に置く。
補助的な操作を並べる場合は `<Button>` を項目の外に出す。

## 状態

| 状態 | 見え方 |
|---|---|
| 既定 | 枠線は `token(color.text-muted)` |
| フォーカス | 枠線は `token(color.brand-primary)` |
| エラー | 枠線は `token(color.danger)` |
