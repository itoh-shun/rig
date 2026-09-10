"""The brick-resolution declaration against the resolver, and the prose against both.

`rig_workbench/registry/bricks.py` writes down, as data, which directories rig searches for
each asset kind and in what order. This file is what makes that writing-down worth anything:
it holds the declaration against the code, and then the shipped prose against the
declaration.

## Task 15 — the declaration against the resolver

**Nothing here reads the resolver's source.** Restating `resolve_all`'s directory list in a
test would produce a test that agrees with the source by construction — it would still pass
on the day a tier was deleted, because the restatement would be deleted with it. So the walk
is *observed*: a temporary tree is built with the four anchor roots (`ANCHORS`) side by side,
an asset called `probe` is planted in every directory the declaration names — five installed
packs, the `.claude/rig/` overlays, the shipped directories under `$RIG_HOME` — and
`resolve_all` is called for real. What comes back is every candidate it found, ranked, so the
observed sequence of directories *is* the walk. Comparing that to `bricks.WALKS` fails if the
resolver stops looking somewhere it was declared to look, starts looking somewhere it was
not, or ranks two tiers the other way round.

The order and the anchors are observed by two separate runs, because they are separable facts
and one tree cannot show both:

* Order needs `project` and `shared` to be the same directory, which is what every caller
  with one root gets. Within a single tier `resolve_all` ranks by the source path string, so
  two same-tier directories under two *different* roots are ordered by the names of those
  roots — a fact about the sort, not about tiers, and not something a declaration can state.
* The anchors need them to be different directories, which is the only way to see which of
  the two a directory hangs off. That run compares the set rather than the sequence, for the
  reason immediately above.

Two more observations back the claims the table makes in prose: the fallback walk is read out
of the resolver's own `searched:` line, and `~/.claude/rig/...` is planted and shown never to
be reached.

## Task 16 — the prose as a projection

`skills/engine/SKILL.md` and `skills/engine/facets/instructions/resolve.md` carry tier tables
of their own. They are a projection of the same fact, so they are parsed out of the shipped
markdown and compared to the declaration.

They do not agree today, so a bare assertion would be red on arrival and would stay red. The
divergences are listed instead, one entry each, in `KNOWN_PROSE_DRIFT`, and the computed
difference is asserted to be *exactly* that set.

**Fixing the prose means deleting an entry from `KNOWN_PROSE_DRIFT`.** That is the whole
design: correcting `resolve.md` without deleting its entry turns this test red, adding a new
wrong claim to either document turns it red, and changing the code so that a claim which is
true today stops being true turns it red. Nothing about the entries below approves of them —
they are a measurement of a gap that stage 2 records and does not close. Fixing the prose is
a separate change.
"""

from __future__ import annotations

import contextlib
import copy
import io
import pathlib
import re

import pytest

from rig_workbench.registry import bricks
from test_eval_cases import valid_case

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL_MD = REPO_ROOT / "skills" / "engine" / "SKILL.md"
RESOLVE_MD = REPO_ROOT / "skills" / "engine" / "facets" / "instructions" / "resolve.md"

#: The one name planted in every directory. One name in every tier is what makes the walk
#: visible: `resolve_all` returns every candidate it found, ranked, so with the same name
#: everywhere the answer is the whole search order rather than one winner.
PROBE = "probe"

#: The word each document uses for each tier, and the one translation this file performs.
#: `shipped` and `core` are the same tier under two names; that they *are* two names is
#: recorded in `KNOWN_PROSE_DRIFT` rather than being quietly absorbed here.
PROSE_TIER_NAMES = {
    "project": "project",
    "user": "user",
    "org": "org",
    "shipped": "core",
}

