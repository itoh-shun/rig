"""The capability registry's type and container (v3 stage 2).

`docs/v3-architecture-design-brief.ja.md` §2 makes one table the place every capability is
declared, and §9 points it at intent rather than at argparse. This file pins the two
properties that decision rests on and that nothing else can recover once they are lost.

**A declaration may not carry a callable.** The moment one field holds a handler, the table
stops being data: it cannot be serialised for the MCP servers or `action.yml`, it cannot be
read without importing whatever the handler closes over, and the leaf module becomes the next
hub. So `TestNoCallables` tries every field, including nested ones, and expects a refusal.

**A declaration may not change under its readers.** Four surfaces project this table; one of
them mutating an entry would be invisible to the other three.

**Two facts, two fields.** What a capability disturbs on this machine and whether it reaches
off it are independent — `gh-check` leaves nothing behind and still talks to github.com, `run`
writes a worktree and may call a provider. `TestTwoAxes` holds them apart: `effect_class` no
longer admits `network`, `network` no longer admits a bare bool, and the pair still projects
onto the annotation sets `remote_mcp.py` builds by hand — read from that file, not restated
here, so the day it changes this fails instead of drifting.

**Two audiences, and only the audience picks the language.** The same record is read aloud by
`talk-loop.md`, which speaks Japanese, and printed by `rig-wb <group> --help`, which is English
like the rest of this repository. `TestWhichAudienceReadsWhichField` asks `registry/parser.py`
which fields actually reach a CLI user and holds those — and only those — to English, so that
Japanese stays right where it belongs and wrong where it does not.

The rest is the container's behaviour, and one validation sweep over the real entries. That
sweep passes vacuously today — `CAPABILITIES` is empty — and starts biting the moment the
first entry lands without the fields a conversation needs, which is the point of writing it
before the entries exist rather than after.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
import re

import pytest

from rig_workbench.registry import CAPABILITIES, by_id, children
from rig_workbench.registry.model import (
    EFFECT_CLASSES,
    NETWORK_REACH,
    NO_PRECONDITION,
    PARENTS,
    Capability,
    ExitCode,
    Flag,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL_SOURCE = REPO_ROOT / "rig_workbench" / "registry" / "model.py"
PARSER_SOURCE = REPO_ROOT / "rig_workbench" / "registry" / "parser.py"
REMOTE_MCP_SOURCE = REPO_ROOT / "rig_workbench" / "remote_mcp.py"


def hand_built_annotations() -> dict[str, dict]:
    """The `ToolAnnotations` sets `remote_mcp.py` declares, read out of that file.

    Copying the four booleans into this test would only prove the copy matches itself. The
    point of `mcp_hints` is that one table answers the question that file answers today, so
    the expectation is parsed from the file: rename a set or flip a hint there and this
    stops passing, which is the conversation worth having.
    """
    source = REMOTE_MCP_SOURCE.read_text(encoding="utf-8")
    blocks = re.findall(r"(\w+)_annotations = ToolAnnotations\((.*?)\)", source, re.DOTALL)
    return {
        name: {
            f"{hint}Hint": value == "True"
            for hint, value in re.findall(r"(\w+)Hint=(True|False)", body)
        }
        for name, body in blocks
    }


def capability(**overrides) -> Capability:
    """A valid capability, built locally: these tests never depend on the real entries."""
    fields = {
        "id": "wb.gate",
        "parent": "wb",
        "verb": "gate",
        "intent": "check whether the work is finished enough to accept, and what is missing",
        "preconditions": ("git-repo", "task-exists"),
        "effect_line": "acceptance criteria を採点して gate の記録を更新します",
        "effect_class": "writes-state",
        "network": "never",
        "flags": (Flag(name="task_id", type="string", help="the task to judge"),),
        "output_schema": "rig.gate-report/v1",
        "exit_codes": (ExitCode(code=0, meaning="the gate passed"),),
    }
    fields.update(overrides)
    return Capability(**fields)


class TestContainer:
    """The table exists, is populated, and is a tuple — stage 2 declares, it does not wire."""

    def test_every_dispatchable_verb_is_declared_once(self):
        # 135 = 37 top-level + 51 under wb + 47 across the five other groups. The number is
        # written out because a capability appearing or vanishing is a change to the command
        # surface, and this is the table the other surfaces are meant to be projections of.
        # It was 137 = 39 + 51 + 47 until `list` and `review` — dispatched at the top level
        # and implemented by nobody — were taken out of `rig_workbench/cli.py`'s
        # `_orch_delegates` and out of this table with them.
        top_level = sum(1 for c in CAPABILITIES if c.parent is None)
        under_wb = sum(1 for c in CAPABILITIES if c.parent == "wb")
        assert len(CAPABILITIES) == 135, (
            f"the capability table holds {len(CAPABILITIES)} entries, not 135.\n"
            f"  declared: {top_level} top-level + {under_wb} under `wb` + "
            f"{len(CAPABILITIES) - top_level - under_wb} across govern/pack/eval/baseline/"
            "githooks\n"
            "  expected: 37 + 51 + 47\n"
            "  The term that moved says where to look. Edit the number here only once the "
            "change to the command surface is the one that was meant — this table is what "
            "every other surface is supposed to be a projection of."
        )
        assert len({c.id for c in CAPABILITIES}) == len(CAPABILITIES), "duplicate id"
        assert len({c.command_path for c in CAPABILITIES}) == len(CAPABILITIES), "duplicate path"

    def test_is_a_tuple(self):
        # A list would let one projection append to the table another is reading.
        assert isinstance(CAPABILITIES, tuple)


class TestNoCallables:
    """No field may hold something that can be called. Enforced, not merely documented."""

    def handler(self) -> None:  # a plausible mistake: the function that runs the capability
        return None

    @pytest.mark.parametrize(
        "field",
        ["id", "parent", "verb", "intent", "summary", "effect_line", "effect_class", "network",
         "output_schema"],
    )
    def test_scalar_field_refuses_a_function(self, field):
        with pytest.raises(TypeError, match="callable"):
            capability(**{field: self.handler})

    def test_every_capability_field_is_covered_by_the_sweep(self):
        """`Flag`'s sweep is derived from the dataclass; this is the same for `Capability`.

        The list above is by hand and `summary` was added to it by hand, which is the moment
        the omission would have been easy: a new field with a callable in it passes every
        other test in the file. So the fields are read off the dataclass here, and one that
        arrives without anybody remembering is covered from the moment it exists.
        """
        for field in dataclasses.fields(Capability):
            with pytest.raises(TypeError, match="callable"):
                capability(**{field.name: self.handler})

    def test_tuple_field_refuses_a_function_inside_it(self):
        # The check is recursive: a callable smuggled into a tuple is still a callable.
        with pytest.raises(TypeError, match="callable"):
            capability(preconditions=("git-repo", self.handler))
        with pytest.raises(TypeError, match="callable"):
            capability(flags=(self.handler,))
        with pytest.raises(TypeError, match="callable"):
            capability(exit_codes=(self.handler,))

    def test_flag_type_must_be_a_name_not_a_type_object(self):
        # `int` is callable, which is exactly why flag types are spelled as strings.
        with pytest.raises(TypeError, match="callable"):
            Flag(name="--limit", type=int, help="how many")

    def test_flag_default_refuses_a_callable(self):
        with pytest.raises(TypeError, match="callable"):
            Flag(name="--limit", type="int", help="how many", default=len)

    def test_flag_dest_refuses_a_callable(self):
        # `dest` names an attribute a handler reads; a function here would be a handler
        # arriving by the back door of the field that points at one.
        with pytest.raises(TypeError, match="callable"):
            Flag(name="--limit", type="int", help="how many", dest=len)

    def test_flag_aliases_refuse_a_callable_inside_them(self):
        with pytest.raises(TypeError, match="callable"):
            Flag(name="--limit", type="int", help="how many", aliases=("--cap", len))

    def test_every_flag_field_is_covered_by_the_sweep(self):
        """The sweep above is by hand; this is what notices when a field is added to `Flag`.

        Adding a field and forgetting to try a callable in it is exactly how the rule stops
        being enforced, and it would be invisible — every existing test still passes. So the
        field list is read off the dataclass and every field is fed a function here, which
        means a new one is covered the moment it exists whether or not anybody remembers.
        """
        valid = {"name": "--limit", "type": "int", "help": "how many"}
        for field in dataclasses.fields(Flag):
            with pytest.raises(TypeError, match="callable"):
                Flag(**{**valid, field.name: self.handler})

    def test_exit_code_refuses_a_callable(self):
        with pytest.raises(TypeError, match="callable"):
            ExitCode(code=0, meaning=self.handler)

    def test_a_lambda_is_refused_like_any_other_callable(self):
        with pytest.raises(TypeError, match="callable"):
            capability(intent=lambda: "review the current diff")

    def test_the_model_stays_a_leaf(self):
        """model.py imports no execution: no argparse, no subprocess, no workbench pillar."""
        imports = [
            line for line in MODEL_SOURCE.read_text(encoding="utf-8").splitlines()
            if re.match(r"^(import|from)\s", line)
        ]
        forbidden = ("argparse", "subprocess", "workbench", "orchestrate")
        offenders = [line for line in imports if any(name in line for name in forbidden)]
        assert offenders == [], f"model.py must stay a leaf; found {offenders}"


class TestImmutable:
    """Four surfaces read each record. None of them may edit it."""

    def test_capability_cannot_be_reassigned(self):
        record = capability()
        with pytest.raises(dataclasses.FrozenInstanceError):
            record.verb = "accept"

    def test_flag_cannot_be_reassigned(self):
        flag = Flag(name="--json", type="bool", help="emit JSON")
        with pytest.raises(dataclasses.FrozenInstanceError):
            flag.required = True

    def test_exit_code_cannot_be_reassigned(self):
        code = ExitCode(code=1, meaning="the gate rejected the work")
        with pytest.raises(dataclasses.FrozenInstanceError):
            code.code = 0

    def test_tuple_fields_refuse_a_list(self):
        # A list field would be mutable through the back door on a frozen record.
        with pytest.raises(TypeError, match="must be a tuple"):
            capability(preconditions=["git-repo"])


class TestShape:
    """The vocabulary is closed and the conversation fields are not optional."""

    def test_every_parent_is_accepted(self):
        for parent in PARENTS:
            assert capability(id=f"{parent}.thing", parent=parent).parent == parent

    def test_an_unknown_parent_is_refused(self):
        with pytest.raises(ValueError, match="parent"):
            capability(parent="orchestrate")

    def test_top_level_capability_has_no_parent(self):
        assert capability(id="usage", parent=None, verb="usage").parent is None

    def test_every_effect_class_is_accepted(self):
        for effect in EFFECT_CLASSES:
            assert capability(effect_class=effect).effect_class == effect

    def test_an_unknown_effect_class_is_refused(self):
        with pytest.raises(ValueError, match="effect_class"):
            capability(effect_class="destructive")

    @pytest.mark.parametrize("field", ["intent", "effect_line", "verb"])
    def test_an_empty_conversation_field_is_refused(self, field):
        with pytest.raises(ValueError, match="empty"):
            capability(**{field: "   "})

    @pytest.mark.parametrize("field", ["intent", "effect_line"])
    def test_a_multi_line_conversation_field_is_refused(self, field):
        # These are read aloud before a command runs; a paragraph is not that.
        with pytest.raises(ValueError, match="one line"):
            capability(**{field: "does a thing\nand then another"})

    def test_the_help_line_is_the_intent_when_no_summary_is_written(self):
        entry = capability()
        assert entry.summary is None
        assert entry.help_line == entry.intent

    def test_a_written_summary_is_the_line_the_cli_prints(self):
        entry = capability(summary="run the gate and say what is missing")
        assert entry.help_line == "run the gate and say what is missing"
        assert entry.intent != entry.help_line, "the intent is left alone"

    def test_a_summary_that_merely_repeats_the_intent_is_refused(self):
        """Optional in the way `Flag.dest` is: a written one has to mean something.

        Allowed to repeat, `summary` would become a second copy of `intent` on every entry
        somebody filled in dutifully — a second place to make the same edit, and a field a
        reader can no longer take as a statement that the two audiences really do differ.
        """
        with pytest.raises(ValueError, match="repeats the intent"):
            capability(summary=capability().intent)
        with pytest.raises(ValueError, match="repeats the intent"):
            capability(summary=f"  {capability().intent}  ")

    def test_an_empty_summary_is_refused_rather_than_read_as_absent(self):
        # `None` means "intent is the help line"; a blank string is somebody having started.
        with pytest.raises(ValueError, match="empty"):
            capability(summary="   ")

    def test_preconditions_are_never_empty(self):
        with pytest.raises(ValueError, match="preconditions"):
            capability(preconditions=())

    def test_nothing_required_is_said_explicitly(self):
        assert capability(preconditions=(NO_PRECONDITION,)).preconditions == ("none",)

    def test_a_precondition_must_be_a_check_name_not_prose(self):
        with pytest.raises(ValueError, match="check name"):
            capability(preconditions=("the repository must be a git repository",))

    def test_output_schema_must_be_versioned_or_absent(self):
        assert capability(output_schema=None).output_schema is None
        with pytest.raises(ValueError, match="output_schema"):
            capability(output_schema="gate-report")

    def test_exit_codes_are_declared(self):
        with pytest.raises(ValueError, match="exit_codes"):
            capability(exit_codes=())

    def test_a_shell_reserved_exit_code_is_refused(self):
        # 124/126/127 and 128+signal belong to the shell; rig never assigns them.
        with pytest.raises(ValueError, match="reserved"):
            ExitCode(code=127, meaning="command not found")

    def test_command_path_is_what_a_person_types(self):
        assert capability().command_path == ("wb", "gate")
        assert capability(id="usage", parent=None, verb="usage").command_path == ("usage",)
        assert capability(
            id="queue.add", parent=None, verb="queue add"
        ).command_path == ("queue", "add")

    def test_as_dict_is_json_shaped(self):
        record = capability().as_dict()
        assert record["flags"] == [{
            "name": "task_id", "type": "string", "help": "the task to judge",
            "required": False, "choices": [], "default": None,
            # Both written out even when unset: a projection reading this record has to be
            # able to tell "argparse derives the dest" from "this key does not exist here".
            "dest": None, "aliases": [],
        }]
        assert record["exit_codes"] == [{"code": 0, "meaning": "the gate passed"}]
        assert record["command_path"] == ["wb", "gate"]
        # Both axes reach the projections; a surface reading only one would under-warn.
        assert record["effect_class"] == "writes-state"
        assert record["network"] == "never"
        # The help line is carried resolved as well as declared, so a projection that only
        # wants "the line to print" does not re-derive the fallback and get it wrong.
        assert record["summary"] is None
        assert record["help_line"] == record["intent"]
        written = capability(summary="run the gate").as_dict()
        assert (written["summary"], written["help_line"]) == ("run the gate", "run the gate")

    def test_a_flag_knows_whether_it_is_positional(self):
        assert Flag(name="task_id", type="string", help="the task").positional
        assert not Flag(name="--json", type="bool", help="emit JSON").positional

    def test_a_choice_flag_declares_its_choices(self):
        with pytest.raises(ValueError, match="choices"):
            Flag(name="--runtime", type="choice", help="which runtime")
        with pytest.raises(ValueError, match="choices"):
            Flag(name="--json", type="bool", help="emit JSON", choices=("yes", "no"))


class TestWhereTheValueLands:
    """`dest`: optional, derived when absent, and a statement when present.

    The field exists because two govern flags could not be declared at all — `--criterion`
    stores into `args.criteria`, `--action` into `args.filter_action` — and it is optional
    because writing it on all ~500 flags would be a second copy of the flag name. That
    choice only pays if a written `dest` can be *read* as "argparse would get this wrong",
    which is what the refusals below are for.
    """

    def test_an_undeclared_dest_is_the_one_argparse_derives(self):
        assert Flag(name="--since-days", type="int", help="h").dest_name == "since_days"
        assert Flag(name="task_id", type="string", help="h").dest_name == "task_id"

    def test_a_declared_dest_is_where_the_value_lands(self):
        flag = Flag(name="--criterion", type="string-list", help="h", dest="criteria")
        assert (flag.dest_name, flag.derived_dest) == ("criteria", "criterion")

    def test_declaring_the_dest_argparse_would_derive_anyway_is_refused(self):
        # Otherwise the field says nothing where it agrees, and a reader has to check every
        # one of them against argparse's rule to find the few that mean something.
        with pytest.raises(ValueError, match="derives anyway"):
            Flag(name="--since-days", type="int", help="h", dest="since_days")

    def test_a_positional_may_not_declare_a_dest(self):
        # argparse itself raises "dest supplied twice for positional argument"; refusing it
        # here means the declaration fails rather than the parser that projects it.
        with pytest.raises(ValueError, match="positional"):
            Flag(name="task_id", type="string", help="h", dest="task")

    def test_a_dest_must_be_an_attribute_name(self):
        with pytest.raises(ValueError, match="snake_case"):
            Flag(name="--criterion", type="string", help="h", dest="filter-action")

    def test_two_flags_landing_on_one_attribute_are_refused(self):
        """The audit bug, caught at declaration instead of at parse time.

        `govern audit` takes an `action` positional and an `--action` option. Without a
        `dest` the option overwrites the positional, and `govern audit verify --action
        policy.init` loses the word `verify` before any handler sees it.
        """
        with pytest.raises(ValueError, match="same attribute"):
            capability(flags=(
                Flag(name="action", type="string", help="what to do"),
                Flag(name="--action", type="string", help="filter by action name"),
            ))

    def test_declaring_the_dest_settles_it(self):
        assert capability(flags=(
            Flag(name="action", type="string", help="what to do"),
            Flag(name="--action", type="string", help="filter", dest="filter_action"),
        )).flags[1].dest_name == "filter_action"


class TestOtherSpellings:
    """`aliases`: more option strings for one flag, without a second identity for it."""

    def test_a_flag_offers_its_name_first_and_then_its_aliases(self):
        flag = Flag(name="--recipe", type="string", help="h", aliases=("--explicit-recipe",))
        # The order argparse reads them in: the first is the usage line and the dest.
        assert flag.option_strings == ("--recipe", "--explicit-recipe")
        assert flag.dest_name == "recipe"

    def test_an_alias_must_be_an_option(self):
        with pytest.raises(ValueError, match="option"):
            Flag(name="--recipe", type="string", help="h", aliases=("recipe",))

    def test_a_positional_has_no_aliases(self):
        with pytest.raises(ValueError, match="aliases"):
            Flag(name="task_id", type="string", help="h", aliases=("--task",))

    def test_a_spelling_may_not_be_declared_twice_on_one_flag(self):
        with pytest.raises(ValueError, match="twice"):
            Flag(name="--recipe", type="string", help="h", aliases=("--recipe",))

    def test_an_alias_may_not_collide_with_another_flag_of_the_same_capability(self):
        # An alias is an option string people's scripts depend on, so it is counted like a
        # name when a capability is checked for the same flag declared twice.
        with pytest.raises(ValueError, match="declared twice"):
            capability(flags=(
                Flag(name="--recipe", type="string", help="h", aliases=("--explicit-recipe",)),
                Flag(name="--explicit-recipe", type="string", help="h"),
            ))


class TestTwoAxes:
    """`effect_class` and `network` answer different questions and must stay separable.

    The case this guards is the one that made the split worth doing: a capability that both
    writes and reaches out used to have to pick, and whichever fact lost stopped being said.
    """

    def test_network_is_no_longer_an_effect_class(self):
        # The retired fourth value. An entry copied from the old vocabulary must not pass.
        assert "network" not in EFFECT_CLASSES
        with pytest.raises(ValueError, match="effect_class"):
            capability(effect_class="network")

    def test_the_reach_it_admits_is_closed(self):
        for reach in NETWORK_REACH:
            assert capability(network=reach).network == reach
        assert NETWORK_REACH == ("never", "sometimes", "always")

    @pytest.mark.parametrize("value", [True, False, "yes", "no", None, "", "offline", 1])
    def test_an_unknown_reach_is_refused(self, value):
        # A bool included on purpose: the shape is three-valued, and `network=True` would
        # have to be read as one of them silently.
        with pytest.raises(ValueError, match="network"):
            capability(network=value)

    def test_the_reach_has_no_default(self):
        """Omitting it would be a claim — "nothing leaves the machine" — made by silence.

        Same reason `preconditions` refuses to be empty: this table has to keep "checked,
        and the answer is no" apart from "nobody filled this in", and 135 entries are about
        to be written by people who will occasionally not know.
        """
        fields = {
            "id": "pack.sync", "parent": "pack", "verb": "sync",
            "intent": "re-scan the pack and bring its manifest back in line",
            "preconditions": ("git-repo",),
            "effect_line": "pack manifest を更新します",
            "effect_class": "writes-state",
            "exit_codes": (ExitCode(code=0, meaning="the manifest is current"),),
        }
        with pytest.raises(TypeError, match="network"):
            Capability(**fields)
        assert Capability(**fields, network="never").network == "never"

    def test_both_facts_survive_on_one_record(self):
        """The worked example from the field docs: `run --provider claude`."""
        run = capability(
            id="run", parent=None, verb="run",
            effect_class="writes-worktree", network="sometimes",
        )
        assert run.effect_class == "writes-worktree"  # the half the old axis dropped
        assert run.may_reach_network
        assert run.as_dict()["network"] == "sometimes"

    def test_may_reach_network_treats_sometimes_as_yes(self):
        assert not capability(network="never").may_reach_network
        assert capability(network="sometimes").may_reach_network
        assert capability(network="always").may_reach_network

    def test_the_three_hand_built_annotation_sets_still_come_back_out(self):
        """Each of `remote_mcp.py`'s sets, against the capability that tool actually is."""
        expected = hand_built_annotations()
        assert set(expected) == {"read", "run", "destructive"}, expected

        # rig_status / rig_board / rig_diff / rig_plan: local reads.
        assert capability(
            effect_class="read-only", network="never"
        ).mcp_hints == expected["read"]

        # rig_run: writes a worktree, and may call a provider (mock sends nothing).
        assert capability(
            effect_class="writes-worktree", network="sometimes"
        ).mcp_hints == expected["run"]

        # rig_accept / rig_discard: write the worktree, reach nothing.
        assert capability(
            effect_class="writes-worktree", network="never"
        ).mcp_hints == expected["destructive"]

    def test_a_provider_that_always_reaches_out_annotates_like_the_run_tool(self):
        # `sometimes` and `always` differ in what a person is told, not in what an MCP
        # client is allowed to assume: both mean "this may talk to something out there".
        assert (
            capability(effect_class="writes-worktree", network="always").mcp_hints
            == capability(effect_class="writes-worktree", network="sometimes").mcp_hints
        )

    def test_a_harmless_remote_read_is_the_set_remote_mcp_never_needed(self):
        """`gh-check` runs `gh auth status` against github.com and leaves nothing behind.

        No hand-built set covers this: all four of that file's read tools stay on the
        machine. It is not ambiguous — each hint follows from the axis that answers it —
        and it is the case the single axis could only get wrong, by calling a read
        destructive because it used the network.
        """
        hints = capability(effect_class="read-only", network="always").mcp_hints
        assert hints == {
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        }
        assert hints not in hand_built_annotations().values()

    def test_destructiveness_is_decided_by_the_local_axis_alone(self):
        # Reaching out is not by itself a reason to ask; writing is. That is exactly how
        # remote_mcp.py annotates its own tools.
        for reach in NETWORK_REACH:
            assert not capability(effect_class="read-only", network=reach).mcp_hints[
                "destructiveHint"
            ]
            assert capability(effect_class="writes-state", network=reach).mcp_hints[
                "destructiveHint"
            ]


