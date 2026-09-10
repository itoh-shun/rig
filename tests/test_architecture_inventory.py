"""The structural ratchet for the V3 rearchitecture (design brief §3, §7 stage 3).

Stage 3 moves `rig_workbench/` behind six ports one pillar at a time. That is far
more work than one session, and a change that large has no way to tell progress
from drift unless the structural numbers are pinned first. This file pins them.

What it freezes, and how:

* **Import cycles** — as an *exact set of member tuples*, not a count, so a cycle
  that disappears and a different one that appears cannot cancel out. Runtime
  cycles and `if TYPE_CHECKING:`-only cycles are frozen separately: the second
  kind costs nothing at runtime and breaking it is not the same event.
* **The hub and the god module** — inbound edges into `workbench/state.py`,
  outbound edges out of `workbench/cli.py`, as ceilings.
* **Effect sites per package** — `print(`, `subprocess.*(...)`, `open(..., 'w'/'a')`,
  `write_text`/`write_bytes`, `os.environ`/`os.getenv`, and direct clock reads.
  Counted over the AST, not over lines, so a docstring that says "print" and a
  comment about `subprocess` do not inflate the baseline the way an earlier
  line-matching pass did (see the brief's "測定の限界").

Every number below is a **baseline to be lowered, not a target**. The effect
counts and the two edge counts are ceilings (`<=`): they may only come down, and
lowering one means editing the literal down in the same commit. The cycle sets
are exact: a new cycle fails even if an old one vanished in the same change.

The measurement is a read-only AST walk over the package — no imports of the
code under test, no subprocess, safe under `pytest -n auto`.
"""

from __future__ import annotations

import ast
import functools
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "rig_workbench"
PACKAGE_NAME = "rig_workbench"

# The bucket name used for modules that sit directly in rig_workbench/ rather
# than in one of its sub-packages.
ROOT_BUCKET = "rig_workbench/"

# The port/adapter layer is where effects are *supposed* to end up, so it is not
# under the effect ratchet. Counting it would fight the migration it exists to
# encourage: moving a print() out of workbench/ and into the Presenter adapter
# lowers workbench's count and raises this one, and a ceiling here would block
# exactly that move. Cycles through it are still measured.
PORT_LAYER_PACKAGE = "ports"


# ---------------------------------------------------------------------------
# Frozen baseline — measured at 9d038b7 by the walk below.
# These are ceilings and exact sets to ratchet DOWN, never a goal to fill up to.
# ---------------------------------------------------------------------------

# Strongly connected components of the intra-package import graph that exist at
# run time: at least one edge in each direction is executed on import or on call
# (module-level or function-local — a function-local import hides a cycle, it
# does not remove it, which is exactly the brief's point in §3).
#
# Six components, not the seven the brief claims. The seventh
# (bench <-> bench_score) is `if TYPE_CHECKING:`-only and is frozen separately.
BASELINE_RUNTIME_CYCLES: frozenset[tuple[str, ...]] = frozenset(
    {
        ("rig_workbench.cli", "rig_workbench.githooks"),
        (
            "rig_workbench.eval.affected",
            "rig_workbench.eval.gate",
            "rig_workbench.orchestrate.graph",
            "rig_workbench.orchestrate.recipes",
            "rig_workbench.packs.catalog",
            "rig_workbench.packs.installer",
            "rig_workbench.packs.lock",
            "rig_workbench.packs.publisher",
            "rig_workbench.packs.resolver",
            "rig_workbench.packs.sources",
            "rig_workbench.packs.tester",
            "rig_workbench.packs.validation",
        ),
        ("rig_workbench.orchestrate.providers", "rig_workbench.orchestrate.runstate"),
        (
            "rig_workbench.workbench.assurance",
            "rig_workbench.workbench.assurance_target",
            "rig_workbench.workbench.assurance_wiring",
            "rig_workbench.workbench.intent_wiring",
        ),
        ("rig_workbench.workbench.check_synthesis", "rig_workbench.workbench.instincts"),
        (
            "rig_workbench.workbench.knowledge_candidate",
            "rig_workbench.workbench.org_knowledge",
        ),
    }
)

