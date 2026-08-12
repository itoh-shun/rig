"""Atomic trusted execution of all approved cases affected by a diff."""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile

from .affected import analyze_affected, prompt_surface_digests
from .cases import EvalCaseError
# Where this run files what it measured. Defined by the gate rather than here,
# because the gate now reads that path literally to decide what a measurement has
# to beat: one writer and one ratchet, both naming the same constant.
from .gate import EVIDENCE_REL, evaluate_gate
from .runner import make_judge_adapter, run_case


def _rev_parse(root: pathlib.Path, revision: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=root,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvalCaseError("cannot resolve affected-run revision") from exc
    value = completed.stdout.strip()
    if completed.returncode != 0 or len(value) != 40:
        raise EvalCaseError("cannot resolve affected-run revision")
    return value


def _dirty_paths(root: pathlib.Path) -> list[str]:
    """Working-tree entries that would make the measurement unreproducible.

    The signed diff is taken tree-to-tree at the resolved head, so anything
    uncommitted is measured but not described — the gate would later recompute a
    different hash and report a mismatch nobody can explain. Evidence from an
    earlier run is exempt: it is the output of this command, not an input to it.
    """
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "-z"], cwd=root, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvalCaseError("cannot read working tree status") from exc
    if completed.returncode != 0:
        raise EvalCaseError("cannot read working tree status")
    entries = [entry for entry in completed.stdout.split("\0") if entry]
    dirty: list[str] = []
    index = 0
    while index < len(entries):
        status, path = entries[index][:2], entries[index][3:]
        # A rename or copy spends a second NUL-separated field on its origin path;
        # skipping it keeps the origin from being read as a status line of its own.
        index += 2 if status[0] in {"R", "C"} else 1
        if path and path != EVIDENCE_REL and not path.startswith(EVIDENCE_REL + "/"):
            dirty.append(path)
    return sorted(dirty)


