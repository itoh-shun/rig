# rig:talk 常時強制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** rig プラグインが有効な対話セッションでは、ユーザーが `/rig:talk` を明示的に打たなくても、セッション開始時に注入される標準指示によって会話が常に talk 導線（`rig` skill → `talk-assistant` persona → `talk-loop` instruction）を通るようにする。

**Architecture:** 新規 `SessionStart` hook（`hooks/inject-talk-mode.sh`）が `hookSpecificOutput.additionalContext` として talk-always-on 指示（サブエージェント/headless の脱出条項つき）を注入する。`SessionStart(source=compact)` は既知バグで注入されないことがあるため、既存の確実に動く `PreCompact` hook（`hooks/preserve-rig-state.sh`）にも同じ指示の維持を一文追加し、圧縮境界を二重化する。engine（`SKILL.md`）・既存の `talk-assistant`/`talk-loop`/`commands/talk.md` は無改変。

**Tech Stack:** POSIX `sh`（rig の既存 hook と同じ、外部依存なし）、JSON 出力は `printf`（superpowers `hooks/session-start` と同じ実証済みパターン）。JSON 妥当性検証は `python3 -m json.tool`（プロジェクトに `jq` は無い）。

## Global Constraints

- 新規 hook スクリプトは実行可能（`chmod +x`）にすること（`docs/superpowers/specs/2026-07-03-rig-talk-always-on-design.md` 変更ファイル一覧）。
- `talk-assistant`・`talk-loop`・`rig` skill（`SKILL.md`）・`commands/talk.md` の内容は無改変（重複定義しない — spec 受け入れ基準4）。
- 注入テキストは日本語（rig 全体の既定言語、`talk-assistant.md` 冒頭「日本語既定」）。
- サブエージェント/headless 除外は hook 側の技術的出し分けをせず、注入テキスト自体の脱出条項で行う（spec の設計判断）。
- `SessionStart` matcher は `startup|clear|compact`（`compact` は既知バグがあっても無害なため含める）。
- `.claude-plugin/plugin.json` の version は既存の hook 追加コミットと同じ粒度で bump する（前例: 0.7.0→0.8.0 で PreCompact hook 追加）。今回は 0.62.0 → 0.63.0。
- 既存の `/rig:talk` や他の `/rig:*` スラッシュコマンドの明示起動フローは無改変（spec 受け入れ基準5）。

---

## Task 1: `hooks/inject-talk-mode.sh` を新規作成する

**Files:**
- Create: `hooks/inject-talk-mode.sh`

**Interfaces:**
- Produces: 標準出力に JSON（`{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"<message>"}}`）を出力し、exit code 0 で終了する。Task 2 でこのスクリプトを `hooks/hooks.json` の `SessionStart` エントリから `${CLAUDE_PLUGIN_ROOT}/hooks/inject-talk-mode.sh` として呼び出す。

- [ ] **Step 1: スクリプトを作成する**

`hooks/inject-talk-mode.sh` を次の内容で作成する:

```sh
#!/usr/bin/env sh
# rig talk-always-on — SessionStart hook.
# Fires on session startup/clear/compact (see hooks.json matcher). On exit 0,
# stdout JSON is read as SessionStart additionalContext and injected into
# context, so the model treats user turns as rig:talk input from the start.
#
# NOTE: SessionStart(source=compact) is known not to re-inject on some
# Claude Code versions (see
# docs/superpowers/specs/2026-06-22-rig-compaction-survival-design.md).
# hooks/preserve-rig-state.sh carries a belt-and-suspenders copy of this
# directive through the (reliable) PreCompact hook for that case.

message='このセッションでは、ユーザーとの対話は必ず rig:talk の導線で処理する。まだ起動していなければ `rig` skill を Skill ツールで起動し、その SKILL.md（PARSE → RESOLVE → COMPOSE → RUN・context-minimal）に従うこと。起動後は `talk-assistant` 人格と `talk-loop` instruction に従って会話する: 雑談・質問はそのまま短く答え、rig アクション要求は正規化 → 利用可能な /rig:* を動的列挙して分類 → 起動文字列を一言確認 → 該当コマンド経由で engine に委譲 → 短い話し言葉で報告する。書き込み・push・merge・capture など影響あるアクションは確認必須、情報取得・--plan 等の低リスクは即応する。「もういい / exit / やめて」で通常モードに戻ってよい。例外: あなたが Task/Agent 経由のサブエージェントとして特定タスクのために起動された場合、または headless/自動化実行（例: claude -p）の場合は、この指示を無視してタスクに直行してよい。'

escape_for_json() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

escaped=$(escape_for_json "$message")

printf '{\n  "hookSpecificOutput": {\n    "hookEventName": "SessionStart",\n    "additionalContext": "%s"\n  }\n}\n' "$escaped"
```

