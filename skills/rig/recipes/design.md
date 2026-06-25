---
name: design
description: UI/UX・a11y を内蔵したデザイン成果物（仕様書/コンポーネント仕様/ワイヤー/a11y 計画）を生成し、UI/UX(ux-reviewer)・a11y(a11y-reviewer/WCAG)で並列検閲して収束させる。--ppt/--claudedesign で追加出力。
scope: shipped
steps:
  - id: draft
    instruction: design-draft
    pattern: serial
    personas: [design/ui-ux-designer]
  - id: vet
    instruction: design-vet
    pattern: parallel-fanout
    gate: acceptance-gate
    acceptance:
      - "UI/UX・a11y の両観点で評価済み（ux-reviewer / a11y-reviewer）"
      - "各指摘が『どこの何を・なぜ・どう直すか』分かる粒度"
      - "目標 WCAG レベル（既定 AA）未達の違反が無い、または条件として明示されている"
      - "全成果物が実在前提・誇張/捏造なし（不明は [要記入]）"
      - "総合 verdict（APPROVE/APPROVE_WITH_CONDITIONS/REJECT）が出ている"
    personas: [design/ux-reviewer, design/a11y-reviewer]
    output_contract: design-verdict
autonomy: interactive
---

# design

> **モード pack 注記**: rig engine（`SKILL.md`）を共用する design pack の recipe。engine は書き換えず、`design/{ui-ux-designer,ux-reviewer,a11y-reviewer}` persona と `design-draft`/`design-vet` instruction・`design-verdict` 契約・`a11y-wcag`/`ui-ux-heuristics` 知識を足すだけで成立する。`/rig:design` から起動する作成モード。

## 使う場面
UI/UX・a11y を最初から織り込んだデザインを作りたい時。「この機能の画面を設計して、UX と a11y を検閲して」。例:
- 「ログイン画面のデザイン仕様とコンポーネント仕様を、AA 準拠で」
- 「設定ページのワイヤーと a11y 計画を、--ppt で資料化して」

## 展開（生成 → 検閲）
1. **draft**（`design/ui-ux-designer`）— 要件確定 → 成果物生成（既存デザインスキルへ委譲）→ 出力バックエンド（既定 Markdown・`--ppt`/`--claudedesign` で追加）。a11y を設計時点で内蔵。
2. **vet（並列検閲）**（`parallel-fanout` ＋ `acceptance-gate`）—
   - `ux-reviewer`（＋`ui-ux-heuristics` 知識）= ユーザビリティ・視覚階層・IA・認知負荷・状態網羅・コピー
   - `a11y-reviewer`（＋`a11y-wcag` 知識）= WCAG 2.2 達成基準（POUR）・目標レベル（既定 AA）
   - acceptance-gate で「両観点評価済み・粒度十分・目標レベル充足 or 条件化・誇張なし・総合 verdict」へ収束（未達は `draft` へ差し戻し）。
3. 通った成果物と `design-verdict` を返す。

手順本体は `facets/instructions/{design-draft,design-vet}` に従う。

## ガード
- a11y は後付けにしない（draft 時点で内蔵し、vet で WCAG を名指し検証）。
- 検閲を通すための儀式にしない（目標レベル未達・空ワード・状態欠落が残れば差し戻す）。
- 実在前提・誇張/捏造禁止・不明は `[要記入]`。
