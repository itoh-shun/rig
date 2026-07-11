"""orchestrate recipes: recipe loading + RESOLVE reference implementation (split from scripts/orchestrate.py)."""

import sys
import os
import re
import pathlib
import subprocess

try:
    import yaml
except ImportError:
    # Don't kill importers (pytest collection, library use) at import time —
    # fail with the CLI hint on first actual use instead (parse_frontmatter).
    yaml = None

from . import config

# ── Project-recipe trust gate ─────────────────────────────────────────────────
# A repository can ship `.rig/recipes/*.md` that overlays a shipped recipe of
# the same name, and a recipe's `checks:` lines run as shell commands in the
# invocation cwd. Cloning a repo must therefore never be enough to get its
# commands executed: loading a project-local recipe requires explicit consent.
#
# Consent, in precedence order:
#   1. a recorded content hash in the trust store (a previously allowed,
#      unchanged file passes silently; any edit re-requires consent)
#   2. `--allow-project-recipes` on the command line, or
#      `RIG_ALLOW_PROJECT_RECIPES=1` in the environment — both record the
#      hash so later runs hit (1)
# Anything else refuses with instructions. Shipped (RIG_HOME) and org-tier
# recipes are exempt: both locations are configured by the user, not by the
# repository being worked on.

def _trust_store_path() -> pathlib.Path:
    env = os.environ.get("RIG_TRUST_STORE")
    if env:
        return pathlib.Path(env).expanduser()
    return pathlib.Path.home() / ".claude" / "rig" / "trusted-recipes.json"


def _load_trust_store() -> dict:
    p = _trust_store_path()
    if not p.exists():
        return {}
    try:
        import json
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except (ValueError, OSError):
        return {}


def _record_trust(path: pathlib.Path, digest: str) -> None:
    import json
    p = _trust_store_path()
    store = _load_trust_store()
    store[str(path)] = digest
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _is_project_recipe(path: pathlib.Path) -> bool:
    try:
        overlay = config.PROJECT_RECIPES.resolve()
        return path.resolve().is_relative_to(overlay)
    except OSError:
        return False


def ensure_recipe_trusted(path: pathlib.Path) -> pathlib.Path:
    """Consent gate for project-local recipe overlays. Returns path if allowed, exits otherwise."""
    if not _is_project_recipe(path):
        return path
    import hashlib
    resolved = path.resolve()
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if _load_trust_store().get(str(resolved)) == digest:
        return path
    allowed = ("--allow-project-recipes" in sys.argv
               or os.environ.get("RIG_ALLOW_PROJECT_RECIPES") == "1")
    if allowed:
        _record_trust(resolved, digest)
        print(f"[trust] project recipe allowed and recorded: {resolved}")
        return path
    print(f"[ERROR] untrusted project-local recipe: {resolved}\n"
          f"  Recipes under <cwd>/.rig/recipes/ come from the repository you are working\n"
          f"  on, and their `checks:` lines execute as shell commands. First use requires\n"
          f"  explicit consent:\n"
          f"    re-run with --allow-project-recipes   (records a content hash; silent next time)\n"
          f"    or set RIG_ALLOW_PROJECT_RECIPES=1\n"
          f"  Review the file first: {resolved}\n"
          f"  Trust store: {_trust_store_path()}")
    sys.exit(2)


# ── Project-manifest trust gate ───────────────────────────────────────────────
# The project manifest `.claude/rig.md` is repo-controlled too (Rules-File-
# Backdoor / AIShellJack class): it adds a recipe search tier (org_dir), sets
# default flags/personas, and its lint:/build:/test: commands are eval'd by
# the shipped pre-commit/pre-push git hooks. Same trust model as recipes
# (content hash in the shared trust store), separate consent switch.

# (path, digest) pairs already warned about in this process — load_manifest()
# sits on hot paths (plan, every recipe resolve), so warn once, not per call.
_warned_manifests: set[tuple[str, str]] = set()


