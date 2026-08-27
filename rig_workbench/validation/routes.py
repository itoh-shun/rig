"""Route-level producer coverage for binding acceptance criteria (#508)."""

from collections.abc import Mapping

from rig_workbench.workbench.capabilities import (
    ROUTE_PRODUCERS,
    LocalRecipe,
    select_task_route,
)
from rig_workbench.workbench.config import GATE_PRESETS, TASK_TYPES

from .config import RECIPES
from .state import _emit, parse_frontmatter

_ROUTE_KEYS = {"task_type", "recipe", "capability", "context", "profile", "producers"}
_PRODUCER_KEYS = {"kind", "name"}
_KINDS = {"step", "sensor", "manual"}
_SENSORS = {
    "scan-secrets", "scan-injection", "scan-destructive", "anti-tamper",
}
_MANUAL_PRODUCERS = {"operator"}
_CORE_NAMES = {
    "bugfix", "feature", "refactor", "documentation", "debug", "release-flow",
    "design-first", "review-only",
}


def _available(profile: str) -> dict[str, LocalRecipe] | None:
    core = {name: LocalRecipe(name, "core", None, True) for name in _CORE_NAMES}
    additions = {
        "core": None,
        "preferred-design": LocalRecipe("design", "project", "design", True, True),
        "preferred-test": LocalRecipe(
            "test-design", "project", "test-design", True, True,
        ),
        "preferred-pr": LocalRecipe(
            "pr-review", "project", "pr-review", True, True,
        ),
    }
    if profile not in additions:
        return None
    addition = additions[profile]
    if addition is not None:
        core[addition.name] = addition
    return core


def _gate(task_type: str) -> set[str]:
    return {
        criterion
        for preset in TASK_TYPES[task_type]
        for criterion in GATE_PRESETS[preset]
    }


def check_route_producers() -> None:
    """Require every shipped route's binding gate to name a resolvable producer.

    This proves ownership and resolution only.  It deliberately does not claim
    that a named producer generates adequate evidence or that its conclusion is
    correct.
    """
    seen: set[tuple[str, str, str]] = set()
    for index, route in enumerate(ROUTE_PRODUCERS):
        ctx = f"route producers[{index}]"
        if not isinstance(route, Mapping):
            _emit("FAIL", f"{ctx} — route must be a mapping")
            continue
        unknown = set(route) - _ROUTE_KEYS
        missing_keys = _ROUTE_KEYS - set(route)
        if unknown:
            _emit("FAIL", f"{ctx} — unknown keys: {', '.join(sorted(unknown))}")
        if missing_keys:
            _emit("FAIL", f"{ctx} — missing keys: {', '.join(sorted(missing_keys))}")
            continue
        task_type, recipe, capability = (
            route["task_type"], route["recipe"], route["capability"]
        )
        ctx = f"route {task_type}/{capability} → {recipe}"
        if task_type not in TASK_TYPES:
            _emit("FAIL", f"{ctx} — task_type does not resolve")
            continue
        if not isinstance(recipe, str) or not recipe:
            _emit("FAIL", f"{ctx} — recipe must be a non-empty string")
            continue
        identity = (task_type, capability, recipe)
        if identity in seen:
            _emit("FAIL", f"{ctx} — duplicate route declaration")
            continue
        seen.add(identity)

        context = route["context"]
        available = _available(route["profile"])
        if not isinstance(context, Mapping):
            _emit("FAIL", f"{ctx} — context must be a mapping")
            continue
        if available is None:
            _emit("FAIL", f"{ctx} — profile `{route['profile']}` does not resolve")
            continue
        selected = select_task_route(task_type, context, available)
        if (selected["recipe"], selected["capability"]) != (recipe, capability):
            _emit(
                "FAIL",
                f"{ctx} — declaration does not reproduce selector result "
                f"{selected['capability']} → {selected['recipe']}",
            )
            continue

        path = RECIPES / f"{recipe}.md"
        fm, _ = parse_frontmatter(path) if path.is_file() else (None, "")
        if fm is None:
            _emit("FAIL", f"{ctx} — recipe does not resolve")
            continue
        step_ids = {
            step.get("id") for step in fm.get("steps", [])
            if isinstance(step, Mapping) and isinstance(step.get("id"), str)
        }
        producers = route["producers"]
        if not isinstance(producers, Mapping):
            _emit("FAIL", f"{ctx} — producers must be a mapping")
            continue
        gate = _gate(task_type)
        absent = gate - set(producers)
        extra = set(producers) - gate
        for criterion in sorted(absent):
            _emit("FAIL", f"{ctx} — binding criterion `{criterion}` has no producer")
        for criterion in sorted(extra):
            _emit("FAIL", f"{ctx} — producer names non-binding criterion `{criterion}`")
        invalid = False
        for criterion in sorted(gate & set(producers)):
            owner = producers[criterion]
            owner_ctx = f"{ctx}.{criterion}"
            if not isinstance(owner, Mapping):
                _emit("FAIL", f"{owner_ctx} — producer must be a mapping")
                invalid = True
                continue
            owner_unknown = set(owner) - _PRODUCER_KEYS
            if owner_unknown:
                _emit("FAIL", f"{owner_ctx} — unknown keys: {', '.join(sorted(owner_unknown))}")
                invalid = True
            if set(owner) != _PRODUCER_KEYS:
                _emit("FAIL", f"{owner_ctx} — producer must contain exactly `kind` and `name`")
                invalid = True
                continue
            kind, name = owner["kind"], owner["name"]
            if kind not in _KINDS or not isinstance(name, str) or not name.strip():
                _emit("FAIL", f"{owner_ctx} — producer kind/name is invalid")
                invalid = True
            elif kind == "step" and name not in step_ids:
                _emit("FAIL", f"{owner_ctx} — step producer `{name}` does not resolve")
                invalid = True
            elif kind == "sensor" and name not in _SENSORS:
                _emit("FAIL", f"{owner_ctx} — sensor producer `{name}` does not resolve")
                invalid = True
            elif kind == "manual" and name not in _MANUAL_PRODUCERS:
                _emit("FAIL", f"{owner_ctx} — manual producer `{name}` does not resolve")
                invalid = True
        if not absent and not extra and not invalid:
            _emit("PASS", f"{ctx}: producer coverage OK")
