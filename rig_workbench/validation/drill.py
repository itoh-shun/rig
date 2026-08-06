"""validation drill: /rig:drill coverage check for gate-bearing recipes (#266).

`/rig:drill` measures reviewer detection rates by injecting bug seeds from the
seed catalog (facets/instructions/drill.md, the "種の class" table) into a
synthetic diff and running review fan-out. A recipe's review/acceptance gate is
therefore only *drill-coverable* when the gate is enforced by reviewer personas
that have a corresponding seed class ("検出すべき観点" column) in that catalog.

This check WARNs (never FAILs — coverage guidance, not schema) for shipped
gate-bearing recipes that /rig:drill cannot exercise:
  - recipes whose reviewer personas all lack a seed class in the catalog, and
  - recipes with a gate but no reviewer personas at all (aggregated into one
    WARN; their gate efficacy is only visible via `rig stats` rubber-stamp
    detection, not via drill).
Recipes with at least one covered reviewer count as coverable, but reviewers
without a seed class are still surfaced (detection rate unmeasured for them).
"""

import pathlib

from rig_workbench.orchestrate.gates import is_runtime_gate

from .config import FACETS, SKILLS
from .state import _emit, parse_frontmatter

# The seed catalog table is anchored by this header cell (perspective column).
_PERSPECTIVE_HEADER = "検出すべき観点"


def parse_seed_perspectives(drill_md: pathlib.Path) -> set[str]:
    """Extract the perspectives /rig:drill can exercise from the seed catalog.

    Reads the markdown table in facets/instructions/drill.md whose header row
    contains the "検出すべき観点" column and collects that column's tokens
    (cells like "design / lazy-senior" are split on "/"). Returns an empty set
    when the file or table is missing so the caller can WARN instead of crash.
    """
    if not drill_md.exists():
        return set()
    perspectives: set[str] = set()
    column: int | None = None
    for line in drill_md.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            column = None
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if _PERSPECTIVE_HEADER in cells:
            column = cells.index(_PERSPECTIVE_HEADER)
            continue
        if column is None or column >= len(cells):
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue  # separator row (|---|---|…)
        for token in cells[column].split("/"):
            token = token.strip()
            if token:
                perspectives.add(token)
    return perspectives


_CORPUS_VERSION_RE_STR = r"corpus_version:\s*(\d+)"
_VALID_SEVERITIES = ("Critical", "High", "Medium", "Low")
_VALID_BLOCKING = ("Blocking", "Non-blocking")


def parse_seed_rows(drill_md: pathlib.Path) -> list[dict]:
    """Data rows of the seed-catalog table as dicts keyed by header cell."""
    if not drill_md.exists():
        return []
    rows: list[dict] = []
    headers: list[str] | None = None
    for line in drill_md.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            headers = None
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if _PERSPECTIVE_HEADER in cells:
            headers = cells
            continue
        if headers is None or len(cells) != len(headers):
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue  # separator row
        rows.append(dict(zip(headers, cells)))
    return rows


def check_corpus_integrity(drill_instruction: pathlib.Path | None = None) -> None:
    """WARN on standard-corpus rot (#270): missing version marker, empty
    class/provenance/perspective cells, or out-of-range severity/blocking.
    The corpus is the drill's answer key — a malformed row silently corrupts
    every score computed from it."""
    import re

    drill_md = drill_instruction or (FACETS / "instructions" / "drill.md")
    if not drill_md.exists():
        _emit("WARN", f"drill corpus — {drill_md.name} not found (cannot check corpus integrity)")
        return
    text = drill_md.read_text(encoding="utf-8")
    m = re.search(_CORPUS_VERSION_RE_STR, text)
    if not m:
        _emit("WARN", "drill corpus — no `corpus_version:` marker in the seed catalog "
                      "(bump it on every corpus change so scores stay comparable per version)")
        return
    version = int(m.group(1))
    rows = parse_seed_rows(drill_md)
    problems: list[str] = []
    for row in rows:
        label = row.get("種の class", "?")
        if not row.get("種の class") or not row.get("cwe/odc") or not row.get(_PERSPECTIVE_HEADER):
            problems.append(f"{label}: empty class/provenance/perspective cell")
        if row.get("期待 severity") not in _VALID_SEVERITIES:
            problems.append(f"{label}: severity '{row.get('期待 severity')}' not in {_VALID_SEVERITIES}")
        if row.get("期待 blocking") not in _VALID_BLOCKING:
            problems.append(f"{label}: blocking '{row.get('期待 blocking')}' not in {_VALID_BLOCKING}")
    if problems:
        for p in problems:
            _emit("WARN", f"drill corpus — {p}")
        return
    _emit("PASS", f"drill corpus: standard corpus v{version} — {len(rows)} seed classes, "
                  "all rows carry class/provenance/perspective and valid severity/blocking")


