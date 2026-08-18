"""The shape a run actually took, as nodes and edges (#426).

Mission Control can already list a task's steps and its acceptance criteria. What a
list cannot show is the *shape*: which steps ran one after another, which fanned out
to several reviewers at once, where the machine gate sits, and which of those a person
still has to sign. Reading that out of two flat lists is work the reader should not
have to do.

This is a reader. It builds nothing of its own and decides nothing:

    task.json + steps.json + review.json   ──┐
                                             ├─→ graph (nodes, edges)
    assurance.build_receipt()  ──────────────┘
       (itself a projection of gate / provenance / approvals)

Gate status, approvals and the final verdict all arrive through the receipt, so this
module never touches the gate, RBAC or approval logic — a projection of a projection
introduces no third opinion, which is the only way "does not duplicate the judgment"
can be true by construction rather than by promise.

**Structure comes from what ran, not from the recipe on disk.** `steps.json` records
the steps as they were seeded, and it is the authority for which ran and how each
turned out. It does not record whether a step was serial or a fan-out, so that one
attribute is read from the recipe — and only when the recipe's step ids still match
the recorded ones exactly. A recipe edited after the run describes a different run;
saying so is better than drawing it.

**Presentation-neutral.** No colours, no coordinates, no pixel sizes. Nodes carry a
`lane` and a `kind`; how those become a picture is the client's business, and a second
client should not have to adopt this one's stylesheet to read the same graph.
"""

from __future__ import annotations

import pathlib

from . import assurance
from .state import load_task

SCHEMA = "rig.assurance-graph/v1"

#: The four responsibilities a node can belong to. A lane is a statement about *who or
#: what is answerable* at that point — not a column index — which is why the verifier
#: lane exists even when nothing is recorded about who verified: the responsibility is
#: real whether or not the identity was captured.
LANES = ("execution", "verification", "gate", "decision")

#: A step staffed by reviewer personas is a verification step. Keyed on the persona
#: naming convention rather than on the step id, because a recipe may call its review
#: step anything at all, and a graph that only recognises the id `review-diff` would
#: quietly file a custom recipe's reviewers under execution.
_REVIEWER_SUFFIX = "-reviewer"


def _declared_steps(recipe: str, root: pathlib.Path) -> list[dict]:
    """The recipe's own steps, `extends` resolved, or [] when it cannot be read.

    The repository being graphed is consulted before the installed one. Mission Control
    can serve a checkout that is not the rig doing the serving, and reading that other
    repository's run against this one's recipes would describe a workflow it never had.

    Silent on failure for the same reason `progress.load_recipe_steps` is: a broken or
    absent recipe must cost the graph one attribute, never the graph.
    """
    if not recipe:
        return []
    try:
        from ..orchestrate.config import RECIPES
        from ..orchestrate.recipes import load_steps, parse_frontmatter, resolve_extends

        local = root / "skills" / "engine" / "recipes" / f"{recipe}.md"
        path = local if local.is_file() else pathlib.Path(RECIPES) / f"{recipe}.md"
        if not path.is_file():
            return []
        frontmatter = parse_frontmatter(path)
        try:
            frontmatter, _ = resolve_extends(frontmatter, path)
        except Exception:
            pass
        return [s for s in load_steps(frontmatter) if s.get("id")]
    except Exception:
        return []


#: What `structure_resolved_from` can say, and what each value means for a reader.
#: `recipe-as-currently-defined` is the honest ceiling: the run recorded *which* recipe
#: it used and not *which revision*, so matching step ids prove the recipe still has the
#: same steps — never that the step bodies are the ones that ran.
STRUCTURE_SOURCES = {
    "recipe-as-currently-defined":
        "the recipe's step ids still match this run's, so its per-step attributes are "
        "shown — but from the recipe as it stands now. The run did not record which "
        "revision it used, so an in-place edit that kept the ids (a step switched "
        "between serial and parallel-fanout, say) would be shown as though it had "
        "always been that way",
    "recipe-drifted":
        "the recipe's steps no longer match this run's, so none of its per-step "
        "attributes are shown; pairing them up would attach one step's shape to "
        "another step's outcome",
    "unrecorded":
        "the recipe could not be read, so no per-step structure is shown",
}


