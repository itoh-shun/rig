---
description: rig/sales — 商談レビュー・ハーネス。商談記録を5観点(ヒアリング/ニーズ/提案/クロージング/ネクストアクション)で並列評価し、総合評価＋型化された改善フィードバックを返す。営業メンバーも使える平易な入口。
argument-hint: [商談記録の本文 or ファイルパス] [--plan] [--autonomous] [--capture]
---

# rig/sales — 商談レビュー

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順・知識層注入）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。dev と同じ engine を sales ドメインで使う。

起動後、`--recipe deal-review` を既定として次の引数を PARSE し、商談記録を5観点で並列評価する:

```
$ARGUMENTS
```

## やること

引数（商談記録の本文 or ファイルパス、バラバラなメモ可）を `deal-review` recipe に渡す。手順本体（5観点 `parallel-fanout` → `acceptance-gate` → 総合評価 S/A/B/C ＋観点別 ＋次回アクション ＋情報不足の集約提示）は `facets/instructions/deal-review` に従う。

## 入力

- 商談記録テンプレ: `skills/rig/templates/deal-record.md`（埋めて渡すと評価精度が上がる。空欄は「情報不足」として指摘される）。
- バラバラなメモ・議事録の貼り付けでも受理する。記入は強制しない。

## 自社固有の評価

`skills/rig/facets/knowledge/sales-domain/` に自社のプロダクト強み・ICP・価格レンジ・競合・良い商談の型を記入しておくと、各レビュアーが自社文脈で評価する。未記入なら汎用観点のみで動く。

## flag

- `--plan` … COMPOSE まで実行してハーネスを提示し停止（ドライラン）。
- `--autonomous` … 観点ごとの確認を省き完走（capture ゲートは解除されない）。
- `--capture` … レビューから得た「良い商談の型」等の学びを承認ダイアログなしで知識層へ（提案表示・事後報告は省略しない）。

## 例

```
/rig:sales ./deals/2026-06-acme-initial.md          # 記録ファイルをレビュー
/rig:sales "ACME社 初回。情シス3名。課題は…"          # メモ貼り付けで即レビュー
/rig:sales --plan ./deals/acme.md                   # レビュー構成をドライラン確認
```