def ensure_manifest_trusted(path: pathlib.Path, require: bool = False) -> bool:
    """Consent gate for the project manifest `.claude/rig.md`. True = usable.

    Mirrors ensure_recipe_trusted (same trust store, hash-recorded consent via
    `--allow-project-manifest` in argv or `RIG_ALLOW_PROJECT_MANIFEST=1`; an
    unchanged file passes silently, any edit re-requires consent) but with a
    deliberately different failure mode:

    - Recipes execute shell commands (`checks:`) the moment they load, so an
      untrusted recipe must refuse HARD (sys.exit) — running it at all is the
      hazard.
    - The manifest only contributes defaults (org_dir search tier, default
      flags/personas, size thresholds); ignoring it degrades behavior to
      "no manifest present", which is always safe. load_manifest() is also on
      hot paths (plan etc.), so an untrusted manifest degrades SOFT: warn one
      line, return False, and the caller behaves as if the file were absent.

    Exception: `require=True` (for command paths where the user explicitly
    asked for manifest-driven behavior) exits 2 with the full consent
    instructions, like the recipe gate.
    """
    import hashlib
    resolved = path.resolve()
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if _load_trust_store().get(str(resolved)) == digest:
        return True
    allowed = ("--allow-project-manifest" in sys.argv
               or os.environ.get("RIG_ALLOW_PROJECT_MANIFEST") == "1")
    if allowed:
        _record_trust(resolved, digest)
        print(f"[trust] project manifest allowed and recorded: {resolved}")
        return True
    if require:
        print(f"[ERROR] untrusted project manifest: {resolved}\n"
              f"  The manifest comes from the repository you are working on and drives\n"
              f"  recipe search paths, default flags/personas, and the git hooks'\n"
              f"  lint/build/test commands. First use requires explicit consent:\n"
              f"    re-run with --allow-project-manifest   (records a content hash; silent next time)\n"
              f"    or set RIG_ALLOW_PROJECT_MANIFEST=1\n"
              f"  Review the file first: {resolved}\n"
              f"  Trust store: {_trust_store_path()}")
        sys.exit(2)
    key = (str(resolved), digest)
    if key not in _warned_manifests:
        _warned_manifests.add(key)
        print(f"[WARN] untrusted project manifest ignored: {resolved} "
              f"(consent: --allow-project-manifest or RIG_ALLOW_PROJECT_MANIFEST=1)")
    return False


# ── Recipe loading ────────────────────────────────────────────────────────────
def parse_frontmatter(path: pathlib.Path) -> dict:
    if yaml is None:
        print("[ERROR] PyYAML not found. `pip install pyyaml`.")
        sys.exit(1)
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    return yaml.safe_load(parts[1]) or {}


def load_steps(fm: dict) -> list[dict]:
    """Extract the deterministic step definition list from recipe frontmatter (pure function)."""
    out = []
    for s in (fm.get("steps") or []):
        if not isinstance(s, dict):
            continue
        gate = s.get("gate")
        out.append({
            "id": s.get("id"),
            "instruction": s.get("instruction"),
            "gate": None if gate in (None, "—", "-") else gate,
            "pattern": s.get("pattern"),
            "personas": list(s.get("personas") or []),       # roles of parallel verifiers
            "needs": list(s.get("needs") or []),             # optional: dependency steps (DAG parallelism)
            "acceptance": list(s.get("acceptance") or []),
            "checks": list(s.get("checks") or []),          # optional: machine-verification commands
            "max_retries": s.get("max_retries") or config.DEFAULT_K,
            "model": s.get("model"),                        # optional: generator model for this step
            "verifier_model": s.get("verifier_model"),      # optional: verifier model for this step (separate assignment)
            "output_contract": s.get("output_contract"),
            "condition": s.get("condition"),                 # optional: conditional step (size/flag)
        })
    return out


# ── RESOLVE reference implementation (extends merge; badge/steps field derivation) ──
# Deterministic reference implementation of SKILL.md §4.2.2 (extends, one level at a time)
# and facets/instructions/list.md (fixed badge order, steps: field). Lets CI (selftest Q)
# golden-verify the prose engine's display rules — phase 1 of codifying RESOLVE.

