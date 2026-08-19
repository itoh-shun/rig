"""Acceptance as a dependency edge (#427).

The queue already survives a restart and already runs items in parallel. What it could
not say is that one item must not start until another's *result* cleared rig's quality
boundary — not that the previous agent finished, but that what it produced was accepted.

That distinction is the whole feature. A queue item reaching `done` means its gate
settled; it does not mean anyone applied it. `_build_queue_task_prompt` says so
explicitly, and accepting is deliberately a person's action. So a dependency edge reads
the workbench task's own record, not the queue's idea of how the item went.

Three consequences follow, and each is load-bearing.

**A dependent cannot become ready inside one `queue go`.** Acceptance requires a human,
and GO does not wait for one. So the batch runs what is ready, records the rest as
`waiting` with the reason, and says so. Anything else would be a scheduler pretending to
be a queue.

**`waiting` and `blocked` are persisted statuses, not a filter.** The detached worker
(`mission_worker`) loops until nothing is `queued`; leaving a dependent at `queued` would
spin it at several cycles a second forever. Writing the state down is also what makes it
survive the restart the acceptance criteria ask about.

**A lookup that fails is never `ready`.** The surrounding module swallows exceptions and
returns a benign default in several places, and it is right to there — the batch has
already run by then. Here the same reflex would start work whose dependency was never
accepted, so every unreadable record resolves to `waiting` or `blocked` carrying the
reason it could not be read.
"""

from __future__ import annotations

import json
import pathlib

SCHEMA = "rig.queue-dependencies/v1"

#: The only policy this version defines. The issue asks for restraint here on purpose:
#: a vocabulary of edge conditions is a DAG language, and rig is not building one.
POLICY_ACCEPTED = "accepted"
POLICIES = (POLICY_ACCEPTED,)
DEFAULT_POLICY = POLICY_ACCEPTED

#: What an item's dependencies say about whether it may run now.
READY = "ready"
WAITING = "waiting"
BLOCKED = "blocked"

#: Queue statuses that end an item's work without a result to accept. A dependent of one
#: of these has nothing left to wait for, and each says so in its own words: `failed` can
#: be retried, `cancelled` (#459) was work someone decided would never run.
TERMINAL_UNRESOLVED = {
    "failed": "queue #{dep} ended as failed; retry it (`queue retry {dep}`) and the block "
              "clears on the next GO",
    "cancelled": "queue #{dep} was cancelled — it will not run, so this edge can never be "
                 "satisfied. Requeue it (`queue retry {dep}`) or drop the dependency",
}


class DependencyError(ValueError):
    """A dependency declaration rig refuses to store."""


