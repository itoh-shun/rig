---
name: spd-review
description: SPD(院内物品物流管理)ドメインの対象(提案書/仕様/業務フロー/契約骨子)を6ステークホルダー視点(病院経営/用度材料/看護現場/SPD現場/事業者経営/卸流通)で並列評価し、acceptance-gate で総合判定＋優先アクションへ収束させるレビュー recipe。
scope: shipped
steps:
  - id: spd-review
    instruction: spd-review
    pattern: parallel-fanout
    gate: acceptance-gate
    acceptance: ["6視点すべてが判定済み(◎○△×)", "各懸念・要改善点が誰が次に何をすべきか分かる粒度", "情報不足と事実誤認(制度・償還・薬機法)が明示されている"]
    personas: [spd/hospital-executive, spd/materials-manager, spd/ward-nurse, spd/spd-operator, spd/spd-vendor-manager, spd/distributor]
    output_contract: spd-verdict
autonomy: interactive
---

# spd-review

> **ドメイン pack 注記**: これは rig engine（`SKILL.md`）を **dev / sales と共用**する SPD ドメイン pack の recipe。engine は書き換えず、ステークホルダー persona・instruction・output-contract・knowledge を追加するだけで成立する（多ドメイン実証の3例目）。

## 使う場面

SPD（Supply Processing and Distribution＝院内物品物流管理）に関わる成果物——SPD導入提案書、委託仕様書・契約骨子、SPDシステムの要件/設計、院内の物品管理業務フロー、病院向け営業資料——を、**業界に登場する6つの立場から同時にレビュー**したい時。「経営には刺さるが看護現場で破綻する」「病院メリットだけで事業者の採算が語られていない」といった、単一視点では落ちる指摘を型として固定する。

## 展開

1. **対象の受理** — 提案書・仕様・フロー等の本文 or ファイルパスを受け取る（形式不問・欠落は許容）。
2. **知識注入** — `facets/knowledge/spd-domain/`（汎用: spd-basics / spd-industry / spd-glossary、固有: _template 記入分）を各レビュアーに注入。
3. **6視点 並列評価** — `parallel-fanout` で hospital-executive / materials-manager / ward-nurse / spd-operator / spd-vendor-manager / distributor を subagent dispatch（context-minimal: 親は dispatch と集約のみ、対象全文を抱えない）。
4. **収束** — `acceptance-gate` で「全視点判定済み・懸念点が実行可能・情報不足と事実誤認の明示」へ収束。
5. **集約提示** — `spd-verdict`（② 親集約）の形式で総合判定 GO / 条件付きGO / 要再検討 ＋視点別 ＋優先アクション ＋情報不足を提示。

## 汎用 / 固有の分離

ステークホルダー persona・recipe・output-contract・業界知識（spd-basics / spd-industry / spd-glossary）は**汎用**（どの病院・事業者でも使える）。自院/自社固有（施設プロフィール・運用実態・KPI・課題）は `facets/knowledge/spd-domain/_template` に外出しし、差し替えれば他院・他社にも転用できる。
