---
name: deal-review
description: 商談記録を5観点(ヒアリング/ニーズ/提案/クロージング/ネクストアクション)で並列評価し、acceptance-gate で総合評価＋改善アクションへ収束させる事後レビュー recipe。
scope: shipped
steps:
  - id: deal-review
    instruction: deal-review
    pattern: parallel-fanout
    gate: acceptance-gate
    acceptance: ["5観点すべてが判定済み(◎○△×)", "各改善必須点が誰が次に何をすべきか分かる粒度", "情報不足が明示されている"]
    personas: [sales/hearing-reviewer, sales/needs-reviewer, sales/proposal-reviewer, sales/closing-reviewer, sales/next-action-reviewer]
    output_contract: deal-verdict
autonomy: interactive
---

# deal-review

> **ドメイン pack 注記**: これは rig engine（`SKILL.md`）を **dev と共用**する sales ドメイン pack の recipe。engine は書き換えず、観点 persona・instruction・output-contract を追加するだけで成立する（多ドメイン実証）。

## 使う場面

実商談（初回 / 提案 / クロージング）の記録を**事後レビュー**し、「型化された改善フィードバックを毎回一定品質で」受け取りたい時。できる営業の暗黙知を観点に固定し、商談の質を人依存から外す。

## 展開

1. **記録の受理** — `templates/deal-record` 形式またはバラバラなメモを受け取る（欠落は許容）。
2. **知識注入** — `facets/knowledge/sales-domain/`（自社固有）があれば各レビュアーに注入。無ければ汎用観点のみ。
3. **5観点 並列評価** — `parallel-fanout` で hearing / needs / proposal / closing / next-action を subagent dispatch（context-minimal: 親は dispatch と集約のみ、記録全文を抱えない）。
4. **収束** — `acceptance-gate` で「全観点判定済み・改善必須点が実行可能・情報不足明示」へ収束。
5. **集約提示** — `deal-verdict`(② 親集約) の形式で総合評価 S/A/B/C ＋観点別 ＋次回アクション ＋情報不足を提示。

## 汎用 / 固有の分離

観点 persona・recipe・output-contract は**汎用**（どの会社でも使える）。自社固有（プロダクト強み・ICP・価格・競合・良い商談の型）は `facets/knowledge/sales-domain/` に外出しし、差し替えれば他社にも転用できる。
