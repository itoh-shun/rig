# rig 改善アイデアの自動 Issue 化（毎日12時）— design / 実装 spec

- 日付: 2026-06-22
- ブランチ: `claude/rig-goal-loop-resolution-2nl735`
- 種別: 自動化（GitHub Actions scheduled workflow）

## 目的

rig の改善を止めないために、**毎日 昼12時（JST）に1件だけ**、リポジトリに根ざした改善アイデアを GitHub Issue として自動生成する。実装はせず**アイデアの backlog**を作るに留め、採否は人間が選別する（無監督の自動実装が生む AI-slop / churn を避ける）。

## なぜ GitHub Actions cron か

- 「毎日決まった時刻」に確実に発火するのは scheduled workflow だけ。Claude Code のセッション内ループ（`/loop` / `ScheduleWakeup`）は**一時コンテナの寿命に依存**して止まるため、定時の日次実行には不適。
- Actions ならセッション/コンテナに依存せず恒久的に回る。

## 設計

`.github/workflows/rig-daily-idea.yml`：

- `schedule: cron "0 3 * * *"`（03:00 UTC = **12:00 JST**）＋ `workflow_dispatch`（手動テスト）。
- `permissions: issues: write`、`concurrency` で多重起動防止。
- ラベル `rig-idea` / `automated` を冪等作成（`gh label create --force`）。
- `anthropics/claude-code-action@v1` に prompt を渡し、Claude が：repo を読む → 既存 Issue を見て重複回避 → **最も価値ある1件**を選ぶ → `gh issue create` で1件だけ作成（実装はしない）。
- `--allowedTools` を read 系＋ `gh issue`/`gh search`/`git log` に限定。

## 受け入れ基準

1. デフォルトブランチに置かれ、毎日 03:00 UTC（12:00 JST）に発火する（手動 `workflow_dispatch` でもテストできる）。
2. 1 回の実行で **Issue を高々1件**作成し、`rig-idea,automated` ラベルが付く。
3. 既存 Issue と重複する場合は作成を見送る。
4. 実装・コミット・PR はしない（アイデア出しのみ）。
5. 本文に背景/提案/受け入れ基準/影響範囲/参照が含まれる。

## 有効化に必要な操作（リポジトリ管理者）

1. **Secret 追加**：`Settings → Secrets and variables → Actions` に `ANTHROPIC_API_KEY` を登録。
2. **デフォルトブランチへ反映**：scheduled workflow は**デフォルトブランチ上のファイルのみ**発火するため、この workflow を `master` にマージする。
3. **テスト**：`Actions → rig daily improvement idea → Run workflow` で即時実行を確認。

## 注意・既知の制約

- GitHub cron は**負荷時に数分〜遅延**することがある（定時厳密ではない）。
- リポジトリが **60 日無アクティビティ**だと scheduled workflow は自動停止する。
- 時刻変更は cron 値を編集（例 13:00 JST = `0 4 * * *`）。
- コスト：1日1回・軽量だが、API 課金が発生する。`--max-turns` と read 限定でコストを抑制。

## 非スコープ

- アイデアの**自動実装**（別途。実装は人間が Issue を選んで `/rig:dev` 等で着手）。
- 複数アイデアの一括生成（1日1件に限定）。
- master への自動マージ・自動 PR。
