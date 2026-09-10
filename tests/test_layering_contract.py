"""The import rule for stage 3 (design brief §3), and the proof that it works.

`docs/v3-architecture-design-brief.ja.md` §3 settles the whole of stage 3 into one
sentence — *a judgement module may import the six ports and nothing else that touches
the outside* — and then says the thing that makes this file necessary: 「これは散文では
なく import ルールとして機械的に検査する。rig 自身が抱える『散文止まりのルール』を
新しく作らないため。」 A rule that only exists in a paragraph is the weakness rig
measures in other people's repositories. So the sentence is a checker here.

**What this file is not.** `tests/test_architecture_inventory.py` freezes the *numbers*
(cycles, hub fan-in, effect sites) and the `TID251` block in `pyproject.toml` names the
*call sites* (`os.environ`, `subprocess.run`, `datetime.datetime.now`). This file asserts
the *shape*: where a module is allowed to reach. It says nothing about effects, which is
why `import subprocess` in a judgement module is accepted **here** — lint objects to that,
and two files objecting to the same thing in different words is how a rule stops being
read.

The rule
--------

A module in a **migrated** pillar's **judgement layer** may import exactly three things:

1. the standard library,
2. its own pillar,
3. `rig_workbench.ports`.

Nothing else from `rig_workbench`, and nothing third-party — a third-party package is
not one of the three allowances, and a judgement layer that grows one has acquired an
outside dependency the ports exist to hold. Separately, `rig_workbench.ports` itself may
import nothing from `rig_workbench` except `exitcodes` (the same single package import
`registry/model.py` allows itself, and for the same reason: the exit codes have one
owner). Adapters are exempt from that and declared in `PORT_ADAPTERS` — an adapter
exists precisely to hold what a protocol may not.

Relative and absolute imports are resolved to the same dotted name before anything is
decided, so `from ..workbench import state` and `from rig_workbench.workbench import
state` cannot be judged differently.

Function-local imports count, exactly as module-level ones do
-------------------------------------------------------------

This is the decision that gives the rule teeth. rig has 160 function-local imports across
51 modules and 20 of them sit inside the cycles §3 names; the brief's own words are
「関数内 import は循環を消したのではなく隠している」. A rule that read only `tree.body`
would be evaded by the exact pattern stage 3 exists to remove — and evaded *silently*,
because moving an import into a function looks like a fix. So the walk is over the whole
AST: module level, inside a function, inside a method, inside a `try:`, and under
`if TYPE_CHECKING:`. Where the import sits changes only the wording of the failure, never
the verdict. A type-checking-only import is still a design dependency: it means the
module states its own signatures in another pillar's vocabulary.

What it does not catch: `importlib.import_module("rig_workbench.workbench.state")` and
friends. A dotted name assembled at run time is not visible to an AST walk. That hole is
written down rather than papered over; nothing in the tree does this today, and a static
rule that pretended otherwise would be prose again.

Judgement layer versus shell
----------------------------

Not every module in a pillar is judgement. `govern/cli.py` parses flags, prints through
what will be the `Presenter`, and turns a verdict into an exit code; holding it to the
rule would forbid the wiring the pillar is wired *by*. So each pillar declares its shell
in `SHELL_MODULES` — **data, one entry per module, each carrying the reason it is a
shell**. It is a closed list: anything not named there is judgement. Making the exemption
a written sentence is the point. A contributor who hits this rule will want to add an
exception, and an exception that has to be typed out with a justification next to
`govern/cli.py`'s is one they will usually decide not to want.

Proving the rule while nothing is migrated
------------------------------------------

`MIGRATED` is empty: no pillar has moved behind the ports yet, and `govern` joins it in a
later task once its effects are behind them. An empty scan passes vacuously, and a
vacuous pass would keep passing if the checker were written backwards — which is exactly
how a check comes to exist without ever having been checked. So the corpus below runs the
real checker over constructed modules: `REJECTED` covers every violation the rule
distinguishes (and `test_every_violation_kind_has_a_negative_case` fails if a new kind
arrives without one), and `ACCEPTED` covers the compliant shapes *including several that
are one character from a violation* — a `..` that climbs out of a module and lands back
in its own pillar, `rig_workbench.portsmith`, `rig_workbench.eval_extra`, a function-local
import of the module's own pillar, an annotation naming another pillar in a string. A
checker that rejected everything would pass every negative test and fail those.

The measurement is a read-only AST walk. No imports of the code under test, no
subprocess, safe under `pytest -n auto`.
"""

from __future__ import annotations

import ast
import dataclasses
import functools
import pathlib
import sys
import textwrap

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "rig_workbench"
PACKAGE = "rig_workbench"
PORTS_PACKAGE = f"{PACKAGE}.ports"
EXITCODES_MODULE = f"{PACKAGE}.exitcodes"

PORT_NAMES = ("Presenter", "ProcessRunner", "FileStore", "Env", "GitRepo", "Clock")


# ---------------------------------------------------------------------------
# The declarations. Everything the rule needs to know that is not derivable
# from the tree lives here, as data.
# ---------------------------------------------------------------------------

#: Pillars whose judgement layer is behind the six ports. Stage 3 adds one at a time,
#: and a name only belongs here once the pillar actually passes the rule below.
#: Empty today: §7 stage 3 is 未着手. `govern` is next.
MIGRATED: tuple[str, ...] = ()

