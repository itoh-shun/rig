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
- **`[project.scripts]`** — an installed command absent from §2 (`BRICKS.md`) is
  a command the engine's own inventory denies having.

Both allow an explicit way out, and neither allows a silent one. `UNINDEXED_DOCS`
names the files deliberately kept out of the README index together with where a
reader does reach them, and it is checked against the filesystem so it cannot decay
into cover for a file nobody meant to hide.
"""

import fnmatch
import pathlib
import re

import pytest

from rig_workbench.validation import catalog

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
README_EN = REPO_ROOT / "README.md"
README_JA = REPO_ROOT / "README.ja.md"
SKILL_MD = REPO_ROOT / "skills" / "engine" / "SKILL.md"
#: The engine's inventory, which is `SKILL.md` plus the reference files it defers its longer
#: sections to: §2 — the inventory proper — moved to `BRICKS.md` when `SKILL.md` was cut down
#: to what a first turn needs, so reading `SKILL.md` alone now asks the summary rather than
#: the list. Borrowed from the check that draws the same line, so both move together.
INVENTORY_MD = [REPO_ROOT / "skills" / "engine" / name
                for name in catalog.INVENTORY_DOCUMENTS]
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Documents that belong somewhere other than the README index, with the route a
# reader actually takes to each. Adding a name here is a decision to be defended in
# review; leaving a new document out of both lists is not a decision at all.
UNINDEXED_DOCS = {
    "CHANGELOG-archive.md": "linked from CHANGELOG.md, where a reader looking for old entries already is",
    # The rationale behind the pack model rather than instructions for using it, so it belongs
    # under packs.md — which the README does index — instead of beside it.
    "pack-vnext-design-brief.ja.md": "linked from docs/packs.md as the design rationale",
    # Design notes for the Japanese-writing prosody work. They are cited by the
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


#: Entry points the engine's §2 inventory still omits, recorded rather than asserted away.
#: A ratchet, in the same shape as the prompt-coverage gate: existing debt is named and
#: allowed, growth is not. Closing one means editing `skills/engine/BRICKS.md`, which is a
#: covered prompt surface — the evaluation gate then wants freshly signed evidence, so it is
#: a maintainer task with the attestation key rather than something this test can slip in.
#: Naming them here is the difference between a debt somebody decided to carry and a gap
#: nobody knew about, which is the whole complaint in #395.
SKILL_INVENTORY_DEBT = {"rig-mcp", "rig-mission-control-live"}


def _missing_from_inventory() -> set[str]:
    inventory = "\n".join(path.read_text(encoding="utf-8") for path in INVENTORY_MD)
    return {name for name in entry_points() if name not in inventory}


def test_the_engine_inventory_gap_does_not_grow():
    """§2 calls itself the inventory and `--validate` checks it against the brick
    files. Entry points are not bricks, so nothing checked them at all — the hole #395 was
    filed in. This does not close the hole; it stops the next command falling into it."""
    new = sorted(_missing_from_inventory() - SKILL_INVENTORY_DEBT)
    assert not new, (
        f"the engine's inventory never names {new}, though pyproject installs them — the "
        "inventory that calls itself canonical is denying a shipped command. Add the row, "
        "or add the name to SKILL_INVENTORY_DEBT and say why it has to wait."
    )


def test_the_recorded_inventory_debt_is_still_real():
    """A name that has since been added to §2 must leave this list. An exemption that has
    stopped applying is not harmless: it silently pre-approves the next command of that
    name, which is how a ratchet stops ratcheting."""
    settled = sorted(SKILL_INVENTORY_DEBT - _missing_from_inventory())
    assert not settled, (
        f"SKILL_INVENTORY_DEBT still excuses {settled}, which the inventory now names — "
        "remove them so the list keeps meaning what it says."
    )


def test_every_installed_command_is_reachable_from_something_a_reader_opens():
    """The weaker claim that can be true today, and is worth holding on its own: a command
    absent from the inventory *and* from both READMEs exists only in `pyproject.toml`.
    `rig-mission-control-live` was exactly that until this was written."""
    reachable = "\n".join(path.read_text(encoding="utf-8")
                          for path in [*INVENTORY_MD, README_EN, README_JA])
    invisible = [name for name in entry_points() if name not in reachable]
    assert not invisible, (
        f"{invisible} is installed by pyproject and named in no inventory and no README — "
        "a command a reader can only find by listing their PATH."
    )


@pytest.mark.parametrize("readme", [README_EN, README_JA], ids=["en", "ja"])
def test_the_prompt_evaluation_gate_is_documented_where_the_other_evidence_is(readme):
    """#385: `rig-wb eval affected --ratchet` decides whether a prompt-surface change
    is backed by an approved case. Its neighbours in the evidence table (`rig-wb
    coverage`, `rig-wb asvs`) are listed; it was not."""
    text = readme.read_text(encoding="utf-8")
    assert "eval affected" in text and "--ratchet" in text


# ── a documented command has to be a command ────────────────────────────────


#: What a reader is told to type in `rig-wb <sub>` form.
RIG_WB_SUBCOMMAND = re.compile(r"rig-wb\s+([a-z][a-z0-9-]*)")

#: Verbs one document names in order to argue about them rather than to tell anyone to type
#: them, keyed by the document and listed one by one.
#:
#: `docs/v3-architecture-design-brief.ja.md` §11 row T4 quotes `rig-wb list` and `rig-wb
#: review` as the two verbs it proposes to delete, and its measurement table quotes them
#: again with the exit code they returned. A reader following those lines is being shown a
#: removal, not given an instruction — and scanning them would leave the brief unable to
#: record a verb it wants gone without this test demanding the verb be kept.
#:
#: Two names, not the document. Excusing the whole file would have taken its `rig-wb check`
#: and `rig-wb pack` mentions out of the scan too, and those are ordinary instructions with
#: nothing wrong with them: a broad exemption written for two sentences would have stopped
#: checking the rest of the brief for as long as it stood.
VERBS_A_DOCUMENT_QUOTES_RATHER_THAN_PRESCRIBES = {
    "v3-architecture-design-brief.ja.md": frozenset({"list", "review"}),
}


def documented_rig_wb_subcommands() -> set[str]:
    """Every `rig-wb <sub>` a reader is told to type, across the READMEs and docs/."""
    names: set[str] = set()
    sources = [README_EN, README_JA, SKILL_MD, *sorted((REPO_ROOT / "docs").glob("*.md"))]
    for path in sources:
        found = set(RIG_WB_SUBCOMMAND.findall(path.read_text(encoding="utf-8")))
        names |= found - VERBS_A_DOCUMENT_QUOTES_RATHER_THAN_PRESCRIBES.get(
            path.name, frozenset())
    return names


def test_every_excused_verb_is_still_quoted_in_the_document_it_was_excused_for():
    """An exemption has to keep matching the sentences it was written for.

    Not "the file still exists": the file outlives any wording, and an exemption that
    survives the paragraph it was granted for is a hole nobody is looking at. So each name
    is checked to be still quoted in that document. Reword the brief so it stops naming
    `rig-wb list`, or delete the file, and the entry dies here instead of quietly excusing a
    verb somebody has since put back.
    """
    for name, verbs in VERBS_A_DOCUMENT_QUOTES_RATHER_THAN_PRESCRIBES.items():
        path = REPO_ROOT / "docs" / name
        assert path.is_file(), (
            f"docs/{name} is excused from the `rig-wb <sub>` scan and does not exist. "
            "Drop the entry: an exemption that matches no file cannot be reviewed."
        )
        quoted = set(RIG_WB_SUBCOMMAND.findall(path.read_text(encoding="utf-8")))
        stale = sorted(verbs - quoted)
        assert not stale, (
            f"docs/{name} no longer names `rig-wb {stale}`, which it is excused for. "
            "Drop the name from the entry: the exemption is now excusing the verb "
            "everywhere in that file for no reason anybody can read."
        )


def test_every_documented_subcommand_is_one_rig_wb_will_accept():
    """`rig-wb perf` and `rig-wb otel` shipped in 2.8.0, were written up in the READMEs
    eighteen times between them — including a line meant to be pasted into a CI job — and
    answered "Unknown sub-command" every time. Both features worked; neither name was on the
    delegation list, and nothing compared the two.

    Documentation drift is usually a stale sentence. This is the sharper kind: instructions
    that fail the moment somebody follows them, on the release's own headline features.
    """
    from rig_workbench.cli import _orch_delegates  # noqa: PLC2701 - the list under test

    accepted = set(_orch_delegates) | _own_subcommands()
    missing = sorted(name for name in documented_rig_wb_subcommands()
                     if name not in accepted)
    assert not missing, (
        f"the docs tell a reader to run `rig-wb {missing}`, which rig-wb does not accept — "
        "either route the subcommand or stop documenting it in that form."
    )


def _own_subcommands() -> set[str]:
    """The names `rig-wb` handles itself, rather than delegating to the orchestrator.

    Read from its own dispatch rather than listed here, so a command that moves between the
    two halves does not turn into a false report from this file.
    """
    text = (REPO_ROOT / "rig_workbench" / "cli.py").read_text(encoding="utf-8")
    return set(re.findall(r'^\s*(?:elif|if)\s+sub\s*==\s*"([a-z][a-z0-9-]*)"', text, re.M))


#: `[tool.setuptools.package-data]`'s entry for the engine's own directory. Read as globs,
#: because that is what setuptools matches names against.
def _engine_package_data() -> list[str]:
    text = PYPROJECT.read_text(encoding="utf-8")
    section = text.split('"skills.engine" = [', 1)[1].split("]", 1)[0]
    return re.findall(r'"([^"]+)"', section)


def test_the_wheel_ships_every_document_the_entry_document_defers_to():
    """`SKILL.md` is read whole on every activation, so its longest sections live in sibling
    files and it keeps a summary and a reference. Shipping the entry document without them
    ships a document whose references resolve to nothing.

    Measured before this existed: dropping `RESOLVE.md` from `package-data` passed all 81
    packaging tests. The patterns are matched the way setuptools matches them, so the current
    `*.md` glob and a future explicit list both satisfy this.
    """
    patterns = _engine_package_data()
    unshipped = [name for name in catalog.INVENTORY_DOCUMENTS
                 if not any(fnmatch.fnmatch(name, pattern) for pattern in patterns)]
    assert not unshipped, (
        f"pyproject's skills.engine package-data ({patterns}) does not ship {unshipped}, "
        f"which SKILL.md defers its sections to — an installed rig would carry an entry "
        f"document pointing at files that are not there")


def test_the_wheel_ships_the_pack_detail_rows_the_catalogue_points_at():
    """The same claim for `PACKS.md`, which §2 names as the source of truth for what each
    pack does. It was absent from this list for as long as it has existed."""
    patterns = _engine_package_data()
    assert any(fnmatch.fnmatch("PACKS.md", pattern) for pattern in patterns), (
        f"pyproject's skills.engine package-data ({patterns}) does not ship PACKS.md, which "
        f"§2 calls the source of truth for the pack rows")
