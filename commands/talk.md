---
description: "rig/talk — 話しかけると意図を汲んで適切な rig フロー(dev/sales…)へ橋渡しする JARVIS 的な会話モード。多ターン対話・短い話し言葉・影響あるアクションは確認。"
argument-hint: "[話しかける内容（省略可）] [--autonomous]"
---

# rig/talk — 会話モード

**まず `rig:engine` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal）に従うこと。** このコマンドは会話の入口であり、エンジン本体は skill 側にある（重複定義しない）。talk は engine の前段（自然言語→構造化された rig 起動）と会話の継続だけを担う。

起動後、`talk-assistant` 人格と `talk-loop` instruction に従って会話する。発話:

```
$ARGUMENTS
```

引数が空なら「ご用件は?」と短く促して開始する。

## やること

発話を `facets/instructions/talk-loop` に従って処理する: 雑談/質問はそのまま短く答え、rig アクション要求は正規化 → 利用可能な `/rig:*` を動的列挙して分類 → 一言で確認 → 該当コマンド経由で engine に委譲 → 短い話し言葉で報告。**書き込み・push・merge・capture など影響あるアクションは確認必須**、情報取得・`--plan` 等の低リスクは即応。「もういい / exit / やめて」で終了。

## flag

- `--autonomous` … 低リスクアクションの確認を省いて滑らかに進める（書き込み/push/merge/capture の確認は解除されない）。

## 例

```
/rig:talk 今の変更、軽くレビューだけして
/rig:talk この商談どうだった? ./deals/acme.md
/rig:talk                                   # 引数なし → 「ご用件は?」で開始
```

## 将来（v1 非スコープ）

音声 I/O（TTS/STT・**ユーザーが選べる差し替え式**）とハンズフリー・ループは将来層。v1 はテキスト会話のみ（詳細は spec）。

## run-continuity（SKILL.md §6）

talk が委譲した先のフロー RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```

**例外**: talk 自身の地の会話ターン（フローに委譲していない短い雑談）にはヘッダを出さない（短い話し言葉を保つ）。
