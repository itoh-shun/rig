"""What rig can do, declared once, in the words of the person who wants it.

`docs/v3-architecture-design-brief.ja.md` §2 counts the damage: 137 capabilities registered
three ways at three levels (an if/elif chain in `rig_workbench/cli.py`, ~51 `add_parser`
calls in `rig_workbench/workbench/cli.py`, a `COMMANDS` dict in
`rig_workbench/orchestrate/cli.py`), and four consumers — the CLI, `scripts/mcp_server.py`,
`rig_workbench/remote_mcp.py`, `action.yml`, and the routing in `talk-loop.md` — each
keeping its own hand-written copy of the list. Nothing checks the copies against each other,
so they drift, and the drift is invisible until somebody types a verb that only three of
them know about.

§9 settles which way this record points. **Intent is the source of truth and the CLI is a
projection.** argparse needs flag names, types and help strings; a conversation needs the
intent, the preconditions, and one line saying what is about to happen before it happens.
Declaring the argparse half and asking a conversation to reverse-engineer the rest is the
arrangement that produced the current mess, so the conversation half is declared first here
and the flags sit beside it as the part argparse consumes.

**This is declaration, not dispatch.** A `Capability` says what exists, what it needs, what
it will do, and how it answers. It never says how to do it: execution stays in the code that
already performs it, and rewiring the surfaces onto this table is stage 3. So a `Capability`
may not carry a callable in any field — not a handler, not a validator, not `int` as a flag
type. That is enforced structurally (see `_reject_callables`), for two reasons. A record with
a function in it cannot be serialised for the MCP servers or the GitHub Action, and it cannot
be read without importing whatever the function closes over — which is how a leaf module
becomes another hub. Flag types are therefore *names* of types (`"int"`), not type objects.

The module is a leaf on purpose: it imports nothing from `rig_workbench.workbench` or
`rig_workbench.orchestrate`, and neither `argparse` nor `subprocess`. Its one package import
is `exitcodes.RESERVED`, so the codes a capability may claim are checked against the single
place that owns them rather than a second copy of the list.
"""

from __future__ import annotations

import dataclasses
import re

from ..exitcodes import RESERVED

#: The schema id for the JSON projection of this table (`as_dict`).
SCHEMA = "rig.capability-registry/v1"

#: The surfaces a capability can hang under. `None` is a top-level `rig-wb <verb>`; every
#: other value is the word that comes before the verb. Closed on purpose — a new group is a
#: change to the command surface, which is an observable contract, not a field value.
PARENTS = ("wb", "govern", "pack", "eval", "baseline", "githooks")

#: What running it does to the world, in the vocabulary `remote_mcp.py` already uses. The
#: three MCP annotation sets that file builds by hand map onto these one for one (see
#: `mcp_hints`): `read_annotations` is `read-only`, `destructive_annotations` is the two
#: writing classes, `run_annotations` is `network`. A parallel vocabulary would mean deciding
#: twice which tools an MCP client is allowed to call without asking.
#:
#: Exactly one applies. When more than one is true of a capability, take the strongest in
#: this order — it is ordered by what a person would most want to be asked about first:
#: reading nothing, writing rig's own records, touching the user's files or branches, leaving
#: the machine. `rig-wb pack install` both fetches and writes, and is `network`.
READ_ONLY = "read-only"
WRITES_STATE = "writes-state"
WRITES_WORKTREE = "writes-worktree"
NETWORK = "network"
EFFECT_CLASSES = (READ_ONLY, WRITES_STATE, WRITES_WORKTREE, NETWORK)

#: What a flag carries, named rather than referenced. `"int"` and not `int`, because a type
#: object is callable and this record refuses callables; also because the CLI, the two MCP
#: servers and `action.yml` each want a different spelling of the same idea, and a name can
#: be translated into all three while a Python type cannot.
#:
#: `bool` is argparse's `store_true`. `string-list` is a flag that may be given more than
#: once (`action="append"`) or a positional taking several values. `choice` requires
#: `choices` and nothing else may set it. `path` is a string that names a file — worth
#: keeping apart from `string` because it is the one type a precondition can check.
FLAG_TYPES = ("string", "int", "float", "bool", "path", "choice", "string-list")

#: A capability that deliberately requires nothing. `preconditions` is never empty: an empty
#: tuple would read as "nobody has written these down yet", and this table has to be able to
#: tell that apart from "checked, and there is nothing to check" — the same distinction
#: `workbench/intent.py` draws between `unsatisfied` and `unverifiable`, and the brief's §4
#: between a measurement and the absence of one.
NO_PRECONDITION = "none"

