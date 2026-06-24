---
description: rig/slot — Rigsino。6号機風 AT パチスロ実機シミュ（通常時→CZ「PR REVIEW」→AT「SHIP RUSH」・押し順ベル・天井・設定1〜6・純増・永続メダル管理）の息抜きゲーム。架空メダル・実ギャンブルではない。
argument-hint: [spin [--order L|C|R] | auto [N] | status | reset | cashin <N> | payouts]
---

# rig/slot — Rigsino（6号機風 AT 機）🎰

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。

> ⚠️ これは**遊び**。メダルは架空（fake）、現金は一切絡まない。実ギャンブルの助長ではなく、ビルド待ちの息抜きパロディです。

起動後、`--recipe slot` を既定として次の引数を PARSE する:

```
$ARGUMENTS
```

引数が無ければ台の前に案内し、`status` で手持ちメダル・現在状態を見せて `spin` を促す。

## やること

Rigsino のディーラー（`slot-dealer`）が **6号機風 AT 機**を実機さながらに回す。台のルール本体（リール・小役・状態遷移・配当・機械割）は実エンジン **`scripts/rigsino.py`** が正典で、各操作はそれを実行して出力を提示する（手順は `facets/instructions/slot-machine`）。**手持ちメダルと台の状態は `~/.claude/rig/rigsino/wallet.json` に永続**（セッション・プロジェクトをまたいで持ち越す）。

- **通常時 → CZ「PR REVIEW」→ AT「SHIP RUSH」🚀** の状態機械。押し順ベル・天井（800G）・設定1〜6・純増・上乗せ・セット継続を実機相当で再現（機械割 設定1≈95% / 設定6≈115%）。
- **公平な台**（イカサマなし）・**深追いは煽らない**・**遊びと割り切る**（実 dev フローの採否はスロットで決めない。軽い決定は `/rig:coin`、重い決定は `/rig:magi`）。

## 操作

- `spin [--order L|C|R]` … 1G 回す（通常時の押し順ベルは第1停止を指定可・無指定はランダム押し）。
- `auto [N]` … N ゲームまとめ消化（既定 50・ハイライトだけ実況）。
- `status` … 手持ちメダル・現在状態・生涯戦績（実測機械割・AT 初当たり・最高 AT）。
- `reset` … 台移動（状態/設定リセット・メダルは持ち越し）。
- `cashin <N>` … 架空メダル補充（追加投資）。
- `payouts` … 小役・配当・状態の早見表。

## 例

```
/rig:slot                 # 台の前へ（status 表示）
/rig:slot spin --order C   # 1G・第1停止 中リール
/rig:slot auto 100         # 100G まとめ消化
/rig:slot status           # 手持ちメダルと戦績
/rig:slot reset            # 台移動（メダルは持ち越し）
```