# ── the measured gap between the prose and the code (task 16) ────────────────
#
# Measured on 2026-09-10 against `bricks.WALKS` and the runtime observations below. Each
# entry is one divergence: what the document claims, and what the code does instead. Delete
# an entry when the document is fixed — this test goes red if a fix lands without the
# deletion, and red if a new divergence appears.
KNOWN_PROSE_DRIFT = {
    # The user tier the prose promises. `test_the_user_tier_the_prose_promises_is_not_read`
    # plants both of these paths and shows the resolver never reaches them; the user tier
    # that does exist is `~/.rig/packs/<pack>/…`, which neither document mentions.
    ("path-not-walked", "resolve.md", "recipe", "user", "user:.claude/rig/recipes"):
        "resolve.md 2.1 sends recipes to `~/.claude/rig/recipes`; no code reads it",
    ("path-not-walked", "SKILL.md", "recipe", "user", "user:.claude/rig/recipes"):
        "SKILL.md 4.2.1 repeats the same `~/.claude/rig/recipes` row",
    ("path-not-walked", "SKILL.md", "persona", "user", "user:.claude/rig/personas"):
        "SKILL.md §5 sends personas to `~/.claude/rig/personas`; no code reads it",
    # The org tier exists, but not as a directory of loose files: `pack_roots` looks under
    # `$RIG_ORG_HOME/packs/<pack>/facets/personas`. `<org_dir>/recipes` is real — the recipe
    # fallback walk reads it — which is why no recipe row appears here.
    ("path-not-walked", "SKILL.md", "persona", "org", "org:personas"):
        "SKILL.md §5 sends personas to `<org_dir>/personas`; the org tier is packs only",
    # Tiers the code walks that the table does not list at all.
    ("tier-missing-from-table", "resolve.md", "recipe", "org"):
        "resolve.md 2.1 lists three tiers; the org tier is walked (packs, and `<org>/recipes`)",
    ("tier-missing-from-table", "resolve.md", "recipe", "official"):
        "resolve.md 2.1 never mentions the official tier (`$RIG_HOME/packs/official`)",
    ("tier-missing-from-table", "SKILL.md", "recipe", "org"):
        "SKILL.md 4.2.1 omits org from the recipe table, though §5 says recipes resolve through it",
    ("tier-missing-from-table", "SKILL.md", "recipe", "official"):
        "SKILL.md 4.2.1 never mentions the official tier",
    ("tier-missing-from-table", "SKILL.md", "persona", "official"):
        "SKILL.md §5 lists four tiers; the official tier is the fifth and is walked",
    # Vocabulary.
    ("vocabulary-prose-only", "shipped"):
        "both documents call the lowest tier `shipped`; the code calls it `core`",
    ("vocabulary-code-only", "core"):
        "the other half of the same rename: `core` appears in no prose tier table",
    ("vocabulary-code-only", "official"):
        "`official` is a tier in `packs.model.TIERS` and a word the prose does not have",
}


# ── building a tree with the same brick at every tier ────────────────────────

#: One asset per kind, as `packs.model.ASSET_DIRS` lays a pack out. Agents are YAML and
#: evaluation records are JSON; everything else is markdown, and the bodies stay trivial
#: because `validate_pack` scans asset text for unsafe and injected content.
_PACK_ASSETS = {
    "recipe": ("recipes/probe.md", f"---\nname: {PROBE}\nsteps: []\n---\n"),
    "persona": ("facets/personas/probe.md", f"---\nname: {PROBE}\ndescription: probe\n---\nbody\n"),
    "instruction": ("facets/instructions/probe.md", "probe\n"),
    "pattern": ("patterns/probe.md", "probe\n"),
    "wiki": ("facets/knowledge/probe.md", "probe\n"),
    "policy": ("facets/policies/probe.md", "probe\n"),
    "output-contract": ("facets/output-contracts/probe.md", "probe\n"),
    "command": ("commands/probe.md", "probe\n"),
    "agent": ("agents/probe.yaml", f"name: {PROBE}\n"),
    "eval-result": ("evals/results/probe.json", "{}\n"),
    "resource": ("resources/probe.txt", "probe\n"),
}

