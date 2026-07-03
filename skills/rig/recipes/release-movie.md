---
name: release-movie
description: リリーストレーラー特化 recipe（movie の --release サブクラス）。素材を CHANGELOG / リリースノートに切り替え、出荷済みバージョンの告知トレーラーを作る。target は movie と同じ（既定 hyperframes・--target で切替）。ハイプだが嘘なし。
scope: shipped
extends: movie
steps:
  - id: storyboard
    instruction: release-movie
    pattern: serial
    personas: [release-director]
  - id: render
    instruction: release-movie
    pattern: serial
    personas: [release-director]
autonomy: interactive
---

# release-movie

> **movie の `--release` サブクラス**。汎用入口は [`movie`](./movie.md) ／ `/rig:movie`。`/rig:movie --release [version]` でこちらに切り替わる（後方互換）。

> ⚠️ harness は render を行わない。納品は「制作台本」＋「target に応じた実物」。movie と同じ規律。

## 使う場面

**出荷済みバージョンの告知トレーラー**を作るとき。素材は CHANGELOG / リリースノート（指定バージョン、省略時は最新）。プロジェクト全体のデモが目的なら movie 既定（`/rig:movie` 引数なし）の方が素直。

- 「v0.30.0 のリリーストレーラーを作って」
- 「直近のリリースを 30 秒で告知したい」
- 「v1.0 ローンチフィルムは長尺で／`--target remotion` で」

## movie からの差分

| 項目 | movie（汎用） | release-movie（差分） |
|---|---|---|
| 素材 | プロジェクト本体（コード / README / 動く画面 / 開発フロー） | **CHANGELOG / リリースノート**（指定バージョン） |
| ソース対応表 | 実コード / 実機能 / 実素材 | **CHANGELOG 項目**（盛り過ぎ防止） |
| 構成 | 自由（30〜180 秒） | **コールドオープン → ビルド → リビール → CTA**（リリーストレーラー型） |
| persona | `video-director`（汎用） | `release-director`（リリース演出特化・[release-director](../facets/personas/release-director.md) 参照） |
| target | 既定 hyperframes・`--target` で切替 | 同左 |

それ以外（演出の作法・ガード・納品物 2 点）は movie と同じ。`facets/knowledge/video-grammar` の普遍ノウハウも継承する。

## 展開

1. **CHANGELOG の取得** — バージョン指定（省略時は最新）。長文は subagent へ。
2. **目玉の決定** — そのバージョンの一番見せたい機能を 1 つ選びクライマックスに置く。
3. **storyboard** — `release-director` が制作台本を生成（CHANGELOG ソース対応表・ハイプだが嘘なし・コールドオープン→ビルド→リビール→CTA の構造）。
4. **render** — `--target` に対応した `render-<target>` instruction の契約で実物を生成。

手順本体は `facets/instructions/release-movie`（CHANGELOG ソースの差分手順）、target 別の契約は `facets/instructions/render-<target>`、演出は `release-director` に従う。

## ガード（movie と同じ ＋ release 特有）

- **CHANGELOG の項目に紐づける**（捏造機能を作らない）。
- **「次世代」「革命的」等の中身ゼロの煽り禁止**。具体・数字・before→after で熱を作る。
- 動いている画面ショット必須・目玉は 1 つ・テロップは 1 行（movie と同じ）。
