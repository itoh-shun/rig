"""Route-level producer coverage for binding acceptance criteria (#508)."""

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from .config import RECIPES
from .rig_surfaces import GATE_PRESETS, ROUTE_PRODUCERS, TASK_ROUTER, TASK_TYPES
from .state import _emit, parse_frontmatter


@runtime_checkable
class TaskRouter(Protocol):
    """Which recipe and capability the real selector picks, given a route declaration.

    This check's whole claim is that a shipped route declaration *reproduces what the
    selector does* — so the selector has to be the selector. Re-implementing the choice
    here would make the check agree with itself for ever while the routing it documents
    moved underneath it, which is the failure mode a declaration test exists to prevent.

    Inverted rather than imported, and the inversion needs this shape rather than a bare
    call because the selector takes pre-discovered recipe facts as `LocalRecipe` values —
    another pillar's dataclass, which this module used to *construct*. A judgement module
    that builds another pillar's type holds the edge whatever the call looks like, so the
    construction sits in `rig_surfaces.py` with the call, and what crosses back is a route.

    `None` means the profile named no known profile, which is a finding of its own here and
    not an absence of an answer.
    """

    def route(self, task_type: str, context: Mapping[str, object],
              profile: str) -> Mapping[str, str] | None:
        ...


_ROUTE_KEYS = {"task_type", "recipe", "capability", "context", "profile", "producers"}
_PRODUCER_KEYS = {"kind", "name"}
_KINDS = {"step", "sensor", "manual"}
_SENSORS = {
    "scan-secrets", "scan-injection", "scan-destructive", "anti-tamper",
}
_MANUAL_PRODUCERS = {"operator"}


def _gate(task_type: str, task_types: Mapping[str, Sequence[str]],
          gate_presets: Mapping[str, Sequence[str]]) -> set[str]:
    return {
        criterion
        for preset in task_types[task_type]
        for criterion in gate_presets[preset]
    }


def check_route_producers(*, router: TaskRouter = TASK_ROUTER,
                          task_types: Mapping[str, Sequence[str]] = TASK_TYPES,
                          gate_presets: Mapping[str, Sequence[str]] = GATE_PRESETS) -> None:
    """Require every shipped route's binding gate to name a resolvable producer.

    This proves ownership and resolution only.  It deliberately does not claim
    that a named producer generates adequate evidence or that its conclusion is
    correct.

    `TASK_TYPES` and `GATE_PRESETS` are the workbench's own mappings handed in as data, not
    transcribed: the gate this compares a declaration against must be the gate the run
    really builds, or the check passes on a vocabulary only it believes in.

    `ROUTE_PRODUCERS` is read as a module global rather than taken as a parameter, and the
    difference is deliberate. `tests/test_route_producer_contract.py` drives the whole CLI
    entry with a substituted declaration table, and it substitutes it *here* — a default
    argument is bound once at import and would leave that test asserting against the
    shipped table while believing it had replaced it.
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
        if task_type not in task_types:
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
        if not isinstance(context, Mapping):
            _emit("FAIL", f"{ctx} — context must be a mapping")
            continue
        selected = router.route(task_type, context, route["profile"])
        if selected is None:
            _emit("FAIL", f"{ctx} — profile `{route['profile']}` does not resolve")
            continue
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
        gate = _gate(task_type, task_types, gate_presets)
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
