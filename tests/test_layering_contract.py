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

Proving the rule on more than the tree it happens to be run against
-------------------------------------------------------------------

`MIGRATED` names `govern`, `eval`, `packs`, `validation` and `orchestrate`, and five pillars
of seven is still not much of a scan: two are outside the rule, and all five of them pass it
today, so the real-tree check can only ever say that nothing has regressed. A check that passes because it found nothing keeps passing if
the checker is written backwards — which is exactly how a check comes to exist without
ever having been checked, and it was the whole of this file's evidence for the stage in
which `MIGRATED` was still empty. So the corpus below runs the real checker over
constructed modules, and it stays the load-bearing half however many pillars migrate:
`REJECTED` covers every violation the rule
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
#: `govern` is the first, and its last edge is worth recording: `conformance.py` imported
#: `workbench.reporting.read_all_tasks` for the run records it scores. That import did not
#: move to the shell and did not become a port method — it was inverted. `conformance`
#: states what it needs of the records as a protocol of its own (`RunRecords`) and takes
#: them as an argument, because it scores run evidence and does not go and get it; the
#: shell (`govern/cli.py`) and `evidence.py` pass `read_all_tasks` in.
#:
#: `eval` is the second, and it needed the same move three times over rather than once.
#: `affected.py` reached `orchestrate.config`, `orchestrate.graph` and
#: `orchestrate.recipes` for the brick graph, and `promote.py` reached `packs.model` for
#: the directory a pack keeps evaluation cases in — every one of them a *function-local*
#: import, which is how a cross-pillar edge stays invisible while looking like it was
#: fixed. Each is now a protocol this pillar declares (`BrickGraphSource`, `PackCaseDir`)
#: and an adapter that satisfies it (`eval/source_graph.py`, `eval/pack_layout.py`), with
#: the callers handing the real one in. The fifth edge was `rig_workbench.__version__`,
#: imported by `gate.py` and `runner.py` to stamp and compare an executor version; that
#: one became `eval.cases.EXECUTOR_VERSION`, this pillar's own constant, because a value
#: the code only ever records is the weakest possible reason to hold an edge.
#:
#: `packs` is the third, and the widest: twenty-one edges out of its judgement layer, into
#: four other pillars at once. Two of them were function-local — `lock.py` reaching
#: `workbench.secrets` and `resolver.py` reaching `orchestrate.config._skill_root` — and
#: would have been invisible to a rule that read only `tree.body`. Nineteen were inverted
#: onto three adapters this pillar declares (`packs/scanners.py` for the content sensors,
#: `packs/eval_bridge.py` for the evaluation machinery and `packs/case_schema.py` for the
#: case schema alone — split off the bridge because taking it from there closed a new
#: ten-module cycle), with each caller stating the narrow
#: thing it needs: `LineScanner`, `TextSafety`, `FileScanner`, `RecipeGate`, `EvalEvidence`,
#: `ResultGate`, `CaseCheck`, `CaseRunner`. One of those, `CaseRunner`, is the first
#: inverted edge in stage 3 that is not a pure function — it runs a paid provider — and it
#: is borrowed rather than reimplemented precisely because a pack measured through a second
#: runner would not be comparable with the evidence `eval` produces.
#:
#: The remaining two were moved rather than inverted, and both say something about where the
#: line is. `rig_workbench.__version__` became `packs.model.ENGINE_VERSION`, following
#: `eval.cases.EXECUTOR_VERSION` — but *not* its justification: an executor version may
#: drift from the release, while this one is compared against a range the release publishes,
#: so it tracks it, and the comment there says so instead of borrowing a freedom this value
#: does not have. And `orchestrate.config._skill_root` was copied into `packs/resolver.py`,
#: because an inversion would have been a protocol plus an adapter plus a binding to keep
#: borrowing three lines and a private name.
#:
#: `validation` is the fourth, and the one where the borrowing was the point rather than an
#: accident. This pillar is the repository's own CI check, and every rule it applies asks
#: the same question — does a shipped document still agree with the code that runs it —
#: which cannot be answered without holding the code's answer beside the document's. So it
#: reached `orchestrate.gates` for what a gate is, `workbench.config` for the task types and
#: gate presets, `workbench.capabilities` for the route selector, `workbench.cli` for the
#: subcommand list, `workbench.stale_refs`, `orchestrate.mcp_scan`, `govern.stage` and
#: `packs.resolver`: eight collaborators, eleven edges, five of them function-local.
#:
#: Every one is inverted onto a single adapter, `validation/rig_surfaces.py`, with each
#: caller declaring the narrow thing it needs — `RecipeGate`, `RuntimeGateTest`,
#: `HumanGateParser`, `TaskRouter`, `StaleRefScanner`, `McpScanner`, `AssetResolver`,
#: `ParserSource`. One adapter rather than the three `packs` needed, and that is a
#: measurement rather than a preference: `packs` split because a single bridge closed a new
#: ten-module runtime cycle, and no cycle is reachable here because nothing in
#: `rig_workbench` imports `rig_workbench.validation.*` at all — the pillar is a sink,
#: entered by path from `scripts/validate.py` and `rig_workbench/cli.py`.
#:
#: Two of the inversions say something the earlier pillars did not. `HumanGateParser`
#: answers `(rule, error)` instead of raising, because `recipes.py` used to catch
#: `govern.stage.StageConfigError` — a class named in an `except` clause is a cross-pillar
#: edge exactly as much as a function named in a call. And `TaskRouter` needed the
#: *construction* to move, not just the call: `routes.py` built
#: `workbench.capabilities.LocalRecipe` values to feed the selector, and a judgement module
#: that builds another pillar's dataclass holds the edge whatever the call looks like.
#:
#: Nothing was copied. `GATE_PRESETS`, `TASK_TYPES` and `ROUTE_PRODUCERS` arrive as the
#: workbench's own objects handed in as data, which is the opposite of the move `packs` made
#: with `_skill_root`: there, a copy ended an edge nobody measured against; here the drift
#: between the code and the document *is the measurement*, and a checker holding its own
#: copy of the vocabulary would drift alongside the document it audits and report nothing.
#:
#: `orchestrate` is the fifth and the largest — thirteen thousand lines, twenty-one commands
#: and forty cross-pillar findings, into `packs`, `govern`, `workbench` and the package's own
#: top-level modules at once. They are held by four adapters this pillar declares rather than
#: one, and which edge went into which is a measurement rather than a filing decision:
#: `pack_surfaces.py` holds the resolver and the trust store, `govern_surfaces.py` the stage
#: gates and the ledger, `package_surfaces.py` `repo_paths` / `caller` / `bench_providers`,
#: and `batch_surface.py` the three workbench edges *because* putting them beside the others
#: closed a new six-module runtime cycle back through `orchestrate.config`. Four modules is
#: what the graph left standing, not what read best.
#:
#: The other reason this pillar took four passes is that its wiring is not all judgement.
#: `config.py` is declared a shell below, and the entry there states the decision rather than
#: summarising it: it is configuration, resolved once at import, and moving it behind `Env`
#: would change *when* it resolves.
MIGRATED: tuple[str, ...] = ("govern", "eval", "packs", "validation", "orchestrate")

