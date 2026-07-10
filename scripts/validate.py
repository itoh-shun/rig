#!/usr/bin/env python3
"""
rig structure validator (for CI)

Mechanically checks shipped-tier recipe frontmatter, step references, extends
chains, and persona frontmatter.
Implements the (1)(2)(3) (+ (3)-b persona schema) subset of the --validate
instruction (facets/instructions/validate.md).
No Claude required — runs entirely on the filesystem.

Exit code: 0=pass / 1=has FAIL
"""

import sys
import os
import re
import json
import pathlib
import traceback

try:
    import yaml
except ImportError:
    print("[ERROR] PyYAML not found. Install it with `pip install pyyaml`.")
    sys.exit(1)

# ── path constants ───────────────────────────────────────────────────────────
ROOT     = pathlib.Path(__file__).parent.parent
SKILLS   = ROOT / "skills" / "rig"
RECIPES  = SKILLS / "recipes"
FACETS   = SKILLS / "facets"
PATTERNS = SKILLS / "patterns"
AGENTS   = ROOT / "agents"

# ── counters ─────────────────────────────────────────────────────────────────
results: list[str] = []
_pass = _warn = _fail = 0


def _emit(level: str, msg: str) -> None:
    global _pass, _warn, _fail
    if level == "PASS":
        _pass += 1
    elif level == "WARN":
        _warn += 1
    elif level == "FAIL":
        _fail += 1
    results.append(f"[{level}] {msg}")


