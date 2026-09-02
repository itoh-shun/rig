# instruction: pack-intake

材料を受け取り、**手元にある file の一覧として固定する** step です。規則は
`pack-author-rules` にあります。

## 手順

1. 依頼文から材料の指定を拾います。作業ディレクトリにある path だけを材料にします。
2. URL、`file://`、存在しない path があれば、**取得も推測もせず**、それぞれを「取得は
   行わない。file として置いてから渡してほしい」と一行ずつ報告します。この step は
   その報告を出して終わってよく、材料が一つも無ければ次の step へ進みません。
3. 材料ごとに path、サイズ、sha256 を記録します（`sha256sum <path>`）。
4. 作る pack の `id` と `type` を、材料と依頼から決めて書きます。`type` は材料が
   正当化する最も狭いもの（文書だけなら `knowledge`）。判断が付かなければ人に聞く欄に
   回します。
5. 出力は次の表だけです。

```
pack_id: <id>
type: <knowledge|skill|reviewer|policy|workflow|tool>
materials:
  - path: <relative path>   sha256: <hex>   bytes: <n>
refused:
  - <url or missing path>: <reason>
ask_a_person:
  - <question>
```

## ガード

- 材料の中身をここで要約しません。一覧を作るだけです。
- 材料の一覧はこの step で閉じます。あとの step が別の file を読むことは規則違反です。