class TestLookup:
    """`by_id` and `children`, against a locally built table."""

    table = (
        capability(id="usage", parent=None, verb="usage", effect_class="read-only"),
        capability(id="wb.gate", parent="wb", verb="gate"),
        capability(id="wb.accept", parent="wb", verb="accept",
                   effect_class="writes-worktree"),
        capability(id="pack.install", parent="pack", verb="install",
                   effect_class="writes-worktree"),
    )

    def test_by_id_finds_a_declared_capability(self):
        found = by_id("wb.accept", self.table)
        assert found is not None and found.verb == "accept"

    def test_by_id_answers_none_for_an_undeclared_one(self):
        assert by_id("wb.nope", self.table) is None

    def test_by_id_reads_the_real_table_by_default(self):
        assert by_id("wb.gate").verb == "gate"
        assert by_id("wb.no-such-verb") is None

    def test_children_returns_a_group_in_declaration_order(self):
        assert [c.id for c in children("wb", self.table)] == ["wb.gate", "wb.accept"]

    def test_children_of_none_is_the_top_level(self):
        assert [c.id for c in children(None, self.table)] == ["usage"]

    def test_children_of_an_empty_group_is_empty(self):
        assert children("govern", self.table) == ()

    def test_children_refuses_a_parent_outside_the_closed_set(self):
        with pytest.raises(ValueError, match="parent"):
            children("orchestrate", self.table)

    def test_children_reads_the_real_table_by_default(self):
        assert len(children("wb")) == 51
        assert {c.parent for c in children("wb")} == {"wb"}


