---
description: "[deprecated-in-name-only alias of /rig:go] rig — 統一入口の互換エイリアス。/rig:go と完全に同じ動作（自然文タスクの分類→隔離worktree→acceptance-gate→accept、および status/diff/accept/discard/log/board/stats/review/gc/audit/scan-secrets/scan-injection/digest/gh サブコマンド）。"
argument-hint: "\"<自然文タスク>\" | status [id] | diff [id] | accept [id] [--force] | discard <id> --yes | log [--limit N] | board [--all] | stats [--recipe R] [--verifier P] [--last Nd] | review <id> --set p=v | gc [--older-than Nd] [--dry-run] | audit [--limit N] [--action A] [--since YYYY-MM-DD] | scan-secrets [paths…|--diff id] | scan-injection [paths…|--diff id] | digest [--period week|month] [--out PATH] | gh issue <n> | gh pr <n> review|fix | gh ci"
---

# /rig:rig — compatibility alias of /rig:go

This command is a compatibility alias. Read `commands/go.md` in this plugin and behave exactly as `/rig:go` with the same arguments:

```
$ARGUMENTS
```

Do not treat the alias itself as deprecated behavior — only the name moved. Everything (subcommand routing, isolated-worktree workbench flow, acceptance-gate, run-continuity header) is defined in `commands/go.md`.
