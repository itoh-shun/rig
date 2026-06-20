---
name: hotfix
description: 最短リリースパス。intake→implement→verify→pr の4ステップ。設計・レビューを省略して緊急対応に特化。
scope: shipped
steps:
  - id: intake
    instruction: intake
    pattern: serial
    personas: [orchestrator]
    policies: [branch-strategy]
  - id: implement
    instruction: implement
    pattern: serial
    personas: [implementer]
    policies: [risk-based-testing, ci-cost]
  - id: verify
    instruction: verify
    pattern: serial
    personas: [implementer]
    policies: [risk-based-testing, ci-cost]
  - id: pr
    instruction: pr
    pattern: serial
    personas: [orchestrator]
    policies: [pr-hygiene, branch-strategy]
autonomy: interactive
---

# hotfix

## 使う場面

本番障害・重大バグなど、速度を最優先したい緊急対応。設計フェーズとレビューフェーズを意図的に省略し、最短パスで PR を開くことだけを目的とする。

マージとデプロイはこのレシピのスコープ外とする（merge は手動 or 別途 `--only merge` で対処する）。

## 展開手順

1. **intake** — 障害の内容・影響範囲・修正方針を確定する。サイズは原則 S 扱い。
2. **implement** — 最小限の修正を実施する。`risk-based-testing` ポリシーに従いリグレッションリスクを判断する。
3. **verify** — ビルド・lint・最小テストを実行し、修正が壊れていないことを確認する。
4. **pr** — `pr-hygiene` / `branch-strategy` に従い push してプルリクエストを開く。

## 注意

設計・レビューを省略しているため、マージ後に通常の `release-flow --review` で追跡レビューを行うことを推奨する。
