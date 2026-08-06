"""fixture corpus for /rig:drill — materialize pre-built cases and score reviews.

The standard drill corpus (`facets/instructions/drill.md`) is a table of seed
*classes* that a subagent synthesizes into a fresh diff each run. This module
backs the other corpus shape: `skills/engine/corpora/fixture/`, where the diff is
already written — every case ships a `base/` tree (committed) and a `head/` tree
(uncommitted working-tree change), and the answer key ships with it.

Two jobs, both deterministic so the drill never has to eyeball a score:

  materialize   base/ committed into a throwaway git repo, head/ laid over it as
                uncommitted changes — i.e. exactly the shape "review the current
                changes" expects. The real repo is never touched.
  score         a review text vs. the answer key. A planted defect counts as
                detected only when the review carries **both** a location signal
                and a concept signal **near each other** (PROXIMITY_WINDOW).
                Matching them anywhere in the document would be far too
                generous: a long review that mentions `mergeMetadata` in one
                paragraph and the word "any" in an unrelated sentence would
                score as a detection. `location_hit` / `concept_hit` are also
                reported separately, because "named the symbol but never said
                what was wrong with it" is a different failure from "never
                looked at it".

On the **clean** case (zero planted defects) the direction inverts: any blocking
language is a false positive, and plain "looks fine" prose is not. That case is
what measures precision — a reviewer that cannot stay quiet when there is
nothing to find is as broken as one that misses defects.

What this scorer deliberately does NOT compute: `severity_accuracy`,
`blocking_accuracy`, `explanation_quality` (they need the judge step in
drill ③-b) and false positives on the *violation* cases (separating an invented
finding from a real bug the reviewer happened to spot is a judgement call; the
clean case measures the same thing under control). Those fields are left absent
rather than filled with a number nobody measured.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from typing import Any

# A concept must appear within this many characters of some mention of the
# location for the pair to count as one finding rather than two coincidences.
PROXIMITY_WINDOW = 600

# On a clean case, blocking language is the false positive. A suggestion phrased
# as optional ("might be worth …") deliberately does not match.
CLEAN_FP_RE = re.compile(
    r"(?i)(critical|high severity|severity:\s*(critical|high)|must fix|blocking|"
    r"security (issue|risk|vulnerab)|bug\b|defect|重大|要修正|ブロッ)"
)

# drill derives expected blocking from expected severity (facets/instructions/drill.md ①).
_BLOCKING_BY_SEVERITY = {
    "critical": "Blocking",
    "high": "Blocking",
    "medium": "Non-blocking",
    "low": "Non-blocking",
}


def corpus_root() -> pathlib.Path:
    """Directory of the shipped fixture corpus (RIG_HOME wins when it is set)."""
    rel = pathlib.Path("skills") / "engine" / "corpora" / "fixture"
    env = os.environ.get("RIG_HOME")
    if env and (pathlib.Path(env) / rel).is_dir():
        return (pathlib.Path(env) / rel).resolve()
    return (pathlib.Path(__file__).resolve().parents[2] / rel).resolve()


def load_corpus_meta(root: pathlib.Path | None = None) -> dict[str, Any]:
    """`corpus.json` (corpus id + corpus_version); {} when the corpus is absent."""
    path = (root or corpus_root()) / "corpus.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases(
    selected: list[str] | None = None,
    root: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    """Answer keys of the corpus, sorted by case id. `_dir` carries base/ and head/."""
    base = root or corpus_root()
    cases_dir = base / "cases"
    if not cases_dir.is_dir():
        return []
    cases: list[dict[str, Any]] = []
    for case_dir in sorted(cases_dir.iterdir()):
        meta_path = case_dir / "case.json"
        if not case_dir.is_dir() or not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["_dir"] = case_dir
        if not selected or selected == ["all"] or meta["id"] in selected:
            cases.append(meta)
    return cases


def expected_blocking(severity: str) -> str | None:
    """Blocking / Non-blocking, derived from severity the way drill ① derives it."""
    return _BLOCKING_BY_SEVERITY.get(str(severity).lower())


def score_violation(text: str, violation: dict[str, Any]) -> tuple[bool, bool, bool]:
    """Credit a planted defect only when its class is discussed *near* its symbol.

    Returns (location_hit, concept_hit, detected).
    """
    location = re.compile(violation["location"])
    concept = re.compile(violation["concept"])
    location_hit = bool(location.search(text))
    concept_hit = bool(concept.search(text))
    detected = False
    for match in location.finditer(text):
        start = max(0, match.start() - PROXIMITY_WINDOW)
        end = min(len(text), match.end() + PROXIMITY_WINDOW)
        if concept.search(text[start:end]):
            detected = True
            break
    return location_hit, concept_hit, detected


def perspective_of(persona: str) -> str:
    """Reviewer perspective a persona speaks for (`security-reviewer` → `security`)."""
    base = persona.rsplit("/", 1)[-1].strip()
    return base.removesuffix("-reviewer")


def corpus_perspectives(cases: list[dict[str, Any]]) -> set[str]:
    """Every perspective some planted defect is attributed to."""
    return {
        p
        for case in cases
        for violation in case.get("violations") or []
        for p in violation.get("perspectives") or []
    }


def accountable_violations(
    case: dict[str, Any], perspective: str | None
) -> list[dict[str, Any]]:
    """Planted defects this perspective is expected to catch (all of them when None)."""
    violations = case.get("violations") or []
    if perspective is None:
        return list(violations)
    return [v for v in violations if perspective in (v.get("perspectives") or [])]


def score_review(
    case: dict[str, Any],
    text: str,
    perspective: str | None = None,
) -> dict[str, Any]:
    """Score one reviewer's output for one case against that case's answer key."""
    result: dict[str, Any] = {
        "case": case["id"],
        "clean": bool(case.get("clean")),
        "detections": [],
        "seeded": 0,
        "detected": 0,
    }
    if case.get("clean"):
        # Every finding here is a false positive by construction.
        result["flagged"] = bool(CLEAN_FP_RE.search(text))
        return result

    for violation in accountable_violations(case, perspective):
        location_hit, concept_hit, detected = score_violation(text, violation)
        result["detections"].append({
            "violation": violation["id"],
            "category": violation.get("category"),
            "severity": violation.get("severity"),
            "expected_blocking": expected_blocking(violation.get("severity", "")),
            "location_hit": location_hit,
            "concept_hit": concept_hit,
            "detected": detected,
        })
    result["seeded"] = len(result["detections"])
    result["detected"] = sum(1 for d in result["detections"] if d["detected"])
    return result


def build_drill_row(
    reviews: dict[str, dict[str, str]],
    cases: list[dict[str, Any]] | None = None,
    root: pathlib.Path | None = None,
) -> dict[str, Any]:
    """One `.rig/drill-results.jsonl` row from {case-id: {persona: review text}}.

    Attribution: a persona is scored on the planted defects whose `perspectives`
    name its perspective. A persona whose perspective appears nowhere in the
    corpus (a generalist reviewer, say) would otherwise score 0/0 — those rows
    are scored on every planted defect instead and marked `attribution: "all"`,
    so a scoreboard reader can tell the two apart.
    """
    all_cases = cases if cases is not None else load_cases(root=root)
    by_id = {c["id"]: c for c in all_cases}
    known = corpus_perspectives(all_cases)
    meta = load_corpus_meta(root)

    personas = sorted({p for per_case in reviews.values() for p in per_case})
    scored_cases = [by_id[cid] for cid in reviews if cid in by_id]
    scores: list[dict[str, Any]] = []

    for persona in personas:
        perspective = perspective_of(persona)
        attribution = "perspective" if perspective in known else "all"
        effective = perspective if attribution == "perspective" else None
        detected = seeded = 0
        clean_diffs = clean_findings = 0
        missed: list[str] = []
        missed_detail: list[dict[str, Any]] = []
        for case in scored_cases:
            text = reviews[case["id"]].get(persona)
            if text is None:
                continue
            row = score_review(case, text, effective)
            if row["clean"]:
                clean_diffs += 1
                clean_findings += int(row["flagged"])
                continue
            detected += row["detected"]
            seeded += row["seeded"]
            for d in row["detections"]:
                if d["detected"]:
                    continue
                missed.append(str(d["category"]))
                missed_detail.append({
                    "case": case["id"],
                    "violation": d["violation"],
                    "category": d["category"],
                    "severity": d["severity"],
                })
        score: dict[str, Any] = {
            "reviewer": persona,
            "detected": detected,
            "seeded": seeded,
            "missed": missed,
            "missed_detail": missed_detail,
            "attribution": attribution,
            "clean_diffs": clean_diffs,
            "clean_findings": clean_findings,
        }
        if clean_diffs:
            score["clean_fp_rate"] = round(clean_findings / clean_diffs, 3)
        scores.append(score)

    planted = sum(len(c.get("violations") or []) for c in scored_cases if not c.get("clean"))
    return {
        "ts": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "corpus": meta.get("corpus", "fixture"),
        "corpus_version": meta.get("corpus_version"),
        "seeds": planted,
        "valid_seeds": planted,
        "clean_diffs": sum(1 for c in scored_cases if c.get("clean")),
        "cases": [c["id"] for c in scored_cases],
        "scores": scores,
    }


def materialize_case(
    case_dir: pathlib.Path, into: pathlib.Path | None = None
) -> pathlib.Path:
    """Commit base/, then lay head/ over it as uncommitted working-tree changes."""
    if into is None:
        workspace = pathlib.Path(tempfile.mkdtemp(prefix=f"drill-{case_dir.name}-"))
    else:
        workspace = pathlib.Path(into)
        workspace.mkdir(parents=True, exist_ok=True)
    shutil.copytree(case_dir / "base", workspace, dirs_exist_ok=True)
    git = ["git", "-c", "user.name=rig-drill", "-c", "user.email=drill@rig.local"]
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=workspace, check=True, capture_output=True)
    subprocess.run(
        [*git, "commit", "-q", "-m", "base"], cwd=workspace, check=True, capture_output=True
    )
    for path in (case_dir / "head").iterdir():
        if path.is_dir():
            shutil.copytree(path, workspace / path.name, dirs_exist_ok=True)
        else:
            shutil.copy2(path, workspace / path.name)
    return workspace


# ── CLI ──────────────────────────────────────────────────────────────────────


def _resolve_review(value: str) -> str:
    """A review is inline text, or `@path` to read it from a file."""
    if value.startswith("@"):
        return pathlib.Path(value[1:]).read_text(encoding="utf-8")
    return value


def cmd_drill_corpus(args: argparse.Namespace) -> None:
    cases = load_cases(args.cases or None)
    if not cases:
        print(f"no cases found under {corpus_root()}")
        return

    if args.action == "list":
        meta = load_corpus_meta()
        if args.json:
            print(json.dumps({
                "corpus": meta.get("corpus", "fixture"),
                "corpus_version": meta.get("corpus_version"),
                "root": str(corpus_root()),
                "cases": [
                    {
                        "id": c["id"],
                        "language": c.get("language"),
                        "clean": bool(c.get("clean")),
                        "violations": [v["id"] for v in c.get("violations") or []],
                    }
                    for c in cases
                ],
            }, ensure_ascii=False))
            return
        print(f"## drill fixture corpus v{meta.get('corpus_version')} ({corpus_root()})")
        for case in cases:
            kind = ("clean (measures false positives)" if case.get("clean")
                    else f"{len(case['violations'])} planted defects")
            print(f"  {case['id']:24s} {case.get('language', ''):11s} {kind}")
            for violation in case.get("violations") or []:
                perspectives = ", ".join(violation.get("perspectives") or []) or "-"
                print(f"      {violation['id']:26s} {violation['severity']:9s} {perspectives}")
        return

    if args.action == "materialize":
        if not args.case:
            print("materialize needs a case id (see `list`)")
            return
        target = [c for c in cases if c["id"] == args.case]
        if not target:
            print(f"unknown case: {args.case}")
            return
        workspace = materialize_case(target[0]["_dir"], args.into)
        print(workspace)
        return

    # action == "score"
    if not args.reviews:
        print("score needs --reviews <path.json> ({case-id: {persona: review text or @path}})")
        return
    raw = json.loads(pathlib.Path(args.reviews).read_text(encoding="utf-8"))
    reviews = {
        case_id: {p: _resolve_review(text) for p, text in per_case.items()}
        for case_id, per_case in raw.items()
    }
    unknown = sorted(set(reviews) - {c["id"] for c in cases})
    if unknown:
        print(f"unknown case id(s) in --reviews: {', '.join(unknown)}")
        return
    row = build_drill_row(reviews, cases)
    line = json.dumps(row, ensure_ascii=False)
    if args.append:
        path = pathlib.Path(args.append)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print(f"appended to {path}")
    print(line)
