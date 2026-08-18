"""One page answering "why is this change acceptable?" (#428).

Everything a reviewer needs is already recorded — `task.json`, `acceptance.json`,
`provenance.json`, `steps.json`, `approvals.json`, the evidence directory — in six
files that each answer a different question and none of which answers that one. The
receipt is the projection that does, and it is *only* a projection: it re-judges
nothing, and every value in it is copied from a file that already decided it.

Three rules keep it from becoming a second truth.

**Absence is not success.** Most of what a full assurance story wants — which model
produced the change, who verified it, whether the verifier was independent — rig does
not record today. The tempting rendering is a blank, a zero, or a cheerful default,
and all three read as "fine" to anyone skimming. So every field that can be missing is
an :func:`unobserved` block carrying the reason it is missing, and a consumer that
ignores the wrapper reads a dict where it wanted a value rather than reading a pass.

**A worktree is not a sandbox.** `eval/cases.py` ranks isolation `none < agent-policy
< os-enforced` for *evaluation providers*, and reusing that vocabulary here would claim
an OS boundary that a `git worktree` does not have. What a workbench task can honestly
say is which tree it wrote to, so that is what this says.

**Staleness is detectable.** The receipt records the digest of every file it read.
:func:`verify` recomputes them, so a receipt built before a gate was overridden can be
told apart from one built after — by content, not by mtime, and without re-deriving
any of the judgments.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import subprocess

from .state import (die, load_task, repo_root, resolve_task_id, run_dir,
                    verify_provenance)

SCHEMA = "rig.assurance-receipt/v1"

#: Files under the run directory the receipt projects, and the key each lands under.
#: The digest of every one of them is recorded, which is what makes staleness a
#: content question rather than a timestamp question.
_SOURCES = (
    ("task", "task.json"),
    ("acceptance", "acceptance.json"),
    ("provenance", "provenance.json"),
    ("steps", "steps.json"),
    ("approvals", "approvals.json"),
)

#: How the authoritative gate status maps onto the receipt's final status. This is a
#: translation table, never a judgment: the receipt does not decide acceptability, and
#: a status it has no mapping for stays visible as itself rather than being rounded to
#: the nearest familiar word.
#: Task status is one of `running` / `accepted` / `discarded` (`workbench/accept.py`);
#: gate status one of `passed` / `passed_with_warnings` / `pending` / `skipped` /
#: `failed` (`state.gate_status`). Every pair is spelled out, because a pair this
#: table forgets falls through to `in-progress` — a settled task described as still
#: running, which is the wrong direction to be silently wrong in.
_GATE_STATUSES = ("passed", "passed_with_warnings", "pending", "skipped", "failed")
_FINAL_STATUS = {
    ("accepted", "passed"): "acceptable",
    ("accepted", "passed_with_warnings"): "acceptable",
    ("accepted", "failed"): "accepted-over-failed-gate",
    ("accepted", "pending"): "accepted-over-unresolved-gate",
    ("accepted", "skipped"): "accepted-without-gate",
    ("running", "passed"): "awaiting-acceptance",
    ("running", "passed_with_warnings"): "awaiting-acceptance",
    ("running", "failed"): "rejected",
    ("running", "pending"): "in-progress",
    ("running", "skipped"): "in-progress",
    **{("discarded", gate): "discarded" for gate in _GATE_STATUSES},
}


def final_status_values() -> set[str]:
    """Every value :func:`_final_status` can emit.

    Published from here rather than restated by whoever consumes it, because a
    hand-copied vocabulary is what drifts — and the drift shows up as a status silently
    reported as something familiar. The two additions are the values the fall-through
    branches emit, which the table alone does not contain.
    """
    return set(_FINAL_STATUS.values()) | {"waiting-approval", "in-progress"}


def unobserved(reason: str) -> dict:
    """A value rig does not have, and why.

    The reason is not decoration. "not recorded" invites a reader to assume the
    measurement failed this once; naming what would have had to record it says
    whether the gap is a bug, a limit, or simply a thing nobody has built yet.
    """
    return {"observed": False, "reason": reason}


def observed(**fields) -> dict:
    return {"observed": True, **fields}


def _digest(path: pathlib.Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _read_json(path: pathlib.Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _git(root: pathlib.Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", str(root), *args],
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _commit_exists(root: pathlib.Path, sha: str) -> bool:
    return bool(sha) and _git(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}") is not None


# ── the sections ─────────────────────────────────────────────────────────────
def _import_block(task: dict) -> dict | None:
    """The BYOO import record, or `None` for a task rig produced itself (#429).

    `None` here is not an unmeasured value and deliberately not an :func:`unobserved`
    block: rig knows perfectly well that this task was not imported. The distinction
    matters because the rest of this module treats absence as a thing to explain, and
    explaining this one would suggest a measurement had been attempted and failed.
    """
    block = task.get("import")
    if not isinstance(block, dict) or not block.get("head_commit"):
        return None
    return {
        "producer": block.get("producer"),
        "producer_runtime": block.get("producer_runtime"),
        "run_id": block.get("run_id"),
        "source_url": block.get("source_url"),
        "head_commit": block.get("head_commit"),
        "head_requested": block.get("head_requested"),
        "head_ref": block.get("head_ref"),
        "head_symbolic": bool(block.get("head_symbolic")),
        "claims": block.get("claims", []),
        "claims_gate_effect": "none",
        "diff_summary": block.get("diff_summary"),
        "imported_at": block.get("imported_at"),
    }


def _target(root: pathlib.Path, task: dict) -> dict:
    """What was verified, and whether that thing can still be pointed at.

    `immutable` is the roadmap's third invariant made checkable: a receipt about a
    commit that git can still resolve describes a fixed object, and one about a
    branch name describes whatever that name points at today.
    """
    base = str(task.get("base_commit") or "")
    effective = str(task.get("base_commit_effective") or base)
    imported = _import_block(task)
    # `record-commit` writes `commit_sha` (`workbench/feedback.py`). Guessing the
    # field name here cost nothing loudly: the receipt reported "no commit linked"
    # for tasks that had one, which is the false negative this module exists to
    # avoid producing.
    #
    # An imported task knows its head from the moment it is registered — that pinned
    # commit is the identity rig verified — so it is used when nothing has been linked
    # yet. `source` keeps the two apart: one is the commit that landed after `accept`,
    # the other is the commit that was handed to rig, and a reader who cannot tell
    # them apart cannot tell a verified change from a merged one.
    commit = task.get("commit_sha")
    source = "record-commit"
    if not commit and imported:
        commit, source = imported["head_commit"], "import"
    if commit and _commit_exists(root, str(commit)):
        head = observed(commit=str(commit), resolvable=True, source=source)
    elif commit:
        head = observed(commit=str(commit), resolvable=False, source=source)
    else:
        head = unobserved(
            "no commit is linked to this task — `workbench.py record-commit` links the "
            "final SHA after the change lands, and it has not been run for this one"
        )
    return {
        "repository": _git(root, "config", "--get", "remote.origin.url") or str(root.name),
        "base_branch": task.get("base_branch"),
        "base_commit": base or None,
        "base_commit_effective": effective or None,
        "base_rebased": bool(task.get("base_rebased")),
        "branch": task.get("branch"),
        "head": head,
        "immutable": bool(head.get("observed") and head.get("resolvable")),
        "import": imported,
    }


def _producer(task: dict) -> dict:
    """Who made the change, separating what was declared from what was inferred.

    `caller` follows `rig_workbench/caller.py`: `declared` marks an operator's
    statement, and anything else is rig's own guess from the environment. Collapsing
    the two would let a heuristic be read as a fact, which is the failure that module
    exists to prevent.

    `external` is the same distinction one level out (#429). Every field in it was
    supplied by whoever ran `import`, so it is reported as a declaration and never as
    something rig checked — including the producer's own claims, which carry
    `gate_effect: "none"` in the record rather than in a footnote.
    """
    caller = task.get("caller")
    if isinstance(caller, dict) and caller.get("id"):
        harness = observed(id=caller.get("id"), source=caller.get("source"),
                           declared=bool(caller.get("declared")))
    else:
        harness = unobserved(
            "tasks created before #428 carry no `caller` block, and a plain terminal "
            "identifies no harness to record"
        )
    imported = _import_block(task)
    if imported:
        external = observed(
            name=imported["producer"],
            run_id=imported["run_id"],
            source_url=imported["source_url"],
            claims=imported["claims"],
            claims_gate_effect="none",
            declared=True,
            basis="supplied to `workbench.py import`; rig verified the commit, not the "
                  "account of who produced it",
        )
    else:
        external = None
    # The reason a value is missing has to stay true. Saying "rig does not record the
    # provider/model" on a task whose operator declared one would be the mirror image
    # of the failure this module guards against: a recorded fact reported as absent.
    if imported and imported["producer_runtime"]:
        runtime = observed(id=imported["producer_runtime"], declared=True,
                           source="--producer-runtime")
    elif imported:
        runtime = unobserved(
            "this task was imported without --producer-runtime, so the runtime that "
            "produced the change was never stated"
        )
    else:
        runtime = unobserved(
            "rig does not record the provider/model that produced a workbench task; "
            "only evaluation runs (`evals/evidence/`) carry an execution identity"
        )
    return {
        "actor": task.get("actor") or None,
        "harness": harness,
        "external": external,
        "runtime": runtime,
    }


def _verifier(steps: dict | None, task: dict) -> dict:
    """Who checked it, and how independent that was.

    For a task rig produced itself the independence verdict is `unrecorded`, not
    `independent`. rig's review step dispatches subagents whose identity never reaches
    task state, so the honest answer is that nobody wrote it down — and a receipt that
    guessed `independent` here would be asserting exactly the property the trust
    boundary exists to establish.

    An imported task is the one case where something more can be said, and it is still
    less than `independent`: the change came from outside rig and rig ran its own gate
    over it, so producer and verifier are structurally different processes. What rig
    did not do is verify *who* the producer was — that name is a declaration. So the
    verdict is `declared-separate`, which is a weaker claim wearing its own weakness.
    """
    names = [s.get("name") for s in (steps or {}).get("steps", []) if isinstance(s, dict)]
    review_steps = [n for n in names if n and "review" in n]
    imported = _import_block(task)
    if imported:
        independence = {
            "verdict": "declared-separate",
            "basis": (
                f"the change was produced outside rig by `{imported['producer']}` and "
                "imported as a fixed commit, so rig's gate ran over work it did not do. "
                "This rests on the caller's declaration of who produced it, which rig "
                "did not verify — it is not a measurement of independence"
            ),
        }
    else:
        independence = {
            "verdict": "unrecorded",
            "basis": (
                "no producer or verifier identity is stored for a workbench task, so "
                "independence can be neither confirmed nor denied from this record"
            ),
        }
    return {
        "identity": unobserved(
            "rig does not record a reviewer identity per task; review steps are "
            "dispatched to subagents and only their verdict returns to task state"
        ),
        "review_steps": review_steps,
        "independence": independence,
    }


def _isolation(task: dict) -> dict:
    """Which tree the work was written to — deliberately not an isolation rank.

    `eval/cases.py`'s `none/agent-policy/os-enforced` ranks what an evaluation
    provider's sandbox enforces. A workbench task's worktree is a different claim:
    it keeps the change off the main tree, and it stops there. Borrowing the eval
    vocabulary would promise a boundary the OS is not holding.
    """
    branch = task.get("branch")
    if branch:
        return observed(
            mode="git-worktree",
            branch=branch,
            worktree_path=task.get("worktree_path"),
            enforced_by="git worktree — the change is confined to its own branch and "
                        "directory until `accept` stages it",
            note="this is write isolation from the main tree, not an OS sandbox: the "
                 "task's own commands run with the operator's full privileges",
        )
    return observed(
        mode="main-tree",
        enforced_by=None,
        note="the task was created with --no-worktree and wrote to the main working tree",
    )


def _gates(acceptance: dict | None) -> dict:
    """The acceptance gate exactly as it ruled, overrides included."""
    if not acceptance:
        return unobserved("no acceptance.json — the gate has not been evaluated for this task")
    checks = [c for c in acceptance.get("checks", []) if isinstance(c, dict)]
    criteria = []
    for c in checks:
        entry = {"name": c.get("name"), "status": c.get("status"),
                 "detail": c.get("detail") or ""}
        # A criterion a human set to passed over a sensor that said otherwise is the
        # single most important thing on this page, so it is a field rather than
        # something to be inferred from prose in `detail`.
        if c.get("tamper_override"):
            entry["overridden"] = True
            entry["overridden_sensor_findings"] = c.get("tamper_findings")
        elif c.get("detail"):
            entry["reason_recorded"] = True
        criteria.append(entry)
    counts: dict[str, int] = {}
    for c in criteria:
        counts[str(c["status"])] = counts.get(str(c["status"]), 0) + 1
    return observed(
        status=acceptance.get("status"),
        presets=acceptance.get("presets", []),
        checked_at=acceptance.get("checked_at"),
        counts=counts,
        criteria=criteria,
        overridden=[c["name"] for c in criteria if c.get("overridden")],
    )


def _approvals(approvals: dict | None) -> dict:
    if not approvals or not approvals.get("decisions"):
        return unobserved(
            "no approval decisions are recorded — governance is inactive for this "
            "repository, or this task's steps declare no human gate"
        )
    decisions = [d for d in approvals["decisions"] if isinstance(d, dict)]
    return observed(
        decisions=[{"actor": d.get("actor"), "decision": d.get("decision"),
                    "roles": d.get("roles", []), "at": d.get("at") or d.get("ts"),
                    "head": d.get("head"), "note": d.get("note") or ""}
                   for d in decisions],
        approved=[d.get("actor") for d in decisions if d.get("decision") == "approve"],
        denied=[d.get("actor") for d in decisions if d.get("decision") == "deny"],
    )


def _provenance(root: pathlib.Path, provenance: dict | None) -> dict:
    """The existing signed accept record, referenced rather than re-signed.

    `accept` already HMACs its own account of the gate. Signing the same facts a
    second time under a second key would create two records that can disagree, and
    the disagreement would be rig's to explain. The receipt points at that signature
    and reports whether it still verifies.
    """
    if not provenance:
        return unobserved(
            "no provenance.json — it is written by `workbench.py accept`, and this "
            "task has not been accepted"
        )
    record = provenance.get("record")
    signature = provenance.get("signature")
    verified = None
    if isinstance(record, dict) and isinstance(signature, str):
        try:
            verified = verify_provenance(root, record, signature)
        except Exception:
            verified = None
    return observed(
        algorithm=provenance.get("algo"),
        signature=signature,
        verified=verified,
        forced=bool((record or {}).get("forced")),
        accepted_at=(record or {}).get("accepted_at"),
        verify_with=f"workbench.py verify-provenance {(record or {}).get('task_id') or ''}".strip(),
    )


def _evidence(root: pathlib.Path, run: pathlib.Path, gates: dict) -> list[dict]:
    """Pointers back to the authoritative artefacts, with digests.

    A reference without a digest is an invitation to read a file that may have moved
    on since the receipt described it. These are the same digests `verify` checks.

    `evals/evidence/` is repository-level and belongs to no single task, so it is
    listed only when this task's gate carries the criterion that consults it, and it
    says so in `scope`. Attaching every signed evaluation to every receipt would read
    as "this task produced that evidence", which is a claim nobody made.
    """
    out = []
    for name, kind in (("diff.md", "diff summary"), ("risk.md", "risk summary")):
        p = run / name
        if p.is_file():
            out.append({"path": str(p.relative_to(root)), "kind": kind,
                        "scope": "task", "sha256": _digest(p)})
    criteria = gates.get("criteria", []) if gates.get("observed") else []
    consults_eval = any(c.get("name") == "prompt_regression_passed" for c in criteria)
    evidence_root = root / "evals" / "evidence"
    if consults_eval and evidence_root.is_dir():
        for current in sorted(evidence_root.glob("*/current.json")):
            out.append({"path": str(current.relative_to(root)),
                        "kind": "signed evaluation evidence backing `prompt_regression_passed`",
                        "scope": "repository", "case": current.parent.name,
                        "sha256": _digest(current)})
    return out


def _final_status(task: dict, gates: dict, approvals: dict | None) -> dict:
    """The authoritative outcome, translated but never re-decided.

    One overlay: a task that is not accepted while its run directory holds recorded
    approval decisions, none of which is an approval, is waiting on a person. Both
    halves of that are facts on disk. What this does *not* do is evaluate the policy
    to work out whether the quorum is met — `govern` owns that judgment, and a second
    opinion here is exactly the second truth the receipt must not become.
    """
    task_status = str(task.get("status") or "unknown")
    gate_status = str(gates.get("status") or "") if gates.get("observed") else ""
    if not gate_status:
        return {"value": "in-progress",
                "basis": f"task status `{task_status}`; the gate has not ruled"}
    key = (task_status, gate_status)
    if key in _FINAL_STATUS:
        value = _FINAL_STATUS[key]
        basis = f"task status `{task_status}` with gate `{gate_status}`"
        # Narrowed deliberately: only a task the table has already placed at
        # `awaiting-acceptance` can be waiting on a person. Applying the overlay
        # first would let a pending signature mask a state that is already settled —
        # a discarded task, or one whose gate failed — which is the opposite of what
        # this status is for.
        decisions = [d for d in (approvals or {}).get("decisions", [])
                     if isinstance(d, dict)]
        if value == "awaiting-acceptance" and decisions and \
                not any(d.get("decision") == "approve" for d in decisions):
            return {"value": "waiting-approval",
                    "basis": f"{basis}, and {len(decisions)} recorded approval "
                             f"decision(s), none of them an approval"}
        return {"value": value, "basis": basis}
    return {"value": "in-progress",
            "basis": f"task status `{task_status}` with gate `{gate_status}` — no "
                     f"mapping for this combination, shown as recorded"}


# ── build / verify ───────────────────────────────────────────────────────────
def build_receipt(root: pathlib.Path, task_id: str) -> dict:
    """Project one task's recorded state into a receipt. Reads only; decides nothing."""
    run, task = load_task(root, task_id)
    loaded = {key: _read_json(run / name) for key, name in _SOURCES}
    # Absent sources are recorded with a null digest rather than omitted. A receipt
    # built while a task was still running would otherwise stay `fresh` after `accept`
    # wrote `provenance.json` — the single most material change that can happen to a
    # task, invisible because the receipt never mentioned the file it was waiting for.
    sources = []
    for key, name in _SOURCES:
        f = run / name
        sources.append({"path": str((run / name).relative_to(root)),
                        "sha256": _digest(f) if f.is_file() else None})
    gates = _gates(loaded["acceptance"])
    return {
        "schema": SCHEMA,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "task": {
            "id": task.get("task_id"),
            "type": task.get("task_type"),
            "recipe": task.get("recipe"),
            "input": task.get("input"),
            "status": task.get("status"),
            "created_at": task.get("created_at"),
            "accepted_at": task.get("accepted_at"),
        },
        "target": _target(root, task),
        "producer": _producer(task),
        "verifier": _verifier(loaded["steps"], task),
        "isolation": _isolation(task),
        "gates": gates,
        "approvals": _approvals(loaded["approvals"]),
        "provenance": _provenance(root, loaded["provenance"]),
        "evidence": _evidence(root, run, gates),
        "sources": sources,
        "final_status": _final_status(task, gates, loaded["approvals"]),
    }


def _resolve(root: pathlib.Path, ref: str) -> str | None:
    return _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}") or None


def target_moved(root: pathlib.Path, receipt: dict) -> dict:
    """Does the change this receipt describes still sit where it did when rig looked?

    A digest cannot answer this. `verify` compares file contents, and a ref moving
    changes no file — yet it is the change that matters most to an external caller,
    because the question it asked was "is my branch acceptable?" and the branch may no
    longer be the thing rig looked at (#429).

    Two refs can drift, and they drift for different reasons, so both are checked and
    reported separately:

    * the **producer's ref**, when the import named one instead of a commit. It moves
      when the producing side keeps working after handing rig a branch name.
    * the **task branch**, which rig owns and an operator can commit into. It moves
      when someone adds to the worktree after the import — and this is the more
      dangerous of the two, because it is the branch `accept` squash-merges. Reporting
      only the first would let a receipt name the commit rig was handed while a
      different one is what lands, which is the shape of defect this module exists to
      make impossible.

    Both are measurements, not judgments: refs are re-resolved and SHAs compared. A
    caller who handed rig an immutable commit and left the worktree alone gets
    `moved: False` from two checks that both ran, not from two that were skipped.
    """
    imported = (receipt.get("target") or {}).get("import")
    if not isinstance(imported, dict):
        return {"applicable": False, "moved": False, "checks": [],
                "reason": "this task's change was produced by rig, so there is no "
                          "pinned commit for a ref to drift away from"}
    verified = imported.get("head_commit")
    checks = []

    ref = imported.get("head_ref")
    if imported.get("head_symbolic") and ref:
        now = _resolve(root, ref)
        checks.append({
            "kind": "producer-ref", "applicable": True, "ref": ref, "resolves_to": now,
            "moved": now != verified,
            "reason": "" if now == verified else
                      f"`{ref}` no longer resolves in this repository, so what rig "
                      f"verified can no longer be reached by the name it was given"
                      if now is None else
                      f"`{ref}` now points at {now[:12]}, not at the {str(verified)[:12]} "
                      f"rig verified",
        })
    else:
        checks.append({"kind": "producer-ref", "applicable": False, "moved": False,
                       "ref": None, "resolves_to": None,
                       "reason": "the import named an immutable commit, which cannot move"})

    branch = (receipt.get("target") or {}).get("branch")
    now = _resolve(root, branch) if branch else None
    if branch and now:
        checks.append({
            "kind": "task-branch", "applicable": True, "ref": branch, "resolves_to": now,
            "moved": now != verified,
            "reason": "" if now == verified else
                      f"`{branch}` is at {now[:12]}, not at the {str(verified)[:12]} that "
                      f"was imported — `accept` applies the branch, so what lands is not "
                      f"what this receipt describes",
        })
    else:
        checks.append({
            "kind": "task-branch", "applicable": False, "moved": False, "ref": branch,
            "resolves_to": None,
            "reason": f"`{branch}` no longer resolves — `accept` and `discard` remove the "
                      f"task branch, so there is nothing left to compare"
                      if branch else "this task has no branch to compare against",
        })

    moved = [c for c in checks if c["moved"]]
    return {
        "applicable": any(c["applicable"] for c in checks),
        "moved": bool(moved),
        "verified": verified,
        "checks": checks,
        "reason": " / ".join(c["reason"] for c in moved),
    }


def verify(root: pathlib.Path, receipt: dict) -> dict:
    """Is this receipt still describing the files it was built from?

    Content, not mtime: a file rewritten with identical bytes did not change what the
    receipt says, and a file touched by a checkout did not either.
    """
    if receipt.get("schema") != SCHEMA:
        return {"fresh": False, "reason": f"unknown schema {receipt.get('schema')!r}",
                "changed": [], "missing": []}
    changed, missing = [], []
    for entry in receipt.get("sources", []) + receipt.get("evidence", []):
        rel = entry.get("path")
        if not rel:
            continue
        f = root / rel
        recorded = entry.get("sha256")
        if not f.is_file():
            # A file that was absent when the receipt was built and is still absent is
            # not a change; one that has gone away is.
            if recorded is not None:
                missing.append(rel)
        elif recorded is None or _digest(f) != recorded:
            changed.append(rel)
    # A moved target is kept out of `changed`, which consumers read as a list of file
    # paths — putting a ref name in it would be an unannounced type change in a field
    # other tools already parse.
    moved = target_moved(root, receipt)
    fresh = not changed and not missing and not moved["moved"]
    return {
        "fresh": fresh,
        "changed": changed,
        "missing": missing,
        "target_moved": moved,
        "final_status": receipt.get("final_status", {}).get("value") if fresh else "invalidated",
        "reason": "" if fresh else
                  moved["reason"] if moved["moved"] else
                  "a source this receipt projected has changed since it was built, so "
                  "its final status no longer describes the current record",
    }


# ── rendering ────────────────────────────────────────────────────────────────
def _render_value(block: dict, *keys: str) -> str:
    if not block.get("observed"):
        return f"not recorded — {block.get('reason', '')}"
    shown = " · ".join(f"{k}: {block[k]}" for k in keys if block.get(k) is not None)
    # An observed block that prints as "" would be indistinguishable from an absent
    # one, which is the exact confusion this module exists to prevent.
    return shown or "recorded, but with none of the fields this line renders"


def render_markdown(receipt: dict) -> str:
    """The same model as the JSON, read aloud.

    Rendered from the receipt rather than from the files, so the two cannot drift:
    anything this page can say, a consumer of the JSON can also say.
    """
    t, target = receipt["task"], receipt["target"]
    lines = [
        f"# Assurance Receipt — {t['id']}",
        "",
        f"**{receipt['final_status']['value']}** — {receipt['final_status']['basis']}",
        "",
        f"> {t.get('input') or ''}",
        "",
        "## What was verified",
        "",
        f"- repository: `{target['repository']}`",
        f"- base: `{target['base_branch']}` @ `{(target['base_commit_effective'] or '')[:12]}`",
        f"- head: {_render_value(target['head'], 'commit', 'resolvable', 'source')}",
        f"- immutable target: {'yes' if target['immutable'] else 'no'}",
    ]
    imported = target.get("import")
    if imported:
        lines += [
            f"- imported from `{imported['producer']}` as "
            + (f"`{imported['head_requested']}` → the name resolved to "
               f"`{(imported['head_commit'] or '')[:12]}`, and a name can move"
               if imported["head_symbolic"] else
               f"`{(imported['head_commit'] or '')[:12]}`, an immutable commit"),
            f"- diff summary: {imported['diff_summary']}"
            + (" — restated from the producer's own commit messages, not a review"
               if imported["diff_summary"] == "derived" else ""),
        ]
    lines += [
        "",
        "## Who",
        "",
        f"- producer actor: `{receipt['producer']['actor'] or 'unknown'}`",
        f"- producer harness: {_render_value(receipt['producer']['harness'], 'id', 'source', 'declared')}",
        f"- producer runtime: {_render_value(receipt['producer']['runtime'], 'id', 'declared', 'source')}",
        f"- verifier identity: {_render_value(receipt['verifier']['identity'])}",
        f"- independence: **{receipt['verifier']['independence']['verdict']}** — "
        f"{receipt['verifier']['independence']['basis']}",
    ]
    external = receipt["producer"].get("external")
    if external:
        lines.append(
            f"- external producer: `{external['name']}`"
            + (f" · run `{external['run_id']}`" if external.get("run_id") else "")
            + (f" · <{external['source_url']}>" if external.get("source_url") else "")
            + f" — declared, {external['basis']}")
        for claim in external.get("claims", []):
            lines.append(
                f"  - producer claims `{claim['name']}={claim['value']}` — "
                f"**gate_effect: {claim['gate_effect']}**, recorded next to rig's "
                "verdict and never as part of it")
    lines += [
        "",
        "## Isolation",
        "",
        f"- mode: `{receipt['isolation'].get('mode')}`",
        f"- enforced by: {receipt['isolation'].get('enforced_by') or '—'}",
        f"- {receipt['isolation'].get('note')}",
        "",
        "## Gates",
        "",
    ]
    gates = receipt["gates"]
    if not gates.get("observed"):
        lines.append(f"not evaluated — {gates.get('reason')}")
    else:
        counts = " · ".join(f"{v} {k}" for k, v in sorted(gates["counts"].items()))
        lines += [f"**{gates['status']}** ({counts}) — presets {', '.join(gates['presets'])}", ""]
        lines += ["| criterion | status | note |", "|---|---|---|"]
        for c in gates["criteria"]:
            note = c["detail"].replace("|", "\\|")
            if c.get("overridden"):
                note = f"**overridden** — {note}"
            lines.append(f"| `{c['name']}` | {c['status']} | {note} |")
    lines += ["", "## Approvals", ""]
    approvals = receipt["approvals"]
    if not approvals.get("observed"):
        lines.append(f"none recorded — {approvals.get('reason')}")
    else:
        for d in approvals["decisions"]:
            lines.append(f"- `{d['actor']}` **{d['decision']}** ({', '.join(d['roles']) or 'no role'}) {d['note']}")
    prov = receipt["provenance"]
    lines += ["", "## Provenance", ""]
    if not prov.get("observed"):
        lines.append(f"unsigned — {prov.get('reason')}")
    else:
        verified = {True: "verifies", False: "DOES NOT VERIFY", None: "could not be checked"}[prov["verified"]]
        lines.append(f"- {prov['algorithm']} signature {verified} (`{prov['verify_with']}`)")
        if prov["forced"]:
            lines.append("- **accepted with --force**")
    lines += ["", "## Evidence", ""]
    for e in receipt["evidence"]:
        lines.append(f"- `{e['path']}` — {e['kind']} (`{(e.get('sha256') or '')[:12]}`)")
    if not receipt["evidence"]:
        lines.append("none recorded")
    lines += ["", "---", "",
              f"Projected from {len(receipt['sources'])} recorded source(s) at "
              f"{receipt['generated_at']}. This receipt makes no judgment of its own; "
              "verify it is still current with `workbench.py receipt --verify`.", ""]
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────
def cmd_receipt(args: argparse.Namespace) -> None:
    root = repo_root()
    task_id = resolve_task_id(root, args.task_id)
    run = run_dir(root, task_id)
    path = run / "assurance.json"

    if args.verify:
        stored = _read_json(path)
        if stored is None:
            die(f"no receipt at {path.relative_to(root)} — build one with "
                f"`workbench.py receipt {task_id}`")
        result = verify(root, stored)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"## receipt verify: {task_id}")
            print(f"  {'fresh' if result['fresh'] else 'STALE'} — final status: {result['final_status']}")
            for rel in result["changed"]:
                print(f"  changed since the receipt was built: {rel}")
            for rel in result["missing"]:
                print(f"  missing: {rel}")
            if result["target_moved"]["moved"]:
                print(f"  target moved: {result['target_moved']['reason']}")
            elif not result["fresh"]:
                print(f"  {result['reason']}")
        raise SystemExit(0 if result["fresh"] else 1)

    receipt = build_receipt(root, task_id)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = run / "assurance.md"
    md.write_text(render_markdown(receipt), encoding="utf-8")
    if args.json:
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
    elif args.markdown:
        print(render_markdown(receipt))
    else:
        print(f"## assurance receipt: {task_id}")
        print(f"  {receipt['final_status']['value']} — {receipt['final_status']['basis']}")
        print(f"  {path.relative_to(root)}")
        print(f"  {md.relative_to(root)}")
