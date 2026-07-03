# instruction: video-direct

**汎用の動画ディレクション skill（target 非依存）。** 意図 → 素材収集 → 目玉決定 → 絵コンテ（制作台本）まで。レンダリング実体（HTML/Remotion/DaVinci/AviUtl）は `facets/instructions/render-<target>` に従う。演出の作法は `video-director` persona が持つ。

`/rig:movie` の起点。`--release` 指定時は `release-movie` instruction が CHANGELOG ソースの差分を上書きする（後方互換）。

## 入力

- **対象**: 引数で渡された "何の動画か"（省略時は実装中のプロジェクト全体）。例: "認証フローの実装デモ"、"v1.0 ローンチフィルム"、"会社紹介 30 秒"、"このライブラリの使い方"。
- **target**: `--target hyperframes|remotion|davinci|aviutl`（既定 hyperframes）。レンダリングパイプラインを決める。後段の `render-<target>` instruction が該当する契約に従って実体化する。
- **長さ/比率**: `--length 30s|60s|90s|3min` 等（既定 30〜60 秒）、`--aspect 16:9|9:16|1:1`（既定 16:9）。
- **モード**: `--release [version]` で release-movie へ切替（CHANGELOG をソースに、リリーストレーラー特化）。

## 手順

1. **意図の確認** — 対象が曖昧なら短い質問で詰める（誰に／何を／どうしてほしいのか）。`--plan` 時はここで止めて構成提案だけ出す。
2. **素材の収集**（subagent で context-minimal）
   - **既定（プロジェクト動画）**: README・マニフェスト（plugin.json / package.json 等）・主要ソース／エントリポイント＝「何を作っているか」、作業ブランチの git ログ＋作業ツリー diff＝「いま実装中の何か」、そして**実際に動かした画面**。
   - **`--release` 時**: CHANGELOG / リリースノート（指定バージョン、省略時は最新）を正準ソース。手順は `release-movie` instruction に切替。
   - **任意素材**（`--asset` で渡される）: 実録 mp4・スクショ・既存ロゴ／フォント・BGM の方向指定。
3. **目玉の決定** — 一番見せたい価値を 1 つ選びクライマックスに置く（**実際に動く画面で見せられるもの**が最強）。「全部見せたい」を許さない。
4. **制作台本（絵コンテ）の作成** — 次の構造で出す:
   - **ログライン**（1 文）
   - **シーン表**: シーン番号・尺（秒）・映像（カット内容・構図）・テロップ（1 行）・VO（語り口）・BGM/SE キュー
   - **CTA**
   - **ソース対応表**: 各ビートが**実コード/実機能/実素材**のどこに紐づくか（誇張防止。`--release` 時は CHANGELOG 項目）
5. **target ごとの実体化** — `facets/instructions/render-<target>` へ引き渡し、契約どおり生成。各 target instruction が出力場所と認証契約を持つ：
   - `render-hyperframes`（既定）→ `video/<name>/index.html` 等＋ `web/<name>.html` の即プレビュー版（HyperFrames 認証契約厳守）
   - `render-remotion` → `video/<name>/` に Remotion プロジェクト（Composition + Sequence）
   - `render-davinci` → `video/<name>/` に Fusion comp / Lua / Python script ＋ STORYBOARD.md（人間編集者引き渡し）
   - `render-aviutl` → `video/<name>/` に `.exo` プロジェクト＋拡張編集スクリプト断片

## 出力

- **制作台本**（target 非依存）。実編集者にも、後段の render-* にも渡せる中間表現。
- **target に応じた実物**（HTML / Remotion / DaVinci / AviUtl）。詳細は各 render-* instruction。

## ガード

- **動いている画面ショットを最低 1 つ**（target を問わず）。文字・ロゴだけの動画にしない。実録が無ければ**モック**（実機能の実出力に揃える＝捏造画面禁止）。
- **各ビートを実出所に紐づける**（ソース対応表必須）。空ワード禁止（「革命的」「次世代」等）。コピーは `/rig:dev --recipe de-ai-smell` で仕上げ可（任意）。
- **目玉は 1 つ・テロップは 1 行**。
- **render はユーザー環境**（hyperframes は Node22+/FFmpeg/Chrome、remotion は Node、davinci/aviutl はそれぞれのアプリ）。harness では実 MP4 を出さない target もある（各 render-* の引き渡し節に明記）。

## 関連

- `facets/personas/video-director` — 演出の作法（target 非依存）。
- `facets/instructions/release-movie` — `--release` 時の差分（CHANGELOG ソース・リリーストレーラー作法）。
- `facets/instructions/render-hyperframes` ／ `render-remotion` ／ `render-davinci` ／ `render-aviutl` — 各 target の実体化契約。
- `facets/knowledge/video-grammar` — 尺/カット/間/構図/音の普遍知識（target 非依存）。
