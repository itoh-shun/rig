---
description: rig/slot — Rigsino スロット。dev テーマ(🚀ship/🐛bug/🔥prod/🟢green/💎release/🦆duck-WILD/☕coffee)の3リール・スロットで遊ぶ息抜きゲーム。架空クレジット・実ギャンブルではない。
argument-hint: [spin | bet <n> | cash out（省略可・既定は spin 案内）]
---

# rig/slot — Rigsino スロット 🎰

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。

> ⚠️ これは**遊び**。クレジットは架空（fake）、現金は一切絡まない。実ギャンブルの助長ではなく、ビルド待ちの息抜きパロディです。

起動後、`--recipe slot` を既定として次の引数を PARSE する:

```
$ARGUMENTS
```

引数が無ければ台の前に案内し、残高（開始 100）・ベット（既定 10）を示して `spin` を促す。

## やること

Rigsino のディーラー（`slot-dealer`）が dev テーマのスロットを回す。台のルール（リール重み・配当表・進行ループ）は `facets/instructions/slot-machine` が正典。

- **公平な台**: 出目は重み表に正直（イカサマなし）。
- **深追いは煽らない**: 残高が尽きかけたら引き際を優しく示す。
- **遊びと割り切る**: 実 dev フローの採否はスロットで決めない（軽い決定は `/rig:coin`、重い決定は `/rig:magi`）。

## 操作

- `spin` … 1 回回す（ベット額を賭ける）。
- `bet <n>` … 賭け金を変更（残高以下）。
- `cash out` … 精算して終了（最終損益を表示）。

## 配当（ベット × 倍率）

💎💎💎 ×100 ／ 🦆🦆🦆 ×50 ／ 🚀🚀🚀 ×25 ／ 🟢🟢🟢 ×10 ／ ☕☕☕ ×5 ／ 🐛🐛🐛 ×3 ／ 🔥🔥🔥 没収。任意ペア ×2（🔥 絡みのペアは焦げ付き）。🦆 は WILD（任意シンボルの代用）。

## 例

```
/rig:slot                 # 台の前へ（残高 100・ベット 10）
/rig:slot spin            # いきなり1回転
/rig:slot bet 25          # ベットを 25 に
```
