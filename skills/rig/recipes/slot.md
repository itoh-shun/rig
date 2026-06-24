---
name: slot
description: Rigsino スロット。dev テーマ(🚀ship/🐛bug/🔥prod/🟢green/💎release/🦆duck-WILD/☕coffee)の3リール・スロットマシンで遊ぶ息抜きゲーム。架空クレジット・実ギャンブルではない。
scope: shipped
steps:
  - id: slot
    instruction: slot-machine
    pattern: serial
    personas: [slot-dealer]
autonomy: interactive
---

# slot

> **モード pack 注記**: rig engine（`SKILL.md`）を共用する humor pack の recipe。engine は書き換えず、`slot-dealer` persona と `slot-machine` instruction を足すだけで成立する。`/rig:slot` から起動。これは純粋な**息抜きゲーム**で、dev フローの判断には関与しない。

> ⚠️ 遊び。クレジットは**架空**、現金は絡まない。実ギャンブルの助長ではなく開発の合間のパロディ。

## 使う場面

ビルド待ち・CI 待ち・煮詰まった時の**息抜き**。Rigsino のディーラーが dev テーマのスロットを回してくれる。

## 台のテーマ

| シンボル | 開発あるある |
|---|---|
| 💎 | THE RELEASE（大当たり ×100） |
| 🦆 | duck（**WILD**・任意シンボルの代用。ラバーダックが救う） |
| 🚀 | SHIP IT（×25） |
| 🟢 | ALL GREEN（×10） |
| ☕ | CAFFEINATED（×5） |
| 🐛 | BUG HARVEST（皮肉の大豊作 ×3） |
| 🔥 | PROD DOWN（大凶・ベット没収） |

開始バンクロール 100 クレジット、既定ベット 10。リール重み・配当表・進行ループは `facets/instructions/slot-machine` が正典（公平性の担保）。

## 展開

1. **提示** — 残高とベットを示し `spin` / `bet <n>` / `cash out` を促す。
2. **スピン** — 重み表どおり3リールを引き、台を描画。
3. **精算** — 配当表で倍率判定（🦆 WILD 代用を適用）、残高更新、ディーラーが実況。
4. **続行/終了** — 残高 0 か `cash out` で終了。最終損益を表示。

手順本体は `facets/instructions/slot-machine`、口上は `slot-dealer` に従う。

## ガード

- **公平に回す**（重み・配当に正直）。イカサマにしない。
- **深追いを煽らない**。引き際を優しく示す（依存を作らない）。
- **実 dev フローの採否をスロットで決めない**（これはただの遊び。軽い決定は coin、重い決定は magi）。
