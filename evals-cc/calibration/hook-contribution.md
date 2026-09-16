# フックの寄与 — 同一スイートを hooks.json だけ変えて2回

両実行とも 7 ケース × 1 run × 2 arm、judge は sonnet。差分は `hooks/hooks.json` の
SessionStart から `inject-talk-mode.sh` を外した1箇所のみ。

| | A: 出荷形態 | B: talk-mode フック除去 |
|---|---|---|
| meanDelta | 0.00 | +0.048 |
| overallScore | 0.619 | 0.714 |
| costUsd | 5.20 | 2.59 |
| fire 5件で `rig:engine` が発火 | **5 / 5** | **0 / 5** |
| negative 2件で過剰発火 | **2 / 2** | **0 / 2** |

## 決定的な観測

`fires-engine` は `tool_used` grader であって judge を通らない。したがって
5/5 → 0/5 の反転は採点のブレではなく機械的な測定である。

**フックを外すと rig:engine は一度も発火しない。** 自然文の開発依頼5件すべてで、
description は skill を呼ばせていない。フックありの実行で発火していたのは
description が一致したからではなく、フックが「全ターンを rig:talk に通せ」と
命じていたからである。

A 側のトレースに残った引数がその証拠になっている。

    {"skill": "rig:go", "args": "talk: git rebase --onto の3引数の意味を知りたい（説明のみ、操作不要）"}

`talk:` 接頭辞はフック文面の語彙であって、ユーザーの依頼文には無い。

## 帰結

- 負例の過剰発火は description が広すぎるのではない。**description による発火は
  そもそも起きていない。** 直す場所はフックの発火条件か、talk フローの離脱条件である。
- A で観測された uplift（01 の +0.67）はフックの uplift であって、
  description の uplift ではない。
- with-arm の turn 数が 16→3 / 9→3 / 7→3 と崩れるのは、skill が読み込まれず
  baseline と同じ経路を通っているため。

## 注意 — runs: 1 の雑音

02 の without-arm は A で 0.00、B で 0.33。この arm はプラグインを読み込まないので
両実行で条件は同一であり、差はすべて単一実行の雑音である。**llm grader の
Δ は 0.33 程度動きうる。** 上の表で信用してよいのは `fires-engine` と
`no-rig-fires` の機械測定だけで、判断を賭けるならこの2つに賭ける。
