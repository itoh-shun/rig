"""validation catalog: §2 catalog drift / wiki hygiene / brick graph checks (split from scripts/validate.py)."""

import argparse
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
    # Facet kinds are the direct children of facets/.  Derive them instead of
    # keeping a second category list here: a newly added kind must be checked
    # from its first file.  recipes/ and patterns/ are the two non-facet brick
    # collections in the engine layout.
    brick_roots = [SKILLS / "recipes", SKILLS / "patterns"]
    brick_roots.extend(sorted(path for path in FACETS.iterdir() if path.is_dir()))
    wiki = FACETS / "knowledge" / "wiki"
    for brick_root in brick_roots:
        for f in sorted(brick_root.rglob("*.md")):
            if f.stem.startswith("_"):
                continue
            if wiki in f.parents:
                continue
            if f.stem not in skill and f.stem not in expanded_stems:
                relative = f.relative_to(SKILLS)
                _emit("WARN", f"§2 catalog — {relative} is not listed in SKILL.md (missed listing for a pack addition?)")
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



# ── workbench subcommand ↔ go.md route table (#478) ──────────────────────────
#: Subcommands `/rig:go` never routes to, because the flow calls them itself: the workbench
#: instruction drives `new`/`route`/`step`/`gate`, `intent` writes a contract during a run, and
#: the rest are `drill` and provenance plumbing. Declared here rather than inferred, so adding
#: one is a decision somebody made — and checked in the other direction below, because an entry
#: naming a subcommand that no longer exists is a silence nobody would notice.
INTERNAL_ONLY = frozenset({
    "new", "route", "step", "gate", "intent", "drill-corpus",
    "record-commit", "record-outcome", "trace-commit", "verify-provenance",
})

#: Where each surface keeps its list. Both documents say more than they route — `go.md`
#: explains the natural-language path below the table and gives examples, and the ops
#: instruction documents every subcommand in its body — so reading either whole would count a
#: name written in a sentence as a name the flow dispatches. That is the one mistake this check
#: cannot afford: it would report success for exactly what it exists to catch.
#:
#: The route table is found by its own header row rather than by the section around it, so a
#: second table added beside it is not mistaken for the dispatch table.
#: Both spellings are accepted while the command layer is being translated to English, and
#: exactly one line of the document may match any of them. Accepting a second spelling is not
#: the same as accepting a second table: two headers present at once is still the ambiguity
#: this check refuses, because it would read one table while reporting about the other.
ROUTE_TABLE_HEADERS = ("| First word | Delegates to |", "| 先頭語 | 委譲先 |")
OPS_HEADER_SECTION = ("# instruction: workbench-ops", "## 共通ルール")


def _sole_of(document: str, landmarks: tuple[str, ...]) -> int | None:
    """Which line is one of `landmarks`, if exactly one line of the document is any of them.

    Counted across all of them together rather than per spelling: a document carrying one
    table under each spelling holds two route tables, which is precisely the ambiguity
    `_sole` exists to refuse.
    """
    matches = [i for i, line in enumerate(document.splitlines())
               if line.strip() in landmarks]
    return matches[0] if len(matches) == 1 else None


def _sole(document: str, landmark: str) -> int | None:
    """Which line is `landmark`, if exactly one line of the document is.

    A whole line, because a landmark found as a substring is not a landmark found by
    structure: `> | 先頭語 | 委譲先 |` contains the route table's header row and *is* a
    blockquote, and slicing from the `|` inside it would hand back rows that no longer form
    the table this check says it read.

    Exactly one, because a landmark appearing twice locates nothing: `str.find` takes the
    first copy, and this check would then read one of them while reporting about the other. A
    test asserting the shipped files have unique landmarks only protects a run that executes
    that test — the check has to refuse the ambiguity itself, or `--validate` can pass while
    looking at the wrong table.
    """
    matches = [i for i, line in enumerate(document.splitlines()) if line.strip() == landmark]
    return matches[0] if len(matches) == 1 else None


def _from(document: str, line_no: int | None) -> str | None:
    """The document from `line_no` on."""
    return None if line_no is None else "\n".join(document.splitlines()[line_no:])


def _section(document: str, bounds: tuple[str, str]) -> str | None:
    """The slice between two landmarks, or None if either fails to locate the section.

    None rather than the whole document, because a landmark that stopped matching means this
    check no longer knows where the list is, and falling back to the document would turn that
    into a quiet answer about the wrong text.
    """
    start, end = bounds
    head = _sole(document, start)
    stop = _sole(document, end)
    if head is None or stop is None or stop <= head:
        return None
    return "\n".join(document.splitlines()[head:stop])


