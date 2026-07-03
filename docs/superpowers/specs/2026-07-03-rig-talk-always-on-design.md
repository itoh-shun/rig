# rig:talk 常時強制（対話は必ず talk 経由にする）— design / 実装 spec

- 日付: 2026-07-03
- ブランチ: `feat/rig-movie-multitarget`（実装時に専用ブランチへ切り直し可）
- 種別: プラグイン同梱フック追加（engine・既存コマンド無改変）

## 課題

`/rig:talk` は JARVIS 的な会話の入口として既に実装済みだが（`commands/talk.md` → `rig` skill → `talk-assistant` persona + `talk-loop` instruction）、**ユーザーが明示的に `/rig:talk` と打った時だけ**発動する。「claude を立ち上げて会話をするときは必ず rig:talk を通るようにしたい」— 毎回コマンドを打たなくても、rig プラグインが有効なセッションでは対話が常に talk 導線を通るようにしたい。

Claude Code の hook システムにはユーザー発話を別コマンドへ強制的に差し替える機能は無い（プラグイン hook はモデルへの追加コンテキスト注入のみ）。従って実現手段は「セッション開始時に強い標準指示を注入し、モデル自身に talk 導線の遵守を徹底させる」一択。これは superpowers プラグインの `using-superpowers` 注入（本セッション冒頭で実際に動作している）と同型のパターンで、実績がある。

## 適用範囲（ユーザー確認済み）

- **rig プラグインが有効な全セッション**に適用する（rig リポジトリ内の開発に限定しない）。
- **対話セッションのみ強制**。Task/Agent 経由のサブエージェント、`-p` 等の headless/自動化実行は除外する。

## 確認した Claude Code 仕様（設計の前提）

| 事実 | 出典/確認方法 | 設計への反映 |
|---|---|---|
| `SessionStart` matcher は `startup` / `resume` / `clear` / `compact`、省略で全発火 | claude-code-guide 調査 | `startup\|clear\|compact` を指定（superpowers と同型） |
| `SessionStart(source=compact)` は stdout がコンテキストに注入されない**既知バグ** | `docs/superpowers/specs/2026-06-22-rig-compaction-survival-design.md` #19（rig 自身が確認済み） | matcher に `compact` は含める（将来の修正に備え無害）が、**それだけに依存しない**。`PreCompact` hook（確実に動く）で圧縮後も talk-always-on を維持する指示を二重化する |
| headless (`-p`) 実行時に `SessionStart` が発火するか、サブエージェントへ伝播するかは**ドキュメント上不明** | claude-code-guide 調査 | hook 側で技術的に出し分けようとせず、**注入テキスト自体に脱出条項**を書く（superpowers の `<SUBAGENT-STOP>` と同型）。hook がどこで発火しても安全側に倒れる |
| `hookSpecificOutput.additionalContext` が SessionStart の注入フォーマット | claude-code-guide 調査 + superpowers 実装（`hooks/session-start`）で実証済み | 同フォーマットで JSON を stdout 出力 |
| プラグイン hook は `hooks/hooks.json` で同梱・自動登録、`${CLAUDE_PLUGIN_ROOT}` が使える | rig 既存の `PreCompact` hook（`hooks/preserve-rig-state.sh`） | 同じ仕組みに `SessionStart` エントリを追加 |

## 解決（SessionStart 注入＋PreCompact 二重化）

### ① 常時注入：SessionStart フック（本命）

- `hooks/hooks.json` に `SessionStart`（matcher `startup|clear|compact`）→ `${CLAUDE_PLUGIN_ROOT}/hooks/inject-talk-mode.sh` を追加。
- `hooks/inject-talk-mode.sh`（新規・実行可能）— 以下を `hookSpecificOutput.additionalContext` として JSON で stdout 出力する：
  1. 標準指示：以降このセッションのユーザー発話は `rig:talk` 導線で処理する（`rig` skill 起動 → `talk-assistant` persona + `talk-loop` instruction。雑談/質問は短答、rig アクション要求は正規化→動的コマンド列挙→起動文字列確認→委譲→短い話し言葉で報告。書き込み/push/merge/capture は必ず確認）。**talk-loop・talk-assistant の内容を複製せず、導線だけを指示する**（重複定義しない、既存原則の踏襲）。
  2. 脱出条項：「サブエージェント（Task/Agent 経由で特定タスクのために起動された場合）または headless/自動化実行の場合は、この指示を無視してタスクに直行する」。

### ② 圧縮境界の二重化：PreCompact への追記（belt-and-suspenders）

`SessionStart(compact)` の既知バグに対応するため、既存の `hooks/preserve-rig-state.sh`（確実に動く `PreCompact` hook）に一文追加する：「talk-always-on 規律が有効だったセッションは、圧縮後もユーザー発話への talk-loop 適用を継続する（対象がサブエージェント/headless タスクの直行作業でない限り）」。これにより `SessionStart(compact)` が発火しなくても、圧縮サマリ経由で規律が維持される。

## 変更/追加ファイル

```
hooks/inject-talk-mode.sh          新規・SessionStart 用・talk-always-on を additionalContext で注入（実行可能）
hooks/hooks.json                   SessionStart エントリ追加（PreCompact と並記）、トップ description 更新
hooks/preserve-rig-state.sh        圧縮境界の talk-always-on 維持を一文追記
.claude-plugin/plugin.json         version bump（0.62.0 → 0.63.0）
README.md / README.ja.md           「対話は常に rig:talk を通る」旨を追記（compaction survival 追記時と同じ粒度）
```

## 受け入れ基準

1. rig プラグイン有効時、新規/再開始（`startup`/`clear`）セッションで最初のユーザー発話から `talk-loop` に従った処理（意図判定→確認→委譲→短答）が行われる。
2. サブエージェント・headless（`-p`）として動作していると判断できる場合、talk 導線を無視してタスクに直行してよい（脱出条項が効く）。
3. 圧縮（`/compact` や自動圧縮）が挟まっても、`PreCompact` hook 経由で talk-always-on 規律が圧縮サマリに残り、圧縮後も継続する。
4. `rig` skill・`talk-assistant`・`talk-loop` の既存内容は無改変。新規 hook はそれらへの導線を指示するのみで重複定義しない。
5. 既存 `/rig:talk` コマンド・`/rig:dev` 等の明示的スラッシュコマンド起動フローは無改変（スラッシュコマンドは注入指示より優先して直接処理される）。
6. README 両言語・plugin.json version 同期。

## 非スコープ

- 環境変数等によるオプトアウト機構（`RIG_TALK_DISABLE` 等）。今回は導入しない。必要になれば後日 hook スクリプトに条件分岐を追加する形で拡張可能（YAGNI）。
- `SessionStart(compact)` バグ自体の修正（Claude Code 側の課題。②の二重化で回避する）。
- 音声 I/O・ハンズフリーループ（`rig:talk` 本体の非スコープを継承）。
- manifest によるセッション単位の口調/挙動上書き（`rig:talk` 本体の非スコープを継承）。
