"""Shipping a thing and telling anyone about it are separate acts (#421, #392, #395, #385).

The same defect has now been filed at least ten times — #221, #327, #337, #353, #385,
#392, #395, #412, #417, #421 — always in the same shape: something is implemented,
tested, documented in its own file, and reachable from nowhere a reader would look.
Each issue was repaired by hand and each repair held. What recurred is that nothing
was watching, and #395 says so outright: `rig-evidence` and `rig-mission-control` are
CLI entry points rather than bricks, so `--validate`'s catalog-drift check cannot see
them at all.

This is the watcher for the two registries that check nobody kept:

- **`docs/`** — a document that exists but is linked from neither README is a
  document only `ls` will find.
- **`[project.scripts]`** — an installed command absent from SKILL.md §2 is a
  command the engine's own inventory denies having.

Both allow an explicit way out, and neither allows a silent one. `UNINDEXED_DOCS`
names the files deliberately kept out of the README index together with where a
reader does reach them, and it is checked against the filesystem so it cannot decay
into cover for a file nobody meant to hide.
"""

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
README_EN = REPO_ROOT / "README.md"
README_JA = REPO_ROOT / "README.ja.md"
SKILL_MD = REPO_ROOT / "skills" / "engine" / "SKILL.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Documents that belong somewhere other than the README index, with the route a
# reader actually takes to each. Adding a name here is a decision to be defended in
# review; leaving a new document out of both lists is not a decision at all.
UNINDEXED_DOCS = {
    "CHANGELOG-archive.md": "linked from CHANGELOG.md, where a reader looking for old entries already is",
    # Design notes for the japanese-writing pack's prosody work. They are cited by the
    # code that implements them (scripts/prose_rhythm.py, benchmarks/writing-tasks/
    # jp-natural-writing/affect_state.py) rather than read as user-facing docs.
    "jp-affect-seed-design.ja.md": "cited from the affect-state implementation",
    "jp-affect-seed-results.ja.md": "cited from the affect-state implementation",
    "jp-corpus-genre-control.ja.md": "cited from the prosody implementation",
    "jp-four-genre-probe.ja.md": "cited from the prosody implementation",
    "jp-humanness-redefinition.ja.md": "cited from the prosody implementation",
    "jp-indistinguishability-criterion.ja.md": "cited from the prosody implementation",
    "jp-naturalness-engineering.ja.md": "cited from the prosody implementation",
    "jp-prior-art-crosscheck.ja.md": "cited from the prosody implementation",
}


def shipped_docs():
    return sorted(p.name for p in (REPO_ROOT / "docs").glob("*.md"))


def entry_points():
    """`name = "module:main"` lines under [project.scripts]."""
    text = PYPROJECT.read_text(encoding="utf-8")
    section = text.split("[project.scripts]", 1)[1].split("\n[", 1)[0]
    return sorted(re.findall(r"^([a-z][a-z0-9-]*)\s*=", section, re.M))


@pytest.mark.parametrize("readme", [README_EN, README_JA], ids=["en", "ja"])
def test_every_shipped_document_is_reachable_from_the_readme_index(readme):
    text = readme.read_text(encoding="utf-8")
    missing = [name for name in shipped_docs()
               if name not in UNINDEXED_DOCS and f"docs/{name}" not in text]
    assert not missing, (
        f"{readme.name} links none of {missing} — a document reachable only by "
        "listing docs/ is a document the reader never learns exists. Link it, or "
        "name it in UNINDEXED_DOCS with the route that does reach it."
    )


def test_the_unindexed_list_names_only_documents_that_exist():
    stale = sorted(set(UNINDEXED_DOCS) - set(shipped_docs()))
    assert not stale, (
        f"UNINDEXED_DOCS still excuses {stale}, which docs/ no longer contains — a "
        "stale exemption quietly pre-approves the next file of the same name."
    )


def test_every_installed_command_appears_in_the_engine_inventory():
    """SKILL.md §2 calls itself the inventory and `--validate` checks it against the
    brick files. Entry points are not bricks, so nothing checked them — which is the
    hole #395 was filed in."""
    inventory = SKILL_MD.read_text(encoding="utf-8")
    missing = [name for name in entry_points() if name not in inventory]
    assert not missing, (
        f"skills/engine/SKILL.md §2 never names {missing}, though pyproject installs "
        "them — the inventory that calls itself canonical is denying a shipped command."
    )


@pytest.mark.parametrize("readme", [README_EN, README_JA], ids=["en", "ja"])
def test_the_prompt_evaluation_gate_is_documented_where_the_other_evidence_is(readme):
    """#385: `rig-wb eval affected --ratchet` decides whether a prompt-surface change
    is backed by an approved case. Its neighbours in the evidence table (`rig-wb
    coverage`, `rig-wb asvs`) are listed; it was not."""
    text = readme.read_text(encoding="utf-8")
    assert "eval affected" in text and "--ratchet" in text