#: The `.claude/rig/` overlays, which hang off the working tree, and `.rig/recipes`, which
#: hangs off the repository. Which root each one uses is the thing
#: `test_every_directory_hangs_off_the_declared_anchor` measures, so the builder is told
#: both roots and never assumes they are the same.
_OVERLAYS = (
    ("project", ".claude/rig/recipes"),
    ("project", ".claude/rig/personas"),
    ("project", ".claude/rig/knowledge"),
    ("shared", ".rig/recipes"),
)

#: The shipped directories `_core_assets` maps each kind onto, relative to `$RIG_HOME`.
_SHIPPED = {
    "recipe": "skills/engine/recipes",
    "persona": "skills/engine/facets/personas",
    "instruction": "skills/engine/facets/instructions",
    "pattern": "skills/engine/patterns",
    "wiki": "skills/engine/facets/knowledge",
    "policy": "skills/engine/facets/policies",
    "output-contract": "skills/engine/facets/output-contracts",
    "command": "commands",
    "agent": "agents",
}


def _write_pack(root: pathlib.Path, pack_id: str) -> None:
    """One valid pack carrying a `probe` of every asset kind.

    A pack has to survive `validate_pack` before `resolve_all` will look inside it — the
    manifest declares every file, every hash matches, and a prompt-bearing pack carries an
    approved evaluation case bound to one of its own prompts. That is not incidental: an
    invalid pack is not searched at all, so the walk could only be observed through packs
    the real validator accepts.
    """
    from rig_workbench import __version__
    from rig_workbench.packs.manifest import canonical, digest
    from rig_workbench.packs.model import ASSET_DIRS

    pack = root / pack_id
    assets: dict[str, list[str]] = {kind: [] for kind in ASSET_DIRS}
    for kind, (relative, body) in _PACK_ASSETS.items():
        path = pack / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        assets[kind] = [relative]
    case = copy.deepcopy(valid_case())
    case["id"] = f"{pack_id}-case"
    case["prompt_surfaces"] = [f"recipe:{PROBE}"]
    case_path = pack / "evals" / "cases" / PROBE / "case.json"
    case_path.parent.mkdir(parents=True, exist_ok=True)
    case_path.write_text(canonical(case), encoding="utf-8")
    assets["eval-case"] = [f"evals/cases/{PROBE}/case.json"]
    resource = pack / _PACK_ASSETS["resource"][0]
    manifest = {
        "pack_schema_version": 2, "id": pack_id, "type": "skill", "version": "1.0.0",
        "kind": "domain", "engine": f">={__version__}", "dependencies": [],
        "display_name": "Probe", "description": "one asset of every kind, at one tier",
        "capabilities": ["probe"], "entrypoints": [], "references": [],
        "assets": assets,
        "hashes": {item: digest(pack / item) for paths in assets.values() for item in paths},
        "resources": {_PACK_ASSETS["resource"][0]: {
            "media_type": "text/plain",
            "size": resource.stat().st_size,
            "sha256": digest(resource),
        }},
        "provenance": {"source": "test", "created_at": "2026-08-05T00:00:00+00:00"},
    }
    compatibility = {
        "compatibility_schema_version": 1, "pack_id": pack_id, "pack_version": "1.0.0",
        "engine": f">={__version__}", "platforms": ["any"],
    }
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    (pack / "compatibility.yaml").write_text(canonical(compatibility), encoding="utf-8")


