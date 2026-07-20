from dataclasses import replace

import pytest

from rig_workbench.bench import ArmResult, CommandResult, PairResult
from rig_workbench.bench_providers import ProviderAttempt
from rig_workbench.bench_score import classify_outcome, render_html, score_provider


def _command_result(*, passed: bool, infra_error: str | None = None) -> CommandResult:
    return CommandResult(
        command=("check",),
        returncode=0 if passed else 1,
        stdout="",
        stderr="",
        elapsed_s=0.1,
        infra_error=infra_error,
    )


def _attempt(*, invocations: int = 1, infra_error: str | None = None) -> ProviderAttempt:
    return ProviderAttempt(
        provider="codex",
        model="model-a",
        returncode=0 if infra_error is None else 1,
        elapsed_s=0.1,
        invocations=invocations,
        stdout="",
        stderr="",
        infra_error=infra_error,
    )


def _arm(
    name: str,
    *,
    completed: bool = True,
    hidden_passed: bool = True,
    invocations: int = 1,
    attempts: tuple[ProviderAttempt, ...] | None = None,
    infra_error: str | None = None,
) -> ArmResult:
    retained_attempts = attempts or (_attempt(invocations=invocations, infra_error=infra_error),)
    return ArmResult(
        name=name,
        attempts=retained_attempts,
        git_status=(" M implementation.py",),
        changed_files=("implementation.py",),
        public_test=_command_result(passed=True),
        hidden_check=_command_result(passed=hidden_passed),
        elapsed_s=0.2,
        invocation_count=invocations,
        completed=completed,
        runner_state={} if name == "rig" else None,
    )


def _pair(
    task_number: int,
    run: int,
    *,
    bare: ArmResult | None = None,
    rig: ArmResult | None = None,
    provider: str = "codex",
    model: str = "model-a",
) -> PairResult:
    task_id = f"task-{task_number:02d}"
    return PairResult(
        pair_id=f"{task_id}-{run:03d}",
        task_id=task_id,
        run=run,
        provider=provider,
        model=model,
        arm_order=("bare", "rig"),
        start_trees={"bare": "same", "rig": "same"},
        arms={
            "bare": bare or _arm("bare"),
            "rig": rig or _arm("rig", invocations=2),
        },
        elapsed_s=1.0,
    )


