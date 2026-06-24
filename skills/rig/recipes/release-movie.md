---
name: release-movie
description: CHANGELOG/リリースノートから、ソフトウェアリリースの短いトレーラーを作る recipe。制作台本(絵コンテ/VO/テロップ/尺/BGMキュー/ソース対応表)＋再生できるアニメ HTML トレーラーの2点を生成。ハイプだが嘘なし。
scope: shipped
steps:
  - id: storyboard
    instruction: release-movie
    pattern: serial
    personas: [release-director]
  - id: trailer
    instruction: release-movie
    pattern: serial
    personas: [release-director]
autonomy: interactive
---

# release-movie

> **モード pack 注記**: rig engine（`SKILL.md`）を共用する humor/marketing pack の recipe。engine は書き換えず、`release-director` persona と `release-movie` instruction を足すだけで成立する。`/rig:movie` から起動。

> ⚠️ harness は**実動画をレンダリングしない**。納品は「制作台本（実編集に渡せる）」＋「ブラウザで再生できるアニメ HTML トレーラー（実物）」。実 mp4 が要るなら台本を After Effects / CapCut 等へ。

## 使う場面

リリースを**かっこよく見せたい**時。CHANGELOG はあるけど、それを 60 秒のトレーラーや告知映像にしたい。例:

- 「v0.30.0 のリリーストレーラーを作って」
- 「この機能リリースの告知ムービーの台本と、再生できるデモが欲しい」

## なにを作るか（2 点・両方フル）

1. **制作台本（絵コンテ）** — ログライン／シーン表（尺・映像・テロップ・VO・BGM/SE）／CTA／**ソース対応表**（各ビートが CHANGELOG のどの項目かを示す＝誇張防止）。実動画編集にそのまま渡せる。
2. **アニメ HTML トレーラー** — `web/release-trailer.html` の SCENES を台本に合わせて埋めた、**ブラウザで再生できる実物**（タイトル→機能リビール→クライマックス→CTA、再生/停止・前後・リプレイ・任意 BGM）。

## 展開

1. **素材の収集** — 既定は CHANGELOG 最新エントリ（バージョン指定可）。`plugin.json`・直近 git ログ・README 要点で補助。長文は subagent へ（context-minimal）。
2. **目玉の決定** — 今回の一番の価値を 1 つ選びクライマックスに置く。
3. **storyboard** — `release-director` が制作台本を生成（ハイプだが嘘なし・ソース対応表つき）。
4. **trailer** — 同 persona が `web/release-trailer.html` の SCENES を台本に合わせて埋める（プレイヤーは既存・データだけ書く）。

手順本体は `facets/instructions/release-movie`、演出は `release-director` に従う。

## ガード

- **実際に動いている画面ショットを最低 1 つ必ず入れる（必須）**。文字・ロゴだけにしない。実録が無ければ**モック**（HTML の `type:"screen"` 端末再現／台本の「画面収録」ショット）で代替。モックでも実機能の実出力に揃える（捏造画面を作らない）。
- **各ビートを実機能に紐づける**（ソース対応表必須・捏造機能を作らない）。
- **空ワード禁止**（「革命的」「次世代」等）。具体・数字・before→after で熱を作る。
- 目玉は 1 つ・テロップは 1 行。コピーは `/rig:dev --recipe de-ai-smell` で仕上げ可（任意）。
