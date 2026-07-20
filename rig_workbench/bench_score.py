"""Validity-aware scoring and reporting for paired benchmark evidence."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass
from html import escape
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bench import ArmResult, PairResult


@dataclass(frozen=True)
class ProviderScore:
    verdict: str
    reasons: tuple[str, ...]
    bare_silent_defect_rate: float
    rig_silent_defect_rate: float
    relative_reduction: float | None
    rig_safe_stop_rate: float
    call_ratio: float
    infra_error_rate: float


def classify_outcome(arm: ArmResult) -> str:
    """Classify an arm without converting missing evidence into success."""
    if (
        not isinstance(arm.completed, bool)
        or arm.hidden_check is None
        or not isinstance(arm.invocation_count, int)
    ):
        return "invalid"
    if any(attempt.infra_error for attempt in arm.attempts):
        return "infra_error"
    if arm.public_test.infra_error or arm.hidden_check.infra_error:
        return "infra_error"
    if arm.completed and arm.hidden_check.passed:
        return "clean_pass"
    if arm.completed and not arm.hidden_check.passed:
        return "silent_defect"
    if arm.hidden_check.passed:
        return "safe_stop"
    return "stopped_wrong"


def _arm_evidence_issue(arm: ArmResult | None) -> str | None:
    if arm is None:
        return "missing arm"
    if not isinstance(arm.completed, bool):
        return "missing completion state"
    if arm.hidden_check is None:
        return "missing hidden check"
    if not isinstance(arm.invocation_count, int) or arm.invocation_count < 0:
        return "missing invocation count"
    return None


def _invocation_cost(arm: ArmResult) -> int:
    retained_cost = sum(
        attempt.invocations
        for attempt in arm.attempts
        if isinstance(attempt.invocations, int) and attempt.invocations >= 0
    )
    reported_cost = (
        arm.invocation_count
        if isinstance(arm.invocation_count, int) and arm.invocation_count >= 0
        else 0
    )
    return max(reported_cost, retained_cost)


def score_provider(pairs: list[PairResult]) -> ProviderScore:
    """Score one concrete provider/model group from retained paired evidence."""
    reasons: list[str] = []
    identities = {(pair.provider, pair.model) for pair in pairs}
    if len(identities) != 1 or any(model is None for _, model in identities):
        reasons.append("results must contain exactly one concrete provider/model group")

    infra_arms = 0
    valid_pairs: list[PairResult] = []
    valid_rig_arms: list[ArmResult] = []
    missing_evidence: list[str] = []
    unsafe_evidence: list[str] = []
    for pair in pairs:
        pair_issues = []
        for name in ("bare", "rig"):
            arm = pair.arms.get(name)
            issue = _arm_evidence_issue(arm)
            if issue:
                pair_issues.append(f"{pair.pair_id} {name}: {issue}")
                continue
            outcome = classify_outcome(arm)
            if outcome == "infra_error":
                infra_arms += 1
                pair_issues.append(f"{pair.pair_id} {name}: infrastructure error")
            elif name == "rig":
                valid_rig_arms.append(arm)
            if arm.unrelated_files:
                unsafe_evidence.append(
                    f"{pair.pair_id} {name}: unrelated files {', '.join(arm.unrelated_files)}"
                )
            if arm.workspace_leaks:
                unsafe_evidence.append(
                    f"{pair.pair_id} {name}: workspace leaks {', '.join(arm.workspace_leaks)}"
                )
        if any("missing" in issue for issue in pair_issues):
            missing_evidence.extend(issue for issue in pair_issues if "missing" in issue)
        if not pair_issues:
            valid_pairs.append(pair)

    planned_arms = len(pairs) * 2
    infra_error_rate = infra_arms / planned_arms if planned_arms else 0.0
    if infra_error_rate > 0.10:
        reasons.append(f"infrastructure error rate {infra_error_rate:.1%} exceeds 10%")
    if missing_evidence:
        reasons.append(f"missing required evidence: {'; '.join(missing_evidence)}")

    valid_by_task = Counter(pair.task_id for pair in valid_pairs)
    qualifying_tasks = sum(count >= 3 for count in valid_by_task.values())
    if qualifying_tasks < 10:
        reasons.append(
            f"only {qualifying_tasks} tasks have at least 3 valid pairs; 10 tasks required"
        )

    valid_count = len(valid_pairs)
    bare_outcomes = [classify_outcome(pair.arms["bare"]) for pair in valid_pairs]
    rig_outcomes = [classify_outcome(pair.arms["rig"]) for pair in valid_pairs]
    bare_silent_defect_rate = (
        bare_outcomes.count("silent_defect") / valid_count if valid_count else 0.0
    )
    rig_silent_defect_rate = (
        rig_outcomes.count("silent_defect") / valid_count if valid_count else 0.0
    )
    relative_reduction = (
        None
        if bare_silent_defect_rate == 0
        else (bare_silent_defect_rate - rig_silent_defect_rate) / bare_silent_defect_rate
    )
    valid_rig_outcomes = [classify_outcome(arm) for arm in valid_rig_arms]
    rig_safe_stop_rate = (
        valid_rig_outcomes.count("safe_stop") / len(valid_rig_outcomes)
        if valid_rig_outcomes
        else 0.0
    )
    bare_calls = sum(
        _invocation_cost(pair.arms["bare"])
        for pair in pairs
        if pair.arms.get("bare") is not None
    )
    rig_calls = sum(
        _invocation_cost(pair.arms["rig"])
        for pair in pairs
        if pair.arms.get("rig") is not None
    )
    call_ratio = rig_calls / bare_calls if bare_calls else math.inf

    acceptance_failures = []
    if relative_reduction is not None and relative_reduction < 0.50:
        acceptance_failures.append(f"silent-defect reduction {relative_reduction:.1%} is below 50%")
    if rig_safe_stop_rate > 0.20:
        acceptance_failures.append(f"rig safe-stop rate {rig_safe_stop_rate:.1%} exceeds 20%")
    if call_ratio > 2.5:
        acceptance_failures.append(f"call ratio {call_ratio:.2f} exceeds 2.5")
    acceptance_failures.extend(unsafe_evidence)

    invalid = bool(reasons)
    if invalid:
        verdict = "invalid"
    elif acceptance_failures:
        verdict = "fail"
        reasons.extend(acceptance_failures)
    elif relative_reduction is None:
        verdict = "inconclusive"
        reasons.append("bare silent-defect rate is zero; relative reduction is inconclusive")
    else:
        verdict = "pass"
        reasons.append("all acceptance thresholds passed")

    return ProviderScore(
        verdict=verdict,
        reasons=tuple(reasons),
        bare_silent_defect_rate=bare_silent_defect_rate,
        rig_silent_defect_rate=rig_silent_defect_rate,
        relative_reduction=relative_reduction,
        rig_safe_stop_rate=rig_safe_stop_rate,
        call_ratio=call_ratio,
        infra_error_rate=infra_error_rate,
    )


def _legacy_outcome(arm: dict[str, Any], mode: str) -> str:
    hidden_passed = arm.get("spec_check") == "PASS"
    completed = arm.get("runner_exit", 0) == 0 if mode == "rig" else True
    if completed and hidden_passed:
        return "clean_pass"
    if completed:
        return "silent_defect"
    if hidden_passed:
        return "safe_stop"
    return "stopped_wrong"


def _check_evidence(data: object) -> SimpleNamespace | None:
    if not isinstance(data, dict):
        return None
    passed = data.get("passed")
    if not isinstance(passed, bool):
        return None
    return SimpleNamespace(passed=passed, infra_error=data.get("infra_error"))


def _arm_evidence(data: object) -> SimpleNamespace | None:
    if not isinstance(data, dict):
        return None
    attempts = tuple(
        SimpleNamespace(
            infra_error=attempt.get("infra_error"),
            invocations=attempt.get("invocations", 0),
        )
        for attempt in data.get("attempts", ())
        if isinstance(attempt, dict)
    )
    return SimpleNamespace(
        attempts=attempts,
        public_test=_check_evidence(data.get("public_test")),
        hidden_check=_check_evidence(data.get("hidden_check")),
        completed=data.get("completed"),
        invocation_count=data.get("invocation_count"),
        unrelated_files=tuple(data.get("unrelated_files") or ()),
        workspace_leaks=tuple(data.get("workspace_leaks") or ()),
    )


def _summary_pairs(summary: dict[str, Any]) -> list[SimpleNamespace]:
    pairs = []
    for task in summary.get("tasks", ()):
        if not isinstance(task, dict):
            continue
        for pair in task.get("runs", ()):
            if not isinstance(pair, dict):
                continue
            arm_data = pair.get("arms")
            arms = (
                {name: _arm_evidence(arm_data.get(name)) for name in ("bare", "rig")}
                if isinstance(arm_data, dict)
                else {}
            )
            pairs.append(
                SimpleNamespace(
                    pair_id=str(pair.get("pair_id", pair.get("run", ""))),
                    task_id=str(pair.get("task_id", task.get("task_id", ""))),
                    provider=pair.get("provider", summary.get("provider")),
                    model=pair.get("model", summary.get("model")),
                    arms=arms,
                )
            )
    return pairs


def _format_rate(value: object) -> str:
    return f"{float(value):.1%}" if isinstance(value, (int, float)) else "unavailable"


def _format_ratio(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "unavailable"
    return "infinite" if math.isinf(value) else f"{value:.2f}x"


def _render_legacy_html(summary: dict[str, Any]) -> str:
    rows = []
    for task in summary.get("tasks", ()):
        for run in task.get("runs", ()):
            modes = run.get("modes", {})
            bare = modes.get("bare", {})
            rig = modes.get("rig", {})
            rows.append(
                "<tr>"
                f"<td>{escape(str(task.get('task_id', '')))}</td>"
                f"<td>{escape(str(run.get('run', '')))}</td>"
                f"<td>{escape(str(bare.get('outcome') or _legacy_outcome(bare, 'bare')))}</td>"
                f"<td>{escape(str(rig.get('outcome') or _legacy_outcome(rig, 'rig')))}</td>"
                "</tr>"
            )
    banner = " <strong>WIRING ONLY</strong>" if summary.get("provider") == "mock" else ""
    return _html_document(
        "Paired benchmark",
        (
            "<p class='notice'>legacy schema v1: acceptance score unavailable</p>"
            f"<p>{escape(str(summary.get('provider', 'unknown')))} / "
            f"{escape(str(summary.get('model', 'unknown')))}{banner}</p>"
            "<table><thead><tr><th>Task</th><th>Run</th><th>Bare outcome</th>"
            f"<th>Rig outcome</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
        ),
    )


def _html_document(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>"
        ":root{--ink:#17201c;--paper:#f5f1e7;--line:#a59e8c;--accent:#a33b20}"
        "body{background:var(--paper);color:var(--ink);font:16px Georgia,serif;"
        "margin:0 auto;max-width:88rem;padding:2rem}h1,h2{letter-spacing:.02em}"
        ".identity,.metrics{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,"
        "minmax(12rem,1fr));margin:1.25rem 0}.card{border-top:4px solid var(--ink);"
        "padding:.75rem;background:#fff9}.label{font-size:.78rem;text-transform:uppercase}"
        ".value{font-size:1.45rem;font-weight:bold;margin-top:.35rem}"
        "table{border-collapse:collapse;width:100%;margin:1rem 0 2rem}"
        "th,td{border:1px solid var(--line);padding:.5rem;text-align:left;vertical-align:top}"
        "th{background:#e7dfcf}.notice,strong{color:var(--accent)}code{white-space:pre-wrap}"
        "@media(max-width:700px){body{padding:1rem}table{display:block;overflow-x:auto}}"
        "</style></head><body>"
        f"{body}</body></html>"
    )


def render_html(summary: dict) -> str:
    """Render schema-v2 evidence or a compatible schema-v1 benchmark report."""
    schema_version = summary.get("schema_version", 1)
    if schema_version == 1:
        return _render_legacy_html(summary)
    if schema_version != 2:
        raise ValueError(f"unsupported benchmark schema_version {schema_version!r}")

    pairs = _summary_pairs(summary)
    score_data = summary.get("score")
    if not isinstance(score_data, dict):
        score_data = asdict(score_provider(pairs))

    provider = escape(str(summary.get("provider", "unknown")))
    model = escape(str(summary.get("model", "unknown")))
    provider_version = escape(str(summary.get("provider_version", "unavailable")))
    banner = " <strong>WIRING ONLY</strong>" if summary.get("provider") == "mock" else ""
    reasons = "".join(f"<li>{escape(str(reason))}</li>" for reason in score_data["reasons"])

    pair_rows = []
    attempt_rows = []
    unrelated = []
    leaks = []
    for task in summary.get("tasks", ()):
        for pair in task.get("runs", ()):
            arms = pair.get("arms", {})
            outcomes = {}
            for name in ("bare", "rig"):
                arm = arms.get(name, {})
                evidence = _arm_evidence(arm)
                outcomes[name] = arm.get("outcome") or (
                    "invalid" if evidence is None else classify_outcome(evidence)
                )
                unrelated.extend(
                    f"{pair.get('pair_id', pair.get('run', ''))} {name}: {path}"
                    for path in arm.get("unrelated_files", ())
                )
                leaks.extend(
                    f"{pair.get('pair_id', pair.get('run', ''))} {name}: {path}"
                    for path in arm.get("workspace_leaks", ())
                )
                attempts = arm.get("attempts", ())
                for index, attempt in enumerate(attempts, 1):
                    role = "final" if index == len(attempts) else "discarded/replacement"
                    attempt_rows.append(
                        "<tr>"
                        f"<td>{escape(str(pair.get('pair_id', pair.get('run', ''))))}</td>"
                        f"<td>{escape(name)}</td><td>{index}</td><td>{role}</td>"
                        f"<td>{escape(str(attempt.get('returncode', '')))}</td>"
                        f"<td>{escape(str(attempt.get('invocations', '')))}</td>"
                        f"<td>{escape(str(attempt.get('infra_error') or ''))}</td>"
                        f"<td><code>{escape(str(attempt.get('stderr') or ''))}</code></td>"
                        "</tr>"
                    )
            pair_rows.append(
                "<tr>"
                f"<td>{escape(str(task.get('task_id', '')))}</td>"
                f"<td>{escape(str(pair.get('pair_id', pair.get('run', ''))))}</td>"
                f"<td>{escape(str(outcomes['bare']))}</td>"
                f"<td>{escape(str(outcomes['rig']))}</td>"
                "</tr>"
            )

    relative = score_data.get("relative_reduction")
    relative_text = "inconclusive" if relative is None else _format_rate(relative)
    unrelated_items = (
        "".join(f"<li>{escape(item)}</li>" for item in unrelated) or "<li>None recorded</li>"
    )
    leak_items = "".join(f"<li>{escape(item)}</li>" for item in leaks) or "<li>None recorded</li>"
    body = (
        f"<h1>Adaptive bugfix benchmark{banner}</h1>"
        "<div class='identity'>"
        f"<div class='card'><div class='label'>Provider / model</div><div class='value'>{provider} / {model}</div></div>"
        f"<div class='card'><div class='label'>Provider version</div><div class='value'>{provider_version}</div></div>"
        f"<div class='card'><div class='label'>Validity</div><div class='value'>{escape(str(score_data['verdict']))}</div></div>"
        "</div><div class='metrics'>"
        f"<div class='card'><div class='label'>Bare silent-defect rate</div><div class='value'>{_format_rate(score_data.get('bare_silent_defect_rate'))}</div></div>"
        f"<div class='card'><div class='label'>Rig silent-defect rate</div><div class='value'>{_format_rate(score_data.get('rig_silent_defect_rate'))}</div></div>"
        f"<div class='card'><div class='label'>Silent-defect delta</div><div class='value'>{relative_text}</div></div>"
        f"<div class='card'><div class='label'>Safe-stop rate</div><div class='value'>{_format_rate(score_data.get('rig_safe_stop_rate'))}</div></div>"
        f"<div class='card'><div class='label'>Call ratio</div><div class='value'>{_format_ratio(score_data.get('call_ratio'))}</div></div>"
        f"<div class='card'><div class='label'>Infrastructure errors</div><div class='value'>{_format_rate(score_data.get('infra_error_rate'))}</div></div>"
        f"</div><h2>Reasons</h2><ul>{reasons}</ul>"
        "<h2>Paired outcomes</h2><table><thead><tr><th>Task</th><th>Pair</th>"
        f"<th>Bare</th><th>Rig</th></tr></thead><tbody>{''.join(pair_rows)}</tbody></table>"
        f"<h2>Unrelated diffs</h2><ul>{unrelated_items}</ul>"
        f"<h2>Workspace leaks</h2><ul>{leak_items}</ul>"
        "<h2>Retained attempts</h2><table><thead><tr><th>Pair</th><th>Arm</th>"
        "<th>Attempt</th><th>Role</th><th>Exit</th><th>Invocations</th>"
        f"<th>Infrastructure error</th><th>Detail</th></tr></thead><tbody>{''.join(attempt_rows)}</tbody></table>"
    )
    return _html_document("Adaptive bugfix benchmark", body)
