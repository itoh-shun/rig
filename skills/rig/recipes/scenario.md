---
name: scenario
description: 短尺プロダクト動画のシナリオライターモード。脚本(フック→課題→転換→ペイオフ→CTA・VO 草案・source 対応)を書き、既存ペルソナ×知識(ai-smell-reviewer＋ai-writing-smells × sns-post-reviewer)を掛け合わせて検閲する。/rig:movie の前段。
scope: shipped
steps:
  - id: write
    instruction: scenario-write
    pattern: serial
    personas: [scenario-writer]
  - id: vet
    instruction: scenario-vet
    pattern: parallel-fanout
    gate: acceptance-gate
    acceptance:
      - "AI 臭・空ワードの指摘が無い（ai-smell-reviewer）"
      - "全ビートが実機能に対応（source 実在・誇張/捏造なし）"
      - "フックが効くと判定される（sns-post-reviewer の hook 可）"
      - "ブランド/炎上・誤認リスクが許容範囲"
    personas: [ai-smell-reviewer, sns-post-reviewer]
    output_contract: review-verdict
autonomy: interactive
---

# scenario

> **モード pack 注記**: rig engine（`SKILL.md`）を共用する scenario（シナリオライター）pack の recipe。engine は書き換えず、`scenario-writer` persona と `scenario-write`/`scenario-vet` instruction を足すだけで成立する。**検閲は新規 reviewer を作らず既存の掛け合わせ**（`ai-smell-reviewer`＋knowledge `ai-writing-smells` × `sns-post-reviewer`・`review-verdict` 共用）。`/rig:scenario` から起動し、`/rig:movie` の前段になる。

## 使う場面

`/rig:movie` でいきなり絵コンテに行く前に、**動画の物語を書いて検閲したい**時。「何を・どの順で・どんな言葉で見せるか」を固め、AI 臭・誇張・弱いフックを落としてから映像化する。例:

- 「rig の before/after 紹介動画のシナリオを書いて、検閲して」
- 「この機能のトレーラー台本を、誇張なしで」

## 展開（書く → 検閲）

1. **write**（`scenario-writer`）— 目的/尺/観客を確定 → 素材収集 → **フック→課題→転換→ペイオフ→CTA** のビートシート＋VO 草案＋**各ビートの source（実機能）**を書く。show, don't tell・空ワード禁止。
2. **vet（検閲・既存の掛け合わせ）**（`parallel-fanout` ＋ `acceptance-gate`）—
   - `ai-smell-reviewer`（＋`ai-writing-smells` 知識）= AI 臭・空ワード・テンプレ臭・過剰な煽りを検出
   - `sns-post-reviewer` = フック強度・ブランド整合・誇張/炎上/誤認リスクを判定
   - ＋ **source 対応チェック**（各ビートの実機能が実在するか照合）
   - acceptance-gate で「AI 臭なし・誇張/捏造なし・フックが効く・リスク許容」へ収束（未達は `write` へ差し戻して再走）
3. 通ったシナリオを `/rig:movie`（`release-movie` の storyboard / `hyperframes-video` の SCENES）に渡す。

手順本体は `facets/instructions/{scenario-write,scenario-vet}` に従う。

## ガード

- **検閲は既存ブリックの掛け合わせ**（新規 reviewer を作らない＝設計意図）。
- 検閲を**通すための儀式にしない**（誇張・AI 臭・弱フックが残れば差し戻す）。
- 全ビートが実機能の裏打ち（source 実在）・空ワード禁止・目玉は1つ。