- [ ] **Step 2: 実行可能にする**

```bash
chmod +x hooks/inject-talk-mode.sh
```

- [ ] **Step 3: 手動実行して JSON 妥当性を確認する**

Run:
```bash
./hooks/inject-talk-mode.sh | python3 -m json.tool
```

Expected: エラー無くフォーマットされた JSON が表示される（exit code 0）。`"hookEventName": "SessionStart"` と、`"additionalContext"` の値に `talk-assistant` `talk-loop` `Task/Agent` `headless` の文字列が含まれることを目視確認する。

- [ ] **Step 4: additionalContext の中身を個別に取り出して脱出条項を確認する**

Run:
```bash
./hooks/inject-talk-mode.sh | python3 -c "import json,sys; print(json.load(sys.stdin)['hookSpecificOutput']['additionalContext'])"
```

Expected: 例外なくメッセージ本文が1行で出力され、末尾が「〜タスクに直行してよい。」で終わる。

- [ ] **Step 5: commit**

```bash
git add hooks/inject-talk-mode.sh
git commit -m "feat(hooks): SessionStart で talk-always-on を注入するフックを追加"
```

---

## Task 2: `hooks/hooks.json` に `SessionStart` エントリを登録する

**Files:**
- Modify: `hooks/hooks.json`（全体、現状17行）

**Interfaces:**
- Consumes: Task 1 で作成した `hooks/inject-talk-mode.sh`（パスのみ、コマンドとして呼び出す）。
- Produces: プラグインロード時に `SessionStart`（matcher `startup|clear|compact`）と `PreCompact`（既存、matcher `*`）の両方が登録された状態。Task 3 はこのファイルを変更しない。

- [ ] **Step 1: 現在の内容を確認する**

Run:
```bash
cat hooks/hooks.json
```

