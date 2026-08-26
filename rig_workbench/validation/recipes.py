"""validation recipes: recipe frontmatter/step/extends/needs checks (split from scripts/validate.py)."""

import pathlib
import re

from rig_workbench.orchestrate.gates import is_runtime_gate, validate_executable_recipe
from rig_workbench.workbench.config import GATE_PRESETS

from .config import AGENTS, FACETS, PATTERNS, RECIPES, ROOT
from .state import _emit, parse_frontmatter

#: Every criterion id any preset can put on a task's gate. Derived from `GATE_PRESETS`, never
#: re-typed here: a second copy of the vocabulary is a copy that drifts (and the one in
#: `facets/instructions/acceptance-check.md` already had, by eleven criteria, before it was
#: replaced with an instruction to read the task's gate).
PRESET_CRITERION_IDS = frozenset(
    criterion for preset in GATE_PRESETS.values() for criterion in preset
)

#: An `acceptance[]` entry in id-form: `criterion_id — 説明`. The ` — ` separator is the
#: convention `facets/instructions/acceptance-check.md` states; anything else is prose-form,
#: which carries no vocabulary constraint at all (two thirds of the shipped catalogue is
#: prose-form, and a rule demanding ids everywhere would have rejected all of it).
_ACCEPTANCE_ID_FORM = re.compile(r"^\s*([a-z][a-z0-9_]*)\s+\u2014\s")  # \u2014 is the em dash of the ` — ` separator


def _check_acceptance_forms(step: dict, step_ctx: str) -> None:
    """A step's `acceptance[]` is entirely id-form or entirely prose-form (#497 C3).

    The list is a WORK LIST — the criteria this flow's own steps produce evidence for —
    never the condition for acceptance. What the form governs is vocabulary: an id-form
    entry claims a criterion that `build_acceptance()` will actually put on the task's
    gate, so a misspelled or invented id is a claim nothing can ever record. Prose-form
    entries name work no preset knows about and are left alone.
    """
    entries = step.get("acceptance")
    if entries is None:
        return
    if not isinstance(entries, list):
        _emit("FAIL", f"{step_ctx} — acceptance value is not a list ({entries!r})."
                      " Specify acceptance as an array of strings")
        return
    forms: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, str) or not entry.strip():
            _emit("FAIL", f"{step_ctx} — acceptance[{index}] is not a non-empty string"
                          f" ({entry!r})")
            continue
        match = _ACCEPTANCE_ID_FORM.match(entry)
        if match is None:
            forms.append("prose")
            continue
        forms.append("id")
        criterion_id = match.group(1)
        if criterion_id not in PRESET_CRITERION_IDS:
            _emit(
                "FAIL",
                f"{step_ctx} — acceptance[{index}] declares criterion id"
                f" `{criterion_id}`, which no gate preset defines. `wb gate --set` rejects"
                " any name not already on the task's gate, so this id can never be"
                " recorded. Use an id from `rig-wb wb gates`, or write the entry as prose"
                " (no `id — ` prefix) if it names work no preset knows about.",
            )
    if len(set(forms)) > 1:
        ids = [entries[i] for i, f in enumerate(forms) if f == "id"]
        _emit(
            "FAIL",
            f"{step_ctx} — acceptance[] mixes id-form and prose-form entries"
            f" ({forms.count('id')} id-form, {forms.count('prose')} prose-form; first"
            f" id-form: {ids[0]!r}). A step's list is entirely one form or entirely the"
            " other — the form decides whether the entries are checked against the gate"
            " vocabulary, and a mixed list is checked as neither.",
        )


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


def _check_model_field(step: dict, ctx: str) -> None:
    """model / verifier_model must be strings (§3.5; #362).

    Both reach the provider as `argv += ["--model", value]`, so a non-string
    fails at subprocess time rather than here. An empty string is worse than
    wrong: it is falsy, so the provider quietly uses its default and the
    recipe's explicit choice disappears without a word.
    """
    for field in ("model", "verifier_model"):
        value = step.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            _emit(
                "FAIL",
                f"{ctx} — {field} must be a string (value: {value!r})."
                f" e.g. {field}: claude-opus-5",
            )
        elif not value.strip():
            _emit(
                "WARN",
                f"{ctx} — {field} is empty; the provider silently falls back to its"
                f" default model. Remove the key or name a model.",
            )


_AUTO_ROUTE_SIZES = ("S", "M", "L", "XL")


def _check_auto_route(value: object, ctx: str) -> None:
    """auto_route.candidates schema + cheapest-first ordering (#358).

    resolve_auto_route() scans candidates in declared order and takes the first
    whose max_size covers the current size. That makes the declared order part
    of the behaviour: list an expensive tier first and it wins every time a
    cheaper one would have done, with nothing to show for it at run time.
    """
    if value is None:
        return
    if not isinstance(value, dict):
        _emit("FAIL", f"{ctx} — auto_route must be a mapping with a candidates list (value: {value!r})")
        return

    candidates = value.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        _emit("FAIL", f"{ctx} — auto_route.candidates must be a non-empty list (value: {candidates!r})")
        return

    ranks: list[int] = []
    for index, candidate in enumerate(candidates):
        entry_ctx = f"{ctx} — auto_route.candidates[{index}]"
        if not isinstance(candidate, dict):
            _emit("FAIL", f"{entry_ctx} is not a mapping (value: {candidate!r})")
            return
        for field in ("model", "cost_tier"):
            field_value = candidate.get(field)
            if not isinstance(field_value, str) or not field_value.strip():
                _emit("FAIL", f"{entry_ctx} — {field} must be a non-empty string (value: {field_value!r})")
                return
        max_size = candidate.get("max_size")
        if max_size not in _AUTO_ROUTE_SIZES:
            _emit(
                "FAIL",
                f"{entry_ctx} — max_size must be one of {'/'.join(_AUTO_ROUTE_SIZES)}"
                f" (value: {max_size!r}). An unrecognised value defaults to XL, so this"
                f" candidate would win every route.",
            )
            return
        ranks.append(_AUTO_ROUTE_SIZES.index(max_size))

    if ranks != sorted(ranks):
        order = ", ".join(str(candidate.get("max_size")) for candidate in candidates)
        _emit(
            "FAIL",
            f"{ctx} — auto_route.candidates must be ordered cheapest-first by max_size"
            f" (declared: {order}). Selection takes the first candidate large enough,"
            f" so an out-of-order list routes to a costlier model than declared.",
        )


