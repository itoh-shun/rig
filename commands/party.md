---
description: "[experimental] rig/party — パーティ編成画面 🎮。テレメトリ(runs.jsonl)・drill 実測(検出率)・ブリック在庫から RPG 風キャラクターシート(Lv/出撃/REJECT/検出率/実績)を描画。ゲーム画面に見えるが全行が実データ=ハーネスの健康診断ダッシュボード。読み取り専用。"
argument-hint: ""
---

# rig/party — パーティ編成画面 🎮

**まず `rig` skill を Skill ツールで起動すること。** このコマンドは入口であり、描画本体は決定論スクリプトにある（計算的センサー一次）:

```
orchestrate party
```

を実行してそのまま表示する（$ARGUMENTS があれば注記として添える）。

```
$ARGUMENTS
```

## 見かた

- **Lv.** = DONE した run 数。**出撃 / REJECT** = 各 reviewer の検証票（`runs.jsonl`）。
- **⚔ 検出率** = `/rig:drill` の実測（`drill-results.jsonl`）。「未測定」の枠は drill で較正できる。
- **🛡 finding-verifier** = 反証の回数（棄却の質は `runs --personas` で監査）。
- **実績 🏆** = テレメトリから機械判定（初DONE / 十連戦無傷 / 百戦錬磨 / 満点狙撃手 / 大図書館）。
- パーティ = 既定 3-way + manifest `default_personas`。控え = `--persona` で出撃できる選択投入枠。

ゲーム画面は演出、中身はハーネスの健康診断（低出撃・未測定・高REJECTがそのまま改善指示になる）。育成は `/rig:drill`（較正）→ `/rig:persona`（観点を尖らせる）→ `/rig:drill --replay`（回帰確認）。
