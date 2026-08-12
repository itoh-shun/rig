"""Machine evaluation gate for affected prompt surfaces."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import posixpath
import subprocess

from rig_workbench import __version__

from .affected import analyze_affected
from .cases import EvalCaseError, canonical_json, evaluation_spec_hash, validate_case
from .compare import validate_result
from .execution import execution_diff_sha256

# Where a measurement lands once it is committed. `evals/` is where the cases and
# the surface registry already live and is not a prompt-surface root, so evidence
# landing here adds nothing to the gate's own field of view. One file per case,
# overwritten: `_evidence_index` collects every `*.json` under the tree whose
# `case_id` matches, and a second `current` result for the same case is
# `current_evidence_count`, so an accumulating layout breaks the gate the first
# time a case is measured twice.
#
# The ratchet reads this path as a **literal** rather than deriving it from
# `--evidence-dir`, and that is the whole of the fix for the relocation bypass:
# a derived path is an attacker input, and pointing `evals/evidence` at a
# directory with no history was enough to make the comparison come back empty.
# `--evidence-dir` still says where the evidence being judged is *read* from —
# `affected-run` gates its own staging directory with it — but what that evidence
# has to beat is fixed by the repository's layout, not by the argument.
EVIDENCE_REL = "evals/evidence"


def _git_ok(root: pathlib.Path, argv: list[str]) -> bool:
    try:
        completed = subprocess.run(
            ["git", *argv], cwd=root, capture_output=True, timeout=10, shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _resolve_commit(root: pathlib.Path, revision: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", revision], cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvalCaseError("cannot resolve evaluation gate revision") from exc
    value = completed.stdout.strip()
    if completed.returncode != 0 or len(value) != 40:
        raise EvalCaseError("cannot resolve evaluation gate revision")
    return value


def _cases(root: pathlib.Path) -> dict[str, dict]:
    loaded: dict[str, dict] = {}
    tier = root / "evals" / "cases"
    if tier.is_dir():
        for path in sorted(tier.glob("*/case.json")):
            try:
                raw = path.read_text(encoding="utf-8")
                value = json.loads(raw)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise EvalCaseError("cannot read evaluation gate case") from exc
            validate_case(value)
            if value["status"] != "approved" or raw != canonical_json(value):
                raise EvalCaseError("evaluation gate case must be approved canonical JSON")
            if value["id"] in loaded:
                raise EvalCaseError("duplicate evaluation gate case id")
            loaded[value["id"]] = value
    return loaded


def _evidence_index(evidence_root: pathlib.Path) -> dict[str, list[dict]]:
    """case id → its evidence, reading the tree once.

    `<case-id>/current.json` was a convention held up only by the side that writes
    it: matching on the `case_id` field alone meant the directory name was
    decoration, and case A's result filed under `evals/evidence/B/` verified. The
    directory now has to agree, which is the rule `packs/evidence.py` already
    enforces for its own `evals/results/<case-id>/` layout. A file directly in the
    root keeps matching on the field, because that is the shape `affected-run`
    stages and gates before it files anything.

    Read once rather than per case: the previous form re-walked and re-parsed the
    whole tree for every affected case.
    """
    index: dict[str, list[dict]] = {}
    if not evidence_root.is_dir():
        return index
    for path in sorted(evidence_root.rglob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            # Named, because "evidence is malformed" with a tree to search is a
            # worse report than the exception it came from.
            raise EvalCaseError(
                f"evaluation gate evidence is malformed: {path.name}"
            ) from exc
        if not isinstance(value, dict) or not isinstance(value.get("case_id"), str):
            continue
        parent = path.parent
        if parent != evidence_root and parent.name != value["case_id"]:
            continue
        index.setdefault(value["case_id"], []).append(value)
    return index


def _evidence_symlinks(evidence_root: pathlib.Path, resolved_head: str,
                       root: pathlib.Path) -> list[str]:
    """Links at or under the evidence tree, which this gate does not accept.

    Two readers, two answers, and the gap between them was a bypass: evidence is
    read off the **filesystem** (`_evidence_index` walks it), while the ratchet
    reads the **tree** at a commit. Committing `evals/evidence` as a link to
    another directory let the first reader follow it to blobs the second one was
    never looking at, and the ratchet then had nothing to compare against.

    Fixing the ratchet's path (`EVIDENCE_REL`) closes that on its own. The shape
    is refused as well because it is not one anything here writes, and because a
    link is the one entry whose *content* is a path — read it and the gate is
    judging a file chosen by name resolution rather than by the layout it gates.
    Both readers are checked: the walk covers what is read, including the
    uncommitted `head="working"` form, and the tree scan covers what would land.

    Only the evidence tree itself is examined. Parent directories are not: a repo
    checked out under a symlinked path (`/tmp` on macOS, any `tmp_path` fixture)
    is ordinary and has nothing to do with where evidence points.

    The walk is the load-bearing half and has no way to abstain — it reads the
    same directory entries the evidence itself is read from. The tree scan is
    defence in depth over what would land, and a git that will not answer it
    leaves the walk's answer standing rather than becoming a failure of its own:
    it cannot hide a link the gate actually read.
    """
    def name(path: pathlib.Path) -> str:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return path.as_posix()

    found: set[str] = set()
    if evidence_root.is_symlink():
        found.add(name(evidence_root))
    elif evidence_root.is_dir():
        for parent, dirnames, filenames in os.walk(evidence_root, followlinks=False):
            for entry in [*dirnames, *filenames]:
                path = pathlib.Path(parent) / entry
                if path.is_symlink():
                    found.add(name(path))
    try:
        listing = subprocess.run(
            ["git", "ls-tree", "-r", "-z", resolved_head, "--", EVIDENCE_REL],
            cwd=root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return sorted(found)
    if listing.returncode != 0:
        return sorted(found)
    for entry in listing.stdout.split("\0"):
        # `<mode> <type> <object>\t<path>`; 120000 is git's mode for a symlink.
        if entry.startswith("120000 ") and "\t" in entry:
            found.add(entry.split("\t", 1)[1])
    return sorted(found)


def quality_result_failures(
    result: dict, case: dict, *, expected_commit: str | None = None,
    expected_base: str | None = None, expected_diff: str | None = None,
    provider: str | None = None, model: str | None = None,
    judge_provider: str | None = None, judge_model: str | None = None,
    verify_attestation: bool = True,
) -> list[str]:
    """Canonical attested-current quality policy for eval gates and packs."""
    validate_case(case)
    validate_result(result, verify_attestation=verify_attestation)
    case_id = case["id"]
    failures: list[str] = []
    policy = case["provider_policy"]
    if result["provider"] == "mock":
        failures.append(f"mock_evidence_forbidden:{case_id}")
    if policy["mode"] == "allowlist" and result["provider"] not in policy["allowed"]:
        failures.append(f"provider_policy:{case_id}")
    if policy.get("models") and result["model"] not in policy["models"]:
        failures.append(f"model_policy:{case_id}")
    if policy.get("judge_providers") and result["judge_provider"] not in policy["judge_providers"]:
        failures.append(f"judge_provider_policy:{case_id}")
    if policy.get("judge_models") and result["judge_model"] not in policy["judge_models"]:
        failures.append(f"judge_model_policy:{case_id}")
    if provider is not None and result["provider"] != provider:
        failures.append(f"provider_mismatch:{case_id}")
    if model is not None and result["model"] != model:
        failures.append(f"model_mismatch:{case_id}")
    if result["judge_provider"] == "mock":
        failures.append(f"mock_judge_forbidden:{case_id}")
    if judge_provider is not None and result["judge_provider"] != judge_provider:
        failures.append(f"judge_provider_mismatch:{case_id}")
    if judge_model is not None and result["judge_model"] != judge_model:
        failures.append(f"judge_model_mismatch:{case_id}")
    if result["executor_version"] != __version__:
        failures.append(f"executor_version_mismatch:{case_id}")
    if result["judge_executor_version"] != __version__:
        failures.append(f"judge_executor_version_mismatch:{case_id}")
    if result["case_id"] != case_id or result["case_hash"] != evaluation_spec_hash(case):
        failures.append(f"case_hash_mismatch:{case_id}")
    if result["execution_status"] != "available":
        failures.append(f"execution_identity_unavailable:{case_id}")
    if expected_commit is not None and result["execution_commit"] != expected_commit:
        failures.append(f"execution_commit_mismatch:{case_id}")
    if expected_base is not None and result["execution_base_commit"] != expected_base:
        failures.append(f"execution_base_mismatch:{case_id}")
    if expected_diff is not None and result["execution_diff_sha256"] != expected_diff:
        failures.append(f"execution_diff_mismatch:{case_id}")
    if result["phase"] != "current" or result["repeat"] != case["repeat"]:
        failures.append(f"result_phase_or_repeat:{case_id}")
    if any(row["outcome"] != "pass" or row["infra_status"] is not None
           for row in [*result["target"], *result["clean"]]):
        failures.append(f"quality_not_green:{case_id}")
    if case["semantic_rubric"]:
        expected = [item["id"] for item in case["semantic_rubric"]]
        if result["judge"] != {"required": True, "status": "measured"}:
            failures.append(f"judge_unmeasured:{case_id}")
        for row in [*result["target"], *result["clean"]]:
            criteria = row["judge"]["criteria"]
            if (row["judge"]["status"] != "measured"
                    or [item["id"] for item in criteria] != expected
                    or any(item["status"] != "pass" for item in criteria)):
                failures.append(f"semantic_criteria_failed:{case_id}")
                break
    return sorted(set(failures))


def _head_digest(root: pathlib.Path, path: str, *, resolved_head: str, head: str) -> str | None:
    """The object id this path has at the head being gated, or None if absent.

    The working-tree form hashes the file on disk rather than reading the tree, so
    an uncommitted prompt edit made after signing is seen. `--path` applies the
    same attribute-driven filters git would apply on the way in, so the id is
    comparable with one that came out of `ls-tree`.
    """
    if head == "working":
        candidate = root / path
        if not candidate.is_file():
            return None
        argv = ["hash-object", f"--path={path}", "--", str(candidate)]
    else:
        argv = ["rev-parse", "--verify", "--quiet", f"{resolved_head}:{path}"]
    try:
        completed = subprocess.run(
            ["git", *argv], cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        return None
    return value


def _evidence_identity_failures(
    root: pathlib.Path, result: dict, case_id: str, *, resolved_head: str, head: str,
    affected_surfaces: set[str],
) -> list[str]:
    """Bind evidence to the prompt content it measured, not to a commit id.

    Evidence that lives in the repository can never claim `execution_commit ==
    HEAD`: committing the file makes a new HEAD, so the claim is false the instant
    the evidence is tracked. The obvious repair — require the measured commit to be
    HEAD's **ancestor** — holds for a merge commit and fails for the other two
    merge buttons this repository has enabled. Squash and rebase both rewrite the
    branch, so the measured commit is either gone from the history or no longer an
    ancestor of anything, and the evidence that travelled in with the merge is
    refused *after* landing: the PR is green, the push to the default branch is
    red, and nobody could have seen it coming. That is #402's shape with a longer
    fuse, and it is why ancestry is not the binding here.

    What a squash preserves exactly is content. So the measurement signs the object
    id of every prompt surface in the tree it measured, and the question becomes:
    does every surface **this change is accountable for** still hold the content
    that was measured? Intersecting with the affected set, rather than comparing
    the whole map, is what keeps a merge legal — another PR's persona landing on
    the base branch is not this change's to answer for and was gated on its own PR.
    A surface the author edits after measuring is in both sets and fails.

    Recording the entire surface set rather than only the affected part is what
    makes a *missing* entry meaningful: a path the gate holds accountable and the
    measurement never saw is a file created after the measurement, and fails.

    The `(base, measured)` diff is still recomputed, as a provenance check on the
    evidence's own account of itself, whenever history still holds both commits.
    After a squash it does not, and this check is skipped — which is safe only
    because it was never the binding: content is, and the evidence ratchet in
    `_evidence_ratchet_failures` is what stops an old signed measurement being
    replayed onto matching content.

    Known limit: comparison is per-file content, so a surface edited after the
    measurement and restored byte-for-byte passes. That is not a hole — the tree
    being gated is then the tree that was measured. What genuinely escapes is
    everything outside the surface registry: prompt-composition code under
    `rig_workbench/`, `scripts/`, or `skills/engine/corpora/` can change after a
    measurement without invalidating it. The registry is this gate's declared
    field of view and always was; the older whole-tree diff bound more only as a
    side effect.
    """
    if result["execution_status"] != "available":
        return []                      # already `execution_identity_unavailable`
    recorded = result.get("prompt_surface_digests")
    if not isinstance(recorded, dict):
        # No content binding at all: measured outside a repository, or by a version
        # that predates the map. Nothing here can be checked, so nothing passes.
        return [f"execution_digests_absent:{case_id}"]
    failures: list[str] = []
    for path in sorted(affected_surfaces):
        if _head_digest(root, path, resolved_head=resolved_head, head=head) != recorded.get(path):
            failures.append(f"execution_prompt_surface_changed:{case_id}:{path}")
    measured = result["execution_commit"]
    measured_base = result["execution_base_commit"]
    if not _git_ok(root, ["rev-parse", "--verify", "--quiet", f"{measured}^{{commit}}"]):
        return failures                # squashed or rebased away; content decides
    if not _git_ok(root, ["rev-parse", "--verify", "--quiet",
                          f"{measured_base}^{{commit}}"]):
        return [*failures, f"execution_base_unreachable:{case_id}"]
    try:
        recomputed = execution_diff_sha256(root, base=measured_base, head=measured)
    except EvalCaseError:
        return [*failures, f"execution_base_unreachable:{case_id}"]
    if recomputed != result["execution_diff_sha256"]:
        failures.append(f"execution_diff_mismatch:{case_id}")
    return failures


def _evidence_ratchet_failures(
    result: dict, case_id: str, *,
    prior: dict[str, tuple[dt.datetime, str]] | None,
) -> list[str]:
    """Refuse evidence older than the evidence the base branch already holds.

    Everything else here is a statement about *some* trusted measurement. Without
    this, an attacker who holds no key at all can open a PR that re-applies a
    prompt a human already reverted and restores, byte for byte, the signed
    evidence blob that measured it — both are public in the git history. Every
    other check passes by construction: the signature is genuine, the measured
    commit really is an ancestor, and the content matches because it is the same
    content. Write access to a branch would be enough to land a prompt nobody
    re-measured, which is exactly what signing evidence was supposed to prevent.

    So evidence ratchets like everything else in this module: for a given case it
    may only move forward. `started_at` is the ordering, and it is inside the
    signed payload, so moving it is forgery rather than editing.

    `started_at` also survives what ancestry does not: a squash or rebase carries
    the timestamp through unchanged, which is why the ratchet is written on the
    timestamp and not on the measured commit's descent.

    The comparison point is the **base branch's tip**, not the fork point. The
    fork point was chosen to keep two concurrent PRs legal, and it cost the whole
    check: where a branch forks from is the author's choice, so branching from
    before a case was ever measured left nothing to compare against and the
    ratchet fell silent.

    "Tip" has to mean the tip at the time of the check, and that is a demand on
    the caller rather than a property of `base`. `github.event.pull_request.base.sha`
    is the base branch as the event saw it, and an author who opens the PR before
    a revert lands pins it to the commit that still carried the reverted prompt.
    They cannot choose an arbitrary value, but a pin is all a replay needs. So CI
    resolves `refs/remotes/origin/<base branch>` itself on a PR and passes
    `github.event.before` on a push — that one *is* a tip, chosen by the server at
    push time — and a lower bound taken at either is one the branch cannot move.
    See `.github/workflows/validate.yml`; the two paths are pinned by
    `tests/test_eval_workflow_contract.py`, and what a pinned base costs is pinned
    by `test_the_replay_is_refused_at_the_base_tip_and_invisible_at_a_pinned_snapshot`.

    A pinned base is worse than a silent ratchet, which is why it is a workflow
    concern and not one this function can defend against. The affected set diffs
    from `merge-base(base, head)`, so a head restored to the pinned commit's
    content carries no prompt surface in its diff: no case is selected, and
    `evaluate_gate` returns at `noop` before it reaches this function at all.

    Taking the tip rather than the largest `started_at` anywhere in the base
    branch's history is deliberate: for history to hold a *newer* measurement than
    the tip, evidence on the base branch must have moved backwards, which needs
    either a merge resolved to the older side or a push that bypassed this gate —
    and the push event runs this same check with `before` as its base, so the
    branch goes red when it happens rather than quietly lowering the bound. The
    walk it saves is one `git show` per evidence file per re-measurement, forever.

    The cost is stated rather than hidden. Two PRs whose surfaces are covered by
    the same case, where the second measured before the first landed, are told to
    measure again — the base branch holds a newer measurement of that case than
    the one they are carrying. That is a tightening of the intersection rule,
    which would have let the second through on the grounds that its neighbour was
    gated on its own PR. It is the same demand the 30-day expiry already makes, it
    names what to do, and it is *already* what those two PRs owe each other: both
    write `evals/evidence/<case-id>/current.json`, so git refuses to merge the
    second one without a human resolving that file by hand. What the base tip
    changes is only *when* they are told, and by how much: the fork point told
    them on the push that resolved that conflict, and the base tip tells them on
    the branch's next CI run — its next push, or a re-run, which now re-reads the
    tip rather than replaying a snapshot. Both are before the merge button. The
    gain is a shorter gap, not a new guarantee.

    A question that could not be answered is now an accusation
    (`evidence_ratchet_unavailable`) rather than a shrug. This is not the stance
    `_coverage_at` and `_registry_at` take, and the difference is what the check
    is for: coverage monotonicity is one guard among several, while this is the
    only thing standing between someone with no key at all and evidence that
    looks current. Every other check passes a replay by construction. It costs no
    false positives, either: this runs only where a valid current result for the
    case exists, and a case with no evidence at the head is already
    `evidence_absent`.

    `prior` being empty *for this case* is an answer rather than a failure to
    answer: the base branch holds no measurement of it, so there is nothing to
    move backwards from. That is the state this repository ships in — no evidence
    is committed yet — and a case is protected from the second measurement of it
    that lands on the base branch onwards.
    """
    if prior is None:
        return [f"evidence_ratchet_unavailable:{case_id}"]
    earlier = prior.get(case_id)
    if earlier is None:
        return []                      # no measurement to go backwards from
    current = _started_at(result)
    if current is None:
        return [f"evidence_ratchet_unavailable:{case_id}"]
    # Named like the module's other monotonic failures (`coverage_regression`,
    # `registry_narrowed`) because it is the same rule: evidence only moves
    # forward. Re-measuring is the answer in both the honest and the hostile
    # reading of it.
    #
    # Equal timestamps are decided by identity rather than allowed through. The
    # ordering has microsecond resolution, so a tie means the same measurement —
    # unless it does not, and then it is a substitution the ordering cannot see.
    started, digest = earlier
    if current < started or (current == started
                             and str(result.get("result_sha256")) != digest):
        return [f"evidence_regression:{case_id}"]
    return []


def _started_at(result: dict) -> dt.datetime | None:
    try:
        value = dt.datetime.fromisoformat(str(result["started_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    return value if value.tzinfo is not None else None


def _base_evidence(
    root: pathlib.Path, revision: str,
) -> dict[str, tuple[dt.datetime, str]] | None:
    """case id → (when the evidence at `revision` was measured, its identity).

    Read once for every gated case rather than once per case, which is the same
    correction `_evidence_index` needed: the tree is the same tree each time.

    None means the question could not be answered — a git that would not answer,
    or a repository whose objects are not all present (a blobless clone reaches
    this). The caller turns that into a named failure rather than a pass.

    The directory a result is filed under has to be its case here too, the rule
    `_evidence_index` applies at the head. Nothing an author writes reaches this
    revision, so the asymmetry was harmless; it was also an invitation to file a
    result under a case it is not, on the one side that would not have noticed.
    """
    try:
        listing = subprocess.run(
            ["git", "ls-tree", "-r", "-z", "--name-only", revision, "--", EVIDENCE_REL],
            cwd=root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listing.returncode != 0:
        return None
    found: dict[str, tuple[dt.datetime, str]] = {}
    for path in listing.stdout.split("\0"):
        if not path.endswith(".json"):
            continue
        try:
            blob = subprocess.run(
                ["git", "show", f"{revision}:{path}"], cwd=root, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=15, shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if blob.returncode != 0:
            return None
        try:
            value = json.loads(blob.stdout)
        except json.JSONDecodeError:
            continue      # malformed evidence on the base branch is not this change's fault
        if not isinstance(value, dict) or not isinstance(value.get("case_id"), str):
            continue
        started = _started_at(value)
        if started is None:
            continue
        case_id = value["case_id"]
        parent = posixpath.dirname(path)
        if parent != EVIDENCE_REL and posixpath.basename(parent) != case_id:
            continue
        entry = (started, str(value.get("result_sha256")))
        if case_id not in found or entry > found[case_id]:
            found[case_id] = entry
    return found


def evaluate_gate(
    repo: pathlib.Path | str, *, base: str, head: str = "working",
    evidence_dir: pathlib.Path | str, provider: str | None = None,
    model: str | None = None, judge_provider: str | None = None,
    judge_model: str | None = None, ratchet: bool = False,
) -> tuple[dict, int]:
    """`ratchet` is the same direction CI drives with `eval affected --ratchet`.

    Off, this is the strict form: every affected surface must already have a case
    or the change is `uncovered`. On, it delegates the classification to
    `analyze_affected` in exactly the argument shape the CLI uses, so a surface
    nobody has written a case for yet comes back as `debt` — reported, exit 0 —
    while removing coverage, an unregistered surface kind, or a narrowed registry
    stay fatal. Nothing about the evidence checks below changes: the cases that
    *do* exist are still evaluated in either mode.

    The evidence itself is judged against the commit it was measured at rather
    than against `base`/`head` — see `_evidence_identity_failures`. `base` and
    `head` decide only *which* cases are affected.
    """
    root = pathlib.Path(repo).resolve()
    affected = analyze_affected(
        root, base=base, head=head, require_cases=not ratchet, ratchet=ratchet,
        evidence_dir=evidence_dir,
    )
    debt = affected["coverage_debt"]
    if affected["status"] == "noop":
        return ({"eval_gate_schema_version": 1, "status": "noop", "base": base,
                 "head": head, "resolved_head": affected["resolved_head"],
                 "cases": [], "coverage_debt": debt, "failures": []}, 0)
    if affected["status"] == "uncovered":
        # Regressions and registry narrowings reach `uncovered` on their own,
        # without ever landing in `affected["uncovered"]` — a deleted case touches
        # no surface, and the registry is explicitly not one. Reported as their own
        # failures because listing only the paths left this branch failing with an
        # empty `failures` and no way to see why.
        return ({"eval_gate_schema_version": 1, "status": "failed", "base": base,
                 "head": head, "resolved_head": affected["resolved_head"],
                 "cases": affected["affected_cases"], "coverage_debt": debt,
                 "failures": [f"uncovered:{path}" for path in affected["uncovered"]]
                 + [f"coverage_regression:{item}"
                    for item in affected["coverage_regressions"]]
                 + [f"registry_narrowed:{item}"
                    for item in affected["registry_narrowings"]]}, 1)
    resolved_head = _resolve_commit(root, "HEAD" if head == "working" else head)
    cases = _cases(root)
    failures: list[str] = []
    infra: list[str] = []
    evidence_root = pathlib.Path(evidence_dir)
    evidence_index = _evidence_index(evidence_root)
    affected_surfaces = {item["path"] for item in affected["affected_surfaces"]}
    failures.extend(f"evidence_symlink:{item}"
                    for item in _evidence_symlinks(evidence_root, resolved_head, root))
    # Read at `base`, once for every case rather than once per case. `base` is
    # resolved rather than passed through so the revision handed to git is a
    # commit id this repository produced, and so a base that cannot be resolved is
    # an error instead of an unanswerable comparison. Resolution accepts a
    # symbolic ref, which is what lets CI hand this `origin/<base branch>` and get
    # the tip as it stands now — see `_evidence_ratchet_failures` for why the
    # caller has to supply a tip rather than a remembered one.
    prior = (_base_evidence(root, _resolve_commit(root, base))
             if affected["affected_cases"] else {})
    for case_id in affected["affected_cases"]:
        case = cases.get(case_id)
        if case is None:
            failures.append(f"case_absent:{case_id}")
            continue
        candidates = evidence_index.get(case_id, [])
        if not candidates:
            failures.append(f"evidence_absent:{case_id}")
            continue
        valid: list[dict] = []
        for result in candidates:
            try:
                validate_result(result)
            except EvalCaseError as exc:
                infra.append(f"invalid_evidence:{case_id}:{exc}")
                continue
            valid.append(result)
        if not valid:
            # Every candidate already said why it was rejected. Adding "there is
            # not exactly one current result" on top of that reads as a second,
            # different problem — the shape stale evidence took, where an expiry
            # was reported as an infrastructure fault plus a miscount.
            continue
        matching = [result for result in valid if result["phase"] == "current"]
        if len(matching) != 1:
            failures.append(f"current_evidence_count:{case_id}")
            continue
        result = matching[0]
        quality = quality_result_failures(
            result, case, provider=provider, model=model,
            judge_provider=judge_provider, judge_model=judge_model,
        )
        identity = _evidence_identity_failures(
            root, result, case_id, resolved_head=resolved_head, head=head,
            affected_surfaces=affected_surfaces,
        )
        identity.extend(_evidence_ratchet_failures(result, case_id, prior=prior))
        if any(item.startswith(("execution_", "executor_", "judge_executor_"))
               for item in [*quality, *identity]):
            failures.append(f"execution_identity_mismatch:{case_id}")
        failures.extend(quality)
        failures.extend(identity)
    status = "pass" if not failures and not infra else ("infra_error" if infra else "failed")
    exit_code = 0 if status == "pass" else (2 if infra else 1)
    if status == "pass" and debt:
        # Its own status rather than folded into `pass`, matching `eval affected`:
        # the run proceeds, and the number stays visible so paying it down is
        # progress rather than a silence that reads as coverage.
        status = "debt"
    return ({
        "eval_gate_schema_version": 1, "status": status, "base": base, "head": head,
        "resolved_head": affected["resolved_head"], "cases": affected["affected_cases"],
        "coverage_debt": debt, "failures": sorted(failures + infra),
    }, exit_code)
