"""Where a brick is looked for, declared once (v3 stage 2, task 15).

`docs/v3-architecture-design-brief.ja.md` §7 puts this in the second stage, in as many
words: 「ブリック解決の宣言も 1 箇所に集まるため、散文と実装のずれ…も同時に解消できる」.
The divergence it means is real and measured — `skills/engine/SKILL.md` §4.2.1 and
`skills/engine/facets/instructions/resolve.md` 2.1 promise a user tier at
`~/.claude/rig/recipes` and `~/.claude/rig/personas` that no code reads, and both call the
lowest tier `shipped` while `rig_workbench/packs/model.py` calls it `core`.

**This module is the declaration; the code is what it describes, not the other way round.**
The direction matters because it is the one §9 settles for the capability table
(「正本は意図であり、CLI はその投影である」) and the same argument applies here. A search
order read back out of `resolve_all` would agree with `resolve_all` by construction, and a
test comparing the two would pass on the day somebody deletes a tier. So the order is
written here as data, in the words a person would use to ask the question, and
`tests/test_brick_resolution_declaration.py` observes what the resolver *actually walks* at
runtime and compares the observation to this table. The prose is then checked against this
table too, so the three can no longer drift silently in pairs.

Fixing the divergence once is not the point. The point is that after this there is one place
the answer is written, and a future divergence has somewhere to be caught.

**Declaration, not resolution.** Nothing here resolves anything: no record carries a
callable, no field holds a `pathlib.Path`, and paths are relative strings against a named
anchor (`ANCHORS`) rather than absolute paths, because an absolute path could only be
produced by running the resolution this table describes. The module is a leaf — stdlib only,
nothing from `rig_workbench.workbench`, `rig_workbench.orchestrate` or even
`rig_workbench.packs`. `TIER_ORDER` is therefore a second spelling of `packs.model.TIERS`
rather than an import of it, and the test asserts the two agree; importing the module under
test to define the expectation is exactly the tautology this file exists against.

## What one record says

A `SearchDir` is one directory the resolver looks in, for one asset kind, at one tier. A
`Walk` is the ordered sequence of them for one (kind, entry point) — the order being the
order the resolver ranks them in, so the first match in a `Walk` is the winner and the rest
are what it shadows.

## Two entry points, because there are two walks

`packs.resolver.resolve_all` is the one every brick kind goes through. `resolve_recipe` in
`rig_workbench/orchestrate/recipes.py` calls it first and then, only when it comes back
empty, walks three more directories of its own — including `<org>/recipes`, which is the
only place the manifest's `org_dir:` key reaches (`pack_roots` reads `$RIG_ORG_HOME` and
nothing else). Both walks are declared, and the second names the first in `after`.
"""

from __future__ import annotations

import dataclasses
import re

# No schema id, for the reason `registry/model.py` gives: nothing emits this table through a
# command yet, and naming a document nobody prints would put a public id into the registry
# that no CLI run could be driven to produce.

#: The roots a search directory hangs off. Named rather than resolved, because resolving one
#: means running the very code this table describes — and because which root a directory
#: hangs off is itself a fact that has been got wrong: `.rig/` is per *repository* (one
#: install, shared by every linked worktree) while `.claude/rig/` is branch content, per
#: *working tree*, and #471 was that confusion resolving a task worktree's empty copy.
ANCHORS = {
    "project": "the working tree whose tracked content counts — `resolve_all(project=…)`, "
               "`orchestrate.config.INVOCATION_CWD`",
    "shared": "the repository holding the gitignored install state under `.rig/` — "
              "`resolve_all(shared=…)`, `orchestrate.config.STATE_ROOT`; equal to `project` "
              "for every caller with one root",
    "user": "`$RIG_USER_HOME`, else the home directory",
    "org": "`$RIG_ORG_HOME`; the manifest's `org_dir:` reaches only the recipe fallback walk",
    "rig": "`$RIG_HOME`, else the installed distribution root — `packs.resolver._rig_home`",
}

#: Tier precedence, highest first: the rank `resolve_all` sorts candidates by.
#:
#: Written out rather than imported from `packs.model.TIERS` so that this module stays a leaf
#: and so that the two are two statements that can disagree — `tests/…_declaration.py`
#: asserts they do not. `core` is what the prose calls `shipped`; that disagreement is
#: recorded in the test's `KNOWN_PROSE_DRIFT`, not silently translated here.
TIER_ORDER = ("project", "user", "org", "official", "core")

