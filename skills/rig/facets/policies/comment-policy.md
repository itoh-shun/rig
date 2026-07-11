# policy: comment-policy

## facet: policy / comment-policy

`--comment` モードで **PR に何を届けるか**を統制するポリシー。低価値 nit の洪水・既存問題の再訴訟でシグナルが埋もれるのを防ぎ、投稿1件あたりの価値を高く保つ。PR への投稿を組み立てる step のプロンプト末尾への注入を前提とする。

### severity → 投稿マッピング

| severity | 投稿 |
|---|---|
| Critical / High | **常に投稿**する（本文コメント・必須対応事項に載せる） |
| Medium / Low | **nit** として投稿する。**1レビューあたり上限 5 件**（ハードキャップ）。超過分は個別投稿せず「**+N similar**」の1行ロールアップに畳む |

### Pre-existing マーカー

対象 diff が**導入していない**（既存コードに由来する）所見には `Pre-existing:` マーカーを付け、**note** として投稿する。**REJECT / merge-block の根拠にしない**（この PR の責任範囲外。直したい場合は別 issue/PR を提案する）。

### 再レビュー収束（re-review convergence）

rig が既にレビューした PR への再レビューでは、往復ごとに指摘を収束させる:

1. **Important のみ投稿**する（Critical/High・必須対応事項）。nit は新規 push 分でも出さない。
2. 前回指摘した条件が**修正済み**なら「resolved」と明示的にマークする。言い換えての蒸し返し（re-litigation）をしない。
3. 未修正の前回指摘は再掲してよいが、同一趣旨で1回だけ（増幅しない）。

### 禁止事項

- nit 上限（5件）を超えて Medium/Low を個別投稿しない。
- Pre-existing 所見を REJECT / merge-block の根拠に使わない。
- 再レビューで修正済みの指摘を蒸し返さない。