_FIXTURE_SEVERITIES = ("critical", "high", "medium", "low")


def check_fixture_corpus_integrity(corpus_dir: pathlib.Path | None = None) -> None:
    """WARN on rot in the pre-built fixture corpus (`skills/engine/corpora/fixture/`).

    Same reasoning as the standard-corpus check: the corpus is the drill's answer
    key, and a malformed case silently corrupts every score computed from it.
    Here the answer key is regexes over base/ and head/ trees, so the failure
    modes differ — a case that lost its trees, a violation whose location/concept
    regex no longer compiles, or the loss of the clean case (with it goes the
    only false-positive control the corpus has)."""
    import json
    import re

    root = corpus_dir or (SKILLS / "corpora" / "fixture")
    if not root.is_dir():
        _emit("WARN", f"drill fixture corpus — {root} not found (nothing to check)")
        return

    problems: list[str] = []
    meta_path = root / "corpus.json"
    version: object = None
    if not meta_path.exists():
        problems.append("corpus.json is missing (no corpus_version to keep scores comparable)")
    else:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            meta = {}
            problems.append(f"corpus.json is unreadable: {error}")
        version = meta.get("corpus_version")
        if not isinstance(version, int):
            problems.append("corpus.json has no integer `corpus_version` (bump it on every "
                            "corpus change so scores stay comparable per version)")

    cases_dir = root / "cases"
    case_dirs = sorted(p for p in cases_dir.iterdir() if p.is_dir()) if cases_dir.is_dir() else []
    if not case_dirs:
        problems.append("no cases under cases/")
    clean_cases = 0
    planted = 0
    for case_dir in case_dirs:
        label = case_dir.name
        meta_file = case_dir / "case.json"
        if not meta_file.exists():
            problems.append(f"{label}: case.json is missing")
            continue
        try:
            case = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            problems.append(f"{label}: case.json is unreadable: {error}")
            continue
        for tree in ("base", "head"):
            if not (case_dir / tree).is_dir():
                problems.append(f"{label}: {tree}/ tree is missing (no diff to review)")
        if case.get("id") != label:
            problems.append(f"{label}: case.json id '{case.get('id')}' "
                            "does not match its directory")
        violations = case.get("violations") or []
        if case.get("clean"):
            clean_cases += 1
            if violations:
                problems.append(f"{label}: marked clean but carries {len(violations)} violation(s)")
        elif not violations:
            problems.append(f"{label}: not marked clean but plants no violation")
        planted += 0 if case.get("clean") else len(violations)
        for violation in violations:
            vid = violation.get("id", "?")
            for field in ("id", "category", "severity", "location", "concept"):
                if not violation.get(field):
                    problems.append(f"{label}/{vid}: empty `{field}`")
            if not violation.get("perspectives"):
                problems.append(f"{label}/{vid}: no `perspectives` "
                                "(nothing says which reviewer should have caught it)")
            severity = str(violation.get("severity", "")).lower()
            if severity not in _FIXTURE_SEVERITIES:
                problems.append(f"{label}/{vid}: severity '{violation.get('severity')}' "
                                f"not in {_FIXTURE_SEVERITIES}")
            for field in ("location", "concept"):
                pattern = violation.get(field)
                if not pattern:
                    continue
                try:
                    re.compile(pattern)
                except re.error as error:
                    problems.append(f"{label}/{vid}: `{field}` is not a valid regex: {error}")
    if case_dirs and not clean_cases:
        problems.append("no clean case (the corpus can no longer measure false positives)")

    if problems:
        for problem in problems:
            _emit("WARN", f"drill fixture corpus — {problem}")
        return
    _emit("PASS", f"drill fixture corpus: v{version} — {len(case_dirs)} cases "
                  f"({planted} planted defects, {clean_cases} clean), "
                  "all carry base/head trees and a compilable answer key")