#: One installed pack directory, whichever it is. A pack tier searches every pack installed
#: under its root, so the segment cannot be named — but it is exactly one segment deep, and
#: writing that down is what lets the test check the shape of an observed path rather than
#: just its prefix.
PACK = "<pack>"

#: The engine skill's directory name under `$RIG_HOME`. `packs.resolver._core_assets` asks
#: `orchestrate.config._skill_root` for it rather than hardcoding it, so a plugin installed
#: before the rename still resolves from `skills/rig/`. Declared as the current name with the
#: alias named here, because a directory that exists on one machine and not another is not
#: two tiers — it is one tier with two spellings.
SKILL_DIR = "skills/engine"
SKILL_DIR_ALIAS = "skills/rig"

_KIND = re.compile(r"^[a-z]+(?:-[a-z]+)*$")
_DOTTED = re.compile(r"^[a-z_]+(?:\.[a-z_]+)+$")
_SEGMENT = re.compile(r"^(?:<pack>|[A-Za-z0-9_.][A-Za-z0-9_.-]*)$")


def _reject_callables(owner: str, field: str, value: object) -> None:
    """Refuse a callable in any declared field, at any depth.

    Same rule and same reason as `registry/model.py`: a record with a function in it cannot
    be serialised, and cannot be read without importing whatever the function closes over —
    which for *this* table would mean importing the resolver, and a declaration that imports
    the code it describes has stopped being a declaration.
    """
    if callable(value):
        raise TypeError(
            f"{owner}.{field} carries a callable ({value!r}); this table declares where a "
            "brick is looked for, it does not look for one. Paths are strings against an "
            "anchor, never `pathlib.Path` objects and never functions that build one."
        )
    if isinstance(value, tuple):
        for item in value:
            _reject_callables(owner, field, item)


