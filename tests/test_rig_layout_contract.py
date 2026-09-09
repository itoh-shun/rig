"""Where rig keeps its state under `.rig/`, frozen as a contract.

`.rig/` is not private scratch space. Other tools read it by path: this repo's own
VS Code extension opens `.rig/runs/` directly (`vscode-extension/src/rigState.ts`),
`rig-wb usage` reads `.rig/runs.jsonl` in whatever directory it is pointed at, and a
team's CI or a person's shell script reads `.rig/runs/<task_id>/acceptance.json` the
same way. Moving one of those paths is a breaking change for everybody outside this
repository, and — because every in-repo caller moves with the constant — it is a
breaking change that nothing in the suite noticed until this file existed.

Nothing anywhere in the repository listed those paths. `.rig/` was described in a
docstring here and a help string there, and the layout as a whole existed only as an
emergent property of thirty modules each joining `root / ".rig" / "something"`. The
tables below are that missing list: THE canonical inventory of the core run contract,
written down so that changing it has to be a deliberate act.

Two groups, selectable with `-k`:

    pytest tests/test_rig_layout_contract.py -k top_level   # T13: `.rig/` itself
    pytest tests/test_rig_layout_contract.py -k run_dir     # T14: `.rig/runs/<task_id>/`

Both drive the real CLI through a real run — `wb new`, the gate, `wb accept` — in a
throwaway git repository, and assert against what the process actually wrote. A path
the driven flow does not produce is still contract, so it is pinned a second way:
by resolving it through the production accessor that computes it, which fails loudly
when that accessor is renamed. Every entry says which of the two it got, and
`NOT_PINNED` says what neither reaches, and why.

If a test here fails, the layout moved. That is allowed and sometimes right — but say
so in review, and update the table on purpose, because somebody's script is reading
the old path.
"""

import dataclasses
import importlib
import json
import os
import pathlib
import subprocess

import pytest

from conftest import subprocess_timeout

# ── how an entry's location is derived from production code ──────────────────
#: `accessor(root)` -> absolute path.
CALL_WITH_ROOT = "call(root)"
#: `accessor(root, task_id)` -> absolute path.
CALL_WITH_ROOT_AND_TASK = "call(root, task_id)"
#: A module-level `pathlib.Path` already anchored at the state root.
PATH_UNDER_STATE_ROOT = "path-under-state-root"
#: A module-level string holding the whole repo-relative path.
RELATIVE_STRING = "relative-string"
#: A module-level string holding only the basename; `.rig/` is joined at the call site.
BASENAME_UNDER_RIG = "basename-under-rig"
#: `pack_roots(root)` -> tiers; the project tier is the one under `.rig/`.
PROJECT_PACK_TIER = "pack-roots-project-tier"
#: `QUEUE_PATH` with `.lock` appended — the flock file is derived, not named separately.
QUEUE_LOCK_BESIDE_QUEUE = "queue-path-plus-.lock"
#: No accessor exists: production code writes the literal inline at each call site.
#: Such an entry has to be pinned by observation or by behaviour, and
#: `test_..._every_top_level_path_without_an_accessor_is_pinned_another_way` enforces that.
NO_ACCESSOR = "inline literal, no named accessor"


@dataclasses.dataclass(frozen=True)
class Pinned:
    """One path in the layout, and everything this file claims about it."""

    #: Repo-relative, POSIX. Run-dir entries carry a `{task}` placeholder.
    rel: str
    #: "file" or "dir".
    kind: str
    #: What puts it there, in the user's own vocabulary.
    written_by: str
    #: True when THIS file drives whatever creates it and then looks at the result on
    #: disk. False means the location is pinned only through the production accessor
    #: (or, for the person-authored config, through behaviour).
    driven: bool
    #: True when a plain run — new, gate, review, note, accept — is enough to produce it.
    #: False means it needs an extra command, or a person, or is never created at all.
    plain_run: bool
    #: (module, attribute) resolving the location in production code, or None.
    accessor: tuple[str, str] | None
    #: One of the resolution kinds above.
    via: str
    #: "json-object", "jsonl-object-rows", "text", "opaque" or "dir".
    shape: str
    #: Top-level keys a reader may rely on. A required subset, never the whole set:
    #: adding a key is backwards compatible, removing one is not.
    required_keys: tuple[str, ...] = ()