def registered_subcommands(parser) -> list[str]:
    """Every name the CLI dispatches, asked of argparse rather than read off the source.

    `cli.py` is where a subcommand is registered, but how it is *spelled* there is not the
    invariant — a registration moved into a helper or a loop dispatches exactly the same and
    would vanish from any regex over the source. `sub.choices` is what argparse will actually
    match an argv against, so it cannot disagree with the CLI's behaviour.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return sorted(action.choices)
    return []


def route_table(go_md: str) -> str | None:
    """The rows of the route table itself, found by its header row.

    The contiguous run of rows under that header, not the section it sits in: `go.md` may well
    grow a second table beside this one — flags, states, examples — and a check that read every
    row between two headings would take that table's first column for dispatch wiring. A
    second table under the *same* header is refused rather than guessed at.
    """
    below = _from(go_md, _sole_of(go_md, ROUTE_TABLE_HEADERS))
    if below is None:
        return None
    rows = []
    for row in below.splitlines():
        if not row.startswith("|"):
            break
        rows.append(row)
    return "\n".join(rows)


def routed_subcommands(table_rows: str) -> list[str]:
    """Every name the route table's first column routes, given that table's rows.

    Takes rows because `route_table` is where what counts as a row is decided; repeating that
    judgement here would be a second place for it to be wrong, and the two could disagree.

    The first cell is what `/rig:go` matches a leading word against, and it reads
    `` `<name> [args…]` ``, so the name is the leading word of the cell's opening backticked
    run — a *complete* run, since an unclosed backtick is a row nobody proof-read rather than
    a route somebody wrote. A backticked name further along that cell is an annotation on the
    row — `（廃止: `receipt`）` routes nothing — and a name in a later column is a description.

    Cells are split on unescaped pipes, because several rows carry `\\|` inside their usage
    string (`--period week\\|month`) and splitting on every pipe would cut those cells in half,
    leaving a run that never closes.
    """
    routed = set()
    for row in table_rows.splitlines():
        first = re.split(r"(?<!\\)\|", row.lstrip().lstrip("|"))[0]
        match = re.match(r"\s*`([a-z0-9][a-z0-9-]*)[^`]*`", first)
        if match:
            routed.add(match.group(1))
    return sorted(routed)


def listed_subcommands(ops_header: str) -> list[str]:
    """Every name the ops instruction's own header lists as `` `/rig <name>` ``."""
    return sorted(set(re.findall(r"`/rig ([a-z0-9][a-z0-9-]*)`", ops_header)))


def workbench_routing(parser, go_md: str, ops_md: str) -> tuple[list, list, list]:
    """(unrouted subcommands, stale allowlist entries, why this check cannot answer).

    Returns rather than emits so a test can hand it a wiring it must object to. Five issues
    (#261, #262, #327, #417, #473) were this same omission found one at a time after the fact;
    a check that cannot be exercised would be the sixth.

    Two surfaces, one rule: `go.md` says what `/rig:go` dispatches and the ops instruction says
    what it is a procedure for, and #473 was both of them missing the same four names. A check
    covering one of the two would have reported that issue half-fixed.

    Takes whole documents and locates the two lists itself, so that where it reads is part of
    what a test can break.
    """
    registered = registered_subcommands(parser)
    table = route_table(go_md)
    header = _section(ops_md, OPS_HEADER_SECTION)
    routed = routed_subcommands(table) if table is not None else []
    listed = listed_subcommands(header) if header is not None else []

    # A check that found nothing to check has not passed. Each of these means the shape this
    # check reads has moved — a different parser, a relocated table, a rewritten header — and
    # reporting zero omissions then would be the check saying "all clear" about text it never
    # looked at.
    blind = []
    if not registered:
        blind.append("the parser exposes no subcommands: the CLI wiring this check reads has "
                     "changed shape")
    if table is None:
        blind.append(f"commands/go.md does not hold exactly one table headed one of "
                     f"{ROUTE_TABLE_HEADERS!r}: this check cannot tell which table is the "
                     f"route table")
    elif not routed:
        blind.append("no routed names found under the route table's header: the rows this "
                     "check reads have changed shape")
    if header is None:
        blind.append(f"workbench-ops.md does not hold exactly one section bounded by "
                     f"{OPS_HEADER_SECTION[0]!r} and {OPS_HEADER_SECTION[1]!r} in that order: "
                     f"this check cannot tell where its header list is")
    elif not listed:
        blind.append("no `/rig <name>` entries found in the ops instruction's header: the "
                     "list this check reads has changed shape")

    unrouted = [name for name in registered
                if name not in INTERNAL_ONLY
                and (name not in routed or name not in listed)] if not blind else []
    stale = sorted(INTERNAL_ONLY - set(registered)) if registered else []
    return unrouted, stale, blind