class Tree:
    """The four anchor roots, with a `probe` planted in every declared directory."""

    def __init__(self, base: pathlib.Path, *, one_root: bool) -> None:
        self.project = base / "tree"
        self.shared = self.project if one_root else base / "state"
        self.user = base / "home"
        self.org = base / "org"
        self.rig = base / "rig"
        self.pack_dirs = {f"probe-{tier}" for tier in bricks.TIER_ORDER}
        self._build()

    def _build(self) -> None:
        for root in (self.project, self.shared, self.user, self.org, self.rig):
            root.mkdir(parents=True, exist_ok=True)
        _write_pack(self.shared / ".rig" / "packs", "probe-project")
        _write_pack(self.user / ".rig" / "packs", "probe-user")
        _write_pack(self.org / "packs", "probe-org")
        _write_pack(self.rig / "packs" / "official", "probe-official")
        _write_pack(self.rig / "packs" / "core", "probe-core")
        for anchor, relative in _OVERLAYS:
            self._plant(getattr(self, anchor) / relative, f"{PROBE}.md")
        # `_skill_root` recognises a rig home by this file, and `_core_assets` asks it where
        # the engine skill is rather than hardcoding the directory.
        (self.rig / "skills" / "engine").mkdir(parents=True, exist_ok=True)
        (self.rig / "skills" / "engine" / "SKILL.md").write_text("# probe\n", encoding="utf-8")
        for kind, relative in _SHIPPED.items():
            name = f"{PROBE}.yaml" if kind == "agent" else f"{PROBE}.md"
            self._plant(self.rig / relative, name)
        # What the prose promises and nothing reads. Planted so that its absence from every
        # observed walk is a measurement rather than a claim.
        self._plant(self.user / ".claude" / "rig" / "recipes", f"{PROBE}.md")
        self._plant(self.user / ".claude" / "rig" / "personas", f"{PROBE}.md")

    @staticmethod
    def _plant(directory: pathlib.Path, name: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text("probe\n", encoding="utf-8")

    def use(self, monkeypatch: pytest.MonkeyPatch) -> Tree:
        monkeypatch.setenv("RIG_HOME", str(self.rig))
        monkeypatch.setenv("RIG_USER_HOME", str(self.user))
        monkeypatch.setenv("RIG_ORG_HOME", str(self.org))
        return self

    def locate(self, path: pathlib.Path, *, drop: int) -> tuple[str, str]:
        """An observed absolute path, as the anchor it hangs off and the directory under it.

        `drop` is how much of the tail belongs to the asset rather than to the directory
        that was searched: the file name, plus — for an evaluation case — the directory that
        carries the case's name, since a case is `<name>/case.json`.

        The pack directory's own name is replaced by the declaration's `<pack>` wildcard, by
        exact match against the names this tree created, so the segment is identified rather
        than guessed at by position.
        """
        anchors = sorted(
            (("project", self.project), ("shared", self.shared), ("user", self.user),
             ("org", self.org), ("rig", self.rig)),
            key=lambda item: len(str(item[1])), reverse=True,
        )
        for anchor, root in anchors:
            if path.is_relative_to(root):
                parts = path.relative_to(root).parts[:-drop]
                return anchor, "/".join(
                    bricks.PACK if segment in self.pack_dirs else segment for segment in parts
                )
        raise AssertionError(f"{path} is under none of this tree's roots")


@pytest.fixture(scope="module")
def one_root(tmp_path_factory) -> Tree:
    """`project` and `shared` are the same directory — what a single checkout gives."""
    return Tree(tmp_path_factory.mktemp("one-root"), one_root=True)


@pytest.fixture(scope="module")
def two_roots(tmp_path_factory) -> Tree:
    """`project` and `shared` are different — a linked worktree beside its repository."""
    return Tree(tmp_path_factory.mktemp("two-roots"), one_root=False)


def _observe(tree: Tree, kind: str) -> tuple[tuple[str, str, str], ...]:
    """Every directory `resolve_all` actually found `probe` in, ranked, as declaration keys."""
    from rig_workbench.packs.resolver import resolve_all

    found = resolve_all(kind, PROBE, project=tree.project, shared=tree.shared)
    # An evaluation case is a directory (`<name>/case.json`); every other kind is one file.
    drop = 2 if kind == "eval-case" else 1
    observed = []
    for item in found:
        anchor, directory = tree.locate(item.path, drop=drop)
        observed.append((item.tier, anchor, directory))
    return tuple(observed)


def _table(declared: list[tuple], observed: list[tuple]) -> str:
    """Both sequences side by side, tier first, with the first disagreement pointed at."""
    width = max(len(declared), len(observed))
    lines = []
    for index in range(width):
        left = declared[index] if index < len(declared) else None
        right = observed[index] if index < len(observed) else None
        mark = "  " if left == right else "→ "
        lines.append(f"{mark}declared {left}\n  observed {right}")
    return "\n".join(lines)


# ── task 15: the declaration against the resolver it describes ───────────────

def test_the_tier_vocabulary_is_the_one_the_packs_use():
    """`bricks.TIER_ORDER` is a second spelling of `packs.model.TIERS`, held to the first.

    The declaration deliberately does not import it (see the module docstring there): a leaf
    that imported the resolver's vocabulary could not disagree with it, and a table that
    cannot disagree cannot catch anything.
    """
    from rig_workbench.packs.model import TIERS

    assert bricks.TIER_ORDER == TIERS


def test_every_asset_kind_has_a_declared_walk():
    """A thirteenth asset kind cannot be added without saying where it is looked for."""
    from rig_workbench.packs.model import ASSET_DIRS

    assert set(bricks.KINDS) == set(ASSET_DIRS)


@pytest.mark.parametrize("kind", bricks.KINDS)
def test_the_declared_walk_is_the_walk_the_resolver_takes(kind, one_root, monkeypatch):
    """The declared directory sequence, against the sequence a real `resolve_all` returns.

    Compared as a sequence, not a set: the order is the whole point of a tier system, and
    `resolve_asset` hands the first entry back as the winner and the rest as `shadowed`.
    Anchors are folded away here because this tree has one root; they are the next test.
    """
    tree = one_root.use(monkeypatch)
    declared = [(d.tier, d.path) for d in bricks.walk(kind).order]
    observed = [(tier, directory) for tier, _anchor, directory in _observe(tree, kind)]
    assert observed == declared, (
        f"the declared walk for {kind!r} is not the walk `resolve_all` takes:\n"
        + _table(declared, observed)
    )


def test_every_directory_hangs_off_the_declared_anchor(two_roots, monkeypatch):
    """With the working tree and the repository apart, which root each directory uses.

    This is the fact #471 turned on: `.rig/` is installed once per repository and `.claude/`
    is branch content, so a task worktree that resolved `.rig/packs` from its own tree would
    route differently from the checkout beside it. Set rather than sequence, because within
    one tier `resolve_all` ranks by source path and two same-tier directories under two
    different roots are therefore ordered by the names of those roots.
    """
    tree = two_roots.use(monkeypatch)
    for kind in bricks.KINDS:
        declared = {d.key for d in bricks.walk(kind).order}
        observed = set(_observe(tree, kind))
        assert observed == declared, (
            f"{kind!r}: declared but not walked {sorted(declared - observed)}; "
            f"walked but not declared {sorted(observed - declared)}"
        )


def test_the_user_tier_the_prose_promises_is_not_read(one_root, monkeypatch):
    """`~/.claude/rig/{recipes,personas}` exist in this tree and are reached by nothing.

    The drift entries above say the prose promises a directory nothing reads. This is that
    claim measured: the files are there, with the name every other tier uses, and no walk
    returns them.
    """
    tree = one_root.use(monkeypatch)
    promises = (("recipe", ".claude/rig/recipes", "recipes"),
                ("persona", ".claude/rig/personas", "facets/personas"))
    for kind, promised, in_a_pack in promises:
        observed = _observe(tree, kind)
        assert ("user", "user", promised) not in observed, observed
        # The `.claude/rig/` overlay that *is* read is the project one, and it is the same
        # relative path — so this is a statement about the tier, not about the spelling.
        assert ("project", "project", promised) in observed
        # And the user tier that does exist is the pack root, which neither document names.
        assert ("user", "user", f".rig/packs/{bricks.PACK}/{in_a_pack}") in observed


def test_the_recipe_fallback_walk_is_the_one_the_resolver_prints(tmp_path, monkeypatch):
    """The second walk, read out of the failure the resolver itself reports.

    `resolve_recipe` tries `resolve_all` first and then three directories of its own; when
    all of them miss it prints `searched: …`, which is the walk in the resolver's own words
    at run time. Nothing here parses `recipes.py` — the list comes out of a real call.
    """
    from rig_workbench.orchestrate import config, recipes

    project = tmp_path / "tree"
    org = tmp_path / "org"
    rig = tmp_path / "rig"
    for directory in (project, org / "recipes", rig / "skills" / "engine" / "recipes"):
        directory.mkdir(parents=True, exist_ok=True)
    (rig / "skills" / "engine" / "SKILL.md").write_text("# probe\n", encoding="utf-8")
    monkeypatch.setenv("RIG_HOME", str(rig))
    monkeypatch.setenv("RIG_ORG_HOME", str(org))
    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(config, "INVOCATION_CWD", project)
    monkeypatch.setattr(config, "RECIPES", rig / "skills" / "engine" / "recipes")

    output = io.StringIO()
    with pytest.raises(SystemExit) as exit_code, contextlib.redirect_stdout(output):
        recipes.resolve_recipe("nothing-is-called-this")
    assert exit_code.value.code == 1
    searched = [line for line in output.getvalue().splitlines()
                if line.strip().startswith("searched:")]
    assert len(searched) == 1, output.getvalue()
    roots = {"shared": project, "org": org, "rig": rig}

    def under(printed: str) -> tuple[str, str]:
        """One printed candidate, as the anchor it sits under and the directory holding it."""
        directory = pathlib.Path(printed.strip()).parent
        for anchor, root in roots.items():
            if directory.is_relative_to(root):
                return anchor, str(directory.relative_to(root))
        return "?", str(directory)

    entries = searched[0].split("searched:", 1)[1].split(", ")
    declared = bricks.walk("recipe", "orchestrate.recipes.resolve_recipe")
    assert declared.after == "packs.resolver.resolve_all"
    assert len(entries) == len(declared.order), (
        f"the recipe fallback walk searches {len(entries)} directories; the declared tiers "
        f"are {', '.join(d.tier for d in declared.order)}:\n  " + "\n  ".join(entries)
    )
    for position, (directory, printed) in enumerate(zip(declared.order, entries), start=1):
        assert under(printed) == (directory.anchor, directory.path), (
            f"the recipe fallback walk's {directory.tier!r} tier is not where it is declared: "
            f"expected {directory.anchor}:{directory.path}, walked {under(printed)} "
            f"(position {position} of the printed `searched:` line)"
        )


# ── task 16: the prose as a projection of the same table ─────────────────────

_TIER_TABLES = (
    ("resolve.md", "recipe", RESOLVE_MD, "### 2.1 recipe ファイル検索順"),
    ("SKILL.md", "recipe", SKILL_MD, "#### 4.2.1 recipe ファイル検索順"),
    ("SKILL.md", "persona", SKILL_MD, "### persona facet の tier 解決"),
)
_TIER_WORD = "|".join(sorted(set(bricks.TIER_ORDER) | set(PROSE_TIER_NAMES)))
_CHAIN = re.compile(rf"(?:{_TIER_WORD})(?:\s*→\s*(?:{_TIER_WORD}))+")
_BACKTICKED = re.compile(r"`([^`]+)`")


def _rows(path: pathlib.Path, heading: str) -> list[tuple[str, str]]:
    """The (tier word, path) rows of the first markdown table under a heading."""
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(heading))
    rows: list[tuple[str, str]] = []
    seen_table = False
    for line in lines[start + 1:]:
        if not line.startswith("|"):
            if seen_table:
                break
            continue
        seen_table = True
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        label = re.match(r"\*{0,2}([a-z]+)", cells[0].strip())
        target = _BACKTICKED.search(cells[1]) if len(cells) > 1 else None
        if label is None or target is None:
            continue  # the header row, and the `|---|` rule under it
        rows.append((label.group(1), target.group(1)))
    return rows