# ═════════════════════════════════════════════════════════════════════════════
# T13 — `.rig/` itself.
#
# path                      | plain run? | keys pinned?
# --------------------------|------------|--------------
# runs.jsonl                | yes        | yes
# runs/                     | yes        | (directory)
# context.jsonl             | yes        | yes
# provenance.key            | yes        | no — 32 opaque bytes
# locks/                    | yes (1)    | (directory)
# audit.jsonl               | extra cmd  | yes
# ledger.jsonl              | extra cmd  | yes
# queue.json                | extra cmd  | yes
# queue.json.lock           | extra cmd (1) | no — empty flock file
# instincts.jsonl           | extra cmd  | yes
# org.json                  | extra cmd  | yes
# policy/org.json           | extra cmd  | yes
# gates.json                | no — authored by a person; rig only reads it
# access.json               | no — authored by a person; rig only reads it
# packs/                    | no — `pack install`
# recipes/                  | no — authored, or written by pack/instinct tooling
# sources.json              | no — authored by a person
# drill-results.jsonl       | no — drill scoring
#
# (1) POSIX only: both lock files exist because `fcntl` does. See OPTIONAL_TOP_LEVEL.
# ═════════════════════════════════════════════════════════════════════════════
TOP_LEVEL_LAYOUT = (
    Pinned(
        rel=".rig/runs.jsonl",
        kind="file",
        written_by="wb accept — telemetry appends one record per finished run",
        driven=True,
        plain_run=True,
        accessor=("rig_workbench.orchestrate.config", "RUNS_PATH"),
        via=PATH_UNDER_STATE_ROOT,
        shape="jsonl-object-rows",
        required_keys=("ts", "task_id", "recipe", "final"),
    ),
    Pinned(
        rel=".rig/runs",
        kind="dir",
        written_by="wb new — one directory per task, named by task_id",
        driven=True,
        plain_run=True,
        accessor=("rig_workbench.workbench.state", "runs_dir"),
        via=CALL_WITH_ROOT,
        shape="dir",
    ),
    Pinned(
        rel=".rig/context.jsonl",
        kind="file",
        written_by="every command that prints — the context meter tallies its own output",
        driven=True,
        plain_run=True,
        accessor=("rig_workbench.context_meter", "CONTEXT_REL"),
        via=RELATIVE_STRING,
        shape="jsonl-object-rows",
        required_keys=("ts", "command", "argv", "task_id", "bytes", "lines", "invoker"),
    ),
    Pinned(
        rel=".rig/provenance.key",
        kind="file",
        written_by="wb accept — the HMAC key it signs provenance records with",
        driven=True,
        plain_run=True,
        accessor=("rig_workbench.workbench.state", "_provenance_key_path"),
        via=CALL_WITH_ROOT,
        shape="opaque",
    ),
    Pinned(
        rel=".rig/locks",
        kind="dir",
        written_by="any command taking the per-task lock (`wb gate` is the first here)",
        driven=True,
        plain_run=True,
        accessor=("rig_workbench.workbench.state", "locks_dir"),
        via=CALL_WITH_ROOT,
        shape="dir",
    ),
    Pinned(
        rel=".rig/audit.jsonl",
        kind="file",
        written_by="wb accept --force — the permanent record of an overridden gate",
        driven=True,
        plain_run=False,
        accessor=("rig_workbench.workbench.state", "audit_path"),
        via=CALL_WITH_ROOT,
        shape="jsonl-object-rows",
        required_keys=("action", "task_id", "task_type", "gate_status", "bypassed", "ts"),
    ),
    Pinned(
        rel=".rig/ledger.jsonl",
        kind="file",
        written_by="govern approve — the hash-chained trail of governed decisions",
        driven=True,
        plain_run=False,
        accessor=("rig_workbench.govern.ledger", "ledger_path"),
        via=CALL_WITH_ROOT,
        shape="jsonl-object-rows",
        required_keys=("seq", "prev", "hash", "action", "actor", "subject", "ts"),
    ),
    Pinned(
        rel=".rig/queue.json",
        kind="file",
        written_by="queue add",
        driven=True,
        plain_run=False,
        accessor=("rig_workbench.orchestrate.config", "QUEUE_PATH"),
        via=PATH_UNDER_STATE_ROOT,
        shape="json-object",
        required_keys=("items", "next_id"),
    ),
    Pinned(
        rel=".rig/queue.json.lock",
        kind="file",
        written_by="queue add — the flock file beside the queue",
        driven=True,
        plain_run=False,
        accessor=("rig_workbench.orchestrate.config", "QUEUE_PATH"),
        via=QUEUE_LOCK_BESIDE_QUEUE,
        shape="opaque",
    ),
    Pinned(
        rel=".rig/instincts.jsonl",
        kind="file",
        written_by="wb instincts --add (the project tier; the host tier is ~/.rig/)",
        driven=True,
        plain_run=False,
        # The basename alone is the public constant `INSTINCTS_PATH_NAME`; this resolves
        # the whole path, so a move of the `.rig/` half is caught as well as a rename.
        accessor=("rig_workbench.workbench.instincts", "_instincts_path"),
        via=CALL_WITH_ROOT,
        shape="jsonl-object-rows",
        required_keys=("id", "text", "status", "confidence"),
    ),
    Pinned(
        rel=".rig/org.json",
        kind="file",
        written_by="govern init — which org/team this checkout is bound to",
        driven=True,
        plain_run=False,
        accessor=("rig_workbench.govern.identity", "org_binding_path"),
        via=CALL_WITH_ROOT,
        shape="json-object",
        required_keys=("schema", "org", "policy_layers"),
    ),
    Pinned(
        rel=".rig/policy/org.json",
        kind="file",
        written_by="govern init — the starter policy layer `org.json` points at",
        driven=True,
        plain_run=False,
        # No named accessor: `govern/cli.py` and `govern/policy.py` each join the literal.
        # Pinned instead by the binding above naming this path in its `policy_layers`,
        # which is the same string a shared-policy checkout would have to reproduce.
        accessor=None,
        via=NO_ACCESSOR,
        shape="json-object",
        required_keys=("schema", "id", "org", "version"),
    ),
    Pinned(
        rel=".rig/gates.json",
        kind="file",
        written_by="a person — project gate extensions; rig only ever reads this file",
        driven=False,
        plain_run=False,
        accessor=("rig_workbench.workbench.state", "project_gates_path"),
        via=CALL_WITH_ROOT,
        shape="json-object",
    ),
    Pinned(
        rel=".rig/access.json",
        kind="file",
        written_by="a person — the v1 accept allowlist; rig only ever reads this file",
        driven=False,
        plain_run=False,
        # No named accessor: `state.load_access_control`, `govern/cli.py` and
        # `govern/conformance.py` each join the literal. Pinned by behaviour instead —
        # a file written here changes what `wb accept` does.
        accessor=None,
        via=NO_ACCESSOR,
        shape="json-object",
    ),
    Pinned(
        rel=".rig/packs",
        kind="dir",
        written_by="pack install — the project tier of the pack resolver",
        driven=False,
        plain_run=False,
        accessor=("rig_workbench.packs.resolver", "pack_roots"),
        via=PROJECT_PACK_TIER,
        shape="dir",
    ),
    Pinned(
        rel=".rig/recipes",
        kind="dir",
        written_by="a person, or `wb instincts --generate-checks` — project recipe overlay",
        driven=False,
        plain_run=False,
        accessor=("rig_workbench.orchestrate.config", "PROJECT_RECIPES"),
        via=PATH_UNDER_STATE_ROOT,
        shape="dir",
    ),
    Pinned(
        rel=".rig/sources.json",
        kind="file",
        written_by="a person — declared pack sources; rig only ever reads this file",
        driven=False,
        plain_run=False,
        accessor=("rig_workbench.packs.sources", "sources_path"),
        via=CALL_WITH_ROOT,
        shape="json-object",
    ),
    Pinned(
        rel=".rig/drill-results.jsonl",
        kind="file",
        written_by="wb drill scoring (--append) — one row per scored mutation drill",
        driven=False,
        plain_run=False,
        accessor=("rig_workbench.orchestrate.config", "DRILL_PATH"),
        via=PATH_UNDER_STATE_ROOT,
        shape="jsonl-object-rows",
    ),
)


