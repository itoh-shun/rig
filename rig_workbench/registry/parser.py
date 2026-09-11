"""The CLI as a projection: an argparse tree built from `Capability` records.

`docs/v3-architecture-design-brief.ja.md` §9 settles which way the registry points — 「正本
は意図であり、CLI はその投影である」 — and §2 says what that buys: one declaration, and every
surface reading it instead of keeping a hand-written copy. Stage 2 declared the table and held
it against the shipping parsers *by test* (`tests/test_capability_registry_vs_cli.py`), which
is two sources of truth for one surface, accepted for one stage. This module is the half that
makes the second one unnecessary: it turns `Capability` records into the parser a group offers,
so the CLI can stop being written down twice.

**It builds a parser; it does not know what any verb does.** A `Capability` may not carry a
callable (`model._reject_callables`), so nothing here can bind a verb to the function that
runs it. That binding — `set_defaults(func=...)` in today's parsers — stays with the caller,
which is the one place that may import both the table and the code. The caller is therefore
expected to do exactly one thing this module cannot:

    parser = build_group_parser(children("govern"), prog="rig-wb govern", description=...)
    parser.parse_args(argv)             # every flag, in the shapes the registry declares
    ...                                 # and the caller maps `args.cmd` to its handler

**A leaf, like the rest of `rig_workbench/registry/`.** It imports `argparse` — it is the
argparse projection, so the alternative would be handing the caller a description of a parser
and asking it to build one — and `.model`, and nothing else from `rig_workbench`. It reads no
files, runs no processes, prints nothing.

Turning a declared type *name* into what argparse wants
-------------------------------------------------------

`Flag.type` is a name (`"int"`), never a type object, because a type object is callable and
the registry refuses callables. `ARGPARSE_TYPES` is the single translation, and it is
deliberately not `getattr(builtins, name)`: only two of the seven names are a Python
converter at all. `"string"`, `"path"`, `"choice"` and `"string-list"` map to `None`, meaning
*pass no `type=` at all* — argparse's own default leaves the string untouched, and passing
`type=str` instead would put a converter on the action that the hand-written parsers do not
have, which is a difference a reader would have to argue about for no gain.

An unhandled name raises `UnknownFlagType` rather than falling through to a string. The
mistake it catches is a real one: `FLAG_TYPES` grows a name, every flag using it parses as a
string, and the first symptom is a value arriving at a handler in the wrong Python type —
after argparse, after validation, far from the declaration. `model.Flag` already rejects a
type outside `FLAG_TYPES` at construction, so in a healthy tree this cannot fire; it fires on
the *next* edit, which is when it is worth having.

What each declaration becomes
-----------------------------

| declared | argparse |
|---|---|
| `name` without dashes | a positional |
| positional, `required=True` | no `nargs` (argparse requires exactly one) |
| positional, `required=False` | `nargs="?"` |
| positional, `string-list` | `nargs="+"` when required, `nargs="*"` when not |
| `--flag`, `bool` | `action="store_true"` (so the default is `False`, not `None`) |
| `--flag`, `string-list` | `action="append"` — the repeatable flag |
| `--flag`, `required=True` | `required=True` |
| `choices` | `choices=(...)`, on a positional or an option alike |
| `default` other than `None` | `default=...`; `None` is left to argparse |
| `aliases` | extra option strings after the name: `add_argument(name, *aliases)` |
| `dest` other than `None` | `dest=...`; `None` leaves argparse to derive it |

`bool` is an option-only type: a positional that stores `True` without consuming a word is
not a thing argparse can build, and a `bool` positional in the table is a declaration error
rather than something to project into an approximation.

What a `Flag` could not say, and now can
----------------------------------------

Generating `govern` and comparing it against the shipped parser
(`tests/test_generated_parser_equivalence.py`) found two things the shipping parsers do that
no field could express, and `model.Flag` grew a field for each:

* **`dest`** — `govern waiver` declares `--criterion` but stores it as `args.criteria`, and
  `govern audit` declares `--action` but stores it as `args.filter_action`. Given argparse's
  derived dest, the first renamed a field the handler reads and the second *collided with
  `audit`'s own `action` positional* and overwrote it. Nothing here papers over that; the
  declaration says it now (`Flag.dest`), and `Capability` refuses two flags that land on one
  attribute, so the collision fails at import rather than at parse time.
* **aliases** — `wb route` accepts `--explicit-recipe` beside `--recipe` as one action with
  two spellings. `Flag.aliases` carries the extra spellings and this module passes them
  straight through as further option strings.

Neither is projected into an approximation: what the declaration says is what the parser
gets, and where it says nothing argparse's own default stands.
"""

from __future__ import annotations

import argparse

from .model import FLAG_TYPES, Capability, Flag


class UnknownFlagType(ValueError):
    """A declared `Flag.type` this projection has no translation for."""


#: The one place a declared type *name* becomes what argparse takes. `None` means "pass no
#: `type=`": argparse then hands the raw string through, which is what every hand-written
#: `add_argument` in the tree does for these four.
ARGPARSE_TYPES: dict[str, type | None] = {
    "string": None,
    "int": int,
    "float": float,
    "bool": None,
    "path": None,
    "choice": None,
    "string-list": None,
}