def _structure(recorded: list[dict], declared: list[dict]) -> tuple[dict, str]:
    """Per-step attributes the run did not record, and where they came from.

    Matching ids is the weakest of the two things a reader might assume it means. It
    shows the recipe still declares the same steps; it cannot show that those steps are
    unchanged, because the run recorded a recipe *name* and never a revision. So the
    strongest value this returns is `recipe-as-currently-defined`, and the caveat
    travels with it rather than living in a doc nobody reading the graph will open.
    """
    names = [s.get("name") for s in recorded]
    ids = [s.get("id") for s in declared]
    if not declared:
        return {}, "unrecorded"
    if names != ids:
        return {}, "recipe-drifted"
    return ({s["id"]: s for s in declared}, "recipe-as-currently-defined")


def _is_verification(step: dict, declared: dict | None) -> bool:
    personas = step.get("personas") or (declared or {}).get("personas") or []
    return any(str(p).endswith(_REVIEWER_SUFFIX) for p in personas)


#: How a recorded reviewer verdict reads as a node status. `REJECT` must not render as
#: a pass: the panel shows a glyph and a colour long before anyone reads the label, and
#: a rejecting reviewer drawn in green is the worst thing this graph could say.
#: `config.VALID_VERDICT` is the vocabulary and the suite holds this map level with it;
#: an unrecognised value reads as `pending` here, because a verdict nobody understands
#: is not one to vouch for. Deliberately *not* an import-time assert — `mission_server`
#: imports this module at load, so a vocabulary change would take the whole page down
#: to prevent one node reading `pending`, and `-O` would strip the check anyway. The
#: drift belongs in a test, where it fails the build instead of the server.
_VERDICT_STATUS = {
    "APPROVE": "passed",
    "REJECT": "failed",
    "APPROVE_WITH_CONDITIONS": "warning",
}


def _reviewer_verdicts(review: dict | None) -> tuple[dict[str, str], set[str]]:
    """Each persona's verdict, and which personas were recorded more than once.

    Last row wins, matching `cmd_review`'s own upsert, so the graph and the writer agree
    on which record is current. `rig-wb review` cannot produce a duplicate, but
    `review.json` is a plain file and a hand-edited or externally-written one can — and
    a conflicting pair silently collapsing to whichever came last is the kind of quiet
    that this module exists to avoid. So the duplicates come back too, and the node says
    so rather than showing one verdict as though it were the only one.
    """
    verdicts: dict[str, str] = {}
    duplicated: set[str] = set()
    for entry in (review or {}).get("verdicts", []):
        # `is None` rather than falsiness: a row that names a persona is a row, and
        # dropping the odd ones would let a hand-edited file hold a verdict this reader
        # reports as absent. Keys are stringified because that is how they are looked
        # up — a fan-out member's name comes from the step's `personas`, which are
        # strings — so `cmd_review`'s raw keys and these can differ for a non-string
        # persona. That asymmetry is left alone: matching the writer would mean a
        # numeric persona shadowing a string one at lookup time, which is worse than
        # a hand-written oddity never matching a member and therefore never rendering.
        if not isinstance(entry, dict) or entry.get("persona") is None:
            continue
        persona = str(entry["persona"])
        if persona in verdicts:
            duplicated.add(persona)
        verdicts[persona] = str(entry.get("verdict"))
    return verdicts, duplicated


def _provider_slots() -> dict:
    """Execution and verification providers, kept apart even when both are empty.

    Merging them into one "provider: unknown" would erase the distinction the trust
    boundary rests on — that the thing which wrote the change is not the thing which
    judged it. Two slots, each saying separately that nobody recorded it, keeps the
    question visible.
    """
    return {
        "execution": assurance.unobserved(
            "rig does not record which provider/model executed a workbench step; the "
            "harness that invoked rig is on the task's `caller`, which is not the same "
            "question"
        ),
        "verification": assurance.unobserved(
            "rig records which reviewer personas ran, but not the provider behind them; "
            "independence from the executing provider therefore cannot be shown here"
        ),
    }


