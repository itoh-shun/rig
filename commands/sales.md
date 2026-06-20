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

1. 引数（商談記録の本文 or ファイルパス）を商談記録として受理する。決まった形でなくてよい（バラバラなメモでも可）。
2. `deal-review` recipe を RESOLVE/COMPOSE する（5観点 reviewer ＋ acceptance-gate ＋ deal-verdict）。
3. `parallel-fanout` で hearing / needs / proposal / closing / next-action を **subagent 並列 dispatch**（context-minimal: 親は dispatch と集約のみ）。
4. `acceptance-gate` で「全観点判定済み・改善必須点が実行可能・情報不足明示」へ収束。
5. 総合評価（S/A/B/C）＋観点別テーブル＋次回の具体アクション＋情報不足を提示する。

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

## 規則（skill が正典）

- **context-minimal（ハードルール）** … 各観点 reviewer は subagent dispatch。親は dispatch＋集約＋ゲート判断のみ。記録全文を親 context に引き込まない。
- **推測補完の禁止** … 記録に無い項目を親が埋めない。欠落は「情報不足」として返す。
