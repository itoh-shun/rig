# チェックアウト画面 デザイン仕様

予約確定の最終画面。関連 Issue は #48210。

## 目次

- [使用トークン](#使用トークン)
- [構成](#構成)

## 使用トークン

| 用途 | 参照 |
|---|---|
| 背景 | `token(color.surface)` |
| 本文 | `token(color.text-body)` |
| 補足文 | `token(color.text-muted)` |
| 主操作の背景 | `token(color.brand-primary)` |
| 主操作のホバー | `token(color.brand-primary-hover)` |
| セクション間 | `token(spacing.lg)` |
| カードの角丸 | `token(radius.card)` |
| 書体 | `token(font.body)` |

## 構成

上から順に、予約内容の要約・支払い方法・確定操作の 3 ブロック。
確定操作は `<Button>` を使い、画面の最下部に固定する。要約は `<Card>` で囲む。

## 実装の断片

```css
.checkout__confirm {
  background: var(--color-brand-primary);
  padding: var(--spacing-md);
  border-radius: var(--radius-card);
}
```

## 履歴

配色の見直しはコミット a1b2c3d で入った。
