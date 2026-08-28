---
description: "rig/usage — count when, from where, and how often rig-wb was used. Defaults to .rig/runs.jsonl in the current directory (per project); --global reads ~/.rig/runs.jsonl (across every project, broken down by project). Tells runs that went through rig-wb apart from bare direct calls."
argument-hint: "[--global | -g] [--limit N] [--json]"
---

# rig/usage — what actually got used

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (context-minimal, the knowledge layer, §6 run-continuity). This command is only the entry point; the work is done by `rig-wb usage` (`rig_workbench/cli.py`) and is not repeated here.

Then PARSE the following arguments and pass them to `rig-wb usage`:

```
$ARGUMENTS
```

## What it does

Run one of these **through the Bash tool** — the first when `rig-wb` is installed, the second as a fallback:

```bash
rig-wb usage $ARGUMENTS
# when not installed:
python3 -m rig_workbench.cli usage $ARGUMENTS   # inside the rig repo
python3 "$RIG_HOME/rig_workbench/cli.py" usage $ARGUMENTS
```

The output reads `.rig/runs.jsonl` and counts by invoker:

- `◆ rig-wb/<version>` — runs that went through the rig-wb CLI
- `direct (not via rig-wb)` — runs that called `scripts/*.py` directly
- with `--global`, a **per-project** section as well

## Flags

- `--global` / `-g` — count `~/.rig/runs.jsonl` instead, across every project. The default is `.rig/runs.jsonl` in the current directory.
- `--limit N` — count only the most recent N runs (`--limit 100`).
- `--json` — machine-readable output (`scope`, `runs_path`, `total`, `by_invoker`, `last_seen_by_invoker`, and `by_project` under `--global`). For CI and dashboards.

## Examples

```
/rig:usage                       # .rig/runs.jsonl here, as text
/rig:usage --global              # across every project, as text
/rig:usage --global --limit 100  # only the last hundred runs
/rig:usage --json                # machine-readable
/rig:usage --global --json       # machine-readable, across projects
```

## When to reach for it

- **You installed it but are not sure you use it** — `/rig:usage --global` gives the ratio: N runs through rig-wb out of M in total.
- **You want to know which projects call it** — the per-project section of `/rig:usage --global` shows where it came from.
- **You want to count from CI or a dashboard** — `--json`.
- **You want to know which versions are in use** — `by_invoker` counts `rig-wb/1.6.0`, `rig-wb/1.5.0`, and one day `rig-codex/0.1.0` separately.

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output — the visibility is the evidence that the harness is driving:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