# ── frontmatter parser ───────────────────────────────────────────────────────
def parse_frontmatter(path: pathlib.Path) -> tuple[dict | None, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text
    try:
        fm = yaml.safe_load(parts[1]) or {}
        return fm, parts[2]
    except yaml.YAMLError as exc:
        return None, str(exc)


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


# ── persona facet schema check ───────────────────────────────────────────────
def check_personas() -> None:
    """Check the frontmatter schema of shipped persona facets.

    - frontmatter exists and parses as YAML (FAIL)
    - `name` matches the path relative to personas/ (no extension, `/` separated)
      (FAIL, since recipe `personas[]` / `--persona <name>` name resolution
      would break otherwise)
    - `description` is a non-empty string (FAIL; used for catalog / --list display)
    - `inject`, if present, is a list (FAIL; declaration format of wiki reference §5)
    """
    personas_dir = FACETS / "personas"
    persona_files = sorted(personas_dir.rglob("*.md"))
    if not persona_files:
        _emit("WARN", "no .md files found in facets/personas/")
        return

    ok = 0
    for path in persona_files:
        rel_name = str(path.relative_to(personas_dir))[:-3].replace("\\", "/")
        ctx = f"persona {rel_name}"
        fm, raw = parse_frontmatter(path)

        if fm is None:
            if path.read_text(encoding="utf-8").startswith("---"):
                _emit("FAIL", f"{ctx} — frontmatter cannot be parsed (YAML error: {raw[:80]})")
            else:
                _emit("FAIL", f"{ctx} — frontmatter is missing (name/description are required)")
            continue

        bad = False
        if fm.get("name") != rel_name:
            _emit("FAIL", f"{ctx} — name '{fm.get('name')}' does not match relative path '{rel_name}'")
            bad = True
        desc = fm.get("description")
        if not isinstance(desc, str) or not desc.strip():
            _emit("FAIL", f"{ctx} — description is empty or undefined")
            bad = True
        inject = fm.get("inject")
        if inject is not None and not isinstance(inject, list):
            _emit("FAIL", f"{ctx} — inject must be a list (value: {inject!r})")
            bad = True
        elif isinstance(inject, list):
            # inject in a shipped persona must resolve within the shipped wiki tier
            # (user/project tiers do not exist in fresh installs, hence FAIL)
            wiki_dir = FACETS / "knowledge" / "wiki"
            for entry in inject:
                m = re.match(r"^\[\[([^\]|]+)(?:\|[^\]]*)?\]\]$", str(entry).strip())
                if not m:
                    _emit("FAIL", f"{ctx} — inject entry {entry!r} is not in [[slug]] format")
                    bad = True
                    continue
                slug = m.group(1)
                if not (wiki_dir / f"{slug}.md").exists():
                    _emit("FAIL", f"{ctx} — inject [[{slug}]] does not resolve to the shipped wiki"
                                  f" (skills/rig/facets/knowledge/wiki/{slug}.md)")
                    bad = True
        if not bad:
            ok += 1

    _emit("PASS", f"personas: {ok}/{len(persona_files)} schema OK")


# ── commands / agents frontmatter checks ─────────────────────────────────────
# Prevent regressions in CI of the real bug classes from v0.77 (invalid
# frontmatter YAML left all commands unregistered) and v0.78 (reserved-name
# collision with `skill`).
_RESERVED_COMMAND_NAMES = {"skill", "status"}  # collided in practice (skill) / renamed to avoid collision (status→party)


def check_commands() -> None:
    cmd_dir = ROOT / "commands"
    if not cmd_dir.is_dir():
        return
    ok = 0
    files = sorted(cmd_dir.glob("*.md"))
    for path in files:
        ctx = f"command {path.stem}"
        fm, raw = parse_frontmatter(path)
        bad = False
        if fm is None:
            _emit("FAIL", f"{ctx} — frontmatter cannot be parsed as YAML (regression class of the all-commands-unregistered bug): {raw[:80]}")
            continue
        desc = fm.get("description")
        if not isinstance(desc, str) or not desc.strip():
            _emit("FAIL", f"{ctx} — description is empty or not a string")
            bad = True
        ah = fm.get("argument-hint")
        if ah is not None and not isinstance(ah, str):
            _emit("FAIL", f"{ctx} — argument-hint '{ah!r}' must be a string (writing it as an array invites broken YAML)")
            bad = True
        if path.stem in _RESERVED_COMMAND_NAMES:
            _emit("WARN", f"{ctx} — '{path.stem}' is a name with a track record of colliding with CC built-ins (precedents: skill→forge / status→party)")
        if not bad:
            ok += 1
    _emit("PASS", f"commands: {ok}/{len(files)} frontmatter OK")


def check_agents() -> None:
    if not AGENTS.is_dir():
        return
    ok = 0
    files = sorted(AGENTS.glob("*.md"))
    for path in files:
        ctx = f"agent {path.stem}"
        fm, raw = parse_frontmatter(path)
        bad = False
        if fm is None:
            _emit("FAIL", f"{ctx} — frontmatter cannot be parsed as YAML: {raw[:80]}")
            continue
        if fm.get("name") != path.stem:
            _emit("FAIL", f"{ctx} — name '{fm.get('name')}' does not match filename '{path.stem}' (breaks subagent_type resolution)")
            bad = True
        if not isinstance(fm.get("description"), str) or not fm["description"].strip():
            _emit("FAIL", f"{ctx} — description is empty or undefined")
            bad = True
        if not fm.get("tools"):
            _emit("WARN", f"{ctx} — tools is undefined (read-only reviewers should explicitly list Read, Grep, Glob, Bash)")
        if not bad:
            ok += 1
    _emit("PASS", f"agents: {ok}/{len(files)} frontmatter OK")


# ── §2 catalog drift (mechanical implementation of validate.md (4)) ──────────
def _expand_braces(token: str) -> list[str]:
    """`a/{b,c}-d` → [`a/b-d`, `a/c-d`] (single level only; sufficient for §2 notation)."""
    m = re.search(r"\{([^{}]+)\}", token)
    if not m:
        return [token]
    out = []
    for part in m.group(1).split(","):
        out.extend(_expand_braces(token[:m.start()] + part.strip() + token[m.end():]))
    return out


def check_catalog_drift() -> None:
    """Cross-check backticked brick references in SKILL.md §2 → real files
    (ghost entries = FAIL), and real files → SKILL.md listings (missing
    entries = WARN)."""
    skill = (SKILLS / "SKILL.md").read_text(encoding="utf-8")
    s2 = skill[skill.index("## 2."):skill.index("## 3.")]

    base_map = {
        "facets/": SKILLS / "facets", "recipes/": SKILLS / "recipes",
        "patterns/": SKILLS / "patterns", "manifests/": SKILLS / "manifests",
        "agents/": AGENTS, "commands/": ROOT / "commands",
        "hooks/": ROOT / "hooks", "scripts/": ROOT / "scripts",
        "web/": ROOT / "web",
    }
    ghosts = 0
    tokens = set()
    for raw_tok in re.findall(r"`([A-Za-z0-9_{},/.-]+)`", s2):
        for prefix, base in base_map.items():
            if raw_tok.startswith(prefix):
                for tok in _expand_braces(raw_tok):
                    tokens.add((tok, base / tok[len(prefix):]))
                break
    for tok, path in sorted(tokens):
        if tok.endswith("/"):
            exists = path.is_dir()
        else:
            exists = path.exists() or path.with_suffix(".md").exists()
        if not exists:
            _emit("FAIL", f"§2 catalog — `{tok}` does not resolve to a real file (ghost entry)")
            ghosts += 1

    # bricks registered via brace notation ({a,b}-reviewer etc.) are also matched against expanded tokens
    expanded_stems = {pathlib.Path(tok).stem for tok, _ in tokens}
    missing = 0
    for sub in ("recipes", "facets/instructions", "facets/personas"):
        for f in sorted((SKILLS / sub).rglob("*.md")):
            if f.stem.startswith("_"):
                continue
            if f.stem not in skill and f.stem not in expanded_stems:
                _emit("WARN", f"§2 catalog — {sub}/{f.relative_to(SKILLS / sub)} is not listed in SKILL.md (missed listing for a pack addition?)")
                missing += 1
    _emit("PASS", f"§2 catalog drift: {len(tokens)} references ({ghosts} ghosts) / {missing} suspected missing listings")


# ── shipped wiki hygiene check (including freshness) ─────────────────────────
def check_wiki() -> None:
    """Check frontmatter hygiene and freshness (reviewed_at; 180 days) of shipped wiki pages."""
    import datetime
    wiki_dir = FACETS / "knowledge" / "wiki"
    if not wiki_dir.is_dir():
        return
    ok = 0
    pages = sorted(wiki_dir.glob("*.md"))
    for path in pages:
        ctx = f"wiki {path.stem}"
        fm, raw = parse_frontmatter(path)
        bad = False
        if fm is None:
            _emit("FAIL", f"{ctx} — frontmatter cannot be parsed (YAML error: {raw[:80]})")
            continue
        if fm.get("slug") != path.stem:
            _emit("FAIL", f"{ctx} — slug '{fm.get('slug')}' does not match filename '{path.stem}'")
            bad = True
        if fm.get("status") not in ("canonical", "draft", "deprecated"):
            _emit("FAIL", f"{ctx} — status '{fm.get('status')}' must be canonical|draft|deprecated")
            bad = True
        ra = fm.get("reviewed_at")
        if ra is not None:
            try:
                d = ra if isinstance(ra, datetime.date) else datetime.date.fromisoformat(str(ra))
                if (datetime.date.today() - d).days > 180:
                    _emit("WARN", f"{ctx} — reviewed_at is over 180 days old ({d}): review and update the content or mark it deprecated (knowledge freshness)")
            except ValueError:
                _emit("FAIL", f"{ctx} — reviewed_at '{ra}' is not in YYYY-MM-DD format")
                bad = True
        if not bad:
            ok += 1
    _emit("PASS", f"wiki: {ok}/{len(pages)} schema OK (shipped tier)")



# ── brick graph consistency check (ontology constraints; #graph) ─────────────
def check_graph() -> None:
    """Call orchestrate.py graph --json (the primary implementation of the typed graph) and check for unresolved edges.

    Instead of reimplementing the derivation logic, invoke the primary
    implementation via subprocess (avoid duplicating prose and code). Relations
    already covered by other checks (injects=check_personas / uses-*=check_recipe)
    are skipped to avoid double reporting; this check only handles
    **links-to (broken wiki cross-links) = FAIL / references & mirrors = WARN**.
    """
    import subprocess
    proc = subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).parent / "orchestrate.py"), "graph", "--json"],
        capture_output=True, text=True, env={**os.environ, "RIG_HOME": str(ROOT)})
    if proc.returncode != 0:
        _emit("FAIL", f"graph — orchestrate.py graph --json failed: {proc.stderr[:200]}")
        return
    g = json.loads(proc.stdout)
    covered = {"injects", "uses-persona", "uses-instruction", "uses-pattern",
               "gated-by", "applies-policy", "emits-contract", "extends"}
    bad = 0
    for e in g["edges"]:
        if e["resolved"] or e["rel"] in covered:
            continue
        bad += 1
        if e["rel"] == "links-to":
            _emit("FAIL", f"graph — broken wiki link: {e['from']} → [[{e['to'].split(':', 1)[1]}]] does not exist")
        elif e["rel"] == "mirrors":
            _emit("WARN", f"graph — no persona corresponding to {e['from']} (missing native-first counterpart)")
        else:
            _emit("WARN", f"graph — {e['from']} references {e['to']} but it cannot be resolved")
    if bad == 0:
        _emit("PASS", f"graph: {len(g['nodes'])} nodes / {len(g['edges'])} edges — no unresolved edges in the typed graph")


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


