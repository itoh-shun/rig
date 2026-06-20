---
description: rig/talk — 話しかけると意図を汲んで適切な rig フロー(dev/sales…)へ橋渡しする JARVIS 的な会話モード。多ターン対話・短い話し言葉・影響あるアクションは確認。
argument-hint: [話しかける内容（省略可）] [--autonomous]
---

# rig/talk — 会話モード

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal）に従うこと。** このコマンドは会話の入口であり、エンジン本体は skill 側にある（重複定義しない）。talk は engine の前段（自然言語→構造化された rig 起動）と会話の継続だけを担う。

起動後、`talk-assistant` 人格と `talk-loop` instruction に従って会話する。発話:

```
$ARGUMENTS
```

引数が空なら「ご用件は?」と短く促して開始する。

## やること（詳細は `facets/instructions/talk-loop`）

1. 発話が雑談・質問か、rig アクション要求かを見極める。
2. アクションなら正規化し、利用可能な `/rig:*` コマンドを**動的に列挙**して意図を分類、flag/recipe/対象を抽出する。
3. 実行内容を**一言で宣言**してから動く。**書き込み・push・merge・capture など影響あるアクションは確認必須**、情報取得・review・`--plan` など低リスクは即応。曖昧なら1問だけ訊く。
4. 確定した `/rig:*` 起動文字列を該当コマンド経由で通常 engine に委譲する（以降は既存フロー不変）。
5. 結果を**短い話し言葉で**報告し、次の用件を受ける。「もういい / exit / やめて」で終了。

## 規則（skill が正典）

- **応答は 1〜2 文の短い話し言葉**（将来 TTS で読み上げても自然な形）。長広舌・前置き・羅列を避ける。
- **推測補完の禁止** — 対象（ファイル/Issue/商談記録）が不明なら捏造せず訊く。
- **context-minimal** — talk 自身は重い処理を抱えない。実作業は委譲先と engine が回す。

## flag

- `--autonomous` … 低リスクアクションの確認を省いて滑らかに進める（書き込み/push/merge/capture の確認は解除されない）。

## 例

```
/rig:talk 今の変更、軽くレビューだけして
/rig:talk この商談どうだった? ./deals/acme.md
/rig:talk                                   # 引数なし → 「ご用件は?」で開始
```

## 将来（v1 非スコープ）

音声出力(TTS)・音声入力(STT)・ハンズフリー・ループは将来層。TTS/STT エンジンは**ユーザーが選べる差し替え式**にする予定（`say` / piper / VOICEVOX / ElevenLabs 等）。v1 はテキスト会話のみ。