def _line(owner: str, field: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{owner}.{field} must be a string, not {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{owner}.{field} must not be empty")
    if "\n" in value:
        raise ValueError(f"{owner}.{field} must be one line")
    return value


@dataclasses.dataclass(frozen=True)
class SearchDir:
    """One directory the resolver looks in, for one kind, at one tier."""

    #: Which tier this directory belongs to; one of `TIER_ORDER`. Not derived from the path:
    #: `<rig>/packs/core/<pack>/recipes` and `<rig>/skills/engine/recipes` are both `core`
    #: and look nothing alike, and `<shared>/.rig/packs/<pack>/recipes` and
    #: `<project>/.claude/rig/recipes` are both `project` and hang off different anchors.
    tier: str

    #: Which root of `ANCHORS` the path is relative to.
    anchor: str

    #: Posix path under the anchor, no leading or trailing slash. `<pack>` (`PACK`) stands
    #: for one installed pack directory.
    path: str

    #: The function that reads this directory, as a dotted path under `rig_workbench` —
    #: where to go when the observed walk and this table disagree.
    reader: str

    #: One line a reader needs that the fields above do not carry: what else the directory
    #: is, why it hangs off that anchor, what it is *not*. Empty when there is nothing to add.
    note: str = ""

    def __post_init__(self) -> None:
        for field in dataclasses.fields(self):
            _reject_callables("SearchDir", field.name, getattr(self, field.name))
        if self.tier not in TIER_ORDER:
            raise ValueError(
                f"tier {self.tier!r} is not one of {', '.join(TIER_ORDER)}"
            )
        if self.anchor not in ANCHORS:
            raise ValueError(
                f"anchor {self.anchor!r} is not one of {', '.join(ANCHORS)}"
            )
        _line("SearchDir", "path", self.path)
        segments = self.path.split("/")
        if any(not _SEGMENT.match(segment) for segment in segments):
            raise ValueError(
                f"path {self.path!r} must be a relative posix path under its anchor "
                f"(segments, no `..`, no leading slash); {PACK!r} is the one wildcard"
            )
        if segments.count(PACK) > 1:
            raise ValueError(f"path {self.path!r} names more than one pack directory")
        _line("SearchDir", "reader", self.reader)
        if not _DOTTED.match(self.reader):
            raise ValueError(
                f"reader {self.reader!r} must be a dotted path under `rig_workbench`, "
                "e.g. `packs.resolver._legacy_assets`"
            )
        if self.note:
            _line("SearchDir", "note", self.note)

    @property
    def rank(self) -> int:
        """Tier precedence as a number; lower wins."""
        return TIER_ORDER.index(self.tier)

    @property
    def key(self) -> tuple[str, str, str]:
        """What the runtime observation is compared against: tier, anchor, path."""
        return (self.tier, self.anchor, self.path)

    def as_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}


@dataclasses.dataclass(frozen=True)
class Walk:
    """Every directory one entry point searches for one kind, in the order it ranks them."""

    #: The asset kind, spelled as `packs.model.ASSET_DIRS` spells it.
    kind: str

    #: The function a caller reaches for, as a dotted path under `rig_workbench`.
    entry: str

    #: The directories, best first. The first hit wins; `resolve_asset` returns the rest as
    #: `shadowed`.
    order: tuple[SearchDir, ...]

    #: One line: what a caller is asking when they call this entry point for this kind.
    intent: str

    #: The entry point that runs before this one, or `None` when this is the first. Only the
    #: recipe fallback has one, and having it as a field rather than as prose is what lets
    #: the test assert the two walks are read in that order.
    after: str | None = None

    #: One line of anything the order alone does not say. Empty when there is nothing.
    note: str = ""

    def __post_init__(self) -> None:
        for field in dataclasses.fields(self):
            _reject_callables("Walk", field.name, getattr(self, field.name))
        _line("Walk", "kind", self.kind)
        if not _KIND.match(self.kind):
            raise ValueError(f"kind {self.kind!r} must be a lowercase kebab-case asset kind")
        _line("Walk", "entry", self.entry)
        if not _DOTTED.match(self.entry):
            raise ValueError(f"entry {self.entry!r} must be a dotted path under rig_workbench")
        if not isinstance(self.order, tuple) or not self.order:
            raise TypeError(f"{self.kind}: order must be a non-empty tuple of SearchDir")
        for directory in self.order:
            if not isinstance(directory, SearchDir):
                raise TypeError(f"{self.kind}: order must hold SearchDir records, got {directory!r}")
        ranks = [directory.rank for directory in self.order]
        if ranks != sorted(ranks):
            raise ValueError(
                f"{self.kind}: the walk leaves tier order — {', '.join(d.tier for d in self.order)}; "
                "a lower tier searched before a higher one would mean the tier ranking is "
                "not what decides, and this table would be describing something else"
            )
        keys = [directory.key for directory in self.order]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{self.kind}: the same directory is declared twice in one walk")
        _line("Walk", "intent", self.intent)
        if self.after is not None and not _DOTTED.match(str(self.after)):
            raise ValueError(f"{self.kind}: after {self.after!r} must be a dotted path or None")
        if self.note:
            _line("Walk", "note", self.note)

    @property
    def tiers(self) -> tuple[str, ...]:
        """The tiers this walk touches, in order, without repeats."""
        seen: list[str] = []
        for directory in self.order:
            if directory.tier not in seen:
                seen.append(directory.tier)
        return tuple(seen)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "entry": self.entry,
            "intent": self.intent,
            "after": self.after,
            "note": self.note,
            "order": [directory.as_dict() for directory in self.order],
        }


_RESOLVE_ALL = "packs.resolver.resolve_all"
_PACK_READER = "packs.resolver._validated_pack_assets"
_LEGACY_READER = "packs.resolver._legacy_assets"
_CORE_READER = "packs.resolver._core_assets"

