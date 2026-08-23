"""What assurance was asked for, checked against what the receipt recorded (#434).

The assurance receipt already writes down what a task *achieved*: which tree the work was
written to, how independent the checker was, whether the accept record still verifies, which
approvals were recorded, how the gate ruled. What it has never had is the other half — what
was *asked for* — and without that "the gate passed" is a fact about rig's defaults rather
than about anyone's requirements.

This module is the asking half, and it is deliberately small, because the hard part is
already done: the receipt does the observing, and everything here copies from it.

**A target may only name an axis the receipt can answer.** `_isolation` in that module
refuses to borrow the evaluation vocabulary's `none/agent-policy/os-enforced` ranking,
because a git worktree keeps a change off the main tree and stops there — claiming an OS
boundary git is not holding would be the receipt lying about itself. A target that could
demand `os-enforced` would be demanding an answer nothing here can give, and reporting that
as "unmet" would say rig looked and found the isolation weak. It did not look; it cannot.
So an axis rig cannot observe is `unobservable`, which is its own outcome and not a softer
`unmet` — the same three-way split `hostcheck` states for itself and `intent.py` uses for
requirements.

**A target is machine-readable or it is refused.** "production quality" is not a target. It
is a word that would have to be mapped to one, and rig cannot explain a mapping it did not
receive — so it does not invent one. The Issue asking for this module lists that mapping
among its non-goals, and a refusal here is what keeps it there.

**Nothing downgrades quietly.** If what was asked for is not what was achieved, that is the
answer. There is no nearest-acceptable, no partial credit, and no rounding in rig's favour.
"""

from __future__ import annotations

SCHEMA = "rig.assurance-target/v1"

#: Asked for, and recorded as achieved.
MET = "met"
#: Asked for, and the receipt records something that does not satisfy it.
UNMET = "unmet"
#: Asked for, and rig has no way to answer this axis at all. Not a weaker `unmet`: `unmet`
#: says rig looked, and this says it cannot. A caller that folds them together will read
#: "we do not measure that" as "we measured it and it was insufficient", and act on it.
UNOBSERVABLE = "unobservable"

OUTCOMES = (MET, UNMET, UNOBSERVABLE)

#: The axes the receipt actually reports, and the values each one can take. Written down here
#: rather than inferred, so that adding an axis to the receipt is a deliberate act in two
#: places instead of a silent widening of what a target may promise.
#: Which receipt block each axis reads from, where the names differ. Written down because
#: guessing it inline is how `approval` came to look for a block called `approval` and report
#: rig's own placeholder instead of the receipt's reason for not having looked.
BLOCKS = {"verification": "verifier", "approval": "approvals", "gate": "gates"}

AXES = {
    "isolation": ("git-worktree", "main-tree"),
    # No `independent`. `_verifier` never asserts it: rig's review step dispatches subagents
    # whose identity never reaches task state, so for work rig produced itself the verdict is
    # `unrecorded`, and for an imported change it is `declared-separate` — "a weaker claim
    # wearing its own weakness", in that module's words. Offering `independent` would offer a
    # target nothing can meet.
    "verification": ("declared-separate", "unrecorded"),
    "provenance": ("signed-and-verified", "none"),
    # No `none`. `_approvals` reports the absence of decisions as `observed: false` with a
    # reason — governance inactive, or no human gate declared — so there is no receipt shape
    # that *achieves* "no approvals"; asking for it would be unobservable forever, from a
    # table whose whole claim is that it mirrors what the receipt can say. Requiring the
    # absence of an approval would need the receipt to distinguish "looked, found none
    # required" from "could not look", and it does not.
    "approval": ("recorded",),
    "gate": ("passed", "passed_with_warnings", "failed", "pending", "skipped"),
}

#: Words that name a level without naming what it is. Refused by name so the refusal can say
#: what to write instead, rather than failing an unknown-axis check and leaving the author to
#: guess that the whole phrasing was wrong.
VAGUE = ("production quality", "production-quality", "production", "high assurance",
         "high-assurance", "best effort", "best-effort", "strong", "strict", "maximum",
         "enterprise", "prod")


#: The keys a target document may carry. Closed for the reason every other schema in this
#: package is: a key accepted here and read by nothing would let a target carry `waive: true`
#: or `axis: "isolation"` all the way to the floor, the receipt and the dashboard while the
#: field it asserted was discarded — leaving the author believing the target said something no
#: part of rig ever read.
TARGET_KEYS = frozenset({"schema", "axes"})


