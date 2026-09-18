"""Pure, fail-closed evaluation of machine check evidence.

The caller must supply a trusted, fixed context and required-check contract, and
obtain evidence through an independently enforced write boundary. These types do
NOT authenticate evidence, capture snapshots, inspect environments, or detect
changes during a check. Digests are compared, never computed or verified here.
This module is not wired to the runner or acceptance boundary yet.

Malformed typed values and contracts raise ValueError. Malformed evidence
collections/entries produce a rejected decision. No AI verdict is accepted.
"""

from dataclasses import dataclass
import re


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _identifier(value: object, name: str) -> None:
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError(f"{name} must be a nonempty string without edge whitespace")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} must not contain control characters")


def _digest(value: object, name: str) -> None:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


@dataclass(frozen=True)
class EvidenceContext:
    """Caller-trusted identity of one immutable verification attempt."""

    run_id: str
    unit_id: str
    attempt_id: int
    subject_digest: str
    baseline_digest: str
    environment_digest: str

    def __post_init__(self) -> None:
        _identifier(self.run_id, "run_id")
        _identifier(self.unit_id, "unit_id")
        if type(self.attempt_id) is not int or self.attempt_id < 1:
            raise ValueError("attempt_id must be a positive integer")
        for name in ("subject_digest", "baseline_digest", "environment_digest"):
            _digest(getattr(self, name), name)


@dataclass(frozen=True)
class RequiredCheck:
    """A required check from the caller's fixed verification contract."""

    check_id: str
    definition_digest: str

    def __post_init__(self) -> None:
        _identifier(self.check_id, "check_id")
        _digest(self.definition_digest, "definition_digest")


@dataclass(frozen=True)
class CheckEvidence:
    """One check observation; constructing it conveys no provenance guarantee."""

    context: EvidenceContext
    check_id: str
    definition_digest: str
    status: str
    exit_code: int | None
    artifact_digest: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.context) is not EvidenceContext:
            raise ValueError("context must be an EvidenceContext")
        _identifier(self.check_id, "check_id")
        _digest(self.definition_digest, "definition_digest")
        _digest(self.artifact_digest, "artifact_digest")
        if type(self.status) is not str or self.status not in ("PASS", "FAIL", "UNKNOWN"):
            raise ValueError("status must be PASS, FAIL, or UNKNOWN")
        if self.exit_code is not None and type(self.exit_code) is not int:
            raise ValueError("exit_code must be an integer or None")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported evidence schema_version")


@dataclass(frozen=True, order=True)
class GateIssue:
    """Stable machine-readable rejection reason; empty check_id means global."""

    code: str
    check_id: str = ""


@dataclass(frozen=True)
class GateDecision:
    """Canonical result; issues are sorted and deduplicated."""

    issues: tuple[GateIssue, ...]

    @property
    def passed(self) -> bool:
        return not self.issues


def evaluate_gate(
    context: EvidenceContext,
    required_checks: tuple[RequiredCheck, ...],
    evidence: tuple[CheckEvidence, ...],
) -> GateDecision:
    """Pass only exact, complete, current successful machine observations.

    Contract mistakes raise ValueError, including empty or duplicate required
    checks. Evidence mistakes reject; unknown checks are not ignored. Results
    are independent of tuple order, including duplicate conflicting records.
    An absent artifact is malformed even for FAIL/UNKNOWN: callers must retain
    an artifact describing the failure or unavailable-check condition.
    """
    if type(context) is not EvidenceContext:
        raise ValueError("context must be an EvidenceContext")
    if type(required_checks) is not tuple or not required_checks:
        raise ValueError("required_checks must be a nonempty tuple")
    required: dict[str, str] = {}
    for check in required_checks:
        if type(check) is not RequiredCheck:
            raise ValueError("required_checks must contain RequiredCheck values")
        if check.check_id in required:
            raise ValueError("duplicate required check_id")
        required[check.check_id] = check.definition_digest

    issues: set[GateIssue] = set()
    counts: dict[str, int] = {}
    if type(evidence) is not tuple:
        issues.add(GateIssue("malformed_evidence"))
        evidence = ()
    for record in evidence:
        if type(record) is not CheckEvidence:
            issues.add(GateIssue("malformed_evidence"))
            continue
        check_id = record.check_id
        counts[check_id] = counts.get(check_id, 0) + 1
        if check_id not in required:
            issues.add(GateIssue("unknown_check", check_id))
        elif record.definition_digest != required[check_id]:
            issues.add(GateIssue("definition_mismatch", check_id))
        if record.context != context:
            issues.add(GateIssue("context_mismatch", check_id))
        if record.status != "PASS" or record.exit_code != 0:
            issues.add(GateIssue("check_not_passed", check_id))
    for check_id in required:
        if check_id not in counts:
            issues.add(GateIssue("missing_check", check_id))
    for check_id, count in counts.items():
        if count > 1:
            issues.add(GateIssue("duplicate_check", check_id))
    return GateDecision(tuple(sorted(issues)))
