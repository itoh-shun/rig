"""validation recipes: recipe frontmatter/step/extends/needs checks (split from scripts/validate.py)."""

import pathlib
import re

from .config import AGENTS, FACETS, PATTERNS, RECIPES, ROOT
from .state import _emit, parse_frontmatter


# ── reference resolution helpers ─────────────────────────────────────────────
def _check_exists(path: pathlib.Path, ctx: str, field: str, hint_dir: pathlib.Path | None = None) -> bool:
    if path.exists():
        return True
    rel = path.relative_to(ROOT) if ROOT in path.parents or path.is_relative_to(ROOT) else path
    msg = f"{ctx} — {field}: {rel} does not exist"
    if hint_dir is not None and hint_dir.is_dir():
        available = sorted(p.stem for p in hint_dir.glob("*.md"))
        if available:
            msg += f" (expected path: {rel}; available: {', '.join(available)})"
    _emit("FAIL", msg)
    return False


def _resolve_persona(name: str, ctx: str) -> bool:
    """Resolve a persona in shipped facets → agents order (shipped equivalent of §5 tier resolution)."""
    # facets/personas/<name>.md (subdirectories allowed via / separator)
    facet_path = FACETS / "personas" / pathlib.Path(name.replace("/", "/") + ".md")
    if facet_path.exists():
        return True
    # agents/<name>.md (directly under repo root)
    agent_path = AGENTS / f"{name}.md"
    if agent_path.exists():
        return True
    _emit("FAIL", f"{ctx} — personas[{name!r}] cannot be resolved (looked in facets/personas/ and agents/)")
    return False


def _check_pattern_or_gate(val: str | None, ctx: str, field: str) -> None:
    if not val or val in ("—", "-"):
        return
    _check_exists(PATTERNS / f"{val}.md", ctx, field)


_VALID_GATES = ("review-gate", "acceptance-gate", "magi-consensus")


def _check_gate(val: str | None, ctx: str, field: str) -> None:
    """gate only allows the two values review-gate|acceptance-gate (enum FAIL from #198; #227).

    The `pattern` field allows every brick name under patterns/, so reusing
    `_check_pattern_or_gate` (existence check) is fine there, but `gate` is
    limited to those two values and needs a separate criterion (do not let the
    existence of e.g. patterns/serial.md cause a false PASS).
    """
    if not val or val in ("—", "-"):
        return
    if val not in _VALID_GATES:
        _emit(
            "FAIL",
            f"{ctx} — {field}: value '{val}' is an invalid enum value."
            f" Allowed values: {', '.join(_VALID_GATES)}",
        )


_SIZE_TOKEN_RE = re.compile(r"\b(?:S|M|L|XL)\+")


def _check_condition(val: str | None, ctx: str, field: str) -> None:
    """condition is expected to contain a size token (S+/M+/L+/XL+) in its free text (#109/#229/#230).

    The canonical form is judged by "presence of a size token" rather than a
    mandatory `size:` prefix (to avoid a false WARN on release-flow.md's
    real-world value `"--design or size L+"`).
    """
    if val is None:
        return
    if not _SIZE_TOKEN_RE.search(str(val)):
        _emit(
            "WARN",
            f"{ctx} — {field}: no valid size token (S+/M+/L+/XL+) found in '{val}'"
            f" (the size-aware RESOLVE decision may not work as intended)",
        )


