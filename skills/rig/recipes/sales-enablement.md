---
name: sales-enablement
description: 開発資材(README/CHANGELOG/コード/リリース)から営業1枚資料と荷電スクリプトを生成する sales 生成 recipe。機能→ベネフィット翻訳・実在機能のみ・誇張禁止。--only で片方だけも可。
scope: shipped
steps:
  - id: material
    instruction: sales-material
    pattern: serial
    personas: [sales/material-writer]
    output_contract: sales-collateral
  - id: script
    instruction: call-script
    pattern: serial
    personas: [sales/cold-caller]
    output_contract: sales-collateral
autonomy: interactive
---

# sales-enablement

> **ドメイン pack 注記**: rig engine（`SKILL.md`）を dev と**共用**する sales ドメイン pack の生成 recipe。engine は書き換えず、生成 persona・instruction・output-contract を足すだけで成立する。`deal-review`（商談の事後レビュー）と対をなす「**前段の資材生成**」。`/rig:sales --material` / `--script` から起動。

## 使う場面

開発した（している）プロダクトを**売りに出す資材が無い／作るのが面倒**な時。コードと README はあるのに営業資料が無い、を埋める。例:

- 「この OSS / プロダクトの営業1枚資料を作って」
- 「荷電（テレアポ）のスクリプトが欲しい」
- 「リリースした機能を客向けの価値に翻訳して」

## deal-review との対（sales pack の両輪）

| | sales-enablement | deal-review |
|---|---|---|
| 役 | 売る**前**の資材を作る | 商談の**後**にレビュー |
| 入力 | 開発資材（README/CHANGELOG/コード） | 商談記録 |
| 出力 | 1枚資料＋荷電スクリプト | 5観点評価＋改善 |

## 展開

1. **資材の収集** — 既定は現在のリポジトリ（`README*`/`CHANGELOG*`/`plugin.json`/`docs/`）。`--from <path>` で対象指定可。長文は subagent に渡して要点抽出（context-minimal）。
2. **固有知識の注入** — `facets/knowledge/sales-domain/`（ICP・価格・差別化）があれば反映。無ければ汎用＋`[要記入]`。
3. **生成（2 step）**
   - `material` — `sales/material-writer` が**機能→ベネフィット翻訳**で営業1枚資料を生成（実在機能のみ・課題ドリブン・AI 臭禁止）。
   - `script` — `sales/cold-caller` が**15秒オープニング＋反論処理＋next action**の荷電スクリプトを生成。
4. **構造化** — 両 step とも `output-contracts/sales-collateral` 準拠。

`--only material` / `--only script` で片方だけも可。手順本体は `facets/instructions/{sales-material,call-script}` に従う。

## ガード

- **実在機能のみ・捏造禁止・盛った実績を書かない**（資材に根拠が無い訴求は出さない）。
- **不明は `[要記入: …]`**（価格・実績・社名を埋めた風にしない）。
- 生成物は `/rig:dev --recipe de-ai-smell` に通すと AI 臭をさらに落とせる（仕上げ・任意）。
