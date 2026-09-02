# instruction: pack-present

宣言と検査が走ったあと、**人が承認するのに要るものだけを並べて止まる** step です。
reviewer は `pack-draft-reviewer`、出力は `pack-author-report` の形です。

## 手順

1. 直前の `checks` の出力（`pack sync` / `validate` / `doctor` / `test`）を読みます。
   走っていない、または失敗しているものは、その行を引いてそのまま書きます。
2. `pack test` の結果を分類します。計測されたなら数字を、`structural_only` なら
   「provider が無く未計測」と書きます。どちらでもない失敗は失敗と書きます。
3. 起草した asset と材料の対応表を、draft step の出力から引き写します。
4. 人に決めてもらう欄（`reviewed_at`、`owner`、type の妥当性、矛盾した材料のどちらを
   採るか）を列挙します。
5. 次に人が打つコマンドを、そのまま書きます。

```
rig-wb eval promote --into <pack dir> <evidence>    # 計測済み evidence があるとき
rig-wb pack install <pack dir> --scope project      # 承認したあと
```

## ガード

- ここで install も promote もしません。
- `structural_only` を「検証済み」と言い換えません。
- reviewer の判定を書き手が書きません。`pack-draft-reviewer` が gate の内側で返します。
