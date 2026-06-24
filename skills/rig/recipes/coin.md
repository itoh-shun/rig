---
name: coin
description: コイン投げ即決モード。可逆で些末な 50/50（N 択可）を熟考させずに即断する反-bikeshed ゲート。重い/不可逆な決定はトリアージで弾いて magi に回す。magi の対極。
scope: shipped
steps:
  - id: coin-flip
    instruction: coin-flip
    pattern: serial
    personas: [coin-flipper]
autonomy: interactive
---

# coin

> **モード pack 注記**: rig engine（`SKILL.md`）を magi 等と**共用**する humor pack の recipe。engine は書き換えず、`coin-flipper` persona と `coin-flip` instruction を足すだけで成立する。`/rig:coin` から起動。

## 使う場面

**可逆で些末な決定**を、考えすぎずに即決したい時。例:

- 「この変数名 `count` と `n` どっち？」
- 「タブ幅 2 と 4、もう決めて」
- 「先に lint 直す？ test 書く？（どっちでもいい）」

迷う時間がやり直す時間を超える類の決定。bikeshedding（自転車置き場の議論）を撃ち落とす。

## magi との対称（労力を決定の重さに釣り合わせる）

| | coin | magi |
|---|---|---|
| 対象 | 可逆・些末・実害小 | 不可逆・被害半径大・高 stakes |
| 機構 | 乱択（即断） | 3賢者の多数決合議 |
| 目的 | 過剰熟考の抑止 | 慎重な裁定 |

**過剰熟考も過小熟考も実害**。coin と magi は「決定にかける労力を、決定の重さに釣り合わせる」ための対の道具。

## 展開

1. **トリアージ**（`coin-flip` 手順 ②）— 可逆性・被害半径・実害を判定。
   - **重い/不可逆なら投げない** → `/rig:magi` へ回す（これが最大のガード — コインで重大決定を下さない）。
2. **投げる** — 些末・可逆と確認できたら公平に乱択し、正準出力で確定。
3. **背中を押す** — 「確定・可逆だからまず動こう」。再議論に戻さない。

手順本体は `facets/instructions/coin-flip` に従う。

## ガード

- **重大・不可逆な決定をコインで下さない**（トリアージで弾いて magi へ）。これは遊びだが、判断の境界は本物。
- 影響あるアクション（書き込み/push 等）の採否そのものはコインで決めない（それは些末ではない）。