def _review_reference(root: pathlib.Path, run: pathlib.Path) -> dict | None:
    """A pointer to `review.json`, or None when the reviewers left no record."""
    path = run / "review.json"
    if not path.is_file():
        return None
    data = assurance._read_json(path) or {}
    return {"path": str(path.relative_to(root)),
            "verdicts": len(data.get("verdicts") or [])}


def build_graph(root: pathlib.Path, task_id: str) -> dict:
    """The resolved execution graph for one task. Reads only; decides nothing."""
    run, task = load_task(root, task_id)
    receipt = assurance.build_receipt(root, task_id)
    steps_doc = assurance._read_json(run / "steps.json") or {}
    recorded = [s for s in steps_doc.get("steps", []) if isinstance(s, dict) and s.get("name")]
    declared_list = _declared_steps(str(task.get("recipe") or ""), root)
    declared, resolved_from = _structure(recorded, declared_list)
    verdicts, duplicated = _reviewer_verdicts(assurance._read_json(run / "review.json"))

    nodes: list[dict] = []
    edges: list[dict] = []

    def add(node: dict) -> str:
        nodes.append(node)
        return node["id"]

    def link(src: str, dst: str, kind: str = "sequence") -> None:
        edges.append({"from": src, "to": dst, "kind": kind})

    task_status = str(task.get("status") or "")
    previous = add({
        "id": "task", "kind": "task", "lane": "execution",
        "label": task.get("input") or task_id,
        # Read, not assumed. A hardcoded `passed` here put a green head on a discarded
        # run — the one node a reader glances at first.
        "status": {"accepted": "passed", "discarded": "skipped",
                   "running": "running"}.get(task_status, "pending"),
        "detail": f"{task.get('task_type') or '?'} · {task.get('recipe') or '?'}"
                  f" · task status {task_status or 'unknown'}",
    })
    isolation = receipt["isolation"]
    node = add({
        "id": "isolate", "kind": "isolate", "lane": "execution",
        "label": isolation.get("mode"),
        # `skipped` for a --no-worktree run: nothing was isolated, and saying `passed`
        # would describe an isolation that did not happen.
        "status": "passed" if isolation.get("mode") == "git-worktree" else "skipped",
        "detail": isolation.get("enforced_by") or isolation.get("note"),
        # Carried verbatim from the receipt: a client must not be able to render a
        # worktree and an OS sandbox with the same badge.
        "isolation": isolation,
    })
    link(previous, node)
    previous = node

    for step in recorded:
        name = str(step["name"])
        spec = declared.get(name, {})
        status = str(step.get("status") or "pending")
        verification = _is_verification(step, spec)
        personas = list(step.get("personas") or spec.get("personas") or [])
        pattern = spec.get("pattern")
        fanout = pattern == "parallel-fanout" or (verification and len(personas) > 1)
        node_id = f"step:{name}"
        node = {
            "id": node_id,
            "kind": "fanout" if fanout else "step",
            "lane": "verification" if verification else "execution",
            "label": name, "status": status,
            "instruction": step.get("instruction") or spec.get("instruction"),
            # `null` rather than a guessed "serial": the recipe is the only place this
            # is written down, and it was not readable for this run.
            "pattern": pattern,
            "personas": personas,
            "human_gate": bool(step.get("human_gate") or spec.get("human_gate")),
            "actor": step.get("actor") or spec.get("actor"),
        }
        add(node)
        link(previous, node_id)
        if fanout:
            members = []
            for persona in personas:
                member_id = f"{node_id}/{persona}"
                add({
                    "id": member_id, "kind": "reviewer", "lane": "verification",
                    "parent": node_id, "label": persona,
                    # A recorded verdict read for what it says, or the parent's status
                    # — never a verdict invented for a reviewer who did not report one,
                    # and never a rejection rendered as a pass.
                    "status": _VERDICT_STATUS.get(verdicts[persona], "pending")
                              if persona in verdicts else status,
                    "verdict": verdicts.get(persona),
                    "detail": ("review.json records more than one verdict for this "
                               "persona; showing the last"
                               if persona in duplicated else None) if persona in verdicts
                              else "no per-reviewer verdict recorded; showing the step's own status",
                })
                link(node_id, member_id, "fanout")
                members.append(member_id)
            node["members"] = members
        previous = node_id

    gates = receipt["gates"]
    gate_id = add({
        "id": "gate:acceptance", "kind": "gate", "lane": "gate",
        "label": "acceptance gate",
        "status": _gate_node_status(gates),
        "detail": gates.get("status") if gates.get("observed") else gates.get("reason"),
        # Everything a reader needs to walk back to the authority, without this module
        # holding an opinion about any of it.
        "references": {
            "criteria": gates.get("criteria", []) if gates.get("observed") else [],
            "overridden": gates.get("overridden", []) if gates.get("observed") else [],
            "evidence": receipt["evidence"],
            "provenance": receipt["provenance"],
            # The reviewers' own record, so the gate leads to it directly rather than
            # only through whichever member node happens to carry a verdict.
            "review": _review_reference(root, run),
        },
    })
    link(previous, gate_id)
    previous = gate_id

    approvals = receipt["approvals"]
    if approvals.get("observed"):
        approved, denied = approvals.get("approved", []), approvals.get("denied", [])
        approval_id = add({
            "id": "approval", "kind": "approval", "lane": "gate",
            "label": "human approval",
            # Deliberately not a verdict. Whether these decisions satisfy the rule —
            # quorum, roles, separation of duties, expiry, whether one denial sinks
            # three approvals — is `govern.approval.evaluate`'s judgment, and a second
            # opinion computed here would be exactly the duplication this must avoid.
            # What is recorded is that `accept` succeeded, and `accept` is where govern
            # enforces the rule. Anything short of that is still open.
            "status": "passed" if task_status == "accepted" else "pending",
            # Worded to the fact, not past it. `accept` blocks on an unsatisfied
            # approval — before `--force` is even considered — but only where a policy
            # layer is active; with governance off, `check_accept` returns without
            # evaluating anything and these decisions were advisory. Claiming "accept
            # enforced the rule" would assert a policy this graph never read.
            "detail": f"{len(approved)} approved, {len(denied)} denied"
                      + (" — the task was accepted, and accept is the only path that "
                         "enforces an approval rule where the repository has one"
                         if task_status == "accepted"
                         else " — not yet accepted, so no approval rule has been enforced"),
            "decided_by": "govern (evaluated at accept, not here)",
            "references": {"decisions": approvals.get("decisions", []),
                           "approved": approved, "denied": denied},
        })
        link(previous, approval_id)
        previous = approval_id

    final = receipt["final_status"]
    decision_id = add({
        "id": "decision", "kind": "decision", "lane": "decision",
        "label": final["value"], "status": _decision_node_status(final["value"]),
        "detail": final["basis"],
    })
    link(previous, decision_id)

    return {
        "schema": SCHEMA,
        "task_id": task_id,
        "recipe": {"name": task.get("recipe"),
                   "structure_resolved_from": resolved_from,
                   "structure_caveat": STRUCTURE_SOURCES[resolved_from]},
        "lanes": list(LANES),
        "providers": _provider_slots(),
        "nodes": nodes,
        "edges": edges,
        # The graph is exactly as current as the receipt it projects, so it points at
        # the receipt's own sources rather than digesting them a second time.
        "sources": receipt["sources"],
    }


def _gate_node_status(gates: dict) -> str:
    if not gates.get("observed"):
        return "pending"
    return {"passed": "passed", "passed_with_warnings": "warning",
            "failed": "failed", "pending": "pending",
            "skipped": "skipped"}.get(str(gates.get("status")), "pending")


def _decision_node_status(value: str) -> str:
    if value == "acceptable":
        return "passed"
    if value in ("rejected", "accepted-over-failed-gate"):
        return "failed"
    if value in ("accepted-over-unresolved-gate", "accepted-without-gate"):
        return "warning"
    if value == "discarded":
        return "skipped"
    return "pending"
