---
description: "rig/setup — install the rig-wb CLI (the pip package) through pipx, uv, or pip. The first-time setup when you start using rig as a skill, and the common ground that lets other providers (Codex, Cursor, Copilot) delegate to the same CLI."
argument-hint: "[--yes skip prompts] [--force reinstall] [--check detect only] [--uninstall] [--ref <branch|tag|sha>]"
---

# rig/setup — the rig-wb CLI installer

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (context-minimal, the knowledge layer, §6 run-continuity). This command is only the entry point; the work is done by `scripts/install.sh` and is not repeated here.

Then PARSE the following arguments and pass them to the installer:

```
$ARGUMENTS
```

## What it does

Runs `scripts/install.sh` **through the Bash tool**. The installer, in order:

1. **Checks the GitHub CLI (optional)**: whether `gh` itself is present and whether the `github/gh-stack` extension is installed. If the extension is missing it **offers** to install it (`--yes` skips the prompt, `--force` reinstalls, `--check` only detects). Declining changes nothing, and neither does not having `gh` at all — **it is not required**. `gh` itself is a system package, so it is never installed behind your back. **Authentication is not a requirement either**: the state is displayed, and `gh auth login` is neither run nor demanded.
2. **Detects the environment**: which of `pipx`, `uv`, or `pip` is usable, preferred in that order.
3. **Compares against what is installed**: not "is it there" but **"does it match this checkout"**. It compares `rig-wb version` against `__version__` in `rig_workbench/__init__.py` and skips when they agree. When they disagree it **shows both versions and asks whether to update** (`--yes` skips the prompt, `--force` always reinstalls, `--check` only displays). It never swaps one for the other silently.
4. **Confirms**: shows what will be installed and how before continuing (skipped under `--yes`, or once you agreed to the update in step 3).
5. **Installs**: from `github.com/itoh-shun/rig.git` over git+URL.
6. **Verifies**: `rig-wb version` answering is enough. If it is not on PATH, it points at `pipx ensurepath` or adding `~/.local/bin`.

## gh and gh-stack are optional

**Neither `gh` nor the `github/gh-stack` extension is a requirement.** `workbench new`, `orchestrate run|init|ab`, and `queue go` all work without them. When they are absent you get **one line on stderr**, and nothing stops.

They used to be required, and the reason for that collapsed under measurement. The reason was delegating the cascade rebase of a stacked branch to `gh stack` — but `gh stack` switches branches by checking them out, and git **refuses to check out a branch another worktree is holding**. rig creates a worktree per task, so the target branch is always held:

```
$ gh stack rebase --no-trunk
✗ could not start rebase of task2 onto task1: failed to run git:
  fatal: 'task2' is already used by worktree at '.../wt2'
```

Worktree isolation is the core of rig's safety and does not move, so the side that could not do the operation it was required for was the side that got dropped. The cascade happens inside each worktree with plain git (`git -C <child> rebase --onto ...`). Where `gh stack` still earns its place is **the publishing side** — declaring a stack, `submit`, `push` — which a workflow that opens no PRs does not need at all. Hence one line of guidance rather than a gate.

**Authentication and a remote are just as optional.** `gh stack`'s local operations work unauthenticated and with no remote; only `push`, `submit`, and `sync` touch GitHub. `gh-check` **only displays** the authentication state.

You can ask for the current state at any time — and because that is an explicit question, the answer is always complete:

```
rig-wb gh-check           # exit 0=ok / 3=no gh / 5=no gh-stack (auth state shown, never enforced)
rig-wb gh-check --json
```

`RIG_SKIP_GH_CHECK=1` silences the one-line notice — silences, not unblocks, because nothing was blocking. `gh-check` and `/rig:setup` are explicit requests to be told about the environment, so that variable does not silence them. The implementation is `rig_workbench/gh_requirement.py` (the single source of truth; install.sh reproduces the same state names in bash).

## Why this exists

rig runs **as a skill inside Claude Code** (`/rig:go`) and **as the `rig-wb` CLI** you get from `pip install rig-workbench`. A skill in another provider — a Codex plugin, Cursor rules, a Copilot extension — that calls the same `rig-wb` gets the same workbench: the same recipes, gates, accept, and dashboard. It is the ground that lets rig **live inside whichever AI coding tool you use, instead of asking you to switch**.

## Flags

- `--yes` — install without the interactive prompts (what a skill uses when it runs this itself), including the gh-stack confirmation. It still **never swaps silently**: an update always prints one line saying what is being replaced with what, from where. When the installed version is **newer** than the checkout, the change would be a downgrade and `--yes` will not do it; `--force` is the only way through.
- `--force` — reinstall even when it is already installed (and reinstall gh-stack), ignoring which version is newer.
- `--check` — detect and exit. 0 means there is a way to install, 1 means there is not. The gh and gh-stack state and any version mismatch are **displayed without affecting the exit code**, and neither prompts nor installs.
- `--uninstall` — remove `rig-workbench`, working out from how it was installed (pipx, uv, or pip).
- `--ref <ref>` — install that branch, tag, or commit from GitHub. **The default is install.sh's own checkout rather than a ref**: unless what is compared and what is installed are the same thing, agreeing to an update leaves the mismatch in place and the prompt comes back. With an explicit `--ref` there is no way to know that version locally, so no version comparison and no update offer happen — it falls back to presence only.

## Examples

```
/rig:setup                 # install interactively (recommended the first time)
/rig:setup --yes           # install without prompts
/rig:setup --check         # only find out whether this environment can install it
/rig:setup --force         # reinstall this checkout even over a newer version
/rig:setup --uninstall     # remove it
/rig:setup --ref v1.3.0    # pin to a GitHub tag (no version comparison)
```

## What you can do afterwards

```
rig-wb --help                  # the subcommands
rig-wb wb board                # the workbench's state
rig-wb plan bugfix             # show a plan
rig-wb runs --html /tmp/x.html # an HTML dashboard
```

That is what lets the same workbench be driven **from outside Claude Code** — `rig-wb ...` from the Codex CLI, Cursor, or a plain terminal.

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output — the visibility is the evidence that the harness is driving:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
