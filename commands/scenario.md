---
description: "rig/scenario — 短尺プロダクト動画のシナリオライターモード。脚本(フック→課題→転換→ペイオフ→CTA・VO 草案・source 対応)を書き、既存ペルソナ×知識(ai-smell-reviewer＋ai-writing-smells × sns-post-reviewer)を掛け合わせて検閲する。/rig:movie の前段。"
argument-hint: "[何の動画か（release trailer / before-after / 機能紹介・対象・尺）] [--plan]"
---

# rig/scenario — シナリオライターモード 🎬✍️

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順・知識層注入）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。

起動後、`--recipe scenario` を既定として次の引数を PARSE する:

```
$ARGUMENTS
```

引数が無ければ「何の動画か（種別・対象・尺・観客）」を一言確認する（捏造しない）。

## やること

動画シナリオを `scenario` recipe に渡す。手順本体（①目的/尺/観客の確定 →②素材収集 →③脚本執筆 →④**検閲**）は `facets/instructions/{scenario-write,scenario-vet}` に従う。

- **書く**（`scenario-writer`）: フック→課題→転換→ペイオフ→CTA のビートシート＋VO 草案＋**各ビートの source（実機能）**。show, don't tell・空ワード禁止・目玉は1つ。
- **検閲（既存ペルソナ×知識の掛け合わせ）**: `ai-smell-reviewer`（＋`ai-writing-smells` 知識）で AI 臭・空ワードを、`sns-post-reviewer` でフック強度・ブランド/炎上リスクを判定。＋ source 対応チェック（実機能の実在照合）。`acceptance-gate` で収束（未達は書き直し）。**新規 reviewer は作らず既存を掛け合わせる**。
- 通ったシナリオは **`/rig:movie`** に渡せる（`release-movie` の絵コンテ / `--hyperframes` の SCENES の設計図）。

## 作家性レンズ（任意・`--persona`）

検閲に**作家アーキタイプ**を足して、より尖った演出批評を得られる（実名・スタジオ名は使わない抽象ペルソナ）。`--persona` で投入する（複数可）:

- `--persona auteur/deconstructionist` … **解体派**：本音/痛点・形式の破壊・内面の緊張・間と削ぎ落とし・技術的執着。「よく出来た説明動画」を壊して本音を差す。
- `--persona auteur/humanist` … **人間派**：人間を中心に・素直な感情・日常の中の発見・手触り・非シニカル。「正確だが冷たい」に温かさと誠実を差す。

2つは直交（緊張×温かさ）。両方入れると magi のように相反する目で揉める。盛らない原則は共通（面白さのための誇張は禁止）。

## flag

- `--plan` … 構成（recipe）を提示して停止（ドライラン）。

## 例

```
/rig:scenario rig の before/after 紹介動画・開発者向け・60秒
/rig:scenario v0.37.0 のリリーストレーラー台本
/rig:scenario --plan                    # 構成だけ確認
```

→ 検閲済みシナリオができたら `/rig:movie`（必要なら `--hyperframes`）で映像化。
