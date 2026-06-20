# instruction: pr

レビューを通過したコードを push し、プルリクエストを開く。

## 手順

### ① pr-pre-push-review の実行

`pr-pre-push-review` スキルを起動してブランチ全体の diff をレビューさせる。未修正の問題があれば push を止め、implement へ差し戻す。

### ② push

`pr-pre-push-review` が承認したら、manifest の `base-branch` または recipe で指定されたリモートへ push する。

### ③ PR の作成

push 完了後、PR を作成する。PR のタイトル・本文・ベースブランチ・CI 設定は manifest の `pr` セクションの定義に従う。manifest に定義がない場合はデフォルト（ベースブランチ: `main`）を使う。

### ④ 結果の引き継ぎ

PR URL を出力し、merge ステップへ引き継ぐ。
