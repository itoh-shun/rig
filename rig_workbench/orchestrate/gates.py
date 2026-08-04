"""Executable gate registry shared by runtime and validators.

Only gates with code-backed handlers belong here.  A prompt pattern may describe
another aggregation contract, but it must never become executable merely because
a Markdown file with the same name exists.
"""

RUNTIME_GATES = frozenset({"acceptance-gate", "review-gate"})


def is_runtime_gate(value: object) -> bool:
    return isinstance(value, str) and value in RUNTIME_GATES


def validate_executable_steps(
    steps: object, *, no_orchestrate: object = False,
) -> dict:
    """Pure authority for whether recipe steps may enter the code runner.

    Custom prompt gates remain valid documentation for an exact boolean
    ``no_orchestrate: true`` recipe, but are never executable.
    """
    errors: list[str] = []
    unsupported: list[dict[str, str]] = []
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
        if gate in (None, "", "—", "-"):
            continue
        step_id = step.get("id") if isinstance(step.get("id"), str) else f"[{index}]"
        if not is_runtime_gate(gate):
            unsupported.append({"step": step_id, "gate": str(gate)})
    if unsupported and not manual_only:
        errors.extend(
            f"step `{item['step']}` uses unsupported executable gate `{item['gate']}`"
            for item in unsupported
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
