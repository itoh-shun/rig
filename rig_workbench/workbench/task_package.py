"""The compact task package a fresh agent session receives instead of a transcript (#460).

When rig creates a task worktree through Orca and starts an agent inside it, that agent
starts from nothing. Handing it the root session's whole conversation would carry unrelated
history, stale hypotheses and other tasks into the implementation session; handing it
nothing would make it guess. What travels is a package: goal, the constraints rig imposes,
the acceptance criteria the gate will ask about, the identifiers a later run needs to find
this one, and where to write things down.

Composed from what the task record already holds. Nothing here is inferred from the task
text, and the goal is fenced as untrusted text the same way the orchestrator fences it —
an issue body pasted into `new` is data describing the task, not instructions to the agent.
"""

from __future__ import annotations

from ..orchestrate.quarantine import wrap_untrusted


def compose(task: dict, *, criteria: list[str] | None = None) -> str:
    """The package as one prompt, sections in a fixed order."""
    route = task.get("route") if isinstance(task.get("route"), dict) else {}
    lines = [
        "# rig task package",
        "",
        f"task_id: {task.get('task_id', '?')}",
        f"task_type: {task.get('task_type', '?')}",
        f"recipe: {task.get('recipe') or '(none)'}",
        f"base: {task.get('base_branch', '?')} @ {task.get('base_commit', '?')}",
        f"branch: {task.get('branch') or '(none)'}",
        "",
        "## Goal",
        "",
        wrap_untrusted(str(task.get("input") or ""), "task text"),
        "",
        "## Constraints",
        "",
        "- Work only inside this worktree. rig accepts or discards the result; you do not.",
        "- Do not edit `.rig/`, CI workflows or the acceptance gate; that is tampering and the gate detects it.",
        "- Keep the diff to what the goal needs. Unrelated changes fail `no_unrelated_diff`.",
        "- Never commit secrets. The gate scans the diff and refuses accept on a finding.",
    ]
    if criteria:
        lines += ["", "## Acceptance criteria (what the gate will ask)", ""]
        lines += [f"- {name}" for name in criteria]
    reviewers = route.get("reviewers") if isinstance(route.get("reviewers"), list) else []
    if reviewers:
        lines += ["", "## Independent review", "",
                  "A separate verifier judges the result; do not try to pass it by describing "
                  "the work — the diff is what is judged. Reviewers: " + ", ".join(map(str, reviewers))]
    lines += [
        "",
        "## Where to write things down",
        "",
        f"- Progress: `rig-wb wb step {task.get('task_id', '<task_id>')} ...` per step.",
        f"- For a later run: `rig-wb wb note {task.get('task_id', '<task_id>')} \"<what it should know>\" --about <path>`.",
        "- When done, stop. Accept is a human decision made outside this session.",
    ]
    return "\n".join(lines) + "\n"