# ── per-recipe check ─────────────────────────────────────────────────────────
def check_recipe(path: pathlib.Path) -> None:
    ctx = f"recipe {path.stem}"
    fm, raw = parse_frontmatter(path)

    if fm is None:
        _emit("FAIL", f"{ctx} — frontmatter cannot be parsed (YAML error: {raw[:80]})")
        return

    # (1) required top-level keys (§3.5)
    required_top = ["name", "description", "scope", "steps", "autonomy"]
    missing = [k for k in required_top if k not in fm or fm[k] is None]
    if missing:
        for k in missing:
            _emit("FAIL", f"{ctx} — required field `{k}` is missing")
        return  # further checks are meaningless with required fields missing

    # name ↔ filename (#216: match the FAIL severity defined by validate.md)
    if fm["name"] != path.stem:
        _emit("FAIL", f"{ctx} — name '{fm['name']}' does not match filename '{path.stem}'")

    # scope value range
    if fm["scope"] not in ("shipped", "user", "project"):
        _emit("FAIL", f"{ctx} — scope '{fm['scope']}' must be shipped|user|project")

    # autonomy value range
    if fm["autonomy"] not in ("interactive", "autonomous"):
        _emit("FAIL", f"{ctx} — autonomy '{fm['autonomy']}' must be interactive|autonomous")

    # backend value range (#52)
    backend_val = fm.get("backend")
    if backend_val is not None and backend_val not in ("manual", "workflow"):
        _emit("FAIL", f"{ctx} — backend '{backend_val}' must be manual|workflow")

    # tdd value range (#56)
    tdd_val = fm.get("tdd")
    if tdd_val is not None and not isinstance(tdd_val, bool):
        _emit("FAIL", f"{ctx} — tdd '{tdd_val!r}' must be a boolean (true/false)")

    # no_default_personas value range (#70)
    ndp_val = fm.get("no_default_personas")
    if ndp_val is not None and not isinstance(ndp_val, bool):
        _emit("FAIL", f"{ctx} — no_default_personas '{ndp_val!r}' must be a boolean (true/false)")

    # orchestrate value range (#129/#151)
    orch_val = fm.get("orchestrate")
    if orch_val is not None and not isinstance(orch_val, bool):
        _emit("FAIL", f"{ctx} — orchestrate '{orch_val!r}' must be a boolean (true/false)")

    # cross_llm value range (#130/#151)
    cross_llm_val = fm.get("cross_llm")
    if cross_llm_val is not None and not isinstance(cross_llm_val, bool):
        _emit("FAIL", f"{ctx} — cross_llm '{cross_llm_val!r}' must be a boolean (true/false)")

    # no_capture value range (#137/#151)
    no_capture_val = fm.get("no_capture")
    if no_capture_val is not None and not isinstance(no_capture_val, bool):
        _emit("FAIL", f"{ctx} — no_capture '{no_capture_val!r}' must be a boolean (true/false)")

    # verify_findings value range (review-gate adversarial verification; §3.5)
    vf_val = fm.get("verify_findings")
    if vf_val is not None and not isinstance(vf_val, bool):
        _emit("FAIL", f"{ctx} — verify_findings '{vf_val!r}' must be a boolean (true/false)")

    # adversarial value range (#172/#228)
    adversarial_val = fm.get("adversarial")
    if adversarial_val is not None and not isinstance(adversarial_val, bool):
        _emit("FAIL", f"{ctx} — adversarial '{adversarial_val!r}' must be a boolean (true/false)")

    # visual value range (#174/#228)
    visual_val = fm.get("visual")
    if visual_val is not None and not isinstance(visual_val, bool):
        _emit("FAIL", f"{ctx} — visual '{visual_val!r}' must be a boolean (true/false)")

    # no_orchestrate value range (#178/#228)
    no_orch_val = fm.get("no_orchestrate")
    if no_orch_val is not None and not isinstance(no_orch_val, bool):
        _emit("FAIL", f"{ctx} — no_orchestrate '{no_orch_val!r}' must be a boolean (true/false)")

    # design value range (#182/#228)
    design_val = fm.get("design")
    if design_val is not None and not isinstance(design_val, bool):
        _emit("FAIL", f"{ctx} — design '{design_val!r}' must be a boolean (true/false)")

    # review value range (#182/#228)
    review_val = fm.get("review")
    if review_val is not None and not isinstance(review_val, bool):
        _emit("FAIL", f"{ctx} — review '{review_val!r}' must be a boolean (true/false)")

    # capture value range (#184/#228)
    capture_val = fm.get("capture")
    if capture_val is not None and not isinstance(capture_val, bool):
        _emit("FAIL", f"{ctx} — capture '{capture_val!r}' must be a boolean (true/false)")

    # (2) extends chain (§4.2.2 + validate.md (1))
    parent_step_ids: list[str] = []
    extends_name: str | None = fm.get("extends")
    if extends_name:
        parent_path = RECIPES / f"{extends_name}.md"
        if not parent_path.exists():
            _emit("FAIL", f"{ctx} — extends: '{extends_name}' not found")
        else:
            parent_fm, _ = parse_frontmatter(parent_path)
            if parent_fm:
                # grandchild inheritance check (#42)
                if parent_fm.get("extends"):
                    _emit(
                        "WARN",
                        f"{ctx} (extends: {extends_name}) — {extends_name} also has extends"
                        f" (multi-level inheritance = grandchild extends; the parent's extends is ignored at RUN time. SKILL.md §4.2.2)",
                    )
                parent_step_ids = [
                    s.get("id", "")
                    for s in (parent_fm.get("steps") or [])
                    if isinstance(s, dict)
                ]

    # (3) steps checks
    steps = fm.get("steps")
    if not isinstance(steps, list) or len(steps) == 0:
        _emit("FAIL", f"{ctx} — steps[] is empty or invalid")
        _emit("PASS", f"{ctx}: reference checks skipped (invalid steps)")
        return

    seen_ids: set[str] = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            _emit("FAIL", f"{ctx} — steps[{i}] must be a dict")
            continue

        step_id = step.get("id") or f"[{i}]"
        step_ctx = f"{ctx}.{step_id}"

        # id required, slug format (#197/#219), uniqueness
        if not step.get("id"):
            _emit("FAIL", f"{ctx} — steps[{i}] has no id")
        else:
            if not re.fullmatch(r"[a-z][a-z0-9-]*", step_id):
                _emit(
                    "FAIL",
                    f"{step_ctx} — id '{step_id}' has an invalid format."
                    f" Use [a-z][a-z0-9-]* (lowercase alphanumerics and hyphens only, starting with a lowercase letter)",
                )
            if step_id in seen_ids:
                _emit("FAIL", f"{ctx} — steps[].id '{step_id}' is duplicated")
            seen_ids.add(step_id)

        # instruction required
        instr = step.get("instruction")
        if not instr:
            _emit("FAIL", f"{step_ctx} — instruction is missing")
        else:
            _check_exists(FACETS / "instructions" / f"{instr}.md", step_ctx, "instruction",
                          hint_dir=FACETS / "instructions")

        # personas[]
        for persona in (step.get("personas") or []):
            _resolve_persona(persona, step_ctx)

        # policies[]
        for policy in (step.get("policies") or []):
            _check_exists(FACETS / "policies" / f"{policy}.md", step_ctx, f"policies[{policy}]",
                          hint_dir=FACETS / "policies")

        # output_contract
        oc = step.get("output_contract")
        if oc:
            _check_exists(FACETS / "output-contracts" / f"{oc}.md", step_ctx, "output_contract",
                          hint_dir=FACETS / "output-contracts")

        # pattern → existence check under patterns/ (any shipped-tier brick name allowed)
        _check_pattern_or_gate(step.get("pattern"), step_ctx, "pattern")
        # gate → only the two values review-gate|acceptance-gate allowed (enum FAIL from #198; #227)
        _check_gate(step.get("gate"), step_ctx, "gate")

        # checks: type / empty-entry validation (CI adoption of #200; #218)
        checks_val = step.get("checks")
        if checks_val is not None:
            if not isinstance(checks_val, list):
                _emit(
                    "FAIL",
                    f"{step_ctx} — checks value is not a list ({checks_val!r})."
                    f" Specify checks as an array of shell commands (e.g. [\"npm test\"])",
                )
            else:
                for idx, cmd in enumerate(checks_val):
                    if cmd == "":
                        _emit(
                            "FAIL",
                            f"{step_ctx} — checks contains an empty-string entry (index {idx})",
                        )

        # condition value validation (#109/#229/#230)
        _check_condition(step.get("condition"), step_ctx, "condition")

        # max_retries type / value range (§3.5)
        max_retries = step.get("max_retries")
        if max_retries is not None:
            if not isinstance(max_retries, int) or max_retries < 1:
                _emit(
                    "FAIL",
                    f"{step_ctx} — max_retries must be an integer ≥1 (value: {max_retries!r})",
                )
            if step.get("gate") != "acceptance-gate":
                _emit(
                    "WARN",
                    f"{step_ctx} — max_retries is set on a step without gate: acceptance-gate (no effect in this context)",
                )

        # acceptance-gate + acceptance[] presence recommended
        if step.get("gate") == "acceptance-gate" and not step.get("acceptance"):
            _emit(
                "WARN",
                f"{step_ctx} — gate: acceptance-gate but acceptance[] is undefined (the gate may always pass)",
            )

        # match child step IDs against extends parent (#41)
        if parent_step_ids and step_id not in parent_step_ids and step.get("id"):
            _emit(
                "WARN",
                f"{ctx} (extends: {extends_name}) — child step `{step_id}` does not exist in parent"
                f" (possible override typo; ignore if a new step is intended. SKILL.md §4.2.2)",
            )

    # needs: broken-reference check (check A; #152)
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = step.get("id") or "?"
        needs_list = step.get("needs")
        if not needs_list:
            continue
        for needed_id in needs_list:
            if not isinstance(needed_id, str):
                continue
            if needed_id not in seen_ids:
                _emit(
                    "FAIL",
                    f"{ctx}.{step_id} — needs contains undefined step-id {needed_id!r}."
                    f" Valid step-ids: {', '.join(sorted(seen_ids))}",
                )

    _emit("PASS", f"{ctx}: OK")