def argparse_type(type_name: str) -> type | None:
    """The converter argparse should apply, or `None` when it should apply none.

    Raises `UnknownFlagType` for a name this module has not been taught, rather than
    defaulting to a string — see the module docstring on why the silent fallback is the
    expensive one.
    """
    try:
        return ARGPARSE_TYPES[type_name]
    except KeyError:
        raise UnknownFlagType(
            f"flag type {type_name!r} has no argparse projection; known names are "
            f"{', '.join(sorted(ARGPARSE_TYPES))} (registry.model.FLAG_TYPES declares "
            f"{', '.join(FLAG_TYPES)}). Teach ARGPARSE_TYPES the new name — a flag whose "
            "type is not understood must not quietly become a string."
        ) from None


def flag_kwargs(flag: Flag) -> dict:
    """Everything `add_argument` should be given for this flag, except its name.

    Separated from the call so a test can read the projection of one declaration without
    building a parser around it, and so the two callers below share one set of rules.
    """
    kwargs: dict = {"help": flag.help}
    converter = argparse_type(flag.type)

    if flag.positional:
        if flag.type == "bool":
            raise ValueError(
                f"flag {flag.name!r}: `bool` is an option-only type — a positional that "
                "stores True without consuming a word is not something argparse builds. "
                "Declare it as `--flag`, or give the positional a type that carries a value."
            )
        if flag.type == "string-list":
            kwargs["nargs"] = "+" if flag.required else "*"
        elif not flag.required:
            kwargs["nargs"] = "?"
    elif flag.type == "bool":
        kwargs["action"] = "store_true"
        converter = None
    else:
        if flag.type == "string-list":
            kwargs["action"] = "append"
        if flag.required:
            kwargs["required"] = True

    if converter is not None:
        kwargs["type"] = converter
    if flag.choices:
        kwargs["choices"] = flag.choices
    if flag.default is not None:
        kwargs["default"] = flag.default
    if flag.dest is not None:
        # Only when declared. Passing `dest=flag.dest_name` unconditionally would be the
        # same parser, but it would also put a `dest=` on every positional, which argparse
        # refuses outright ("dest supplied twice for positional argument").
        kwargs["dest"] = flag.dest
    return kwargs


def add_flag(parser: argparse.ArgumentParser, flag: Flag) -> argparse.Action:
    """Attach one declared flag to a parser, and hand back the action argparse built.

    `option_strings` is the canonical name followed by its aliases, which is the order
    argparse reads them in: the first is what the usage line shows and what an undeclared
    dest is derived from.
    """
    return parser.add_argument(*flag.option_strings, **flag_kwargs(flag))


def add_capability(
    subparsers: argparse._SubParsersAction, capability: Capability
) -> argparse.ArgumentParser:
    """Register one capability under `subparsers`, nesting where its verb is two words.

    `Capability.verb` is "the word typed on the command line", and a few verbs are two of
    them (`pack source list`). Those nest: the first word becomes a sub-parser whose own
    subparsers are `required=True`, because `rig-wb pack source` alone is a usage error and
    not a command. A group created for one two-word verb is reused by its siblings.
    """
    words = capability.verb.split(" ")
    parent = subparsers
    for word in words[:-1]:
        existing = parent.choices.get(word)
        if existing is None:
            group = parent.add_parser(word, help=f"{word} …")
            parent = group.add_subparsers(dest=f"{word}_cmd", required=True)
        else:
            parent = next(
                action
                for action in existing._actions
                if isinstance(action, argparse._SubParsersAction)
            )

    leaf = parent.add_parser(words[-1], help=capability.intent)
    for flag in capability.flags:
        add_flag(leaf, flag)
    return leaf


def build_group_parser(
    capabilities: tuple[Capability, ...],
    *,
    prog: str | None = None,
    description: str | None = None,
    dest: str = "cmd",
) -> argparse.ArgumentParser:
    """The parser for one group of capabilities — `rig-wb govern`, `rig-wb pack`, …

    `capabilities` is passed in rather than looked up by parent so that this stays a
    function of its argument: a caller holding a filtered view (one pillar mid-migration,
    a test's three records) gets a parser for exactly those. `prog` and `description` are
    the caller's because they are presentation, and the registry declares intent.

    The sub-parsers are `required=True`: every group in rig today refuses a bare
    `rig-wb <group>` with a usage error, and a capability under a parent is by definition
    reached by naming it.
    """
    parser = argparse.ArgumentParser(prog=prog, description=description)
    subparsers = parser.add_subparsers(dest=dest, required=True)
    for capability in capabilities:
        add_capability(subparsers, capability)
    return parser


def subcommand_parsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    """The sub-parsers of a group parser, by the word that reaches each one.

    The half of the swap this module *can* do for its caller. A caller still has to bind
    each verb to the function that runs it — a `Capability` may not carry a callable, so the
    binding cannot come from the table — and doing that means holding the sub-parser
    objects, which argparse only offers through `_actions`. Reading that here once, and
    saying so, is better than every caller reaching into a private attribute.

    One level only: a two-word verb's leaf is reached through its own group parser, and no
    caller needs it today (`govern` has none). It stays one level until one does, rather
    than growing a flattening rule nothing exercises.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    raise ValueError(
        f"{parser.prog} has no sub-parsers; `subcommand_parsers` reads a group parser built "
        "by `build_group_parser`, not a leaf."
    )