# Components that exist only once `if TYPE_CHECKING:` imports are counted. These
# cost nothing at run time; they are frozen so that turning a runtime cycle into
# a type-only one reads as the improvement it is, instead of vanishing silently.
BASELINE_TYPE_ONLY_CYCLES: frozenset[tuple[str, ...]] = frozenset(
    {
        ("rig_workbench.bench", "rig_workbench.bench_score"),
    }
)

# The hub: distinct modules that import workbench/state.py. Ceiling, lower it.
BASELINE_STATE_INBOUND = 37

# The god module: distinct modules workbench/cli.py imports. Ceiling, lower it.
# Note this is *distinct modules*, not import statements — see
# `test_the_god_module_count_is_distinct_modules_not_import_statements`.
BASELINE_CLI_OUTBOUND = 39

# Effect sites per package, by kind. Every one of these is a call site the six
# ports are meant to absorb, so every one of them is a number that goes down as
# stage 3 lands. Ceilings, per package and per kind.
BASELINE_EFFECT_SITES: dict[str, dict[str, int]] = {
    # package        print  subprocess  open(w/a)  write_text  os.environ  clock
    ROOT_BUCKET: {
        "print": 197,
        "subprocess": 34,
        "open_write": 7,
        "write_text": 10,
        "env": 26,
        "clock": 11,
    },
    "eval": {
        "print": 16,
        "subprocess": 24,
        "open_write": 0,
        "write_text": 1,
        "env": 3,
        "clock": 4,
    },
    # The first pillar behind the ports (§7 stage 3). Five kinds are zero because every
    # site moved onto a port, and the sixth is not what it looks like: four of the six
    # `write_text` are `govern/cli.py`'s own `pathlib` writes — the shell writing the files
    # `init`, `migrate` and `--out` create — and the other two are `files.write_text(...)`,
    # port calls this walk counts by attribute name because it never resolves a receiver.
    # Lowering it to 2 would therefore be a claim about the shell nobody has made yet.
    "govern": {
        "print": 0,
        "subprocess": 0,
        "open_write": 0,
        "write_text": 6,
        "env": 0,
        "clock": 0,
    },
    "orchestrate": {
        "print": 212,
        "subprocess": 21,
        "open_write": 4,
        "write_text": 10,
        "env": 19,
        "clock": 4,
    },
    "packs": {
        "print": 54,
        "subprocess": 5,
        "open_write": 0,
        "write_text": 9,
        "env": 8,
        "clock": 5,
    },
    # The capability registry declares; it does not act. Zero is the shape every
    # judgement layer carved out in stage 3 is supposed to end up with.
    "registry": {
        "print": 0,
        "subprocess": 0,
        "open_write": 0,
        "write_text": 0,
        "env": 0,
        "clock": 0,
    },
    "validation": {
        "print": 13,
        "subprocess": 1,
        "open_write": 0,
        "write_text": 4,
        "env": 1,
        "clock": 0,
    },
    "workbench": {
        "print": 513,
        "subprocess": 11,
        "open_write": 5,
        "write_text": 13,
        "env": 8,
        "clock": 12,
    },
}

EFFECT_KINDS = ("print", "subprocess", "open_write", "write_text", "env", "clock")

EFFECT_KIND_LABELS = {
    "print": "print(...)",
    "subprocess": "subprocess.*(...)",
    "open_write": "open(..., 'w'/'a')",
    "write_text": "write_text/write_bytes",
    "env": "os.environ / os.getenv",
    "clock": "datetime.now / datetime.utcnow / time.time",
}

EFFECT_KIND_PORTS = {
    "print": "Presenter",
    "subprocess": "ProcessRunner",
    "open_write": "FileStore",
    "write_text": "FileStore",
    "env": "Env",
    "clock": "Clock",
}

RATCHET_RULE = (
    "The architecture ratchet only moves down. If you removed effect sites, edit "
    "BASELINE_EFFECT_SITES in this file down to the new number in the same commit. "
    "If you added them, put them behind the port instead."
)


# ---------------------------------------------------------------------------
# Measurement — a read-only AST walk. No imports of the code under test.
# ---------------------------------------------------------------------------