#: The shell of each pillar: modules that wire, not modules that judge. Closed list —
#: everything else in a migrated pillar is judgement. Every entry states why, because
#: an exemption without a reason is how the exemption list grows.
SHELL_MODULES: dict[str, dict[str, str]] = {
    "govern": {
        f"{PACKAGE}.govern.cli": (
            "The command shell: argparse wiring, the 68 prints that are now Presenter "
            "calls, and the mapping from a verdict to an exit code. It is where the "
            "judgement modules get called from, so it is allowed to know about them, "
            "about gitroot, and about workbench.reporting — conformance takes the run "
            "records it scores as an argument, and this is the module that reads them "
            "and hands them over. Holding it to the rule would forbid the wiring."
        ),
    },
    "packs": {
        f"{PACKAGE}.packs.cli": (
            "The command shell: argparse wiring for every `rig-wb pack` verb, the words "
            "each one says through the Presenter, and the mapping from an outcome to an "
            "exit code. It is where the judgement modules get called from, so it is "
            "allowed to know about them and to build the adapters they are handed. It is "
            "also the one module that reaches `orchestrate.commands`: `pack invoke` on a "
            "recipe entrypoint hands the recipe to the orchestrator's runner, which is "
            "wiring between two command surfaces and not a judgement this pillar makes."
        ),
        f"{PACKAGE}.packs.scanners": (
            "The first of this pillar's two adapters, and here for the reason "
            "PORT_ADAPTERS gives for ports/local.py: an adapter exists precisely to hold "
            "what the protocol may not. It holds the content sensors packs borrows to "
            "judge text it did not write — workbench.injection, workbench.destructive, "
            "workbench.secrets, eval.safety and orchestrate.gates — behind the LineScanner "
            "and TextSafety shapes manifest.py declares and the FileScanner and RecipeGate "
            "shapes validation.py declares. Grouped by what the collaborator is rather "
            "than by which pillar it currently lives in, because three adapter modules "
            "each holding one import would say nothing one module does not. It imports no "
            "judgement module, so the inverted edges stay one-way."
        ),
        f"{PACKAGE}.packs.case_schema": (
            "The narrow half of the evaluation borrowing, and a separate module because a "
            "measurement said so rather than because it reads better: validation.py needs "
            "only the case schema, and taking it from the wide bridge gave that module a "
            "module-level path to eval.gate and from there back into packs.resolver — a "
            "new ten-module import cycle that test_architecture_inventory.py refused. This "
            "reaches eval.cases and nothing else, and eval_bridge takes its case names "
            "from here, so the schema is still borrowed in exactly one place."
        ),
        f"{PACKAGE}.packs.eval_bridge": (
            "The second adapter: the evaluation machinery, which is a different "
            "collaborator with a different shape rather than a different address. One "
            "object satisfies three of the four narrow declarations — "
            "evidence.EvalEvidence, installer.ResultGate and tester.CaseRunner — because a "
            "protocol is structural and those callers' needs overlap; the fourth, "
            "validation.CaseCheck, takes the narrower case_schema for the cycle reason "
            "stated above. It earns its place "
            "beyond holding imports in one specific way: evidence.py used to reach "
            "eval.runner._git_identity, a private name, and the bridge republishes it as "
            "git_identity so that reach-in stops here instead of appearing in signatures "
            "the judgement layer writes."
        ),
    },
    "validation": {
        f"{PACKAGE}.validation.cli": (
            "The command shell: the argv-taking half of `scripts/validate.py`, the four "
            "adapters it builds once at the process boundary and forwards, and the mapping "
            "from the FAIL tally to an exit code. It is where the twenty-two checks get "
            "called from, so it is allowed to know about all of them. `main()` still takes "
            "no arguments and still ends in `sys.exit`, because `rig_workbench/cli.py`'s "
            "`_run_validate` swaps `sys.argv` and calls it, and "
            "tests/test_capability_registry_vs_cli.py freezes that dispatch shape."
        ),
        f"{PACKAGE}.validation.rig_surfaces": (
            "This pillar's one adapter, here for the reason PORT_ADAPTERS gives for "
            "ports/local.py: an adapter exists precisely to hold what the protocol may "
            "not. It holds all eight surfaces the validator checks shipped documents "
            "against — orchestrate.gates, orchestrate.mcp_scan, govern.stage, "
            "workbench.cli, workbench.config, workbench.capabilities, "
            "workbench.stale_refs and packs.resolver — behind the shapes catalog.py, "
            "drill.py, manifest.py, mcp_scan.py, recipes.py, routes.py and stale_refs.py "
            "declare. One module rather than the three `packs` needed, because the cycle "
            "that forced that split cannot occur here: nothing in rig_workbench imports "
            "rig_workbench.validation, so no collaborator's closure comes back through "
            "it. It imports no judgement module, so the inverted edges stay one-way."
        ),
        f"{PACKAGE}.validation.yaml_adapter": (
            "The pillar's one optional dependency, behind a call. PyYAML is third-party "
            "and a judgement module may not import it, which is the rule working rather "
            "than an inconvenience: this used to be a bare `import yaml` in state.py that "
            "printed and called sys.exit during the import itself, so importing any module "
            "in the pillar could kill the caller's interpreter. The import and the guard "
            "live here and the guard raises PyYAMLMissing, which cli.py reports through "
            "the Presenter it built. Holding this module to the rule would forbid the one "
            "import the whole pillar is built on."
        ),
    },
    "orchestrate": {
        f"{PACKAGE}.orchestrate.cli": (
            "The command shell: the module docstring that *is* `--help`, the argparse-free "
            "dispatch table `COMMANDS`, and the one `ConsolePresenter` built in `main()` and "
            "handed to all twenty-one verbs as `out=`. It is where the judgement modules get "
            "called from, so it is allowed to know about them, and about `context_meter` and "
            "`gh_requirement` — counting what an invocation prints at the parent session and "
            "advising on a missing `gh` are things the process boundary does once, not "
            "judgements any command makes. The `COMMANDS` dict stays a literal because "
            "tests/test_capability_registry_vs_cli.py parses this file with `ast`."
        ),
        f"{PACKAGE}.orchestrate.config": (
            "Configuration wiring, and the one shell entry in stage 3 that is not a command "
            "surface or an adapter. It reads RIG_HOME, os.getcwd() and RIG_GLOBAL_RUNS_PATH "
            "at import time and RIG_CONVERGENCE_K beside them, and it reaches "
            "rig_workbench.gitroot for the main worktree the state root is derived from. "
            "Putting those reads behind `Env` and `GitRepo` would not move an effect out of "
            "the judgement layer, because none of this judges anything: it answers where the "
            "assets, the run log and the state root are, which is what every other module in "
            "the pillar is configured *by* — twelve of them import it, and roughly forty "
            "tests monkeypatch its attributes to point rig at a temporary project. It would "
            "change *when* those questions are answered, from import time to first call, and "
            "that is a behaviour change nobody asked for and no test declares. So the reads "
            "stay, they are named here rather than left to be rediscovered, and "
            "pyproject.toml carries the matching narrow TID251 line for this file alone."
        ),
        f"{PACKAGE}.orchestrate.pack_surfaces": (
            "The first of this pillar's four adapters, here for the reason PORT_ADAPTERS "
            "gives for ports/local.py: an adapter exists precisely to hold what the protocol "
            "may not. Twenty edges into `packs` — does this asset name resolve, is the file "
            "it resolves to trusted, which packs are installed, what does a pack call this "
            "kind of asset — behind the PackAssets, PackComposition, PackProvenance and "
            "PackInventory shapes recipes.py, providers.py, runstate.py and graph.py "
            "declare. `PackError` is republished rather than inverted, because an `except` "
            "clause compares class identity and two classes would be two rules. It imports "
            "no judgement module, so the inverted edges stay one-way."
        ),
        f"{PACKAGE}.orchestrate.govern_surfaces": (
            "The second adapter: eleven edges into `govern`, all one request in different "
            "words — has this step a human gate, may this identity sign it, who is this "
            "identity, where is the decision written down. Quorum, qualifying roles, "
            "separation of duties and the hash chain are govern's arithmetic, and a runner "
            "that re-derived any of it would be a second governance implementation nobody "
            "audits. Behind the StageGovernance and StepGovernance shapes commands.py and "
            "runstate.py declare. `PolicyError` stops here — this pillar only ever caught "
            "it — and `policy()` answers a refusal the command prints unchanged."
        ),
        f"{PACKAGE}.orchestrate.package_surfaces": (
            "The third adapter, and the one that holds what belongs to no pillar: "
            "`repo_paths` (where is scripts/<name>.py), `caller` (what invoked this "
            "process) and `bench_providers` (the patch machinery a tool-free local "
            "generator is given writable parity through). Behind ScriptLocator, "
            "CallerIdentity and PatchApplier. A fourth module rather than a fourth group in "
            "pack_surfaces.py because these are one collaborator — the package's own shared "
            "utilities — and an answer about scripts/dashboard.py does not belong in a "
            "module about trust stores; the cycle measurement left that choice free."
        ),
        f"{PACKAGE}.orchestrate.batch_surface": (
            "The fourth adapter, and the only one whose existence is a measurement rather "
            "than a preference. It holds three workbench edges — `workbench.batch` for what "
            "a batch did, `workbench.state` for where the repository is, `workbench.state`'s "
            "companion `workbench.run_index` for the projects that have recorded a run — "
            "behind the BatchSummary and ProjectIndex shapes queueing.py and commands.py "
            "declare. Filed apart from the other three because `workbench.progress` reaches "
            "`orchestrate.recipes` and `workbench.run_index` reaches `orchestrate.config`: "
            "in the same module as the packs and govern collaborators those imports close a "
            "new six-module runtime cycle that tests/test_architecture_inventory.py freezes "
            "the absence of. The same constraint that split packs/case_schema.py out of "
            "packs/eval_bridge.py, found the same way — by running the graph."
        ),
        f"{PACKAGE}.orchestrate.yaml_adapter": (
            "The pillar's one optional dependency, behind a call, and validation's "
            "yaml_adapter.py's twin. PyYAML is third-party and a judgement module may not "
            "import it. `recipes.py` carried `try: import yaml / except ImportError: yaml = "
            "None` at module level, which bound the name once at import and made the "
            "`yaml is None` branch unreachable from any running process with PyYAML "
            "installed — a refusal no test had ever executed. The import and the guard live "
            "here, the guard raises PyYAMLMissing, and parse_frontmatter reports it through "
            "the Presenter the shell built. Holding this module to the rule would forbid "
            "the one import the whole pillar is built on."
        ),
    },
    "eval": {
        f"{PACKAGE}.eval.cli": (
            "The command shell: argparse wiring, the 16 prints that are now Presenter "
            "calls, and the exit status each verb reports. It is where the judgement "
            "modules get called from, so it is allowed to know about them and to build "
            "the adapters they are handed — the Presenter, the ProcessRunner, the Env "
            "and the Clock, once each, at the process boundary. Holding it to the rule "
            "would forbid the wiring the pillar is wired by."
        ),
        f"{PACKAGE}.eval.source_graph": (
            "The adapter behind eval's one cross-pillar edge, and it is here for the "
            "reason PORT_ADAPTERS gives for ports/local.py: an adapter exists precisely "
            "to hold what the protocol may not. affected.py states what it needs of a "
            "brick graph — a tree in, nodes and edges out — as BrickGraphSource, and "
            "this module satisfies it by reading the orchestrator's RIG_HOME, "
            "build_brick_graph and recipe frontmatter. It imports nothing from the "
            "judgement layer, so the inverted edge stays one-way: the surface layout it "
            "walks is passed in by the caller that declares it."
        ),
        f"{PACKAGE}.eval.pack_layout": (
            "The second adapter, and the smaller one: where a pack keeps its evaluation "
            "cases, which is the packs pillar's declaration and not eval's. promote.py "
            "states what it needs as PackCaseDir and this satisfies it, so the import "
            "that used to sit inside a function — deferred to dodge a cycle, which hides "
            "a dependency rather than removing it — is a declared edge in a module whose "
            "whole purpose is to hold it."
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

    The scan over the real tree exercises whatever `govern` happens to do, which is a
    fraction of the rule and shrinks as the pillar gets tidier. This does the rest: every
    kind the checker can emit must be produced by a constructed module above. A new kind
    without a case fails here, in the same commit that adds it.
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
    """The contract itself, and no longer vacuous: five pillars are behind the ports.

    All five pass, which is the only thing a green contract can mean — every edge either
    became a port call or was inverted into a protocol the pillar declares, and each
    pillar's wiring is named in `SHELL_MODULES`. What it cannot mean is that the rule is
    right: five compliant pillars exercise almost none of the checker, which is what the
    corpus above is for. The skip below is kept for the case `MIGRATED` is ever emptied to
    take a pillar back out.
    """
    layout = real_layout()
    if not layout.migrated:
        pytest.skip(
            "MIGRATED is empty, so there is no migrated pillar to scan (design brief §7 "
            "stage 3). `govern` was the first and emptying the tuple takes it back out; "
            "the rule itself is proved against the constructed corpus in this file."
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

    Applied to every pillar as if it had migrated and with no shell exempt, the rule must
    object to something — two of the seven pillars are still 未着手, and the five that are
    not answer here through their shells and adapters: the shell wires and the adapter holds
    what a protocol may not, so with the exemptions dropped `govern/cli.py`'s imports of
    `gitroot` and `workbench.reporting` are findings, and so are `packs/scanners.py`'s
    sensors, `packs/eval_bridge.py`'s evaluation imports, every one of the eight surfaces
    `validation/rig_surfaces.py` holds, and everything orchestrate's four adapters hold —
    plus `orchestrate/config.py`'s `gitroot`, which is the one shell entry in this table
    that is neither a command surface nor an adapter. (What no longer appears is the
    judgement layer of any of the five: `conformance.py`, `affected.py`, `promote.py`,
    `manifest.py`, `validation.py`, `evidence.py`, `installer.py`, `tester.py`, `lock.py`,
    `resolver.py`, validation's `catalog.py`, `drill.py`, `manifest.py`, `mcp_scan.py`,
    `recipes.py`, `routes.py` and `stale_refs.py`, and orchestrate's `commands.py`,
    `providers.py`, `runstate.py`, `recipes.py`, `queueing.py` and `graph.py` have each had
    their cross-pillar imports inverted into a protocol they declare. Those are the edges
    `MIGRATED` was waiting on.) A scan that found nothing here
    would mean the walk read no files, and every check above it would be passing on air.
    When the last pillar migrates this test starts failing, and deleting it is the correct
    response: it will have run out of work.

    This assertion is the one thing in the file that a pillar entering `MIGRATED` does not
    move: it replaces `migrated` with every pillar and `shell` with nothing before it scans,
    so declaring `orchestrate` changed the count by zero. That is the design, not an
    oversight — the number moves when imports move, which is what the four preceding
    commits did and this one did not.
    """
    layout = real_layout()
    everything = dataclasses.replace(layout, migrated=layout.pillars, shell=frozenset())
    found = scan(PACKAGE_ROOT, everything)
    assert found, (
        "Scanning rig_workbench/ with every pillar treated as migrated produced no "
        "findings at all. Before celebrating, check that _module_name and scan() are "
        "still reading the package: 193 files that all obey a rule five pillars of seven "
        "have started applying is the less likely explanation."
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
