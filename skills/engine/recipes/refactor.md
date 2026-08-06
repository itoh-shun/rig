---
name: refactor
description: workbench 既定の refactor フロー（inspect→identify-behavior-boundaries→plan→implement→test→compare-behavior→acceptance）。挙動を変えないことを機械的に確認してから acceptance-check で締める。`/rig "<責務整理・重複除去>"` から自動選択される。
scope: shipped
autonomy: interactive
steps:
  - id: inspect
    instruction: intake
    pattern: serial
    personas: [orchestrator]
    policies: [branch-strategy]
  - id: identify-behavior-boundaries
    instruction: identify-behavior-boundaries
    pattern: serial
    personas: [implementer]
  - id: plan
    instruction: implement
    pattern: serial
    personas: [implementer]
  - id: implement
    instruction: implement
    pattern: serial
    personas: [implementer]
    policies: [risk-based-testing, ci-cost]
  - id: test
    instruction: verify
    pattern: serial
    personas: [implementer]
    policies: [risk-based-testing, ci-cost]
  - id: compare-behavior
    instruction: compare-behavior
    pattern: serial
    personas: [implementer]
  - id: acceptance
    instruction: acceptance-check
    pattern: serial
    gate: acceptance-gate
    max_retries: 2
    acceptance:
      - "task_intent_satisfied — 依頼の意図が満たされている"
      - "no_unrelated_diff — 依頼と無関係な差分が含まれていない"
      - "diff_summary_written — diff.md に差分の要約が書かれている（refactor は「挙動は変わっていない」ことの明記も可）"
      - "risk_summary_written — リスクサマリが書かれている"
      - "tests_pass_or_explained — テストが green か、失敗の合理的説明がある"
      - "no_type_errors_or_explained — 型エラーがないか、あれば説明がある"
      - "no_secret_leak — secret の混入がない"
      - "no_destructive_operation — 破壊的操作を含まない"
      - "behavior_boundaries_identified — 変えてはいけない挙動境界を特定した"
      - "no_unintended_behavior_change — 意図しない挙動変化がない"
      - "tests_confirm_behavior_preserved — テストが挙動不変を裏付けている"
      - "no_unrelated_refactor — 依頼スコープを超えたリファクタが混ざっていない"
      - "public_api_changes_documented_if_any — 意図的な公開 API 変更があれば説明されている"
    personas: [implementer]
---

# refactor

## 使う場面

`/rig "<責務整理・重複除去・可読性向上の依頼>"` から `task_type: refactor` として自動選択される workbench 既定 recipe。refactor 特有のリスク——「動いているように見えて実は挙動が変わった」——を `identify-behavior-boundaries` → `compare-behavior` の対で機械的に検知する。

## 展開手順

1. **inspect** — 何を整理したいか、対象範囲を確定する。
2. **identify-behavior-boundaries** — 公開インターフェース・副作用・エラー挙動・性能特性のうち「変えてはいけない境界」を明文化する（`facets/instructions/identify-behavior-boundaries`）。これが answer key になる。
3. **plan** — 境界を守ったまま目的（責務分離・重複除去等）を達成する変更方針を立てる（コード変更前）。
4. **implement** — 内部構造を変更する。
5. **test** — build/lint/test を実行する。
6. **compare-behavior** — ②の境界リストと実装後の挙動を突き合わせ、意図しない差異がないか確認する（`facets/instructions/compare-behavior`）。
7. **acceptance** — 13基準（standard 8 + refactor 5）の acceptance-check。`no_unintended_behavior_change`/`tests_confirm_behavior_preserved` は「境界を守りつつ目的を達成したか」で判定する。

## review-diff を持たない理由

`bugfix`/`feature` と異なり本 recipe は既定で `review-diff`（3-way 並列レビュー）を含まない——refactor は「挙動不変の証明」が主眼であり、`compare-behavior` がその役目を担う。設計判断のレビューが要る規模（インターフェース変更を伴う等）は `--review` フラグまたは `--persona design-reviewer` で追加できる。