# ═════════════════════════════════════════════════════════════════════════════
# T14 — `.rig/runs/<task_id>/`.
#
# path             | plain run? | keys pinned?
# -----------------|------------|--------------
# task.json        | yes        | yes
# steps.json       | yes        | yes
# acceptance.json  | yes        | yes
# diff.md          | yes (2)    | no — prose, parsed by section heading
# review.json      | yes (3)    | yes
# reviews/         | yes (3)    | (directory)
# handoff.json     | yes (3)    | yes
# provenance.json  | yes        | yes
# approvals.json   | extra cmd  | yes
#
# (2) written by the implementing agent, never by rig — see the entry.
# (3) produced by `wb review` / `wb note`, which the driven flow runs.
# ═════════════════════════════════════════════════════════════════════════════
RUN_DIR_LAYOUT = (
    Pinned(
        rel=".rig/runs/{task}",
        kind="dir",
        written_by="wb new",
        driven=True,
        plain_run=True,
        accessor=("rig_workbench.workbench.state", "run_dir"),
        via=CALL_WITH_ROOT_AND_TASK,
        shape="dir",
    ),
    Pinned(
        rel=".rig/runs/{task}/task.json",
        kind="file",
        written_by="wb new, updated by every command that moves the task",
        driven=True,
        plain_run=True,
        accessor=None,
        via=NO_ACCESSOR,
        shape="json-object",
        required_keys=("task_id", "task_type", "status", "input", "recipe", "route",
                       "base_branch", "base_commit", "branch", "worktree_path",
                       "created_at", "updated_at"),
    ),
    Pinned(
        rel=".rig/runs/{task}/steps.json",
        kind="file",
        written_by="wb new (seeded from the recipe), updated by `wb step`",
        driven=True,
        plain_run=True,
        accessor=None,
        via=NO_ACCESSOR,
        shape="json-object",
        required_keys=("steps", "seeded"),
    ),
    Pinned(
        rel=".rig/runs/{task}/acceptance.json",
        kind="file",
        written_by="wb new (built from the presets), recorded into by `wb gate`",
        driven=True,
        plain_run=True,
        accessor=None,
        via=NO_ACCESSOR,
        shape="json-object",
        required_keys=("task_id", "task_type", "presets", "status", "checks", "checked_at"),
    ),
    Pinned(
        rel=".rig/runs/{task}/diff.md",
        kind="file",
        written_by="the implementing agent — rig never writes it here, it requires it",
        driven=True,
        plain_run=True,
        accessor=None,
        via=NO_ACCESSOR,
        shape="text",
    ),
    Pinned(
        rel=".rig/runs/{task}/review.json",
        kind="file",
        written_by="wb review --set — per-persona verdict labels",
        driven=True,
        plain_run=True,
        accessor=None,
        via=NO_ACCESSOR,
        shape="json-object",
        required_keys=("task_id", "verdicts"),
    ),
    Pinned(
        rel=".rig/runs/{task}/reviews",
        kind="dir",
        written_by="wb review --body — one `<persona>.md` per reviewer body",
        driven=True,
        plain_run=True,
        accessor=None,
        via=NO_ACCESSOR,
        shape="dir",
    ),
    Pinned(
        rel=".rig/runs/{task}/handoff.json",
        kind="file",
        written_by="wb note — append-only hand-off notes",
        driven=True,
        plain_run=True,
        accessor=None,
        via=NO_ACCESSOR,
        shape="json-object",
        required_keys=("task_id", "notes"),
    ),
    Pinned(
        rel=".rig/runs/{task}/provenance.json",
        kind="file",
        written_by="wb accept — the signed record `wb verify-provenance` checks",
        driven=True,
        plain_run=True,
        accessor=None,
        via=NO_ACCESSOR,
        shape="json-object",
        required_keys=("record", "signature", "algo"),
    ),
    Pinned(
        rel=".rig/runs/{task}/approvals.json",
        kind="file",
        written_by="govern approve",
        driven=True,
        plain_run=False,
        accessor=("rig_workbench.govern.approval", "approvals_path"),
        via=CALL_WITH_ROOT_AND_TASK,
        shape="json-object",
        required_keys=("task_id", "decisions"),
    ),
)