EXTENDS_MAX_DEPTH = 5  # inheritance that is too deep collapses cognitive economy, so CI FAILs it (#193)


def _resolve_extends_chain(fm: dict, recipe_path: pathlib.Path,
                            warnings: list[str]) -> list[tuple[str | None, dict]]:
    """Walk the extends chain leaf -> root and return [(name, fm), ...].

    Cycles (A->B->A etc.) and depth overruns (EXTENDS_MAX_DEPTH) emit a warning and cut off.
    The pair's name is the ancestor's name found along the way (the leaf itself is None).
    """
    chain: list[tuple[str | None, dict]] = [(None, fm)]
    trail: list[str] = [recipe_path.stem]   # ordered inheritance path (for cycle messages)
    visited: set[str] = {recipe_path.stem}
    current_fm = fm
    current_path = recipe_path
    while True:
        parent_name = current_fm.get("extends")
        if not parent_name:
            return chain
        if parent_name in visited:
            warnings.append(f"extends: circular inheritance detected ({' → '.join(trail)} → {parent_name}); "
                            f"cutting the chain off here")
            return chain
        if len(chain) >= EXTENDS_MAX_DEPTH:
            warnings.append(f"extends: inheritance depth limit {EXTENDS_MAX_DEPTH} exceeded "
                            f"(ignoring '{parent_name}' and beyond). Keep chains shallow for cognitive economy")
            return chain
        parent_path = None
        fname = f"{parent_name}.md"
        for base in (current_path.parent, config.PROJECT_RECIPES, config.RECIPES):
            cand = base / fname
            if cand.exists():
                parent_path = cand
                break
        if parent_path is None:
            warnings.append(f"extends: cannot resolve '{parent_name}' (reached via {' → '.join(trail)})")
            return chain
        ensure_recipe_trusted(parent_path)
        parent_fm = parse_frontmatter(parent_path)
        chain.append((parent_name, parent_fm))
        visited.add(parent_name)
        trail.append(parent_name)
        current_fm = parent_fm
        current_path = parent_path


def resolve_extends(fm: dict, recipe_path: pathlib.Path) -> tuple[dict, list[str]]:
    """Resolve extends up to N levels; return frontmatter with finalized steps plus warnings (pure-function style).

    Merge rules (§4.2.2):
      - Collect the chain in [leaf, parent, grandparent, ..., root] order
      - Start from the root's steps and apply overrides ancestor -> parent -> child
      - At each layer, `remove: true` statically drops the inherited step, an existing id overrides, a new id appends
      - Circular inheritance and the depth limit (EXTENDS_MAX_DEPTH) warn and cut off
      - Origin markers from inheritance end up as "inherited" / "override" / "added"
    """
    warnings: list[str] = []
    raw_steps = [s for s in (fm.get("steps") or []) if isinstance(s, dict)]
    if not fm.get("extends"):
        for s in raw_steps:
            s.setdefault("_origin", None)
        return fm, warnings

    chain = _resolve_extends_chain(fm, recipe_path, warnings)
    if len(chain) == 1:
        # extends is declared but resolution failed (not found / cycle / depth exceeded)
        for s in raw_steps:
            s.setdefault("_origin", None)
        return fm, warnings

    # Use the root ancestor's steps as the base, marked "inherited"
    root_fm = chain[-1][1]
    merged: list[dict] = []
    for ps in (root_fm.get("steps") or []):
        if isinstance(ps, dict):
            m = dict(ps)
            m["_origin"] = "inherited"
            merged.append(m)

    # Apply overrides ancestor -> parent -> child (chain is leaf-first; reverse puts root first)
    for name, layer_fm in reversed(chain[:-1]):  # everything but root, in root->leaf order
        index = {s.get("id"): i for i, s in enumerate(merged)}
        for cs in (layer_fm.get("steps") or []):
            if not isinstance(cs, dict):
                continue
            cid = cs.get("id")
            if cs.get("remove") is True:
                if cid in index:
                    merged = [s for s in merged if s.get("id") != cid]
                    index = {s.get("id"): i for i, s in enumerate(merged)}
                else:
                    layer_label = name or "leaf"
                    warnings.append(f"remove: true step '{cid}' does not exist in the inherited base "
                                    f"(declared by {layer_label}; #144 WARN)")
                continue
            m = dict(cs)
            if cid in index:
                m["_origin"] = "override"
                merged[index[cid]] = m
            else:
                m["_origin"] = "added"
                merged.append(m)
                index[cid] = len(merged) - 1

    # Top-level keys: layer the whole chain with the leaf winning (root -> ... -> leaf)
    out: dict = {}
    for _, layer_fm in reversed(chain):
        out.update({k: v for k, v in layer_fm.items() if k != "steps"})
    out["steps"] = merged
    return out, warnings