class TestDeclaredEntries:
    """Sweeps the real table. Vacuous while it is empty; it bites as entries land.

    The fields a conversation needs are exactly the ones argparse would never have made
    anyone write, which is why they are the ones worth a sweep: `intent` (what the person
    wanted), `preconditions` (what must hold first), `effect_line` (what is about to happen),
    `effect_class` (what that disturbs here) and `network` (what leaves the machine). An entry
    that has flags and help text but none of these is the stage-2 failure mode — a CLI table
    wearing a registry's name.
    """

    def test_every_capability_carries_the_conversation_fields(self):
        for entry in CAPABILITIES:
            assert entry.intent.strip(), f"{entry.id}: intent is empty"
            assert entry.preconditions, f"{entry.id}: preconditions are empty"
            assert entry.effect_line.strip(), f"{entry.id}: effect_line is empty"
            assert entry.effect_class in EFFECT_CLASSES, f"{entry.id}: effect_class"
            assert entry.network in NETWORK_REACH, f"{entry.id}: network"

    def test_intent_is_not_a_restatement_of_the_verb(self):
        for entry in CAPABILITIES:
            words = entry.intent.lower().split()
            assert len(words) > 2, (
                f"{entry.id}: intent {entry.intent!r} says no more than the verb does; "
                "write what the person wanted, not what the command is called"
            )

    def test_every_id_is_declared_once(self):
        ids = [entry.id for entry in CAPABILITIES]
        assert len(set(ids)) == len(ids), "a capability id is declared twice"

    def test_every_command_path_is_reachable_once(self):
        paths = [entry.command_path for entry in CAPABILITIES]
        assert len(set(paths)) == len(paths), "two capabilities claim the same command path"

    def test_entries_are_reachable_through_the_container(self):
        for entry in CAPABILITIES:
            assert by_id(entry.id) is entry
            assert entry in children(entry.parent)


