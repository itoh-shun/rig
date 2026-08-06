# instruction: visual-verify

UI 差分を視覚的に確認する。verify ステップから `--visual` フラグまたは UI 変更の検出時に呼び出される。

## 手順

### ① 発動条件の確認

以下のいずれかを満たす場合のみ実行する：

- `--visual` フラグが指定されている
- 変更ファイルに UI コンポーネント・スタイル・テンプレートが含まれる

### ② アプリの起動確認

アプリが既に起動していない場合、manifest の `dev` または `start` コマンドを参照して起動する。

### ③ スクリーンショット取得・比較

変更前後のスクリーンショットを取得し、目視または差分ツールで確認する。確認観点：

- 意図した UI 変更が反映されているか
- 意図しないレイアウト崩れ・スタイル破損がないか
- レスポンシブ対応が必要な場合は複数ビューポートで確認する

**保存先・処分ルールは `patterns/visual-artifacts` が正本**：workbench task_id が存在する場合は `<repo>/.rig/runs/<task-id>/visual/`、無い場合（ad-hoc 実行）は `<repo>/.rig/visual/adhoc/<YYYYMMDD-HHMMSS>-<slug>/` に `verify-before.png`/`verify-after.png` として保存する。判断結果は④で散文として引き継ぐため、画像自体は discard 時に即時削除され、accept 後も既定14日で `workbench.py gc` の対象になる（恒久保存ではない）。

### ④ 結果の引き継ぎ

問題がなければ「visual-verify 済み」として pr ステップへ進む。問題がある場合は implement へ差し戻す。
