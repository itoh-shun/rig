# instruction: update-docs

**feature 専用の条件付き step。** 実装がユーザー向け挙動・公開 API・設定項目を変えた場合に限り、関連ドキュメント（README / CHANGELOG / 該当 docs）を実装と同じ diff 内で更新する。

## 手順

### ① 発動判定

`implement` step の diff を見て、以下のいずれかに該当するか確認する：

- 新しいコマンド・フラグ・設定項目・公開 API を追加/変更した
- 既存の使い方（手順・前提条件）が変わった
- README/CHANGELOG に記載済みの挙動と矛盾する変更をした

**いずれにも該当しない場合はこの step をスキップする**（無関係なドキュメント整形をしない＝`no_unrelated_diff` 基準を汚さない）。

### ② 更新

該当する箇所だけを最小限で更新する。既存の構成・トーンを踏襲する（`facets/instructions/docs-draft` と同じ執筆規律。新規ドキュメントの起草が必要な規模なら `recipes/documentation` を別途走らせるよう提案し、本 step では行わない）。

### ③ 引き継ぎ

更新有無と対象ファイルを `acceptance-check` step の `public_api_changes_documented` 判定の根拠として渡す。