# ── release metadata consistency (plugin.json ⇄ CHANGELOG.md; #231) ──────────
def check_release_metadata() -> None:
    """Check that CHANGELOG.md has a `## [x.y.z]` section matching plugin.json's version.

    release.yml silently falls back to auto-generated notes when this match is
    not found (by design it does not block the release itself). The --validate
    side detects it as FAIL to prevent it from slipping in unnoticed.
    """
    plugin_path = ROOT / ".claude-plugin" / "plugin.json"
    changelog_path = ROOT / "CHANGELOG.md"
    if not plugin_path.is_file() or not changelog_path.is_file():
        return
    try:
        version = json.loads(plugin_path.read_text(encoding="utf-8"))["version"]
    except Exception as exc:
        _emit("FAIL", f"release — cannot read version from .claude-plugin/plugin.json: {exc}")
        return
    changelog = changelog_path.read_text(encoding="utf-8")
    heading = f"## [{version}]"
    if heading not in changelog:
        _emit(
            "FAIL",
            f"release — CHANGELOG.md has no \"{heading}\" section matching"
            f" the plugin.json version ({version})",
        )
    else:
        _emit("PASS", f"release: plugin.json version ({version}) ⇄ CHANGELOG.md section match")


# ── skills-lock.json consistency (/rig:import provenance record; #249) ───────
_VALID_IMPORT_MODES = ("delegate", "translate", "knowledge")