def run_affected(
    repo: pathlib.Path | str, *, base: str, head: str, provider: str, model: str,
    judge_provider: str, judge_model: str, provider_command: str | None = None,
    judge_command: str | None = None, timeout_s: float = 30, ratchet: bool = False,
) -> tuple[dict, int, pathlib.Path | None]:
    """`ratchet` has to reach here too, or the CI gate's ratchet buys nothing.

    A change that touches one covered surface next to a surface nobody has written
    a case for yet is the ordinary shape in this repository — two prompt surfaces
    are covered and ~198 are not. Strict, this refuses to measure it at all, so the
    maintainer cannot produce the evidence the gate then reports as
    `evidence_absent`: the two ends of the same workflow would disagree about
    whether a case-less surface is fatal, and the PR would be unpassable from both.
    The evidence written is identical in either mode; only the coverage question
    changes.
    """
    if provider == "mock" or judge_provider == "mock":
        raise EvalCaseError("affected-run forbids mock provider and mock judge")
    root = pathlib.Path(repo).resolve()
    affected = analyze_affected(root, base=base, head=head,
                                require_cases=not ratchet, ratchet=ratchet)
    if affected["status"] == "noop":
        report, code = evaluate_gate(root, base=base, head=head,
                                     evidence_dir=root / ".rig" / "none", ratchet=ratchet)
        return report, code, None
    if affected["status"] == "uncovered":
        return ({"eval_gate_schema_version": 1, "status": "failed", "base": base,
                 "head": head, "resolved_head": affected["resolved_head"],
                 "cases": affected["affected_cases"],
                 "failures": [f"uncovered:{item}" for item in affected["uncovered"]]}, 1, None)
    if not affected["affected_cases"]:
        # Reachable only under `ratchet`: every surface this change touched is still
        # debt, so there is nothing to measure. Same answer as `noop` rather than an
        # empty run that would report a destination holding no evidence.
        report, code = evaluate_gate(root, base=base, head=head,
                                     evidence_dir=root / ".rig" / "none", ratchet=ratchet)
        return report, code, None
    cases: dict[str, dict] = {}
    for case_id in affected["affected_cases"]:
        path = root / "evals" / "cases" / case_id / "case.json"
        try:
            cases[case_id] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvalCaseError("affected-run cannot read approved case") from exc
    # The provider only ever sees the checked-out tree, so evidence describing a
    # different head would claim a tree nobody measured — and, since the gate now
    # recomputes the diff at the commit the evidence names, that claim would verify.
    checked_out = _rev_parse(root, "HEAD")
    if affected["resolved_head"] != checked_out:
        raise EvalCaseError(
            f"affected-run measures the checked-out tree; --head resolves to "
            f"{affected['resolved_head'][:12]} but HEAD is {checked_out[:12]}"
        )
    dirty = _dirty_paths(root)
    if dirty:
        raise EvalCaseError(
            "affected-run requires a clean working tree; uncommitted: "
            + ", ".join(dirty[:5]) + (" …" if len(dirty) > 5 else "")
        )
    # Staged outside the destination so a multi-case run cannot hash the evidence
    # of its own earlier case into the identity of its later one, and so a run that
    # fails partway leaves the committed evidence untouched. (The older reason —
    # that `execution_diff_sha256` skips this prefix when framing untracked input —
    # no longer applies: `execution_head` is always a resolved commit here, so the
    # untracked framing never runs.)
    staging_parent = root / ".rig" / "evals" / "results"
    staging_parent.mkdir(parents=True, exist_ok=True)
    destination = root / EVIDENCE_REL
    staging = pathlib.Path(tempfile.mkdtemp(prefix=".affected-run.", dir=staging_parent))
    try:
        judge = make_judge_adapter(
            provider=judge_provider, model=judge_model, repo=root,
            command=judge_command, timeout_s=timeout_s,
        )
        produced: dict[str, pathlib.Path] = {}
        # Taken once, at the commit every case is measured at, and signed into each
        # result: this is what lets the gate ask "has the prompt this was measured
        # against moved?" from content rather than from ancestry, and content is the
        # only form of that question a squash or rebase merge leaves answerable.
        digests = prompt_surface_digests(root, affected["resolved_head"])
        for case_id in sorted(cases):
            path, result = run_case(
                cases[case_id], repo=root, provider=provider, model=model,
                repeat=cases[case_id]["repeat"], phase="current",
                command=provider_command, timeout_s=timeout_s, judge_adapter=judge,
                execution_base=base, execution_head=affected["resolved_head"],
                result_root=staging, prompt_surface_digests=digests,
            )
            produced[case_id] = path
            if any(row["infra_status"] is not None
                   for row in [*result["target"], *result["clean"]]):
                return ({"eval_gate_schema_version": 1, "status": "infra_error",
                         "base": base, "head": head,
                         "resolved_head": affected["resolved_head"], "cases": sorted(cases),
                         "failures": [f"provider_unavailable:{case_id}"]}, 2, None)
            if any(row["judge"]["status"] == "error"
                   for row in [*result["target"], *result["clean"]]):
                return ({"eval_gate_schema_version": 1, "status": "infra_error",
                         "base": base, "head": head,
                         "resolved_head": affected["resolved_head"], "cases": sorted(cases),
                         "failures": [f"judge_unavailable:{case_id}"]}, 2, None)
            if any(row["outcome"] != "pass"
                   for row in [*result["target"], *result["clean"]]):
                return ({"eval_gate_schema_version": 1, "status": "failed",
                         "base": base, "head": head,
                         "resolved_head": affected["resolved_head"], "cases": sorted(cases),
                         "failures": [f"quality_not_green:{case_id}"]}, 1, None)
        report, code = evaluate_gate(
            root, base=base, head=head, evidence_dir=staging, provider=provider,
            model=model, judge_provider=judge_provider, judge_model=judge_model,
            ratchet=ratchet,
        )
        if code != 0:
            return report, code, None
        # One `os.replace` per case rather than one for the whole directory, which
        # the `<case-id>/current.json` layout costs: a failure midway leaves some
        # cases updated and some not. Each result is bound to its own measurement,
        # so a half-applied run fails closed on the cases that did not move; it is
        # not silently accepted.
        for case_id, path in produced.items():
            final = destination / case_id / "current.json"
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, final)
        return report, 0, destination
    finally:
        if staging != pathlib.Path() and staging.exists():
            shutil.rmtree(staging)
