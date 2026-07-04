---
name: refactor
description: workbench 既定の refactor フロー（inspect→identify_behavior_boundaries→plan→implement→test→compare_behavior→acceptance）。挙動を変えないことを機械的に確認してから acceptance-check で締める。`/rig "<責務整理・重複除去>"` から自動選択される。
scope: shipped
autonomy: interactive
steps:
  - id: inspect
    instruction: intake
    pattern: serial
    personas: [orchestrator]
    policies: [branch-strategy]
  - id: identify_behavior_boundaries
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
  - id: compare_behavior
    instruction: compare-behavior
    pattern: serial
    personas: [implementer]
  - id: acceptance
    instruction: acceptance-check
    pattern: serial
    gate: acceptance-gate
    max_retries: 2
    acceptance:
      - "no_unrelated_diff — 依頼と無関係な差分が含まれていない"
      - "tests_pass_or_reasonable_explanation — テストが green か、失敗の合理的説明がある"
      - "no_type_errors — 型エラーなし"
      - "no_lint_errors — lint エラーなし"
      - "behavior_summary_written — 挙動変更のサマリが書かれている（refactor は「変わっていない」ことの明記も可）"
      - "risk_summary_written — リスクサマリが書かれている"
      - "implementation_matches_request — リファクタが依頼スコープと一致している"
      - "tests_added_or_existing_tests_confirmed — 既存テストで挙動不変が担保されることを確認した"
      - "public_api_changes_documented — 意図的な公開 API 変更があれば説明されている"
      - "no_unrelated_refactor — 依頼スコープを超えた変更が混ざっていない"
      - "no_secret_leak — secret の混入がない"
      - "no_destructive_operation — 破壊的操作を含まない"
    personas: [implementer]
---

# refactor

## 使う場面

`/rig "<責務整理・重複除去・可読性向上の依頼>"` から `task_type: refactor` として自動選択される workbench 既定 recipe。refactor 特有のリスク——「動いているように見えて実は挙動が変わった」——を `identify_behavior_boundaries` → `compare_behavior` の対で機械的に検知する。

## 展開手順

1. **inspect** — 何を整理したいか、対象範囲を確定する。
2. **identify_behavior_boundaries** — 公開インターフェース・副作用・エラー挙動・性能特性のうち「変えてはいけない境界」を明文化する（`facets/instructions/identify-behavior-boundaries`）。これが answer key になる。
3. **plan** — 境界を守ったまま目的（責務分離・重複除去等）を達成する変更方針を立てる（コード変更前）。
4. **implement** — 内部構造を変更する。
5. **test** — build/lint/test を実行する。
6. **compare_behavior** — ②の境界リストと実装後の挙動を突き合わせ、意図しない差異がないか確認する（`facets/instructions/compare-behavior`）。
7. **acceptance** — 12基準の acceptance-check。`implementation_matches_request` は「境界を守りつつ目的を達成したか」で判定する。

## review_diff を持たない理由

`bugfix`/`feature` と異なり本 recipe は既定で `review_diff`（3-way 並列レビュー）を含まない——refactor は「挙動不変の証明」が主眼であり、`compare_behavior` がその役目を担う。設計判断のレビューが要る規模（インターフェース変更を伴う等）は `--review` フラグまたは `--persona design-reviewer` で追加できる。
