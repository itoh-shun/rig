---
description: rig/movie — リリースムービー生成。CHANGELOG/リリースノートから、短いリリーストレーラーの制作台本(絵コンテ/VO/テロップ/尺/BGMキュー/ソース対応表)＋再生できるアニメ HTML トレーラーの2点を作る。ハイプだが嘘なし。
argument-hint: [バージョン/タグ（省略時は CHANGELOG 最新）] [--plan]
---

# rig/movie — リリースムービー 🎬

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。

> ⚠️ harness は**実動画をレンダリングしない**。納品は「制作台本（実編集に渡せる）」＋「ブラウザで再生できるアニメ HTML トレーラー（実物）」。実 mp4 が要るなら台本を After Effects / CapCut 等へ。

起動後、`--recipe release-movie` を既定として次の引数を PARSE する:

```
$ARGUMENTS
```

引数が無ければ **CHANGELOG の最新エントリ**を対象にする。

## やること

対象リリース（CHANGELOG エントリ等）を `release-movie` recipe に渡す。手順本体（①素材収集 →②目玉の決定 →③制作台本 →④`web/release-trailer.html` の SCENES を埋めて再生できる HTML を生成）は `facets/instructions/release-movie` に従う。

- **2 点フル納品**: 制作台本（絵コンテ・VO・テロップ・尺・BGM/SE・**ソース対応表**）＋ アニメ HTML トレーラー（ブラウザで再生）。
- **実際に動いている画面ショットが必須**: 文字・ロゴだけにしない。実録が無ければモック（端末/UI が動く `type:"screen"` 再現）で代替するが、**実機能の実出力**に揃える（捏造画面は作らない）。目玉は動かして見せる。
- **ハイプだが嘘なし**: 各ビートを実機能（CHANGELOG）に紐づける。空ワード・捏造機能は使わない。目玉は 1 つ・テロップは 1 行。
- 実作業（素材読解・生成）は subagent が回す（context-minimal）。長い CHANGELOG を親に引き込まない。

## flag

- `--plan` … 構成（尺・シーン数・目玉）を提示して停止（ドライラン）。

## 例

```
/rig:movie                     # CHANGELOG 最新リリースのトレーラー
/rig:movie v0.30.0             # 特定バージョンのトレーラー
/rig:movie --plan              # 構成だけ先に確認
```

生成した HTML はブラウザで開くと自動再生（再生/停止＝Space、前後＝←→、リプレイ、任意 BGM）。
