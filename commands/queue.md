---
description: "rig/queue — タスクを積んで、まとめて GO。キューを管理ツール(GitHub/GitLab Issue)かローカルで持ち、go で全タスクを並列実行(各タスクをゲート通過)して結果を Issue に書き戻す。"
argument-hint: "<add \"task\" | list | go | done id> [--backend local|github|gitlab] [--repo owner/repo] [--provider rig] [--max-parallel N]"
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
orchestrate queue list                    # 確認
orchestrate queue go --provider rig --max-parallel 3   # まとめて GO
orchestrate queue done <id>               # 手動で完了に
```

- **go**＝積まれた全タスクを実行：独立タスクは**別プロセスで並列**、各タスクは生成→**独立検証（採点者≠生成者）**のゲートを通過、結果を一括レポート。中身は既存の orchestrate（並列・マルチプロバイダ・local LLM）をそのまま GO エンジンに使う。
- provider は `rig`（各タスクを rig ハーネスで実行・推奨）/ `claude` / `codex` / `ollama` / `lmstudio` / `cmd` / `mock`。

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