#: The argparse keywords whose value is *prose a person reads*, as opposed to the ones that
#: shape parsing. A field reaching any of these is on the shipped CLI.
PROSE_KWARGS = frozenset({"help", "description", "epilog", "metavar", "usage"})

#: The parameter names `parser.py` binds a record to, and the record each one is.
PROJECTION_RECEIVERS = {"flag": "Flag", "capability": "Capability"}

#: Hiragana, katakana, CJK ideographs, fullwidth forms and CJK punctuation. Latin words in a
#: Japanese line (`policy`, `repository`) are exactly what makes a substring test useless
#: here, so the question asked is "does any Japanese character appear", not "is this English".
JAPANESE = re.compile(r"[぀-ヿ㐀-鿿！-｠　-〿]")

#: Who each declared string is written for, and therefore which language it is in. Only the
#: audience decides: `effect_line` is Japanese *because* `talk-loop.md` speaks Japanese, and
#: `Flag.help` is English *because* it is printed by `rig-wb <group> --help` alongside code,
#: comments and `README.md`. `intent` is in both columns — a conversation matches an utterance
#: against it and `parser.py` hands it to `add_parser(help=...)` — so it is English and the
#: conversation reads English there. If that ever stops being acceptable, the fix is a second
#: field for the spoken line, not a Japanese `intent`.
#:
#: `Capability.summary` is that second field, arriving for the other reason the row above
#: anticipated. The two readers of `intent` turned out to want different *sentences* before
#: they wanted different languages: making govern's parser a projection printed ten intents
#: where ten terse help lines had been, and `govern can` lost the exit codes a script is
#: written against. So the CLI's line moved into its own optional field and `intent` was left
#: alone — which is the same answer, applied to divergence in content rather than in script.
#: It is swept for Japanese here exactly as `Flag.help` is, because `parser.py` prints it;
#: `intent` stays in the sweep too, since it is still what prints wherever no summary is
#: written (125 of the 135 capabilities today).
AUDIENCE = {
    ("Capability", "intent"): "the conversation and the shipped CLI",
    ("Capability", "summary"): "the shipped CLI, where `intent` is not the line it wants",
    ("Capability", "effect_line"): "the conversation (talk-loop.md), which speaks Japanese",
    ("Flag", "help"): "the shipped CLI",
}


