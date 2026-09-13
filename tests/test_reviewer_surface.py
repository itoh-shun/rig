"""The reviewer surface: every `rig:*` subagent type the shipped prose promises resolves.

rig is installed as a Claude Code plugin named `rig` (`.claude-plugin/plugin.json`,
`/plugin install rig@sito-plugins`), and the host namespaces what it finds at the plugin
root by that name: `commands/go.md` is why the command is `/rig:go`, and `agents/<stem>.md`
is why the subagent type is `rig:<stem>` — the very name
`tests/test_review_body.py::test_colon_persona_round_trips` records a verdict under. So
`agents/*.md` is not a copy of the persona facet of the same name; it is the only place the
`rig:*-reviewer` subagent types come from, and the only place a reviewer's read-only tool
allowlist (`tools: Read, Grep, Glob, Bash`) is expressed at all — a persona facet's
frontmatter carries `name`/`description`/`inject:` and nothing that restricts a tool.

That is the fact this module pins, because a plan to delete the ten reviewer agents as
duplicates of their personas depends on it being false. The prose is parsed rather than
restated: the promises come out of §2's agent and persona rows and out of the two
instruction facets that actually dispatch a fan-out, so a lane added to a document with no
brick behind it fails here rather than at dispatch time.

§2 lives in `skills/engine/BRICKS.md`; `SKILL.md` loads whole on every activation and keeps a
one-line summary pointing there. Which file that is comes from `catalog.py` rather than from a
path written here, for the same reason `_expand_braces` does.
"""

from __future__ import annotations

import pathlib
import re

import pytest