# ── per-recipe check ─────────────────────────────────────────────────────────
_ROLE_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def _check_stage_governance(step: dict, ctx: str) -> None:
    """`actor` (an org role owning the stage) and `human_gate` (halt for a person).

    Shape only — whether the named role exists is a property of whichever org
    policy the recipe runs under, and a shipped recipe has none. Enforcing that
    here would make a portable recipe unvalidatable; `orchestrate` checks it at
    run time, against the policy actually in effect.
    """
    from rig_workbench.govern.stage import StageConfigError, parse_human_gate

    actor = step.get("actor")
    if actor is not None:
        if not isinstance(actor, str) or not _ROLE_RE.match(actor):
            _emit("FAIL", f"{ctx} — actor must be a role name (^[a-z][a-z0-9-]*$), got {actor!r}")
    try:
        rule = parse_human_gate(step.get("human_gate"), where=ctx)
    except StageConfigError as e:
        _emit("FAIL", str(e))
        return
    if rule is not None and not rule["roles"] and not actor:
        _emit("WARN",
              f"{ctx} — human_gate names no roles and the step declares no actor: "
              "anyone holding `approve` can clear it. Name the role that should sign off")
    if actor and rule is None:
        # rig's own doctrine, applied to itself: declaring an owner is not the same as
        # requiring one. Without a human_gate nothing ever asks that role for anything.
        _emit("WARN",
              f"{ctx} — actor `{actor}` is declared but the step has no human_gate, so the "
              "ownership is documentation only (nothing asks that role to sign off)")


def _check_max_retries(step: dict, ctx: str) -> None:
    """`max_retries` — the retry budget K the runner spends before escalating (§3.5).

    K is read on the *generic* failure path of `runstate.compute_next`, not inside
    any one gate handler, so it governs every step whose gate can report a failure.
    Measured by driving `compute_next` with a gate that keeps failing:

        gate=acceptance-gate  K=1 → 1 retry then ESCALATE / K=3 → 3
        gate=review-gate      K=1 → 1 retry then ESCALATE / K=3 → 3
        gate=none, checks=[]  K=1 → DONE / K=3 → DONE      (0 retries either way)
        gate=none, checks=[…] K=1 → 1 retry then ESCALATE / K=3 → 3

    So the only step where K is dead weight is one that can never produce a
    failure at all: no runtime gate *and* no checks. `gate_outcome` returns
    "pass" for such a step even with a failing verdict recorded, and a
    non-runtime gate string ("—", a custom pattern name) returns "unsupported",
    which BLOCKs before the retry path is reached. Warning on "not
    acceptance-gate" instead would advise deleting a working safety property —
    `adaptive-bugfix.targeted-review` carries `max_retries: 1` on a review-gate
    step precisely so a failed review stops rather than retries.
    """
    max_retries = step.get("max_retries")
    if max_retries is None:
        return
    if not isinstance(max_retries, int) or max_retries < 1:
        _emit("FAIL", f"{ctx} — max_retries must be an integer ≥1 (value: {max_retries!r})")
    if not is_runtime_gate(step.get("gate")) and not step.get("checks"):
        _emit(
            "WARN",
            f"{ctx} — max_retries has no effect on this step: it declares neither a runtime"
            " gate (acceptance-gate/review-gate) nor checks[], so nothing can report a gate"
            " failure and the retry budget is never read. K does apply to any gated step"
            " (review-gate included) and to any step with checks[]",
        )


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
    execution = validate_executable_recipe(fm)
    for error in execution["errors"]:
        _emit("FAIL", f"{ctx} — {error}")

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

        # model / verifier_model type validation (§3.5; #362)
        _check_model_field(step, step_ctx)

        # auto_route.candidates schema + cheapest-first ordering (#358)
        _check_auto_route(step.get("auto_route"), step_ctx)

        # condition value validation (#109/#229/#230)
        _check_condition(step.get("condition"), step_ctx, "condition")

        # actor / human_gate — the v2.1 stage-governance fields (§3.5)
        _check_stage_governance(step, step_ctx)

        # max_retries type / value range / effective context (§3.5)
        _check_max_retries(step, step_ctx)

        # acceptance[] form/vocabulary (#497 C3)
        _check_acceptance_forms(step, step_ctx)

        # acceptance-gate + acceptance[] presence recommended.
        # The old wording was "(the gate may always pass)". That stopped being true with
        # #496: a runtime-gated step with no verdict now returns `incomplete`, so an empty
        # acceptance[] does not buy a free pass — it buys a verifier that is asked to judge
        # a step against nothing in particular.
        if step.get("gate") == "acceptance-gate" and not step.get("acceptance"):
            _emit(
                "WARN",
                f"{step_ctx} — gate: acceptance-gate but acceptance[] is undefined"
                " (the verifier is given no criteria to answer, and the run record will"
                " name nothing it judged). acceptance[] is this flow's WORK LIST — the"
                " criteria its own steps produce evidence for — not the condition for"
                " acceptance: that is the task's gate, built from the presets by"
                " `build_acceptance()`, which never reads a recipe.",
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