_ID = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_TOKEN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SCHEMA_ID = re.compile(r"^rig\.[a-z0-9-]+/v[0-9]+$")
_FLAG_NAME = re.compile(r"^(?:--[a-z0-9]+(?:-[a-z0-9]+)*|[a-z0-9]+(?:_[a-z0-9]+)*)$")


def _reject_callables(owner: str, field: str, value: object) -> None:
    """Refuse a callable anywhere inside a declared field, however deeply nested.

    The rule this enforces is the module docstring's: a capability declares, it does not
    dispatch. Checking `callable()` rather than the annotation is deliberate — annotations
    are not enforced at runtime, and the mistake this catches is a natural one to make
    (`type=int` in a flag, a handler dropped into a spare field) rather than an exotic one.
    """
    if callable(value):
        raise TypeError(
            f"{owner}.{field} carries a callable ({value!r}); the capability registry "
            "declares capabilities, it does not dispatch them. Flag types are names "
            '("int"), not type objects, and handlers stay in the code that runs them.'
        )
    if isinstance(value, tuple):
        for item in value:
            _reject_callables(owner, field, item)


def _tuple(owner: str, field: str, value: object) -> tuple:
    if not isinstance(value, tuple):
        raise TypeError(
            f"{owner}.{field} must be a tuple, not {type(value).__name__}; a list would "
            "make a frozen record mutable through the back door"
        )
    return value