def check_skills_lock() -> None:
    """Check the schema and importedAs reference consistency of skills-lock.json.

    Silently skips when the file does not exist (same policy as the
    wiki/accumulated checks). The first stage only targets the project layer
    (directly under the calling repository).
    """
    lock_path = ROOT / "skills-lock.json"
    if not lock_path.is_file():
        return
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _emit("FAIL", f"skills-lock — cannot be parsed as JSON: {exc}")
        return
    if not isinstance(data, dict) or "version" not in data or "skills" not in data:
        _emit("FAIL", "skills-lock — top level must have version / skills keys")
        return

    skills = data["skills"]
    entries = skills.items() if isinstance(skills, dict) else enumerate(skills or [])
    ok = 0
    for key, entry in entries:
        ctx = f"skills-lock[{key}]"
        if not isinstance(entry, dict):
            _emit("FAIL", f"{ctx} — entry is not a dict")
            continue
        bad = False
        for field in ("source", "sourceType", "skillPath", "computedHash"):
            if not entry.get(field):
                _emit("FAIL", f"{ctx} — required field `{field}` is missing")
                bad = True
        mode = entry.get("mode")
        if mode is not None and mode not in _VALID_IMPORT_MODES:
            _emit("FAIL", f"{ctx} — mode '{mode}' is an invalid value. Allowed values: {', '.join(_VALID_IMPORT_MODES)}")
            bad = True
        imported_as = entry.get("importedAs")
        if imported_as is None:
            _emit("WARN", f"{ctx} — importedAs is not recorded (missing traceability of which bricks it was translated into)")
        else:
            for p in (imported_as if isinstance(imported_as, list) else [imported_as]):
                if not (ROOT / str(p)).exists():
                    _emit("FAIL", f"{ctx} — importedAs '{p}' does not exist in the repository")
                    bad = True
        if not bad:
            ok += 1
    _emit("PASS", f"skills-lock: {ok}/{len(skills)} schema OK")


