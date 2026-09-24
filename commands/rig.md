---
description: "[deprecated: use /rig:go; removed in 4.0.0] rig — the unified entry point, under its old name. Identical to /rig:go in every argument and subcommand: it classifies a natural-language task, runs it in an isolated worktree, judges it at the acceptance gate, and carries the same status, diff, accept, discard, log, board, cockpit, stats, review, gc, audit, scan, digest and gh subcommands. Prefer /rig:go; this name goes in 4.0.0."
argument-hint: "\"<task in plain language>\" | status [id] | diff [id] | accept [id] [--force] | discard <id> --yes | log [--limit N] | board [--all] | cockpit | stats [--recipe R] [--verifier P] [--last Nd] | confidence [id] | review <id> --set p=v [--body p=@path] | note [id] <text> [--about PATH] | gc [--older-than Nd] [--dry-run] | audit [--limit N] [--action A] [--since YYYY-MM-DD] | scan-secrets [paths…|--diff id] | scan-injection [paths…|--diff id] | scan-ja-prose [id] | digest [--period week|month] [--out PATH] | context [--since-days N] | stream-checks [id] [--watch --interval N --max-passes M] | stale-refs [paths…] | scan-destructive [paths…|--diff id] | scan-anchors [paths…|--diff id] | instincts [--add TEXT --evidence E --confidence C] [--mute ID|--expire ID|--decay|--inject-preview] | gh issue <n> | gh pr <n> review|fix | gh ci"
---

# /rig:rig — a deprecated shim for /rig:go

**Deprecated.** Use `/rig:go`. This name still works for the whole of 3.x and is **removed in 4.0.0**, after which typing it gets the host's unknown-command error. Nothing else changes in the meantime: `/rig:go` takes the same arguments and the same subcommands, so the fix is to change the word.

Read `commands/go.md` in this plugin and behave exactly as `/rig:go` with the same arguments:

```
$ARGUMENTS
```

Everything — subcommand routing, the isolated-worktree workbench flow, the acceptance gate, the run-continuity header — is defined in `commands/go.md`. This file adds nothing and decides nothing.
