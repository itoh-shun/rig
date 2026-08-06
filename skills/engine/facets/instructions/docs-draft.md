# instruction: docs-draft

**documentation 専用の執筆 step。** `identify-audience` の方針に従い、対象ドキュメントを起草／改稿する。

## 手順

### ① 既存構成の確認

対象ファイル（README / CHANGELOG / docs/*.md 等）が既に存在する場合は既存の見出し構成・トーン・記法（Markdown 方言・コードブロック言語指定）を踏襲する。新規作成の場合は近い性質の既存ドキュメントを1つ参考にする。

### ② 執筆

`identify-audience` の読者像・詳細度に従い、以下を意識して書く：

- 「最初の成功体験」を機能一覧より先に見せる（README トップの場合）
- 動くコード例・コマンド例を用意する（`verify-commands` step で実行確認する前提）
- 冗長な前置き・AI 特有の定型表現（過剰な太字・箇条書きの濫用・「〜することができます」等）を避ける。必要なら `recipes/de-ai-smell` の観点（`facets/knowledge/ai-writing-smells`）を参照してセルフチェックする。

### ③ 引き継ぎ

起草した内容を `verify-commands` step へ渡す（本文中のコマンド例・コードブロックを実行確認する対象として）。