def _pack_tiers(asset_dir: str) -> tuple[SearchDir, ...]:
    """The five installed-pack tiers, whose layout `packs.model.ASSET_DIRS` fixes.

    Identical for every kind, which is why they are assembled rather than copied twelve
    times: the repetition is the resolver's, and a declaration that spells it out twelve
    times invites the twelfth copy to be the one that is wrong. What is *not* uniform — the
    `.claude/rig/` overlays and the shipped directories — is written out per kind below, at
    the position in the order where the resolver actually reaches it. Building records is
    not dispatching: what this returns is the same frozen data a literal would produce, and
    the test compares it to an observed walk either way.
    """
    return (
        SearchDir(tier="project", anchor="shared", path=f".rig/packs/{PACK}/{asset_dir}",
                  reader=_PACK_READER,
                  note="installed once per repository, so a linked worktree reads the main "
                       "checkout's copy rather than its own empty one (#471)"),
        SearchDir(tier="user", anchor="user", path=f".rig/packs/{PACK}/{asset_dir}",
                  reader=_PACK_READER,
                  note="the only user tier that exists; the prose promises `~/.claude/rig/` "
                       "instead, and nothing reads that"),
        SearchDir(tier="org", anchor="org", path=f"packs/{PACK}/{asset_dir}",
                  reader=_PACK_READER,
                  note="`pack_roots` reads `$RIG_ORG_HOME` only — the manifest's `org_dir:` "
                       "does not reach here"),
        SearchDir(tier="official", anchor="rig", path=f"packs/official/{PACK}/{asset_dir}",
                  reader=_PACK_READER),
        SearchDir(tier="core", anchor="rig", path=f"packs/core/{PACK}/{asset_dir}",
                  reader=_PACK_READER,
                  note="ranks above the shipped directory below: same tier, and the sort key "
                       "falls through to the source path"),
    )


def _shipped(kind_dir: str, *, anchor: str = "rig", note: str = "") -> SearchDir:
    return SearchDir(tier="core", anchor=anchor, path=kind_dir, reader=_CORE_READER, note=note)