#: The shell of each pillar: modules that wire, not modules that judge. Closed list —
#: everything else in a migrated pillar is judgement. Every entry states why, because
#: an exemption without a reason is how the exemption list grows.
SHELL_MODULES: dict[str, dict[str, str]] = {
    "govern": {
        f"{PACKAGE}.govern.cli": (
            "The command shell: argparse wiring, the 68 prints that become Presenter "
            "calls, and the mapping from a verdict to an exit code. It is where the "
            "judgement modules get called from, so it is allowed to know about them "
            "and about gitroot; holding it to the rule would forbid the wiring."
        ),
    },
}

#: Modules under `rig_workbench/ports/` that are adapters rather than protocols. An
#: adapter holds the effect its protocol describes, so it reaches into the package on
#: purpose; the protocols must not.
PORT_ADAPTERS: dict[str, str] = {
    f"{PORTS_PACKAGE}.local": (
        "One adapter per port, each wrapping today's behaviour exactly (gitroot, "
        "secure_fs, subprocess). The effects live here so they do not live in the "
        "judgement layer — that is the adapter's whole job."
    ),
}


# ---------------------------------------------------------------------------
# What the checker can object to
# ---------------------------------------------------------------------------

CROSS_PILLAR = "cross-pillar"
PACKAGE_MODULE = "other-rig_workbench-module"
THIRD_PARTY = "third-party"
PORT_LEAF = "port-reaches-into-the-package"

VIOLATION_KINDS = (CROSS_PILLAR, PACKAGE_MODULE, THIRD_PARTY, PORT_LEAF)

FIX = (
    "Do not add an exception. Three ways out, in this order:\n"
    "  1. Name what the module actually needs as a method on one of the six ports in\n"
    f"     rig_workbench/ports/__init__.py ({', '.join(PORT_NAMES)})\n"
    "     and take it as a keyword argument. A port method is written from its call\n"
    "     site, so the site that made you read this is the design input.\n"
    "  2. If the need is wiring and not judgement — parsing flags, printing a report,\n"
    "     turning a verdict into an exit code — it belongs in the pillar's shell.\n"
    "     Move it, and declare that module in SHELL_MODULES in this file with the\n"
    "     reason, next to govern/cli.py's.\n"
    "  3. If the pillar is not behind the ports yet, it does not belong in MIGRATED."
)


# ---------------------------------------------------------------------------
# The layout the rule is applied against. Injectable, so the corpus below can
# state a tree without one existing on disk.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Layout:
    """Which sub-packages are pillars, which have migrated, and what is exempt."""

    pillars: frozenset[str]
    migrated: frozenset[str]
    shell: frozenset[str]
    port_adapters: frozenset[str]


def make_layout(
    *,
    pillars: tuple[str, ...],
    migrated: tuple[str, ...] = (),
    shell: tuple[str, ...] = (),
    port_adapters: tuple[str, ...] = tuple(PORT_ADAPTERS),
) -> Layout:
    return Layout(
        pillars=frozenset(pillars),
        migrated=frozenset(migrated),
        shell=frozenset(shell),
        port_adapters=frozenset(port_adapters),
    )


def _package_directories(root: pathlib.Path) -> tuple[str, ...]:
    return tuple(
        sorted(
            path.name
            for path in root.iterdir()
            if path.is_dir() and path.name != "__pycache__" and (path / "__init__.py").is_file()
        )
    )


@functools.lru_cache(maxsize=1)
def real_layout() -> Layout:
    """The layout of `rig_workbench/` as it is on disk, plus the declarations above."""
    pillars = tuple(name for name in _package_directories(PACKAGE_ROOT) if name != "ports")
    shell = tuple(module for entries in SHELL_MODULES.values() for module in entries)
    return make_layout(
        pillars=pillars,
        migrated=MIGRATED,
        shell=shell,
        port_adapters=tuple(PORT_ADAPTERS),
    )


# ---------------------------------------------------------------------------
# Reading the imports out of a module — all of them, wherever they sit
# ---------------------------------------------------------------------------

AT_MODULE_LEVEL = "at module level"
INSIDE_A_FUNCTION = "inside a function body"
UNDER_TYPE_CHECKING = "under `if TYPE_CHECKING:`"


@dataclasses.dataclass(frozen=True)
class ImportSite:
    """One name brought in by one import statement, wherever the statement sits."""

    target: str  #: the absolute dotted path the name resolves to
    shown: str  #: what to print as "reaches" — the module, not the attribute
    line: int
    where: str
    written: str  #: the statement as the author wrote it


def _alias_text(alias: ast.alias) -> str:
    return f"{alias.name} as {alias.asname}" if alias.asname else alias.name