def _module_name(path: pathlib.Path) -> str:
    parts = list(path.relative_to(PACKAGE_ROOT).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join([PACKAGE_NAME] + parts)


def _package_bucket(path: pathlib.Path) -> str:
    parts = path.relative_to(PACKAGE_ROOT).parts
    return parts[0] if len(parts) > 1 else ROOT_BUCKET


def _source_files() -> list[pathlib.Path]:
    return sorted(p for p in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _is_type_checking_test(test: ast.expr) -> bool:
    """True for `if TYPE_CHECKING:` and `if typing.TYPE_CHECKING:`."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


class _ImportCollector(ast.NodeVisitor):
    """Collects intra-package import edges, tagged by how they are reached.

    Tags: "module" (executed on import), "local" (inside a function body — the
    cycle is hidden, not gone), "type" (inside `if TYPE_CHECKING:` — never
    executed).
    """

    def __init__(self, module: str, is_package: bool, known: frozenset[str]) -> None:
        self.module = module
        self.known = known
        self.base_package = module if is_package else module.rpartition(".")[0]
        self.function_depth = 0
        self.type_checking_depth = 0
        self.edges: dict[tuple[str, str], set[str]] = {}

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    def visit_If(self, node: ast.If) -> None:
        if not _is_type_checking_test(node.test):
            self.generic_visit(node)
            return
        self.type_checking_depth += 1
        for statement in node.body:
            self.visit(statement)
        self.type_checking_depth -= 1
        for statement in node.orelse:
            self.visit(statement)

    def _tag(self) -> str:
        if self.type_checking_depth:
            return "type"
        return "local" if self.function_depth else "module"

    def _record(self, dotted: str) -> None:
        target = self._nearest_module(dotted)
        if target is None or target == self.module:
            return
        self.edges.setdefault((self.module, target), set()).add(self._tag())

    def _nearest_module(self, dotted: str) -> str | None:
        """`from pkg import name` may name a module or an attribute of `pkg`."""
        while dotted:
            if dotted in self.known:
                return dotted
            if "." not in dotted:
                return None
            dotted = dotted.rpartition(".")[0]
        return None

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == PACKAGE_NAME or alias.name.startswith(PACKAGE_NAME + "."):
                self._record(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level:
            parts = self.base_package.split(".")
            climb = node.level - 1
            if climb:
                parts = parts[:-climb] if climb < len(parts) else []
            base = ".".join(parts)
            if not base:
                return
            prefix = f"{base}.{node.module}" if node.module else base
        else:
            module = node.module or ""
            if not (module == PACKAGE_NAME or module.startswith(PACKAGE_NAME + ".")):
                return
            prefix = module
        for alias in node.names:
            self._record(f"{prefix}.{alias.name}")


def _strongly_connected_components(graph: dict[str, set[str]]) -> set[tuple[str, ...]]:
    """Tarjan, iterative (the package is deep enough to blow a recursive one)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: set[tuple[str, ...]] = set()
    counter = 0

    for root in sorted(graph):
        if root in index:
            continue
        work: list[tuple[str, list[str], int]] = [(root, sorted(graph.get(root, ())), 0)]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, successors, position = work[-1]
            if position < len(successors):
                work[-1] = (node, successors, position + 1)
                successor = successors[position]
                if successor not in index:
                    index[successor] = low[successor] = counter
                    counter += 1
                    stack.append(successor)
                    on_stack.add(successor)
                    work.append((successor, sorted(graph.get(successor, ())), 0))
                elif successor in on_stack:
                    low[node] = min(low[node], index[successor])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                member: list[str] = []
                while True:
                    popped = stack.pop()
                    on_stack.discard(popped)
                    member.append(popped)
                    if popped == node:
                        break
                if len(member) > 1:
                    components.add(tuple(sorted(member)))
    return components


class _EffectCollector(ast.NodeVisitor):
    """Counts effect call sites over the AST, so prose cannot inflate them."""

    def __init__(self) -> None:
        self.counts = dict.fromkeys(EFFECT_KINDS, 0)

    @staticmethod
    def _literal_mode(call: ast.Call, *, method: bool) -> str | None:
        position = 0 if method else 1
        if len(call.args) > position:
            argument = call.args[position]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                return argument.value
        for keyword in call.keywords:
            if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    return keyword.value.value
        return None

    def _count_open(self, call: ast.Call, *, method: bool) -> None:
        mode = self._literal_mode(call, method=method)
        if mode and mode[0] in ("w", "a"):
            self.counts["open_write"] += 1

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            if func.id == "print":
                self.counts["print"] += 1
            elif func.id == "open":
                self._count_open(node, method=False)
        elif isinstance(func, ast.Attribute):
            value = func.value
            if isinstance(value, ast.Name) and value.id == "subprocess":
                # A call on the module: run / Popen / check_output / call. Bare
                # references such as subprocess.PIPE are not effect sites.
                self.counts["subprocess"] += 1
            elif func.attr == "open":
                self._count_open(node, method=True)
            elif func.attr in ("write_text", "write_bytes"):
                self.counts["write_text"] += 1
            elif func.attr in ("now", "utcnow") and _names_datetime(value):
                self.counts["clock"] += 1
            elif func.attr == "time" and isinstance(value, ast.Name) and value.id == "time":
                self.counts["clock"] += 1
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        value = node.value
        if isinstance(value, ast.Name) and value.id == "os":
            if node.attr in ("environ", "getenv"):
                self.counts["env"] += 1
        self.generic_visit(node)


def _names_datetime(value: ast.expr) -> bool:
    """`datetime.now(...)` or `dt.datetime.now(...)`."""
    if isinstance(value, ast.Name):
        return value.id == "datetime"
    if isinstance(value, ast.Attribute):
        return value.attr == "datetime"
    return False


class _Inventory:
    def __init__(
        self,
        edges: dict[tuple[str, str], frozenset[str]],
        effects: dict[str, dict[str, int]],
        cli_import_statements: int,
    ) -> None:
        self.edges = edges
        self.effects = effects
        self.cli_import_statements = cli_import_statements

    def _graph(self, tags: frozenset[str]) -> dict[str, set[str]]:
        graph: dict[str, set[str]] = {}
        for (source, target), edge_tags in self.edges.items():
            if edge_tags & tags:
                graph.setdefault(source, set()).add(target)
                graph.setdefault(target, set())
        return graph

    def runtime_cycles(self) -> set[tuple[str, ...]]:
        return _strongly_connected_components(self._graph(frozenset({"module", "local"})))

    def type_only_cycles(self) -> set[tuple[str, ...]]:
        every = _strongly_connected_components(
            self._graph(frozenset({"module", "local", "type"}))
        )
        return every - self.runtime_cycles()

    def inbound(self, module: str) -> set[str]:
        return {source for (source, target) in self.edges if target == module}

    def outbound(self, module: str) -> set[str]:
        return {target for (source, target) in self.edges if source == module}


@functools.lru_cache(maxsize=1)
def inventory() -> _Inventory:
    """Walk rig_workbench/ once per process; every test reads this result."""
    paths = _source_files()
    modules = {_module_name(path): path for path in paths}
    known = frozenset(modules)

    edges: dict[tuple[str, str], set[str]] = {}
    effects: dict[str, dict[str, int]] = {}
    cli_import_statements = 0

    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module = _module_name(path)

        collector = _ImportCollector(module, path.name == "__init__.py", known)
        collector.visit(tree)
        for edge, tags in collector.edges.items():
            edges.setdefault(edge, set()).update(tags)

        package = _package_bucket(path)
        if package != PORT_LAYER_PACKAGE:
            effect_collector = _EffectCollector()
            effect_collector.visit(tree)
            bucket = effects.setdefault(package, dict.fromkeys(EFFECT_KINDS, 0))
            for kind, count in effect_collector.counts.items():
                bucket[kind] += count

        if module == f"{PACKAGE_NAME}.workbench.cli":
            cli_import_statements = _count_intra_package_import_statements(tree)

    return _Inventory(
        {edge: frozenset(tags) for edge, tags in edges.items()},
        effects,
        cli_import_statements,
    )


def _count_intra_package_import_statements(tree: ast.Module) -> int:
    total = 0
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").startswith(PACKAGE_NAME):
                total += 1
        elif isinstance(node, ast.Import):
            if any(alias.name.startswith(PACKAGE_NAME) for alias in node.names):
                total += 1
    return total


def _render_cycle(members: tuple[str, ...], *, show: int = 6) -> str:
    """One cycle on one line. Long ones are elided — a 90-module component is a
    fact about the graph, not something anyone reads module by module."""
    if len(members) <= show:
        return " <-> ".join(members)
    head = " <-> ".join(members[:show])
    return f"({len(members)} modules) {head} <-> ... and {len(members) - show} more"


def _cycle_delta_report(
    measured: set[tuple[str, ...]],
    baseline: frozenset[tuple[str, ...]],
    *,
    label: str,
    appeared_note: str,
    baseline_name: str,
) -> list[str]:
    """The whole point of this file: say which cycle appeared and which went,
    separately, because those are different events."""
    appeared = sorted(measured - baseline)
    disappeared = sorted(baseline - measured)
    if not appeared and not disappeared:
        return []

    # A single new edge can merge several frozen components into one giant SCC.
    # Reporting those frozen components as "gone" is technically true and
    # completely misleading, so absorption is named as such.
    absorbed: dict[tuple[str, ...], tuple[str, ...]] = {}
    for old in disappeared:
        for new in appeared:
            if set(old) < set(new):
                absorbed[old] = new
                break

    lines: list[str] = []
    if appeared:
        lines.append(f"A NEW {label} import cycle appeared ({len(appeared)}). {appeared_note}")
        for cycle in appeared:
            lines.append(f"    + {_render_cycle(cycle)}")
            swallowed = [old for old, new in absorbed.items() if new == cycle]
            for old in swallowed:
                lines.append(f"        absorbs the frozen cycle: {_render_cycle(old)}")
    still_gone = [old for old in disappeared if old not in absorbed]
    if still_gone:
        lines.append(
            f"A frozen {label} import cycle is GONE ({len(still_gone)}). That is the "
            "goal, and it is a separate event from anything above: delete these "
            f"entries from {baseline_name} in this file, in the same commit, so the "
            "ratchet holds at the new position."
        )
        lines += [f"    - {_render_cycle(cycle)}" for cycle in still_gone]
    return lines


# ---------------------------------------------------------------------------
# The ratchet
# ---------------------------------------------------------------------------


def test_the_runtime_import_cycles_are_exactly_the_frozen_set() -> None:
    """Exact set, not a count: a new cycle must fail even if an old one went."""
    lines = _cycle_delta_report(
        inventory().runtime_cycles(),
        BASELINE_RUNTIME_CYCLES,
        label="runtime",
        appeared_note=(
            "This is a regression: the V3 rearchitecture (design brief §3) exists to "
            "remove cycles by inverting dependencies, and a function-local import "
            "hides a cycle rather than breaking it."
        ),
        baseline_name="BASELINE_RUNTIME_CYCLES",
    )
    if not lines:
        return
    lines.append(
        "The cycle set is frozen as a set of member tuples, not as a count, so a "
        "cycle that vanishes cannot pay for one that appears."
    )
    pytest.fail("\n".join(lines))


def test_the_type_checking_only_import_cycles_are_exactly_the_frozen_set() -> None:
    """Cycles that exist only under `if TYPE_CHECKING:` — free at run time."""
    lines = _cycle_delta_report(
        inventory().type_only_cycles(),
        BASELINE_TYPE_ONLY_CYCLES,
        label="type-checking-only",
        appeared_note=(
            "It costs nothing at run time, but it is still a cycle in the design, and "
            "the ratchet records it separately from the runtime ones. If one of these "
            "used to be a RUNTIME cycle, that is progress: move it from "
            "BASELINE_RUNTIME_CYCLES to BASELINE_TYPE_ONLY_CYCLES."
        ),
        baseline_name="BASELINE_TYPE_ONLY_CYCLES",
    )
    if not lines:
        return
    pytest.fail("\n".join(lines))


def test_the_hub_module_is_imported_by_no_more_modules_than_the_baseline() -> None:
    """workbench/state.py is the hub the brief's §3 table names. Ceiling."""
    importers = inventory().inbound(f"{PACKAGE_NAME}.workbench.state")
    measured = len(importers)
    if measured <= BASELINE_STATE_INBOUND:
        return
    added = sorted(importers)
    pytest.fail(
        "The hub grew. rig_workbench/workbench/state.py was imported by "
        f"{BASELINE_STATE_INBOUND} modules, it is now imported by {measured} "
        f"(+{measured - BASELINE_STATE_INBOUND}). The architecture ratchet only "
        "moves down: state.py is the interface-segregation symptom stage 3 is "
        "meant to shrink, so a new importer needs a narrower seam instead. If you "
        "removed importers, edit BASELINE_STATE_INBOUND down in this file.\n"
        "Current importers:\n" + "\n".join(f"    {name}" for name in added)
    )


def test_the_god_module_imports_no_more_modules_than_the_baseline() -> None:
    """workbench/cli.py is the god module. Ceiling on its fan-out."""
    imported = inventory().outbound(f"{PACKAGE_NAME}.workbench.cli")
    measured = len(imported)
    if measured <= BASELINE_CLI_OUTBOUND:
        return
    pytest.fail(
        "The god module grew. rig_workbench/workbench/cli.py imported "
        f"{BASELINE_CLI_OUTBOUND} modules of its own package, it now imports "
        f"{measured} (+{measured - BASELINE_CLI_OUTBOUND}). The architecture "
        "ratchet only moves down: a new sub-command belongs behind the capability "
        "registry, not on another import line here. If you removed imports, edit "
        "BASELINE_CLI_OUTBOUND down in this file.\n"
        "Currently imported:\n" + "\n".join(f"    {name}" for name in sorted(imported))
    )


def test_the_god_module_count_is_distinct_modules_not_import_statements() -> None:
    """Settles 39 vs 40.

    The brief says workbench/cli.py imports 39 modules; a later pass measured 40.
    Both readings are of the same file: there are 40 module-level import
    statements naming this package, and they reach 39 distinct modules, because
    `from .route_cli import ...` appears twice. The ratchet counts modules — the
    coupling is to the module, not to the line — so 39 is the number that is
    frozen, and this test keeps the discrepancy from being rediscovered.
    """
    measured = inventory().cli_import_statements
    assert measured == BASELINE_CLI_OUTBOUND + 1, (
        f"workbench/cli.py now has {measured} intra-package import statements "
        f"reaching {len(inventory().outbound(f'{PACKAGE_NAME}.workbench.cli'))} "
        "distinct modules. The two numbers used to differ by exactly one "
        "(`from .route_cli import ...` written twice). Re-derive both before "
        "editing BASELINE_CLI_OUTBOUND."
    )


@pytest.mark.parametrize("package", sorted(BASELINE_EFFECT_SITES))
def test_effect_sites_in_a_package_never_rise(package: str) -> None:
    """Per package, per kind: the count of unported effect sites is a ceiling."""
    measured = inventory().effects.get(package)
    assert measured is not None, (
        f"Package '{package}' has a frozen effect budget in this file but no "
        "source files under rig_workbench/. If it was renamed or removed, update "
        "BASELINE_EFFECT_SITES to match."
    )

    exceeded = [
        (kind, BASELINE_EFFECT_SITES[package][kind], measured[kind])
        for kind in EFFECT_KINDS
        if measured[kind] > BASELINE_EFFECT_SITES[package][kind]
    ]
    if not exceeded:
        return

    lines = [
        f"Effect sites rose in package '{package}'. Each of these is a call the "
        "six ports (design brief §3) are meant to absorb:"
    ]
    for kind, was, now in exceeded:
        lines.append(
            f"    {EFFECT_KIND_LABELS[kind]}: was {was}, now {now} "
            f"(+{now - was}) — belongs behind the {EFFECT_KIND_PORTS[kind]} port"
        )
    lines.append(RATCHET_RULE)
    pytest.fail("\n".join(lines))


def test_no_package_escapes_the_effect_budget() -> None:
    """A new sub-package must be added to the baseline, not slip past it."""
    measured = set(inventory().effects)
    unfrozen = sorted(measured - set(BASELINE_EFFECT_SITES))
    assert not unfrozen, (
        "These packages under rig_workbench/ have no frozen effect budget: "
        f"{', '.join(unfrozen)}. Measure them and add them to "
        "BASELINE_EFFECT_SITES, so the ratchet covers the whole package rather "
        "than the part of it that existed when this file was written. The one "
        f"deliberate exemption is '{PORT_LAYER_PACKAGE}/', the adapter layer the "
        "effects are being moved into."
    )
