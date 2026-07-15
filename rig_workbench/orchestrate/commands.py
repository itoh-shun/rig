"""orchestrate commands: remaining cmd_* entry points (split from scripts/orchestrate.py)."""

import sys
import os
import json
import time
import shlex
import pathlib
import subprocess

from . import config
from .recipes import (auto_orchestrate, git_diff_lines, load_manifest, load_steps,
                      parse_frontmatter, resolve_effective, resolve_extends,
                      resolve_plan_json, resolve_recipe)
from .runstate import compute_next, load_state, new_state, save_state
from .providers import parse_step_model_spec, run_loop, unknown_step_model_ids
from .isolate import setup_isolation, teardown_isolation

# ── Commands ──────────────────────────────────────────────────────────────────
def render_plan(recipe: str, steps: list[dict]) -> str:
    auto, why = auto_orchestrate(steps)
    lines = [f"## rig computational plan: {recipe}", "",
             f"Steps: {len(steps)} / transitions enforced by code (deterministic)",
             f"Auto orchestrate: {'auto ON' if auto else 'off'} ({why})", ""]
    for i, s in enumerate(steps):
        gate = s["gate"] or "none"
        sensor = (str(len(s["checks"])) + " machine sensor(s)"
                  if s["checks"] else
                  ("independent verdict required" if s["gate"] in ("acceptance-gate", "review-gate") else "—"))
        lines.append(f"  [{i}] {s['id']}  gate={gate}  K={s['max_retries']}  verify={sensor}")
    lines.append("")
    lines.append("Stop condition: each step escalates after K gate failures (no infinite loops).")
    return "\n".join(lines)


def cmd_plan(args):
    path = resolve_recipe(args[0])
    with_flags: list[str] | None = None
    diff_lines: int | None = None
    use_git_diff = False
    i = 1
    while i < len(args):
        if args[i] == "--with" and i + 1 < len(args):
            with_flags = shlex.split(args[i + 1])
            i += 2
        elif args[i] == "--diff-lines" and i + 1 < len(args):
            diff_lines = int(args[i + 1])
            i += 2
        elif args[i] == "--diff-git":
            use_git_diff = True
            i += 1
        else:
            i += 1
    if use_git_diff and diff_lines is None:
        diff_lines = git_diff_lines()  # None if unavailable → size defaults to S (#185)
    if with_flags is not None or diff_lines is not None or use_git_diff:
        plan = resolve_effective(path, with_flags, diff_lines, manifest=load_manifest())
    else:
        plan = resolve_plan_json(path)
    if "--json" in args:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        if plan.get("errors"):
            sys.exit(1)  # same exit contract as the non-JSON path
        return
    print(render_plan(plan["recipe"], plan["steps"]))
    for w in plan.get("warnings", []):
        print(f"[WARN] {w}")
    for e in plan.get("errors", []):
        print(f"[ERROR] {e}")
    if plan.get("errors"):
        sys.exit(1)


def _state_path(args, default="run-state.json") -> pathlib.Path:
    return pathlib.Path(args[0]) if args else pathlib.Path(default)


def cmd_init(args):
    path = resolve_recipe(args[0])
    fm, _warns = resolve_extends(parse_frontmatter(path), path)
    steps = load_steps(fm)
    goal = None
    out = pathlib.Path("run-state.json")
    i = 1
    while i < len(args):
        if args[i] == "--goal" and i + 1 < len(args):
            goal = args[i + 1]
            i += 2
        elif args[i] == "--out" and i + 1 < len(args):
            out = pathlib.Path(args[i + 1])
            i += 2
        else:
            i += 1
    state = new_state(fm.get("name", path.stem), steps, goal)
    save_state(state, out)
    print(render_plan(state["recipe"], steps))
    print(f"\nrun-state: {out}")
    action, msg = compute_next(state)
    save_state(state, out)
    print(f"\n▶ {action}: {msg}")


def _current_running(state: dict):
    if state["cursor"] >= len(state["steps"]):
        return None, None
    step = state["steps"][state["cursor"]]
    st = state["step_state"][step["id"]]
    if st["status"] != "running":
        return None, None
    return step, st


