---
name: brainstorm
description: ラフな着想を質問→代替案→セクション合意で固める壁打ち recipe。実装/タスク分解の前段＝「何を作るか/なぜ/どの順か」を先に固め、曖昧なまま実装に突っ込むのを防ぐ。design-brief に収束し /rig:tasks・/rig:dev へ接続。
scope: shipped
steps:
  - id: brainstorm
    instruction: brainstorm
    pattern: serial
    personas: [brainstormer]
    output_contract: design-brief
    gate: acceptance-gate
    acceptance:
      - "設計がセクション別に提示され各節の合意（または未解決行き）が取れている"
      - "代替案を最低1つ検討し採否の理由がある"
      - "未解決の問いを捏造で埋めず明示している"
autonomy: interactive
---

# brainstorm

> **モード pack 注記**: rig engine（`SKILL.md`）を dev / tasks / goal と**共用**する planning pack の recipe。engine は書き換えず、`brainstormer` persona・`brainstorm` instruction・`design-brief` output-contract を足すだけで成立する。`/rig:brainstorm` から起動。

## 使う場面

**やりたいことがまだぼんやりしていて、いきなり実装に入りたくない**時。例:

- 「こういうの作りたいけど、設計から相談したい」
- 「いくつか案がありそう。トレードオフを整理してから決めたい」
- 「大きく作る前に、何を・なぜ・どの順かを固めたい」

## フロントの位置（brainstorm → tasks → dev）

| | brainstorm | tasks | dev |
|---|---|---|---|
| 問い | 何を作る/なぜ | どう割る | どう実装する |
| 出力 | design-brief | task-plan | 実装/PR |

`/rig:brainstorm` で設計を固め → `/rig:tasks` で細粒度に割り → `/rig:dev` で実装、と前段から繋がる。

## 展開

1. **発散** — `brainstormer` が決め打ちせず質問で詰める（前提・制約・成功条件）。
2. **代替案** — 2〜3案＋トレードオフ。推しを1つに収束（根拠つき）。
3. **セクション合意** — 設計を節に分け、1つずつ承認/修正を取る（一気に決めない）。
4. **収束** — `design-brief`（狙い／セクション別決定／代替案／未解決／次の一手）に。`--plan` なら草案提示で停止。
5. **接続** — 終了時に**次段を1つ理由つきで推薦**し、起動文字列を提示して「これで進める？」と確認（規模大→`/rig:tasks`、小さく明確→`/rig:dev`、未解決が重い→調査先行）。**合意を得てから**次段へ（無断 auto-chain しない）。

手順本体は `facets/instructions/brainstorm`、作法は `brainstormer`、出力は `output-contracts/design-brief` に従う。

## ガード

- **決め打ちしない／代替を必ず見る／セクションで合意する**。最初の案に飛びつかない。
- **未解決を捏造で埋めない**（要調査へ）。**実装には踏み込まない**（次段へ）。
- 承認を取りながら進む（interactive）。壁打ちは dev フローの前段＝engine の再定義ではない。
