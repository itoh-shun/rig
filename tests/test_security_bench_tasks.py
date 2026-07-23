"""Contract tests for the security benchmark corpus (benchmarks/security-tasks/).

These tasks are the evidence behind rig's "with vs without" claim on security
work: the ``narrow`` variant is the plausible-but-still-vulnerable fix a bare
agent tends to write — it passes the public tests yet fails the hidden exploit
(a *silent security defect*). The ``canonical`` variant actually closes the
hole. Each task must keep that separation exactly, or the differential the
benchmark reports is not measuring what it claims to.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parents[1]
SECURITY_CORPUS = REPO_ROOT / "benchmarks" / "security-tasks"

SECURITY_TASK_IDS = (
    "sec-command-injection-shell",
    "sec-idor-doc-access",
    "sec-path-traversal-absolute",
    "sec-ssrf-private-ip",
    "sec-tenant-isolation-read",
    "sec-tenant-isolation-write",
    "sec-unsalted-password-hash",
    "sec-weak-reset-token",
)


def test_security_corpus_membership_is_pinned() -> None:
    from rig_workbench.bench_tasks import load_tasks

    tasks = load_tasks(SECURITY_CORPUS)

    assert set(tasks) == set(SECURITY_TASK_IDS)
    assert all(tasks[task_id].difficulty == "security" for task_id in SECURITY_TASK_IDS)
    assert all("security" in tasks[task_id].risk_domains for task_id in SECURITY_TASK_IDS)


@pytest.mark.parametrize("task_id", SECURITY_TASK_IDS)
def test_security_task_variant_contracts(task_id: str) -> None:
    from rig_workbench.bench_tasks import load_tasks, run_variant_contract

    task = load_tasks(SECURITY_CORPUS)[task_id]

    original = run_variant_contract(task, "original")
    narrow = run_variant_contract(task, "narrow")
    canonical = run_variant_contract(task, "canonical")

    # The vulnerable starting point and the plausible partial fix both keep the
    # public suite green while the exploit still lands.
    assert original.public_passed, original.public_output
    assert not original.hidden_passed, original.hidden_output
    assert narrow.public_passed, narrow.public_output
    assert not narrow.hidden_passed, narrow.hidden_output

    # Only the real fix closes the hole without regressing behaviour.
    assert canonical.public_passed, canonical.public_output
    assert canonical.hidden_passed, canonical.hidden_output
