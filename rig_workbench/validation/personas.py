"""validation personas: persona/command/agent frontmatter checks (split from scripts/validate.py)."""

import re

from .config import AGENTS, FACETS, ROOT
from .state import _emit, parse_frontmatter


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
