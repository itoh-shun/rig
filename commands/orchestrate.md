---
description: "rig/orchestrate — computational orchestration. A deterministic runner (scripts/orchestrate.py) enforces a recipe's step transitions, gates, retries, stopping, and state in code. `run` executes each step as a rig harness in its own process, with parallel verification across providers."
argument-hint: "[recipe (defaults to the currently composed harness)] [--run] [--provider rig|claude|mock] [--isolate] [--auto-route] [--max-parallel N] [--quorum all|majority] [--plan]"
---

# rig/orchestrate — computational orchestration 🧭⚙️

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md.** This command is the way into `--orchestrate`: the mode where **code holds the wheel**. The control loop — which step is next, whether a gate passed, retries, stopping conditions, keeping state — is enforced by **`scripts/orchestrate.py`, a deterministic runner**, rather than by prose. `patterns/computational-orchestration` holds the procedure and the contracts.

```
$ARGUMENTS
```

## Two ways to use it

**1. Semi-automatic (the model does each step's work)**
The runner decides the transitions; the model executes each step by delegation:
```
orchestrate plan   <recipe>            # compute the step state machine (the equivalent of --plan; no model needed)
orchestrate init   <recipe> [--goal G] # create the run-state and the first action
orchestrate next   run-state.json      # compute the next transition deterministically (START/ADVANCE/RETRY/AWAIT/BLOCKED/ESCALATE/DONE)
orchestrate check  run-state.json      # run the step's checks: (lint, tests) — the computational sensors
orchestrate verdict run-state.json --by <reviewer> --pass|--fail   # an independent verifier's judgement (grader != generator)
```

**2. Fully automatic (each step runs as a rig harness in its own process)**
```
orchestrate run <recipe> --provider rig --isolate \  # worktree isolation; only green fast-forwards back
    [--verifier-provider rig] [--max-parallel N] [--quorum all|majority] [--goal G]
```
- **`--provider rig`**: each step runs as **a separate process started through the `rig:engine` skill** — rig calling rig by name, a recursive harness. The alternatives are `claude`, `codex`, **`grok`** (grok-build headless, `grok -p`; its read-only and sandbox flags are undocumented, so a verifier's read-only constraint is a prompt contract only — #328), **`ollama` and `lmstudio`** (local, OpenAI-compatible), **`anthropic`** (the Anthropic Messages API over HTTP; detects Fable 5's refusal classifier and the fallback — #297, see 5 below), `cmd` (any CLI), and `mock`. Local models take `--model <name>` (ollama defaults to `llama3.1`) and `--base-url <url>`, and need the server running.
- **`--auto-route`** (#264): choose deterministically among a step's `auto_route.candidates` — a list of `{model, cost_tier, max_size}` declared cheapest first — according to the current diff size, measured the same way `--diff-git` measures it. A step that declares no `auto_route` is untouched, and its existing `model:` or `--model` still wins. The reason for each choice is recorded in the run-state's `history` (`action: AUTO_ROUTE`) and in `runs.jsonl` under `steps[].auto_route`.
- **`--auto-route-learn`** (#305): takes `--auto-route` further by learning from `.rig/runs.jsonl` — which models were used per recipe and step, and how often they passed the gate — on frequency. It runs in **shadow mode** by default: the prediction is recorded in `steps[].learned_route` and does not affect the choice. `--auto-route-mode active` is what applies it. With too few reference runs or a low pass rate it falls back to static `--auto-route` and always records the counterfactual — why the prediction was rejected. `--exploration-pct N [--exploration-date D]` tries the runner-up on a fraction of runs, decided deterministically from `--exploration-date` and a hash of the recipe and step. No randomness.

**3. Discovering models dynamically (configure what is actually available)**
```
orchestrate models [--save] [--json]   # discover the running LLM servers and CLIs
orchestrate run <recipe> --provider ollama --auto-model   # pick a model from what is really there
```
- `models` calls `/v1/models` on `ollama` and `lmstudio` to **fetch the available models live**, and reports whether the `claude`, `codex`, and `rig` CLIs are present. `--save` writes `~/.claude/rig/models.json`, which `--auto-model` reads next time.
- **`--auto-model`** (also `--auto-model-setting`): with no `--model`, resolve one from the saved settings, then the first entry from a live `/v1/models`, then the default. A missing server falls back to the default rather than crashing.

**4. Testing a provider end to end (`probe`)**
```
orchestrate probe --provider codex                 # one call in the verifier role, checking for a VERDICT
orchestrate probe --provider codex --role generator
orchestrate probe --provider ollama --model llama3.1
```
Runs the provider **exactly once** and shows (1) the command or endpoint actually used, (2) the exit code, (3) the raw output, and (4) whether the contract (`VERDICT` or `STATUS`) parses. Exit 0 means rig can use it. On a `✗`, match the real command and flags with `--provider-cmd "codex exec --... {prompt}"` (the cmd provider).
- **Parallel verification**: a gated step's `personas` fan out into concurrent processes (`--max-parallel`). Aggregation is deterministic (`--quorum all` for unanimity, `majority` for a majority).
- **judge-panel**: `--generators rig,claude,codex` has several models generate the same step in parallel, and the judge picks the first candidate to PASS — in list order, so it is deterministic.
- **Step-DAG parallelism**: when recipe steps carry `needs: [id…]`, independent steps whose dependencies are met run concurrently in the same wave (intake → design and test together → merge).
- **Grader ≠ generator, structurally**: verification is a rig verifier in a separate process — a separate provider if you like — returning `VERDICT: PASS|FAIL`.

**5. Fable 5's refusal classifier and the fallback (`--provider anthropic`, #297)**
```
orchestrate run <recipe> --provider anthropic --model claude-fable-5 --step-model <id>=<model>
```
`--provider anthropic` calls the Anthropic Messages API directly over HTTP rather than through the `claude` or `rig` CLI providers — a CLI runs with `--output-format text` and so has no structured `stop_reason`. Setting `fallback_model` in `cfg` (`claude-opus-4-8`, say) requests `anthropic-beta: server-side-fallback-2026-06-01`, and when Fable 5's refusal classifier fires (cyber, bio, or reasoning_extraction) the server falls back to Opus 4.8 transparently.
- **The fallback worked**: `FABLE_FALLBACK` (from and to model) goes into `state["history"]` and **the gate is not stopped — the step's result is handled as usual**, which is what #297 asked for.
- **No fallback configured, or a direct refusal after it was exhausted**: `FABLE_REFUSAL` (category and explanation) is recorded and the step is told through rc=1. Never a silent failure.
- **Cost**: `usage.input_tokens`, `usage.output_tokens`, and `usage.cache_read_input_tokens` (the fallen-back prefix is billed at 10%) are aggregated into `runs --cost`'s `anthropic` row, and the counts of fallbacks and refusals appear at the end of the summary.

If you assign Fable 5 with `--step-model` to a persona whose job is discussing attack techniques — `security-reviewer` and its kin — always set `fallback_model` (see `agents/security-reviewer.md`).

**What was actually verified**: a mock HTTP server reproducing the Anthropic Messages API's response shapes confirmed all three paths — direct refusal, server-side fallback, ordinary success. It has never been pointed at the real Anthropic API, to avoid the cost and the production risk, so a real model actually returning `stop_reason: refusal`, and the real billing of a fallback, remain unverified.

**6. A/B experiments between recipes (`ab`, #291)**

```
orchestrate ab <recipe1> <recipe2> [...] --provider mock --goal "<goal>" [--verifier-provider V] [--max-steps N]
```

Runs the same task through several recipe variants **genuinely in parallel** and compares elapsed time, retry count, and final state. Each variant runs independently in an isolated worktree, exactly as under `--isolate`, so there is no file contention and a `ThreadPoolExecutor` is safe. The premise is that you are comparing **recipes, not models or providers** — one provider is given, shared by every variant.

```
## rig ab — recipes/bugfix.md vs recipes/hotfix.md

recipe               final      elapsed(s)   retries  worktree
bugfix               DONE       42.3         0        -
hotfix               DONE       18.7         1        -
```

A variant that did not finish, or whose tree is dirty, keeps its worktree (the same rule as `--isolate`). Clean up with `git worktree remove --force <dir>`.

**A/B on rules (differing manifests, #317)**: run one recipe under two conditions that differ only in the manifest. This answers a practical finding — a change that adds a rule cannot be evaluated statically; the only way to compare is to run real tasks.

```
orchestrate ab <recipe> --manifest-a <path> --manifest-b <path> --provider mock --goal "<goal>"
```

Each variant gets its manifest written as `.claude/rig.md` inside its own worktree (the main working tree is never touched), and its content hash is recorded in the trust store — **passing it explicitly on the CLI is the consent**, the same consent model as `--allow-project-manifest`. Rows in the comparison are labelled `A(<stem>)` and `B(<stem>)` so it is clear which manifest each was. **The honest scope**: a variant's manifest takes effect in **the nested provider calls that run with the worktree as their cwd**, because that is how a manifest is resolved. The parent orchestrate process's own `load_manifest()` — the size classification behind `--auto-route`, for instance — keeps reading the calling repository's manifest. Recipe, provider, and model are shared by every variant: what is being measured is only the difference in the rules.

**7. Turning gap prescriptions into a forge draft (`runs`, #268)**

```
orchestrate runs
```

Where the same (recipe, step) pair has escalated twice or more, "## Gap prescriptions" prints **a concrete `/rig:forge` draft request** — identifying the three reviewers that REJECTed at that step most often from the verdict records (`steps[].verdicts`) and writing them into the description. orchestrate.py never calls forge itself, since that needs a model; what it produces is a forge prompt you can paste. A person or an AI reviews and settles the draft, and `/rig:drill --replay` measures whether it improved anything.

**8. Delegating to the Managed Agents API (the review-gate fan-out; experimental, opt-in, #295)**

```python
cfg["parallel_backend"] = "managed-agents"
cfg["environment_id"] = "<the Managed Agents host environment id>"  # required
```

`_execute_step`'s parallel review-gate verification uses `run_verifiers_parallel` (subprocesses and a ThreadPoolExecutor) by default. Only when `cfg["parallel_backend"] == "managed-agents"` does `run_managed_agents_fanout` delegate to the Anthropic Managed Agents API — the coordinator-and-worker beta, `managed-agents-2026-04-01`. **The default path is completely unchanged.** One worker agent is created per persona, and a coordinator that only judges gathers them. It polls `threads.list` and stops when every worker has reported or `managed_agents_max_polls` is reached (30 by default, `managed_agents_poll_interval` 2 seconds). The return shape matches `run_verifiers_parallel` — a list of `{by, persona, provider, ok, note}` — so `_execute_step`'s pass and fail logic needs no change.

- **Required**: `cfg["environment_id"]`, the Managed Agents host environment. Unset, it returns an error verdict immediately rather than failing silently.
- **A worker that never reported**: any worker still missing when `max_polls` runs out is recorded explicitly as a `timeout` verdict, never quietly dropped.
- **Token accounting**: each worker thread's `usage` is aggregated into `cfg["_token_usage"]["managed-agents"]`, the same path `runs --cost` reads.
- **history**: a `MANAGED_AGENTS_SESSION` action (session id, worker count) is recorded in `state["history"]`. Integrating the event stream itself with the run-continuity header is not implemented.

**What was actually verified**: the REST endpoint paths (`/v1/agents` and the rest) are inferred from the official Python SDK's method names (`client.beta.agents.create` and so on; see `managed_agents/CMA_plan_big_execute_small.ipynb` in `anthropics/claude-cookbooks`) rather than read from an official REST reference. A mock HTTP server verified the whole call order — creating workers and the coordinator, creating the session, sending events, polling threads, aggregating, and the error path when `environment_id` is unset — but **it has never been connected to the real API**. Context isolation between workers and the coordinator is a property of Anthropic's server and cannot be verified from client code; what was verified here is only that rig's own code never requests or forwards raw worker output and reads only the final result the API returned.

## Automatic activation (it applies without being asked for)

Even without an explicit `--orchestrate`, a run goes through orchestrate when (§4.3):
- **the recipe declares `checks:` or `needs:`** — a recipe that means to be run deterministically, through machine verification or DAG parallelism; or
- **the manifest sets `default_orchestrate: true`** — a project-wide default.

`--no-orchestrate` returns that one run to the prose engine. It does not affect single-shot generators such as `/rig:persona`. The `plan` output says `auto orchestrate: auto ON/off`.

## Where it earns its place

- **A control loop in prose is weaker than one enforced in code** (`harness-taxonomy`). Transitions, stopping, and retries are held by code.
- **State persists in `run-state.json`**, so the same state machine resumes across compaction and restarts — the computational version of run-continuity.
- **Opt-in, engine unchanged.** The inside of each step is still run by ordinary rig: thin harness, fat skills.

## Things to watch

- `--provider rig` and `claude` **start claude nested** — mind the cost and the recursion. Check a design with `--provider mock`, a separate process that returns a deterministic dummy immediately.
- K failures at a gate means `ESCALATE` (no infinite loops), and self-grading (`by=self`) means `BLOCKED`.
- The determinism can be checked with `orchestrate selftest`.

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output — the visibility is the evidence that the harness is driving:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