def _line(owner: str, field: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{owner}.{field} must be a string, not {type(value).__name__}")
    if not value.strip():
        raise ValueError(f"{owner}.{field} must not be empty")
    if "\n" in value:
        raise ValueError(f"{owner}.{field} must be one line; it is read aloud, not paged")
    return value


@dataclasses.dataclass(frozen=True)
class Flag:
    """One thing the caller can pass, in the shape argparse and a JSON schema both need.

    `name` is either `--long-form` or a positional (`task_id`); the leading dashes are what
    tells them apart, so nothing has to declare which it is. Short forms are not declared —
    rig's surface does not use them, and a projection can add one if it ever wants to.
    """

    name: str
    type: str
    help: str
    required: bool = False
    choices: tuple[str, ...] = ()
    default: str | int | float | bool | None = None

    def __post_init__(self) -> None:
        for field in dataclasses.fields(self):
            _reject_callables("Flag", field.name, getattr(self, field.name))
        if not isinstance(self.name, str) or not _FLAG_NAME.match(self.name):
            raise ValueError(
                f"flag name {self.name!r} must be `--kebab-case` or a `snake_case` positional"
            )
        if self.type not in FLAG_TYPES:
            raise ValueError(
                f"flag {self.name}: type {self.type!r} is not one of {', '.join(FLAG_TYPES)}"
            )
        _line("Flag", "help", self.help)
        if not isinstance(self.required, bool):
            raise TypeError(f"flag {self.name}: required must be a bool")
        choices = _tuple("Flag", "choices", self.choices)
        if self.type == "choice" and not choices:
            raise ValueError(f"flag {self.name}: type `choice` must declare its choices")
        if self.type != "choice" and choices:
            raise ValueError(
                f"flag {self.name}: choices only mean something on a `choice` flag"
            )
        if any(not isinstance(choice, str) or not choice for choice in choices):
            raise ValueError(f"flag {self.name}: every choice must be a non-empty string")
        if self.default is not None and not isinstance(self.default, (str, int, float, bool)):
            raise TypeError(
                f"flag {self.name}: default must be a plain JSON scalar or None, "
                f"not {type(self.default).__name__}"
            )
        if self.type == "bool" and self.default is not None and not isinstance(self.default, bool):
            raise TypeError(f"flag {self.name}: a `bool` flag's default must be a bool")

    @property
    def positional(self) -> bool:
        """Is this typed as a bare word, or behind a `--flag`?"""
        return not self.name.startswith("--")

    def as_dict(self) -> dict:
        """Every field, in the shapes JSON holds — derived, so a new field cannot fall out."""
        return {
            f.name: list(value) if isinstance(value := getattr(self, f.name), tuple) else value
            for f in dataclasses.fields(self)
        }


@dataclasses.dataclass(frozen=True)
class ExitCode:
    """One status this capability actually returns, and what a caller may conclude from it.

    `exitcodes.py` fixes 0/1/2 for every rig command and reserves the shell's own codes; what
    varies per capability is which of them can really come back, plus the command-specific
    ones (`gh-check` answers 3 and 5). Declaring the set is what lets a caller — CI, another
    agent, `action.yml` — branch on a status without reading the source of the command.
    """

    code: int
    meaning: str

    def __post_init__(self) -> None:
        for field in dataclasses.fields(self):
            _reject_callables("ExitCode", field.name, getattr(self, field.name))
        if not isinstance(self.code, int) or isinstance(self.code, bool):
            raise TypeError(f"exit code must be an int, not {type(self.code).__name__}")
        if not 0 <= self.code <= 255:
            raise ValueError(f"exit code {self.code} is outside 0-255")
        if self.code in RESERVED:
            raise ValueError(
                f"exit code {self.code} is reserved by the shell (timeout, signals, "
                "not-executable); rig never assigns it — see rig_workbench/exitcodes.py"
            )
        _line("ExitCode", "meaning", self.meaning)

    def as_dict(self) -> dict:
        return {"code": self.code, "meaning": self.meaning}


@dataclasses.dataclass(frozen=True)
class Capability:
    """One thing rig can do, declared once for every surface that offers it.

    Immutable and callable-free by construction: this is a table to be read, projected and
    serialised, never a dispatch entry. Construction validates shape and vocabulary; whether
    the sentences are any *good* is not something a type can decide, so the fields that carry
    them say in their own docs what makes one good.
    """

    #: Stable name for this capability, `kebab.dotted`. Conventionally the command path
    #: (`wb.gate`, `pack.install`), but deliberately not derived from it: an id survives a
    #: verb being renamed, and everything that stores a reference — a run log, a policy, a
    #: waiver — stores this rather than the words someone typed.
    id: str

    #: `None` for a top-level `rig-wb <verb>`, else the group it hangs under (`PARENTS`).
    parent: str | None

    #: The word typed on the command line, after the parent. One word almost always; a
    #: space-separated pair where the surface nests a level `PARENTS` does not name
    #: (`queue add`), so that `command_path` stays the literal thing a person types.
    verb: str

    #: One line, in human words, naming what someone wants when they reach for this.
    #:
    #: This is the field a conversation matches an utterance against, and it is the source
    #: of truth §9 puts above the CLI — so it must carry information the verb does not.
    #: "Run the gate" is a restatement of `gate` and matches nothing that a person would
    #: actually say; "check whether the work is finished enough to accept, and say what is
    #: still missing" is what they meant when they said 「これもう終わってる?」.
    #: Write the want, not the mechanism: no flag names, no file paths, no rig jargon
    #: would have had to read the docs to use. Present tense, one sentence, no trailing full
    #: stop needed. Where two capabilities would honestly get the same intent line, that is
    #: a finding about the surface, not a licence to copy the line.
    intent: str

    #: What a machine can check holds before this runs, as stable kebab-case check names —
    #: `git-repo`, `task-exists`, `worktree-clean`, `gh-available`. Names, not sentences:
    #: stage 3 attaches one checker per name, and prose could not be attached to anything.
    #: Never empty; a capability that genuinely needs nothing declares `(NO_PRECONDITION,)`,
    #: so "nothing is required" stays distinguishable from "nobody filled this in".
    preconditions: tuple[str, ...]

    #: One line shown to a person immediately before it runs, saying what is about to happen.
    #:
    #: `talk-loop.md` step 5 prints the invocation as `→ rig-wb wb gate` and then declares or
    #: confirms; this is the sentence that follows it. So it is about to happen, not it
    #: happened: "worktree と branch を削除します" before the deletion, never after. It names
    #: what will be touched, because it is the last thing a person sees before a
    #: `writes-worktree` or `network` capability is confirmed, and a line that says only
    #: "続けます" gives them nothing to refuse.
    effect_line: str

    #: What running it does to the world; one of `EFFECT_CLASSES`.
    effect_class: str

    #: What the caller may pass. Order is the order a projection shows them in; positionals
    #: are the ones whose `name` has no leading dashes, so declare them first.
    flags: tuple[Flag, ...] = ()

    #: The `rig.<name>/v<N>` id of the document it emits, or `None` when it prints only prose.
    output_schema: str | None = None

    #: The statuses this capability actually returns.
    exit_codes: tuple[ExitCode, ...] = ()

    def __post_init__(self) -> None:
        for field in dataclasses.fields(self):
            _reject_callables("Capability", field.name, getattr(self, field.name))
        if not isinstance(self.id, str) or not _ID.match(self.id):
            raise ValueError(f"capability id {self.id!r} must be lowercase `kebab.dotted`")
        if self.parent is not None and self.parent not in PARENTS:
            raise ValueError(
                f"{self.id}: parent {self.parent!r} is not one of {', '.join(PARENTS)} (or None)"
            )
        _line("Capability", "verb", self.verb)
        if any(not _TOKEN.match(token) for token in self.verb.split(" ")):
            raise ValueError(
                f"{self.id}: verb {self.verb!r} must be the word typed on the command line "
                "(kebab-case, or two such words where the surface nests)"
            )
        _line("Capability", "intent", self.intent)
        _line("Capability", "effect_line", self.effect_line)
        if self.effect_class not in EFFECT_CLASSES:
            raise ValueError(
                f"{self.id}: effect_class {self.effect_class!r} is not one of "
                f"{', '.join(EFFECT_CLASSES)}"
            )
        preconditions = _tuple("Capability", "preconditions", self.preconditions)
        if not preconditions:
            raise ValueError(
                f"{self.id}: preconditions must not be empty; declare "
                f"({NO_PRECONDITION!r},) to say deliberately nothing is required"
            )
        for name in preconditions:
            if not isinstance(name, str) or not _TOKEN.match(name):
                raise ValueError(
                    f"{self.id}: precondition {name!r} must be a kebab-case check name, "
                    "not a sentence — stage 3 attaches a checker to each one"
                )
        if len(set(preconditions)) != len(preconditions):
            raise ValueError(f"{self.id}: preconditions are declared twice")
        for flag in _tuple("Capability", "flags", self.flags):
            if not isinstance(flag, Flag):
                raise TypeError(f"{self.id}: flags must be Flag records, got {flag!r}")
        names = [flag.name for flag in self.flags]
        if len(set(names)) != len(names):
            raise ValueError(f"{self.id}: the same flag is declared twice")
        if self.output_schema is not None and not _SCHEMA_ID.match(str(self.output_schema)):
            raise ValueError(
                f"{self.id}: output_schema {self.output_schema!r} must look like "
                "`rig.<name>/v<N>`, or be None when the command prints only prose"
            )
        codes = _tuple("Capability", "exit_codes", self.exit_codes)
        if not codes:
            raise ValueError(
                f"{self.id}: exit_codes must name at least the status a success returns"
            )
        for code in codes:
            if not isinstance(code, ExitCode):
                raise TypeError(f"{self.id}: exit_codes must be ExitCode records, got {code!r}")
        seen = [code.code for code in codes]
        if len(set(seen)) != len(seen):
            raise ValueError(f"{self.id}: the same exit code is declared twice")

    @property
    def command_path(self) -> tuple[str, ...]:
        """The words typed, in order: `("wb", "gate")`, `("pack", "install")`, `("usage",)`."""
        parent = () if self.parent is None else (self.parent,)
        return (*parent, *self.verb.split(" "))

    @property
    def mcp_hints(self) -> dict:
        """`effect_class` in the annotation vocabulary the MCP adapters already speak.

        Not a second opinion about safety — the three sets `remote_mcp.py` builds by hand map
        onto the four classes exactly, and this reproduces them: its `read_annotations` for
        `read-only`, its `destructive_annotations` for both writing classes, its
        `run_annotations` for `network`. Deliberately conservative in the same way that file
        is: everything that is not read-only is flagged destructive, because an MCP client
        decides from this whether to ask a person first.
        """
        read_only = self.effect_class == READ_ONLY
        return {
            "readOnlyHint": read_only,
            "destructiveHint": not read_only,
            "idempotentHint": read_only,
            "openWorldHint": self.effect_class == NETWORK,
        }

    def as_dict(self) -> dict:
        """The whole record in the shapes JSON holds, for the MCP and Action projections."""
        return {
            "id": self.id,
            "parent": self.parent,
            "verb": self.verb,
            "command_path": list(self.command_path),
            "intent": self.intent,
            "preconditions": list(self.preconditions),
            "effect_line": self.effect_line,
            "effect_class": self.effect_class,
            "flags": [flag.as_dict() for flag in self.flags],
            "output_schema": self.output_schema,
            "exit_codes": [code.as_dict() for code in self.exit_codes],
        }
