# instruction: compare-behavior

**refactor の verify を「ビルドが通る」だけで終わらせない。** `identify-behavior-boundaries` で固定した境界に対し、実装後の挙動が一致することを確認する。`facets/instructions/verify`（build/lint/test）の後、または並行して実行する refactor 固有の追加チェック。

## 手順

### ① 境界ごとの突き合わせ

`identify-behavior-boundaries` が出した境界リストを1件ずつ確認する：

- 公開インターフェース：シグネチャが変わっていないか（意図的変更として明示されたものを除く）
- 副作用：I/O・DB・外部呼び出しの回数/順序/内容が変わっていないか
- エラー挙動：例外の型・メッセージ・リトライが変わっていないか

既存テストで担保されない境界は、可能な範囲で**リファクタ前後の出力を比較する一時スクリプト**（一時ファイル・実行後削除）で確認してよい。

### ② 差異の扱い

- 「意図的な変更」として明示されていた差異 → 想定どおりとして記録。
- 明示されていない差異を検知 → `implement` へ差し戻す（stuck-guard の対象。同じ差異が2回出たら SKILL.md §6 のエスカレーションへ）。

### ③ 結果の引き継ぎ

境界ごとの一致/不一致を `acceptance-check` step の `no_unrelated_refactor` / `implementation_matches_request` 判定の根拠として渡す。
