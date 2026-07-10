"""validation catalog: §2 catalog drift / wiki hygiene / brick graph checks (split from scripts/validate.py)."""

import json
import os
import pathlib
import re
import sys

from .config import AGENTS, FACETS, ROOT, SKILLS
from .state import _emit, parse_frontmatter


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
        [sys.executable, str(ROOT / "scripts" / "orchestrate.py"), "graph", "--json"],
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