Expected（変更前の現状）:
```json
{
  "description": "rig run-continuity — preserve the active harness run-state across context compaction (PreCompact stdout becomes custom compaction instructions).",
  "hooks": {
    "PreCompact": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/preserve-rig-state.sh"
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: ファイル全体を次の内容に置き換える**

`hooks/hooks.json` を次の内容で上書きする:

```json
{
  "description": "rig hooks — (1) run-continuity: preserve the active harness run-state across context compaction, and carry the talk-always-on directive across compaction (PreCompact stdout becomes custom compaction instructions). (2) talk-always-on: inject a standing instruction so interactive sessions default to routing user turns through rig:talk (SessionStart additionalContext).",
  "hooks": {
    "PreCompact": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/preserve-rig-state.sh"
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "startup|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PLUGIN_ROOT}/hooks/inject-talk-mode.sh"
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 3: JSON 妥当性を確認する**

Run:
```bash
python3 -m json.tool hooks/hooks.json > /dev/null && echo OK
```

Expected: `OK`（パースエラーが出ないこと）。

- [ ] **Step 4: 登録内容を確認する**

Run:
```bash
python3 -c "
import json
d = json.load(open('hooks/hooks.json'))
assert 'PreCompact' in d['hooks']
assert 'SessionStart' in d['hooks']
ss = d['hooks']['SessionStart'][0]
assert ss['matcher'] == 'startup|clear|compact'
assert ss['hooks'][0]['command'].endswith('inject-talk-mode.sh')
print('OK')
"
```

Expected: `OK`。

- [ ] **Step 5: commit**

```bash
git add hooks/hooks.json
git commit -m "feat(hooks): hooks.json に talk-always-on の SessionStart 登録を追加"
```

---

## Task 3: `hooks/preserve-rig-state.sh` に圧縮境界の二重化を追記する

**Files:**
- Modify: `hooks/preserve-rig-state.sh:8-16`（既存の `cat <<'EOF' ... EOF` ブロック）

**Interfaces:**
- Consumes: なし（Task 1/2 とは独立に追記できる）。
- Produces: `PreCompact` 発火時、既存の run-state 保全指示に加えて talk-always-on 維持指示も stdout に含む（`SessionStart(compact)` の既知バグを補完する belt-and-suspenders）。

- [ ] **Step 1: 現在の内容を確認する**

Run:
```bash
cat hooks/preserve-rig-state.sh
```

Expected（変更前）:
```sh
#!/usr/bin/env sh
# rig run-continuity — PreCompact hook.
# Fires just before context compaction (manual /compact or automatic).
# On exit 0, stdout is appended to the compaction as custom instructions,
# so the harness preserves the active rig run-state in the summary.
# It cannot read the live conversation; it tells the compaction WHAT to keep.

cat <<'EOF'
[rig run-continuity] If a rig harness run is active, the compaction summary MUST preserve:
- the rig run-status line: recipe (or ad-hoc), current step id + position (n/N), gate state, backend, mode (gated/autonomous);
- the active recipe's step list — which steps are done, which remain, and the current step id;
- the acceptance contract in force (acceptance-gate criteria / the goal-loop goal) and any unresolved REJECT or merge-blocking conditions;
- the user's goal/intent, key decisions made, and any stuck-guard counters (no-progress rounds);
- the context-minimal discipline: real work is delegated to subagents; the parent only dispatches, aggregates structured reports, and makes gate decisions.
After compaction, on the first work turn, re-emit the rig run-status header and re-anchor to the current step BEFORE doing any work. Do not silently switch to direct, un-gated work (see SKILL.md §6 run-continuity).
EOF
```

- [ ] **Step 2: ファイル全体を次の内容に置き換える**

`hooks/preserve-rig-state.sh` を次の内容で上書きする（先頭コメントは変更なし、`cat` ブロックの末尾に段落を1つ追加）:

```sh
#!/usr/bin/env sh
# rig run-continuity — PreCompact hook.
# Fires just before context compaction (manual /compact or automatic).
# On exit 0, stdout is appended to the compaction as custom instructions,
# so the harness preserves the active rig run-state in the summary.
# It cannot read the live conversation; it tells the compaction WHAT to keep.

cat <<'EOF'
[rig run-continuity] If a rig harness run is active, the compaction summary MUST preserve:
- the rig run-status line: recipe (or ad-hoc), current step id + position (n/N), gate state, backend, mode (gated/autonomous);
- the active recipe's step list — which steps are done, which remain, and the current step id;
- the acceptance contract in force (acceptance-gate criteria / the goal-loop goal) and any unresolved REJECT or merge-blocking conditions;
- the user's goal/intent, key decisions made, and any stuck-guard counters (no-progress rounds);
- the context-minimal discipline: real work is delegated to subagents; the parent only dispatches, aggregates structured reports, and makes gate decisions.
After compaction, on the first work turn, re-emit the rig run-status header and re-anchor to the current step BEFORE doing any work. Do not silently switch to direct, un-gated work (see SKILL.md §6 run-continuity).

[rig talk-always-on] If this session was operating under the talk-always-on directive (interactive user turns routed through rig:talk — see hooks/inject-talk-mode.sh), the compaction summary MUST preserve that directive so it keeps applying after compaction, since SessionStart(source=compact) may not re-inject it. This does not apply to a subagent/headless session already working a specific task directly.
EOF
```

- [ ] **Step 3: 実行して新しい段落が含まれることを確認する**

Run:
```bash
./hooks/preserve-rig-state.sh | grep -c "talk-always-on"
```

Expected: `2`（見出し行 `[rig talk-always-on]` と本文中の `talk-always-on directive` への言及の2箇所がヒットする）。

- [ ] **Step 4: 既存の run-continuity 段落が壊れていないことを確認する**

Run:
```bash
./hooks/preserve-rig-state.sh | grep -c "rig run-continuity"
```

Expected: `1`。

- [ ] **Step 5: commit**

```bash
git add hooks/preserve-rig-state.sh
git commit -m "fix(hooks): PreCompact で talk-always-on 指示も圧縮境界を越えて維持する"
```

---

## Task 4: `.claude-plugin/plugin.json` の version を bump する

**Files:**
- Modify: `.claude-plugin/plugin.json:3`

**Interfaces:**
- Consumes: なし。
- Produces: プラグインの version フィールドが `0.63.0` になる（Task 1〜3 の機能追加を表す）。

- [ ] **Step 1: 現在の version 行を確認する**

Run:
```bash
grep -n '"version"' .claude-plugin/plugin.json
```

Expected: `3:  "version": "0.62.0",`

- [ ] **Step 2: version を書き換える**

`.claude-plugin/plugin.json:3` を次のように変更する:

変更前:
```json
  "version": "0.62.0",
```

変更後:
```json
  "version": "0.63.0",
```

- [ ] **Step 3: JSON 妥当性と値を確認する**

Run:
```bash
python3 -c "import json; d = json.load(open('.claude-plugin/plugin.json')); assert d['version'] == '0.63.0'; print('OK')"
```

Expected: `OK`。

- [ ] **Step 4: commit**

```bash
git add .claude-plugin/plugin.json
git commit -m "chore: version bump 0.62.0 → 0.63.0 (talk-always-on hook)"
```

---

## Task 5: README.md / README.ja.md に talk-always-on を追記する

**Files:**
- Modify: `README.md`（`/rig:talk` の説明行、現状47〜48行付近）
- Modify: `README.ja.md`（`/rig:talk` の説明行、現状47〜48行付近）

**Interfaces:**
- Consumes: なし（ドキュメントのみ）。
- Produces: なし（後続タスク無し。本 Task が最終タスク）。

- [ ] **Step 1: README.md の該当行を確認する**

Run:
```bash
grep -n "rig:talk.*JARVIS-style" README.md
```

Expected: `48:- **Command**: \`/rig:talk\` — a JARVIS-style conversational mode: speak naturally, it routes your intent to the right rig flow (dev/sales) and runs it. e.g. \`/rig:talk just review my current changes\``

- [ ] **Step 2: README.md の行を書き換える**

`README.md` の該当行（Step 1 で確認した行）を次のように変更する:

変更前:
```
- **Command**: `/rig:talk` — a JARVIS-style conversational mode: speak naturally, it routes your intent to the right rig flow (dev/sales) and runs it. e.g. `/rig:talk just review my current changes`
```

変更後:
```
- **Command**: `/rig:talk` — a JARVIS-style conversational mode: speak naturally, it routes your intent to the right rig flow (dev/sales) and runs it. e.g. `/rig:talk just review my current changes`. A shipped `SessionStart` hook makes this the default for every interactive session — you don't need to type `/rig:talk` explicitly. It steps aside for subagent/headless runs and for explicit `/rig:*` commands.
```

- [ ] **Step 3: README.ja.md の該当行を確認する**

Run:
```bash
grep -n "rig:talk.*JARVIS" README.ja.md
```

Expected: `48:- **コマンド**: \`/rig:talk\` — JARVIS 的な会話モード。話しかけると意図を汲んで適切な rig フロー(dev/sales)へ橋渡しして実行する。例: \`/rig:talk 今の変更だけ軽くレビューして\``

- [ ] **Step 4: README.ja.md の行を書き換える**

`README.ja.md` の該当行（Step 3 で確認した行）を次のように変更する:

変更前:
```
- **コマンド**: `/rig:talk` — JARVIS 的な会話モード。話しかけると意図を汲んで適切な rig フロー(dev/sales)へ橋渡しして実行する。例: `/rig:talk 今の変更だけ軽くレビューして`
```

変更後:
```
- **コマンド**: `/rig:talk` — JARVIS 的な会話モード。話しかけると意図を汲んで適切な rig フロー(dev/sales)へ橋渡しして実行する。例: `/rig:talk 今の変更だけ軽くレビューして`。同梱の `SessionStart` フックにより、対話セッションでは既定でこの導線を通る（毎回 `/rig:talk` と打つ必要はない）。サブエージェント/headless 実行や明示的な `/rig:*` コマンドではこの限りでない。
```

- [ ] **Step 5: 両ファイルに talk-always-on 相当の追記があることを確認する**

Run:
```bash
grep -c "SessionStart" README.md README.ja.md
```

Expected: 両ファイルとも `1` 以上（`README.md:1` `README.ja.md:1` のように出力される）。

- [ ] **Step 6: commit**

```bash
git add README.md README.ja.md
git commit -m "docs: README に talk-always-on（SessionStart hook）を追記"
```

---

## 手動確認（全 Task 完了後、実際の Claude Code セッションで）

コード上の自動テストが無いプラグインのため、実機確認を最後に行う:

1. 別ターミナルで rig プラグインが有効なプロジェクトを新規セッション（`claude` 起動）で開く。
2. セッション冒頭のシステムリマインダーに talk-always-on の指示が注入されていることを確認する。
3. 何か雑な話し言葉（例:「今の変更ちょっとレビューして」）を送り、talk-loop の手順（正規化→ルーティング→起動文字列の一言確認→委譲→短い話し言葉での報告）で応答することを確認する。
4. `/rig:dev --only review` のように明示的にスラッシュコマンドを打った場合は、talk 導線を経由せず通常通り動くことを確認する（spec 受け入れ基準5）。
5. 可能であれば `/compact` を手動発火し、圧縮後も talk-loop の適用が継続することを確認する（spec 受け入れ基準3）。

この手動確認は自動化タスクの対象外（herness に headless/CI 実行環境が無いため）。結果はユーザーに報告する。