#: Entries that appear only where `fcntl` does. `state.task_lock` and the queue's
#: `_queue_locked` both no-op without it (Windows), so on such a machine the lock
#: file — and, for `.rig/locks`, the directory itself — is simply never created.
#: They stay in the table because their location is contract wherever they exist;
#: the set comparisons treat them as allowed-but-not-required.
OPTIONAL_TOP_LEVEL = frozenset({".rig/locks", ".rig/queue.json.lock"})

#: What this file does NOT pin, and why. Each line is a deliberate gap, not an oversight.
NOT_PINNED = {
    ".rig/packs/ — that it is created":
        "`pack install` fetches a signed pack over the network from a declared source. "
        "The location is pinned through `packs.resolver.pack_roots`; only the act of "
        "creating the directory is out of reach of a hermetic test.",
    ".rig/recipes/ — that it is created":
        "written by `wb instincts --generate-checks` (which needs instincts the "
        "generator recognises) or authored by hand. Location pinned through "
        "`config.PROJECT_RECIPES`.",
    ".rig/sources.json — that it is created or read":
        "a person authors it; nothing in rig writes it, and reading it means resolving "
        "a pack install. Location pinned through `packs.sources.sources_path`.",
    ".rig/drill-results.jsonl — that it is created":
        "written by mutation-drill scoring, which needs real reviewer runs against a "
        "provider. Location pinned through `config.DRILL_PATH`.",
    "the exact key SET of every file":
        "only a required subset is pinned. Adding a top-level key is backwards "
        "compatible for readers, so freezing the whole set would fail on additions "
        "that break nobody. Removals and renames are what these tests catch.",
    "`.rig/` subtrees outside the core run contract":
        "worktrees/, evals/, mission-control/, visual/, secure-runs/, waivers.json, "
        "fleet.json, field-study.jsonl and provenance-graph state exist and each has "
        "its own module; they are named here so nobody reads this file as an "
        "exhaustive inventory of `.rig/` rather than of the run contract.",
    "the task worktree itself":
        "`wb new` puts it in a sibling `rig-worktrees/<repo>/<task_id>/` directory "
        "outside the repository — deliberately not under `.rig/`, and so not part of "
        "this layout. `state.default_worktree_path` owns that decision.",
}


# ── resolving a location out of production code ──────────────────────────────
def resolve_from_production_code(entry: Pinned, root: pathlib.Path, task_id: str) -> str:
    """Where production code says `entry` lives, as a repo-relative POSIX path.

    This is the half of the contract that does not need a run: it asks the module that
    computes the path, so renaming that accessor — the change that moves every in-repo
    caller at once and would otherwise be invisible — fails here with its own name in
    the message.
    """
    assert entry.accessor is not None, (
        f"{entry.rel} has no production accessor; it is pinned by observation or "
        "behaviour instead and must not be resolved this way")
    module_name, attribute = entry.accessor
    module = importlib.import_module(module_name)
    try:
        value = getattr(module, attribute)
    except AttributeError as exc:
        raise AssertionError(
            f"{module_name} no longer defines `{attribute}` — the accessor this contract "
            f"resolves {entry.rel} through. If it was renamed, the path it computes is a "
            f"published location that other tools read: check whether the path moved too, "
            f"and update this table on purpose."
        ) from exc

    if entry.via == CALL_WITH_ROOT:
        return value(root).relative_to(root).as_posix()
    if entry.via == CALL_WITH_ROOT_AND_TASK:
        return value(root, task_id).relative_to(root).as_posix()
    if entry.via == PROJECT_PACK_TIER:
        return dict(value(root))["project"].relative_to(root).as_posix()
    if entry.via == PATH_UNDER_STATE_ROOT:
        config = importlib.import_module("rig_workbench.orchestrate.config")
        return value.relative_to(config.STATE_ROOT).as_posix()
    if entry.via == QUEUE_LOCK_BESIDE_QUEUE:
        config = importlib.import_module("rig_workbench.orchestrate.config")
        return value.with_name(value.name + ".lock").relative_to(config.STATE_ROOT).as_posix()
    if entry.via == RELATIVE_STRING:
        return str(value)
    if entry.via == BASENAME_UNDER_RIG:
        return f".rig/{value}"
    raise AssertionError(f"unknown resolution kind {entry.via!r} on {entry.rel}")


# ── driving a real run through the real CLI ──────────────────────────────────
# `git init` plus a couple of commits, measured the same way conftest measures its
# own: milliseconds on a developer machine, so the 30s floor applies in practice.
GIT_MEASURED_SECONDS = 2.0


def _git(cwd: pathlib.Path, *args: str) -> None:
    """git with the developer's own configuration kept out, exactly as `rig_git_repo` does.

    The fixture sets identity in the repository's *local* config and neutralises the
    system and global ones; a commit made from here has to do the same, or a machine
    with `commit.gpgsign = true` fails this file for a reason that is not about rig.
    """
    env = dict(os.environ,
               GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=str(cwd.parent / "absent-gitconfig"),
               GIT_TERMINAL_PROMPT="0")
    for leaked in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                   "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        env.pop(leaked, None)
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True,
                   text=True, env=env, timeout=subprocess_timeout(GIT_MEASURED_SECONDS))


