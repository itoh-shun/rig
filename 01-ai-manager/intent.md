# intent — 第1記事

## タイトル候補

1. Claude Codeで楽になるはずが、気づけばAIの面倒を見ていた（仮）
2. AIの面倒を見たくなくて、AIを信用しなくても任せられる仕組みを作った

最終稿で採用したもの: 1 を基調に、本文の問いと一致する形へ調整。

## Target reader

Claude Code を使い込んだ中上級エンジニア。CLAUDE.md / Skills / Subagents / worktree /
別 AI レビュー（Codex 等）/ 独自 workflow / MCP / 開発ルールと Knowledge の蓄積を
すでに使っているか、検討している層。

「Claude Code とは何か」は書かない。

## Reader outcome

「自分は AI を使っているのではなく、AI の面倒を見る仕事をしていたかもしれない」と
自分の作業を言い当てられたと感じる。

## Business / OSS outcome

- rig の GitHub を見に行きたくなる
- 可能なら試したくなる
- 同種の運用課題を持つ人が Issue を立てたくなる

## Single thesis（記事で扱う問いは1つだけ）

**AI で開発が楽になるはずなのに、なぜ人間が AI の管理者になっていくのか。**

## Non-goal

- 初心者向け Claude Code 入門
- rig の全機能紹介（rig の説明量は記事全体の 30〜40% を上限の目安とする）
- 他ツール批判
- TAKT / Skills 等を不要と主張すること
- ベンチマーク記事（measurement の思想には触れるが、数値の主張はしない）

## 構成方針

問題 → 気づき → 設計思想 → rig の順。機能一覧にしない。
rig がまとまって出てくるのは後半1章のみ。

## 事実の扱い

- shipped でないものを「今できること」として書かない
- 技術的主張は repository 内の根拠（sources.md）に紐付ける
- 筆者の体験談・数値・エラー文・効果測定を捏造しない
  （書ける「動機」は、この依頼で与えられた設計上の出発点4点のみ）
