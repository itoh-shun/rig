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

The rest is the container's behaviour, and one validation sweep over the real entries. That
sweep passes vacuously today — `CAPABILITIES` is empty — and starts biting the moment the
first entry lands without the fields a conversation needs, which is the point of writing it
before the entries exist rather than after.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re

import pytest

from rig_workbench.registry import CAPABILITIES, by_id, children
from rig_workbench.registry.model import (
    EFFECT_CLASSES,
    NO_PRECONDITION,
    PARENTS,
    Capability,
    ExitCode,
    Flag,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL_SOURCE = REPO_ROOT / "rig_workbench" / "registry" / "model.py"


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
        "flags": (Flag(name="task_id", type="string", help="the task to judge"),),
        "output_schema": "rig.gate-report/v1",
        "exit_codes": (ExitCode(code=0, meaning="the gate passed"),),
    }
    fields.update(overrides)
    return Capability(**fields)


class TestContainer:
    """The table exists, is empty, and is a tuple — stage 2 declares, it does not wire."""

    def test_importable_and_empty(self):
        assert CAPABILITIES == ()

    def test_is_a_tuple(self):
        # A list would let one projection append to the table another is reading.
        assert isinstance(CAPABILITIES, tuple)


class TestNoCallables:
    """No field may hold something that can be called. Enforced, not merely documented."""

    def handler(self) -> None:  # a plausible mistake: the function that runs the capability
        return None

    @pytest.mark.parametrize(
        "field",
        ["id", "parent", "verb", "intent", "effect_line", "effect_class", "output_schema"],
    )
    def test_scalar_field_refuses_a_function(self, field):
        with pytest.raises(TypeError, match="callable"):
            capability(**{field: self.handler})

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

    def test_effect_class_projects_onto_the_mcp_annotations_already_in_use(self):
        # These are remote_mcp.py's three hand-built annotation sets, not a second opinion.
        assert capability(effect_class="read-only").mcp_hints == {
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        }
        assert capability(effect_class="writes-worktree").mcp_hints == {
            "readOnlyHint": False, "destructiveHint": True,
            "idempotentHint": False, "openWorldHint": False,
        }
        assert capability(effect_class="network").mcp_hints == {
            "readOnlyHint": False, "destructiveHint": True,
            "idempotentHint": False, "openWorldHint": True,
        }

    def test_as_dict_is_json_shaped(self):
        record = capability().as_dict()
        assert record["flags"] == [{
            "name": "task_id", "type": "string", "help": "the task to judge",
            "required": False, "choices": [], "default": None,
        }]
        assert record["exit_codes"] == [{"code": 0, "meaning": "the gate passed"}]
        assert record["command_path"] == ["wb", "gate"]

    def test_a_flag_knows_whether_it_is_positional(self):
        assert Flag(name="task_id", type="string", help="the task").positional
        assert not Flag(name="--json", type="bool", help="emit JSON").positional

    def test_a_choice_flag_declares_its_choices(self):
        with pytest.raises(ValueError, match="choices"):
            Flag(name="--runtime", type="choice", help="which runtime")
        with pytest.raises(ValueError, match="choices"):
            Flag(name="--json", type="bool", help="emit JSON", choices=("yes", "no"))


class TestLookup:
    """`by_id` and `children`, against a locally built table."""

    table = (
        capability(id="usage", parent=None, verb="usage", effect_class="read-only"),
        capability(id="wb.gate", parent="wb", verb="gate"),
        capability(id="wb.accept", parent="wb", verb="accept",
                   effect_class="writes-worktree"),
        capability(id="pack.install", parent="pack", verb="install",
                   effect_class="network"),
    )

    def test_by_id_finds_a_declared_capability(self):
        found = by_id("wb.accept", self.table)
        assert found is not None and found.verb == "accept"

    def test_by_id_answers_none_for_an_undeclared_one(self):
        assert by_id("wb.nope", self.table) is None

    def test_by_id_reads_the_real_table_by_default(self):
        assert by_id("wb.gate") is None  # empty until stage 2's entries land

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
        assert children("wb") == ()


class TestDeclaredEntries:
    """Sweeps the real table. Vacuous while it is empty; it bites as entries land.

    The four fields a conversation needs are exactly the ones argparse would never have made
    anyone write, which is why they are the ones worth a sweep: `intent` (what the person
    wanted), `preconditions` (what must hold first), `effect_line` (what is about to happen)
    and `effect_class` (how much that matters). An entry that has flags and help text but
    none of these is the stage-2 failure mode — a CLI table wearing a registry's name.
    """

    def test_every_capability_carries_the_four_conversation_fields(self):
        for entry in CAPABILITIES:
            assert entry.intent.strip(), f"{entry.id}: intent is empty"
            assert entry.preconditions, f"{entry.id}: preconditions are empty"
            assert entry.effect_line.strip(), f"{entry.id}: effect_line is empty"
            assert entry.effect_class in EFFECT_CLASSES, f"{entry.id}: effect_class"

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