def projected_prose_fields() -> set[tuple[str, str]]:
    """The declared fields `parser.py` puts in front of a CLI user, read out of that file.

    Listing them here instead would only prove the list matches itself, and the mistake this
    guards against arrives precisely as *a new projection* — `description=capability.
    effect_line`, say — which a hand-written list would not notice. So the projection is
    parsed: every `help=`/`description=`/… whose value mentions an attribute of the `flag` or
    `capability` the projection was handed.
    """
    tree = ast.parse(PARSER_SOURCE.read_text(encoding="utf-8"))
    found: set[tuple[str, str]] = set()

    def collect(value: ast.AST) -> None:
        for node in ast.walk(value):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in PROJECTION_RECEIVERS
            ):
                found.add((PROJECTION_RECEIVERS[node.value.id], node.attr))

    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg in PROSE_KWARGS:
            collect(node.value)
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in PROSE_KWARGS:
                    collect(value)
    return found


def cli_facing_strings():
    """Every string the CLI prints, as `(what to call it, field, value)`."""
    for owner, field in sorted(projected_prose_fields()):
        for entry in CAPABILITIES:
            if owner == "Capability":
                # An optional field (`summary`) is absent on most entries, and absent means
                # the CLI prints the other one — there is no string to hold to a language.
                if (value := getattr(entry, field)) is not None:
                    yield entry.id, f"{owner}.{field}", value
            else:
                for flag in entry.flags:
                    yield f"{entry.id} {flag.name}", f"{owner}.{field}", getattr(flag, field)


