---
name: roast
description: 毒舌ロースト・レビュー。現在の変更を辛辣で笑える言い回しでレビューするが、指摘の中身は本物（AI 臭・可読性・過剰/不足・本物のバグ）。笑いで批判のエゴ防御を下げて指摘を実際に読ませる adversarial-review の変種。
scope: shipped
steps:
  - id: roast
    instruction: roast
    pattern: serial
    gate: review-gate
    personas: [roast-reviewer]
    output_contract: review-verdict
autonomy: interactive
---

# roast

> **モード pack 注記**: rig engine（`SKILL.md`）を dev / magi 等と**共用**する humor pack の recipe。engine は書き換えず、`roast-reviewer` persona と `roast` instruction を足すだけで成立する（`review-verdict` / `review-gate` は dev 共用）。`/rig:roast` から起動。

## 使う場面

実装が済んだコードを「**笑いながら、しかし本気で**」レビューしたい時。`adversarial-review`（真顔の敵対レビュー）と的は同じ（AI 臭・可読性・過剰/不足・バグ）だが、**配送をユーモアに振る**。

なぜ機能するか：「ネタだから」の枠があると、人は批判を**実際に読む**。エゴ防御を下げて指摘を届ける配送装置 — ただし**中身は本物のレビュー**で、判定・根拠・必須条件は素面で正確。

## roast と adversarial-review の違い

| | adversarial-review | roast |
|---|---|---|
| 的 | AI 臭・可読性・dead code | 同左＋本物のバグ |
| 声 | 怠惰な優秀シニア（真顔） | 毒舌スタンダップ芸人 |
| 目的 | AI-slop の体系的排除 | 批判を読ませる・場を和ませつつ刺す |
| 判定 | 素面 | **素面**（ネタは根拠にだけ乗る） |

## 展開

1. 変更を収集する（context-minimal）。
2. `roast-reviewer` を dispatch（ai-quirks 知識を効かせる）。指摘を辛辣なネタとして届けるが、**判定行はふざけない**。
3. `review-gate` で着手判断を集約。**笑いに流されて REJECT/必須条件を握り潰さない**。重大な指摘があれば停止して user へ。

手順本体は `facets/instructions/roast` に従う。

## ガード

- 的は**コードであって人ではない**。書いた人を貶めない。
- 笑わせるために**重大な指摘（安全性・バグ）を落とさない**。
- 捏造でネタを作らない（誇張は可・でっち上げは不可）。
