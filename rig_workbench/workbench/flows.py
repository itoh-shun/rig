"""workbench flows: derive a project's flow set from what it has actually run.

The problem this solves
-----------------------
The composition machinery has been complete for a while — the manifest carries
`default_recipe` and `default_personas[]`, project-tier recipes live in
`.claude/rig/recipes/`, and `extends:` composes them N deep. What was missing
was the step that *uses* it: `/rig:init` scaffolded a manifest with the generic
defaults and stopped, so every repo started (and usually stayed) on
`default_recipe: interactive`.

The failure mode to avoid is the opposite one, though. **Growing a flow nobody
runs is worse than growing none**: an unused recipe reads as a considered
decision, so the next person trusts it, extends it, and routes work through a
path that has never been exercised. Two rules follow, and both are enforced
here rather than left to prose:

1. **Cap the proposal at 3.** More than that is a catalogue, not a set of
   defaults, and nobody keeps three-plus flows warm.
2. **Separate evidence from guesswork.** A repo with run history gets
   proposals derived from `.rig/runs.jsonl` and `.rig/runs/*/` — what it really
   ran, how the gate really went. A repo with no history gets proposals marked
   `unevidenced`, because a detected `package.json` is a guess about the future
   and must not be presented as a finding.

Evidence used
-------------
  .rig/runs.jsonl              recipe usage, final status, escalations
  .rig/runs/*/task.json        task_type / recipe of workbench runs
  .rig/runs/*/acceptance.json  gate outcome per task
  .rig/runs/*/review.json      which personas voted, and which ever rejected

Personas are proposed only when they have **rejected at least once**. A
reviewer with a long clean record is the rubber-stamp `stats` already warns
about; promoting one into `default_personas` would wire the flattery in
permanently.

CLI: `rig-wb wb suggest-flows [--limit 3] [--json]`
"""

import argparse
import json
import pathlib
from collections import Counter

from .reporting import (load_reviews, read_all_tasks, verifier_counters)
from .state import gate_status, load_json, repo_root, runs_dir

DEFAULT_LIMIT = 3
# Below this, a recipe's record is an anecdote. Proposing a default off one run
# is how an accident becomes a convention.
MIN_RUNS_FOR_EVIDENCE = 2
MIN_REJECTS_FOR_PERSONA = 1

# Fallback proposals for a repo with no history. Deliberately shipped recipes
# only — a first-run project should meet rig's defaults, not a bespoke recipe
# invented on its behalf before anyone has run anything.
_STACK_MARKERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("package.json", "node", ("bugfix", "feature", "review-only")),
    ("pyproject.toml", "python", ("bugfix", "feature", "review-only")),
    ("setup.py", "python", ("bugfix", "feature", "review-only")),
    ("go.mod", "go", ("bugfix", "feature", "review-only")),
    ("Cargo.toml", "rust", ("bugfix", "feature", "review-only")),
    ("build.gradle", "jvm", ("bugfix", "feature", "review-only")),
    ("build.gradle.kts", "jvm", ("bugfix", "feature", "review-only")),
    ("pom.xml", "jvm", ("bugfix", "feature", "review-only")),
)


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def recipe_evidence(root: pathlib.Path) -> dict[str, dict]:
    """{recipe: {runs, done, escalated, gate_passed, gate_failed}} across both records.

    `.rig/runs.jsonl` (flow completions) and `.rig/runs/*/` (workbench tasks)
    count the same thing from two directions; a recipe that only appears in one
    of them is still real, so both are folded in rather than picking a winner.
    """
    stats: dict[str, dict] = {}

    def bucket(name: str) -> dict:
        return stats.setdefault(name, {"recipe": name, "runs": 0, "done": 0,
                                       "escalated": 0, "gate_passed": 0, "gate_failed": 0})

    for rec in _read_jsonl(root / ".rig" / "runs.jsonl"):
        name = rec.get("recipe")
        if not isinstance(name, str) or not name:
            continue
        b = bucket(name)
        b["runs"] += 1
        if rec.get("final") == "DONE":
            b["done"] += 1
        if rec.get("escalated_at"):
            b["escalated"] += 1

    base = runs_dir(root)
    for task in read_all_tasks(base):
        name = task.get("recipe")
        if not isinstance(name, str) or not name:
            continue
        b = bucket(name)
        b["runs"] += 1
        acc = load_json(base / task["task_id"] / "acceptance.json", {"checks": []})
        if acc.get("checks"):
            status = gate_status(acc)
            if status in ("passed", "passed_with_warnings"):
                b["gate_passed"] += 1
            elif status == "failed":
                b["gate_failed"] += 1
    return stats


def persona_evidence(root: pathlib.Path) -> list[dict]:
    """Personas that have voted, with their reject record, most-used first."""
    base = runs_dir(root)
    tasks = read_all_tasks(base)
    runs, rejects = verifier_counters(load_reviews(base, tasks))
    return [{"persona": persona, "runs": n, "rejects": rejects.get(persona, 0)}
            for persona, n in sorted(runs.items(), key=lambda kv: (-kv[1], kv[0]))]


def detect_stack(root: pathlib.Path) -> tuple[str | None, tuple[str, ...]]:
    for marker, label, recipes in _STACK_MARKERS:
        if (root / marker).is_file():
            return label, recipes
    return None, ()