class TestWhichAudienceReadsWhichField:
    """One table, two audiences, two languages — and only the audience decides which.

    Stage 2 wrote 188 of the 580 `Flag.help` strings in Japanese, which was invisible until
    stage 3 made `govern`'s parser a projection of the table and `rig-wb govern --help`
    started printing them. The conversation fields were right to be Japanese and stayed;
    what went wrong is that a CLI-facing field was filled in as though it were one of them.

    So this does not ask "is the table in English". It asks the projection which fields reach
    a CLI user, and holds *those* to the language every other product surface uses — code,
    comments, commit messages, `README.md`. `effect_line` is swept by nothing here, on
    purpose: it is read aloud by `talk-loop.md`, and English there would be the same mistake
    pointing the other way.
    """

    def test_the_projection_reaches_exactly_the_fields_this_file_knows_about(self):
        projected = projected_prose_fields()
        assert projected == {
            ("Flag", "help"),
            ("Capability", "intent"),
            ("Capability", "summary"),
        }, (
            f"registry/parser.py now prints {sorted(projected)} to CLI users. Every field "
            "here is swept for Japanese below, so add it to AUDIENCE — and if the new one "
            "is a field the conversation owns (effect_line), the projection is the bug: a "
            "Japanese line cannot be printed by `rig-wb --help` and spoken by talk-loop at "
            "the same time."
        )

    def test_no_string_the_cli_prints_is_japanese(self):
        offenders = [
            (where, field, value)
            for where, field, value in cli_facing_strings()
            if JAPANESE.search(value)
        ]
        assert not offenders, "\n".join(
            f"{where}: {field} is Japanese — {AUDIENCE[tuple(field.split('.'))]} reads it, "
            f"and that surface is English: {value!r}"
            for where, field, value in offenders
        )

    def test_the_conversation_keeps_its_japanese(self):
        """The converse, so that "make the table English" cannot pass as a fix.

        `effect_line` is the sentence a person sees immediately before something runs, in the
        language `skills/engine/facets/instructions/talk-loop.md` requires. Nothing above
        touches it; this says so out loud, with the real table as the witness.
        """
        assert ("Capability", "effect_line") not in projected_prose_fields()
        spoken = [entry.id for entry in CAPABILITIES if JAPANESE.search(entry.effect_line)]
        assert len(spoken) == len(CAPABILITIES), (
            "every effect_line is spoken by talk-loop.md and is Japanese on purpose; "
            f"{sorted(set(e.id for e in CAPABILITIES) - set(spoken))} are not"
        )
