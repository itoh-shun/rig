---
description: "rig/coin — コイン投げ即決モード。可逆で些末な 50/50（N 択可）を熟考させず即断する反-bikeshed ゲート。重い/不可逆な決定はトリアージで弾いて magi に回す。magi の対極。"
argument-hint: "[決めたいこと（2択 or 選択肢）] [--autonomous]"
---

# rig/coin — コイン投げ即決 🪙

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。magi と同じ engine を「軽い決定の即断」に使う（magi の対極）。

起動後、`--recipe coin` を既定として次の引数を議題に PARSE する:

```
$ARGUMENTS
```

議題が空なら「何を決める？」と短く確認する（捏造で決めない）。

## やること

議題を `coin` recipe に渡す。手順本体（①議題確定 →②トリアージ：可逆・些末・実害小か →③些末なら乱択して即決／重大なら magi に回す）は `facets/instructions/coin-flip` に従う。

- **トリアージが本体**: 可逆で些末な決定だけコインで決める。**不可逆・被害半径大はコインで決めない** → `/rig:magi` へ回す。
- **`/rig:magi` との違い**: magi は重い決定を3賢者で慎重に裁く。coin は軽い決定を即断する。過剰熟考も過小熟考も実害 — 労力を決定の重さに合わせる対の道具。
- 投げたら背中を押す（「可逆だからまず動こう」）。再議論のループに戻さない。

## flag

- `--autonomous` … トリアージで些末と確定したら確認なしで即投げる（重大判定時の magi 誘導は省略しない）。

## 例

```
/rig:coin 変数名 count と n どっち
/rig:coin タブ幅 2 か 4
/rig:coin 先に lint 直すか test 書くか
/rig:coin このDBスキーマ移行をやるか    # → トリアージで弾かれ magi へ誘導される
```