def _abbrev_condition(cond: str) -> str:
    """Convert a condition value to the steps: field abbreviation (list.md #160; max 20 chars)."""
    flags = re.findall(r"--[a-z][a-z0-9-]*", cond or "")
    # U+FF1A = full-width colon; conditions written in Japanese recipes may use it
    m = re.search(r"size\s*[:\uff1a]?\s*([SMLX]+\+?)", cond or "")
    parts = flags + ([m.group(1)] if m else [])
    return ("|".join(parts) or "cond")[:20]


def derive_badges(fm: dict, steps: list[dict]) -> list[str]:
    """Derive the --list badges in fixed order (1:1 with the ordering in facets/instructions/list.md)."""
    badges: list[str] = []
    if fm.get("tdd") is True:
        badges.append("tdd")
    if any(s.get("gate") == "acceptance-gate" for s in steps):
        badges.append("gated")
    if fm.get("backend") == "workflow":
        badges.append("workflow")
    if fm.get("no_default_personas") is True:
        badges.append("no-defaults")
    if fm.get("orchestrate") is True:
        badges.append("orchestrate")
    elif any(s.get("checks") or s.get("needs") for s in steps):
        badges.append("orchestrate(auto)")
    if fm.get("cross_llm") is True:
        badges.append("cross-llm")
    if fm.get("no_capture") is True:
        badges.append("no-capture")
    if fm.get("adversarial") is True:
        badges.append("adversarial")
    if fm.get("visual") is True:
        badges.append("visual")
    if fm.get("autonomy") == "autonomous":
        badges.append("autonomous")
    if fm.get("no_orchestrate") is True:
        badges.append("no-orchestrate")
    if fm.get("design") is True:
        badges.append("design")
    if fm.get("review") is True:
        badges.append("review")
    if fm.get("capture") is True:
        badges.append("capture")
    if fm.get("verify_findings") is True:
        badges.append("verify-findings")
    return badges


def derive_steps_field(steps: list[dict]) -> str:
    """Derive the steps: field for --list / catalog (id list + condition abbreviations) (list.md #79/#160)."""
    parts = []
    for s in steps:
        sid = s.get("id") or "?"
        cond = s.get("condition")
        parts.append(f"{sid}?[{_abbrev_condition(cond)}]" if cond else sid)
    return ", ".join(parts)


# ── RESOLVE reference implementation phase 2 (condition evaluation, size classing, slicing, flag precedence) ──
# Deterministic reference implementation of SKILL.md §4.3 (flag override), §4.3.1
# (--only/--from/--to/--skip), and §4.4 (size-aware). Golden-verified by selftest R.

_SIZE_RANK = {"S": 0, "M": 1, "L": 2, "XL": 3}

# recipe frontmatter key -> equivalent flag (the §4.3 "key interpretation" set)
_KEY_TO_FLAG = {
    "tdd": "--tdd", "design": "--design", "review": "--review", "visual": "--visual",
    "adversarial": "--adversarial", "cross_llm": "--cross-llm", "orchestrate": "--orchestrate",
    "no_orchestrate": "--no-orchestrate", "no_capture": "--no-capture", "capture": "--capture",
    "no_default_personas": "--no-default-personas", "verify_findings": "--verify-findings",
}