def _is_type_checking_test(test: ast.expr) -> bool:
    """True for `if TYPE_CHECKING:` and `if typing.TYPE_CHECKING:`."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


class _ImportCollector(ast.NodeVisitor):
    """Every import in the module, tagged by where it sits — never filtered by it."""

    def __init__(self, module: str, is_package: bool) -> None:
        self.base = module if is_package else module.rpartition(".")[0]
        self.sites: list[ImportSite] = []
        self._functions = 0
        self._type_checking = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._functions += 1
        self.generic_visit(node)
        self._functions -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._functions += 1
        self.generic_visit(node)
        self._functions -= 1

    def visit_If(self, node: ast.If) -> None:
        if not _is_type_checking_test(node.test):
            self.generic_visit(node)
            return
        self._type_checking += 1
        for statement in node.body:
            self.visit(statement)
        self._type_checking -= 1
        for statement in node.orelse:
            self.visit(statement)

    def _where(self) -> str:
        if self._type_checking:
            return UNDER_TYPE_CHECKING
        return INSIDE_A_FUNCTION if self._functions else AT_MODULE_LEVEL

    def visit_Import(self, node: ast.Import) -> None:
        written = "import " + ", ".join(_alias_text(a) for a in node.names)
        for alias in node.names:
            self.sites.append(
                ImportSite(alias.name, alias.name, node.lineno, self._where(), written)
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        prefix = self._resolve(node)
        written = (
            "from "
            + "." * node.level
            + (node.module or "")
            + " import "
            + ", ".join(_alias_text(a) for a in node.names)
        )
        for alias in node.names:
            target = f"{prefix}.{alias.name}" if prefix else alias.name
            # `from pkg import name` may name a module or an attribute of it. The
            # attribute cannot change which pillar the import reaches, so the prefix is
            # what gets reported — except when the prefix is the bare package, where the
            # name is the only thing that says where it went (`from rig_workbench import
            # gitroot` versus `from rig_workbench import ports`).
            shown = target if prefix in ("", PACKAGE) else prefix
            self.sites.append(ImportSite(target, shown, node.lineno, self._where(), written))

    def _resolve(self, node: ast.ImportFrom) -> str:
        if not node.level:
            return node.module or ""
        parts = self.base.split(".") if self.base else []
        climb = node.level - 1
        if climb:
            parts = parts[:-climb] if climb < len(parts) else []
        if not parts:
            # A level that climbs past `rig_workbench` is not importable at run time;
            # clamp to the package so it is judged rather than silently dropped.
            parts = [PACKAGE]
        base = ".".join(parts)
        return f"{base}.{node.module}" if node.module else base


def import_sites(module: str, source: str, *, is_package: bool) -> list[ImportSite]:
    collector = _ImportCollector(module, is_package)
    collector.visit(ast.parse(source, filename=module))
    return collector.sites


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Violation:
    module: str
    kind: str
    site: ImportSite
    detail: str
    allowances: tuple[tuple[str, bool], ...]
    location: str  #: `path:line` when scanned from disk, `module:line` otherwise

    def render(self) -> str:
        lines = [
            f"{self.location}  {self.written_short()}",
            f"    sits {self.site.where}",
            f"    reaches {self.site.shown} — {self.detail}",
            "    allowances:",
        ]
        for name, met in self.allowances:
            lines.append(f"        {'ok  ' if met else 'FAIL'} {name}")
        return "\n".join(lines)

    def written_short(self) -> str:
        text = self.site.written
        return text if len(text) <= 88 else text[:85] + "..."


def _stdlib(top: str) -> bool:
    return top in sys.stdlib_module_names


def _judgement_verdict(target: str, pillar: str, layout: Layout) -> tuple[str, str] | None:
    """`(kind, detail)` when `target` is not one of the three allowances."""
    parts = target.split(".")
    top = parts[0]
    if top != PACKAGE:
        if _stdlib(top):
            return None
        return THIRD_PARTY, (
            f"the third-party package '{top}', which is not the standard library, not "
            f"the '{pillar}' pillar, and not a port"
        )
    if len(parts) == 1:
        return PACKAGE_MODULE, "the package root itself, which re-exports whatever it re-exports"
    head = parts[1]
    if head == "ports":
        return None
    if head == pillar:
        return None
    if head in layout.pillars:
        return CROSS_PILLAR, f"the '{head}' pillar, which is not this module's pillar"
    return PACKAGE_MODULE, (
        f"rig_workbench.{head}, which is neither the '{pillar}' pillar nor a port"
    )


def _port_verdict(target: str) -> tuple[str, str] | None:
    """`(kind, detail)` when a port protocol module reaches somewhere it may not."""
    parts = target.split(".")
    top = parts[0]
    if top != PACKAGE:
        if _stdlib(top):
            return None
        return THIRD_PARTY, (
            f"the third-party package '{top}'. The ports are a leaf: the standard "
            "library and rig_workbench.exitcodes, nothing else"
        )
    if target == EXITCODES_MODULE or target.startswith(EXITCODES_MODULE + "."):
        return None
    return PORT_LEAF, (
        "back into rig_workbench. A judgement module that imports the ports must not "
        "acquire this transitively — that is the dependency the port exists to cut"
    )


def _pillar_of(module: str, layout: Layout) -> str | None:
    parts = module.split(".")
    if len(parts) < 2 or parts[0] != PACKAGE:
        return None
    return parts[1] if parts[1] in layout.pillars else None


def _is_port_module(module: str) -> bool:
    return module == PORTS_PACKAGE or module.startswith(PORTS_PACKAGE + ".")


def check_module(
    module: str,
    source: str,
    layout: Layout,
    *,
    is_package: bool = False,
    shown_as: str | None = None,
) -> list[Violation]:
    """Every way `module` breaks the rule, or an empty list.

    Modules the rule does not reach — an unmigrated pillar, a declared shell, a declared
    adapter, a top-level module of the package — return an empty list, which is why
    `test_every_violation_kind_has_a_negative_case` exists rather than trusting this.
    """
    if _is_port_module(module):
        if module in layout.port_adapters:
            return []
        verdict, pillar = _port_verdict, None
    else:
        pillar = _pillar_of(module, layout)
        if pillar is None or pillar not in layout.migrated or module in layout.shell:
            return []

        def verdict(target: str, _pillar: str = pillar) -> tuple[str, str] | None:
            return _judgement_verdict(target, _pillar, layout)

    found: list[Violation] = []
    seen: set[tuple[int, str, str]] = set()
    for site in import_sites(module, source, is_package=is_package):
        answer = verdict(site.target)
        if answer is None:
            continue
        kind, detail = answer
        key = (site.line, site.shown, kind)
        if key in seen:
            continue
        seen.add(key)
        found.append(
            Violation(
                module=module,
                kind=kind,
                site=site,
                detail=detail,
                allowances=_allowances(pillar),
                location=f"{shown_as or module}:{site.line}",
            )
        )
    return found


def _allowances(pillar: str | None) -> tuple[tuple[str, bool], ...]:
    """The three (or two) allowances, each marked. Everything reported here failed
    all of them — saying so one by one is what keeps the message from being a scold."""
    if pillar is None:
        return (
            ("the standard library", False),
            (f"{EXITCODES_MODULE} — the one package import the ports may make", False),
        )
    return (
        ("the standard library", False),
        (f"its own pillar, {PACKAGE}.{pillar}", False),
        (f"{PORTS_PACKAGE}", False),
    )


# ---------------------------------------------------------------------------
# Walking a tree
# ---------------------------------------------------------------------------


def _module_name(path: pathlib.Path, package_root: pathlib.Path) -> str:
    parts = list(path.relative_to(package_root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join([PACKAGE] + parts)


def scan(package_root: pathlib.Path, layout: Layout) -> list[Violation]:
    """Apply the rule to every `.py` file under `package_root`."""
    found: list[Violation] = []
    paths = sorted(p for p in package_root.rglob("*.py") if "__pycache__" not in p.parts)
    for path in paths:
        module = _module_name(path, package_root)
        try:
            display = str(path.relative_to(REPO_ROOT))
        except ValueError:
            display = str(path)
        found += check_module(
            module,
            path.read_text(encoding="utf-8"),
            layout,
            is_package=path.name == "__init__.py",
            shown_as=display,
        )
    return found


def report(violations: list[Violation], headline: str) -> str:
    return "\n".join([headline, ""] + [v.render() for v in violations] + ["", FIX])


# ---------------------------------------------------------------------------
# The corpus: the rule proved against constructed modules, so that the empty
# MIGRATED tuple above cannot make this file pass by scanning nothing.
# ---------------------------------------------------------------------------

CORPUS_PILLARS = ("eval", "eval_extra", "govern", "orchestrate", "packs", "workbench")

GOVERN_MIGRATED = make_layout(
    pillars=CORPUS_PILLARS,
    migrated=("govern",),
    shell=(f"{PACKAGE}.govern.cli",),
)
EVAL_MIGRATED = make_layout(pillars=CORPUS_PILLARS, migrated=("eval",))
NOTHING_MIGRATED = make_layout(pillars=CORPUS_PILLARS)


@dataclasses.dataclass(frozen=True)
class Case:
    name: str
    module: str
    source: str
    layout: Layout
    kinds: tuple[str, ...]
    why: str
    is_package: bool = False


def _src(text: str) -> str:
    return textwrap.dedent(text).lstrip("\n")


REJECTED: tuple[Case, ...] = (
    Case(
        name="cross_pillar_relative",
        module=f"{PACKAGE}.govern.conformance",
        source=_src(
            """
            from __future__ import annotations

            import json

            from ..workbench.reporting import TaskRecords, read_all_tasks
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(CROSS_PILLAR,),
        why="the import govern actually has today, and the one stage 3 must remove",
    ),
    Case(
        name="cross_pillar_absolute",
        module=f"{PACKAGE}.govern.conformance",
        source=_src(
            """
            from rig_workbench.workbench.reporting import read_all_tasks
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(CROSS_PILLAR,),
        why="spelling it absolutely must not change the answer",
    ),
    Case(
        name="cross_pillar_plain_import",
        module=f"{PACKAGE}.govern.enforce",
        source=_src(
            """
            import rig_workbench.workbench.state
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(CROSS_PILLAR,),
        why="`import a.b.c` is a different AST node from `from a.b import c`",
    ),
    Case(
        name="package_root_module_absolute",
        module=f"{PACKAGE}.govern.identity",
        source=_src(
            """
            from rig_workbench import gitroot
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(PACKAGE_MODULE,),
        why="gitroot is a top-level module, not a pillar — it is what GitRepo replaces",
    ),
    Case(
        name="package_root_module_relative",
        module=f"{PACKAGE}.govern.identity",
        source=_src(
            """
            from .. import gitroot
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(PACKAGE_MODULE,),
        why="the `from rig_workbench import x` form written with dots",
    ),
    Case(
        name="the_package_itself",
        module=f"{PACKAGE}.govern.ledger",
        source=_src(
            """
            import rig_workbench
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(PACKAGE_MODULE,),
        why="importing the package root reaches whatever its __init__ re-exports",
    ),
    Case(
        name="function_local_cross_pillar",
        module=f"{PACKAGE}.govern.policy",
        source=_src(
            """
            def resolve_layer_paths(root):
                from ..workbench.state import load_task
                return load_task(root)
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(CROSS_PILLAR,),
        why="THE decision: moving an import into a function hides a dependency, "
        "it does not remove one (design brief §3)",
    ),
    Case(
        name="method_local_cross_pillar_nested",
        module=f"{PACKAGE}.govern.stage",
        source=_src(
            """
            def outer():
                class Inner:
                    def method(self):
                        import rig_workbench.workbench.state
                        return rig_workbench.workbench.state
                return Inner
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(CROSS_PILLAR,),
        why="three levels of nesting is still a function body",
    ),
    Case(
        name="type_checking_cross_pillar",
        module=f"{PACKAGE}.govern.conformance",
        source=_src(
            """
            from typing import TYPE_CHECKING

            if TYPE_CHECKING:
                from ..workbench.reporting import TaskRecords
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(CROSS_PILLAR,),
        why="free at run time, but it means the pillar states its signatures in "
        "another pillar's vocabulary",
    ),
    Case(
        name="try_guarded_cross_pillar",
        module=f"{PACKAGE}.govern.waiver",
        source=_src(
            """
            try:
                from ..packs.lock import read_lock
            except ImportError:
                read_lock = None
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(CROSS_PILLAR,),
        why="an optional import is still an import; try/except is not a laundry",
    ),
    Case(
        name="third_party",
        module=f"{PACKAGE}.govern.policy",
        source=_src(
            """
            import json

            import yaml
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(THIRD_PARTY,),
        why="not stdlib, not the pillar, not a port — it fails all three allowances",
    ),
    Case(
        name="pillar_name_is_a_prefix_of_another",
        module=f"{PACKAGE}.eval.gate",
        source=_src(
            """
            from rig_workbench.eval_extra import scoring
            """
        ),
        layout=EVAL_MIGRATED,
        kinds=(CROSS_PILLAR,),
        why="a checker matching on `startswith('rig_workbench.eval')` would wave "
        "this through — the pillar is a path segment, not a prefix",
    ),
    Case(
        name="ports_name_is_a_prefix_of_another",
        module=f"{PACKAGE}.govern.ledger",
        source=_src(
            """
            from rig_workbench.portsmith import Presenter
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(PACKAGE_MODULE,),
        why="the same trap on the port allowance",
    ),
    Case(
        name="mixed_statement_one_allowed_one_not",
        module=f"{PACKAGE}.govern.enforce",
        source=_src(
            """
            from rig_workbench import gitroot, ports
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(PACKAGE_MODULE,),
        why="one statement, two names: the allowed one must not cover the other",
    ),
    Case(
        name="two_pillars_in_one_module",
        module=f"{PACKAGE}.govern.conformance",
        source=_src(
            """
            from ..workbench.reporting import read_all_tasks
            from ..orchestrate.runstate import gate_outcome
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(CROSS_PILLAR, CROSS_PILLAR),
        why="each import is reported, not just the first — a contributor fixing one "
        "line at a time needs the whole list",
    ),
    Case(
        name="port_protocol_reaches_a_root_module",
        module=PORTS_PACKAGE,
        source=_src(
            """
            from .. import gitroot
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(PORT_LEAF,),
        why="the ports may not import gitroot; the adapter does that",
        is_package=True,
    ),
    Case(
        name="port_protocol_reaches_a_pillar",
        module=PORTS_PACKAGE,
        source=_src(
            """
            from ..orchestrate import secure_fs
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(PORT_LEAF,),
        why="this is exactly what makes `import ports` drag subprocess in behind it",
        is_package=True,
    ),
    Case(
        name="port_protocol_reaches_its_own_adapter",
        module=PORTS_PACKAGE,
        source=_src(
            """
            from . import local
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(PORT_LEAF,),
        why="the protocols must not know their adapters; the arrow points the other way",
        is_package=True,
    ),
    Case(
        name="port_protocol_function_local",
        module=f"{PORTS_PACKAGE}.contracts",
        source=_src(
            """
            def default_presenter():
                from rig_workbench.workbench import cli
                return cli
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(PORT_LEAF,),
        why="the function-local decision applies to the ports too, and to a port "
        "module that is not __init__.py",
    ),
    Case(
        name="port_protocol_exitcodes_prefix_trap",
        module=PORTS_PACKAGE,
        source=_src(
            """
            from ..exitcodes_extra import RESERVED
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(PORT_LEAF,),
        why="`rig_workbench.exitcodes_extra` is not `rig_workbench.exitcodes`",
        is_package=True,
    ),
    Case(
        name="port_protocol_third_party",
        module=PORTS_PACKAGE,
        source=_src(
            """
            import yaml
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(THIRD_PARTY,),
        why="a leaf with a dependency is not a leaf",
        is_package=True,
    ),
)


ACCEPTED: tuple[Case, ...] = (
    Case(
        name="stdlib_only",
        module=f"{PACKAGE}.govern.approval",
        source=_src(
            """
            from __future__ import annotations

            import dataclasses
            import datetime
            import json
            import pathlib
            from collections.abc import Mapping, Sequence
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(),
        why="allowance 1, including `from __future__` and a dotted stdlib package",
    ),
    Case(
        name="own_pillar_relative",
        module=f"{PACKAGE}.govern.enforce",
        source=_src(
            """
            from . import ledger, waiver
            from .approval import evaluate, load_approvals
            from .policy import EffectivePolicy
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(),
        why="allowance 2, in the three spellings govern already uses",
    ),
    Case(
        name="own_pillar_via_a_climb_that_lands_home",
        module=f"{PACKAGE}.govern.approval",
        source=_src(
            """
            from .. import govern
            from ..govern.policy import EffectivePolicy
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(),
        why="ALMOST a violation: `..` climbs out of the pillar and back into it. A "
        "checker that flagged the dots rather than the destination would fail here",
    ),
    Case(
        name="own_pillar_absolute",
        module=f"{PACKAGE}.govern.rbac",
        source=_src(
            """
            from rig_workbench.govern.policy import PERMISSIONS
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(),
        why="allowance 2 written absolutely",
    ),
    Case(
        name="the_ports",
        module=f"{PACKAGE}.govern.identity",
        source=_src(
            """
            from rig_workbench.ports import Clock, Env, GitRepo
            from ..ports import Presenter
            import rig_workbench.ports
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(),
        why="allowance 3, in all three spellings",
    ),
    Case(
        name="the_default_adapter_instance",
        module=f"{PACKAGE}.govern.ledger",
        source=_src(
            """
            from rig_workbench.ports import Clock
            from rig_workbench.ports.local import SYSTEM_CLOCK


            def append(record, *, clock: Clock = SYSTEM_CLOCK):
                return clock.stamp()
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(),
        why="ALMOST a violation: a submodule of the allowance, not the allowance. "
        "ports/__init__.py's own docstring designs the migration around `*, clock: "
        "Clock = SYSTEM_CLOCK`, so `rig_workbench.ports.*` is inside the allowance "
        "on purpose — narrowing it to the protocols alone would forbid the migration "
        "step the ports were written for",
    ),
    Case(
        name="function_local_import_of_its_own_pillar",
        module=f"{PACKAGE}.govern.policy",
        source=_src(
            """
            def effective_policy(root):
                from .identity import load_org_binding
                return load_org_binding(root)
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(),
        why="ALMOST a violation, and the shape govern/policy.py:523 has today. The "
        "rule is about where an import reaches, not about where it sits — a checker "
        "that rejected function-local imports as such would fail here",
    ),
    Case(
        name="effects_are_not_this_file_s_business",
        module=f"{PACKAGE}.govern.identity",
        source=_src(
            """
            import os
            import subprocess
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(),
        why="ALMOST a violation: stdlib is stdlib. The TID251 block in pyproject.toml "
        "objects to `subprocess.run(...)` and `os.environ`, and the inventory test "
        "counts them; this file would only be repeating them in different words",
    ),
    Case(
        name="type_checking_stdlib_and_ports",
        module=f"{PACKAGE}.govern.waiver",
        source=_src(
            """
            from typing import TYPE_CHECKING

            if TYPE_CHECKING:
                import datetime
                import pathlib

                from ..ports import FileStore
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(),
        why="the shape ports/__init__.py itself has — a guarded import of something "
        "allowed stays allowed",
    ),
    Case(
        name="a_declared_shell_module",
        module=f"{PACKAGE}.govern.cli",
        source=_src(
            """
            import argparse

            from rig_workbench import gitroot
            from ..workbench.reporting import read_all_tasks
            from . import conformance as conf
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(),
        why="the shell wires; that is its job. This is the SHELL_MODULES entry doing "
        "the work — remove govern/cli.py from that table and this case fails",
    ),
    Case(
        name="a_pillar_that_has_not_migrated",
        module=f"{PACKAGE}.workbench.reporting",
        source=_src(
            """
            from rig_workbench import gitroot
            from ..govern import ledger
            import yaml
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(),
        why="the rule is a claim about migrated pillars only; workbench is not one, "
        "and holding it to the rule today would just paint the tree red",
    ),
    Case(
        name="nothing_migrated_at_all",
        module=f"{PACKAGE}.govern.conformance",
        source=_src(
            """
            from ..workbench.reporting import read_all_tasks
            """
        ),
        layout=NOTHING_MIGRATED,
        kinds=(),
        why="the same source as the first rejected case, under today's empty "
        "MIGRATED. This is the vacuity named out loud: it is what the whole corpus "
        "exists to compensate for",
    ),
    Case(
        name="a_root_module_of_the_package",
        module=f"{PACKAGE}.gitroot",
        source=_src(
            """
            from .workbench import state
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(),
        why="rig_workbench/gitroot.py is not in a pillar; the rule has nothing to say "
        "about it until one claims it",
    ),
    Case(
        name="the_adapter_layer",
        module=f"{PORTS_PACKAGE}.local",
        source=_src(
            """
            import subprocess

            from .. import gitroot
            from ..orchestrate import secure_fs
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(),
        why="ALMOST a violation: the exact imports rejected for the protocols, in the "
        "module PORT_ADAPTERS declares. An adapter holds what a protocol may not",
    ),
    Case(
        name="the_port_protocols_and_exitcodes",
        module=PORTS_PACKAGE,
        source=_src(
            """
            from __future__ import annotations

            from typing import TYPE_CHECKING, Protocol

            from ..exitcodes import RESERVED
            from rig_workbench.exitcodes import OK

            if TYPE_CHECKING:
                import subprocess
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(),
        why="the one package import the ports may make, in both spellings",
        is_package=True,
    ),
    Case(
        name="another_pillar_named_only_in_text",
        module=f"{PACKAGE}.govern.conformance",
        source=_src(
            """
            'This module used to call rig_workbench.workbench.reporting.read_all_tasks.'

            # from ..workbench.reporting import read_all_tasks


            def load(root) -> 'rig_workbench.workbench.reporting.TaskRecords':
                return root
            """
        ),
        layout=GOVERN_MIGRATED,
        kinds=(),
        why="ALMOST a violation: a docstring, a commented-out import and a string "
        "annotation all naming another pillar. A grep-based rule would reject all "
        "three; the AST sees no import, and the note in the docstring is how a "
        "migration explains itself",
    ),
)


# ---------------------------------------------------------------------------
# The corpus, run
# ---------------------------------------------------------------------------


def _run(case: Case) -> list[Violation]:
    return check_module(case.module, case.source, case.layout, is_package=case.is_package)


@pytest.mark.parametrize("case", REJECTED, ids=[c.name for c in REJECTED])
def test_a_violating_module_is_rejected(case: Case) -> None:
    """Every way the rule distinguishes a violation, constructed and checked."""
    found = _run(case)
    kinds = tuple(sorted(v.kind for v in found))
    assert kinds == tuple(sorted(case.kinds)), (
        f"The layering rule did not object as expected to `{case.name}`.\n"
        f"Why this case exists: {case.why}\n"
        f"Expected: {sorted(case.kinds)}\nGot: {kinds}\n"
        f"Module {case.module}:\n{textwrap.indent(case.source, '    ')}"
    )
    rendered = "\n".join(v.render() for v in found)
    assert case.module in rendered or found[0].location.startswith(case.module)
    for violation in found:
        assert violation.site.written in rendered or violation.written_short() in rendered
        assert "FAIL" in violation.render(), (
            "The message must say which allowance failed, not only that something did."
        )


@pytest.mark.parametrize("case", ACCEPTED, ids=[c.name for c in ACCEPTED])
def test_a_compliant_module_is_accepted(case: Case) -> None:
    """A checker that rejects everything passes every negative test. These stop it."""
    found = _run(case)
    assert not found, (
        f"The layering rule wrongly rejected `{case.name}`.\n"
        f"Why this case exists: {case.why}\n"
        + report(found, "It reported:")
        + f"\nModule {case.module}:\n{textwrap.indent(case.source, '    ')}"
    )


def test_every_violation_kind_has_a_negative_case() -> None:
    """The guard against a rule that grows a branch nobody ever triggered.

    `MIGRATED` is empty, so the scan over the real tree proves nothing about the rule.
    This does: every kind the checker can emit must be produced by a constructed module
    above. A new kind without a case fails here, in the same commit that adds it.
    """
    exercised = {v.kind for case in REJECTED for v in _run(case)}
    missing = sorted(set(VIOLATION_KINDS) - exercised)
    assert not missing, (
        f"These violation kinds are unreachable from the corpus: {missing}. Add a case "
        "to REJECTED for each. A branch of the rule that no test triggers is a branch "
        "that has never been checked — which is the failure mode this whole file "
        "exists to avoid."
    )
    unexpected = sorted(exercised - set(VIOLATION_KINDS))
    assert not unexpected, f"Kinds emitted but not declared in VIOLATION_KINDS: {unexpected}"


def test_the_corpus_covers_every_place_an_import_can_sit() -> None:
    """Module level, function body and TYPE_CHECKING each reject something."""
    places = {v.site.where for case in REJECTED for v in _run(case)}
    assert places == {AT_MODULE_LEVEL, INSIDE_A_FUNCTION, UNDER_TYPE_CHECKING}, (
        "The decision on function-local imports (they count, exactly as module-level "
        "ones do) is the one that keeps the rule from being evaded by the pattern "
        f"stage 3 exists to remove. Places exercised: {sorted(places)}"
    )


def test_the_failure_message_points_at_the_port_rather_than_at_an_exception() -> None:
    """A contributor's first instinct is to add an exception. The message answers that."""
    case = REJECTED[0]
    text = report(_run(case), "headline")
    for port in PORT_NAMES:
        assert port in text, f"the message should name the ports; {port} is missing"
    assert "SHELL_MODULES" in text
    assert "MIGRATED" in text
    assert "rig_workbench/ports/__init__.py" in text


# ---------------------------------------------------------------------------
# The walk, run over a constructed tree
# ---------------------------------------------------------------------------


def test_the_walk_checks_the_right_files(tmp_path: pathlib.Path) -> None:
    """`scan` must pick the modules the rule reaches, and only those.

    The corpus above exercises the rule one module at a time; this exercises the part
    that decides *which* modules to hand it — a broken walk would make every scan pass
    by reading nothing.
    """
    root = tmp_path / PACKAGE
    (root / "govern").mkdir(parents=True)
    (root / "workbench").mkdir(parents=True)
    (root / "ports").mkdir(parents=True)
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "gitroot.py").write_text("from .workbench import state\n", encoding="utf-8")
    (root / "govern" / "__init__.py").write_text("from .policy import P\n", encoding="utf-8")
    (root / "govern" / "policy.py").write_text(
        "from ..workbench.state import load\n", encoding="utf-8"
    )
    (root / "govern" / "cli.py").write_text("from .. import gitroot\n", encoding="utf-8")
    (root / "workbench" / "__init__.py").write_text("", encoding="utf-8")
    (root / "workbench" / "state.py").write_text("from ..govern import P\n", encoding="utf-8")
    (root / "ports" / "__init__.py").write_text("from .. import gitroot\n", encoding="utf-8")
    (root / "ports" / "local.py").write_text("from .. import gitroot\n", encoding="utf-8")
    pycache = root / "govern" / "__pycache__"
    pycache.mkdir()
    (pycache / "policy.py").write_text("from ..workbench.state import load\n", encoding="utf-8")

    layout = make_layout(
        pillars=("govern", "workbench"),
        migrated=("govern",),
        shell=(f"{PACKAGE}.govern.cli",),
        port_adapters=(f"{PORTS_PACKAGE}.local",),
    )
    found = scan(root, layout)
    assert sorted((v.module, v.kind) for v in found) == [
        (f"{PACKAGE}.govern.policy", CROSS_PILLAR),
        (f"{PORTS_PACKAGE}", PORT_LEAF),
    ], report(found, "The walk selected the wrong set of modules:")
    assert all(":1" in v.location for v in found)


# ---------------------------------------------------------------------------
# The rule, applied to rig_workbench/ as it is
# ---------------------------------------------------------------------------


def test_a_migrated_pillar_imports_nothing_but_the_ports() -> None:
    """The contract itself.

    Vacuous while `MIGRATED` is empty — stage 3 is 未着手 and no pillar has moved yet.
    That vacuity is the reason for the corpus above; `test_every_violation_kind_has_a_
    negative_case` is what keeps this file honest until a pillar arrives.
    """
    layout = real_layout()
    if not layout.migrated:
        pytest.skip(
            "No pillar has migrated yet (MIGRATED is empty, design brief §7 stage 3). "
            "The rule itself is proved against the constructed corpus in this file."
        )
    found = [v for v in scan(PACKAGE_ROOT, layout) if not _is_port_module(v.module)]
    if found:
        pytest.fail(
            report(
                found,
                f"{len(found)} import(s) reach outside the three allowances in a "
                f"migrated pillar ({', '.join(sorted(layout.migrated))}).",
            )
        )


def test_the_ports_import_nothing_from_the_package_but_exitcodes() -> None:
    """Not vacuous: `rig_workbench/ports/` exists today and this runs against it.

    If the protocols reach back into the package, the sentence 'a judgement module
    imports the ports and nothing else that touches the outside' is false from the first
    day, because the outside comes in behind the port.
    """
    found = [v for v in scan(PACKAGE_ROOT, real_layout()) if _is_port_module(v.module)]
    if found:
        pytest.fail(
            report(
                found,
                "The port layer reaches back into rig_workbench. Protocols may import "
                "the standard library and rig_workbench.exitcodes; everything else "
                "belongs in an adapter (declare it in PORT_ADAPTERS).",
            )
        )


def test_the_scan_reads_real_files_and_finds_real_violations() -> None:
    """The one thing the corpus cannot prove: that the walk reads rig_workbench/.

    Applied to every pillar as if it had migrated, the rule must object to something —
    the tree is 未着手, `govern/conformance.py` alone reaches into `workbench`. A scan
    that found nothing here would mean the walk read no files, and every check above it
    would be passing on air. When the last pillar migrates this test starts failing, and
    deleting it is the correct response: it will have run out of work.
    """
    layout = real_layout()
    everything = dataclasses.replace(layout, migrated=layout.pillars, shell=frozenset())
    found = scan(PACKAGE_ROOT, everything)
    assert found, (
        "Scanning rig_workbench/ with every pillar treated as migrated produced no "
        "findings at all. Before celebrating, check that _module_name and scan() are "
        "still reading the package: 181 files that all obey a rule nobody has started "
        "applying is the less likely explanation."
    )


# ---------------------------------------------------------------------------
# The declarations, checked against the tree so they cannot rot
# ---------------------------------------------------------------------------


def test_migrated_names_real_pillars() -> None:
    layout = real_layout()
    unknown = sorted(set(MIGRATED) - layout.pillars)
    assert not unknown, (
        f"MIGRATED names {unknown}, which are not sub-packages of rig_workbench/. A "
        "typo here would silently switch the contract off for that pillar."
    )
    assert len(set(MIGRATED)) == len(MIGRATED), "MIGRATED lists a pillar twice"


def test_shell_declarations_name_real_modules() -> None:
    layout = real_layout()
    problems: list[str] = []
    for pillar, entries in SHELL_MODULES.items():
        if pillar not in layout.pillars:
            problems.append(f"SHELL_MODULES declares pillar '{pillar}', which is not on disk")
        for module, reason in entries.items():
            if not module.startswith(f"{PACKAGE}.{pillar}."):
                problems.append(f"{module} is declared under the wrong pillar ('{pillar}')")
            relative = module[len(PACKAGE) + 1:].replace(".", "/")
            path = PACKAGE_ROOT / f"{relative}.py"
            if not path.is_file() and not (PACKAGE_ROOT / relative / "__init__.py").is_file():
                problems.append(f"{module} is declared a shell but no such module exists")
            if len(reason.split()) < 8:
                problems.append(f"{module}'s shell reason is too short to be a reason")
    assert not problems, "\n".join(
        ["The shell declarations no longer match the tree:"] + [f"    {p}" for p in problems]
    )


def test_port_adapter_declarations_name_real_modules() -> None:
    problems: list[str] = []
    for module, reason in PORT_ADAPTERS.items():
        if not module.startswith(PORTS_PACKAGE + "."):
            problems.append(f"{module} is not under {PORTS_PACKAGE}")
            continue
        relative = module[len(PACKAGE) + 1:].replace(".", "/")
        if not (PACKAGE_ROOT / f"{relative}.py").is_file():
            if not (PACKAGE_ROOT / relative / "__init__.py").is_file():
                problems.append(f"{module} is declared an adapter but no such module exists")
        if len(reason.split()) < 8:
            problems.append(f"{module}'s adapter reason is too short to be a reason")
    assert not problems, "\n".join(
        ["The adapter declarations no longer match the tree:"] + [f"    {p}" for p in problems]
    )