def _run_checks(checks: list[str]) -> list[dict]:
    """Run each declared shell check in INVOCATION_CWD; return [{cmd, ok, rc}] records.

    The single source of truth for the machine-sensor subprocess loop: both `check`
    and `resume` call this so they stay byte-for-byte identical (same shell, cwd, and
    stdout/stderr suppression). Pure I/O — no printing, no state mutation.
    """
    results = []
    for cmd in checks:
        r = subprocess.run(cmd, shell=True, cwd=str(config.INVOCATION_CWD),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        results.append({"cmd": cmd, "ok": (r.returncode == 0), "rc": r.returncode})
    return results


def cmd_check(args):
    sp = _state_path(args)
    state = load_state(sp)
    step, st = _current_running(state)
    if not step:
        print("[ERROR] no running step. START one with `next` first.")
        sys.exit(1)
    if not step["checks"]:
        print(f"step `{step['id']}` declares no checks: (no machine verification). Use verdict instead.")
        return
    print(f"## check: machine sensors for step `{step['id']}` ({len(step['checks'])} checks)")
    results = _run_checks(step["checks"])
    st["checks"] = [{"cmd": r["cmd"], "ok": r["ok"]} for r in results]
    all_ok = all(r["ok"] for r in results)
    for r in results:
        print(f"  [{'OK ' if r['ok'] else 'NG '}] {r['cmd']}  (exit {r['rc']})")
    save_state(state, sp)
    print(f"→ {'all OK' if all_ok else 'some NG'}. Compute the transition with `next`.")


def _fmt_duration(seconds: float) -> str:
    """Compact human duration (e.g. 2h05m, 3d04h) for the resume mtime-gap cue."""
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days:
        return f"{days}d{hours:02d}h"
    if hours:
        return f"{hours}h{mins:02d}m"
    return f"{mins}m"


def cmd_resume(args):
    """Verify-first resume ritual (session-startup ritual for long-running agents).

    Re-verifies the world before continuing a persisted run: prints a compact digest,
    RE-RUNS the current running step's declared machine checks, and only then computes
    the next action. If a previously-passing check now fails, the recorded state is stale
    ("world drifted") and we REFUSE to advance (exit non-zero). Side effects match
    `check` + `next` (state is written the same way); idempotent.
    """
    sp = _state_path(args)
    state = load_state(sp)
    steps = state["steps"]
    total = len(steps)
    n_passed = sum(1 for st in state["step_state"].values() if st.get("status") == "passed")

    # ── Digest ───────────────────────────────────────────────────────────────
    print(f"## resume: {state['recipe']}  cursor={state['cursor']}/{total}  "
          f"done={n_passed}/{total}  stopped={bool(state['stopped'])}")
    for s in steps:
        st = state["step_state"][s["id"]]
        rejects = [v for v in st["verdicts"] if not v.get("ok")]
        tail = (f"  ⚠ {len(rejects)} REJECT (by {', '.join(str(v.get('by')) for v in rejects)})"
                if rejects else "")
        print(f"  {s['id']:<14} {st['status']:<9} "
              f"checks={sum(1 for c in st['checks'] if c['ok'])}/{len(st['checks'])} "
              f"verdicts={len(st['verdicts'])}{tail}")
    if state["stopped"]:
        print(f"  ⚠ ESCALATED: {state['stopped']['reason']} (at {state['stopped'].get('at')})")

    # ── mtime gap (informational only) ───────────────────────────────────────
    try:
        gap = time.time() - sp.stat().st_mtime
    except OSError:
        gap = 0.0
    if gap >= 3600:
        print(f"↺ resumed after ~{_fmt_duration(gap)} (run-state may predate a context "
              f"compaction; re-verifying before continuing)")

    # ── Verify-first: re-run the current running step's machine checks ────────
    step, st = _current_running(state)
    if step and step["checks"]:
        print(f"## re-verify: re-running {len(step['checks'])} machine check(s) for "
              f"current step `{step['id']}`")
        prior = {c["cmd"]: c["ok"] for c in st["checks"]}
        results = _run_checks(step["checks"])
        drifted = []
        for r in results:
            note = ""
            if prior.get(r["cmd"]) is True and not r["ok"]:
                note = "  ← DRIFT (was passing, now fails)"
                drifted.append(r["cmd"])
            print(f"  [{'OK ' if r['ok'] else 'NG '}] {r['cmd']}  (exit {r['rc']}){note}")
        # Persist the fresh sensor readings (same side effect as `check`).
        st["checks"] = [{"cmd": r["cmd"], "ok": r["ok"]} for r in results]
        save_state(state, sp)
        if drifted:
            print(f"✗ WORLD DRIFTED: {len(drifted)} previously-passing check(s) now fail. "
                  f"The recorded state is stale — REFUSING to advance. Re-run step "
                  f"`{step['id']}` before continuing.")
            sys.exit(1)
        print("✓ world still matches the recorded state.")

    # ── Continue seamlessly (identical to `next`) ────────────────────────────
    action, msg = compute_next(state)
    save_state(state, sp)
    print(f"▶ {action}: {msg}")
    if action == "ESCALATE":
        sys.exit(1)


def cmd_verdict(args):
    sp = _state_path(args)
    state = load_state(sp)
    step, st = _current_running(state)
    if not step:
        print("[ERROR] no running step.")
        sys.exit(1)
    by, ok, note = None, None, ""
    i = 1
    while i < len(args):
        if args[i] == "--by" and i + 1 < len(args):
            by = args[i + 1]
            i += 2
        elif args[i] == "--pass":
            ok = True
            i += 1
        elif args[i] == "--fail":
            ok = False
            i += 1
        elif args[i] == "--note" and i + 1 < len(args):
            note = args[i + 1]
            i += 2
        else:
            i += 1
    if by is None or ok is None:
        print("[ERROR] --by <verifier-name> and --pass|--fail are required.")
        sys.exit(1)
    st["verdicts"].append({"by": by, "ok": ok, "note": note})
    save_state(state, sp)
    guard = " (independent)" if by.lower() not in ("self", "generator", "producer") else " (⚠ generator itself = invalid)"
    print(f"verdict recorded: step `{step['id']}` by={by}{guard} → {'PASS' if ok else 'FAIL'}. Proceed with `next`.")


def cmd_next(args):
    sp = _state_path(args)
    state = load_state(sp)
    action, msg = compute_next(state)
    save_state(state, sp)
    print(f"▶ {action}: {msg}")
    if action == "ESCALATE":
        sys.exit(1)


def cmd_status(args):
    sp = _state_path(args)
    state = load_state(sp)
    print(f"## run: {state['recipe']}  cursor={state['cursor']}/{len(state['steps'])}  "
          f"done={state['done']}  stopped={bool(state['stopped'])}")
    for s in state["steps"]:
        st = state["step_state"][s["id"]]
        print(f"  {s['id']:<14} {st['status']:<9} retries={st['retries']} "
              f"checks={sum(1 for c in st['checks'] if c['ok'])}/{len(st['checks'])} "
              f"verdicts={len(st['verdicts'])}")

def cmd_run(args):
    if not args:
        print("[ERROR] usage: run <recipe> --provider <name> [--verifier-provider <name>] "
              "[--provider-cmd \"...{prompt}...\"] [--step-model <step-id>=<model>] "
              "[--max-steps N] [--goal G] [--out f] [--isolate] [--auto-route] "
              "[--auto-route-learn [--auto-route-mode shadow|active] [--exploration-pct N] [--exploration-date D]]")
        sys.exit(1)
    path = resolve_recipe(args[0])
    fm, _warns = resolve_extends(parse_frontmatter(path), path)
    steps = load_steps(fm)
    gen = ver = None
    generators: list[str] = []
    goal = None
    out = pathlib.Path("run-state.json")
    max_steps = 40
    max_parallel = 4
    quorum = "all"
    cfg: dict = {"_token_usage": {}}  # per-run token accumulator (#271/#296); never merged across runs
    step_models: dict[str, str] = {}
    i = 1
    while i < len(args):
        a = args[i]
        if a == "--provider" and i + 1 < len(args):
            gen = args[i + 1]
            i += 2
        elif a == "--generators" and i + 1 < len(args):
            generators = [g.strip() for g in args[i + 1].split(",") if g.strip()]
            i += 2
        elif a == "--verifier-provider" and i + 1 < len(args):
            ver = args[i + 1]
            i += 2
        elif a == "--verifier-providers" and i + 1 < len(args):
            ver = [v.strip() for v in args[i + 1].split(",") if v.strip()]
            i += 2
        elif a == "--provider-cmd" and i + 1 < len(args):
            cfg["provider_cmd"] = args[i + 1]
            i += 2
        elif a == "--model" and i + 1 < len(args):
            cfg["model"] = args[i + 1]
            i += 2
        elif a == "--step-model" and i + 1 < len(args):
            # Runtime per-step model override (repeatable; #293).
            # Precedence: --step-model > recipe frontmatter `model:` > global --model.
            parsed = parse_step_model_spec(args[i + 1])
            if parsed is None:
                print(f"[ERROR] --step-model expects <step-id>=<model> (e.g. plan=sonnet), got: {args[i + 1]}")
                sys.exit(1)
            step_models[parsed[0]] = parsed[1]
            i += 2
        elif a == "--base-url" and i + 1 < len(args):
            cfg["base_url"] = args[i + 1]
            i += 2
        elif a in ("--auto-model", "--auto-model-setting"):
            cfg["auto_model"] = True
            i += 1
        elif a == "--goal" and i + 1 < len(args):
            goal = args[i + 1]
            i += 2
        elif a == "--out" and i + 1 < len(args):
            out = pathlib.Path(args[i + 1])
            i += 2
        elif a == "--max-steps" and i + 1 < len(args):
            max_steps = int(args[i + 1])
            i += 2
        elif a == "--max-parallel" and i + 1 < len(args):
            max_parallel = int(args[i + 1])
            i += 2
        elif a == "--quorum" and i + 1 < len(args):
            quorum = args[i + 1]
            i += 2
        elif a == "--isolate":
            cfg["isolate"] = True
            i += 1
        elif a == "--allow-headless-in-cc":
            cfg["allow_headless_in_cc"] = True
            i += 1
        elif a == "--auto-route":
            cfg["auto_route"] = True
            i += 1
        elif a == "--auto-route-learn":     # #305: learned route from historical data (default shadow mode)
            cfg["auto_route_learn"] = True
            i += 1
        elif a == "--auto-route-mode" and i + 1 < len(args):
            cfg["auto_route_mode"] = args[i + 1]  # shadow (default: record prediction only) | active (actually used)
            i += 2
        elif a == "--exploration-pct" and i + 1 < len(args):
            cfg["exploration_pct"] = int(args[i + 1])
            i += 2
        elif a == "--exploration-date" and i + 1 < len(args):
            cfg["exploration_date"] = args[i + 1]  # explicit date/bucket string, not randomness, for determinism
            i += 2
        else:
            i += 1
    # Unknown step ids abort the run before anything executes (no silent ignores; #293)
    unknown = unknown_step_model_ids(step_models, steps)
    if unknown:
        print(f"[ERROR] --step-model: unknown step id(s): {', '.join(unknown)} "
              f"(recipe `{fm.get('name', path.stem)}` steps: {', '.join(s['id'] for s in steps)})")
        sys.exit(1)
    if step_models:
        cfg["step_models"] = step_models
    if not gen and generators:
        gen = generators[0]            # --generators alone is fine (first one as representative)
    if not gen:
        print("[ERROR] --provider <name> (or --generators a,b,c) is required"
              " (rig|claude|codex|ollama|lmstudio|cmd|mock). rig = launch each step as a rig harness (recommended)."
              " ollama/lmstudio = local LLM (server required; pick a model with --model). Use mock for tests.")
        sys.exit(1)

    # ── Guard against accidental launches from inside Claude Code ────────────
    # Using `--provider claude` / `--provider rig` inside a Claude Code session
    # spawns `claude -p` as a subprocess. That counts separately from the already
    # running session and may land subscription usage in a different bucket, or
    # bill an API key if one is configured (environment-dependent).
    # Stop unless `--allow-headless-in-cc` is given explicitly.
    _cc_env = os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    _headless_claude = gen in ("claude", "rig") or ver in ("claude", "rig") or \
        any(p in ("claude", "rig") for p in generators) or \
        (isinstance(ver, list) and any(p in ("claude", "rig") for p in ver))
    if _cc_env and _headless_claude and not cfg.get("allow_headless_in_cc"):
        print(
            "[BLOCKED] Inside a Claude Code session, `--provider claude` / `--provider rig` "
            "spawns `claude -p` as a separate subprocess.\n"
            "\n"
            "You are already using Claude in this session, so this risks double-firing and "
            "billing to a different bucket. Switch to one of:\n"
            "\n"
            "  1. Use `/rig:rig \"<task>\"` (manual backend = via the Agent tool, same session)\n"
            "  2. `--provider ollama` / `--provider lmstudio` (local, no billing)\n"
            "  3. `--provider mock` (for tests)\n"
            "  4. If you really must run headless, pass `--allow-headless-in-cc` explicitly\n"
        )
        sys.exit(1)
    ver = ver or gen  # default to the same provider (but a separate process and role)
    state = new_state(fm.get("name", path.stem), steps, goal)
    for sid, model in step_models.items():   # record runtime overrides in run-state (traceable later)
        state["history"].append({"action": "STEP_MODEL_OVERRIDE", "step": sid, "model": model})
    iso = None
    if cfg.get("isolate"):
        iso = setup_isolation(fm.get("name", path.stem))
        cfg["cwd"] = iso["dir"]
        state["isolation"] = iso
        print(f"◈ Isolated run: worktree={iso['dir']} / branch={iso['branch']}")
    print(render_plan(state["recipe"], steps))
    panel = f" / judge-panel={','.join(generators)}" if len(generators) > 1 else ""
    if isinstance(ver, list):
        panel += f" / model-quorum={','.join(ver)}"
    dag = " / DAG-parallel" if any(s["needs"] for s in steps) else ""
    overrides = ("\nStep-model overrides: "
                 + ", ".join(f"{k}={v}" for k, v in step_models.items())) if step_models else ""
    print(f"\nAutonomous run: provider={gen} / verifier={'+'.join(ver) if isinstance(ver, list) else ver} / "
          f"max-steps={max_steps} / parallel={max_parallel} / quorum={quorum}{panel}{dag}{overrides}\n")
    final = run_loop(state, out, gen, ver, cfg, max_steps,
                     max_parallel=max_parallel, quorum=quorum,
                     generators=(generators or None))
    if iso:
        outcome = teardown_isolation(iso, final)
        state["isolation"]["outcome"] = outcome
        save_state(state, out)
        label = {"merged": f"gate green → ff-merged {iso['branch']} and removed the worktree",
                 "clean-removed": "no changes → removed the worktree",
                 "kept": f"worktree and branch preserved (please inspect): {iso['dir']}"}[outcome]
        print(f"◈ Isolated run outcome: {label}")
    print(f"\n=== Finished: {final} ===  run-state: {out}")
    sys.exit(1 if final in ("ESCALATE", "BLOCKED") else 0)

def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows

def cmd_party(_args):
    """Party roster screen (/rig:party): render RPG-style stats from telemetry, measured drills, and the brick inventory.

    Looks like a game screen, but every line is real data (runs.jsonl / drill-results.jsonl /
    shipped bricks) = a health-check dashboard for the harness. Read-only."""
    runs = _read_jsonl(config.RUNS_PATH)
    drills = _read_jsonl(config.DRILL_PATH)
    done = sum(1 for r in runs if r.get("final") == "DONE")
    esc = sum(1 for r in runs if r.get("escalated_at"))
    total = len(runs)

    # Tally verifier votes (sortie counts, REJECT counts; by is "provider:persona")
    votes: dict[str, dict] = {}
    for r in runs:
        for st in r.get("steps", []):
            for v in st.get("verdicts", []):
                persona = (v.get("by") or "?").split(":", 1)[-1]
                a = votes.setdefault(persona, {"sorties": 0, "rejects": 0})
                a["sorties"] += 1
                a["rejects"] += 0 if v.get("ok") else 1

    # drill detection rate (drill.md schema: {"ts":…, "scores":[{"reviewer","detected","seeded","false_positives"}]})
    atk: dict[str, dict] = {}
    for d in drills:
        for s in d.get("scores", []):
            a = atk.setdefault(s.get("reviewer", "?"), {"detected": 0, "seeded": 0, "fp": 0})
            a["detected"] += s.get("detected", 0)
            a["seeded"] += s.get("seeded", 0)
            a["fp"] += s.get("false_positives", 0)

    # Longest consecutive no-escalation streak (for achievements)
    streak = best = 0
    for r in runs:
        streak = 0 if r.get("escalated_at") else streak + 1
        best = max(best, streak)

    def _line(name: str, bench: bool = False) -> str:
        v = votes.get(name, {"sorties": 0, "rejects": 0})
        a = atk.get(name)
        power = (f"⚔ detection {a['detected'] / a['seeded'] * 100:3.0f}% (drill {a['detected']}/{a['seeded']}"
                 + (f", false-pos {a['fp']}" if a["fp"] else "") + ")") if a and a["seeded"] else "⚔ detection unmeasured (calibrate with /rig:drill)"
        tag = " (reserve)" if bench and v["sorties"] == 0 else ""
        return f"│ {name:22s} {power}  sorties {v['sorties']:3d} / REJECT {v['rejects']}{tag}"

    party = ["security-reviewer", "design-reviewer", "test-reviewer"]
    bench = ["performance-reviewer", "observability-reviewer", "api-compat-reviewer",
             "migration-reviewer", "docs-reviewer"]
    for extra in (load_manifest().get("default_personas") or []):
        if extra not in party:
            party.append(extra)

    print("━━━ rig party roster (/rig:party) ━━━━━━━━━━━━━━━")
    rate = f"{esc / total * 100:.0f}%" if total else "—"
    print(f"Lv.{done}  runs {total} / DONE {done} / escalation rate {rate}")
    print("┌─ party (review fan-out)" + "─" * 30)
    for name in party:
        print(_line(name))
    if "finding-verifier" in votes:
        fv = votes["finding-verifier"]
        print(f"│ {'finding-verifier':22s} 🛡 rebuttals {fv['sorties']} (audit rejection quality with runs --personas)")
    print("├─ reserves (deploy with --persona)" + "─" * 31)
    for name in bench:
        print(_line(name, bench=True))
    print("└" + "─" * 56)

    badges = []
    if done >= 1:
        badges.append("🏆 First DONE")
    if best >= 10:
        badges.append("🏆 Ten flawless battles (10 consecutive no-escalation)")
    if total >= 100:
        badges.append("🏆 Battle-hardened (100 runs)")
    if any(a["seeded"] and a["detected"] == a["seeded"] and a["seeded"] >= 2 for a in atk.values()):
        badges.append("🏆 Perfect marksman (all drill seeds detected)")
    wiki_n = len(list((config.INVOCATION_CWD / ".claude" / "rig" / "knowledge" / "wiki").glob("*.md"))) \
        if (config.INVOCATION_CWD / ".claude" / "rig" / "knowledge" / "wiki").is_dir() else 0
    if wiki_n >= 10:
        badges.append(f"🏆 Grand library (project wiki {wiki_n} pages)")
    print("Achievements: " + (" / ".join(badges) if badges else "(none yet — get one run to DONE first)"))
    if not runs:
        print("\n(no telemetry: RUNs accumulate in .rig/runs.jsonl and grow this screen)")
    if not drills:
        print("(detection unmeasured: /rig:drill calibrates reviewer attack power)")

def cmd_runs(args):
    """Run telemetry listing: runs [--limit N] [--recipe R] [--personas] [--html <path>] [--since YYYY-MM-DD].

    Reads .rig/runs.jsonl (appended by telemetry_append; the manual backend appends the same
    format per SKILL.md §6) and prints the latest N runs plus per-recipe aggregates (count,
    DONE rate, average retries, escalation count).
    --personas tallies votes per verifier (the verdict's by), providing input for pruning decisions.
    --html <path> delegates to scripts/dashboard.py to write an HTML dashboard (KPIs, sparkline,
    per-recipe bars, verifier votes, recent-run table in a single-file HTML with no external deps).
    Read-only (the same inspection mode as --list / --validate).
    """
    limit, recipe, personas_mode, html_out, since, cost_mode = 10, None, False, None, None, False
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        elif args[i] == "--recipe" and i + 1 < len(args):
            recipe = args[i + 1]
            i += 2
        elif args[i] == "--personas":
            personas_mode = True
            i += 1
        elif args[i] == "--cost":
            cost_mode = True
            i += 1
        elif args[i] == "--html" and i + 1 < len(args):
            html_out = args[i + 1]
            i += 2
        elif args[i] == "--since" and i + 1 < len(args):
            since = args[i + 1]
            i += 2
        else:
            i += 1
    if html_out:
        dash = pathlib.Path(__file__).resolve().parent.parent.parent / "scripts" / "dashboard.py"
        if not dash.exists():
            print(f"[ERROR] dashboard.py not found: {dash}")
            sys.exit(1)
        cmd = [sys.executable, str(dash), "--repo", str(config.INVOCATION_CWD),
               "--out", html_out, "--limit", str(limit)]
        if recipe:
            cmd += ["--recipe", recipe]
        if since:
            cmd += ["--since", since]
        rc = subprocess.run(cmd).returncode
        sys.exit(rc)
    if not config.RUNS_PATH.exists():
        print(f"No run records yet ({config.RUNS_PATH}). They are appended by orchestrate run / "
              "queue go, or by completing a manual-backend flow (SKILL.md §6).")
        return
    rows = []
    for line in config.RUNS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # skip broken lines (resilience for an append-only log)
    if recipe:
        rows = [r for r in rows if r.get("recipe") == recipe]
    if not rows:
        print("No matching run records.")
        return

    if personas_mode:
        # Per-verifier tally: aggregate each run's steps[].verdicts[] by their by field
        stats: dict[str, dict] = {}
        for r in rows:
            for st in r.get("steps", []):
                for v in st.get("verdicts", []):
                    by = v.get("by") or "?"
                    a = stats.setdefault(by, {"votes": 0, "ok": 0, "reject": 0})
                    a["votes"] += 1
                    a["ok" if v.get("ok") else "reject"] += 1
        if not stats:
            print("No verdict records yet (they accumulate from runs that pass review-gate / acceptance-gate).")
            return
        print(f"## rig runs --personas (verifier votes across {len(rows)} runs)\n")
        print(f"  {'verifier':28s} {'votes':>6s} {'PASS':>6s} {'REJECT':>7s} {'REJECT%':>8s}")
        for by in sorted(stats, key=lambda k: -stats[k]["votes"]):
            a = stats[by]
            print(f"  {by:28s} {a['votes']:6d} {a['ok']:6d} {a['reject']:7d} "
                  f"{a['reject'] / a['votes'] * 100:7.0f}%")
        rubber = [by for by, a in stats.items() if a["votes"] >= 5 and a["reject"] == 0]
        if rubber:
            print("\n  Pruning hint: " + ", ".join(sorted(rubber))
                  + " cast 5+ votes without a single REJECT (possible rubber-stamping, or the lens"
                    " has no bite; consider dropping them or sharpening the lens)")
        return

    if cost_mode:
        # Per-recipe, per-provider token rollup (#271/#296). CLI providers (claude/codex) don't
        # expose structured usage and stay "unmeasured" — only HTTP providers (ollama/lmstudio/
        # anthropic) are actually metered here.
        by_recipe: dict[str, dict[str, dict]] = {}
        any_usage = False
        fallback_count = refusal_count = 0
        for r in rows:
            tu = r.get("token_usage") or {}
            if tu:
                any_usage = True
                rc = by_recipe.setdefault(r.get("recipe", "?"), {})
                for provider, u in tu.items():
                    a = rc.setdefault(provider, {"prompt_tokens": 0, "completion_tokens": 0,
                                                 "cache_read_input_tokens": 0, "calls": 0})
                    a["prompt_tokens"] += u.get("prompt_tokens", 0)
                    a["completion_tokens"] += u.get("completion_tokens", 0)
                    a["cache_read_input_tokens"] += u.get("cache_read_input_tokens", 0)
                    a["calls"] += u.get("calls", 0)
            for s in r.get("steps", []):                       # #297: Fable fallback/refusal occurrence count
                for ev in s.get("fable_events", []):
                    if ev.get("kind") == "fallback":
                        fallback_count += 1
                    elif ev.get("kind") == "refusal":
                        refusal_count += 1
        print(f"## rig runs --cost ({len(rows)} runs)\n")
        if not any_usage:
            print("No token usage recorded (unmeasured). HTTP providers (ollama/lmstudio/anthropic) are metered "
                  "automatically from the usage field. claude/codex run via CLI and "
                  "don't expose structured usage, so they're out of scope here — see Anthropic's Usage & "
                  "Cost Admin API for those instead of estimating.")
        else:
            for rcp, providers in sorted(by_recipe.items()):
                print(f"  {rcp}:")
                for provider, a in sorted(providers.items()):
                    total = a["prompt_tokens"] + a["completion_tokens"]
                    cache = f"  cache_read={a['cache_read_input_tokens']}" if a["cache_read_input_tokens"] else ""
                    print(f"    {provider:16s} calls={a['calls']:4d}  prompt={a['prompt_tokens']:8d}  "
                          f"completion={a['completion_tokens']:8d}  total={total:8d}{cache}")
        if fallback_count or refusal_count:
            print(f"\nFable 5 refusal-classifier (#297): fallback={fallback_count}  direct-refusal={refusal_count}  "
                  "(a fallback is treated as a transparent success and doesn't block the gate; cache_read is the "
                  "fallback-prefix token count billed at 10%)")
        return

    print(f"## rig runs (latest {min(limit, len(rows))} of {len(rows)})\n")
    for r in rows[-limit:]:
        esc = f" / escalated@{r['escalated_at']}" if r.get("escalated_at") else ""
        print(f"  {r.get('ts', '?'):25s} {r.get('recipe', '?'):20s} {r.get('final', '?'):9s} "
              f"steps {r.get('steps_passed', '?')}/{r.get('steps_total', '?')} "
              f"retries {r.get('retries', 0)}{esc}")

    agg: dict[str, dict] = {}
    for r in rows:
        a = agg.setdefault(r.get("recipe", "?"), {"n": 0, "done": 0, "retries": 0, "esc": 0})
        a["n"] += 1
        a["done"] += 1 if r.get("final") == "DONE" else 0
        a["retries"] += r.get("retries", 0)
        a["esc"] += 1 if r.get("escalated_at") else 0
    print("\n## Per-recipe aggregates\n")
    print(f"  {'recipe':20s} {'runs':>5s} {'DONE%':>7s} {'avg-retry':>9s} {'esc':>4s}")
    for name in sorted(agg):
        a = agg[name]
        print(f"  {name:20s} {a['n']:5d} {a['done'] / a['n'] * 100:6.0f}% "
              f"{a['retries'] / a['n']:9.1f} {a['esc']:4d}")

    # Gap prescriptions: if the same (recipe, step) escalated twice or more, suggest acquiring capability
    # (telemetry → /rig:import --discover / /rig:harness = the entry to the self-completion loop)
    gaps: dict[tuple, int] = {}
    for r in rows:
        esc_at = r.get("escalated_at")
        if esc_at:
            gaps[(r.get("recipe", "?"), esc_at)] = gaps.get((r.get("recipe", "?"), esc_at), 0) + 1
    hot = {k: v for k, v in gaps.items() if v >= 2}
    if hot:
        print("\n## Gap prescriptions (repeated escalations at the same step)\n")
        for (rcp, sid), n in sorted(hot.items(), key=lambda kv: -kv[1]):
            print(f"  {rcp} / {sid}: escalated {n} times — consider acquiring capability:"
                  f" /rig:import --discover \"skill to strengthen {sid}\""
                  f" / take inventory with /rig:harness")

def cmd_install_shim(args):
    """Place the shim as a symlink at ~/.local/bin/rig (or the path given via --to).
    Run once; afterwards `rig <subcommand>` works from any directory."""
    target = pathlib.Path("~/.local/bin/rig").expanduser()
    force = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--to" and i + 1 < len(args):
            target = pathlib.Path(args[i + 1]).expanduser()
            i += 2
        elif a in ("--force", "-f"):
            force = True
            i += 1
        else:
            i += 1
    src = config.RIG_HOME / ".claude-plugin" / "bin" / "rig"
    if not src.exists():
        print(f"[ERROR] shim source not found: {src}")
        sys.exit(1)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if not force:
            print(f"[ERROR] already exists: {target} (overwrite with --force)")
            sys.exit(1)
        target.unlink()
    target.symlink_to(src)
    print(f"✓ symlink: {target} → {src}")
    path_dirs = (os.environ.get("PATH") or "").split(os.pathsep)
    if str(target.parent) not in path_dirs:
        print(f"⚠ {target.parent} does not seem to be on $PATH. Add this:")
        print(f"    export PATH=\"{target.parent}:$PATH\"")
    print(f"Verify: `rig models` or `rig --help` (RIG_HOME={config.RIG_HOME})")

