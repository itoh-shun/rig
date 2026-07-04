---
description: "rig/magi — エヴァの MAGI を模した3賢者合議モード。提案を Melchior(科学者=正しさ)/Balthasar(母=守り)/Casper(女=価値)の3観点に並列で諮り、多数決で go/no-go を裁定する。「やるべきか」を裁く decision モード。"
argument-hint: "[裁定にかける提案・選択肢・質問] [--plan] [--autonomous]"
---

# rig/magi — MAGI 合議モード

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal・facet 配置順・知識層注入）に従うこと。** このコマンドは入口であり、エンジン本体は skill 側にある（重複定義しない）。dev / sales / talk / goal / pr と同じ engine を「決定の裁定」に使う。

起動後、`--recipe magi` を既定として次の引数を議題に PARSE し、3 号機の合議にかける:

```
$ARGUMENTS
```

議題が空なら一言だけ確認する（何を裁定にかけるか・捏造しない）。

## やること

議題（提案・選択肢・質問・対象 diff）を `magi` recipe に渡す。手順本体（①議題確定 →② Melchior/Balthasar/Casper を `parallel-fanout` で並列諮問 →③ `magi-consensus` で多数決集計 →④ MAGI コンソールで判決提示）は `facets/instructions/magi-deliberation` に従う。

- **3 号機の観点は直交する**: Melchior-1（科学者＝正しいか）／ Balthasar-2（母＝危険でないか）／ Casper-3（女＝価値があるか）。正しくても危険／無価値なら否決されうる。
- **`/rig:dev --only review` との違い**: dev review はコードの品質レビュー（security/design/test）。magi は**採否そのもの**の裁定（go/no-go）。
- 実作業（評価）は 3 号機 subagent が回す（context-minimal）。長い diff を親に引き込まない。
- **否決・審議継続では先へ進めない**。可決時のみ後続作業へ委譲する。

## flag

- `--plan` … 諮問構成を提示して停止（ドライラン）。
- `--autonomous` … 可決後の後続委譲の step ゲートを省くだけ。判決（否決/条件付/審議継続）は尊重される。

## 例

```
/rig:magi この破壊的変更を今リリースしていいか
/rig:magi 設計案: 認証を JWT から session に戻す。go/no-go
/rig:magi この機能、そもそも作る価値があるか
/rig:magi --plan 現在の変更をマージすべきか        # 諮問構成だけ確認
```


## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない（可視化＝駆動の証拠）:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