def check_workbench_routing() -> None:
    """`workbench.py`'s user-facing subcommands against `commands/go.md`'s route table."""
    from rig_workbench.workbench.cli import build_parser
    go_md = (ROOT / "commands" / "go.md").read_text(encoding="utf-8")
    ops_md = (FACETS / "instructions" / "workbench-ops.md").read_text(encoding="utf-8")
    parser = build_parser()
    unrouted, stale, blind = workbench_routing(parser, go_md, ops_md)

    for why in blind:
        _emit("FAIL", f"workbench routing — {why}")
    for name in unrouted:
        _emit("WARN", f"workbench routing — `{name}` is a subcommand of workbench.py and is "
                      f"missing from commands/go.md's route table or from the ops "
                      f"instruction's header list, so /rig:go cannot dispatch it or has no "
                      f"procedure for it")
    for name in stale:
        _emit("WARN", f"workbench routing — INTERNAL_ONLY names `{name}`, which is not a "
                      f"subcommand any more; an allowlist entry for something that does not "
                      f"exist suppresses nothing and hides that it stopped applying")
    if not blind:
        _emit("PASS", f"workbench routing: {len(registered_subcommands(parser))} subcommands "
                      f"/ {len(unrouted)} unrouted / {len(stale)} stale allowlist")


# ── workbench subcommand ↔ SKILL.md §2 brick catalog (#491) ──────────────────
#: The §2 section, by the two headings that bound it. `check_catalog_drift` slices the same
#: section with `str.index("## 3.")`, which would also land on `## 3.5. Recipe スキーマ` if the
#: two headings were ever reordered; this check locates both bounds as whole lines that occur
#: exactly once, so a renamed or duplicated heading makes it go blind instead of reading a
#: section that is not §2.
CATALOG_SECTION = ("## 2. ブリック目録", "## 3. PARSE — 起動文字列の解釈")

#: Subcommands §2 catalogues through the `/rig:go` workbench pack row rather than one by one.
#: They are the operations on a run — show it, diff it, accept it, discard it, scan it, count
#: it — and §2's rows name *surfaces*: a pack and the bricks it adds. The workbench row is that
#: surface for all of these, and `commands/go.md`'s route table is where each of them is
#: written down individually (checked by `check_workbench_routing`, one row per subcommand).
#:
#: Declared rather than inferred, because nothing about `accept` distinguishes it from
#: `receipt` mechanically — both are user-facing subcommands of the same parser. Which side of
#: the line a new subcommand falls on is a judgement somebody makes, and making it here means
#: a new run operation is a one-line decision while a new surface (which is what #395 and #470
#: both were) is reported until §2 names it. Checked in the other direction below, because an
#: entry naming a subcommand that no longer exists suppresses nothing and hides that it
#: stopped applying.
PACK_ROW_ONLY = frozenset({
    "accept", "audit", "board", "cockpit", "confidence", "context", "diff", "digest",
    "discard", "gates", "gc", "instincts", "log", "review", "scan-anchors",
    "scan-destructive", "scan-injection", "scan-secrets", "stale-refs", "stats", "status",
    "stream-checks",
})


def catalogued_subcommands(section: str) -> list[str]:
    """Every subcommand §2 names structurally, given §2.

    Structurally means as an invocation — a complete `` `rig-wb wb <name>` `` run, or the
    brace notation §2 already uses to group a surface's subcommands into one
    (`` `rig-wb wb {receipt,import,contract}` ``). Not "is the name written somewhere in §2":
    §2 is a catalogue of packs, and a subcommand's name turns up inside other rows' prose and
    inside other rows' file lists. Measured against §2 as it stood before #470 was fixed,
    `import` matched the `/rig:import` pack row and `contract` matched "output-contract
    facet" — so a check asking whether the name is *mentioned* reports the very omission it
    exists to catch as covered.

    The name must be the whole of what follows `rig-wb wb` inside the run, so a usage example
    written with its arguments does not count as the catalogue listing that surface — the same
    rule `listed_subcommands` applies to the ops instruction's header, for the same reason:
    one example inside a description is not the document naming what rig has.
    """
    names = set()
    for token in re.findall(r"`rig-wb wb ([A-Za-z0-9{},_-]+)`", section):
        names.update(_expand_braces(token))
    return sorted(names)