# ── selftest (regression test of validate.py itself; #232) ───────────────────
def run_selftest() -> None:
    """Detect implementation drift in the FAIL/WARN decision logic via synthetic fixtures.

    Same positioning as `orchestrate.py selftest` (the doctor's own doctor).
    Writes minimal recipe frontmatter to a temporary directory instead of real
    files, runs it through `check_recipe()` as-is, and verifies it does/does not
    FAIL as expected (the signature of `check_recipe` stays unchanged).
    The first stage focuses on the 4 classes that already caused real damage:
    #227 (gate enum values), #228 (boolean types; 2 representative cases),
    #219 (id slug format), and #218 (checks type / empty entries).
    """
    import tempfile

    def recipe(name: str, extra_top: str, steps_yaml: str) -> str:
        return (
            f"---\nname: {name}\ndescription: selftest fixture\nscope: project\n"
            f"autonomy: interactive\n{extra_top}steps:\n{steps_yaml}---\n\n# {name}\n"
        )

    scenarios: list[tuple[str, bool, str]] = [
        ("gate-ok", False, recipe("gate-ok", "",
            "  - id: verify\n    instruction: verify\n    gate: acceptance-gate\n")),
        ("gate-bad-serial", True, recipe("gate-bad-serial", "",
            "  - id: verify\n    instruction: verify\n    gate: serial\n")),
        ("bool-bad-capture", True, recipe("bool-bad-capture", 'capture: "yes"\n',
            "  - id: implement\n    instruction: implement\n")),
        ("bool-bad-design", True, recipe("bool-bad-design", "design: 1\n",
            "  - id: implement\n    instruction: implement\n")),
        ("id-ok", False, recipe("id-ok", "",
            "  - id: valid-step-2\n    instruction: implement\n")),
        ("id-bad-space", True, recipe("id-bad-space", "",
            '  - id: "My Step"\n    instruction: implement\n')),
        ("checks-ok", False, recipe("checks-ok", "",
            '  - id: verify\n    instruction: verify\n    checks: ["npm test"]\n')),
        ("checks-bad-scalar", True, recipe("checks-bad-scalar", "",
            '  - id: verify\n    instruction: verify\n    checks: "npm test"\n')),
        ("checks-bad-empty", True, recipe("checks-bad-empty", "",
            '  - id: verify\n    instruction: verify\n    checks: ["npm test", ""]\n')),
    ]

    ok = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        for stem, expect_fail, content in scenarios:
            fixture = tmp_path / f"{stem}.md"
            fixture.write_text(content, encoding="utf-8")
            start = len(results)
            try:
                check_recipe(fixture)
            except Exception:
                _emit("FAIL", f"selftest '{stem}' — error while running check_recipe:\n{traceback.format_exc()}")
            got_fail = any(line.startswith("[FAIL]") for line in results[start:])
            passed = got_fail == expect_fail
            ok += passed
            print(f"  [{'OK' if passed else 'NG'}] {stem}"
                  f" (expected: {'FAIL' if expect_fail else 'no-FAIL'} / actual: {'FAIL' if got_fail else 'no-FAIL'})")

    total = len(scenarios)
    print(f"\nselftest: {ok}/{total} scenarios OK")
    sys.exit(0 if ok == total else 1)


# ── main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        run_selftest()
        return

    recipe_files = sorted(RECIPES.glob("*.md"))
    if not recipe_files:
        print("[WARN] no .md files found in recipes/")
        sys.exit(0)

    for recipe_path in recipe_files:
        try:
            check_recipe(recipe_path)
        except Exception:
            _emit("FAIL", f"recipe {recipe_path.stem} — unexpected error:\n{traceback.format_exc()}")

    try:
        check_personas()
    except Exception:
        _emit("FAIL", f"persona schema check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_commands()
    except Exception:
        _emit("FAIL", f"commands check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_agents()
    except Exception:
        _emit("FAIL", f"agents check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_catalog_drift()
    except Exception:
        _emit("FAIL", f"§2 catalog drift check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_wiki()
    except Exception:
        _emit("FAIL", f"wiki hygiene check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_graph()
    except Exception:
        _emit("FAIL", f"graph consistency check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_extends_cycles(recipe_files)
    except Exception:
        _emit("FAIL", f"extends cycle check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_needs_cycles(recipe_files)
    except Exception:
        _emit("FAIL", f"needs cycle check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_release_metadata()
    except Exception:
        _emit("FAIL", f"release metadata check — unexpected error:\n{traceback.format_exc()}")

    try:
        check_skills_lock()
    except Exception:
        _emit("FAIL", f"skills-lock check — unexpected error:\n{traceback.format_exc()}")

    print("## rig --validate report (CI / shipped tier)\n")
    for line in results:
        print(line)
    print()
    print(f"PASS: {_pass} / WARN: {_warn} / FAIL: {_fail}")

    if _fail > 0:
        print("\nFAILED: one or more FAIL results")
        sys.exit(1)
    elif _warn > 0:
        print("\nPASSED (with WARNs to address)")
    else:
        print("\nPASSED")


if __name__ == "__main__":
    main()
