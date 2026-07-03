---
name: movie
description: 動画を作る汎用 recipe。HTML 即プレビュー（HyperFrames）／Remotion／DaVinci／AviUtl を target で選べる映像作家ハーネス。既定 target は hyperframes。--release で release-movie へ切替（CHANGELOG ソースのリリーストレーラー）。
scope: shipped
steps:
  - id: storyboard
    instruction: video-direct
    pattern: serial
    personas: [video-director]
  - id: render
    instruction: video-direct
    pattern: serial
    personas: [video-director]
autonomy: interactive
---

# movie

> **モード pack 注記**: rig engine（`SKILL.md`）を共用する creative pack の汎用 recipe。engine は書き換えず、`video-director` persona と `video-direct` instruction（＋ target 別 `render-<target>` instruction）を足すだけで成立する。`/rig:movie` から起動。

> ⚠️ harness は **render を行わない**（target を問わず）。納品は「制作台本」＋「target に応じた実物（HTML / Remotion プロジェクト / DaVinci 素材 / AviUtl `.exo`）」。MP4 が要るならユーザー環境で render を回す（hyperframes は Node22+/FFmpeg/Chrome、remotion は Node、davinci/aviutl はそれぞれのアプリ）。

## 使う場面

**動画を作りたい**ときの既定入口。素材は引数次第：

- 「このプロジェクトのデモ動画を作って」（何を作っていて、どう動くか）
- 「認証フローの実装デモを 30 秒で」
- 「会社紹介 60 秒・縦動画」
- 「このライブラリの使い方を 3 分で」
- 「v1.0 ローンチフィルム」
- （`--release` 時）「v0.30.0 のリリーストレーラー」← CHANGELOG ソース、release-movie へ自動切替

target は `--target hyperframes|remotion|davinci|aviutl`（既定 hyperframes）。

## なにを作るか（2 点・両方フル）

1. **制作台本（絵コンテ・target 非依存）** — ログライン／シーン表（尺・映像・テロップ・VO・BGM/SE）／CTA／**ソース対応表**（各ビートが**実出所**のどこに紐づくか＝誇張防止）。実編集者にも、後段の render-* にも渡せる中間表現。
2. **target に応じた実物**:
   - `hyperframes`（既定）→ `video/<name>/index.html` ＋ `web/<name>.html` の即プレビュー版（HyperFrames 認証契約厳守・GSAP タイムライン seekable・OSS で MP4 化可能）
   - `remotion` → `video/<name>/` に Remotion プロジェクト（Composition + Sequence・useCurrentFrame 駆動）
   - `davinci` → `video/<name>/` に Fusion comp / Lua / Python script ＋ STORYBOARD.md（人間編集者引き渡し）
   - `aviutl` → `video/<name>/aviutl/` に `.exo` ＋拡張編集スクリプト断片

## 展開

1. **意図の確認** — 対象が曖昧なら短い質問で詰める（誰に／何を／どうしてほしいのか）。`--plan` 時はここで止めて構成提案だけ出す。
2. **素材の収集** — 既定はプロジェクト本体（README / マニフェスト / 主要ソース / 作業 diff / 実際に動かした画面）。`--release` 時のみ CHANGELOG。長文・大量コードは subagent へ（context-minimal）。
3. **目玉の決定** — 一番見せたい価値を 1 つ選びクライマックスに置く（**実際に動く画面で見せられるもの**が最強）。
4. **storyboard** — `video-director` が制作台本を生成（target 非依存・ハイプだが嘘なし・ソース対応表つき）。
5. **render** — `--target` に対応した `render-<target>` instruction の契約で実物を生成。同 persona が担当。

手順本体は `facets/instructions/video-direct`、target 別の契約は `facets/instructions/render-<target>`、演出は `video-director` に従う。普遍ノウハウは `facets/knowledge/video-grammar`。

## ガード

- **実際に動いている画面ショットを最低 1 つ必ず入れる（必須・target 問わず）**。文字・ロゴだけにしない。実録が無ければ**モック**（実機能の実出力に揃える＝捏造画面禁止）。
- **各ビートを実出所に紐づける**（ソース対応表必須・捏造機能を作らない）。
- **空ワード禁止**（「革命的」「次世代」等）。具体・数字・before→after で熱を作る。
- **目玉は 1 つ・テロップは 1 行**。コピーは `/rig:dev --recipe de-ai-smell` で仕上げ可（任意）。
- **harness は render しない**（target を問わず）。各 render-<target> の引き渡し節で render 環境とライセンス条件を明示する。
