"""Could the CLI be generated from the table? For `govern`, measured rather than argued.

`docs/v3-architecture-design-brief.ja.md` §2 and §9 make the CLI a projection of the
capability registry. Stage 2 declared all 139 capabilities *including their flags* and kept
the hand-written parsers, holding the two together by test
(`tests/test_capability_registry_vs_cli.py`) — two sources of truth for one surface, accepted
for one stage. Stage 3 removes the second one by generating the first. This file is the
proof, taken before the swap: `rig_workbench/registry/parser.py` builds `govern`'s parser out
of `registry.children("govern")`, and every observable thing about it is compared against the
parser `rig_workbench/govern/cli.py` ships today. Nothing here changes what ships.

`govern` is the pillar being migrated and the honest place to measure: ten verbs, already
behind the ports, and a planning pass reported its flags as matching the real parser exactly.
Two of them do not (see `DIVERGENCES`), and that is the finding this file exists to make
unarguable.

## What "equivalent" is asserted to mean

Three layers, each strictly stronger than the one above it, because each of the first two can
pass while the parser still behaves differently:

1. **The verb sets are equal** — the set of sub-parsers, both read out of argparse at run
   time, never from a list written down here.
2. **Action for action**, per verb: `option_strings`, `dest`, `nargs`, `const`, `default`,
   `choices`, `required` and `metavar`, in order, including argparse's own `-h`. Read off
   `parser._actions` rather than compared as help text, which is formatting: two parsers can
   render the same help and disagree about `nargs`, and two that agree about everything here
   can render differently because one line wrapped.
3. **Real argument vectors parsed through both**, namespaces compared field by field, plus
   the error paths — a missing required argument, an unknown flag, a bad choice, a bare
   group, an unknown verb — compared on exit status *and* on the stderr argparse writes
   itself. This is the layer that catches what structure cannot: `govern audit`'s generated
   `--action` matches the shipped one on every attribute but `dest`, and that one attribute
   silently destroys the value of the `action` positional
   (`test_the_generated_audit_parser_loses_the_positional_action_to_its_own_option`).

The vectors are **derived from the capabilities**, not listed: for each verb, the smallest
vector its required flags allow, the widest vector its flags allow, one per value of every
`choice` flag, and one error vector per required flag, per choice flag, and per verb. Listing
a few by hand would test the flags somebody remembered.

## The one thing not compared

`args.func`. The shipped parser calls `set_defaults(func=cmd_init)`; a `Capability` may not
carry a callable (`registry.model._reject_callables`), so the generated parser cannot and
must not. Binding a verb to its handler stays with the caller that imports both — it is the
single thing stage 3's swap has to write by hand, and it is excluded from the namespace
comparison by name, not by ignoring unexpected keys.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io

import pytest

from rig_workbench.govern.cli import build_parser as shipped_parser
from rig_workbench.registry import children
from rig_workbench.registry.model import FLAG_TYPES, Capability, Flag
from rig_workbench.registry.parser import (
    ARGPARSE_TYPES,
    UnknownFlagType,
    argparse_type,
    build_group_parser,
    flag_kwargs,
)

#: The prog the shipped parser announces. The generated one is given the same string because
#: prog is presentation (the registry declares intent, not how a group titles itself), and
#: because argparse writes it into every usage line the error-path comparison reads.
PROG = "rig-wb govern"

#: The attributes an argparse action is compared on. `metavar` is included and is `None`
#: throughout govern; it is here so that a future `metavar=` on one side is a failure rather
#: than an invisible difference in what a person is told to type.
ACTION_FIELDS = (
    "option_strings",
    "dest",
    "nargs",
    "const",
    "default",
    "choices",
    "required",
    "metavar",
)

#: Set by `argparse` on the namespace; the generated parser has no way to produce it and
#: should not (see the module docstring).
HANDLER_FIELD = "func"

#: Distinguishes "this parser set the field to None" from "this parser has no such field".
_MISSING = object()


@dataclasses.dataclass(frozen=True)
class Divergence:
    """One place the registry's declaration cannot say what the shipped parser does.

    Recorded, not accommodated. The registry is a declaration of intent and the hand-written
    parser is what ships; a difference means one of them is wrong, and the test's job is to
    make the difference precise and to fail if the set of them changes in either direction —
    a new one appearing, or a documented one being quietly fixed while this file still claims
    it exists.
    """

    verb: str
    option: str
    shipped_dest: str
    generated_dest: str
    judgement: str

    @property
    def dests(self) -> frozenset[str]:
        return frozenset({self.shipped_dest, self.generated_dest})

    @property
    def usage_fixup(self) -> tuple[str, str]:
        """argparse derives a metavar from the dest, so the usage line diverges with it."""
        return (
            f"{self.option} {self.generated_dest.upper()}",
            f"{self.option} {self.shipped_dest.upper()}",
        )


#: Both differences found by generating `govern`. Neither is fixed here: `model.Flag` has no
#: `dest` field, so the registry *cannot* declare either, and adding a field to the frozen
#: record is a change to the declaration rather than to this projection.
DIVERGENCES = (
    Divergence(
        verb="waiver",
        option="--criterion",
        shipped_dest="criteria",
        generated_dest="criterion",
        judgement=(
            "the shipped parser is right and the registry is under-specified: a repeatable "
            "flag collects a list, and `args.criteria` is what govern/cli.py reads. The "
            "generated `args.criterion` renames a field the handler already uses."
        ),
    ),
    Divergence(
        verb="audit",
        option="--action",
        shipped_dest="filter_action",
        generated_dest="action",
        judgement=(
            "the shipped parser is right and the registry is not merely under-specified but "
            "broken as declared: `govern audit` also takes an `action` positional, so the "
            "derived dest collides with it and the option overwrites the sub-command word. "
            "dest='filter_action' is exactly why it is written down in govern/cli.py."
        ),
    ),
)

DIVERGENT_DESTS: dict[str, frozenset[str]] = {
    verb: frozenset().union(*(d.dests for d in DIVERGENCES if d.verb == verb))
    for verb in {d.verb for d in DIVERGENCES}
}


# ── reading the two parsers ──────────────────────────────────────────────────
def generated() -> argparse.ArgumentParser:
    """govern's parser, built from the declaration and nothing else."""
    return build_group_parser(children("govern"), prog=PROG, description="generated")


def subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """The sub-parsers argparse actually holds, by the word that reaches them."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    raise AssertionError(f"{parser.prog} has no sub-parsers")


def signatures(parser: argparse.ArgumentParser) -> list[tuple]:
    """Every action of one parser, in order, as the tuple of compared attributes."""
    return [
        tuple(getattr(action, field) for field in ACTION_FIELDS) for action in parser._actions
    ]


def namespace(parser: argparse.ArgumentParser, argv: list[str]) -> dict:
    """What this parser makes of `argv`, minus the handler the generated side cannot bind."""
    parsed = vars(parser.parse_args(argv))
    return {key: value for key, value in parsed.items() if key != HANDLER_FIELD}


def failure(parser: argparse.ArgumentParser, argv: list[str]) -> tuple[int, str]:
    """The status and the stderr argparse writes when it refuses `argv`.

    argparse writes usage and the message itself and then exits, so both are part of the
    surface a person meets; capturing them is the only way to compare what the two parsers
    *say*, not just that both refused.
    """
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr), pytest.raises(SystemExit) as exit_info:
        parser.parse_args(argv)
    return int(exit_info.value.code or 0), stderr.getvalue()


def normalise(text: str) -> str:
    """Rewrite the generated usage line's metavars to the shipped spelling.

    Only the documented divergences are rewritten, and only the exact `--flag METAVAR` pair
    each one implies. Anything else that differs still fails.
    """
    for divergence in DIVERGENCES:
        text = text.replace(*divergence.usage_fixup)
    return text


# ── deriving argument vectors from the declarations ──────────────────────────
def _value(flag: Flag, index: int = 0) -> str:
    """A value this flag would accept, chosen from the declaration alone."""
    if flag.choices:
        return flag.choices[index % len(flag.choices)]
    if flag.type == "int":
        return str(7 + index)
    if flag.type == "float":
        return str(1.5 + index)
    if flag.type == "path":
        return f"some/path-{index}"
    return f"{flag.name.lstrip('-')}-value-{index}"


def _tokens(flag: Flag, *, value: str | None = None) -> list[str]:
    """What a caller types to supply this flag."""
    if flag.positional:
        if flag.type == "string-list":
            return [value or _value(flag, 0), _value(flag, 1)]
        return [value or _value(flag)]
    if flag.type == "bool":
        return [flag.name]
    if flag.type == "string-list":
        first = value or _value(flag, 0)
        return [flag.name, first, flag.name, _value(flag, 1)]
    return [flag.name, value or _value(flag)]


def _vector(
    capability: Capability, include: tuple[Flag, ...], overrides: dict[str, str] | None = None
) -> list[str]:
    """A command line for this capability carrying exactly `include`, in declared order."""
    argv = [capability.verb]
    for flag in capability.flags:
        if flag in include:
            argv += _tokens(flag, value=(overrides or {}).get(flag.name))
    return argv


def accepted_vectors(capability: Capability) -> list[list[str]]:
    """Vectors both parsers should accept: smallest, widest, and one per declared choice."""
    required = tuple(flag for flag in capability.flags if flag.required)
    every = tuple(capability.flags)
    vectors = [_vector(capability, required), _vector(capability, every)]
    for flag in capability.flags:
        for choice in flag.choices:
            include = required if flag in required else (*required, flag)
            vectors.append(_vector(capability, include, {flag.name: choice}))
    return vectors


def rejected_vectors(capability: Capability) -> list[list[str]]:
    """Vectors both parsers should refuse: a missing required, an unknown flag, a bad choice."""
    required = tuple(flag for flag in capability.flags if flag.required)
    vectors = [_vector(capability, required) + ["--no-such-flag-anywhere"]]
    for flag in required:
        vectors.append(_vector(capability, tuple(f for f in required if f is not flag)))
    for flag in capability.flags:
        if flag.choices:
            include = required if flag in required else (*required, flag)
            vectors.append(_vector(capability, include, {flag.name: "not-a-declared-choice"}))
    return vectors


def govern_capabilities() -> tuple[Capability, ...]:
    return children("govern")


VERBS = [capability.verb for capability in govern_capabilities()]


# ── 1. the verbs ─────────────────────────────────────────────────────────────
def test_the_generated_parser_offers_exactly_the_verbs_the_shipped_one_does() -> None:
    assert set(subcommands(generated())) == set(subcommands(shipped_parser()))


def test_the_generated_parser_offers_them_in_the_order_the_registry_declares() -> None:
    # Order is observable: it is the order argparse prints in the usage line and in --help.
    assert list(subcommands(generated())) == VERBS


# ── 2. action for action ─────────────────────────────────────────────────────
def test_the_group_parser_itself_matches_the_shipped_one() -> None:
    generated_group, shipped_group = generated(), shipped_parser()
    # The sub-parsers action holds a map of parser objects in `choices`, which are never
    # equal across two parsers; the verbs it offers are compared above, so compare the rest.
    fields = [field for field in ACTION_FIELDS if field != "choices"]
    assert [
        tuple(getattr(action, field) for field in fields) for action in generated_group._actions
    ] == [tuple(getattr(action, field) for field in fields) for action in shipped_group._actions]


@pytest.mark.parametrize("verb", VERBS)
def test_every_action_of_every_verb_matches_the_shipped_parser(verb: str) -> None:
    mine = subcommands(generated())[verb]
    theirs = subcommands(shipped_parser())[verb]
    differing = [
        (ours, yours)
        for ours, yours in zip(signatures(mine), signatures(theirs), strict=True)
        if ours != yours
    ]
    expected = {d.option: d for d in DIVERGENCES if d.verb == verb}
    for ours, yours in differing:
        option = ours[0][0] if ours[0] else f"<positional {ours[1]}>"
        assert option in expected, (
            f"govern {verb} {option}: the generated parser and the shipped one disagree and "
            f"nothing in DIVERGENCES says why.\n  generated: {dict(zip(ACTION_FIELDS, ours))}"
            f"\n  shipped:   {dict(zip(ACTION_FIELDS, yours))}"
        )
        # The documented divergences are about `dest` and nothing else.
        assert [
            field
            for field, ours_value, yours_value in zip(ACTION_FIELDS, ours, yours, strict=True)
            if ours_value != yours_value
        ] == ["dest"]
    found = {ours[0][0] for ours, _ in differing if ours[0]}
    assert found == set(expected), (
        f"govern {verb}: DIVERGENCES claims {sorted(expected)} differ, argparse says "
        f"{sorted(found)}. A documented difference that has gone away is a reason to delete "
        "the entry, not to leave it standing."
    )


def test_the_only_differences_anywhere_in_govern_are_the_two_documented_ones() -> None:
    found = set()
    for verb in VERBS:
        mine, theirs = subcommands(generated())[verb], subcommands(shipped_parser())[verb]
        for ours, yours in zip(signatures(mine), signatures(theirs), strict=True):
            if ours != yours:
                found.add((verb, ours[0][0], yours[1], ours[1]))
    assert found == {
        (d.verb, d.option, d.shipped_dest, d.generated_dest) for d in DIVERGENCES
    }


# ── 3a. real vectors, real namespaces ────────────────────────────────────────
@pytest.mark.parametrize("capability", govern_capabilities(), ids=lambda c: c.verb)
def test_accepted_vectors_produce_the_same_namespace(capability: Capability) -> None:
    mine, theirs = generated(), shipped_parser()
    allowed = DIVERGENT_DESTS.get(capability.verb, frozenset())
    for argv in accepted_vectors(capability):
        ours, yours = namespace(mine, argv), namespace(theirs, argv)
        differing = {
            key
            for key in set(ours) | set(yours)
            if ours.get(key, _MISSING) != yours.get(key, _MISSING)
        }
        assert differing <= allowed, (
            f"`rig-wb govern {' '.join(argv)}` parses differently and "
            f"{sorted(differing - allowed)} is not a documented divergence.\n"
            f"  generated: {ours}\n  shipped:   {yours}"
        )


def test_the_documented_divergences_are_each_witnessed_by_a_real_vector() -> None:
    """A divergence nobody can reproduce is a stale comment; make each one show itself."""
    mine, theirs = generated(), shipped_parser()
    witnessed: set[tuple[str, str]] = set()
    for capability in govern_capabilities():
        for argv in accepted_vectors(capability):
            ours, yours = namespace(mine, argv), namespace(theirs, argv)
            for divergence in DIVERGENCES:
                if divergence.verb != capability.verb:
                    continue
                # The shipped parser fills a field the generated one never writes: the
                # value is there under one name and absent under the other.
                if divergence.shipped_dest in yours and divergence.shipped_dest not in ours:
                    witnessed.add((divergence.verb, divergence.option))
    assert witnessed == {(d.verb, d.option) for d in DIVERGENCES}


def test_the_generated_audit_parser_loses_the_positional_action_to_its_own_option() -> None:
    """The concrete cost of the missing `dest`, pinned rather than described.

    This is not a formatting difference or a renamed field: `govern audit verify --action
    policy.init` asks to verify the ledger chain, filtered to one action name. The shipped
    parser hears that. The generated one hears `action="policy.init"`, which is not a verb
    it offers, and the word the person typed is gone before any handler sees it.
    """
    argv = ["audit", "verify", "--action", "policy.init"]
    shipped = namespace(shipped_parser(), argv)
    assert (shipped["action"], shipped["filter_action"]) == ("verify", "policy.init")

    mine = namespace(generated(), argv)
    assert "filter_action" not in mine
    assert mine["action"] == "policy.init", (
        "the positional survived; if the registry grew a `dest` field this test is the one "
        "to delete, along with the audit entry in DIVERGENCES"
    )


# ── 3b. the error paths argparse writes itself ───────────────────────────────
@pytest.mark.parametrize("capability", govern_capabilities(), ids=lambda c: c.verb)
def test_rejected_vectors_fail_the_same_way(capability: Capability) -> None:
    mine, theirs = generated(), shipped_parser()
    for argv in rejected_vectors(capability):
        our_status, our_stderr = failure(mine, argv)
        their_status, their_stderr = failure(theirs, argv)
        assert our_status == their_status == 2, f"{argv}: {our_status} vs {their_status}"
        assert normalise(our_stderr) == their_stderr, (
            f"`rig-wb govern {' '.join(argv)}` is refused in different words.\n"
            f"  generated: {our_stderr!r}\n  shipped:   {their_stderr!r}"
        )


@pytest.mark.parametrize("argv", [[], ["definitely-not-a-verb"], ["--json"]])
def test_the_group_refuses_a_bad_invocation_the_same_way(argv: list[str]) -> None:
    assert failure(generated(), argv) == failure(shipped_parser(), argv)


def test_the_usage_line_is_only_rewritten_where_a_divergence_says_it_would_be() -> None:
    """`normalise` must not be a blanket excuse: it may only touch waiver and audit."""
    mine, theirs = generated(), shipped_parser()
    rewritten = set()
    for capability in govern_capabilities():
        for argv in rejected_vectors(capability):
            _, our_stderr = failure(mine, argv)
            _, their_stderr = failure(theirs, argv)
            if our_stderr != their_stderr:
                assert normalise(our_stderr) == their_stderr
                rewritten.add(capability.verb)
    assert rewritten == {d.verb for d in DIVERGENCES}


# ── the type-name translation ────────────────────────────────────────────────
def test_every_declared_flag_type_has_exactly_one_argparse_projection() -> None:
    assert set(ARGPARSE_TYPES) == set(FLAG_TYPES)


def test_the_two_numeric_names_are_the_only_ones_that_become_a_converter() -> None:
    # The other five pass no `type=` at all, which is what every hand-written add_argument
    # in govern does; `type=str` would be an action attribute the shipped parser lacks.
    assert {name for name, converter in ARGPARSE_TYPES.items() if converter is not None} == {
        "int",
        "float",
    }
    assert argparse_type("int") is int and argparse_type("bool") is None


def test_an_unknown_type_name_fails_loudly_rather_than_becoming_a_string() -> None:
    with pytest.raises(UnknownFlagType) as error:
        argparse_type("duration")
    assert "duration" in str(error.value)

    # And through the whole projection, not only the lookup: `Flag` refuses an unknown type
    # at construction, so the only way to reach this is the way a future edit would — a name
    # that got past validation because FLAG_TYPES grew and this table did not.
    flag = Flag(name="--window", type="string", help="h")
    object.__setattr__(flag, "type", "duration")
    with pytest.raises(UnknownFlagType):
        flag_kwargs(flag)


def test_a_positional_may_not_be_declared_bool() -> None:
    flag = Flag(name="loud", type="bool", help="h")
    with pytest.raises(ValueError, match="option-only"):
        flag_kwargs(flag)
