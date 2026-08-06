"""rig-wb asvs — which ASVS chapters rig can actually see, and which it cannot.

The point of a verification standard is to decide *before* writing code what has to
hold, so the inspection surface shrinks to something a review can finish. rig cannot
adopt ASVS on a project's behalf — the requirements belong to the product, not to the
harness. What rig can do is state honestly, chapter by chapter, which of its own
mechanisms touch that ground: a drill seed class, a deterministic sensor, a reviewer
lens, a whole-repo scan.

`evals/asvs-map.json` is that statement, and this command reads it. The useful half
is the empty rows. A chapter with `strength: none` means **nothing in rig will notice
a defect there** — session management, token handling, TLS, WebRTC. Those need a
separate instrument or a human, and a map that quietly omitted them would be worse
than no map, because it would read as coverage.

Strength is deliberately coarse:

    measured   a deterministic sensor or a drill class whose detection rate is scored
    partial    something looks at part of this chapter; the rest is stated in the row
    none       rig has no mechanism here

`--check` verifies the map against the repository: every referenced file must exist
and every drill class must still be in the shipped corpus. That is the same guard the
coverage map gets — a mapping whose references have rotted is a claim without backing.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

MAP_VERSION = 1
DEFAULT_MAP = "evals/asvs-map.json"
DRILL_CORPUS = "skills/engine/facets/instructions/drill.md"
STRENGTHS = ("measured", "partial", "none")
EVIDENCE_KINDS = ("sensor", "drill-class", "reviewer", "sast", "command")
EXPECTED_CHAPTERS = tuple(f"V{n}" for n in range(1, 18))


class AsvsMapError(Exception):
    """The ASVS map is malformed or has drifted from the repository."""


def repo_root(start: pathlib.Path | None = None) -> pathlib.Path:
    here = (start or pathlib.Path(__file__).resolve().parent).resolve()
    for candidate in (here, *here.parents):
        if (candidate / DEFAULT_MAP).exists():
            return candidate
    return here


def load_map(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AsvsMapError(f"ASVS map not readable: {path} ({exc})") from exc
    except ValueError as exc:
        raise AsvsMapError(f"ASVS map is not valid JSON: {path} ({exc})") from exc
    if not isinstance(data, dict) or data.get("asvs_map_version") != MAP_VERSION:
        raise AsvsMapError(f"unsupported asvs_map_version (expected {MAP_VERSION})")
    if not isinstance(data.get("chapters"), list) or not data["chapters"]:
        raise AsvsMapError("ASVS map must carry a non-empty 'chapters' array")
    return data


def _drill_classes(root: pathlib.Path) -> str:
    path = root / DRILL_CORPUS
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def validate(data: dict, root: pathlib.Path) -> list[str]:
    problems: list[str] = []
    corpus = _drill_classes(root)
    seen: list[str] = []
    for chapter in data["chapters"]:
        if not isinstance(chapter, dict):
            problems.append("chapters must be objects")
            continue
        cid = chapter.get("id")
        if not isinstance(cid, str) or not cid:
            problems.append("every chapter needs an 'id'")
            continue
        seen.append(cid)
        for field in ("title", "not_covered"):
            if not isinstance(chapter.get(field), str) or not chapter[field].strip():
                problems.append(f"{cid}: missing {field!r}")
        strength = chapter.get("strength")
        if strength not in STRENGTHS:
            problems.append(f"{cid}: strength must be one of {STRENGTHS}")
        covered = chapter.get("covered_by")
        if not isinstance(covered, list):
            problems.append(f"{cid}: 'covered_by' must be a list")
            continue
        if strength == "none" and covered:
            problems.append(f"{cid}: strength 'none' cannot list covering mechanisms")
        if strength in ("measured", "partial") and not covered:
            problems.append(f"{cid}: strength {strength!r} needs at least one covering mechanism")
        for entry in covered:
            problems.extend(_validate_entry(cid, entry, root, corpus))
    missing = [c for c in EXPECTED_CHAPTERS if c not in seen]
    if missing:
        problems.append(f"chapters missing from the map: {', '.join(missing)}")
    duplicates = sorted({c for c in seen if seen.count(c) > 1})
    if duplicates:
        problems.append(f"duplicate chapter ids: {', '.join(duplicates)}")
    return problems


def _validate_entry(cid: str, entry: object, root: pathlib.Path, corpus: str) -> list[str]:
    if not isinstance(entry, dict):
        return [f"{cid}: covering mechanisms must be objects"]
    problems: list[str] = []
    kind = entry.get("kind")
    ref = entry.get("ref")
    if kind not in EVIDENCE_KINDS:
        return [f"{cid}: unknown mechanism kind {kind!r}"]
    if not isinstance(ref, str) or not ref.strip():
        return [f"{cid}: mechanism needs a 'ref'"]
    if not isinstance(entry.get("note", ""), str):
        problems.append(f"{cid}: 'note' must be a string when present")
    if kind == "drill-class":
        if ref not in corpus:
            problems.append(f"{cid}: drill class {ref!r} is not in the shipped corpus")
    elif "/" in ref and ref.endswith((".py", ".md", ".json")):
        if ".." in pathlib.PurePosixPath(ref).parts or ref.startswith("/"):
            problems.append(f"{cid}: ref must be repo-relative without '..': {ref!r}")
        elif not (root / ref).exists():
            problems.append(f"{cid}: referenced path does not exist: {ref}")
    return problems


def summarise(data: dict) -> dict:
    counts = {strength: 0 for strength in STRENGTHS}
    for chapter in data["chapters"]:
        counts[chapter["strength"]] = counts.get(chapter["strength"], 0) + 1
    return {"chapters": len(data["chapters"]), "by_strength": counts,
            "blind": [c["id"] for c in data["chapters"] if c["strength"] == "none"]}


def _print_report(data: dict) -> None:
    print(f"## rig-wb asvs — ASVS {data.get('asvs_release', '?')} の章と、rig の検査面の対応\n")
    print("これは ASVS 要件の実装状況ではなく、rig が「気づける範囲」の地図である。")
    print("空の章は、そこに欠陥があっても rig の側では誰も気づかないという意味になる。\n")
    for chapter in data["chapters"]:
        mark = {"measured": "measured", "partial": "partial ", "none": "none    "}[chapter["strength"]]
        print(f"[{mark}] {chapter['id']:<4} {chapter['title']}")
        for entry in chapter["covered_by"]:
            print(f"           - {entry['kind']}: {entry['ref']}")
        print(f"           未対応: {chapter['not_covered']}")
        print()
    summary = summarise(data)
    counts = summary["by_strength"]
    print(f"## {summary['chapters']} 章 — measured {counts['measured']} / "
          f"partial {counts['partial']} / none {counts['none']}")
    if summary["blind"]:
        print(f"   rig では気づけない章: {', '.join(summary['blind'])}")
    if data.get("titles_transcribed"):
        print(f"\n注記: {data.get('titles_note', '章タイトルは転記である。公式の目次と突き合わせること。')}")


def cmd_asvs(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="rig-wb asvs",
        description="Show which ASVS chapters rig's own mechanisms can see, and which it cannot.",
    )
    parser.add_argument("--map", default=None, help=f"path to the ASVS map (default: {DEFAULT_MAP})")
    parser.add_argument("--check", action="store_true",
                        help="verify the map against the repository and exit non-zero on drift")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of the text report")
    args = parser.parse_args(argv)

    root = repo_root(pathlib.Path.cwd())
    map_path = pathlib.Path(args.map) if args.map else root / DEFAULT_MAP
    try:
        data = load_map(map_path)
    except AsvsMapError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    problems = validate(data, root)
    if problems:
        print("[ERROR] ASVS map has drifted from the repository:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    if args.check:
        print(f"ASVS map is consistent ({len(data['chapters'])} chapters).")
        return 0
    if args.json:
        print(json.dumps({"summary": summarise(data), **data}, ensure_ascii=False,
                         indent=2, sort_keys=True))
    else:
        _print_report(data)
    return 0
