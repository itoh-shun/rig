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
  `write_text`/`write_bytes`, `os.environ`/`os.getenv`, and direct clock reads
  (`CLOCK_READS` below enumerates those and says how the set was derived; the two
  packages whose number went up when it widened carry a note saying so at their entry).
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
#
# One exception to "down", and it is the only one: the `clock` column was
# **re-measured** when `CLOCK_READS` widened, not raised. The narrow walk it
# replaced matched three spellings (`now`, `utcnow`, `time.time`) out of the
# thirteen a file can use to read the wall clock, so three packages were sitting
# under a ceiling that described less code than they contained — `govern`'s read
# 0 with `datetime.date.today()` still in `cli.py`. A number that goes up here
# because the instrument got better is the ratchet starting to work, not a
# regression; the two entries it happened to say so where they sit. Anything that
# goes up for any *other* reason is a new effect site, and belongs behind a port.
# ---------------------------------------------------------------------------

# Strongly connected components of the intra-package import graph that exist at
# run time: at least one edge in each direction is executed on import or on call
# (module-level or function-local — a function-local import hides a cycle, it
# does not remove it, which is exactly the brief's point in §3).
#
# Started at six components, not the seven the brief claims; the seventh
# (bench <-> bench_score) is `if TYPE_CHECKING:`-only and is frozen separately.
#
# Stage 3 pillar 2 then split the twelve-module component. It was held closed by
# three function-local imports inside `rig_workbench/packs/`, and cutting the
# first of them — `lock -> publisher`, inverted into `lock.PublisherVerifier` —
# dropped `eval.affected`, `eval.gate`, `orchestrate.graph`, `orchestrate.recipes`,
# `packs.sources` and `packs.tester` out of any cycle at all, leaving
# {catalog, lock, resolver, validation} and {installer, publisher} in its place.
# The second — `publisher -> installer`, inverted into
# `publisher.LocalQualityStatus` — removed {installer, publisher} outright.
# The third — `validation -> resolver`, inverted into
# `validation.CoreReferenceIds` — removed {catalog, lock, resolver, validation},
# and it had to be that edge: it is in every minimum feedback edge set for that
# component, because `validation` is its only exit.
#
# Two of those three inversions have since outlived what they inverted: rig V3
# removed publisher signing, so `packs/publisher.py` and `packs/signature.py` are
# gone and with them `lock.PublisherVerifier` and `publisher.LocalQualityStatus`.
# The edges they inverted cannot be redrawn by anyone, because neither endpoint
# exists; `installer.local_quality_status` absorbed the verdict `sign_pack` used
# to ask for. Only the third inversion is still load-bearing, and
# `validation.CoreReferenceIds` is still what holds `validation -> resolver` open.
# The paragraph above is kept because it is the record of how the component came
# apart, not a description of today's imports.
#
# Five components now, and `rig_workbench/packs/` is in none of them — measured
# after the deletions, not assumed from them. Nothing below may be re-added
# without the change that adds it saying so here.
BASELINE_RUNTIME_CYCLES: frozenset[tuple[str, ...]] = frozenset(
    {
        ("rig_workbench.cli", "rig_workbench.githooks"),
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
    # The second pillar behind the ports (§7 stage 3). Five kinds are zero because every
    # site moved onto one: 16 `print` to the `Presenter` the shell builds, 24
    # `subprocess.run` to `ProcessRunner` (three of them in bytes mode, one naming
    # `errors="surrogateescape"`, which is why the port grew the parameter), 3 `os.environ`
    # to `Env` and 4 wall-clock reads to `Clock` — each rendered `.astimezone(utc)` at the
    # call, because eval writes UTC into records the gate parses and the port reads the
    # moment through the local offset.
    #
    # The sixth is **not** the counting artefact `govern`'s 6 are. There, four are the
    # shell's own `pathlib` writes and two are `files.write_text(...)` port calls this walk
    # counts by attribute name because it never resolves a receiver. Here the one site is
    # `affected.py`'s `target.write_bytes(...)`, a real `pathlib` write of a blob into the
    # `TemporaryDirectory` `_graph_at` reads a revision through — counted under the
    # `write_text` kind because the walk buckets `write_bytes` with it. `FileStore` has no
    # `write_bytes`, and growing one for a single caller writing scratch files it then
    # deletes would be a port method written from a name rather than from a call site,
    # which is the thing `ports/__init__.py` says it will not do. So 1 is the floor until
    # that write has a reason to be a port call.
    "eval": {
        "print": 0,
        "subprocess": 0,
        "open_write": 0,
        "write_text": 1,
        "env": 0,
        "clock": 0,
    },
    # The first pillar behind the ports (§7 stage 3). Five kinds are zero because every
    # site moved onto a port — `clock` last, and it is the only one of the five whose zero
    # was ever false: the narrow walk matched `now`/`utcnow`/`time.time` and not
    # `datetime.date.today()`, so `govern/cli.py`'s expiry calculation sat under a ceiling
    # that read 0 while the call was still there. The walk below now covers the whole set
    # (`CLOCK_READS`), and the shell takes a `Clock` like it takes a `Presenter`.
    # The sixth is not what it looks like: four of the six
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
    # `clock` re-measured, not raised: 4 -> 6. The two new sites are `time.time_ns()` in
    # `commands.py` and `providers.py`, which the narrow walk never matched because it
    # looked for the attribute `time` and nothing else on the module. Neither is new code.
    #
    # The fifth pillar, and it is moving in passes rather than in one go: `print` comes
    # down as each file's *command* functions take the `Presenter` the shell builds, and
    # the other five kinds are pass 2. 212 -> 96 is `commands.py`'s fifteen `cmd_*`, whose
    # 116 sites became `out.out(...)` — 115 of them one for one, and the sixteenth the
    # `diagnostic()` closure in `cmd_run`, which chose its stream with
    # `file=sys.stderr if artifact_stdout else sys.stdout` and now chooses between
    # `out.err` and `out.out`. That last one is also why this number and ruff's disagree:
    # T201 does not count a `print` whose `file=` is a conditional expression, so the
    # ledger line in `pyproject.toml` said 211 where this said 212.
    #
    # 96 -> 63 is `queueing.py`: `cmd_queue` and the `_cmd_queue_dispatch` it hands its
    # argv to, which between them are the whole of the `queue` verb. The dispatcher takes
    # the port and `cmd_queue` forwards it, so the two halves of one command cannot end up
    # holding different presenters; `_run_one`'s local `out` — the generator's reply —
    # became `reply`, because it shadowed the port inside the one closure that must not
    # lose it.
    #
    # 63 -> 47 is `providers.py`'s two commands, `cmd_models` and `cmd_probe`. They are
    # the only two of that file's ~3,600 lines the shell dispatches to; everything else in
    # it is the run loop, and its words are pass 2's problem. `cmd_probe`'s local `out` —
    # what the provider answered — is `reply` now, for the same reason `queueing.py`'s was
    # renamed: the port and the provider's reply cannot share a name.
    #
    # 47 -> 25 is `graph.py` and `mcp_scan.py`, one command each (`cmd_graph`,
    # `cmd_mcp_scan`) and 11 sites each. Both render a report whose `--json` arm hands a
    # whole `json.dumps(...)` block to a single call, which is the shape `Presenter.out`
    # was written for and the reason it takes text rather than a format.
    #
    # 25 -> 20 closes pass 1: `selftest.py`'s `cmd_selftest` (3) and the two in
    # `cli.py`'s `main()`, which is where the adapter is now built — one `ConsolePresenter`
    # at the process boundary, handed to `COMMANDS[cmd](rest, out=out)`, so all twenty-one
    # commands run on the presenter the shell chose rather than on a global each reached
    # for. The `COMMANDS` dict itself is untouched: `tests/test_capability_registry_vs_cli.py`
    # parses this file with `ast` and a computed dict would be unreadable to it.
    #
    # The 20 left are the judgement layer, and they are pass 2's: `commands.py`'s
    # `_require_executable_recipe`, `_refuse_blocked_state`, `_locked_secure_state_mutation`
    # and `_print_auto_route_regret` (13), and `recipes.py`'s trust and frontmatter
    # warnings (7). Measured with the ledger line lifted in a scratch copy, ruff's T201
    # also reads 20 — the two instruments agree for the first time, because the one site
    # they disagreed about was the `file=<conditional>` print that is now a stream choice.
    # So the `pyproject.toml` line stays until those twenty move.
    "orchestrate": {
        "print": 20,
        "subprocess": 21,
        "open_write": 4,
        "write_text": 10,
        "env": 19,
        "clock": 6,
    },
    # The third pillar behind the ports (§7 stage 3). Five kinds are zero because every
    # site moved onto one: 51 `print` to the `Presenter` the shell builds (50 stdout, one
    # stderr, three of them canonical-JSON documents that keep their single trailing
    # newline through `_emit_document`), 8 `os.environ` to `Env` — 3 tier roots in
    # `resolver.py`, 4 trust-store and consent variables in `trust.py`, and the
    # environment `sources.py` hands to git — 1 `subprocess.run` to `ProcessRunner`, and
    # 2 wall-clock reads to `Clock`, rendered `.astimezone(utc)` at the call because a
    # pack manifest and a lock entry both store UTC while the port reads the moment
    # through the local offset.
    #
    # The sixth is held rather than claimed. The 8 `write_text` were read and they are
    # not one thing: two are the shell's own writes of the pack `init` scaffolds, two are
    # writes into the `mkdtemp` staging tree `evidence.py` validates before swapping it in
    # (the same scratch-write shape as `eval`'s surviving site), one is `trust.py` writing
    # a `.tmp` it then `os.replace`s — an atomic replace, which `FileStore.write_text`
    # does not promise and so is not the same operation — and the remaining three are
    # `sync.py`, `exporter.py` and `sources.py` writing product files. Only the last of
    # those spells `FileStore.write_text`'s exact contract today (`parent.mkdir(parents=
    # True, exist_ok=True)` then the write). Moving some subset would be a claim about
    # which of five shapes belongs on one method, and nobody has made it; 8 is the floor
    # until somebody does.
    #
    # `packs` is deliberately not in `tests/test_layering_contract.py`'s `MIGRATED` yet.
    # The cross-pillar import edges in its judgement layer are the next pass, which is the
    # order `govern` and `eval` went in as well.
    "packs": {
        "print": 0,
        "subprocess": 0,
        "open_write": 0,
        "write_text": 8,
        "env": 0,
        "clock": 0,
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
    # The fourth pillar behind the ports (§7 stage 3). Five kinds are zero because every
    # site moved onto one: 13 `print` to the `Presenter` the shell builds — 8 in `cli.py`
    # (the report header, every result line, the tally and the verdict), 4 in
    # `selftest.py` (one per scenario family plus its own tally), and 1 that was not a
    # command's words at all — 1 `subprocess.run` and the 1 `os.environ` feeding it to
    # `ProcessRunner` / `Env` in `check_graph`, composed as `{**env.snapshot(),
    # "RIG_HOME": ...}` because the port's `env=` replaces rather than extends, and 1
    # wall-clock read to `Clock` in `check_wiki`, whose `> 180 days` verdict is now pinned
    # on both sides of the boundary against a frozen date.
    #
    # **The thirteenth `print` was `state.py`'s import-time PyYAML guard**, which printed
    # and called `sys.exit(1)` while the module was still being imported — in the one
    # module every other module in the pillar imports. It did not become a `Presenter`
    # call where it stood; the import and the guard moved to `validation/yaml_adapter.py`,
    # the guard raises `PyYAMLMissing`, and `cmd_validate` reports it through the
    # presenter the shell built. Same text, same stream, same exit status, and
    # `tests/test_validation_yaml_guard.py` executes the path rather than reasoning about
    # it: it makes `import yaml` raise by putting `None` in `sys.modules`, and asserts
    # that mechanism on its own first so nothing below can pass with the trap unset.
    #
    # The sixth is the same floor `eval` and `packs` record, for the same reason. All 4
    # `write_text` are in `selftest.py`, writing synthetic recipe and manifest fixtures
    # into the `TemporaryDirectory` it then runs `check_recipe` / `check_drill_coverage` /
    # `check_manifest` over and deletes. They are scratch writes into a tree the function
    # owns for the length of a call, not product files; routing them through
    # `FileStore.write_text` would be a port method written from a name rather than from a
    # call site. 4 is the floor until one of them has a reason to be a port call.
    #
    # `validation` is deliberately not in `tests/test_layering_contract.py`'s `MIGRATED`
    # yet, the same as `packs`: the cross-pillar import edges in its judgement layer are
    # the next pass, which is the order `govern` and `eval` went in as well.
    "validation": {
        "print": 0,
        "subprocess": 0,
        "open_write": 0,
        "write_text": 4,
        "env": 0,
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
    "clock": "wall-clock reads (see CLOCK_READS)",
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


#: Every stdlib call whose answer depends on the machine's settable, real-time clock,
#: written as the dotted name it has in the stdlib rather than as the spelling a file
#: happens to use. `_clock_names` maps each file's bindings back onto these, so an alias
#: (`import datetime as dt`, `from datetime import datetime as DT`) resolves to the same
#: entry as the plain spelling.
#:
#: Derived by walking the two stdlib modules that can answer "what time is it" and keeping
#: the calls that need no moment handed to them:
#:
#: * `datetime`: the four constructors that read — `datetime.now`, `datetime.utcnow`,
#:   `datetime.today`, `date.today`.
#: * `time`: the epoch readers (`time`, `time_ns`), the broken-down readers
#:   (`localtime`, `gmtime`) and the string readers (`ctime`, `asctime`, `strftime`),
#:   plus `clock_gettime`/`clock_gettime_ns`, which read CLOCK_REALTIME when asked for it.
#:
#: **`fromtimestamp` / `utcfromtimestamp` are deliberately absent.** They take the moment
#: as an argument, so they cannot tell a caller what time it is; whatever produced the
#: argument is the clock read, and in this tree that is either `time.time()` (already here)
#: or `Path.stat().st_mtime`, a filesystem read the `Clock` port has no method for and
#: should not grow one. Counting them would put sites under the clock ceiling that no port
#: migration can ever remove, which is a ceiling that can only be met by deleting code.
#:
#: **`monotonic`, `monotonic_ns`, `perf_counter`, `perf_counter_ns`, `process_time` and
#: `thread_time` are deliberately absent too**, and for a different reason: they count from
#: an arbitrary, unspecified epoch and are immune to the system clock being set. Their value
#: cannot be written into a governance record or compared against stored ISO text, which is
#: what `Clock` exists to make consistent. All 42 sites in this tree are `deadline = ... +
#: timeout` or `elapsed = ... - started` — durations, not clock reads — and a `Clock` that
#: absorbed them would break every timeout loop the moment a test froze it. `pyproject.toml`
#: says the same thing on the `time.time` ban message.
CLOCK_READS = frozenset(
    {
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.datetime.today",
        "datetime.date.today",
        "time.time",
        "time.time_ns",
        "time.clock_gettime",
        "time.clock_gettime_ns",
    }
)

#: The `time` calls that read the clock only in their short form: given the moment to render
#: they are converters. The value is the index of the argument whose presence makes the call
#: a conversion — `time.strftime(fmt)` reads, `time.strftime(fmt, t)` does not. ruff cannot
#: express this (see `test_the_ruff_ban_and_the_ast_walk_cover_the_same_clock_reads`), so its
#: table bans the name outright and this walk is the more precise of the two.
CLOCK_READS_UNLESS_GIVEN = {
    "time.localtime": 0,
    "time.gmtime": 0,
    "time.ctime": 0,
    "time.asctime": 0,
    "time.strftime": 1,
}


def _clock_names(tree: ast.Module) -> dict[str, str]:
    """Each local binding of `datetime`/`time` (or their classes) -> its stdlib dotted name.

    Walks the whole module, not just its head, because a function-local `import datetime`
    binds the name just as well. ruff's banned-api resolves the same aliases (measured:
    `import datetime as dt` and `from datetime import datetime as DT` both report as
    `datetime.datetime.now`), so this is not where the two halves differ — see
    `test_the_ruff_ban_and_the_ast_walk_cover_the_same_clock_reads` for where they do.
    """
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("datetime", "time"):
                    names[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in ("datetime", "time"):
                continue
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def _dotted(node: ast.expr, names: dict[str, str]) -> str | None:
    """`dt.datetime.now` -> "datetime.datetime.now", given this file's bindings.

    None when the receiver does not root in a name this file imported from `datetime` or
    `time` — the caller then falls back to matching the spelling, so a receiver reached
    through another module's attribute still counts.
    """
    attributes: list[str] = []
    while isinstance(node, ast.Attribute):
        attributes.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    root = names.get(node.id)
    if root is None:
        return None
    return ".".join([root, *reversed(attributes)])


class _EffectCollector(ast.NodeVisitor):
    """Counts effect call sites over the AST, so prose cannot inflate them."""

    def __init__(self, clock_names: dict[str, str] | None = None) -> None:
        self.counts = dict.fromkeys(EFFECT_KINDS, 0)
        self.clock_names = clock_names or {}

    def _count_clock(self, call: ast.Call, func: ast.Attribute) -> bool:
        """True when this call reads the wall clock. See `CLOCK_READS` for the set."""
        dotted = _dotted(func, self.clock_names)
        if dotted is not None:
            if dotted in CLOCK_READS:
                return True
            given = CLOCK_READS_UNLESS_GIVEN.get(dotted)
            return given is not None and len(call.args) <= given
        # Unresolved receiver: fall back to the spelling, which is all the narrow walk
        # ever did. Keeping this arm means the widened walk can never count *fewer* sites
        # than the one it replaces.
        value = func.value
        if func.attr in ("now", "utcnow", "today") and _names_datetime(value):
            return True
        return func.attr == "time" and isinstance(value, ast.Name) and value.id == "time"

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
            elif self._count_clock(node, func):
                self.counts["clock"] += 1
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        value = node.value
        if isinstance(value, ast.Name) and value.id == "os":
            if node.attr in ("environ", "getenv"):
                self.counts["env"] += 1
        self.generic_visit(node)


def _names_datetime(value: ast.expr) -> bool:
    """A receiver *spelled* as the `datetime` module or one of its two reading classes.

    `datetime.now(...)`, `dt.datetime.now(...)`, `datetime.date.today(...)`. This is the
    fallback for a receiver `_dotted` could not root in an import of this file — reached
    through another module's attribute, say. `date` is here as well as `datetime` because
    `date.today()` is the site that started this: the narrow walk matched only `now` and
    `utcnow` on a receiver named `datetime`, so `govern/cli.py`'s `datetime.date.today()`
    read as zero for four months.
    """
    if isinstance(value, ast.Name):
        return value.id in ("datetime", "date")
    if isinstance(value, ast.Attribute):
        return value.attr in ("datetime", "date")
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
            effect_collector = _EffectCollector(_clock_names(tree))
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


# ---------------------------------------------------------------------------
# The two halves of the clock ratchet, held against each other
# ---------------------------------------------------------------------------

#: Durations, not clock reads. In neither the walk above nor `pyproject.toml`'s table, and
#: this test says so out loud so that "why is `time.monotonic` allowed?" has an answer in
#: the tree rather than in a reviewer's memory. They count from an arbitrary epoch, do not
#: move when the system clock is set, cannot be written into a record or compared with
#: stored ISO text, and every site in rig is a timeout deadline or an elapsed measurement.
NOT_CLOCK_READS = frozenset(
    {
        "time.monotonic",
        "time.monotonic_ns",
        "time.perf_counter",
        "time.perf_counter_ns",
        "time.process_time",
        "time.process_time_ns",
        "time.thread_time",
        "time.thread_time_ns",
        # Conversions: the moment arrives as an argument, so these cannot answer
        # "what time is it". Whatever produced the argument is the read.
        "datetime.datetime.fromtimestamp",
        "datetime.datetime.utcfromtimestamp",
        "datetime.date.fromtimestamp",
    }
)


def _banned_api_table() -> dict[str, str]:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    table = data["tool"]["ruff"]["lint"]["flake8-tidy-imports"]["banned-api"]
    return {name: entry["msg"] for name, entry in table.items()}


def test_the_ruff_ban_and_the_ast_walk_cover_the_same_clock_reads() -> None:
    """The two halves of the ratchet must name the same set, or one of them is a trap.

    ruff names the offending line at the moment it is written; this walk freezes a number
    per package. A contributor caught by one and waved through by the other learns that the
    rule is arbitrary, which is how a ratchet stops being believed. So the set is written
    once, here, and both halves are held to it.

    They cannot agree on *everything*, and the two places they differ are deliberate:

    * **Argument count.** `time.strftime(fmt)` reads the clock and `time.strftime(fmt, t)`
      converts. ruff's banned-api matches a qualified name and has nowhere to put that
      condition, so it bans both; the walk checks `CLOCK_READS_UNLESS_GIVEN` and counts only
      the reading form. ruff is the stricter of the two here, which is the safe direction:
      it asks a question, it does not silently miss a read.
    * **Where the name comes from.** ruff resolves a qualified name through this file's
      imports and no further: `local.datetime.date.today()`, reached through another
      module's attribute, resolves to nothing in its table and passes. The walk falls back
      to matching the receiver's *spelling* (`_names_datetime`) when it cannot root the name
      in an import, so it counts that call. The walk is the stricter one here.

      Aliases are *not* on this list, though they look like they should be: ruff follows
      `import datetime as dt` and `from datetime import datetime as DT` to the same
      qualified name the walk resolves them to. Measured, not assumed — both halves bite
      `DT.now()`.

    * **Scope.** ruff is silenced per file by the debt ledger in `pyproject.toml`; this walk
      is silenced nowhere. A clock read added to `workbench/` today gets no ruff message and
      still raises that package's number here. That is the intended division: ruff names the
      line in code that has already migrated, the walk holds the whole tree.
    """
    banned = _banned_api_table()
    clock_bans = {
        name for name in banned
        if name.startswith(("datetime.", "time.")) or name == "time"
    }
    walk_covers = set(CLOCK_READS) | set(CLOCK_READS_UNLESS_GIVEN)

    missing_from_ruff = sorted(walk_covers - clock_bans)
    assert not missing_from_ruff, (
        "tests/test_architecture_inventory.py counts these as clock reads and "
        f"pyproject.toml's banned-api table does not name them: {missing_from_ruff}. "
        "A contributor writing one gets a silently rising ceiling instead of a message "
        "at the line. Add each to [tool.ruff.lint.flake8-tidy-imports.banned-api] with a "
        "msg that names the Clock port."
    )
    missing_from_walk = sorted(clock_bans - walk_covers)
    assert not missing_from_walk, (
        "pyproject.toml bans these as clock reads and the walk in this file does not "
        f"count them: {missing_from_walk}. ruff's per-file-ignores ledger covers most of "
        "the tree, so the ban alone leaves the number in BASELINE_EFFECT_SITES describing "
        "less code than the package contains — which is exactly how govern's clock ceiling "
        "read 0 with datetime.date.today() still in cli.py. Add each to CLOCK_READS."
    )


def test_durations_are_not_clock_reads_in_either_half() -> None:
    """`time.monotonic` and friends stay allowed, and `fromtimestamp` stays uncounted."""
    banned = _banned_api_table()
    walk_covers = set(CLOCK_READS) | set(CLOCK_READS_UNLESS_GIVEN)
    for name in sorted(NOT_CLOCK_READS):
        assert name not in banned, (
            f"{name} is banned as a clock read. It is not one: a duration counts from an "
            "arbitrary epoch and does not move when the system clock is set, and a "
            "fromtimestamp takes the moment as an argument. Banning it would push "
            "rig's timeout loops through a port that cannot serve them."
        )
        assert name not in walk_covers, (
            f"{name} is counted as a clock read by the walk. It is not one — see "
            "NOT_CLOCK_READS. A ceiling that counts it can only be met by deleting code, "
            "because the Clock port has no method that absorbs it."
        )


def test_the_walk_sees_a_clock_read_spelled_through_an_alias() -> None:
    """Every spelling of a read resolves to the same entry in `CLOCK_READS`.

    The narrow walk this replaced matched `a` and `b` and read `c` through `g` as zero,
    which is how `govern/cli.py` sat at `clock: 0` with `datetime.date.today()` in it.
    """
    source = (
        "import datetime\n"
        "import datetime as dt\n"
        "from datetime import datetime as DT\n"
        "from datetime import date as D\n"
        "import time as clock_module\n"
        "a = datetime.datetime.now()\n"
        "b = dt.datetime.now(dt.timezone.utc)\n"
        "c = DT.now()\n"
        "d = D.today()\n"
        "e = datetime.date.today()\n"
        "f = clock_module.time_ns()\n"
        "g = clock_module.strftime('%Y')\n"
    )
    tree = ast.parse(source)
    collector = _EffectCollector(_clock_names(tree))
    collector.visit(tree)
    assert collector.counts["clock"] == 7


def test_the_walk_counts_a_receiver_it_cannot_root_in_an_import() -> None:
    """The one place the walk is stricter than ruff: a receiver reached through a module.

    `local.datetime.date.today()` resolves to no entry in ruff's banned-api table and passes
    it (measured). `_names_datetime` matches the spelling, so the ceiling still moves.
    """
    tree = ast.parse("from rig_workbench.ports import local\nx = local.datetime.date.today()\n")
    collector = _EffectCollector(_clock_names(tree))
    collector.visit(tree)
    assert collector.counts["clock"] == 1


def test_the_walk_does_not_count_a_conversion_or_a_duration() -> None:
    """Every line here takes its moment from somewhere else, or measures an interval."""
    source = (
        "import datetime\n"
        "import time\n"
        "import pathlib\n"
        "a = datetime.datetime.fromtimestamp(pathlib.Path('x').stat().st_mtime)\n"
        "b = datetime.date.fromtimestamp(0)\n"
        "c = time.localtime(0)\n"
        "d = time.gmtime(0)\n"
        "e = time.ctime(0)\n"
        "f = time.strftime('%Y', c)\n"
        "g = time.monotonic()\n"
        "h = time.perf_counter()\n"
        "i = datetime.datetime(2026, 1, 1).strftime('%Y')\n"
        "j = datetime.timedelta(days=1)\n"
    )
    tree = ast.parse(source)
    collector = _EffectCollector(_clock_names(tree))
    collector.visit(tree)
    assert collector.counts["clock"] == 0