def workbench_catalog(parser, skill_md: str) -> tuple[list, list, list]:
    """(subcommands §2 does not catalogue, stale allowlist entries, why this cannot answer).

    Returns rather than emits so a test can hand it a catalogue it must object to — including
    the two catalogues that already fooled the obvious version of this check.

    Takes the whole document and locates §2 itself, so that where it reads is part of what a
    test can break.
    """
    registered = registered_subcommands(parser)
    section = _section(skill_md, CATALOG_SECTION)
    catalogued = catalogued_subcommands(section) if section is not None else []

    # A check that found nothing to check has not passed. Each of these means the shape this
    # check reads has moved — a different parser, a renamed heading, a §2 that stopped
    # spelling these surfaces as invocations — and reporting zero omissions then would be the
    # check saying "all clear" about text it never looked at.
    blind = []
    if not registered:
        blind.append("the parser exposes no subcommands: the CLI wiring this check reads has "
                     "changed shape")
    if section is None:
        blind.append(f"skills/engine/SKILL.md does not hold exactly one section bounded by "
                     f"{CATALOG_SECTION[0]!r} and {CATALOG_SECTION[1]!r} in that order: this "
                     f"check cannot tell where the brick catalog is")
    elif not catalogued:
        blind.append("no `rig-wb wb <name>` entries found in §2: the notation this check "
                     "reads the catalog by has changed shape")

    uncatalogued = [name for name in registered
                    if name not in INTERNAL_ONLY
                    and name not in PACK_ROW_ONLY
                    and name not in catalogued] if not blind else []
    stale = sorted(PACK_ROW_ONLY - set(registered)) if registered else []
    return uncatalogued, stale, blind


def check_workbench_catalog() -> None:
    """`workbench.py`'s user-facing subcommands against SKILL.md §2's brick catalog.

    §2 is what a session reads to find out what rig has, and a surface missing from it does
    not exist from there. Three times a shipped surface went missing (#395, #470, and the nine
    subcommands #470's fix found still unlisted), each time noticed by a person rather than by
    this repository's own checks.
    """
    from rig_workbench.workbench.cli import build_parser
    skill_md = (SKILLS / "SKILL.md").read_text(encoding="utf-8")
    parser = build_parser()
    uncatalogued, stale, blind = workbench_catalog(parser, skill_md)

    for why in blind:
        _emit("FAIL", f"workbench catalog — {why}")
    for name in uncatalogued:
        _emit("WARN", f"workbench catalog — `{name}` is a user-facing subcommand of "
                      f"workbench.py and SKILL.md §2 names no `rig-wb wb {name}`, so a session "
                      f"reading the brick catalog cannot find out it exists (missed listing "
                      f"for a new surface?)")
    for name in stale:
        _emit("WARN", f"workbench catalog — PACK_ROW_ONLY names `{name}`, which is not a "
                      f"subcommand any more; an allowlist entry for something that does not "
                      f"exist suppresses nothing and hides that it stopped applying")
    if not blind:
        registered = registered_subcommands(parser)
        catalogued = set(catalogued_subcommands(_section(skill_md, CATALOG_SECTION)))
        internal = sum(1 for name in registered if name in INTERNAL_ONLY)
        by_row = sum(1 for name in registered
                     if name not in INTERNAL_ONLY and name in PACK_ROW_ONLY)
        named = sum(1 for name in registered
                    if name not in INTERNAL_ONLY and name not in PACK_ROW_ONLY
                    and name in catalogued)
        _emit("PASS", f"workbench catalog: {len(registered)} subcommands — {internal} "
                      f"internal / {by_row} covered by the workbench pack row / {named} named "
                      f"in §2 / {len(uncatalogued)} uncatalogued")


# ── SKILL.md §2 pack rows ↔ PACKS.md detail rows (#573) ──────────────────────
#: The header row of §2's "pack 追加分" table, as the whole line it is written on. §2 keeps
#: its pack table inside a blockquote, so the line carries the `> ` prefix; a copy of the
#: header outside the blockquote is a different table and must not be read as this one.
PACK_TABLE_HEADER = "> | pack | 要旨 | 追加ブリック |"

#: The header row of PACKS.md's "pack 一覧" table. Not inside a blockquote there.
PACKS_TABLE_HEADER = "| pack | 追加ブリックと詳細 |"

#: A row of either table starts with its bold pack id: `| **talk**（`/rig:talk`） | …`. The id
#: is the whole of what the bold holds — `intent / assurance target` is one id with spaces
#: in it, and the parenthesised entry point after it is not part of the id.
_PACK_ROW = re.compile(r"^>?\s*\|\s*\*\*(.+?)\*\*")