def _anchor_and_path(claimed: str) -> tuple[str, str]:
    """A path as the prose writes it, as the declaration would write it."""
    for prefix, anchor in (("<repo>/", "project"), ("~/", "user"), ("<org_dir>/", "org")):
        if claimed.startswith(prefix):
            return anchor, str(pathlib.PurePosixPath(claimed[len(prefix):]).parent)
    return "rig", str(pathlib.PurePosixPath(claimed).parent)


def _prose_tier_words() -> set[str]:
    """Every tier word `SKILL.md` uses: its tier tables, its `scope` values, its `a → b` orders."""
    text = SKILL_MD.read_text(encoding="utf-8")
    words = {label for source, _kind, path, heading in _TIER_TABLES if source == "SKILL.md"
             for label, _path in _rows(path, heading)}
    scope = next(line for line in text.splitlines() if line.startswith("| `scope`"))
    words.update(value for value in _BACKTICKED.findall(scope) if re.fullmatch(r"[a-z]+", value))
    for chain in _CHAIN.findall(text):
        words.update(part.strip() for part in chain.split("→"))
    return words - {"scope"}


def _declared() -> set[tuple[str, str, str, str]]:
    """Every (kind, tier, anchor, path) the registry declares, across both entry points."""
    return {(item.kind, d.tier, d.anchor, d.path) for item in bricks.WALKS for d in item.order}