def validate(payload: dict) -> list[str]:
    """Every way this payload is not an assurance target, not the first one.

    Collected rather than short-circuited, for the reason every other validator in this
    repository collects them: an author who fixes one problem and is refused again for the
    next learns nothing from the second refusal that the first could not have told them.
    """
    problems: list[str] = []
    if not isinstance(payload, dict):
        return [f"target: expected an object, got {type(payload).__name__}"]

    if payload.get("schema") != SCHEMA:
        problems.append(f"schema: expected {SCHEMA!r}, got {payload.get('schema')!r}")

    unknown = sorted(str(key) for key in payload if key not in TARGET_KEYS)
    if unknown:
        problems.append(
            f"unknown key(s) {', '.join(unknown)}: a target carries "
            f"{', '.join(sorted(TARGET_KEYS))}, and a key nothing reads would be asserted here "
            f"and answered nowhere")

    axes = payload.get("axes")
    if not isinstance(axes, dict):
        problems.append("axes: expected an object mapping an axis to the value required")
        return problems
    if not axes:
        # A target asking for nothing is met by anything, which is a way of saying the run
        # was unconstrained while looking like it was constrained.
        problems.append("axes: a target that requires nothing is met by everything; omit "
                        "the target instead of writing an empty one")

    for axis, required in sorted(axes.items()):
        if axis not in AXES:
            problems.append(
                f"axes.{axis}: rig's receipt does not report that axis. It reports "
                f"{', '.join(sorted(AXES))} — asking for anything else would be asking for "
                f"an answer nothing here can give")
            continue
        if not isinstance(required, str):
            problems.append(f"axes.{axis}: expected a string, got {type(required).__name__}")
            continue
        if required.strip().lower() in VAGUE:
            problems.append(
                f"axes.{axis}: {required!r} names a level without naming what it is. rig "
                f"cannot explain a mapping it did not receive, so it does not invent one — "
                f"write one of {', '.join(AXES[axis])}")
            continue
        if required not in AXES[axis]:
            problems.append(f"axes.{axis}: {required!r} is not one of "
                            f"{', '.join(AXES[axis])}")
    return problems


def read(path) -> dict:
    """A target document from disk, refusing what no reader of one should accept.

    The one place a target is parsed. `intent.read` exists for the same reason and was written
    after a reviewer found three parsers disagreeing: JSON allows a key twice and `json.loads`
    keeps the last one silently, so `"gate": "failed", "gate": "passed"` would be refused by
    whichever caller thought to check and accepted as a request for a passing gate by the rest.
    A rule each caller has to remember is a rule one of them will not.
    """
    import json as _json
    import pathlib as _pathlib

    from .synthesis import _no_duplicate_keys

    return _json.loads(_pathlib.Path(path).read_text(encoding="utf-8"),
                       object_pairs_hook=_no_duplicate_keys("target"))


def _achieved(receipt: dict) -> dict[str, str | None]:
    """What the receipt records on each axis, translated into this vocabulary and no further.

    `None` means the receipt did not observe that axis — it says so itself, with a reason,
    and inventing a value here to compare against would be exactly the manufacture the
    receipt's own discipline forbids.
    """
    def block(name: str) -> dict:
        value = receipt.get(name)
        return value if isinstance(value, dict) else {}

    isolation = block("isolation")
    verifier = block("verifier")
    # Same rule as the others: a block that says it did not observe is believed over anything
    # left beside it. `_verifier` fills `independence` on the path where it *did* look, so a
    # verdict sitting next to `observed: false` is a leftover, not a finding.
    independence = (verifier.get("independence")
                    if verifier.get("observed") is not False
                    and isinstance(verifier.get("independence"), dict) else {})
    provenance = block("provenance")
    approvals = block("approvals")
    gates = block("gates")

    # Identity, not equality or truthiness. `1 == True` in Python, so a dict lookup keyed on
    # `True` answers for `1`; and truthiness would read `"yes"` as verified and `""` as a
    # failed check. Three values are what the producer emits, and anything else is a receipt
    # this code does not understand — which is not a licence to pick the nearest one.
    verified = provenance.get("verified")
    signature = ("signed-and-verified" if verified is True
                 else "none" if verified is False else None)

    return {
        "isolation": isolation.get("mode") if isolation.get("observed") else None,
        # The receipt keeps the verdict inside `independence`; an unobserved verifier block
        # and an `unrecorded` verdict are different facts and stay different here.
        "verification": independence.get("verdict") if independence else None,
        "provenance": signature if provenance.get("observed") else None,
        "approval": ("recorded" if approvals.get("observed") else None),
        "gate": gates.get("status") if gates.get("observed") else None,
    }