WALKS: tuple[Walk, ...] = (
    # ── the two kinds a person names by hand, and the wiki a persona injects ──
    Walk(
        kind="recipe",
        entry=_RESOLVE_ALL,
        intent="find the flow somebody asked for by name",
        order=(
            _pack_tiers("recipes")[0],
            SearchDir(tier="project", anchor="project", path=".claude/rig/recipes",
                      reader=_LEGACY_READER,
                      note="tracked, so it is branch content; read from `shared` instead when "
                           "the working tree has no copy of it"),
            SearchDir(tier="project", anchor="shared", path=".rig/recipes",
                      reader=_LEGACY_READER,
                      note="gitignored machine-local state, and it loses to `.claude/rig/` "
                           "above on the source sort despite being listed first in the code"),
            *_pack_tiers("recipes")[1:],
            _shipped(f"{SKILL_DIR}/recipes",
                     note="what the prose calls the `shipped` tier"),
        ),
    ),
    Walk(
        kind="persona",
        entry=_RESOLVE_ALL,
        intent="find the reviewer lens a recipe or `--persona` named",
        order=(
            _pack_tiers("facets/personas")[0],
            SearchDir(tier="project", anchor="project", path=".claude/rig/personas",
                      reader=_LEGACY_READER,
                      note="the only `.claude/rig/personas` anybody reads; there is no "
                           "`~/.claude/rig/personas` in the code, at any tier"),
            *_pack_tiers("facets/personas")[1:],
            _shipped(f"{SKILL_DIR}/facets/personas",
                     note="`orchestrate.providers._persona_text` falls back to this same "
                          "directory (`config.PERSONAS`) when the walk finds nothing"),
        ),
    ),
    Walk(
        kind="wiki",
        entry=_RESOLVE_ALL,
        intent="find the canonical page a persona's `inject:` points at",
        order=(
            _pack_tiers("facets/knowledge")[0],
            SearchDir(tier="project", anchor="project", path=".claude/rig/knowledge",
                      reader=_LEGACY_READER,
                      note="the whole knowledge tree, not just `wiki/`: names resolve "
                           "relative to this directory, so a page under `wiki/` is "
                           "`wiki/<slug>`"),
            *_pack_tiers("facets/knowledge")[1:],
            _shipped(f"{SKILL_DIR}/facets/knowledge"),
        ),
    ),
    # ── the facet kinds a recipe step names, which have no project-tier overlay ──
    Walk(
        kind="instruction",
        entry=_RESOLVE_ALL,
        intent="find the procedure a step delegates to",
        order=(*_pack_tiers("facets/instructions"), _shipped(f"{SKILL_DIR}/facets/instructions")),
        note="no `.claude/rig/` overlay: a project overrides an instruction by shipping a "
             "project-tier pack, not by dropping a file in the tree",
    ),
    Walk(
        kind="pattern",
        entry=_RESOLVE_ALL,
        intent="find the reusable shape a step composes with",
        order=(*_pack_tiers("patterns"), _shipped(f"{SKILL_DIR}/patterns")),
    ),
    Walk(
        kind="policy",
        entry=_RESOLVE_ALL,
        intent="find the rule text a step has to hold to",
        order=(*_pack_tiers("facets/policies"), _shipped(f"{SKILL_DIR}/facets/policies")),
    ),
    Walk(
        kind="output-contract",
        entry=_RESOLVE_ALL,
        intent="find the shape a step's answer has to come back in",
        order=(*_pack_tiers("facets/output-contracts"),
               _shipped(f"{SKILL_DIR}/facets/output-contracts")),
    ),
    # ── the two that live at the distribution root rather than inside the skill ──
    Walk(
        kind="command",
        entry=_RESOLVE_ALL,
        intent="find the slash command a provider offers",
        order=(*_pack_tiers("commands"),
               _shipped("commands",
                        note="under `$RIG_HOME` itself, not the skill: a provider reads "
                             "`commands/` and `agents/` from the plugin root")),
    ),
    Walk(
        kind="agent",
        entry=_RESOLVE_ALL,
        intent="find the subagent definition a step dispatches to",
        order=(*_pack_tiers("agents"), _shipped("agents")),
        note="the one kind whose files are `.yaml`/`.yml` rather than `.md`",
    ),
    # ── evidence and inert data: packs only, no shipped directory to fall back to ──
    Walk(
        kind="eval-case",
        entry=_RESOLVE_ALL,
        intent="find the measured case a pack's prompts are judged against",
        order=_pack_tiers("evals/cases"),
        note="`resolve_all` skips `_core_assets` for this kind explicitly; the core *pack* "
             "tier still applies, and a case is named by its directory (`<name>/case.json`)",
    ),
    Walk(
        kind="eval-result",
        entry=_RESOLVE_ALL,
        intent="find a recorded run of such a case",
        order=_pack_tiers("evals/results"),
    ),
    Walk(
        kind="resource",
        entry=_RESOLVE_ALL,
        intent="find inert data a pack ships",
        order=_pack_tiers("resources"),
        note="`packs.resolver.resolve_resource` is what callers use — it addresses a resource "
             "by owning pack id over these same roots, and never executes one",
    ),
    # ── the second walk ──────────────────────────────────────────────────────
    Walk(
        kind="recipe",
        entry="orchestrate.recipes.resolve_recipe",
        after=_RESOLVE_ALL,
        intent="find a recipe by name after the pack walk came back empty",
        order=(
            SearchDir(tier="project", anchor="shared", path=".rig/recipes",
                      reader="orchestrate.recipes.resolve_recipe",
                      note="`config.PROJECT_RECIPES`; matched as `<name>.md` exactly, not "
                           "globbed"),
            SearchDir(tier="org", anchor="org", path="recipes",
                      reader="orchestrate.recipes.resolve_recipe",
                      note="the one directory the manifest's `org_dir:` reaches, "
                           "`$RIG_ORG_HOME` winning when both are set"),
            SearchDir(tier="core", anchor="rig", path=f"{SKILL_DIR}/recipes",
                      reader="orchestrate.recipes.resolve_recipe",
                      note="`config.RECIPES`"),
        ),
        note="an existing path given as the name short-circuits both walks before either "
             "runs; this list is what the `searched:` line prints when nothing matched",
    ),
)

#: Every asset kind this table declares a walk for. Compared against
#: `packs.model.ASSET_DIRS` by the test, so a thirteenth kind cannot appear without one.
KINDS = tuple(dict.fromkeys(walk.kind for walk in WALKS))

#: Every entry point declared, in the order the first walk that names each one appears.
ENTRY_POINTS = tuple(dict.fromkeys(walk.entry for walk in WALKS))


def walk(kind: str, entry: str = _RESOLVE_ALL) -> Walk:
    """The declared walk for one kind through one entry point."""
    for candidate in WALKS:
        if candidate.kind == kind and candidate.entry == entry:
            return candidate
    raise KeyError(f"no declared walk for {kind!r} through {entry!r}")


def as_dict() -> dict:
    """The whole table in the shapes JSON holds, for whatever projection wants it."""
    return {
        "anchors": dict(ANCHORS),
        "tier_order": list(TIER_ORDER),
        "pack_wildcard": PACK,
        "walks": [item.as_dict() for item in WALKS],
    }