# The same brace expander `--validate`'s §2 drift check uses, and the same landmarks it finds
# §2 by. Restating either here would let the test and the check disagree — about what
# `{a,b}-reviewer` names, or about which document and which lines are the catalogue — which is
# the one thing they must not do.
from rig_workbench.validation.catalog import (
    CATALOG_SECTION,
    _expand_braces,
    _section,
    catalog_document,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENTS = ROOT / "agents"
PERSONAS = ROOT / "skills" / "engine" / "facets" / "personas"
PARALLEL_REVIEW = ROOT / "skills" / "engine" / "facets" / "instructions" / "parallel-review.md"
ADVERSARIAL_REVIEW = ROOT / "skills" / "engine" / "facets" / "instructions" / "adversarial-review.md"

#: The lanes `patterns/review-gate` needs a verdict from before anything can be accepted.
STANDARD_LANES = ("security", "design", "test", "behavioral-correctness")


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _section_2() -> str:
    """§2 — the brick catalogue, where the agent row and the persona row live.

    Located the way `--validate` locates it: the document `catalog.py` names, sliced on the
    two landmarks it slices on. A heading that moved makes this raise rather than hand back
    a section that is not §2.
    """
    section = _section(catalog_document(), CATALOG_SECTION)
    assert section is not None, (
        f"§2 could not be located by {CATALOG_SECTION[0]!r}..{CATALOG_SECTION[1]!r}; the "
        f"catalogue this module reads has moved")
    return section


def _catalogued(prefix: str) -> set[str]:
    """Backticked `<prefix><name>` references in §2, brace notation expanded."""
    out: set[str] = set()
    for raw in re.findall(r"`([A-Za-z0-9_{},/.-]+)`", _section_2()):
        if raw.startswith(prefix):
            out.update(tok[len(prefix):] for tok in _expand_braces(raw))
    return out


def _frontmatter(path: pathlib.Path) -> dict[str, str]:
    """`key: value` pairs from the leading `---` block. Every reviewer brick's is flat."""
    text = _read(path)
    assert text.startswith("---\n"), f"{path.name} has no frontmatter"
    block = text.split("---\n", 2)[1]
    return {k.strip(): v.strip() for k, v in
            (line.split(":", 1) for line in block.splitlines() if ":" in line and not line.startswith(" "))}


def _agent_files() -> list[pathlib.Path]:
    return sorted(AGENTS.glob("*.md"))


def _persona_exists(name: str) -> bool:
    return (PERSONAS / f"{name}.md").exists()


def _agent_exists(name: str) -> bool:
    return (AGENTS / f"{name}.md").exists()


# ── the promises, parsed out of the prose that makes them ────────────────────
def _parallel_review_lanes() -> dict[str, tuple[str, str]]:
    """`lane -> (agent, persona)` from parallel-review's dispatch bullets.

    The bullet says it in one sentence — use `agents/X` if it is there, otherwise compose
    `facets/personas/Y`. The two paths are read from the whole bullet rather than from one
    line, and independently of each other, so wrapping a long bullet or writing the fallback
    clause first changes nothing here. A parse tied to one line and to that order would
    answer a reworded bullet with "no longer dispatches a test lane", which is a false
    diagnosis: the lane is still dispatched, the regex just stopped matching.
    """
    lanes: dict[str, tuple[str, str]] = {}
    lane = None
    for chunk in re.split(r"\n(?=- )", _read(PARALLEL_REVIEW)):
        # A bullet ends at the blank line; without that bound the last one in a run would
        # swallow the paragraph after it and read that paragraph's paths as its own.
        bullet = chunk.split("\n\n")[0]
        head = re.match(r"- \*\*([a-z-]+) 観点\*\*", bullet)
        if not head:
            continue
        lane = head.group(1)
        agent = re.search(r"`agents/([a-z0-9-]+)`", bullet)
        persona = re.search(r"`facets/personas/([a-z0-9-]+)`", bullet)
        if agent and persona:
            lanes[lane] = (agent.group(1), persona.group(1))
    assert lane is not None, "parallel-review has no 観点 bullets at all — the section moved"
    return lanes


def _parallel_review_optional_lanes() -> dict[str, str]:
    """`lane -> reviewer name` for the additional lanes, which the prose names bare."""
    return {m.group(1): m.group(2) for m in
            re.finditer(r"- \*\*([a-z-]+) 観点\*\*（`([a-z0-9-]+)`）", _read(PARALLEL_REVIEW))}


def _optional_lane_bullets() -> list[str]:
    """The same bullets counted without reading their punctuation — the denominator.

    The parse above matches one exact shape, down to the full-width parentheses. An ASCII
    `(`, a space before it, or a renamed lane makes it match nothing at all, and a
    parametrization over an empty dict collects no tests and reports success — the section
    would be unguarded and would look guarded. This counts list items that mention 観点
    inside the 追加観点 block and nothing more, so the two numbers have to agree.
    """
    text = _read(PARALLEL_REVIEW)
    block = text[text.index("**追加観点"):]
    return [line for line in block[:block.index("\n**")].splitlines()
            if re.match(r"- \*\*.+観点", line)]


def _adversarial_review_pairs() -> dict[str, str]:
    """`agent -> persona` for the adversarial lane, where the two names deliberately differ.

    `subagent_type: lazy-senior-reviewer` falls back to `facets/personas/lazy-senior`. The
    `-reviewer` suffix is the whole reason a name-equality check cannot stand in for reading
    the document.
    """
    text = _read(ADVERSARIAL_REVIEW)
    subagents = re.search(r"subagent_type:((?:\s*`[a-z0-9-]+`\s*/?)+)", text)
    personas = re.search(r"`facets/personas/(\{[a-z0-9,-]+\})`", text)
    assert subagents and personas, "adversarial-review no longer declares its fallback pairing"
    agents = re.findall(r"`([a-z0-9-]+)`", subagents.group(1))
    fallbacks = [tok[len("facets/personas/"):] for tok in
                 _expand_braces("facets/personas/" + personas.group(1))]
    assert len(agents) == len(fallbacks), "the agent list and the persona list are not paired"
    return dict(zip(agents, fallbacks))


def _fallback_map() -> dict[str, str]:
    """Every agent's persona fallback: its own name unless a document says otherwise."""
    declared = _adversarial_review_pairs()
    declared.update({a: p for a, p in _parallel_review_lanes().values()})
    return {path.stem: declared.get(path.stem, path.stem) for path in _agent_files()}


# ── the agent row and the files behind it ────────────────────────────────────
def test_the_catalogue_agent_row_and_the_agents_directory_are_the_same_set() -> None:
    """§2's agent row is the list of subagent types rig promises; ghosts and omissions both fail."""
    assert _catalogued("agents/") == {path.stem for path in _agent_files()}


@pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.stem)
def test_an_agent_files_name_is_the_subagent_type_the_plugin_exposes(path: pathlib.Path) -> None:
    """`rig:<stem>` only resolves while frontmatter `name` and filename agree."""
    assert _frontmatter(path).get("name") == path.stem


@pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.stem)
def test_an_agent_carries_the_tool_allowlist_a_persona_facet_cannot(path: pathlib.Path) -> None:
    """The measured reason an agent is not a duplicate of its persona.

    "Read-only reviewer" is prose in a persona facet and an allowlist in an agent. Deleting
    the agent in favour of the persona would drop the enforcement and keep the sentence.
    """
    fm = _frontmatter(path)
    assert fm.get("tools"), f"{path.name} declares no tools"
    assert "Write" not in fm["tools"] and "Edit" not in fm["tools"], \
        f"{path.name} is a read-only reviewer but its allowlist can write"
    fallback = PERSONAS / f"{_fallback_map()[path.stem]}.md"
    assert fallback.exists(), \
        f"{path.name} has no persona at {fallback.name}; add one, or declare its name in a facet"
    assert not _frontmatter(fallback).get("tools"), \
        "a persona facet has started carrying `tools:` — the asymmetry this test measures is gone"


# ── resolution: an agent, or a persona fallback, for every promise ───────────
@pytest.mark.parametrize("lane", STANDARD_LANES)
def test_each_standard_lane_resolves_to_an_agent_and_to_a_persona_fallback(lane: str) -> None:
    """The four lanes the gate blocks on: dispatch and fallback both have a brick behind them."""
    lanes = _parallel_review_lanes()
    assert lane in lanes, f"parallel-review no longer dispatches a {lane} lane"
    agent, persona = lanes[lane]
    assert _agent_exists(agent), f"{lane}: agents/{agent}.md is missing — rig:{agent} would not resolve"
    assert _persona_exists(persona), f"{lane}: facets/personas/{persona}.md is missing — no fallback"


def test_parallel_review_dispatches_exactly_the_four_standard_lanes() -> None:
    assert sorted(_parallel_review_lanes()) == sorted(STANDARD_LANES)


@pytest.mark.parametrize("agent,persona", sorted(_fallback_map().items()))
def test_every_agent_has_a_persona_fallback(agent: str, persona: str) -> None:
    """§5 (`COMPOSE.md`): the persona facet is what a host without the plugin's agents falls
    back to.

    An agent with no fallback is a lane that silently disappears outside a Claude Code
    session — headless `orchestrate.py`, CI, MCP — with nothing to compose in its place.
    """
    assert _agent_exists(agent)
    assert _persona_exists(persona), f"agents/{agent}.md has no fallback at facets/personas/{persona}.md"
    assert persona in _catalogued("facets/personas/"), \
        f"facets/personas/{persona} is a live fallback but §2's persona row does not list it"


def test_the_optional_lane_parse_is_not_silently_empty() -> None:
    """Guards the parametrization below against collecting zero cases."""
    parsed = _parallel_review_optional_lanes()
    bullets = _optional_lane_bullets()
    assert bullets, "no 追加観点 bullets found — the section was renamed or moved"
    assert len(parsed) == len(bullets), (
        f"{len(bullets)} optional-lane bullets in parallel-review, {len(parsed)} parsed — "
        "the bullet form changed and the lanes below would go unchecked")


@pytest.mark.parametrize("lane,reviewer", sorted(_parallel_review_optional_lanes().items()))
def test_each_optional_lane_resolves_somewhere(lane: str, reviewer: str) -> None:
    """The additional lanes are named bare, so either brick satisfies them."""
    assert _agent_exists(reviewer) or _persona_exists(reviewer), \
        f"{lane}: `{reviewer}` resolves to neither an agent nor a persona"


def test_the_adversarial_pair_the_brief_called_unique_already_exists_as_personas() -> None:
    """Measured against the T5 premise, which held that these two had no persona equivalent.

    They have had one all along under a name without the `-reviewer` suffix, which is why
    adversarial-review has to spell the fallback out instead of deriving it. Moving the agents
    into `facets/personas/` would have produced a second copy, not a move.
    """
    assert _adversarial_review_pairs() == {
        "lazy-senior-reviewer": "lazy-senior",
        "cognitive-economist-reviewer": "cognitive-economist",
    }
    for persona in ("lazy-senior", "cognitive-economist"):
        assert _persona_exists(persona)
