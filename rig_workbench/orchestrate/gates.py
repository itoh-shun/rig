"""Executable gate registry shared by runtime and validators.

Only gates with code-backed handlers belong here.  A prompt pattern may describe
another aggregation contract, but it must never become executable merely because
a Markdown file with the same name exists.
"""

RUNTIME_GATES = frozenset({"acceptance-gate", "review-gate"})

#: Executors that return without ever calling a provider, so nothing they run can
#: produce a verdict.  This is the ONE place the set is declared: the runtime
#: preflight (``enforce_executable_state``), ``rig-wb validate`` and the tests all
#: read it from here rather than re-listing the names, because a rule re-derived
#: per layer stops agreeing with itself the first time the set changes.
VERDICTLESS_EXECUTORS = frozenset({"checks-only", "risk-assess"})


def is_runtime_gate(value: object) -> bool:
    return isinstance(value, str) and value in RUNTIME_GATES


def is_verdictless_executor(value: object) -> bool:
    return isinstance(value, str) and value in VERDICTLESS_EXECUTORS


def validate_executable_steps(
    steps: object, *, no_orchestrate: object = False,
) -> dict:
    """Pure authority for whether recipe steps may enter the code runner.

    Custom prompt gates remain valid documentation for an exact boolean
    ``no_orchestrate: true`` recipe, but are never executable.

    It also refuses a step that declares a runtime gate or ``acceptance[]`` while
    its executor cannot produce a verdict.  That combination has no honest
    outcome: before this rule the runner stamped the step ``pass`` on its checks
    alone (a gate that judges nothing), and once the gate correctly waits for a
    verdict the same step parks in ``AWAIT`` forever with no verifier that could
    ever arrive.  Refusing it here — rather than only in ``rig-wb validate``,
    which globs the shipped tier alone — reaches every tier the runner accepts,
    including ``<project>/.rig/recipes/``.
    """
    errors: list[str] = []
    unsupported: list[dict[str, str]] = []
    verdictless: list[dict[str, str]] = []
    if not isinstance(no_orchestrate, bool):
        errors.append("no_orchestrate must be an exact boolean (true/false)")
        manual_only = False
    else:
        manual_only = no_orchestrate
    if not isinstance(steps, list):
        errors.append("steps must be a list")
        values: list[object] = []
    else:
        values = steps
    for index, step in enumerate(values):
        if not isinstance(step, dict):
            errors.append(f"steps[{index}] must be an object")
            continue
        gate = step.get("gate")
        step_id = step.get("id") if isinstance(step.get("id"), str) else f"[{index}]"
        executor = step.get("executor")
        if is_verdictless_executor(executor):
            declared = ("gate: " + str(gate)) if is_runtime_gate(gate) else (
                "acceptance[]" if step.get("acceptance") else None)
            if declared is not None:
                verdictless.append(
                    {"step": step_id, "executor": str(executor), "declared": declared})
        if gate in (None, "", "—", "-"):
            continue
        if not is_runtime_gate(gate):
            unsupported.append({"step": step_id, "gate": str(gate)})
    if unsupported and not manual_only:
        errors.extend(
            f"step `{item['step']}` uses unsupported executable gate `{item['gate']}`"
            for item in unsupported
        )
    # Not excused by no_orchestrate: the combination is a false declaration on the
    # page as well as an unreachable state in the runner.
    errors.extend(
        f"step `{item['step']}` declares {item['declared']} but executor "
        f"`{item['executor']}` cannot produce a verdict (it runs checks and returns)."
        f" Drop the gate and acceptance[], or give the step an executor that judges."
        for item in verdictless
    )
    orchestratable = not errors and not manual_only and not unsupported
    if errors:
        reason = errors[0]
    elif manual_only:
        reason = "recipe declares no_orchestrate: true (manual-only)"
    elif unsupported:
        reason = "custom gates are manual-only"
    else:
        reason = "all gates have code-backed runtime handlers"
    return {
        "structurally_valid": not errors,
        "orchestratable": orchestratable,
        "manual_only": manual_only,
        "unsupported_gates": unsupported,
        "verdictless_gates": verdictless,
        "errors": errors,
        "reason": reason,
    }


def validate_executable_recipe(recipe: object) -> dict:
    """Pure recipe-level wrapper around :func:`validate_executable_steps`."""
    if not isinstance(recipe, dict):
        return validate_executable_steps(None, no_orchestrate=None)
    return validate_executable_steps(
        recipe.get("steps"), no_orchestrate=recipe.get("no_orchestrate", False)
    )