def git_diff_lines() -> int | None:
    """Total added+removed lines from `git diff HEAD --numstat` (staged + unstaged; §4.4/#185). None if unavailable."""
    try:
        r = subprocess.run(["git", "diff", "HEAD", "--numstat"],
                           capture_output=True, text=True, timeout=10, cwd=config.INVOCATION_CWD)
        if r.returncode != 0:
            return None
        total = 0
        for line in r.stdout.splitlines():
            cols = line.split("\t")
            if len(cols) >= 2:
                total += (int(cols[0]) if cols[0].isdigit() else 0)
                total += (int(cols[1]) if cols[1].isdigit() else 0)
        return total
    except Exception:
        return None


def load_manifest(require: bool = False) -> dict:
    """Read the frontmatter of `<cwd>/.claude/rig.md` (empty dict if absent; §4.1).

    The manifest is gated by ensure_manifest_trusted() BEFORE any value is
    used: untrusted content is not parsed at all and {} is returned (fail-safe:
    behave as if no manifest exists) with a one-line warning — no hard exit,
    because this runs on hot paths (plan etc.) and the manifest only supplies
    defaults. `require=True` instead exits 2 with consent instructions, for
    command paths where the user explicitly asked for manifest-driven behavior.
    """
    path = config.INVOCATION_CWD / ".claude" / "rig.md"
    if not path.exists():
        return {}
    try:
        if not ensure_manifest_trusted(path, require=require):
            return {}
        fm = parse_frontmatter(path)
        return fm if isinstance(fm, dict) else {}
    except SystemExit:
        raise
    except Exception:
        return {}


def size_class(diff_lines: int | None, thresholds: dict | None = None) -> str:
    """diff added+removed lines -> size class (§4.4; unknown diff defaults to S)."""
    th = {"S_max": 100, "M_max": 200, "L_max": 400}
    th.update(thresholds or {})
    if diff_lines is None:
        return "S"
    if diff_lines <= th["S_max"]:
        return "S"
    if diff_lines <= th["M_max"]:
        return "M"
    if diff_lines <= th["L_max"]:
        return "L"
    return "XL"


def evaluate_condition(cond: str | None, flags: set[str], size: str) -> tuple[bool, str]:
    """Evaluate a condition expression (e.g. "--design or size L+") — flag component OR size component.

    A condition with neither component, or an uninterpretable one, is always OFF (same handling as --validate #109).
    """
    if not cond:
        return True, "no condition"
    cond_flags = re.findall(r"--[a-z][a-z0-9-]*", cond)
    hit = sorted(set(cond_flags) & flags)
    if hit:
        return True, f"resolved by flag ({' '.join(hit)})"
    # U+FF1A = full-width colon; conditions written in Japanese recipes may use it
    m = re.search(r"size\s*[:\uff1a]?\s*([SMLX]+)\+?", cond)
    if m and m.group(1) in _SIZE_RANK:
        need = m.group(1)
        if _SIZE_RANK[size] >= _SIZE_RANK[need]:
            return True, f"size {need}+ met (size {size})"
        return False, f"size {need}+ not met (size {size})"
    if cond_flags:
        return False, f"flag not set ({' '.join(sorted(set(cond_flags)))})"
    return False, "invalid condition (always OFF; #109)"


def _levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _suggest(bad: str, ids: list[str]) -> list[str]:
    cands = sorted((d, i) for i in ids if (d := _levenshtein(bad, i)) <= 2)
    return [i for _, i in cands[:3]]