def _prose_drift() -> set[tuple]:
    """Every way the shipped prose and the declaration disagree, as comparable keys."""
    declared = _declared()
    drift: set[tuple] = set()
    for source, kind, path, heading in _TIER_TABLES:
        rows = _rows(path, heading)
        for label, claimed in rows:
            tier = PROSE_TIER_NAMES.get(label)
            anchor, relative = _anchor_and_path(claimed)
            if tier is None:
                drift.add(("unknown-tier-word", source, kind, label))
            elif (kind, tier, anchor, relative) not in declared:
                drift.add(("path-not-walked", source, kind, label, f"{anchor}:{relative}"))
        listed = {PROSE_TIER_NAMES.get(label) for label, _claimed in rows}
        walked = {tier for k, tier, _anchor, _path in declared if k == kind}
        for tier in sorted(walked - listed):
            drift.add(("tier-missing-from-table", source, kind, tier))
    words = _prose_tier_words()
    drift.update(("vocabulary-prose-only", word) for word in sorted(words - set(bricks.TIER_ORDER)))
    drift.update(("vocabulary-code-only", word) for word in sorted(set(bricks.TIER_ORDER) - words))
    return drift


def test_the_prose_tables_parse_into_something():
    """A parser that quietly found nothing would make the drift set look clean.

    So the shape of what was read is pinned first: three tables, the row counts each
    document actually ships, and a tier word in every row.
    """
    counts = {(source, kind): len(_rows(path, heading))
              for source, kind, path, heading in _TIER_TABLES}
    assert counts == {
        ("resolve.md", "recipe"): 3, ("SKILL.md", "recipe"): 3, ("SKILL.md", "persona"): 4,
    }
    assert _prose_tier_words() == {"project", "user", "org", "shipped"}


def test_the_prose_diverges_from_the_declaration_exactly_where_it_is_known_to():
    """The shipped tier tables against `bricks.WALKS`, allowing only the recorded gap.

    Fixing a document means deleting its entry from `KNOWN_PROSE_DRIFT` in the same change:
    a fix without the deletion fails here, and so does a new divergence.
    """
    drift = _prose_drift()
    unrecorded = drift - set(KNOWN_PROSE_DRIFT)
    fixed = set(KNOWN_PROSE_DRIFT) - drift
    assert not unrecorded, (
        "the prose diverges from the declaration in a way nothing has recorded:\n  "
        + "\n  ".join(str(item) for item in sorted(map(str, unrecorded)))
    )
    assert not fixed, (
        "these divergences are gone — delete their KNOWN_PROSE_DRIFT entries:\n  "
        + "\n  ".join(str(item) for item in sorted(map(str, fixed)))
    )


def test_every_recorded_divergence_says_what_the_prose_claims():
    """An entry nobody can read is an entry nobody will delete."""
    for key, reason in KNOWN_PROSE_DRIFT.items():
        assert isinstance(key, tuple) and key
        assert reason.strip() and "\n" not in reason