def _ok(result, what: str):
    """Fail with the child's own output rather than a bare exit code."""
    if result.returncode != 0:
        pytest.fail(f"{what} exited {result.returncode}\n--- stdout ---\n{result.stdout}"
                    f"\n--- stderr ---\n{result.stderr}", pytrace=False)
    return result


def _task_id_from(stdout: str) -> str:
    """The task_id as the CLI announced it — read from output, not from the directory.

    Taking it from `.rig/runs/` would make the location tests circular: they would be
    looking for the directory in the place they learned it from.
    """
    for line in stdout.splitlines():
        if line.startswith("task_id:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(f"`wb new` printed no task_id line:\n{stdout}")


def _seed_task(rig_cli, repo: pathlib.Path, slug: str, task_type: str = "feature") -> str:
    """`wb new`, a committed change in the worktree, and the agent's `diff.md`.

    The commit matters because `accept` squash-merges the task branch, and `diff.md`
    matters because `accept` refuses without it — both are what a real task carries by
    the time somebody accepts it.
    """
    new = _ok(rig_cli("wb", "new", f"add {slug}", "--type", task_type, "--slug", slug,
                      cwd=repo), f"wb new {slug}")
    task_id = _task_id_from(new.stdout)
    task_json = repo / ".rig" / "runs" / task_id / "task.json"
    # Checked here rather than left to explode as a FileNotFoundError three lines down:
    # if the run directory has moved, every test in this file is about to fail and the
    # first message they see should say which path went missing.
    assert task_json.is_file(), (
        f"`wb new` announced {task_id} but wrote no .rig/runs/{task_id}/task.json; the run "
        f"directory has moved (found: {sorted(q.name for q in (repo / '.rig').iterdir())})")
    task = json.loads(task_json.read_text("utf-8"))
    worktree = pathlib.Path(task["worktree_path"])
    (worktree / f"{slug.replace('-', '_')}.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "-m", f"add {slug}")
    # Authored by the implementing agent in a real run; rig itself never writes it.
    (repo / ".rig" / "runs" / task_id / "diff.md").write_text(
        "## Summary\n\nAdds a constant.\n", encoding="utf-8")
    return task_id


@dataclasses.dataclass(frozen=True)
class DrivenRun:
    repo: pathlib.Path
    task_id: str

    @property
    def rig(self) -> pathlib.Path:
        return self.repo / ".rig"

    @property
    def run(self) -> pathlib.Path:
        return self.rig / "runs" / self.task_id

    def rel(self, entry: Pinned) -> str:
        return entry.rel.format(task=self.task_id)

    def path(self, entry: Pinned) -> pathlib.Path:
        return self.repo / self.rel(entry)


@pytest.fixture
def driven_run(rig_cli, rig_git_repo) -> DrivenRun:
    """One task carried all the way to `accept` through the real CLI.

    new -> (agent writes code and `diff.md`) -> gate -> review -> note -> accept. That
    is the shortest path that produces every run-dir artifact a completed task has, and
    it is what "a plain run" means everywhere in this file.
    """
    repo = rig_git_repo
    # `wb new` drops a `.gitignore` holding `.rig/` when the repo has none, and `accept`
    # refuses to run against a dirty main tree. Committing it up front is what a real
    # repository looks like on its second run, and keeps the flow deterministic.
    (repo / ".gitignore").write_text(".rig/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "ignore rig state")

    task_id = _seed_task(rig_cli, repo, "layout-contract")

    acceptance = json.loads((repo / ".rig" / "runs" / task_id / "acceptance.json")
                            .read_text("utf-8"))
    # Every criterion in one call (`--set` is repeatable), read off the run's own
    # acceptance.json so the flow does not carry a second copy of the preset list.
    sets = [arg for check in acceptance["checks"]
            for arg in ("--set", f"{check['name']}=passed:driven by the layout contract")]
    _ok(rig_cli("wb", "gate", task_id, *sets, cwd=repo), "wb gate")
    _ok(rig_cli("wb", "review", task_id, "--set", "security-reviewer=APPROVE",
                "--body", f"security-reviewer=@.rig/runs/{task_id}/diff.md", cwd=repo),
        "wb review")
    _ok(rig_cli("wb", "note", task_id, "the layout contract drove this run", cwd=repo),
        "wb note")
    _ok(rig_cli("wb", "accept", task_id, cwd=repo), "wb accept")
    return DrivenRun(repo=repo, task_id=task_id)


# ── reading what the run wrote ───────────────────────────────────────────────
def _json_object(path: pathlib.Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name} is a JSON {type(data).__name__}, not an object"
    return data


def _jsonl_rows(path: pathlib.Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{path.name} line {number} is not JSON: {exc}") from exc
        assert isinstance(row, dict), f"{path.name} line {number} is not a JSON object"
        rows.append(row)
    assert rows, f"{path.name} exists but holds no records"
    return rows


def assert_parses_and_carries_its_keys(entry: Pinned, path: pathlib.Path) -> None:
    """A file is only readable contract if it parses AND carries the keys readers use."""
    assert path.is_file(), f"{entry.rel} is missing"
    if entry.shape == "json-object":
        present = set(_json_object(path))
    elif entry.shape == "jsonl-object-rows":
        present = set().union(*(set(row) for row in _jsonl_rows(path)))
    else:
        # "text" / "opaque": existence, asserted above, is the whole contract. `diff.md`
        # is prose parsed by section heading, `provenance.key` is 32 raw bytes and the
        # lock files are deliberately empty — none of them has a key set to pin.
        return
    missing = sorted(set(entry.required_keys) - present)
    assert not missing, (
        f"{entry.rel} no longer carries {', '.join(missing)}. These are top-level keys "
        f"a reader outside this repository branches on; present: {sorted(present)}")


def _children(directory: pathlib.Path) -> set[str]:
    return {child.name for child in directory.iterdir()}


def _by_rel(table: tuple[Pinned, ...], rel: str) -> Pinned:
    for entry in table:
        if entry.rel == rel:
            return entry
    raise AssertionError(f"{rel} is not in the frozen table")


# ═════════════════════════════════════════════════════════════════════════════
# T13 — `.rig/` itself
# ═════════════════════════════════════════════════════════════════════════════
def test_a_plain_run_creates_exactly_the_top_level_entries_this_contract_freezes(driven_run):
    """The set, not just the members: an ADDED path is as much a change as a moved one.

    A new top-level file appearing under `.rig/` is a new thing every consumer's
    directory listing, backup, and `.gitignore` review has to account for, and it is
    exactly the kind of change that lands unremarked. So this compares the whole
    listing, and the diff is reported in both directions.
    """
    expected = {pathlib.PurePosixPath(entry.rel).parts[1]
                for entry in TOP_LEVEL_LAYOUT
                if entry.plain_run and entry.rel not in OPTIONAL_TOP_LEVEL}
    optional = {pathlib.PurePosixPath(rel).parts[1] for rel in OPTIONAL_TOP_LEVEL}
    observed = _children(driven_run.rig)

    unexpected = sorted(observed - expected - optional)
    absent = sorted(expected - observed)
    assert not unexpected and not absent, (
        "the top level of `.rig/` after a plain run has moved:\n"
        + "".join(f"  appeared: .rig/{name}\n" for name in unexpected)
        + "".join(f"  gone:     .rig/{name}\n" for name in absent)
        + "An appearance is a new public path (add it to TOP_LEVEL_LAYOUT deliberately); "
          "a disappearance is a path other tools may still be reading.")


def test_every_top_level_file_a_plain_run_writes_parses_and_carries_its_keys(driven_run):
    """Location is half the contract; the other half is that the file can be read.

    Every entry the run produces is opened, parsed as whatever it claims to be, and
    checked for the top-level keys a consumer branches on.
    """
    checked = []
    for entry in TOP_LEVEL_LAYOUT:
        if not entry.plain_run or entry.kind != "file":
            continue
        path = driven_run.path(entry)
        if not path.exists() and entry.rel in OPTIONAL_TOP_LEVEL:
            continue          # POSIX-only lock file; see OPTIONAL_TOP_LEVEL
        if not path.exists():
            continue          # written by an extra command, covered by its own test
        assert_parses_and_carries_its_keys(entry, path)
        checked.append(entry.rel)
    assert ".rig/runs.jsonl" in checked and ".rig/context.jsonl" in checked, (
        "the two files a plain run always writes were not checked — the loop above is "
        f"no longer reaching them (checked: {checked})")


def test_the_top_level_paths_no_plain_run_creates_still_sit_where_production_code_says(
        driven_run, monkeypatch):
    """The uncreated half of the contract: `gates.json`, `packs/`, `sources.json` and friends.

    Nothing on disk can pin these, so they are pinned against the accessor that computes
    them. That is not a tautology the way `assert module.X == module.X` would be: the
    table holds the literal path, so a rename of the accessor fails on the name and a
    move of the path fails on the value.
    """
    # `RUNS_PATH` honours $RIG_RUNS_PATH, which conftest deliberately leaves alone so a
    # test can point the per-project log somewhere else. Resolve the default here.
    monkeypatch.delenv("RIG_RUNS_PATH", raising=False)
    for entry in TOP_LEVEL_LAYOUT:
        if entry.accessor is None:
            continue
        resolved = resolve_from_production_code(entry, driven_run.repo, driven_run.task_id)
        assert resolved == entry.rel, (
            f"production code now puts {entry.accessor[0]}.{entry.accessor[1]} at "
            f"{resolved}, while this contract pins {entry.rel}. Whoever reads the old "
            "path — the VS Code extension, a CI job, somebody's script — is now reading "
            "a path rig no longer writes.")


def test_the_top_level_paths_written_only_by_an_extra_command_land_where_the_table_says(
        driven_run, rig_cli):
    """`audit.jsonl`, `ledger.jsonl`, `queue.json`, `instincts.jsonl`, `org.json`.

    None of them appear in a run that simply succeeds — they need a forced accept, an
    approval, a queued task, a recorded instinct, an org binding. Each is cheap to drive,
    so it is driven rather than asserted about: the point of a layout test is that a real
    process put the bytes there.

    Order matters and is part of what is being shown. The forced accept comes first,
    because once `govern init` binds the repo a force needs a waiver; and the tree is
    committed first, because `accept` stages its result and refuses a dirty tree.
    """
    repo = driven_run.repo
    _git(repo, "commit", "-q", "-am", "apply the accepted task")

    forced = _seed_task(rig_cli, repo, "forced-accept", task_type="refactor")
    _ok(rig_cli("wb", "accept", forced, "--force", cwd=repo), "wb accept --force")
    _ok(rig_cli("queue", "add", "a queued probe task", cwd=repo), "queue add")
    _ok(rig_cli("wb", "instincts", "--add", "prefer pathlib over os.path in this repo",
                "--evidence", "recorded by the layout contract", cwd=repo), "wb instincts --add")
    _ok(rig_cli("govern", "approve", "grant", driven_run.task_id,
                "--note", "recorded by the layout contract", cwd=repo), "govern approve")
    _ok(rig_cli("govern", "init", "--org", "acme", "--team", "team-a", cwd=repo), "govern init")

    for rel in (".rig/audit.jsonl", ".rig/ledger.jsonl", ".rig/queue.json",
                ".rig/instincts.jsonl", ".rig/org.json", ".rig/policy/org.json"):
        entry = _by_rel(TOP_LEVEL_LAYOUT, rel)
        assert_parses_and_carries_its_keys(entry, repo / rel)

    # The starter policy layer has no accessor of its own; the binding names it, and that
    # string is what a second checkout of the same org has to reproduce.
    binding = _json_object(repo / ".rig" / "org.json")
    assert binding["policy_layers"] == [".rig/policy/org.json"], (
        "`govern init` no longer points the org binding at .rig/policy/org.json "
        f"(it wrote {binding['policy_layers']})")


def test_the_person_authored_top_level_config_is_read_from_its_frozen_location(
        rig_cli, rig_git_repo):
    """`gates.json` and `access.json` are written by people, so behaviour is the pin.

    Nothing rig runs creates either file, and neither has a named accessor for the whole
    path — `access.json` is an inline literal in three modules. What can be shown is the
    thing that actually matters to a team: a file placed at the frozen path changes what
    the CLI does. Put one there and the gate grows a criterion; put the other there and
    `accept` refuses the identity running it.
    """
    repo = rig_git_repo
    (repo / ".rig").mkdir()

    gates = _by_rel(TOP_LEVEL_LAYOUT, ".rig/gates.json")
    (repo / gates.rel).write_text(json.dumps(
        {"extra_criteria": {"feature": ["layout_contract_probe"]},
         "descriptions": {"layout_contract_probe": "a criterion this test added"}}),
        encoding="utf-8")
    listed = _ok(rig_cli("wb", "gates", cwd=repo), "wb gates").stdout
    assert "layout_contract_probe" in listed and gates.rel in listed, (
        f"a criterion written to {gates.rel} did not reach `wb gates`; the project gate "
        f"config is being read from somewhere else now:\n{listed}")

    access = _by_rel(TOP_LEVEL_LAYOUT, ".rig/access.json")
    (repo / access.rel).write_text(json.dumps({"default": ["somebody-else"]}), encoding="utf-8")
    task_id = _task_id_from(_ok(rig_cli("wb", "new", "a task nobody may accept",
                                        "--type", "bugfix", "--slug", "rbac-probe",
                                        cwd=repo), "wb new").stdout)
    refused = rig_cli("wb", "accept", task_id, cwd=repo)
    assert refused.returncode != 0, "an allowlist that excludes the caller did not stop accept"
    assert access.rel in refused.stdout + refused.stderr, (
        f"accept refused, but without naming {access.rel} — the allowlist it read may be "
        f"a different file:\n{refused.stdout}\n{refused.stderr}")


def test_renaming_a_top_level_path_accessor_is_what_makes_this_contract_fail(rig_git_repo):
    """Proof that the accessor half of this file bites, rather than merely being written.

    A rename inside the repository moves every caller with it and leaves every
    in-process assertion passing — which is exactly why this file resolves accessors by
    NAME. Take the name away and the resolver has to fail, loudly and about the rename.
    """
    entry = _by_rel(TOP_LEVEL_LAYOUT, ".rig/runs")
    module = importlib.import_module(entry.accessor[0])
    assert hasattr(module, entry.accessor[1]), (
        f"{entry.accessor[0]}.{entry.accessor[1]} is already gone, so this proof has "
        "nothing to remove — the rename it exists to demonstrate has happened for real")
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.delattr(module, entry.accessor[1])
        with pytest.raises(AssertionError, match="no longer defines"):
            resolve_from_production_code(entry, rig_git_repo, "rig-000-none")
    finally:
        monkeypatch.undo()
    # And with the name back, the same call resolves to the pinned path again.
    assert resolve_from_production_code(entry, rig_git_repo, "rig-000-none") == entry.rel


def test_every_top_level_path_without_an_accessor_is_pinned_some_other_way():
    """An entry may have no named accessor — it may not have no pin at all.

    Keeps the table honest as it grows: adding a path with `accessor=None` and no
    corresponding on-disk or behavioural check would leave a line that looks pinned and
    is not.
    """
    #: Entries with no accessor, each naming the test that pins it instead.
    pinned_another_way = {
        ".rig/policy/org.json": "the org binding names it (extra-command test)",
        ".rig/access.json": "accept refuses the caller (person-authored config test)",
    }
    accessorless = {entry.rel for entry in TOP_LEVEL_LAYOUT if entry.accessor is None}
    assert accessorless == set(pinned_another_way), (
        "an entry in TOP_LEVEL_LAYOUT has no accessor and no stated alternative pin: "
        f"{sorted(accessorless - set(pinned_another_way))}")


def test_the_top_level_and_run_dir_gaps_named_in_not_pinned_each_state_their_reason():
    """`NOT_PINNED` is part of the contract too: it says where the contract stops."""
    assert NOT_PINNED, "NOT_PINNED was emptied; a file that pins everything owes a note saying so"
    for subject, reason in NOT_PINNED.items():
        assert len(reason) > 40, f"the reason given for {subject!r} does not say why"


# ═════════════════════════════════════════════════════════════════════════════
# T14 — `.rig/runs/<task_id>/`
# ═════════════════════════════════════════════════════════════════════════════
def test_a_plain_run_fills_its_run_dir_with_exactly_the_files_this_contract_freezes(driven_run):
    """The per-task directory is the surface other tools read most.

    `vscode-extension/src/rigState.ts` walks `.rig/runs/` and opens what it finds by
    name; so does every `jq` line somebody has in a CI job. Compare the whole listing.
    """
    expected = {pathlib.PurePosixPath(driven_run.rel(entry)).name
                for entry in RUN_DIR_LAYOUT
                if entry.plain_run and entry.rel != ".rig/runs/{task}"}
    observed = _children(driven_run.run)
    unexpected = sorted(observed - expected)
    absent = sorted(expected - observed)
    assert not unexpected and not absent, (
        f"the contents of .rig/runs/{driven_run.task_id}/ have moved:\n"
        + "".join(f"  appeared: {name}\n" for name in unexpected)
        + "".join(f"  gone:     {name}\n" for name in absent))


def test_the_run_dir_sits_under_runs_named_by_the_task_id_the_cli_announced(driven_run):
    """The directory's own location, resolved through `state.run_dir` and through the id.

    The task_id comes from what `wb new` printed, so this checks the announced id and the
    directory name are the same string — which is the assumption every external reader
    makes when it maps a run to a directory.
    """
    entry = _by_rel(RUN_DIR_LAYOUT, ".rig/runs/{task}")
    resolved = resolve_from_production_code(entry, driven_run.repo, driven_run.task_id)
    assert resolved == f".rig/runs/{driven_run.task_id}"
    assert driven_run.run.is_dir(), "the announced task_id does not name a directory"
    assert driven_run.run.parent == driven_run.repo / ".rig" / "runs"


def test_every_json_file_in_the_run_dir_carries_the_keys_its_readers_branch_on(
        driven_run):
    """task.json / steps.json / acceptance.json / review.json / handoff.json / provenance.json."""
    for entry in RUN_DIR_LAYOUT:
        if entry.shape != "json-object" or not entry.plain_run:
            continue
        path = driven_run.path(entry)
        if not path.exists():
            continue          # approvals.json needs `govern approve`; its own test drives it
        assert_parses_and_carries_its_keys(entry, path)

    # The two the whole flow turns on, spelled out so a silently emptied loop above cannot
    # pass: the task's own record, and the gate the accept was judged against.
    task = _json_object(driven_run.run / "task.json")
    assert task["task_id"] == driven_run.task_id
    assert task["status"] == "accepted", "the driven run did not end accepted"
    assert _json_object(driven_run.run / "acceptance.json")["status"] in (
        "passed", "passed_with_warnings")


def test_the_prose_and_directory_artifacts_of_the_run_dir_are_where_the_table_says(driven_run):
    """`diff.md` and `reviews/` — the two entries that are not JSON.

    `diff.md` is written by the implementing agent, not by rig, which makes its path a
    contract in the strongest sense: rig only ever *requires* it there. `reviews/` holds
    one file per persona, named by persona, because the verdict labels in review.json
    throw away the file:line evidence the bodies carry.
    """
    assert (driven_run.run / "diff.md").read_text(encoding="utf-8").startswith("## Summary")
    reviews = driven_run.run / "reviews"
    assert reviews.is_dir()
    assert _children(reviews) == {"security-reviewer.md"}, (
        "`wb review --body <persona>=@…` no longer files the body as "
        f"reviews/<persona>.md (found {sorted(_children(reviews))})")


def test_accept_names_the_frozen_run_dir_diff_md_path_when_the_agent_has_not_written_one(
        rig_cli, rig_git_repo):
    """The location of an agent-authored file, pinned from the side rig owns.

    Nothing rig runs creates `diff.md`, so the way to show where rig expects it is to
    not write it: `accept` then refuses with a structural precondition, and the message
    names the exact path. That message is also what a person acts on, so it carrying the
    real path is contract in its own right.
    """
    repo = rig_git_repo
    task_id = _task_id_from(_ok(rig_cli("wb", "new", "a task with no diff summary",
                                        "--type", "feature", "--slug", "no-diff",
                                        cwd=repo), "wb new").stdout)
    refused = rig_cli("wb", "accept", task_id, cwd=repo)
    assert refused.returncode != 0, "accept succeeded without a diff summary"
    wanted = _by_rel(RUN_DIR_LAYOUT, ".rig/runs/{task}/diff.md").rel.format(task=task_id)
    assert wanted in refused.stdout + refused.stderr, (
        f"accept refused without naming {wanted}; the path it requires a diff summary at "
        f"has moved:\n{refused.stdout}\n{refused.stderr}")


def test_an_approval_lands_in_the_run_dir_at_the_path_govern_resolves(driven_run, rig_cli):
    """`approvals.json` needs one extra command, so drive it and check both halves.

    `govern.approval.approvals_path` is the accessor, and `govern approve` is the command;
    asserting the accessor alone would leave the two free to drift apart.
    """
    entry = _by_rel(RUN_DIR_LAYOUT, ".rig/runs/{task}/approvals.json")
    assert not driven_run.path(entry).exists(), (
        "approvals.json now exists after a plain run — it is no longer 'extra command' "
        "in the table above, and the run-dir listing test needs updating with it")

    _ok(rig_cli("govern", "approve", "grant", driven_run.task_id,
                "--note", "recorded by the layout contract", cwd=driven_run.repo),
        "govern approve")

    resolved = resolve_from_production_code(entry, driven_run.repo, driven_run.task_id)
    assert resolved == driven_run.rel(entry)
    assert_parses_and_carries_its_keys(entry, driven_run.path(entry))
    decisions = _json_object(driven_run.path(entry))["decisions"]
    assert decisions and decisions[0]["decision"] == "approve"
