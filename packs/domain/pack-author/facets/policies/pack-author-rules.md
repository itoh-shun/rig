# policy: Pack Author Rules v1

材料を渡された rig が pack を**起草する**ときの規則です。対象は #547 の slice 1、
つまり手元にある材料だけを扱う段階です。

## 材料は手元のものだけ

1. **URL は取りに行きません。** 材料は、作業ディレクトリにある file だけです。URL や
   `file://` を渡されたら、fetch せず、そのまま「取得は行わない。file として置いてから
   渡してほしい」と返します。rig はこれまで利用者の代わりに外へ出たことがなく、
   出るならその決定は別の issue で明示的に下されます（SSRF の面が開くため）。
2. **材料の一覧を最初に固定します。** 各 file の path と sha256 を intake の出力に残し、
   以降の step はその一覧にある file だけを読みます。途中で増やしません。

## 起草は材料に閉じる

3. **どの asset がどの材料から来たかを書きます。** persona も wiki も、末尾に
   `sources:` として材料の path を列挙します。材料に無い事実、慣行、数字を足しません。
4. 材料が矛盾していたら、**どちらかを選ばずに両方を書いて、矛盾として報告します。**
5. `knowledge:` block を書くときは `scope` / `topics` / `owner` / `evidence` /
   `reviewed_at` の 5 つを全部埋めます。`evidence` は材料の題名で、`sources` とは
   綴りません（`sources` は pack の入手元を指す別の語です）。`reviewed_at` は今日の日付を
   入れず、材料に日付があればそれを、無ければ **人に聞くべき欄として空のまま報告**します。

## 宣言と検査は道具に任せる

6. `pack.yaml` を手で書きません。asset を置いたら `rig-wb pack sync`、次に
   `pack validate` と `pack doctor` を走らせ、その出力を残します。
7. evaluation case は **draft** として書きます。`status` は `draft` で、`approved` に
   しません。承認は人の行為で、`rig-wb eval promote` を人が打ちます。
8. `pack test` を走らせ、その結果をそのまま報告します。provider が無くて
   `structural_only` だったなら、そう書きます。「動作を確認した」とは書きません。

## 終わり方

9. 最後の出力は `pack-author-report` の形で、人が承認するのに要る材料だけを並べます。
   起草した asset の一覧、材料との対応、検査の出力、計測の結果か未計測の理由、
   人に決めてもらう欄。**pack を install しません。promote しません。**
