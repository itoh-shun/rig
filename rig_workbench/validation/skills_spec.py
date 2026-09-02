"""validation skills_spec: every SKILL.md against the Agent Skills specification (#550).

rig checks its own conventions thoroughly and, until this, did not check the one format it
shares with the outside: the Agent Skills spec is what `/rig:import` parses and what
`/rig:export` claims to emit. The spec's MUST-level rules are all mechanical, which is what
makes them worth automating; its SHOULDs are judgement calls and stay warnings, because a
check that fails on a judgement call teaches people to disable it.
"""

import pathlib
import re

from .config import ROOT
from .state import _emit, parse_frontmatter

#: `name`: 1–64 characters of lowercase `a-z0-9` and `-`, no leading or trailing hyphen, no
#: consecutive hyphens. The pattern is the whole rule; the length is checked separately so the
#: message can say which of the two was broken.
NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_MAX = 64
DESCRIPTION_MAX = 1024
COMPATIBILITY_MAX = 500
#: The spec recommends keeping SKILL.md under this many lines, because the whole body loads
#: on activation. A recommendation, so a warning.
BODY_LINES_SHOULD = 500

#: Directories no skill lives under. `.git` and dependency trees hold copies of other
#: people's files; a SKILL.md there is not one this repository ships.
_SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", ".rig"})


def skill_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Every SKILL.md under `root`, skipping directories no skill lives under."""
    found = []
    for path in sorted(root.rglob("SKILL.md")):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        found.append(path)
    return found


def skill_spec_findings(fm: dict | None, body: str, directory_name: str) -> list[tuple[str, str]]:
    """(level, message) for each way a skill's frontmatter and body depart from the spec.

    Takes the parsed frontmatter, the body and the parent directory's name rather than a
    path, so a test can hand it a skill that must be objected to without writing a tree.
    An empty list is conformance; a `FAIL` is a MUST the spec states; a `WARN` is a SHOULD.
    """
    if fm is None:
        return [("FAIL", "frontmatter cannot be parsed as YAML")]
    if not isinstance(fm, dict):
        return [("FAIL", "frontmatter is not a mapping")]
    findings: list[tuple[str, str]] = []

    name = fm.get("name")
    if not isinstance(name, str) or not name:
        findings.append(("FAIL", "name is required and must be a non-empty string"))
    else:
        if len(name) > NAME_MAX:
            findings.append(("FAIL", f"name is {len(name)} characters; the spec allows at most "
                                     f"{NAME_MAX}"))
        if not NAME_PATTERN.match(name):
            findings.append(("FAIL", f"name {name!r} must be lowercase a-z, 0-9 and hyphens "
                                     f"only, with no leading, trailing or consecutive hyphens"))
        if name != directory_name:
            findings.append(("FAIL", f"name {name!r} does not match the parent directory "
                                     f"{directory_name!r}; the spec requires them to be equal"))

    description = fm.get("description")
    if not isinstance(description, str) or not description.strip():
        findings.append(("FAIL", "description is required and must be a non-empty string"))
    elif len(description) > DESCRIPTION_MAX:
        findings.append(("FAIL", f"description is {len(description)} characters; the spec "
                                 f"allows at most {DESCRIPTION_MAX}"))

    if "compatibility" in fm:
        compatibility = fm["compatibility"]
        if not isinstance(compatibility, str) or not compatibility.strip():
            findings.append(("FAIL", "compatibility, when present, must be a non-empty string"))
        elif len(compatibility) > COMPATIBILITY_MAX:
            findings.append(("FAIL", f"compatibility is {len(compatibility)} characters; the "
                                     f"spec allows at most {COMPATIBILITY_MAX}"))

    if "metadata" in fm:
        metadata = fm["metadata"]
        if not isinstance(metadata, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in metadata.items()):
            findings.append(("FAIL", "metadata, when present, must be a map of string to string"))

    if "license" in fm and not isinstance(fm["license"], str):
        findings.append(("FAIL", "license, when present, must be a string"))

    if "allowed-tools" in fm and not isinstance(fm["allowed-tools"], str):
        findings.append(("FAIL", "allowed-tools, when present, must be a space-separated "
                                 "string"))

    body_lines = body.count("\n") + (1 if body and not body.endswith("\n") else 0)
    if body_lines > BODY_LINES_SHOULD:
        findings.append(("WARN", f"body is {body_lines} lines; the spec recommends keeping "
                                 f"SKILL.md under {BODY_LINES_SHOULD} and moving detail into "
                                 f"referenced files, because the whole body loads on activation"))
    return findings


def check_skills_spec() -> None:
    """Every SKILL.md in the tree against the Agent Skills spec's frontmatter rules.

    Per file, so a report names which skill departs and how. FAIL for a MUST, WARN for a
    SHOULD, and a tree with no SKILL.md at all is a FAIL: this repository ships at least
    its own engine as a skill, and finding none means the walk is looking in the wrong
    place, not that everything conforms.
    """
    files = skill_files(ROOT)
    if not files:
        _emit("FAIL", "skills spec — no SKILL.md found under the repository; the walk this "
                      "check relies on has changed shape")
        return
    ok = 0
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        fm, body = parse_frontmatter(path)
        findings = skill_spec_findings(fm, body, path.parent.name)
        for level, msg in findings:
            _emit(level, f"skills spec {rel} — {msg}")
        if not any(level == "FAIL" for level, _ in findings):
            ok += 1
    _emit("PASS", f"skills spec: {ok}/{len(files)} SKILL.md conform to the Agent Skills "
                  f"frontmatter rules")
