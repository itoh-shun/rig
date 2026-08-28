---
description: "[a compatibility alias of /rig:go; only the name is deprecated] rig — the unified entry point, under its old name. Behaves exactly as /rig:go: classify a natural-language task, run it in an isolated worktree, judge it at the acceptance gate, accept it; plus the status, diff, accept, discard, log, board, cockpit, stats, review, gc, audit, scan-secrets, scan-injection, digest, and gh subcommands."
argument-hint: "\"<task in plain language>\" | status [id] | diff [id] | accept [id] [--force] | discard <id> --yes | log [--limit N] | board [--all] | cockpit | stats [--recipe R] [--verifier P] [--last Nd] | review <id> --set p=v | gc [--older-than Nd] [--dry-run] | audit [--limit N] [--action A] [--since YYYY-MM-DD] | scan-secrets [paths…|--diff id] | scan-injection [paths…|--diff id] | digest [--period week|month] [--out PATH] | gh issue <n> | gh pr <n> review|fix | gh ci"
---

# /rig:rig — a compatibility alias of /rig:go

This command is a compatibility alias. Read `commands/go.md` in this plugin and behave exactly as `/rig:go` with the same arguments:

```
$ARGUMENTS
```

The alias is not deprecated behaviour — only the name moved. Everything (subcommand routing, the isolated-worktree workbench flow, the acceptance gate, the run-continuity header) is defined in `commands/go.md`.
