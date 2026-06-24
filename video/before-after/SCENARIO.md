# SCENARIO — rig before/after デモ（検閲済み）

> `/rig:scenario`（scenario-writer → 検閲）の確定アウトプット。検閲は既存ブリックの掛け合わせ：
> `ai-smell-reviewer`（＋`ai-writing-smells`）× `sns-post-reviewer` ＋ source 対応チェック → acceptance-gate。
> このシナリオが `web/before-after.html` / `video/before-after/`（`/rig:movie --hyperframes`）の設計図。

- 種別: before-after ／ 尺: 約60秒 ／ 観客: 開発者
- ログライン: 同じ変更が、rig を通すだけで「毎回ちゃんとレビューされる」になる。
- 感情の弧: 掴み → 痛み（rig なし）→ 転換 → ペイオフ（rig あり）→ CTA
- 目玉（1つ）: **「親に届くのは判定行だけ＝context 汚染なし」**（context-minimal の体感差）

## ビートシート（検閲済み）

| # | ビート | 画面 | テロップ | VO（草案） | source（実機能） |
|---|---|---|---|---|---|
| 1 | hook | ロゴ | rig で、何が変わる？ | 「同じ変更を、before と after で。」 | —（枠） |
| 2 | before | 😵‍💫 | 「この変更、レビューして」 | 「rig が無いと、どうなる？」 | — |
| 3 | **screen(before)** | 端末(赤)・親が全 diff 読む | context が膨らむ(イメージ)/観点その場任せ/同種ミス反復 | 「親の context が膨らみ、観点はその場任せ。」 | context-minimal（§6 red flags / §9） |
| 4 | before pains | list | context 膨張 / 品質ブレ / 中断で手戻り | 「汚れる、ブレる、手戻る。」 | §6 / §9 |
| 5 | turn | — | rig を通すと？ | 「タスク専用のハーネスを、その場で組む。」 | PARSE→RESOLVE→COMPOSE→RUN（§1） |
| 6 | after | ✨ | コマンド、1つ。 | 「実作業は subagent に渡し、親は集約だけ。」 | context-minimal（§6） |
| 7 | **screen(after)** | 端末・`--plan` | steps: 1 ・RUN しない | 「まず構成だけ、ドライランで見える。」 | `--plan`（§5） |
| 8 | **screen(after)** | 端末・3観点並列→verdict | 判定: APPROVE_WITH_CONDITIONS | 「3観点を並列で。親に届くのは判定行だけ。」 | review-only / parallel-fanout / review-verdict |
| 9 | benefits | list | 効き目3つ | 「軽く、速く、毎回同じ品質。」 | §6 / parallel-fanout / acceptance-gate |
| 10 | 使い勝手 | 🪶 | 軽い時は軽く、重い時だけ厚く | 「中断しても ▸rig が復帰。」 | size-aware（§4.4）/ run-continuity（§6） |
| 11 | contrast | list | before → after | 「全部読む→集約・ブレる→一定・手戻り→復帰。」 | 上記 |
| 12 | cta | コマンド | /rig:dev --only review | 「まずは1コマンド。」 | review-only |

## 検閲ログ（このシナリオが通過した修正）

- **[ai-smell]** 「劇的に変わる」= 空ワード → 削除し、実画面で差を見せる（show, don't tell）。
- **[source]** 「context 汚染 8,200 tokens」= 実測でない偽精度 → 「長い diff/ログで膨らむ（イメージ）」に。
- **[sns-post]** 「同じバグが再発する」= 断定が強い → 「同種のミスを繰り返しやすい」に弱める。

→ 再走で ai-smell / sns-post / source すべて APPROVE・acceptance-gate 充足。

## CTA
`/rig:dev --only review` — 同じ変更が、毎回ちゃんとレビューされる。