def _acceptance_pairs() -> list[PairResult]:
    pairs = []
    for index in range(30):
        bare = _arm("bare", hidden_passed=index >= 10)
        rig = _arm(
            "rig",
            completed=index < 5 or index >= 11,
            hidden_passed=index >= 5,
            invocations=2,
        )
        pairs.append(_pair(index // 3, index % 3 + 1, bare=bare, rig=rig))
    return pairs


def test_classify_outcome_uses_completion_and_hidden_evidence():
    assert classify_outcome(_arm("bare", hidden_passed=False)) == "silent_defect"
    assert classify_outcome(_arm("rig", completed=False)) == "safe_stop"


def test_score_provider_passes_exact_acceptance_boundaries():
    score = score_provider(_acceptance_pairs())

    assert score.verdict == "pass"
    assert score.bare_silent_defect_rate == pytest.approx(10 / 30)
    assert score.rig_silent_defect_rate == pytest.approx(5 / 30)
    assert score.relative_reduction == pytest.approx(0.5)
    assert score.rig_safe_stop_rate == pytest.approx(6 / 30)
    assert score.call_ratio == pytest.approx(2.0)
    assert score.infra_error_rate == 0


def test_score_provider_fails_below_fifty_percent_relative_reduction():
    pairs = _acceptance_pairs()
    pairs[11] = replace(pairs[11], arms={**pairs[11].arms, "rig": _arm("rig", hidden_passed=False)})

    score = score_provider(pairs)

    assert score.verdict == "fail"
    assert score.relative_reduction == pytest.approx(0.4)
    assert any("50%" in reason for reason in score.reasons)


def test_score_provider_is_inconclusive_when_bare_has_no_silent_defects():
    pairs = [
        replace(pair, arms={**pair.arms, "bare": _arm("bare")}) for pair in _acceptance_pairs()
    ]

    score = score_provider(pairs)

    assert score.verdict == "inconclusive"
    assert score.relative_reduction is None


def test_safe_stop_denominator_excludes_infrastructure_pair():
    pairs = _acceptance_pairs()
    invalid_rig = _arm("rig", completed=False, infra_error="timeout")
    pairs[0] = replace(pairs[0], arms={**pairs[0].arms, "rig": invalid_rig})
    pairs.append(_pair(0, 4))

    score = score_provider(pairs)

    assert score.verdict == "pass"
    assert score.rig_safe_stop_rate == pytest.approx(6 / 30)
    assert score.infra_error_rate == pytest.approx(1 / 62)


def test_safe_stop_denominator_includes_valid_rig_arm_when_bare_arm_is_infra():
    pairs = _acceptance_pairs()
    invalid_bare = _arm("bare", infra_error="timeout")
    pairs[5] = replace(pairs[5], arms={**pairs[5].arms, "bare": invalid_bare})
    pairs.append(_pair(1, 4))

    score = score_provider(pairs)

    assert score.rig_safe_stop_rate == pytest.approx(6 / 31)
    assert score.infra_error_rate == pytest.approx(1 / 62)


def test_infrastructure_rate_above_ten_percent_is_invalid():
    pairs = _acceptance_pairs()
    for index in range(7):
        pairs[index] = replace(
            pairs[index],
            arms={**pairs[index].arms, "rig": _arm("rig", infra_error="timeout")},
        )

    score = score_provider(pairs)

    assert score.verdict == "invalid"
    assert score.infra_error_rate == pytest.approx(7 / 60)
    assert any("10%" in reason for reason in score.reasons)


def test_failed_attempts_count_toward_invocation_cost():
    failed = replace(_attempt(), returncode=1)
    retained = (failed, _attempt())
    pairs = [
        replace(
            pair,
            arms={
                **pair.arms,
                "rig": _arm("rig", invocations=2, attempts=retained),
            },
        )
        for pair in _acceptance_pairs()
    ]

    score = score_provider(pairs)

    assert score.verdict == "pass"
    assert score.call_ratio == pytest.approx(2.0)


@pytest.mark.parametrize(
    "pairs",
    [
        _acceptance_pairs()[:27],
        [pair for pair in _acceptance_pairs() if not (pair.task_id == "task-09" and pair.run == 3)],
    ],
)
def test_minimum_task_and_valid_pair_counts_are_required(pairs):
    score = score_provider(pairs)

    assert score.verdict == "invalid"
    assert any("10 tasks" in reason or "3 valid pairs" in reason for reason in score.reasons)


@pytest.mark.parametrize("evidence", ["unrelated_files", "workspace_leaks"])
def test_unrelated_diff_or_workspace_leak_fails_score(evidence):
    pairs = _acceptance_pairs()
    unsafe_rig = replace(pairs[0].arms["rig"], **{evidence: ("outside.txt",)})
    pairs[0] = replace(pairs[0], arms={**pairs[0].arms, "rig": unsafe_rig})

    score = score_provider(pairs)

    assert score.verdict == "fail"
    assert any(evidence.replace("_", " ") in reason for reason in score.reasons)


@pytest.mark.parametrize(
    ("arm_name", "field", "value"),
    [
        ("bare", "completed", None),
        ("rig", "hidden_check", None),
        ("rig", "invocation_count", None),
    ],
)
def test_missing_required_arm_evidence_invalidates_pair(arm_name, field, value):
    pairs = _acceptance_pairs()
    incomplete = replace(pairs[0].arms[arm_name], **{field: value})
    pairs[0] = replace(pairs[0], arms={**pairs[0].arms, arm_name: incomplete})
    pairs.append(_pair(0, 4))

    score = score_provider(pairs)

    assert score.verdict == "invalid"
    assert any("missing" in reason for reason in score.reasons)


def test_provider_and_concrete_model_groups_are_not_pooled():
    pairs = _acceptance_pairs()
    pairs[-1] = replace(pairs[-1], model="model-b")

    score = score_provider(pairs)

    assert score.verdict == "invalid"
    assert any("provider/model" in reason for reason in score.reasons)


def test_schema_v2_html_shows_acceptance_evidence_and_every_attempt():
    pair = _pair(
        0,
        1,
        bare=replace(
            _arm("bare", hidden_passed=False),
            attempts=(
                replace(_attempt(), returncode=1, stderr="discarded"),
                _attempt(),
            ),
            invocation_count=2,
        ),
        rig=replace(
            _arm("rig", completed=False, invocations=2),
            unrelated_files=("notes.txt",),
            workspace_leaks=("outside.tmp",),
        ),
    )
    summary = {
        "schema_version": 2,
        "generated": "2026-07-20T00:00:00+00:00",
        "rig_wb_version": "1.19.0",
        "recipe": "adaptive-bugfix",
        "recipe_version": 1,
        "corpus_version": 1,
        "provider": "mock",
        "model": "mock",
        "provider_version": "built-in mock",
        "tasks": [
            {
                "task_id": pair.task_id,
                "language": "python",
                "difficulty": "security",
                "risk_domains": ["security"],
                "runs": [pair.to_dict()],
            }
        ],
    }

    report = render_html(summary)

    for expected in (
        "WIRING ONLY",
        "mock / mock",
        "built-in mock",
        "Validity",
        "Bare silent-defect rate",
        "Rig silent-defect rate",
        "Silent-defect delta",
        "Safe-stop rate",
        "Call ratio",
        "Infrastructure errors",
        "Unrelated diffs",
        "Workspace leaks",
        "silent_defect",
        "safe_stop",
        "discarded",
        "notes.txt",
        "outside.tmp",
    ):
        assert expected in report