def suggest(root: pathlib.Path, limit: int = DEFAULT_LIMIT) -> dict:
    """The proposal: at most `limit` flows, each labelled with why it is there."""
    stats = recipe_evidence(root)
    evidenced = sorted((s for s in stats.values() if s["runs"] >= MIN_RUNS_FOR_EVIDENCE),
                       key=lambda s: (-s["runs"], s["recipe"]))
    thin = sorted((s for s in stats.values() if s["runs"] < MIN_RUNS_FOR_EVIDENCE),
                  key=lambda s: (-s["runs"], s["recipe"]))

    flows: list[dict] = []
    for s in evidenced[:limit]:
        gate_seen = s["gate_passed"] + s["gate_failed"]
        why = f"{s['runs']} run(s) recorded"
        if gate_seen:
            why += f", gate {s['gate_passed']}/{gate_seen} passed"
        if s["escalated"]:
            why += f", {s['escalated']} escalation(s)"
        flows.append({"recipe": s["recipe"], "evidence": "recorded-runs", "why": why,
                      **{k: s[k] for k in ("runs", "done", "escalated",
                                           "gate_passed", "gate_failed")}})

    stack = None
    if not flows:
        stack, fallback = detect_stack(root)
        for name in fallback[:limit]:
            flows.append({"recipe": name, "evidence": "unevidenced",
                          "why": f"shipped default for a {stack or 'generic'} project — "
                                 f"nothing has been run here yet", "runs": 0})

    personas = [p for p in persona_evidence(root) if p["rejects"] >= MIN_REJECTS_FOR_PERSONA]
    muted = [p for p in persona_evidence(root)
             if p["rejects"] < MIN_REJECTS_FOR_PERSONA and p["runs"] >= 5]

    return {
        "flows": flows,
        "dropped": [{"recipe": s["recipe"], "runs": s["runs"]}
                    for s in evidenced[limit:]],
        "thin": [{"recipe": s["recipe"], "runs": s["runs"]} for s in thin],
        "personas": personas[:limit],
        "muted_personas": muted,
        "limit": limit,
        "stack": stack,
        "has_history": bool(stats),
    }


def manifest_fragment(proposal: dict) -> str:
    """The lines to paste into `.claude/rig.md` — a fragment, never a rewrite.

    `/rig:init` is idempotent and non-destructive, so this prints what to add
    and lets a human (or the init instruction, after confirmation) place it.
    """
    flows = proposal["flows"]
    if not flows:
        return "# (nothing to propose — no run history and no recognised project stack)"
    lines = ["default_recipe: \"%s\"" % flows[0]["recipe"]]
    if len(flows) > 1:
        lines.append("# also in use: " + ", ".join(f["recipe"] for f in flows[1:]))
    personas = [p["persona"] for p in proposal["personas"]]
    if personas:
        lines.append("default_personas: [%s]" % ", ".join(f'"{p}"' for p in personas))
    else:
        lines.append("default_personas: []   # none has rejected anything yet — "
                     "a reviewer that never objects is not worth wiring in")
    return "\n".join(lines)


def cmd_suggest_flows(args: argparse.Namespace) -> None:
    root = repo_root()
    proposal = suggest(root, limit=args.limit or DEFAULT_LIMIT)

    if args.json:
        print(json.dumps(proposal, ensure_ascii=False, indent=2))
        return

    print("## suggest-flows")
    if not proposal["has_history"]:
        print("No run history in .rig/ — the proposals below are guesses from the project "
              "layout, not findings. Run a few flows, then re-run this.")
    print()
    if not proposal["flows"]:
        print("Nothing to propose. Neither run history nor a recognised project stack "
              "(package.json / pyproject.toml / go.mod / Cargo.toml / build.gradle / pom.xml).")
        return

    print(f"Proposed flows (cap {proposal['limit']} — more than that is a catalogue, "
          f"not a set of defaults):")
    for i, f in enumerate(proposal["flows"], start=1):
        mark = "★ default" if i == 1 else ""
        print(f"  {i}. {f['recipe']}  [{f['evidence']}] {mark}")
        print(f"     {f['why']}")

    if proposal["dropped"]:
        # Never let a cap read as "this is everything".
        print(f"\nNot proposed (beyond the cap of {proposal['limit']}): " +
              ", ".join(f"{d['recipe']} ({d['runs']} runs)" for d in proposal["dropped"]))
    if proposal["thin"]:
        print(f"Too little evidence (<{MIN_RUNS_FOR_EVIDENCE} runs): " +
              ", ".join(f"{d['recipe']} ({d['runs']})" for d in proposal["thin"]))

    print("\nReviewer personas with a real record (have rejected at least once):")
    if proposal["personas"]:
        for p in proposal["personas"]:
            print(f"  - {p['persona']}: {p['rejects']} reject(s) over {p['runs']} run(s)")
    else:
        print("  (none yet)")
    for p in proposal["muted_personas"]:
        print(f"  ! {p['persona']} has 0 rejects over {p['runs']} runs — not proposed "
              "(possible rubber-stamp; see `wb stats`)")

    print("\nManifest fragment for .claude/rig.md:\n")
    for line in manifest_fragment(proposal).splitlines():
        print(f"  {line}")
    print("\nNothing was written. `/rig:init` proposes this, confirms, then edits.")
