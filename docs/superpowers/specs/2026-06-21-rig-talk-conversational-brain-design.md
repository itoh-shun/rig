# rig/talk 会話ブレイン（JARVIS 的対話モード）— design / 実装 spec

- 日付: 2026-06-21
- ブランチ: `feat/rig-talk`
- 種別: 新モード pack 追加（rig engine 共用）

## 目的

rig を「コマンドを正確に打つ」道具から、「**話しかければ意図を汲んで適切なフローを回してくれる対話アシスタント**」へ広げる。JARVIS 的に、自然言語の発話を解釈し、dev / sales など適切なドメインへルーティングして実行する多ターン会話の入口を作る。

当初検討した `rig:voice`（単発の自然言語ルータ）は、本 `rig:talk`（多ターン会話 + 人格 + ルーティング）が内包するため**作らない**。

## スコープ

- **v1 は会話ブレインのみ（テキスト）**。プラグインは markdown だけで完結し、現環境（WSL2）でそのまま動く。
- **音声 I/O（TTS/STT）は v1 非スコープ**。ただし応答を「短い話し言葉」に保ち、将来の TTS 外付けを阻害しない設計にする。
- **engine（SKILL.md）は無改変**。dev / sales と同じ pack-add で成立させる（多ドメイン実証の継続）。
- 入口は `/rig:talk`。

## アーキテクチャ

engine（PARSE → RESOLVE → COMPOSE → RUN / context-minimal）は `skills/rig/SKILL.md` を共用。talk は engine の**前段（自然言語→構造化された rig 起動）**と**会話の継続**を担うフロントエンド・モード。以下を**追加**する:

```
commands/talk.md                              /rig:talk 入口（薄い。引数なし可）
skills/rig/facets/personas/talk-assistant.md  会話人格（簡潔・敬意・先回り＝JARVIS 風）
skills/rig/facets/instructions/talk-loop.md   見極め→ルーティング→確認→委譲→継続
```

> recipe / output-contract は作らない。talk は自前の多段フローを持たず、解釈後は既存コマンド（`/rig:dev` `/rig:sales` …）へ委譲し、委譲先が recipe を持つ。確認の提示フォーマットは `talk-loop` 内に定義する（過剰ブリックを作らない）。

## データフロー（1ターン）

1. `/rig:talk [発話]` 起動（引数なしなら「ご用件は?」で開始）→ rig skill → `talk-loop` instruction に従う。
2. **意図の見極め** — 雑談・質問か、rig アクション（dev/sales 実行）要求かを判定する。
3. **アクション要求の場合**:
   a. 発話を正規化（フィラー・言い淀み・口語崩れを落とす）。
   b. 利用可能な `/rig:*` コマンドを**動的に列挙**し、各 description に照らして意図を最も合うドメインへ分類（新 pack は自動的に候補に入る）。
   c. flag / recipe / 対象を発話から抽出する。
   d. **解釈を一言で確認**する（「変更を3観点でレビューしますね、いい?」）。曖昧・確信度が低い・対象不明なら、そこで1問だけ確認する。
4. **確認ガード** — 情報取得・低リスク（review / --plan / 状態確認）は即実行。**書き込み・push・merge・capture など影響あるアクションは確認必須**（暴発防止＝JARVIS の礼儀）。
5. **委譲** — 確定した `/rig:*` 起動文字列を通常 engine（PARSE→RESOLVE→COMPOSE→RUN）へ渡す。以降は既存フローが不変で動く。
6. **会話継続** — 結果を**短い話し言葉**で報告し（1〜2文）、次の用件を受ける。「もういい / exit / やめて」でモード終了。

## 人格: talk-assistant（既定・調整可）

- 簡潔・先回り・敬意ある語り口（JARVIS 風）。長広舌をしない。「要点 → 確認 → 実行 → 短い報告」。
- 冗談・前置き・自己言及は最小。1〜2文で返す。
- 日本語既定。`<repo>/.claude/rig.md`（manifest）で口調を上書きできる余地を残す（v1 では既定のみ実装、上書きは将来）。
- **推測補完の抑制** — 埋められない対象は確認で訊く。勝手に対象を捏造しない。

## ブリック詳細（実装ステップ）

1. `commands/talk.md` — frontmatter（description / argument-hint）+ 本文。dev.md と同型で薄く。「まず rig skill を起動し SKILL.md に従う／`talk-loop` instruction に従って会話する／引数は $ARGUMENTS／影響あるアクションは確認必須／応答は短い話し言葉」を記す。
2. `skills/rig/facets/personas/talk-assistant.md` — `# persona: talk-assistant` 形式。語り口・1〜2文・確認の作法・推測補完の抑制を定義。
3. `skills/rig/facets/instructions/talk-loop.md` — 上記データフロー 2〜6 を手順化。動的コマンド列挙の方法（`/rig:*` の description を見る）、確認ガードの線引き（情報取得=即／影響あり=確認）、委譲の仕方（確定した起動文字列を該当コマンド経由で engine に渡す）、会話継続と終了条件を記す。
4. README.md / README.ja.md に `/rig:talk` と talk pack を追記（dev/sales と並べる）。
5. plugin.json version を 0.3.0 → 0.4.0。

## 受け入れ基準

1. `/rig:talk` で多ターン会話ができ、rig アクション要求は適切な `/rig:*` に解釈・（確認後）委譲・実行される。
2. 利用可能コマンドを動的に列挙して候補化する（dev / sales、今後の pack も自動）。
3. 影響あるアクション（書き込み/push/merge/capture）は無確認実行しない。情報取得・低リスクは即応する。
4. engine（SKILL.md）無改変・dev / sales フロー不変。`/rig:talk` は薄い入口で engine を重複定義しない。
5. 応答が短い話し言葉で、将来の TTS 外付けを阻害しない。

## 非スコープ

- 音声出力（TTS：Stop/Notification hook → 音声エンジン）。**将来実装する際は TTS エンジンをユーザーが選べる差し替え式にする**（`say` / piper / VOICEVOX / ElevenLabs 等を manifest や hook 設定で指定。特定エンジンに固定しない）。
- 音声入力（STT：push-to-talk＋whisper / OS 音声入力）。同様にエンジン選択可能にする。
- 完全ハンズフリー・ループ（マイク常時待受→STT→応答→TTS）。
- manifest による口調上書きの実装（余地だけ残し、実装は将来）。
- `rig:voice` 単独コマンド（talk が内包するため作らない）。
