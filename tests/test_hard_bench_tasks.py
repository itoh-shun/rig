"""Contract tests for the 'hard' benchmark corpus (benchmarks/hard-tasks/).

These tasks exist to give `rig-wb bench-invariance` something to discriminate
on: the natural, hasty fix is incomplete (a second call site left unguarded, or
a plausible-but-flawed helper trusted), so it passes the public suite while the
hidden check still lands. The narrow variant encodes exactly that incomplete
fix; the canonical variant closes it. If a task ever loses that separation, the
corpus stops being a valid test-bed for the metric.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]
HARD_CORPUS = REPO_ROOT / "benchmarks" / "hard-tasks"

HARD_TASK_IDS = (
    "hard-trusted-helper-authz",
    "hard-validate-two-sites",
)


def test_hard_corpus_membership_is_pinned() -> None:
    from rig_workbench.bench_tasks import load_tasks

    tasks = load_tasks(HARD_CORPUS)

    assert set(tasks) == set(HARD_TASK_IDS)
    assert all(tasks[task_id].difficulty == "hard" for task_id in HARD_TASK_IDS)


@pytest.mark.parametrize("task_id", HARD_TASK_IDS)
def test_hard_task_variant_contracts(task_id: str) -> None:
    from rig_workbench.bench_tasks import load_tasks, run_variant_contract

    task = load_tasks(HARD_CORPUS)[task_id]

    original = run_variant_contract(task, "original")
    narrow = run_variant_contract(task, "narrow")
    canonical = run_variant_contract(task, "canonical")

    # The incomplete fix keeps the public suite green while the exploit lands.
    assert original.public_passed, original.public_output
    assert not original.hidden_passed, original.hidden_output
    assert narrow.public_passed, narrow.public_output
    assert not narrow.hidden_passed, narrow.hidden_output

    # Only the complete fix closes the hole without regressing behaviour.
    assert canonical.public_passed, canonical.public_output
    assert canonical.hidden_passed, canonical.hidden_output
