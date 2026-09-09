# 旧テーマの取り込み手順

旧テーマから移してくる CSS の断片と、置き換え先を並べる。

## 取り込む断片

```css
.legacy-highlight {
  background: #0A84FF;
}
```

## 置き換え先

| 旧 | 新 |
|---|---|
| ハイライト背景 | `token(color.brand-primary)` |
