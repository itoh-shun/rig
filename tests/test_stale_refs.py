"""Stale path-reference check for the manifest/knowledge layer (#316).

Covers the conservative extraction rules (what is and isn't a checkable
reference), ancestor-walk resolution, exclude prefixes, and the CLI's
default-surface / explicit-path / advisory-exit-code behavior.
"""

import pathlib
import subprocess
import sys

from rig_workbench.workbench.stale_refs import (extract_path_refs,
                                                scan_stale_refs)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


# ---- extraction rules --------------------------------------------------------

def test_backticked_relative_path_with_extension_is_extracted():
    assert extract_path_refs("see `scripts/workbench.py` for details") == [(1, "scripts/workbench.py")]


def test_directory_reference_needs_two_segments():
    # Two segments with trailing slash: checkable. One bare segment: generic namespace, skipped.
    assert extract_path_refs("in `facets/knowledge/`") == [(1, "facets/knowledge/")]
    assert extract_path_refs("under `personas/`") == []


def test_urls_absolute_paths_and_placeholders_are_skipped():
    text = ("`https://example.com/x.md` `/etc/passwd` `~/notes.md` "
            "`path/to/file.md` `<repo>/x.md` `src/*.ts` `a/{b}.md` `docs/….md`")
    assert extract_path_refs(text) == []


def test_unquoted_paths_are_skipped_by_design():
    assert extract_path_refs("see scripts/workbench.py for details") == []


def test_code_fences_are_skipped():
    text = "```\n`dead/ref.md`\n```\n`also/dead.md`"
    assert extract_path_refs(text) == [(4, "also/dead.md")]


# ---- resolution --------------------------------------------------------------

def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_reference_is_a_finding(tmp_path):
    doc = _write(tmp_path, "doc.md", "see `lib/gone.py`")
    findings = scan_stale_refs(tmp_path, [doc])
    assert [(f["file"], f["ref"]) for f in findings] == [("doc.md", "lib/gone.py")]


def test_existing_reference_is_not_a_finding(tmp_path):
    _write(tmp_path, "lib/here.py", "x = 1\n")
    doc = _write(tmp_path, "doc.md", "see `lib/here.py`")
    assert scan_stale_refs(tmp_path, [doc]) == []


def test_ancestor_walk_resolves_contextual_roots(tmp_path):
    # A doc deep in a tree referencing a path relative to an ancestor dir
    # (the SKILL.md-speaks-skill-dir-relative pattern) must not be flagged.
    _write(tmp_path, "skills/rig/facets/knowledge/page.md", "content")
    doc = _write(tmp_path, "skills/rig/facets/instructions/how.md",
                 "inject `facets/knowledge/page.md`")
    assert scan_stale_refs(tmp_path, [doc]) == []


def test_exclude_prefixes_are_skipped(tmp_path):
    doc = _write(tmp_path, "doc.md", "user projects keep `.claude/rig.md` and `video/out/`")
    findings = scan_stale_refs(tmp_path, [doc], exclude_prefixes=(".claude/", "video/"))
    assert findings == []


# ---- CLI ---------------------------------------------------------------------

def _run_cli(args, cwd):
    return subprocess.run([sys.executable, str(WORKBENCH), "stale-refs", *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def test_cli_default_surfaces_and_advisory_exit(tmp_path):
    root = _git_repo(tmp_path)
    _write(root, ".claude/rig.md", "build with `scripts/build.sh`")  # missing -> finding
    _write(root, ".claude/rig/knowledge/domain/auth.md", "see `docs/auth.md`")  # missing -> finding
    r = _run_cli([], root)
    assert r.returncode == 0  # advisory: findings never change the exit code
    assert "2 stale reference(s)" in r.stdout
    assert "scripts/build.sh" in r.stdout and "docs/auth.md" in r.stdout


def test_cli_clean_project(tmp_path):
    root = _git_repo(tmp_path)
    _write(root, "scripts/build.sh", "#!/bin/sh\n")
    _write(root, ".claude/rig.md", "build with `scripts/build.sh`")
    r = _run_cli([], root)
    assert r.returncode == 0
    assert "No stale references" in r.stdout


def test_cli_rig_runtime_state_is_excluded(tmp_path):
    root = _git_repo(tmp_path)
    _write(root, ".claude/rig.md", "state lives in `.rig/runs/task.json`")
    r = _run_cli([], root)
    assert "No stale references" in r.stdout


def test_cli_explicit_paths(tmp_path):
    root = _git_repo(tmp_path)
    _write(root, "notes/setup.md", "run `tools/gen.py` first")
    r = _run_cli(["notes/"], root)
    assert r.returncode == 0
    assert "tools/gen.py" in r.stdout