# ── extends circular-reference check (#71; DFS) ──────────────────────────────
def check_extends_cycles(recipe_files: list[pathlib.Path]) -> None:
    """Detect A→B→…→A cycles via DFS (independent of the depth check in #42).

    Only looks at the shipped-tier graph (cross-tier cycles are handled by the
    Claude-side --validate). Each detected cycle is reported as FAIL exactly
    once, with its path.
    """
    parent: dict[str, str] = {}
    for path in recipe_files:
        fm, _ = parse_frontmatter(path)
        if fm and fm.get("extends"):
            parent[path.stem] = str(fm["extends"])

    reported: set[frozenset] = set()
    for start in parent:
        path_list: list[str] = []
        in_path: set[str] = set()
        node = start
        while node in parent:           # follow only while an extends target exists
            if node in in_path:         # revisiting the current path = cycle
                cycle = path_list[path_list.index(node):] + [node]
                key = frozenset(cycle)
                if key not in reported:
                    reported.add(key)
                    _emit("FAIL", f"recipe:circular-extends — circular chain: {' → '.join(cycle)}")
                break
            path_list.append(node)
            in_path.add(node)
            node = parent[node]


# ── needs: circular-dependency check (check B; #152; DFS) ────────────────────
def check_needs_cycles(recipe_files: list[pathlib.Path]) -> None:
    """Walk each recipe's needs: DAG via DFS and detect circular dependencies (#152).

    Only looks at the shipped-tier graph (cross-tier cycles are handled by the
    Claude-side --validate). Same logic and same severity (FAIL) as
    check_extends_cycles.
    """
    for recipe_path in recipe_files:
        fm, _ = parse_frontmatter(recipe_path)
        if not fm or not isinstance(fm.get("steps"), list):
            continue

        steps = fm["steps"]
        graph: dict[str, list[str]] = {}
        valid_ids: set[str] = set()
        for step in steps:
            if isinstance(step, dict) and step.get("id"):
                sid = str(step["id"])
                valid_ids.add(sid)
                needs = step.get("needs") or []
                graph[sid] = [str(n) for n in needs if isinstance(n, str) and n in valid_ids or True]

        # DFS coloring algorithm (white=unvisited / gray=in progress / black=done)
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {sid: WHITE for sid in valid_ids}
        reported: set[str] = set()

        def dfs(node: str, trail: list[str]) -> bool:
            color[node] = GRAY
            current_trail = trail + [node]
            for dep in graph.get(node, []):
                if dep not in valid_ids:
                    continue
                if color[dep] == GRAY:
                    cycle_start = current_trail.index(dep)
                    cycle = current_trail[cycle_start:] + [dep]
                    cycle_key = " → ".join(cycle)
                    if cycle_key not in reported:
                        reported.add(cycle_key)
                        _emit(
                            "FAIL",
                            f"recipe {recipe_path.stem}: needs circular dependency — {cycle_key}",
                        )
                    return True
                if color[dep] == WHITE:
                    dfs(dep, current_trail)
            color[node] = BLACK
            return False

        for sid in list(valid_ids):
            if color[sid] == WHITE:
                dfs(sid, [])
