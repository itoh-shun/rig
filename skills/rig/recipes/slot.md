---
name: slot
description: Rigsino — 6号機風 AT パチスロ実機シミュ。通常時→CZ「PR REVIEW」→AT「SHIP RUSH」の状態機械、押し順ベル・天井・設定1〜6・純増・上乗せ・永続メダル管理つきの息抜きゲーム。架空メダル・実ギャンブルではない。
scope: shipped
steps:
  - id: slot
    instruction: slot-machine
    pattern: serial
    personas: [slot-dealer]
autonomy: interactive
---

# slot

> **モード pack 注記**: rig engine（`SKILL.md`）を共用する humor pack の recipe。engine は書き換えず、`slot-dealer` persona と `slot-machine` instruction（実エンジン `scripts/rigsino.py` に委譲）を足すだけで成立する。`/rig:slot` から起動。純粋な**息抜きゲーム**で dev フローの判断には関与しない。

> ⚠️ 遊び。メダルは**架空**、現金は絡まない。実ギャンブルの助長ではなく開発の合間のパロディ。

## 参考実機

**6号機 AT/ART 機**。通常時 → CZ → AT の状態機械で出玉が動くタイプ。Vegas 型の「ペイラインで即配当」ではなく、AT を引くまで投資し、AT で回収する波のあるゲーム性。

## 使う場面

ビルド待ち・CI 待ち・煮詰まった時の**息抜き**。Rigsino のディーラーが dev テーマの AT 機を実機さながらに回してくれる。**手持ちメダルはセッションをまたいで永続**するので「昨日の負けを取り返す」も可能。

## ゲーム性（dev テーマに翻訳）

| 実機要素 | Rigsino | 意味 |
|---|---|---|
| 押し順ベル | 🔔 CI ベル | 正解 +9 / こぼし +1（AT 中はナビ） |
| リプレイ | 🔄 再ビルド | 投入なしで再遊技 |
| レア役 | ☕弱 / 🐛チャンス目 / 🔥強 / 💎確定 | CZ/AT 抽選 |
| CZ | 🟦 PR REVIEW | レビュー承認で AT 当選 |
| AT | 🚀 SHIP RUSH | ナビ純増・上乗せ・セット継続 |
| 天井 | 🔧 救済 | 800G ハマりで CZ |
| 告知ランプ | 💡 DEPLOY ランプ | 当選/上乗せで点灯 |
| 設定1〜6 | 機械割 95%→115% | 看破要素 |

開始メダル 1000枚・3枚掛け。リール重み・小役確率・状態遷移・配当・機械割は実エンジン `scripts/rigsino.py` が正典（公平性の担保・50 万 G シミュで実機相当に調整済み）。

## 展開

操作は `scripts/rigsino.py` に委譲（手順は `facets/instructions/slot-machine`、口上は `slot-dealer`）:

- `spin [--order L|C|R]` — 1G 回す（通常時の押し順ベルは第1停止を指定可）。
- `auto [N]` — N ゲームまとめ消化（ハイライトだけ実況）。
- `status` — 手持ちメダル・現在状態・生涯戦績（実測機械割・AT 初当たり）。
- `reset` — 台移動（状態/設定リセット・メダルは持ち越し）。
- `cashin <N>` — 架空メダル補充。

## ガード

- **公平に回す**（重み・配当に正直）。イカサマにしない。
- **深追いを煽らない**。引き際を優しく示す（依存を作らない・`cashin` を無理に勧めない）。
- **実 dev フローの採否をスロットで決めない**（軽い決定は `/rig:coin`、重い決定は `/rig:magi`）。