def resolve_effective(recipe_path: pathlib.Path, flags: list[str] | None = None,
                      diff_lines: int | None = None,
                      thresholds: dict | None = None,
                      manifest: dict | None = None) -> dict:
    """Return RESOLVE's final result: the executable step set after flag overrides, condition evaluation, and slicing.

    Deterministic implementation of §4.3/§4.3.1/§4.4. Any errors mean the run cannot proceed
    (same as the prose engine's ERROR stop). The manifest (§4.1) contributes size_thresholds
    and default_orchestrate.
    """
    plan = resolve_plan_json(recipe_path)
    fm = parse_frontmatter(recipe_path)
    manifest = manifest or {}
    if thresholds is None and isinstance(manifest.get("size_thresholds"), dict):
        thresholds = manifest["size_thresholds"]
    warnings = list(plan["warnings"])
    errors: list[str] = []

    # (1) effective flag set = explicit flags ∪ recipe-key equivalent flags (§4.3 "key interpretation")
    fset = set(flags or [])
    for key, flg in _KEY_TO_FLAG.items():
        if fm.get(key) is True:
            fset.add(flg)
    if fm.get("autonomy") == "autonomous":
        fset.add("--autonomous")
    if fm.get("backend") == "workflow":
        fset.add("--workflow")

    # (2) size classification (§4.4) and condition evaluation
    size = size_class(diff_lines, thresholds)
    steps = [dict(s) for s in plan["steps"]]
    for s in steps:
        s["active"], s["why"] = evaluate_condition(s.get("condition"), fset, size)

    ids = [s["id"] for s in steps]
    active_ids = [s["id"] for s in steps if s["active"]]

    # (3) slicing (§4.3.1; applied to the list after condition evaluation)
    def _slice_val(name: str) -> str | None:
        lst = flags or []
        return lst[lst.index(name) + 1] if name in lst and lst.index(name) + 1 < len(lst) else None

    only, frm, to = _slice_val("--only"), _slice_val("--from"), _slice_val("--to")
    skips = [v for i, v in enumerate(flags or []) if i > 0 and (flags or [])[i - 1] == "--skip"]

    if only and frm:
        warnings.append("--only and --from both specified: --only wins; --from ignored")
        frm = None
    if only and to:
        warnings.append("--only and --to both specified: --only wins; --to ignored")
        to = None
    if only and skips:
        warnings.append("--only and --skip both specified: --only wins; --skip ignored")
        skips = []

    def _check_id(sid: str, flag: str) -> bool:
        if sid in ids:
            return True
        sug = _suggest(sid, ids)
        errors.append(f"{flag} {sid}: step not found"
                      + (f" (did you mean: {', '.join(sug)})" if sug else "")
                      + f". Available step-ids: {', '.join(ids)}")
        return False

    for sid, flg in ((only, "--only"), (frm, "--from"), (to, "--to")):
        if sid:
            _check_id(sid, flg)
    if only and only in ids and only not in active_ids:
        cond = next(s.get("condition") for s in steps if s["id"] == only)
        errors.append(f"--only {only}: condition (\"{cond}\") is currently OFF "
                      f"({next(s['why'] for s in steps if s['id'] == only)}). "
                      f"Add the enabling flag.")
    if frm and to and frm in ids and to in ids and ids.index(frm) > ids.index(to):
        errors.append(f"--from {frm} --to {to}: step order is reversed. Available step-ids: {', '.join(ids)}")

    for sid in skips:
        if sid not in ids:
            _check_id(sid, "--skip")  # nonexistent id is an ERROR (case A)
            continue
        st = next(s for s in steps if s["id"] == sid)
        if not st["active"]:
            warnings.append(f"--skip {sid}: step {sid} is already condition-OFF (--skip unnecessary)")
        if st.get("gate") == "acceptance-gate":
            warnings.append(f"--skip {sid}: step {sid} has gate: acceptance-gate"
                            " — the quality convergence loop will be skipped")

    # (4) finalize active/why (slice > --skip > condition; explicit skip wins in the end)
    for s in steps:
        sid = s["id"]
        if only:
            if sid != only:
                s["active"], s["why"] = False, "outside slice (--only)"
        else:
            if frm and frm in ids and ids.index(sid) < ids.index(frm):
                s["active"], s["why"] = False, "outside slice (--from)"
            if to and to in ids and ids.index(sid) > ids.index(to):
                s["active"], s["why"] = False, "outside slice (--to)"
        if sid in skips:
            s["active"], s["why"] = False, "[SKIP: --skip flag]"

    # (5) mode summary (orchestrate precedence: on > off (negation) > auto; §4.3)
    auto, auto_why = auto_orchestrate(plan["steps"],
                                      manifest_default=manifest.get("default_orchestrate") is True)
    if "--orchestrate" in fset:
        orch = "on"
    elif "--no-orchestrate" in fset:
        orch = "off"
    elif auto:
        orch = f"auto ({auto_why})"
    else:
        orch = "off"
    mode = {
        "autonomy": "autonomous" if "--autonomous" in fset else "interactive",
        "backend": "workflow" if "--workflow" in fset else "manual",
        "tdd": "--tdd" in fset,
        "orchestrate": orch,
        "capture": "off" if "--no-capture" in fset else ("auto" if "--capture" in fset else "ask"),
    }
    if "--capture" in fset and "--no-capture" in fset:
        warnings.append("--capture and --no-capture both specified: --no-capture wins (§7.3)")

    plan.update({
        "flags": sorted(fset),
        "size": {"diff_lines": diff_lines, "class": size},
        "steps": steps,
        "effective_steps": [s["id"] for s in steps if s["active"]],
        "slice": {"only": only, "from": frm, "to": to, "skip": skips},
        "mode": mode,
        "warnings": warnings,
        "errors": errors,
    })
    return plan


