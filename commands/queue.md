---
description: "rig/queue — タスクを積んで、まとめて GO。キューを管理ツール(GitHub/GitLab Issue)かローカルで持ち、go で全タスクを並列実行(各タスクをゲート通過)して結果を Issue に書き戻す。"
argument-hint: "<add \"task\" | list | go | done id | retry id> [--backend local|github|gitlab] [--repo owner/repo] [--provider rig] [--max-parallel N]"
---

# rig/queue — タスクキュー（積んで GO） 📋

**まず `rig` skill を Skill ツールで起動し、その SKILL.md（context-minimal・計算的オーケストレーション §4.3）に従うこと。** キューの実体は `scripts/orchestrate.py queue`（決定論ランナー＝GO エンジン）。

```
$ARGUMENTS
```

## やること

「1依頼ずつ流す」から「**溜めて一括**」へ。タスクを積み、まとめて並列実行する。

```
orchestrate queue add "<やること>"        # 積む
orchestrate queue list                    # 確認（失敗理由・完了コメントは note として行末に表示）
orchestrate queue go --provider rig --max-parallel 3   # まとめて GO
orchestrate queue done <id>               # 手動で完了に
orchestrate queue retry <id>              # failed（検証 FAIL）の item を queued に戻して再 GO 対象にする
```

- **go**＝積まれた全タスクを実行：独立タスクは**別プロセスで並列**、各タスクは生成→**独立検証（採点者≠生成者）**のゲートを通過、結果を一括レポート。中身は既存の orchestrate（並列・マルチプロバイダ・local LLM）をそのまま GO エンジンに使う。
- provider は `rig`（各タスクを rig ハーネスで実行・推奨）/ `claude` / `codex` / `ollama` / `lmstudio` / `cmd` / `mock`。
- **`--provider rig`（既定）は各 item を `/rig:rig "<task>"` 経由で dispatch する**——`patterns/isolated-worktree` により各タスクが自動的に専用 worktree へ隔離されるため、**並列実行中の headless プロセス同士が同じファイルを取り合う心配がない**。queue の verifier は「gate まで確定したか」＋「本体の作業ツリーに書き込まず isolated worktree 内で完結したか」を判定するだけで、**accept はしない**（queue は隔離・実行・ゲートの層、反映はユーザーの明示操作）。
- **`queue list` は done を除くアクティブ item（queued/running/failed）のみ表示する**（`local`/`github`/`gitlab` 共通）。完了済みタスクで一覧が肥大化しない。
- **`queue retry <id>`**＝検証 FAIL で `failed` になった item を `queued` に戻し、次の `queue go` の実行対象に含める。プロバイダの一時的なタイムアウト等で落ちたタスクをタスク文の打ち直し（＝別 id・別 Issue）なしに再試行できる。

## 複数タスクを並行で進める（ターミナルを増やさず一括把握）

```
/rig:queue add "ログイン画面のバグを直して"
/rig:queue add "在庫一覧に検索機能を追加して"
/rig:queue go --provider rig --max-parallel 3   # 3タスクを並列 dispatch（各々 isolated worktree）

/rig:rig board       # 今どのタスクがどこまで進んだか、1コマンドで一覧
/rig:rig diff <id>   # 個別に差分確認 → /rig:rig accept <id> で個別に反映
```

複数のターミナルを開いて「どれが何をしていたか忘れる」問題は、`/rig:rig board` が単一の真実の情報源になることで解消する。

## バックエンド（キューをどこで持つか）

| backend | 実体 | 状態管理 |
|---|---|---|
| `local`（既定） | `<repo>/.rig/queue.json` | json の status |
| `github` | GitHub Issues（`gh` CLI） | ラベル `rig-queue→rig-running→rig-done`／コメントに結果／完了で close |
| `gitlab` | GitLab Issues（`glab` CLI） | 同上 |

`--backend github --repo owner/repo` で Issue 連携。**チームで共有・永続する backlog** になり、rig がそこから引いて実行・結果を Issue に書き戻す。要：`gh`/`glab` CLI が認証済み（未インストールでも crash せず error 表示）。

## 他フローとの連結

- `/rig:brainstorm` → `/rig:tasks` で割った各タスクを **queue add** で積む → `queue go` で一括実行。
- 「終わりのある仕事」を溜めて回す＝`/rig:goal`（達成収束）・`/rig:loop`（繰り返し）と別軸。

## 例

```
/rig:queue add "JWT リフレッシュを追加"
/rig:queue add "検索の N+1 を直す"
/rig:queue go --provider rig --max-parallel 3
/rig:queue go --backend github --repo itoh-shun/rig    # Issue から引いて実行・書き戻し
```


## run-continuity（SKILL.md §6）

RUN 中は各ターン冒頭に次の run-status ヘッダを1行必ず再掲すること。中断・質疑・tool 出力の直後でも省かない（可視化＝駆動の証拠）:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
