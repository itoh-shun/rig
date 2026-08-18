"""The one answer an external orchestrator can act on (#429).

An orchestrator that hands rig a change needs to branch on the result, and prose does
not support that. What it needs is small: a stable status, a stable exit code, and the
identity of the thing that was judged. What it must never get is an answer it cannot
tell apart from a failure to answer.

That last point is the reason this module exists rather than a `--json` flag on
`receipt`. `state.die` exits 1 for everything — a bad task id, corrupt run state, an
unmet gate — so a caller reading exit 1 cannot distinguish "rig examined this and said
no" from "rig could not look". Both readings lead somewhere bad: retrying a refusal, or
merging past an outage. So nothing here calls :func:`die`; every failure becomes an
`execution-error` result with its own exit code.

    0  acceptable       rig's gate cleared this change
    1  not-acceptable   rig looked and this did not clear — including a change a human
                        accepted over a failed gate, which is an override, not a pass
    2  execution-error  rig could not answer; the change has not been judged
    3  pending          not decided yet; ask again later

Four, not the three the issue names, because folding `pending` into either of the
others is a specific and costly mistake: into `not-acceptable` and a poller reads
"still running" as "refused"; into `acceptable` and it merges something no gate has
ruled on. The three the issue asks for are all present and mean what it says.

The mapping from the receipt's `final_status` is exhaustive by construction and tested
against the receipt's own vocabulary, because the failure mode of a partial mapping is
a new status falling through to whatever the default was — and any default here is a
lie about a state nobody has considered.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import assurance
from .state import repo_root, resolve_task_id, run_dir

SCHEMA = "rig.assurance-contract/v1"

ACCEPTABLE = "acceptable"
NOT_ACCEPTABLE = "not-acceptable"
PENDING = "pending"
EXECUTION_ERROR = "execution-error"

EXIT_CODE = {ACCEPTABLE: 0, NOT_ACCEPTABLE: 1, EXECUTION_ERROR: 2, PENDING: 3}

#: Every value :func:`assurance._final_status` can emit, and what an external caller
#: should do about it. Judged nowhere: this is a second translation of a decision the
#: gate already made, and `final_status` travels alongside it verbatim so that nothing
#: is lost in the coarsening.
#:
#: The three `accepted-over-*` values are deliberately **not** `acceptable`. Each of
#: them is a human overriding rig — a forced accept, an unresolved gate, a skipped one
#: — and a caller told `acceptable` would record that rig vouched for a change it did
#: not. What rig can honestly say is that its gate did not clear this; who chose to
#: apply it anyway is in `final_status` and in the receipt.
STATUS = {
    "acceptable": ACCEPTABLE,
    "accepted-over-failed-gate": NOT_ACCEPTABLE,
    "accepted-over-unresolved-gate": NOT_ACCEPTABLE,
    "accepted-without-gate": NOT_ACCEPTABLE,
    "rejected": NOT_ACCEPTABLE,
    "discarded": NOT_ACCEPTABLE,
    "awaiting-acceptance": PENDING,
    "waiting-approval": PENDING,
    "in-progress": PENDING,
}


def final_status_vocabulary() -> set[str]:
    """Every value the receipt's `final_status.value` can take.

    Asked of the receipt rather than restated here. A hand-copied vocabulary is exactly
    what drifts, and the drift shows up as a status silently reported as `pending`.
    """
    return assurance.final_status_values()


def build(root: pathlib.Path, task_id: str) -> dict:
    """The contract result for one task.

    The receipt is built fresh and written to disk before the result is returned, so
    the path in `receipt` is guaranteed to hold the record this answer came from. A
    contract pointing at a receipt from an earlier run — or at a file that is not
    there — would send a caller to read a different answer than the one it acted on.
    """
    receipt = assurance.build_receipt(root, task_id)
    run = run_dir(root, task_id)
    path = run / "assurance.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    (run / "assurance.md").write_text(assurance.render_markdown(receipt), encoding="utf-8")

    final = receipt["final_status"]
    value = final.get("value")
    moved = assurance.target_moved(root, receipt)
    imported = (receipt.get("target") or {}).get("import")

    status = STATUS.get(value)
    if status is None:
        # An unmapped value is not rounded to the nearest friendly answer. The caller
        # is told rig produced a state this contract has no reading for, which is a
        # thing to fix rather than a thing to act on.
        # The receipt was written above and is readable; saying otherwise would send
        # the caller looking for a file that is right there, in the one situation
        # where reading it is how they find out what happened.
        return {**_error(task_id, receipt,
                         f"the receipt reported final status {value!r}, which this "
                         f"contract version has no mapping for"),
                "receipt": str(path.relative_to(root))}
    reason = final.get("basis", "")
    # A target that moved after verification is not a judgment being revised; it is
    # the judged object no longer being the one the caller is asking about. Saying
    # `acceptable` here would answer a question that was not asked.
    if moved["moved"] and status == ACCEPTABLE:
        status, reason = NOT_ACCEPTABLE, moved["reason"]

    return {
        "schema": SCHEMA,
        "status": status,
        "task_id": receipt["task"]["id"],
        "final_status": value,
        "reason": reason,
        "verified_head": (receipt["target"]["head"].get("commit")
                          if receipt["target"]["head"].get("observed") else None),
        "verified_head_immutable": receipt["target"]["immutable"],
        "target_moved": moved,
        "imported": bool(imported),
        "producer": (imported or {}).get("producer"),
        "gate_status": receipt["gates"].get("status") if receipt["gates"].get("observed") else None,
        "receipt": str(path.relative_to(root)),
    }


def _error(task_id: str | None, receipt: dict | None, reason: str) -> dict:
    return {
        "schema": SCHEMA,
        "status": EXECUTION_ERROR,
        "task_id": task_id,
        "final_status": (receipt or {}).get("final_status", {}).get("value"),
        "reason": reason,
        "verified_head": None,
        "verified_head_immutable": False,
        "target_moved": {"applicable": False, "moved": False,
                         "reason": "not established — rig did not reach a verdict"},
        "imported": None,
        "producer": None,
        "gate_status": None,
        "receipt": None,
    }


def cmd_contract(args: argparse.Namespace) -> None:
    """Never raises past this frame, and never calls `die`.

    Every exception becomes `execution-error` with exit 2. A crash escaping here would
    surface to the caller as some other exit code and a traceback on stderr, which is
    the same ambiguity this command exists to remove — just wearing a different hat.
    """
    task_id = None
    try:
        root = repo_root()
        task_id = resolve_task_id(root, args.task_id)
        result = build(root, task_id)
    except SystemExit as exc:
        # `resolve_task_id` and friends call `die`, which raises SystemExit(1). That
        # code means "not acceptable" in this command's vocabulary, so it has to be
        # translated rather than propagated.
        result = _error(task_id, None,
                        f"rig could not read this task's state (exit {exc.code}); "
                        f"stderr above carries the detail")
    except Exception as exc:  # noqa: BLE001 — the whole point is that nothing escapes
        result = _error(task_id, None, f"{type(exc).__name__}: {exc}")

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"## rig assurance contract: {result['task_id'] or '(unresolved)'}")
        print(f"  {result['status']} — {result['reason']}")
        if result["final_status"]:
            print(f"  final status: {result['final_status']} / gate: {result['gate_status']}")
        if result["verified_head"]:
            print(f"  verified head: {result['verified_head']} "
                  f"({'immutable' if result['verified_head_immutable'] else 'not immutable'})")
        if result["producer"]:
            print(f"  producer (declared): {result['producer']}")
        if result["receipt"]:
            print(f"  receipt: {result['receipt']}")
    sys.exit(EXIT_CODE[result["status"]])
