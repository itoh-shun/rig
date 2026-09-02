# instruction: pack-draft

固定された材料から pack の asset を起草し、道具に宣言させる step です。書き手は
`pack-author`、規則は `pack-author-rules`、道順は `[[pack-authoring-road]]` にあります。

## 手順

1. `rig-wb pack init <pack_id> --type <type> --kind domain --root <出力先>` で雛形を作ります。
   出力先は依頼にあるディレクトリ、無ければ `.rig/packs-drafts/`。
2. 材料を読み、asset を書きます。`knowledge` なら `resources/` に材料の整理版と
   `facets/knowledge/` に概念ページ、`skill` 以上なら必要な persona / instruction / recipe。
   **各 asset の末尾に出典（材料の path）を残します。**
3. 材料が pack の対象範囲を述べていれば `pack.yaml` に `knowledge:` block を書きます。
   5 欄すべてが必要で、材料から埋まらない欄は書かずに、報告の「人に決めてもらう欄」に
   回します。
4. evaluation case を `evals/cases/<id>/case.json` に **`"status": "draft"`** で書きます。
   `target_inputs` / `target_expectations` / `deterministic_checks` は、材料に書かれている
   事実から作ります。`approved` にしません。
5. 出力は「asset → 材料」の対応表です。

```
drafted:
  - asset: <relative path>      from: <material path>[, <material path>]
knowledge_block: <written|not written: <reason>>
case: <evals/cases/<id>/case.json> (draft)
ask_a_person:
  - <欄>: <なぜ材料から埋まらないか>
```

次の step の `checks` が `RIG_PACK_DIR` を読みます。この step の最後に
`export RIG_PACK_DIR=<pack dir>` に相当する値を、報告の先頭行 `pack_dir: <path>` として
残してください。

## ガード

- `pack.yaml` の `assets` / `hashes` を手で書きません。次の step の `pack sync` が書きます。
- 材料に無いことを、もっともらしく補いません。空欄は空欄のまま人に渡します。