def evaluate(target: dict, receipt: dict) -> dict:
    """Compare what was asked for against what the receipt recorded.

    Nothing is judged here. Each axis is read out of the receipt and compared to the value
    the target named; where the receipt observed nothing, the outcome is `unobservable` and
    carries the receipt's own reason for not having looked.

    `validate` is not re-run: a caller handing over an invalid target would get an answer
    shaped like a verdict, so this raises instead.
    """
    problems = validate(target)
    if problems:
        raise ValueError("not an assurance target:\n  " + "\n  ".join(problems))

    achieved = _achieved(receipt)
    axes: dict[str, dict] = {}
    for axis, required in sorted(target["axes"].items()):
        actual = achieved.get(axis)
        if actual is None:
            block = receipt.get(BLOCKS.get(axis, axis))
            reason = block.get("reason") if isinstance(block, dict) else None
            axes[axis] = {"outcome": UNOBSERVABLE, "required": required, "achieved": None,
                          "reason": reason or "the receipt does not record this axis"}
        else:
            axes[axis] = {"outcome": MET if actual == required else UNMET,
                          "required": required, "achieved": actual}

    outcomes = [entry["outcome"] for entry in axes.values()]
    if UNMET in outcomes:
        status = "assurance-incomplete"
    elif UNOBSERVABLE in outcomes:
        status = "assurance-unobservable"
    else:
        status = "assurance-complete"
    return {
        "schema": SCHEMA,
        "status": status,
        "axes": axes,
        "met": outcomes.count(MET),
        "unmet": outcomes.count(UNMET),
        "unobservable": outcomes.count(UNOBSERVABLE),
    }


#: What `assurance-target` returns. `1` covers both an invalid target and an unmet one,
#: because both mean the same thing to a caller deciding whether to proceed: what was asked
#: for has not been shown. The JSON says which.
COMPLETE, INCOMPLETE, EXECUTION_ERROR = 0, 1, 2


def cmd_assurance_target(args) -> "NoReturn":  # noqa: F821
    """Compare a target against a task's receipt.

    Exits rather than returns: the dispatcher calls subcommands for their effect and
    discards what they hand back, so an unmet target that returned `1` would print its
    reasons and leave the shell believing the assurance held.

    Never raises past this frame. A traceback and some other exit code is the same ambiguity
    this command exists to remove, which is `cmd_contract`'s reason too.
    """
    import json
    import sys

    from .assurance import build_receipt
    from .state import repo_root

    try:
        target = read(args.target)
    except Exception as exc:  # noqa: BLE001 — every failure to read is one status
        print(json.dumps({"schema": SCHEMA, "status": "execution-error",
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(EXECUTION_ERROR)

    problems = validate(target)
    if problems:
        print("\n".join(f"[REJECTED] {p}" for p in problems), file=sys.stderr)
        sys.exit(INCOMPLETE)

    try:
        receipt = build_receipt(repo_root(), args.task_id)
        # Through the projection, not `evaluate` directly. One *implementation* compares a
        # target against a receipt, and everything that shows a comparison — this command, the
        # receipt's own block, the Markdown page, Mission Control — reaches it through that
        # one, so two views cannot come to different answers about the same question.
        #
        # Not the same as one comparison happening. The run may have recorded a target of its
        # own, which the receipt has already compared; the target named on the command line is
        # a different question with a legitimately different answer. Both are printed, because
        # a command that showed only one while the other existed would let a reader take the
        # answer to the question they did not ask.
        from .assurance_wiring import projection

        result = projection(target, receipt)
        # `.get`, and then said out loud. Every receipt this repository builds carries the
        # block; one that does not is a receipt from somewhere else, and printing nothing
        # about the run's own target would read as the run having recorded none.
        recorded = receipt.get("assurance_target") or {
            "observed": False,
            "reason": "this receipt carries no assurance-target block, so what the run itself "
                      "asked for is unknown — not absent"}
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"schema": SCHEMA, "status": "execution-error",
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(EXECUTION_ERROR)

    if args.json:
        print(json.dumps({"schema": SCHEMA, "asked": result, "recorded": recorded},
                         ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"assurance target: {result['status']} — {result['met']} met, "
              f"{result['unmet']} unmet, {result['unobservable']} unobservable")
        for axis, entry in sorted(result["axes"].items()):
            if entry["outcome"] == MET:
                continue
            detail = entry.get("reason") or f"recorded {entry['achieved']!r}"
            print(f"  {entry['outcome']:>13}  {axis}: asked for "
                  f"{entry['required']!r} — {detail}")
        # The run's own target, when it has one. This command answered about the file named on
        # the command line; the receipt already answered about the file in the run, and a
        # reader who did not know both existed would take one for the other.
        print("  the run's own recorded target: "
              + (recorded["status"] if recorded.get("observed")
                 else f"none — {recorded['reason']}"))
    sys.exit(COMPLETE if result["status"] == "assurance-complete" else INCOMPLETE)
