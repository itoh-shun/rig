---
description: rig/movie — 動画を作る映像作家ハーネス。HTML 即プレビュー（HyperFrames）／Remotion／DaVinci／AviUtl を target で選べる。既定は実装中のプロジェクトのデモ動画を HyperFrames で。--release で CHANGELOG からのリリーストレーラー。ハイプだが嘘なし。
argument-hint: ["何の動画か（省略時はこのプロジェクト全体）"] [--target hyperframes|remotion|davinci|aviutl] [--release [バージョン]] [--length 30s|60s|90s] [--aspect 16:9|9:16|1:1] [--plan]
---

# rig/movie — 動画を作る映像作家 🎬

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。

> ⚠️ harness は **render を行わない**（target を問わず）。納品は「制作台本」＋「target に応じた実物」。実 MP4 はユーザー環境で render（hyperframes は Node22+/FFmpeg/Chrome、remotion は Node、davinci/aviutl はそれぞれのアプリ）。

起動後、`--recipe movie` を既定として次の引数を PARSE する（`--release` 指定時は `release-movie` に自動切替）:

```
$ARGUMENTS
```

## 主目的：動画作成の汎用ハーネス

`/rig:movie` は**リリーストレーラー専用ではない**。`video-director` persona と `video-direct` instruction を核に、target を切り替えるだけで HTML（HyperFrames）/ Remotion / DaVinci / AviUtl のいずれかの実体まで生成する。

- 引数で「何の動画か」を渡せる（例: `"認証フローの実装デモ"`、`"会社紹介 30 秒"`、`"このライブラリの使い方"`）。省略時は**プロジェクト全体**（何を作っているか → 実際に動く様子 → 開発体験）を対象にする。
- 素材は**プロジェクトそのもの**：README・マニフェスト（plugin.json / package.json 等）・主要ソース・エントリポイント＝「何を作っているか」、作業ブランチの git ログ＋作業ツリー diff＝「いま実装中の何か」、そして**実際に動かした画面**＝目玉。
- **`--release` 指定時**は CHANGELOG / リリースノートを正準ソースにした**リリーストレーラー**（`release-movie` recipe・`release-director` persona に自動切替）。

## やること

対象を `movie` recipe（`--release` 時は `release-movie` recipe）に渡す。手順本体（①意図確認 → ②素材収集 → ③目玉の決定 → ④制作台本 → ⑤ `render-<target>` 契約で実体化）は `facets/instructions/video-direct` に従う。

- **2 点フル納品**: 制作台本（絵コンテ・VO・テロップ・尺・BGM/SE・**ソース対応表**）＋ target に応じた実物。
- **実際に動いている画面ショットが必須**: 文字・ロゴだけにしない。実録が無ければモック（実機能の実出力に揃える＝捏造画面禁止）。
- **ハイプだが嘘なし**: 各ビートを**実出所**（実コード/実機能、または `--release` 時は CHANGELOG 項目）に紐づける。空ワード・捏造機能は使わない。目玉は 1 つ・テロップは 1 行。
- 実作業（ソース読解・生成）は subagent が回す（context-minimal）。長いコード/CHANGELOG を親に引き込まない。

## flag

- `--target hyperframes|remotion|davinci|aviutl` … レンダリングパイプラインを選ぶ（既定 **hyperframes**）。各 target の認証契約は `facets/instructions/render-<target>`：
  - `hyperframes`（既定）：素 HTML・Apache-2.0・OSS render・per-render 料金なし。エージェント完結に最適。
  - `remotion`：React/TS（Composition + Sequence・`useCurrentFrame()`）。React 文化のあるプロジェクトに馴染む。商用利用は公式ライセンス条件を確認。
  - `davinci`：プロ NLE。Fusion comp / Lua / Python script を素材として納品し、人間編集者が DaVinci で仕上げる前提（**stub・v0.x で契約のみ**）。
  - `aviutl`：拡張編集 `.exo` プロジェクト＋ `.anm` Lua スクリプトを納品（**stub・v0.x で契約のみ**・日本語コミュニティ向け）。
- `--release [バージョン]` … **リリーストレーラーモード**（`release-movie` recipe へ切替）。素材を CHANGELOG / リリースノート（指定バージョン、省略時は最新）に切り替える。出荷済みリリースの告知向け。
- `--hyperframes` … `--target hyperframes` の旧エイリアス（後方互換・新規には `--target hyperframes` を推奨）。
- `--length 30s|60s|90s|3min` … 尺の目安（既定 30〜60 秒）。長尺は `--target remotion` / `davinci` が向く。
- `--aspect 16:9|9:16|1:1` … 縦横比（既定 16:9）。SNS の縦動画は 9:16。
- `--plan` … 構成（尺・シーン数・目玉・target）を提示して停止（ドライラン）。

## 例

```
/rig:movie                              # このプロジェクトのデモ動画（既定 hyperframes）
/rig:movie "認証フローの実装デモ"        # いま実装中の特定機能を動かして見せる
/rig:movie --target remotion            # Remotion プロジェクトとして生成
/rig:movie "会社紹介 30 秒" --aspect 9:16 # SNS 向けの縦動画
/rig:movie --plan                       # 構成だけ先に確認
/rig:movie --release v0.30.0            # 出荷済みバージョンのリリーストレーラー
/rig:movie --release v1.0.0 --target remotion --length 90s   # 長尺ローンチフィルムを Remotion で
/rig:movie --target davinci             # DaVinci 編集者への素材納品（stub・v0.x で契約のみ）
```

target を問わず、生成した制作台本（絵コンテ・ソース対応表）は実編集者にもそのまま渡せる。`hyperframes` / `remotion` は render まで自走可能（要 Node 22+ / FFmpeg / Chrome）、`davinci` / `aviutl` は人間編集者の引き渡し前提。
