---
description: rig/sales — 営業ハーネス。商談記録を5観点で並列評価(deal-review)するほか、開発資材から営業1枚資料・荷電スクリプトを生成(--material/--script)する。営業メンバーも使える平易な入口。
argument-hint: [商談記録 or ファイルパス] [--material] [--script] [--from <path>] [--plan] [--autonomous] [--capture]
---

# rig/sales — 営業ハーネス（商談レビュー ＋ 資材生成）

**まず `rig:rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順・知識層注入）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。dev と同じ engine を sales ドメインで使う。

## モード（2 系統）

| 指定 | recipe | 何をする |
|---|---|---|
| （既定・商談記録を渡す） | `deal-review` | 商談記録を5観点で並列評価し改善フィードバック（事後レビュー） |
| `--material` | `sales-enablement --only material` | 開発資材 → **営業1枚資料**を生成 |
| `--script` | `sales-enablement --only script` | 開発資材 → **荷電スクリプト**を生成 |
| `--material --script`（両方 or どちらも無く資材生成意図） | `sales-enablement` | 1枚資料＋荷電スクリプトを両方生成 |

`--material` / `--script` のいずれかが指定されたら**資材生成モード**（`sales-enablement` recipe）、無ければ**商談レビュー**（`deal-review`）。資材生成の対象は既定で現在のリポジトリ、`--from <path>` で指定可。

起動後、次の引数を PARSE する:

```
$ARGUMENTS
```

## やること

- **商談レビュー（既定）**: 引数（商談記録の本文 or ファイルパス、バラバラなメモ可）を `deal-review` recipe に渡す。手順本体（5観点 `parallel-fanout` → `acceptance-gate` → 総合評価 S/A/B/C ＋観点別 ＋次回アクション ＋情報不足の集約提示）は `facets/instructions/deal-review` に従う。
- **資材生成（`--material` / `--script`）**: 開発資材（既定は現在のリポジトリ `README*`/`CHANGELOG*`/`plugin.json`/`docs/`、`--from <path>` で指定可）を `sales-enablement` recipe に渡す。手順本体（実在機能の抽出 → 機能→ベネフィット翻訳 → 営業1枚資料 / 荷電スクリプト生成）は `facets/instructions/{sales-material,call-script}` に従う。**実在機能のみ・誇張禁止・不明は `[要記入]`**。`sales-domain` 知識があれば ICP・価格・差別化に反映。

## 入力

- 商談記録テンプレ: `skills/rig/templates/deal-record.md`（埋めて渡すと評価精度が上がる。空欄は「情報不足」として指摘される）。
- バラバラなメモ・議事録の貼り付けでも受理する。記入は強制しない。

## 自社固有の評価

`skills/rig/facets/knowledge/sales-domain/` に自社のプロダクト強み・ICP・価格レンジ・競合・良い商談の型を記入しておくと、各レビュアーが自社文脈で評価する。未記入なら汎用観点のみで動く。

## flag

- `--material` … 開発資材から**営業1枚資料**を生成（資材生成モード）。
- `--script` … 開発資材から**荷電スクリプト**を生成（資材生成モード）。両方指定で両方生成。
- `--from <path>` … 資材生成の対象を指定（既定は現在のリポジトリ）。
- `--plan` … COMPOSE まで実行してハーネスを提示し停止（ドライラン）。
- `--autonomous` … 確認を省き完走（capture ゲートは解除されない）。
- `--capture` … 学び（良い商談の型等）を承認ダイアログなしで知識層へ（提案表示・事後報告は省略しない）。

## 例

```
# 商談レビュー
/rig:sales ./deals/2026-06-acme-initial.md          # 記録ファイルをレビュー
/rig:sales "ACME社 初回。情シス3名。課題は…"          # メモ貼り付けで即レビュー

# 資材生成（開発資材 → 営業資材）
/rig:sales --material                                # このリポジトリ → 営業1枚資料
/rig:sales --script                                  # → 荷電スクリプト
/rig:sales --material --script --from ./             # 両方まとめて生成
```