def _base_name(persona: str) -> str:
    """Last path segment of a persona reference (design/ux-reviewer → ux-reviewer)."""
    return persona.rsplit("/", 1)[-1].strip()


def _is_reviewer(persona: str, perspectives: set[str]) -> bool:
    base = _base_name(persona)
    return base.endswith("-reviewer") or base in perspectives


def _is_covered(persona: str, perspectives: set[str]) -> bool:
    base = _base_name(persona)
    return base in perspectives or base.removesuffix("-reviewer") in perspectives


def check_drill_coverage(
    recipe_files: list[pathlib.Path],
    drill_instruction: pathlib.Path | None = None,
) -> None:
    """WARN for gate-bearing recipes that /rig:drill cannot exercise (#266)."""
    drill_md = drill_instruction or (FACETS / "instructions" / "drill.md")
    perspectives = parse_seed_perspectives(drill_md)
    if not perspectives:
        _emit(
            "WARN",
            f"drill coverage — seed catalog table ('{_PERSPECTIVE_HEADER}' column) not found in"
            f" {drill_md.name} (cannot check which gates /rig:drill exercises)",
        )
        return

    gated_total = 0
    coverable: list[str] = []
    no_reviewer: list[str] = []
    for path in recipe_files:
        fm, _ = parse_frontmatter(path)
        steps = (fm or {}).get("steps")
        if not isinstance(steps, list):
            continue
        steps = [s for s in steps if isinstance(s, dict)]
        if not any(is_runtime_gate(s.get("gate")) for s in steps):
            continue  # gate-less recipes are out of drill's scope
        gated_total += 1

        reviewers: list[str] = []
        for step in steps:
            for persona in step.get("personas") or []:
                if (isinstance(persona, str) and _is_reviewer(persona, perspectives)
                        and persona not in reviewers):
                    reviewers.append(persona)

        if not reviewers:
            no_reviewer.append(path.stem)
            continue

        uncovered = [p for p in reviewers if not _is_covered(p, perspectives)]
        if len(uncovered) == len(reviewers):
            _emit(
                "WARN",
                f"drill coverage — recipe {path.stem}: gate-bearing, but none of its reviewers"
                f" ({', '.join(reviewers)}) have a seed class in the drill catalog"
                f" (/rig:drill cannot exercise this gate; extend the seed catalog or the personas)",
            )
            continue
        if uncovered:
            _emit(
                "WARN",
                f"drill coverage — recipe {path.stem}: reviewers without a drill seed class:"
                f" {', '.join(uncovered)} (their detection rate stays unmeasured)",
            )
        coverable.append(path.stem)

    if no_reviewer:
        _emit(
            "WARN",
            "drill coverage — gate-bearing recipes with no reviewer personas"
            " (/rig:drill cannot exercise their gates; efficacy is only visible via"
            " `rig stats` rubber-stamp detection): " + ", ".join(sorted(no_reviewer)),
        )

    _emit(
        "PASS",
        f"drill coverage: {len(coverable)}/{gated_total} gate-bearing recipes exercisable by"
        f" /rig:drill (seed perspectives: {', '.join(sorted(perspectives))})",
    )