def pack_table_ids(document: str, header: str) -> list[str] | None:
    """The pack ids of the table whose header row is `header`, or None if there is no such
    table exactly once in `document`.

    Reads rows, not mentions: the id is what the first cell holds in bold, and the table ends
    at the first line that is not a row of it. §2's blockquote keeps a `>` prefix on every
    row; PACKS.md's table has none; both spellings are one table shape here.

    None rather than an empty list when the header is missing or duplicated, so a renamed or
    copied heading makes the caller go blind instead of reporting that nothing drifted.
    """
    start = _sole(document, header)
    if start is None:
        return None
    lines = document.splitlines()[start + 1:]
    prefix = ">" if header.lstrip().startswith(">") else "|"
    ids = []
    for line in lines:
        if not line.strip().startswith(prefix):
            break
        stripped = line.strip().lstrip(">").strip()
        if not stripped.startswith("|"):
            break
        if re.fullmatch(r"\|(\s*:?-+:?\s*\|)+", stripped):
            continue  # the separator row under the header
        match = _PACK_ROW.match(line)
        if match:
            ids.append(match.group(1).strip())
    return ids


def packs_catalog_drift(skill_md: str, packs_md: str) -> tuple[list, list, list]:
    """(§2 pack rows with no PACKS.md detail row, PACKS.md rows with no §2 row, why this
    cannot answer).

    §2 says it itself: a new pack gets a one-line row there *and* a detail row in `PACKS.md`,
    and `PACKS.md` opens by saying the same. Nothing checked the pair — `check_catalog_drift`
    compares §2 against the files on disk — and ten packs went into §2 with no detail row
    while the rule stood (#573).

    Returns rather than emits so a test can hand it two documents it must object to. Takes
    the whole of SKILL.md and locates §2 itself, so that where it reads is part of what a
    test can break; a pack table outside §2 is not the catalogue.
    """
    blind = []
    section = _section(skill_md, CATALOG_SECTION)
    if section is None:
        blind.append(f"skills/engine/SKILL.md does not hold exactly one section bounded by "
                     f"{CATALOG_SECTION[0]!r} and {CATALOG_SECTION[1]!r} in that order: this "
                     f"check cannot tell where the brick catalog is")
        skill_ids = None
    else:
        skill_ids = pack_table_ids(section, PACK_TABLE_HEADER)
        if skill_ids is None:
            blind.append(f"SKILL.md §2 does not hold exactly one line {PACK_TABLE_HEADER!r}: "
                         f"this check cannot tell where the pack table is")
        elif not skill_ids:
            blind.append("SKILL.md §2's pack table has no `| **<id>** |` rows: the row shape "
                         "this check reads pack ids by has changed")
    packs_ids = pack_table_ids(packs_md, PACKS_TABLE_HEADER)
    if packs_ids is None:
        blind.append(f"skills/engine/PACKS.md does not hold exactly one line "
                     f"{PACKS_TABLE_HEADER!r}: this check cannot tell where the pack list is")
    elif not packs_ids:
        blind.append("PACKS.md's pack table has no `| **<id>** |` rows: the row shape this "
                     "check reads pack ids by has changed")
    if blind:
        return [], [], blind
    missing = [pack for pack in skill_ids if pack not in packs_ids]
    stale = [pack for pack in packs_ids if pack not in skill_ids]
    return missing, stale, []


def check_packs_catalog() -> None:
    """SKILL.md §2's pack rows against PACKS.md's detail rows, in both directions.

    A §2 row with no detail row is the addition the rule in §2 asks for and did not get; a
    detail row with no §2 row is a pack that left the catalogue and kept its long description.
    Both WARN, in the shape `check_catalog_drift` already uses for a suspected missed
    listing. Not finding either table is FAIL: a check that could not read is not a pass.
    """
    skill_md = (SKILLS / "SKILL.md").read_text(encoding="utf-8")
    packs_md = (SKILLS / "PACKS.md").read_text(encoding="utf-8")
    missing, stale, blind = packs_catalog_drift(skill_md, packs_md)

    for why in blind:
        _emit("FAIL", f"packs catalog — {why}")
    for pack in missing:
        _emit("WARN", f"packs catalog — SKILL.md §2 lists pack `{pack}` and PACKS.md has no "
                      f"detail row for it (§2 says every pack gets both; missed the PACKS.md "
                      f"half?)")
    for pack in stale:
        _emit("WARN", f"packs catalog — PACKS.md has a detail row for `{pack}` and SKILL.md "
                      f"§2's pack table does not list it (stale detail row for a pack that "
                      f"left §2?)")
    if not blind:
        _emit("PASS", f"packs catalog: {len(missing)} §2 rows without a PACKS.md row / "
                      f"{len(stale)} PACKS.md rows without a §2 row")
