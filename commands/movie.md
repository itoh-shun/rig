---
description: rig/movie — 実装中のプロジェクトのデモ動画を作る。既定は「いま作っているプロジェクト」（コード/README/実際に動く画面/開発フロー）から、再生できるアニメ HTML を生成。--hyperframes で MP4 レンダリング可能な HyperFrames コンポジションも。--release で CHANGELOG からのリリーストレーラー。ハイプだが嘘なし。
argument-hint: ["何を見せる動画か（省略時はこのプロジェクト全体）"] [--release [バージョン]] [--hyperframes] [--plan]
---

# rig/movie — プロジェクトのデモ動画 🎬

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。

> ⚠️ harness は**実動画をレンダリングしない**。既定の納品は「制作台本」＋「ブラウザで再生できるアニメ HTML（実物）」。**`--hyperframes` 指定時**は、本物の MP4 を出せる **HyperFrames コンポジション**（HTML→決定論的 MP4・OSS）も生成する — render はユーザー環境で `npx hyperframes render`（この harness では実行しない）。

起動後、`--recipe release-movie` を既定として次の引数を PARSE する:

```
$ARGUMENTS
```

## 主目的：実装中のプロジェクトの動画化

**既定の素材は「いま実装しているプロジェクト（このリポジトリ）」**。CHANGELOG から作るのではなく、**実際のコード・README・実行して動く画面・開発（実装）フロー**を素材に、何を作っていて・どう動き・どう使うのかを見せる。

- 引数で「何を見せる動画か」を渡せる（例: `"認証フローの実装デモ"`）。省略時は**プロジェクト全体**（何を作っているか → 実際に動く様子 → 開発体験）を対象にする。
- 素材は**プロジェクトそのもの**：README・マニフェスト（plugin.json / package.json 等）・主要ソース・エントリポイント＝「何を作っているか」、作業ブランチの git ログ＋作業ツリー diff＝「いま実装中の何か」、そして**実際に動かした画面**＝目玉。
- **`--release` 指定時のみ** CHANGELOG / リリースノートを正準ソースにした**リリーストレーラー**を作る（出荷済みバージョンの告知用）。

## やること

対象（既定＝このプロジェクト／`--release` 時は CHANGELOG エントリ）を `release-movie` recipe に渡す。手順本体（①素材収集 →②目玉の決定 →③制作台本 →④`web/release-trailer.html` の SCENES を埋めて再生できる HTML を生成）は `facets/instructions/release-movie` に従う。

- **2 点フル納品**: 制作台本（絵コンテ・VO・テロップ・尺・BGM/SE・**ソース対応表**）＋ アニメ HTML（ブラウザで再生）。
- **実際に動いている画面ショットが必須**: 文字・ロゴだけにしない。実録が無ければモック（端末/UI が動く `type:"screen"` 再現）で代替するが、**実コード・実機能の実出力**に揃える（捏造画面は作らない）。目玉は動かして見せる。
- **ハイプだが嘘なし**: 各ビートを**実コード/実機能**（`--release` 時は CHANGELOG）に紐づける。空ワード・捏造機能は使わない。目玉は 1 つ・テロップは 1 行。
- 実作業（ソース読解・生成）は subagent が回す（context-minimal）。長いコード/CHANGELOG を親に引き込まない。

## flag

- `--plan` … 構成（尺・シーン数・目玉）を提示して停止（ドライラン）。
- `--release [バージョン]` … **リリーストレーラーモード**。素材を CHANGELOG / リリースノート（指定バージョン、省略時は最新）に切り替える。出荷済みリリースの告知向け。
- `--hyperframes` … **HyperFrames コンポジション**（本物の MP4 を出せる・OSS）も生成する。手順は `facets/instructions/hyperframes-video`（認証契約：`data-composition-id` / `class="clip"`＋`data-start`/`data-duration` / GSAP タイムラインを `window.__timelines` に登録＝seekable）。`video/<name>/`（index.html＋STORYBOARD.md＋README.md）として出力。同梱例: `video/launch-film/`・`video/before-after/`。render は Node22+/FFmpeg/Chrome が要り、**この harness では行わない**（ユーザー環境で `npx hyperframes preview` → `render`）。

## 例

```
/rig:movie                              # このプロジェクト全体のデモ動画（何を作り・どう動くか）
/rig:movie "認証フローの実装デモ"        # いま実装中の特定機能を動かして見せる
/rig:movie --plan                       # 構成だけ先に確認
/rig:movie --release v0.30.0            # 出荷済みバージョンのリリーストレーラー（CHANGELOG ソース）
/rig:movie --hyperframes                # MP4 を出せる HyperFrames コンポジションも生成
```

生成した HTML はブラウザで開くと自動再生（再生/停止＝Space、前後＝←→、リプレイ、任意 BGM）。`--hyperframes` 版は `npx hyperframes render` で MP4 を書き出す。