def normalise(raw) -> list[str]:
    """Queue ids as strings, in order, without duplicates.

    Local queue ids are integers (`next_id`) while the github/gitlab backends hand back
    strings, and `queue_set_status` already compares with `str()`. Normalising once here
    keeps a `3` from failing to match a `"3"` somewhere later, which would read as a
    missing dependency rather than as a type mismatch.
    """
    if raw is None:
        return []
    if isinstance(raw, (str, int)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise DependencyError(f"depends_on must be a list, got {type(raw).__name__}")
    out: list[str] = []
    for entry in raw:
        if isinstance(entry, bool) or not isinstance(entry, (str, int)):
            raise DependencyError(f"dependency id must be a string or int, got {entry!r}")
        value = str(entry).strip()
        if not value:
            raise DependencyError("dependency id must not be empty")
        if value not in out:
            out.append(value)
    return out


def validate_new(items: list[dict], depends_on: list[str], policy: str) -> None:
    """Refuse a declaration before it is stored.

    Only three things can go wrong at this point, and all three are refusals rather than
    warnings: an unknown id (nothing will ever satisfy it), a self-reference (nothing can),
    and an unknown policy (guessing which one was meant is how a gate gets weaker than
    the operator thinks).

    A cycle cannot be created here — ids are monotonic and a new item can only reference
    ones that already exist, so its edges all point backwards. :func:`cycles` exists for
    the store `_local_load` already expects to have been hand-edited.
    """
    if policy not in POLICIES:
        raise DependencyError(
            f"dependency_policy {policy!r} is not one of: {', '.join(POLICIES)}")
    known = {str(it.get("id")) for it in items if isinstance(it, dict)}
    unknown = [d for d in depends_on if d not in known]
    if unknown:
        raise DependencyError(
            f"no queue item with id {', '.join(unknown)} — list the queue first "
            f"(`queue list`); a dependency on something that does not exist can never "
            f"be satisfied")


def cycles(items: list[dict]) -> list[list[str]]:
    """Every dependency cycle in the store, each as the ids that form it.

    Reached through a hand-edited `.rig/queue.json` rather than through the CLI. The file
    is already treated as something a person may have written by hand — `_local_load`
    repairs a stale `next_id` for exactly that reason — so checking is consistent with
    the store's own posture rather than defending against nothing.
    """
    edges = {str(it.get("id")): normalise_quietly(it.get("depends_on"))
             for it in items if isinstance(it, dict)}
    found: list[list[str]] = []
    seen: set[str] = set()
    stack: list[str] = []
    on_stack: set[str] = set()

    def walk(node: str) -> None:
        if node in on_stack:
            found.append(stack[stack.index(node):] + [node])
            return
        if node in seen:
            return
        seen.add(node)
        stack.append(node)
        on_stack.add(node)
        for nxt in edges.get(node, []):
            if nxt in edges:
                walk(nxt)
        stack.pop()
        on_stack.discard(node)

    for node in edges:
        walk(node)
    return found


def normalise_quietly(raw) -> list[str]:
    """:func:`normalise`, but a malformed value reads as no dependencies rather than raising.

    Used only where a bad declaration must not take down a listing. It is never used on
    the path that decides whether an item may run — there, a value rig cannot read has to
    stop the item, not release it.
    """
    try:
        return normalise(raw)
    except DependencyError:
        return []


def acceptance(runs_dir: pathlib.Path, task_id: str) -> dict:
    """What the workbench recorded about one task, copied and not re-judged.

    The queue does not evaluate the gate, and it does not decide whether an accept that
    overrode a failed gate should count. It reports `task_status`, and it carries
    `gate_status` and `forced` alongside so that a forced accept is visible to whoever
    reads the edge rather than being flattened into "accepted".
    """
    run = runs_dir / task_id
    task = _read_json(run / "task.json")
    if task is None:
        return {"observed": False,
                "reason": f"no run state at {run.name} — the workbench task this queue "
                          f"item reported was not found, so its acceptance cannot be read"}
    acc = _read_json(run / "acceptance.json") or {}
    return {"observed": True,
            "task_status": task.get("status"),
            "gate_status": acc.get("status"),
            "forced": bool(task.get("forced"))}


def _read_json(path: pathlib.Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _edge(dep_id: str, item: dict | None, runs_dir: pathlib.Path) -> dict:
    """One dependency, and whether it has been satisfied.

    Every branch that is not "the workbench task says accepted" produces a reason. A
    dependency this cannot read is not a dependency that is met.
    """
    base = {"id": dep_id, "satisfied": False, "queue_status": None,
            "task_id": None, "acceptance": None}
    if item is None:
        return {**base, "state": BLOCKED,
                "reason": f"queue #{dep_id} does not exist — nothing will ever satisfy this "
                          f"edge (a hand-edited queue store, or an item deleted after it "
                          f"was depended on)"}
    base["queue_status"] = item.get("status")
    terminal = TERMINAL_UNRESOLVED.get(str(item.get("status")))
    if terminal:
        return {**base, "state": BLOCKED, "reason": terminal.format(dep=dep_id)}
    # The dependency's *current* run has to be over. Without this, an item that was
    # accepted once and has since been requeued (`queue retry`) still carries the old
    # task id, and its dependent would be released against a result that is being
    # replaced. The recorded id answers "what did this item produce"; only the item's own
    # status answers "and is that still what it is producing".
    if item.get("status") != "done":
        return {**base, "state": WAITING,
                "reason": f"queue #{dep_id} is {item.get('status')!r} — its current run "
                          f"has not finished, so whatever it produced before is not what "
                          f"this edge is waiting on"}
    task_id = str(item.get("task_id") or "").strip()
    if not task_id:
        return {**base, "state": WAITING,
                "reason": f"queue #{dep_id} has no workbench task recorded, so whether its "
                          f"result was accepted cannot be observed — it has not run yet, or "
                          f"it ran under a provider that registers no rig task, in which "
                          f"case this will not clear on its own"}
    base["task_id"] = task_id
    observed = acceptance(runs_dir, task_id)
    base["acceptance"] = observed
    if not observed["observed"]:
        return {**base, "state": WAITING, "reason": observed["reason"]}
    status = observed["task_status"]
    if status == "accepted":
        note = " (accepted over an unmet gate — `forced`)" if observed["forced"] else ""
        return {**base, "satisfied": True, "state": READY,
                "reason": f"queue #{dep_id} → {task_id} is accepted{note}"}
    if status == "discarded":
        return {**base, "state": BLOCKED,
                "reason": f"queue #{dep_id} → {task_id} was discarded; nothing from it will "
                          f"be accepted"}
    return {**base, "state": WAITING,
            "reason": f"queue #{dep_id} → {task_id} is {status!r} and has not been accepted. "
                      f"Accepting is a person's action (`workbench.py accept {task_id}`); "
                      f"this clears on the next GO afterwards"}


def resolve(item: dict, items: list[dict], runs_dir: pathlib.Path) -> dict:
    """May this item run now, and if not, why.

    An item with no `depends_on` is `ready` with no edges — that is the whole of the
    backwards compatibility promise, and it is a different rule from every other absence
    here: nothing was declared, so nothing is unmet. Anything rig tried to read and could
    not goes the other way.
    """
    try:
        declared = normalise(item.get("depends_on"))
    except DependencyError as exc:
        return {"schema": SCHEMA, "state": BLOCKED, "policy": None, "edges": [],
                "reason": f"this item's `depends_on` cannot be read ({exc}); rig will not "
                          f"run an item whose dependencies it does not understand"}
    policy = item.get("dependency_policy") or DEFAULT_POLICY
    if not declared:
        return {"schema": SCHEMA, "state": READY, "policy": policy, "edges": [],
                "reason": ""}
    if policy not in POLICIES:
        return {"schema": SCHEMA, "state": BLOCKED, "policy": policy, "edges": [],
                "reason": f"dependency_policy {policy!r} is not one this version defines "
                          f"({', '.join(POLICIES)}); rig will not guess which was meant"}

    in_cycle = {node for ring in cycles(items) for node in ring}
    if str(item.get("id")) in in_cycle:
        return {"schema": SCHEMA, "state": BLOCKED, "policy": policy, "edges": [],
                "reason": "this item is part of a dependency cycle, so no order of "
                          "execution satisfies it — repair `.rig/queue.json`"}

    by_id = {str(it.get("id")): it for it in items if isinstance(it, dict)}
    edges = [_edge(dep, by_id.get(dep), runs_dir) for dep in declared]
    blocked = [e for e in edges if e["state"] == BLOCKED]
    waiting = [e for e in edges if e["state"] == WAITING]
    state = BLOCKED if blocked else WAITING if waiting else READY
    reason = " / ".join(e["reason"] for e in (blocked or waiting)) if state != READY else ""
    return {"schema": SCHEMA, "state": state, "policy": policy, "edges": edges,
            "reason": reason}


def graph(items: list[dict], runs_dir: pathlib.Path) -> dict:
    """The dependency graph as data, for any client that wants to draw it (#427 AC 8).

    Presentation-neutral in the same sense as `rig.assurance-graph/v1`: nodes carry a
    state and a reason, never a colour, a coordinate or a class. A second client reads
    this without adopting Mission Control's stylesheet, and Mission Control decides
    nothing here that the queue and the workbench have not already decided.
    """
    rows = [it for it in items if isinstance(it, dict)]
    nodes, edges = [], []
    for item in rows:
        node_id = str(item.get("id"))
        resolution = resolve(item, rows, runs_dir)
        nodes.append({
            "id": node_id,
            "task": item.get("task"),
            "queue_status": item.get("status"),
            "task_id": item.get("task_id") or None,
            "dependency_state": resolution["state"],
            "dependency_policy": resolution["policy"],
            "blocked_reason": resolution["reason"],
        })
        for edge in resolution["edges"]:
            edges.append({"from": edge["id"], "to": node_id,
                          "state": edge["state"], "satisfied": edge["satisfied"],
                          "reason": edge["reason"]})
    return {"schema": SCHEMA, "policies": list(POLICIES), "nodes": nodes, "edges": edges,
            "cycles": cycles(rows)}
