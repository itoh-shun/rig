"""Where a task actually is in its recipe, and what the next move is.

`steps.json` used to start empty and grow as the model reported step names, so the
workbench path had no idea how many steps the recipe even had. "3/7" was not
displayable because the 7 did not exist anywhere — `board` could only echo the last
name it was told, which reads as a position and is not one.

Seeding the resolved recipe at registration gives every later view a denominator. The
step list is display metadata: it is what the recipe declared, recorded once so
progress can be shown, and it is never what decides whether the task may be accepted.
That stays with the acceptance gate.
"""

from __future__ import annotations

import dataclasses
import pathlib

# The step statuses `workbench step --set` accepts, in the order they read as progress.
DONE = ("passed", "skipped")

BAR_DONE, BAR_CURRENT, BAR_TODO, BAR_SKIP, BAR_RETRY = "✓", "▸", "·", "−", "↻"


def load_recipe_steps(recipe: str) -> list[dict]:
    """The declared steps of a shipped recipe, or [] when it cannot be read.

    Deliberately silent and shipped-only. This runs inside `new`, where the user asked
    for a task and not for a recipe audit: a missing or malformed recipe must degrade
    to "no progress display", never to a failed registration or a consent prompt for a
    project-local recipe. `--plan` and `--validate` are where a broken recipe is
    supposed to be loud.
    """
    if not recipe:
        return []
    try:
        from ..orchestrate.config import RECIPES
        from ..orchestrate.recipes import load_steps, parse_frontmatter, resolve_extends

        path = pathlib.Path(RECIPES) / f"{recipe}.md"
        if not path.is_file():
            return []
        frontmatter = parse_frontmatter(path)
        try:
            frontmatter, _warnings = resolve_extends(frontmatter, path)
        except Exception:
            pass                       # a broken parent still leaves the child's own steps
        steps = load_steps(frontmatter)
    except Exception:
        return []
    seeded = []
    for step in steps:
        if not step.get("id"):
            continue
        entry = {"name": step["id"], "status": "pending", "updated_at": None}
        for key in ("instruction", "gate", "condition", "actor"):
            if step.get(key):
                entry[key] = step[key]
        personas = step.get("personas") or []
        if personas:
            entry["personas"] = list(personas)
        if step.get("human_gate"):
            entry["human_gate"] = True
        seeded.append(entry)
    return seeded


@dataclasses.dataclass
class Progress:
    """A task's position in its recipe."""
    total: int
    done: int
    current: dict | None
    nxt: dict | None
    bar: str
    conditional: int = 0

    @property
    def known(self) -> bool:
        """False for runs registered before the recipe was seeded, and for recipes that
        could not be read. Callers fall back to the old last-name-reported display
        rather than inventing a denominator."""
        return self.total > 0

    def label(self, width: int = 0) -> str:
        """`3/7 implement`. `width` truncates the step name so the caller's column
        stays aligned across recipes with very different id lengths."""
        if not self.known:
            return "-"
        where = self.current["name"] if self.current else "done"
        if width and len(where) > width:
            where = where[: width - 1] + "…"
        return f"{self.done}/{self.total} {where}"


def from_state(data: dict) -> Progress:
    """Progress from a `steps.json` document.

    The document records *whether it was seeded*, and that flag — not the length of the
    list — is what makes a denominator legitimate. An unseeded run grows its step list
    from whatever the model reports, so after one ad-hoc report the list holds exactly
    one entry and computing from length alone would announce "1/1, all steps complete"
    about a run whose step count nobody knows. That is the fabricated denominator this
    whole module exists to avoid, so the fact is read rather than inferred.
    """
    return compute(data.get("steps") or [], seeded=bool(data.get("seeded")))


def compute(steps: list[dict], *, seeded: bool = True) -> Progress:
    """Position from the recorded step list.

    "Current" is the first step that is neither passed nor skipped — not the last one
    reported. A step reported out of order, or reported twice, must not make the task
    look further along than it is.
    """
    real = [s for s in steps if s.get("name")] if seeded else []
    if not real:
        return Progress(total=0, done=0, current=None, nxt=None, bar="")
    done = sum(1 for s in real if s.get("status") in DONE)
    pending = [s for s in real if s.get("status") not in DONE]
    current = pending[0] if pending else None
    nxt = pending[1] if len(pending) > 1 else None
    marks = []
    for step in real:
        status = step.get("status")
        if status == "skipped":
            marks.append(BAR_SKIP)
        elif status == "passed":
            marks.append(BAR_DONE)
        elif status == "failed":
            marks.append(BAR_RETRY)
        elif step is current:
            marks.append(BAR_CURRENT)
        else:
            marks.append(BAR_TODO)
    return Progress(total=len(real), done=done, current=current, nxt=nxt,
                    bar="".join(marks),
                    conditional=sum(1 for s in real if s.get("condition")))


def next_action(task: dict, progress: Progress, gate: str) -> str:
    """The one line that says whose move it is.

    `board` exists to answer this and nothing else — a list of tasks that does not say
    which of them is waiting on *you* is a list you have to open one by one. Ordered so
    the strongest claim on the reader wins: their own decision first, then somebody
    else's, then "rig is still working".
    """
    status = task.get("status")
    if status == "accepted":
        return "済: commit する"
    if status == "discarded":
        return "済: 破棄"
    if progress.current and progress.current.get("human_gate"):
        owner = progress.current.get("actor")
        who = f"{owner} の署名" if owner else "人の署名"
        return f"⏸ {who}待ち"
    if gate == "failed":
        return "→ あなた: 未達基準を直す or discard"
    if gate in ("passed", "passed_with_warnings"):
        return "→ あなた: diff を見て accept"
    if status == "gate_passed":
        return "→ あなた: diff を見て accept"
    if progress.known and progress.current is None:
        # Every step is through but no criterion has a verdict yet. Still rig's move,
        # and saying "実行中" next to "7/7" reads as a stuck run rather than a stage.
        return "… ゲート評価待ち"
    return "… rig 実行中"