def resolve_plan_json(recipe_path: pathlib.Path) -> dict:
    """RESOLVE the recipe and return the computed fields of the --list/--plan display as JSON (deterministic)."""
    fm = parse_frontmatter(recipe_path)
    extends_name = fm.get("extends")
    resolved_fm, warnings = resolve_extends(fm, recipe_path)
    raw_steps = [s for s in (resolved_fm.get("steps") or []) if isinstance(s, dict)]
    steps = load_steps(resolved_fm)
    for s, raw in zip(steps, raw_steps):
        s["origin"] = raw.get("_origin")
    return {
        "recipe": fm.get("name", recipe_path.stem),
        "extends": extends_name,
        "autonomy": resolved_fm.get("autonomy"),
        "badges": derive_badges(resolved_fm, steps),
        "steps_field": derive_steps_field(steps),
        "n_steps": len(steps),
        "steps": steps,
        "warnings": warnings,
    }

def resolve_recipe(name: str) -> pathlib.Path:
    """Resolve a recipe.
    Priority: existing absolute/relative path -> cwd/.rig/recipes/<name>.md (project overlay) -> RIG_HOME/skills/rig/recipes/<name>.md (built-in).
    An overlay with the same name as a built-in wins, so project-specific recipes can override."""
    p = pathlib.Path(name)
    if p.exists():
        return ensure_recipe_trusted(p)
    fname = name if name.endswith(".md") else f"{name}.md"
    bases = [config.PROJECT_RECIPES]
    org = os.environ.get("RIG_ORG_HOME") or (load_manifest().get("org_dir") or "")
    if org:
        bases.append(pathlib.Path(org).expanduser() / "recipes")  # org tier (team-shared; §5)
    bases.append(config.RECIPES)
    for base in bases:
        cand = base / fname
        if cand.exists():
            return ensure_recipe_trusted(cand)
    print(f"[ERROR] recipe not found: {name}\n"
          f"  searched: " + ", ".join(str(b / fname) for b in bases))
    sys.exit(1)


def auto_orchestrate(steps: list[dict], manifest_default: bool = False) -> tuple[bool, str]:
    """Whether this recipe auto-enables --orchestrate (deterministic; same rules as SKILL §4.3)."""
    has_checks = any(s["checks"] for s in steps)
    has_needs = any(s["needs"] for s in steps)
    if has_checks or has_needs:
        why = "+".join(x for x in (["checks"] if has_checks else []) + (["needs"] if has_needs else []))
        return True, f"recipe declares {why}"
    if manifest_default:
        return True, "manifest default_orchestrate: true"
    return False, "explicit opt-in only (no auto-enable)"
