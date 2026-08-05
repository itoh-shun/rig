---
name: debug
description: バグ調査から修正・検証まで通す最短デバッグフロー（reproduce → isolate → implement → verify）
scope: shipped
autonomy: interactive
steps:
  - id: reproduce
    instruction: intake
    pattern: serial
    personas: [debugger, orchestrator]
    policies: [branch-strategy]
  - id: isolate
    instruction: implement
    pattern: serial
    personas: [debugger]
    policies: [risk-based-testing]
  - id: implement
    instruction: implement
    pattern: serial
    personas: [implementer]
    policies: [risk-based-testing, ci-cost]
  - id: verify
    instruction: verify
    pattern: serial
    gate: acceptance-gate
    acceptance:
      - 再現ステップがエラーなく通ること
      - 既存テスト（lint/type/unit）がグリーンであること
    max_retries: 2
    personas: [implementer]
    policies: [risk-based-testing, ci-cost]
---

# debug

## 使う場面

バグの原因が不明確なとき。`/rig:duck` は質問だけで実装しないため、「調査から修正・検証まで一気通貫でやりたい」場合に使う。

| recipe | 特徴 |
|---|---|
| `duck` | Socratic 問答のみ・実装しない（本人が気づく） |
| `debug` | 再現確認 → 根本原因特定 → 実装 → 検証の4段フロー |
| `hotfix` | 緊急修正・速度最優先（reproduce/isolate フェーズなし） |

## 展開手順

1. **reproduce** — バグの症状・環境・再現手順を確定する（intake 委譲）。再現できない限り次へ進まない。
2. **isolate** — 根本原因を最小ケースに絞り込む。仮説を列挙し、最も蓋然性の高い1つを選択する（debugger persona）。
3. **implement** — 選択した仮説に基づき修正を実装する（implementer persona）。
4. **verify** — 再現ステップが解消されること＋既存テストがグリーンであることを `acceptance-gate` で機械的に保証する（`max_retries: 2`）。

## isolate step の役割

`hotfix` にはない独自の段階。コードを即実装に移ると「仮説を検証せずに当てずっぽうで修正」するリスクがある。isolate では `implement` 命令を読解・仮説列挙モードで使い、根本原因を明文化してから実装に進む（実際のコード変更は行わない）。

## size-aware との関係

デバッグはファイル変更量ではなくバグの性質によって難度が決まるため、size-aware の自動 OFF は適用しない。全段 ON が既定。
