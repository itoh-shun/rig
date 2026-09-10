"""What the real `rig-wb` process actually prints, pinned schema id by schema id.

tests/test_schema_registry.py pins which `rig.<name>/v<N>` strings *exist* in the source
tree: it parses every module and compares the set of literals against a frozen list. That
answers "was an id renamed or dropped", and it deliberately answers nothing else — a
literal can sit in a module that no command reaches, and a command can stop emitting its
document entirely while the constant it used to stamp on it stays exactly where it was.

This file answers the other half, and it is the half a consumer depends on: run the command
through `python -m rig_workbench.cli` (or the console-script module, for the two entry
points that are not `rig-wb` subcommands), read what comes back, and hold it to the schema
id and the top-level keys. Nothing here imports the constant it is checking. Comparing
`payload["schema"]` to `assurance.SCHEMA` would pass whatever both of them were renamed to,
which is the tautology test_schema_registry.py's own docstring calls out. So every expected
id below is a string literal, written out the way somebody else's `if` statement writes it.

Two rules the assertions follow, both about what a break actually costs a consumer:

* **The schema id is exact.** It is the one field a reader is told to branch on, and a
  reader that does not recognise `/v2` is supposed to refuse the payload rather than
  half-understand it. Approximate is no use to it.
* **The key set is a floor, not a photograph.** `REQUIRED <= set(payload)` — a new key is
  something a consumer ignores, and a missing key is something it reads as `None` and acts
  on. Pinning the exact set would turn every additive change into a failure and teach the
  next person to edit this file without reading it.

Three groups, selectable on the command line, because the surfaces answer to different
audiences and a rewrite tends to break one of them at a time:

    pytest tests/test_schema_cli_contract.py -k group1   # acceptance surface
    pytest tests/test_schema_cli_contract.py -k group2   # judgement surface
    pytest tests/test_schema_cli_contract.py -k group3   # organisational + external

Several group2 commands are a judgement *about* a document the caller wrote. Both ends of
that are contract — a caller has to know which id its input must carry as much as which id
comes back — so those tests pin the input id in the file they author and the output id in
what the command prints, and a change to either is a break somebody has to hear about.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


# ── the ids this file does not reach, and why ────────────────────────────────
#: An honest gap beats a test that pins the wrong thing. Each entry is an id in
#: test_schema_registry.py's frozen set that no command in T8–T10's surface emits, with the
#: code path that does emit it. None of the three is refused by the CLI; each is produced by
#: `rig-mission-control-live`, which is an HTTP server on localhost rather than a command
#: that prints a document, and driving it here would mean binding a port and polling a
#: worker loop — a different kind of test from this one, not a harder version of it.
NOT_PINNED = (
    {
        "schema": "rig.assurance-graph/v1",
        "command": "(none)",
        "reason": "built by rig_workbench/workbench/graph.py:build_graph, whose only caller "
                  "in the tree is rig_workbench/mission_server.py — the `rig-mission-control-live` "
                  "localhost server. No `rig-wb` subcommand prints this document, so reaching it "
                  "would mean starting a server and issuing an HTTP request.",
    },
    {
        "schema": "rig.queue-dependencies/v1",
        "command": "(none)",
        "reason": "rig_workbench/orchestrate/dependencies.py stamps it on the verdict `resolve` "
                  "returns and on `graph`'s output. `queueing.resolve_dependencies` consumes the "
                  "verdict and stores only its `state` and `reason`, and `queueing.dependency_graph` "
                  "has one caller: mission_server. `rig-wb queue` has add|list|go|done|retry|cancel "
                  "and no subcommand that emits either document.",
    },
    {
        "schema": "rig.mission-worker/v1",
        "command": "(none)",
        "reason": "rig_workbench/mission_jobs.py writes it as the worker's own record inside the "
                  "`rig-mission-control-live` worker loop. There is no `rig-wb` subcommand that "
                  "prints a worker record.",
    },
)


# ── running the real process ─────────────────────────────────────────────────
# `rig_cli` covers `python -m rig_workbench.cli`, which is every `rig-wb` subcommand. Two of
# group3's surfaces are not subcommands at all: pyproject.toml declares `rig-evidence` and
# `rig-mission-control` as their own console scripts, bound to `rig_workbench.evidence:main`
# and `rig_workbench.mission_control:main`. A user's shell reaches those without going
# through `rig-wb`, so a test that reached them through an import would not be testing the
# thing that ships. This runs the module the console script names, with the same child
# environment `rig_cli` builds — repo root ahead of any installed rig-wb, UTF-8 pinned both
# ways — and is otherwise deliberately the same fixture.
ENTRY_POINTS = {
    # console script -> the module `pyproject.toml` binds it to.
    "rig-evidence": "rig_workbench.evidence",
    "rig-mission-control": "rig_workbench.mission_control",
}

#: Measured rather than guessed, the way conftest asks: `rig-mission-control --json` in a
#: scratch repo is the slower of the two at 0.4s, well under the 30s floor conftest applies.
ENTRY_POINT_TIMEOUT = 30.0


@pytest.fixture
def rig_entry_point():
    """Run one of rig's other console scripts; return the CompletedProcess unjudged.

        result = rig_entry_point("rig-evidence", "summary", "--json", cwd=repo)

    Unjudged for `rig_cli`'s reason: `rig-evidence fleet` exits 1 on a fleet error while
    still printing its answer, so a fixture that raised would hide the document under a
    CalledProcessError.
    """

    def run(entry_point, *args, cwd):
        child_env = dict(
            os.environ,
            PYTHONPATH=os.pathsep.join(
                p for p in (str(REPO_ROOT), os.environ.get("PYTHONPATH")) if p),
            PYTHONIOENCODING="utf-8",
            PYTHONUTF8="1",
        )
        return subprocess.run(
            [sys.executable, "-m", ENTRY_POINTS[entry_point], *(str(a) for a in args)],
            cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=child_env, timeout=ENTRY_POINT_TIMEOUT)

    return run


# ── assertions ───────────────────────────────────────────────────────────────
def assert_document(payload, *, schema, required, what):
    """Hold one emitted document to its id and to the keys a consumer reads off it."""
    assert isinstance(payload, dict), f"{what} emitted {type(payload).__name__}, not a document"
    assert payload.get("schema") == schema, (
        f"{what} emitted schema {payload.get('schema')!r}, expected {schema!r}. This string is "
        f"what a consumer's `if` compares against; renaming it breaks every reader outside "
        f"this repository while every in-repo assertion comparing a constant to itself passes.")
    missing = sorted(set(required) - set(payload))
    assert not missing, (
        f"{what} no longer emits {', '.join(missing)}. A consumer reads a dropped key as null "
        f"and acts on it; adding keys is fine, removing them is not.\n"
        f"emitted: {sorted(payload)}")


def read_json_file(path, *, what):
    """Load a document the CLI wrote to disk, failing with the path rather than a decode error."""
    path = pathlib.Path(path)
    assert path.is_file(), f"{what} did not write {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        pytest.fail(f"{what} wrote {path}, which is not JSON: {exc}\n"
                    f"--- contents ---\n{path.read_text(encoding='utf-8')}", pytrace=False)


def write_json(path, payload):
    """Author one of the caller-supplied input documents these commands judge."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _git(repo, *args):
    """Run git in `repo` with the same hygiene `rig_git_repo` builds the repository under.

    The identity is already in the repository's own config; what has to be repeated is the
    part that keeps the developer's machine out — no system config, a global config pointed
    at a file that does not exist, and the GIT_AUTHOR_*/GIT_COMMITTER_* overrides dropped —
    so a host with `commit.gpgsign = true` and an unreachable key does not fail this file
    for a reason that has nothing to do with rig.
    """
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=str(pathlib.Path(repo).parent / "absent-gitconfig"),
               GIT_TERMINAL_PROMPT="0")
    for leaked in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                   "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        env.pop(leaked, None)
    return subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True,
                          text=True, env=env, timeout=30.0).stdout.strip()


def _external_commit(repo):
    """A commit that is not the branch `wb import` will measure against, and its SHA.

    Stands in for the change an outside orchestrator produced: made on its own branch, with
    the working branch checked back out, so `--head` and `--base` name different objects.
    """
    _git(repo, "checkout", "-q", "-b", "external-work")
    (pathlib.Path(repo) / "external.txt").write_text("produced elsewhere\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "an external change")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    return head


# ── shared state ─────────────────────────────────────────────────────────────
@pytest.fixture
def task(rig_git_repo, rig_cli):
    """A registered task in `rig_git_repo`, and its id.

    `wb new` is what puts `.rig/runs/<id>/task.json` on disk, and receipt / contract /
    assurance-target / dev-loop all read a task record rather than inventing one. The id is
    read back out of the state directory instead of parsed out of the banner, because the
    banner is prose for a person and is not what this file is pinning.
    """
    result = rig_cli("wb", "new", "pin the schema cli contract", "--type", "feature",
                     "--slug", "pin-contract", cwd=rig_git_repo)
    assert result.returncode == 0, (
        f"`wb new` exited {result.returncode}\n--- stdout ---\n{result.stdout}"
        f"\n--- stderr ---\n{result.stderr}")
    [run_dir] = sorted((rig_git_repo / ".rig" / "runs").iterdir())
    return rig_git_repo, run_dir.name


# ══ group1 — the acceptance surface ══════════════════════════════════════════
# What an orchestrator asks rig when it wants to know whether a change may land: which
# criteria exist, what the run recorded, whether the answer is acceptable, and whether it
# is what was asked for.

def test_group1_wb_gates_emits_rig_gates_v1_inside_the_envelope_a_json_caller_branches_on(
        rig_git_repo, rig_cli_json):
    """`wb gates` is the one command already on `jsonio.envelope`, and the envelope is a
    contract in its own right: `schema` to dispatch on, `status` so a caller that captured
    stdout need not also have captured `$?`, `data` for everything else. All three are
    pinned here, and so is the fact that the criteria live under `data`."""
    payload = rig_cli_json("wb", "gates", "--json", cwd=rig_git_repo, expect_returncode=0)
    assert_document(payload, schema="rig.gates/v1", required={"schema", "status", "data"},
                    what="`wb gates --json`")
    assert payload["status"] == "ok"
    assert {"presets", "task_types"} <= set(payload["data"]), (
        "SKILL.md names `wb gates` as the source of truth for acceptance-criteria ids; "
        f"they are read out of data.presets / data.task_types, and data now holds "
        f"{sorted(payload['data'])}")


def test_group1_wb_receipt_emits_rig_assurance_receipt_v1_with_every_block_the_portable_record_carries(
        task, rig_cli_json):
    """The receipt is the artifact that leaves the machine — the thing a consumer reads to
    learn what a run actually did. Each top-level block is a separate claim (who produced
    it, who verified it, how it was isolated, what the gate ruled), so dropping one is not a
    tidy-up: it is a claim silently no longer made."""
    repo, task_id = task
    payload = rig_cli_json("wb", "receipt", task_id, "--json", cwd=repo, expect_returncode=0)
    assert_document(
        payload, schema="rig.assurance-receipt/v1",
        required={"schema", "generated_at", "task", "target", "producer", "verifier",
                  "isolation", "gates", "approvals", "provenance", "evidence", "sources",
                  "intent", "assurance_target", "final_status"},
        what="`wb receipt --json`")


def test_group1_wb_contract_emits_rig_assurance_contract_v1_the_machine_answer_an_orchestrator_acts_on(
        task, rig_cli_json):
    """`wb contract` exists so an external orchestrator has one place to read
    acceptable / not-acceptable / pending / execution-error. `status` and `final_status` are
    what it branches on and `receipt` is where it goes for the detail, so all three are
    contract even on a task that has not been through its gate yet."""
    repo, task_id = task
    payload = rig_cli_json("wb", "contract", task_id, "--json", cwd=repo)
    assert_document(
        payload, schema="rig.assurance-contract/v1",
        required={"schema", "status", "task_id", "final_status", "reason", "verified_head",
                  "verified_head_immutable", "target_moved", "imported", "producer",
                  "gate_status", "receipt"},
        what="`wb contract --json`")
    assert payload["task_id"] == task_id


def test_group1_wb_assurance_target_reads_and_emits_rig_assurance_target_v1_asked_beside_recorded(
        task, rig_cli_json, tmp_path):
    """One id on both ends, which is the point of the command: the caller writes what was
    asked for as a `rig.assurance-target/v1` document, and the answer is the same schema
    carrying `asked` beside `recorded`. Keeping them apart is the whole contract — `asked`
    is the comparison rig just made, `recorded` is what the run had written down — so a
    reader that finds only one of them cannot tell which it is holding."""
    repo, task_id = task
    target = write_json(tmp_path / "assurance-target.json",
                        {"schema": "rig.assurance-target/v1", "axes": {"gate": "passed"}})
    payload = rig_cli_json("wb", "assurance-target", task_id, target, "--json", cwd=repo)
    assert_document(payload, schema="rig.assurance-target/v1",
                    required={"schema", "asked", "recorded"},
                    what="`wb assurance-target --json`")
    assert {"axes", "status", "met", "unmet", "unobservable", "observed"} <= set(payload["asked"])


# ══ group2 — the judgement surface ═══════════════════════════════════════════
# Commands that take a document somebody else authored and say what is true of it. The
# input id is as much a contract as the output id: a caller that stamps the wrong one on
# its file is refused, so both ends are pinned here.

INTENT_CONTRACT = {
    "schema": "rig.intent-contract/v1",
    "goal": "ship the thing",
    "requirements": [{"text": "tests must pass", "origin": "explicit-user",
                      "source": "issue #1", "evidence": ["tests_pass_or_explained"]}],
}


def test_group2_wb_intent_accepts_and_answers_in_rig_intent_contract_v1(
        rig_git_repo, rig_cli_json, tmp_path):
    """`wb intent` validates the contract and reports what it leaves unchecked or
    undeclared. `unchecked` and `undeclared` are the two numbers the command exists to
    produce — a requirement with nothing to verify it, and one rig concluded rather than
    was told — so they are pinned beside the id."""
    contract = write_json(tmp_path / "intent.json", INTENT_CONTRACT)
    payload = rig_cli_json("wb", "intent", contract, "--json", cwd=rig_git_repo,
                           expect_returncode=0)
    assert_document(
        payload, schema="rig.intent-contract/v1",
        required={"schema", "status", "requirements", "unchecked", "undeclared",
                  "open_ambiguities"},
        what="`wb intent --json`")


def test_group2_wb_intent_derive_floor_answers_in_rig_intent_contract_v1_with_the_steps_and_the_misses(
        rig_git_repo, rig_cli_json, tmp_path):
    """`--floor` turns declared requirements into the steps a workflow may not drop.
    `unmatched` is not decoration: evidence naming something outside the catalog may be a
    test id or may be a misspelled component, and the command reports it rather than
    silently dropping it, so a caller reads that list."""
    contract = write_json(tmp_path / "intent.json", INTENT_CONTRACT)
    catalog = write_json(tmp_path / "catalog.json", ["tests_pass_or_explained", "review-diff"])
    payload = rig_cli_json("wb", "intent-derive", contract, "--against", catalog, "--floor",
                           "--json", cwd=rig_git_repo, expect_returncode=0)
    assert_document(payload, schema="rig.intent-contract/v1",
                    required={"schema", "floor", "unmatched"},
                    what="`wb intent-derive --floor --json`")


def test_group2_wb_intent_derive_target_nests_a_rig_assurance_target_v1_document_it_did_not_invent(
        rig_git_repo, rig_cli_json, tmp_path):
    """The `--target` answer is the one place in group2 where the emitted document is
    nested rather than top-level, and that is deliberate in the source: the report carries
    `because` and `unaskable` beside the target, and labelling the whole thing
    `rig.assurance-target/v1` would make a document `assurance_target.validate` refuses.
    So the id is pinned where it actually lives, on `target`, and the report's own keys are
    pinned as the report's."""
    contract = write_json(tmp_path / "intent.json", INTENT_CONTRACT)
    criteria = write_json(tmp_path / "criteria.json", ["tests_pass_or_explained", "review-diff"])
    payload = rig_cli_json("wb", "intent-derive", contract, "--against", criteria, "--target",
                           "--json", cwd=rig_git_repo, expect_returncode=0)
    assert {"target", "because", "unaskable"} <= set(payload), sorted(payload)
    assert_document(payload["target"], schema="rig.assurance-target/v1",
                    required={"schema", "axes"},
                    what="the target `wb intent-derive --target --json` nests")


def test_group2_wb_assurance_derive_reads_and_answers_in_rig_assurance_target_v1(
        rig_git_repo, rig_cli_json, tmp_path):
    """A target in, the floor it needs out, under the same id. `unreachable` is the axis
    values the caller's mapping cannot plan for; a floor without it would read as complete
    when it is only as complete as the mapping it was given."""
    target = write_json(tmp_path / "target.json",
                        {"schema": "rig.assurance-target/v1", "axes": {"gate": "passed"}})
    requires = write_json(tmp_path / "requires.json", {
        "gate": {"passed": [{"id": "acceptance", "source": "policy-required",
                             "reason": "the gate has to run"}]}})
    catalog = write_json(tmp_path / "catalog.json", ["acceptance"])
    payload = rig_cli_json("wb", "assurance-derive", target, "--requires", requires,
                           "--against", catalog, "--json", cwd=rig_git_repo,
                           expect_returncode=0)
    assert_document(payload, schema="rig.assurance-target/v1",
                    required={"schema", "floor", "unreachable"},
                    what="`wb assurance-derive --json`")


def test_group2_wb_knowledge_candidate_reads_two_ids_and_answers_in_a_third(
        rig_git_repo, rig_cli_json, tmp_path):
    """Three ids in one command, and a caller needs all three. The candidate it submits is
    `rig.knowledge-candidate/v1`; every record that candidate cites has to be inside a
    `rig.knowledge-candidate-evidence/v1` envelope, which is why the evidence file is
    written with that id here rather than as a bare list; and the answer comes back as
    `rig.knowledge-candidate-assessment/v1`, a name distinct from both because it is an
    assessment *of* a candidate and not a candidate."""
    write_json(tmp_path / "evidence.json", {
        "schema": "rig.knowledge-candidate-evidence/v1",
        "records": [{"id": "obs-1",
                     "observation": "the gate caught an unrelated refactor twice",
                     "applicable_context": ["python"],
                     "proposed_rules": ["keep refactors out of a bugfix diff"],
                     "observed_benefits": ["smaller diffs to review"],
                     "known_exceptions": ["a rename the fix requires"],
                     "scope": ["this repository"]}]})
    candidate = write_json(tmp_path / "candidate.json", {
        "schema": "rig.knowledge-candidate/v1",
        "triggering_evidence": [{"path": "evidence.json", "record": "obs-1"}],
        "applicable_context": ["python"],
        "proposed_rule": "keep refactors out of a bugfix diff",
        "expected_benefit": "smaller diffs to review",
        "confidence": 0.5,
        "evidence_count": 1,
        "known_exceptions": ["a rename the fix requires"],
        "scope": ["this repository"]})
    payload = rig_cli_json("wb", "knowledge-candidate", candidate, "--json", cwd=rig_git_repo,
                           expect_returncode=0)
    assert_document(
        payload, schema="rig.knowledge-candidate-assessment/v1",
        required={"schema", "status", "evidence", "unsupported", "unobservable", "confidence",
                  "guarantee", "does_not_guarantee"},
        what="`wb knowledge-candidate --json`")
    assert payload["status"] == "supported"


def _change_graph_node(node_id, repository, component, revision):
    return {"id": node_id, "repository": repository, "component": component,
            "base": "git:" + "0" * 40, "target": "git:" + revision * 40,
            "required_change": f"change {component}",
            "assurance_target": f"assurance:{node_id}", "status": "planned"}


def test_group2_wb_change_graph_reads_rig_change_graph_v1_and_answers_in_change_graph_assessment_v1(
        rig_git_repo, rig_cli_json, tmp_path):
    """Two distinct ids, and the distinction is load-bearing: the graph is what the caller
    plans, the assessment is what rig concluded about it, and feeding one back as the other
    would be refused. `stages` is the execution order and `cycles` is why there is none, so
    a reader needs both keys present to tell an answer from a refusal."""
    graph = write_json(tmp_path / "graph.json", {
        "schema": "rig.change-graph/v1", "id": "cross-repo-1",
        "nodes": [_change_graph_node("db", "acme/db", "migration", "1"),
                  _change_graph_node("api", "acme/api", "service", "2")],
        "dependencies": [{"id": "db-before-api", "kind": "migration-before",
                          "predecessor": "db", "successor": "api",
                          "compatibility": {"requirement": "api accepts schema v2",
                                            "status": "satisfied",
                                            "evidence": "contract:test-api-schema-v2"}}]})
    payload = rig_cli_json("wb", "change-graph", graph, "--json", cwd=rig_git_repo,
                           expect_returncode=0)
    assert_document(
        payload, schema="rig.change-graph-assessment/v1",
        required={"schema", "status", "stages", "cycles", "unmet", "unobservable",
                  "rejected_nodes", "unobservable_nodes", "guarantee", "does_not_guarantee"},
        what="`wb change-graph --json`")
    assert payload["stages"] == [["db"], ["api"]]


def test_group2_wb_anomaly_trigger_reads_two_ids_and_answers_in_the_trigger_assessment_id(
        rig_git_repo, rig_cli_json, tmp_path):
    """The event a monitor hands in is `rig.production-anomaly-event/v1`, the records it
    cites live in a `rig.production-anomaly-evidence/v1` envelope, and what comes back is
    `rig.production-anomaly-trigger-assessment/v1`. `claims` is where the event's own words
    about severity and confidence stay, each next to `verified: null`; a consumer that
    found them anywhere else would be reading a claim as a finding."""
    write_json(tmp_path / "anomaly-evidence.json", {
        "schema": "rig.production-anomaly-evidence/v1",
        "records": [{"id": "sample-123",
                     "source": {"system": "external-monitor", "event": "evt-123"},
                     "observed_at": "2026-08-27T09:14:00+00:00",
                     "observations": ["checkout 5xx rate was 4.2 pct"],
                     "comparisons": ["checkout 5xx rate baseline was 0.4 pct"],
                     "environments": ["production"],
                     "components": ["checkout-api"]}]})
    event = write_json(tmp_path / "anomaly-event.json", {
        "schema": "rig.production-anomaly-event/v1",
        "id": "checkout-api-2026-08-27T09:15:00Z",
        "source": {"system": "external-monitor", "event": "evt-123"},
        "detected_at": "2026-08-27T09:16:00+00:00",
        "window": {"opens": "2026-08-27T09:10:00+00:00",
                   "closes": "2026-08-27T09:15:00+00:00"},
        "signal": {"kind": "error-rate-regression",
                   "observation": "checkout 5xx rate was 4.2 pct",
                   "comparison": "checkout 5xx rate baseline was 0.4 pct"},
        "scope": {"environment": "production", "components": ["checkout-api"]},
        "evidence": [{"path": "anomaly-evidence.json", "record": "sample-123"}],
        "severity": "high", "confidence": 0.82})
    payload = rig_cli_json("wb", "anomaly-trigger", event, "--json", cwd=rig_git_repo,
                           expect_returncode=0)
    assert_document(
        payload, schema="rig.production-anomaly-trigger-assessment/v1",
        required={"schema", "status", "event", "evidence", "claims", "unmet", "unobservable",
                  "guarantee", "does_not_guarantee"},
        what="`wb anomaly-trigger --json`")
    assert payload["claims"]["severity"] == {"claimed": "high", "verified": None}


def test_group2_wb_synthesise_reads_rig_resolved_workflow_v1_and_answers_in_workflow_resolution_v1(
        rig_git_repo, rig_cli_json, tmp_path):
    """The report is not a proposal and carries its own id, with the workflow-shaped half
    nested under `workflow` still stamped `rig.resolved-workflow/v1` so it round-trips.
    Collapsing the two would make `corrections` a key the workflow schema does not define,
    and the round trip would be refused for a reason the caller could do nothing about."""
    workflow = write_json(tmp_path / "workflow.json", {
        "schema": "rig.resolved-workflow/v1",
        "steps": [{"id": "implement", "source": "planner-proposed",
                   "reason": "the change has to be written"},
                  {"id": "review-diff", "source": "policy-required", "reason": "org policy"}]})
    catalog = write_json(tmp_path / "catalog.json", ["implement", "review-diff", "security-audit"])
    required = write_json(tmp_path / "required.json", {"review-diff": "org policy"})
    payload = rig_cli_json("wb", "synthesise", workflow, catalog, "--required", required,
                           "--json", cwd=rig_git_repo, expect_returncode=0)
    assert_document(payload, schema="rig.workflow-resolution/v1",
                    required={"schema", "workflow", "floor_held", "corrections"},
                    what="`wb synthesise --json`")
    assert_document(payload["workflow"], schema="rig.resolved-workflow/v1",
                    required={"schema", "steps"},
                    what="the workflow `wb synthesise --json` nests")


def test_group2_wb_synthesise_answers_a_failure_to_read_in_rig_workflow_resolution_error_v1(
        rig_git_repo, rig_cli_json, tmp_path):
    """A third id, and it exists precisely so a caller can tell "rig could not read your
    files" from "rig read them and refused them". An error wearing the report's id would be
    parsed as a resolution whose `floor_held` is missing, which reads as false."""
    catalog = write_json(tmp_path / "catalog.json", ["implement"])
    payload = rig_cli_json("wb", "synthesise", tmp_path / "absent.json", catalog, "--json",
                           cwd=rig_git_repo, expect_returncode=2)
    assert_document(payload, schema="rig.workflow-resolution-error/v1",
                    required={"schema", "status", "error"},
                    what="`wb synthesise --json` over a file that is not there")
    assert payload["status"] == "execution-error"


def test_group2_wb_dev_loop_reads_and_answers_in_rig_development_cycles_v1(
        task, rig_cli_json, tmp_path):
    """One id on both ends. `self_reported` is kept apart from `status` on purpose — the
    loop's own account of itself is not the verdict — so a consumer that lost that key
    would be reading a claim as a judgement."""
    repo, task_id = task
    cycles = write_json(tmp_path / "cycles.json", {
        "schema": "rig.development-cycles/v1", "task": task_id,
        "goal": "pin the schema cli contract",
        "cycles": [{"index": 0, "state": "implement", "product": "0" * 40,
                    "failure": None, "rationale": "", "producer": ""}]})
    payload = rig_cli_json("wb", "dev-loop", task_id, cycles, "--json", cwd=repo)
    assert_document(payload, schema="rig.development-cycles/v1",
                    required={"schema", "status", "reasons", "self_reported", "target",
                              "products_related"},
                    what="`wb dev-loop --json`")


def test_group2_wb_route_team_reads_and_answers_in_rig_team_routing_v1(
        rig_git_repo, rig_cli_json, tmp_path):
    """`reported_unmeasured` is separate from `violations` because it is the record's own
    word about itself rather than the policy's finding, and the command prints it after the
    verdict for the same reason."""
    routing = write_json(tmp_path / "routing.json", {
        "schema": "rig.team-routing/v1", "task": "a-task", "strategy": "evidence-v3",
        "assignments": [{"role": "developer", "provider": "acme/model-a",
                         "confidence": "measured", "evidence_count": 42,
                         "reasons": ["beat the alternatives on this task class"]}]})
    constraints = write_json(tmp_path / "constraints.json",
                             {"identity": {"acme/model-a": "acme"}, "task": "a-task"})
    payload = rig_cli_json("wb", "route-team", routing, "--constraints", constraints, "--json",
                           cwd=rig_git_repo, expect_returncode=0)
    assert_document(payload, schema="rig.team-routing/v1",
                    required={"schema", "status", "strategy", "violations",
                              "reported_unmeasured"},
                    what="`wb route-team --json`")
    assert payload["status"] == "admissible"


def test_group2_wb_budget_plan_reads_and_answers_in_rig_assurance_budget_v1(
        rig_git_repo, rig_cli_json, tmp_path):
    """`selected` is the choice and `excluded` is every plan that did not clear the floor,
    with why. A consumer keeping only the first cannot tell a budget that chose from a
    field from one that had a single candidate."""
    plans = write_json(tmp_path / "plans.json", {
        "schema": "rig.assurance-budget/v1", "task": "a-task",
        "plans": [{"id": "thorough",
                   "guarantees": ["independent-review", "signed-provenance"],
                   "cost": 4.0, "cost_basis": "measured", "latency_seconds": 600.0,
                   "reasons": ["measured on this task class"]},
                  {"id": "quick", "guarantees": ["independent-review"],
                   "cost": 1.0, "cost_basis": "measured", "latency_seconds": 60.0,
                   "reasons": ["measured on this task class"]}]})
    budget = write_json(tmp_path / "budget.json",
                        {"required": ["independent-review"], "task": "a-task", "max_cost": 5.0})
    payload = rig_cli_json("wb", "budget-plan", plans, "--budget", budget, "--json",
                           cwd=rig_git_repo, expect_returncode=0)
    assert_document(payload, schema="rig.assurance-budget/v1",
                    required={"schema", "status", "task", "selected", "excluded", "answers"},
                    what="`wb budget-plan --json`")
    assert payload["selected"]["id"] == "quick"


def test_group2_wb_provenance_reads_and_answers_in_rig_provenance_graph_v1(
        rig_git_repo, rig_cli_json, tmp_path):
    """`upstream` and `downstream` each split into `confirmed` and `inferred`, and that
    split is the command's whole reason for existing: what somebody wrote down and what rig
    worked out are different kinds of claim and must not be read as one list."""
    graph = write_json(tmp_path / "provenance.json", {
        "schema": "rig.provenance-graph/v1",
        "nodes": [{"id": "c1", "kind": "commit", "label": "the commit c1"},
                  {"id": "r1", "kind": "requirement", "label": "the requirement r1"}],
        "edges": [{"source": "c1", "target": "r1", "relation": "implements",
                   "basis": "confirmed", "authority": "receipt:task-1"}]})
    payload = rig_cli_json("wb", "provenance", graph, "c1", "--no-resolve", "--json",
                           cwd=rig_git_repo, expect_returncode=0)
    assert_document(payload, schema="rig.provenance-graph/v1",
                    required={"schema", "node", "direction", "upstream", "downstream",
                              "invalidated", "authorities_looked_up"},
                    what="`wb provenance --json`")
    assert {"confirmed", "inferred"} <= set(payload["upstream"])


def test_group2_wb_expected_outcome_reads_two_ids_and_answers_in_rig_production_outcome_v1(
        rig_git_repo, rig_cli_json, tmp_path):
    """The expectation is `rig.expected-outcome/v1` and the measurements are
    `rig.production-observation/v1` — two ids because the bar is declared in one and never
    in the other, and the command refuses `target`/`baseline` by name in the observation.
    The answer is a third id, `rig.production-outcome/v1`, and it keeps `claim` on it:
    observational-not-causal, which is the sentence a reader has to carry away with the
    numbers. `change` names a real commit in the repository because the command refuses to
    compare numbers against an object git cannot resolve."""
    head = _git(rig_git_repo, "rev-parse", "HEAD")
    expected = write_json(tmp_path / "expected.json", {
        "schema": "rig.expected-outcome/v1", "change": head,
        "declared_by": "explicit-user", "declared_at": "2026-07-30T00:00:00+00:00",
        "source": "#437 acceptance criteria, line 3",
        "window": {"opens": "2026-08-01T00:00:00+00:00",
                   "closes": "2026-08-15T00:00:00+00:00"},
        "metrics": [{"id": "p95_latency_ms", "role": "objective", "unit": "ms",
                     "direction": "decrease", "baseline": 820.0, "target": 574.0},
                    {"id": "error_rate_pct", "role": "guardrail", "unit": "pct",
                     "at_most": 0.5}]})
    observed = write_json(tmp_path / "observed.json", {
        "schema": "rig.production-observation/v1", "change": head,
        "observations": [{"metric": "p95_latency_ms", "value": 787.0, "unit": "ms",
                          "observed_at": "2026-08-14T09:00:00+00:00", "kind": "measured",
                          "source": "apm-export-0814"},
                         {"metric": "error_rate_pct", "value": 0.22, "unit": "pct",
                          "observed_at": "2026-08-14T09:00:00+00:00", "kind": "measured",
                          "source": "apm-export-0814"}]})
    payload = rig_cli_json("wb", "expected-outcome", expected, "--observed", observed,
                           "--as-of", "2026-08-20T00:00:00+00:00", "--json", cwd=rig_git_repo)
    assert_document(
        payload, schema="rig.production-outcome/v1",
        required={"schema", "status", "change", "claim", "counts", "metrics", "window",
                  "final", "declared_by", "declared_at", "declared_source", "inputs",
                  "assurance", "change_cross_check", "recorded_outcome", "unrequested"},
        what="`wb expected-outcome --json`")
    assert payload["claim"] == "observational-not-causal"


def test_group2_wb_effectiveness_reads_the_query_id_and_answers_in_rig_workflow_effectiveness_v1(
        task, rig_cli_json, tmp_path):
    """Two ids: the closed query the caller declares, and the derivation. A metric rig
    cannot measure comes back as `unobservable` with a reason rather than as a zero, so
    `metrics` and `unobservable_patterns` are both contract — a consumer that saw only the
    first would read "we do not measure that" as "we measured nothing"."""
    repo, _ = task
    query = write_json(tmp_path / "query.json", {
        "schema": "rig.workflow-effectiveness-query/v1",
        "patterns": [{"kind": "late-stage-failure", "minimum_occurrences": 2,
                      "late_steps": ["acceptance", "review-diff"]}]})
    payload = rig_cli_json("wb", "effectiveness", "--query", query, "--json", cwd=repo,
                           expect_returncode=0)
    assert_document(
        payload, schema="rig.workflow-effectiveness/v1",
        required={"schema", "metrics", "patterns", "records", "unobservable_patterns",
                  "does_not_guarantee"},
        what="`wb effectiveness --json`")
    assert payload["metrics"]["cost"]["status"] == "unobservable"


def test_group2_wb_compose_options_emits_rig_compose_options_v1_with_all_five_axes(
        rig_git_repo, rig_cli_json):
    """The one group2 command that takes no authored document: the five choices are derived
    from the task type and the measured diff. All five axis ids are pinned, because "the
    five deterministic choices" is what the command promises and four of them is a
    different promise."""
    payload = rig_cli_json("wb", "compose-options", "--type", "feature", "--diff", "120",
                           "--json", cwd=rig_git_repo, expect_returncode=0)
    assert_document(payload, schema="rig.compose-options/v1",
                    required={"schema", "axes", "task_type", "size", "diff_lines",
                              "does_not_guarantee"},
                    what="`wb compose-options --json`")
    assert [axis["id"] for axis in payload["axes"]] == \
        ["recipe", "step", "gate", "backend", "mode"]


# ══ group3 — the organisational and external surface ═════════════════════════
# Governance records, the two console scripts that are not `rig-wb` subcommands, and the
# two documents rig writes into a repository's own state rather than onto stdout.
#
# Three of these ids never appear on stdout at all: `govern init` and `govern waiver` write
# their documents into `.rig/`, and `wb import` writes its provenance block into the task
# record. That is still the CLI emitting them — the file is what the next command, the next
# run, and any external reader will actually parse — so the assertion reads the file the
# process wrote rather than pretending stdout is the only surface.

def test_group3_govern_init_writes_rig_org_v2_binding_the_repository_to_its_org_and_team(
        rig_git_repo, rig_cli):
    """`.rig/org.json` is where every later govern command learns which policy layers apply.
    `policy_layers` is the list it resolves in order, so a reader that lost it would fall
    back to no policy at all — which looks identical to a policy that permits everything."""
    result = rig_cli("govern", "init", "--org", "acme", "--team", "team-a", cwd=rig_git_repo)
    assert result.returncode == 0, result.stderr
    payload = read_json_file(rig_git_repo / ".rig" / "org.json", what="`govern init`")
    assert_document(payload, schema="rig.org/v2",
                    required={"schema", "org", "team", "policy_layers"},
                    what="the .rig/org.json `govern init` writes")
    assert payload["policy_layers"] == [".rig/policy/org.json"]


def test_group3_govern_init_writes_a_starter_rig_policy_v2_layer_that_is_the_floor(
        rig_git_repo, rig_cli):
    """The starter layer is the org floor every team builds on, and `scope` is what makes
    the tightening-only rule checkable: org, then team, then project. `roles`, `members`
    and `approvals` are the three a permission check reads."""
    result = rig_cli("govern", "init", "--org", "acme", "--team", "team-a", cwd=rig_git_repo)
    assert result.returncode == 0, result.stderr
    payload = read_json_file(rig_git_repo / ".rig" / "policy" / "org.json",
                             what="`govern init`")
    assert_document(payload, schema="rig.policy/v2",
                    required={"schema", "id", "scope", "org", "version", "roles", "members",
                              "sealed_roles", "approvals", "waivers"},
                    what="the .rig/policy/org.json `govern init` writes")
    assert payload["scope"] == "org"


def test_group3_govern_policy_show_json_emits_rig_effective_policy_v1_over_its_rig_policy_v2_layers(
        rig_git_repo, rig_cli, rig_cli_json):
    """`govern policy show --json` prints the *effective* policy — every layer folded,
    roles flattened, quorums merged — and it is its own document with its own name.

    The id is deliberately not the `rig.policy/v2` of the layers underneath it. That id
    belongs to one stored, authored, publishable layer; this is a derived view with no
    `scope` and no single `id`, and a reader that saw the layer's name on it would be
    entitled to validate it as a layer. So both ends are pinned here: the composed id on
    what the command prints, and the layer id on the file `layers[].path` points at, which
    is the trail a reader follows from one to the other.

    `jsonio.LEGACY` still lists "govern": this document names itself without being wrapped
    in `{schema, status, data}`, so every key a consumer already reads is where it was.
    Moving it under `data` would be the second, breaking half — and a separate decision."""
    assert rig_cli("govern", "init", "--org", "acme", "--team", "team-a",
                   cwd=rig_git_repo).returncode == 0
    payload = rig_cli_json("govern", "policy", "show", "--json", cwd=rig_git_repo,
                           expect_returncode=0)
    assert_document(payload, schema="rig.effective-policy/v1",
                    required={"schema", "active", "org", "team", "layers", "require_criteria",
                              "roles", "members", "sealed_roles", "approvals", "waivers",
                              "audit_chain_required"},
                    what="`govern policy show --json`")
    assert payload["active"] is True
    [layer] = payload["layers"]
    assert layer["scope"] == "org"
    assert read_json_file(layer["path"], what="the layer `govern policy show` names"
                          )["schema"] == "rig.policy/v2"


def test_group3_govern_waiver_grant_writes_rig_waivers_v2_with_the_exception_it_recorded(
        rig_git_repo, rig_cli):
    """A waiver is an exception to the gate, so the record of it is the audit trail. The
    envelope carries the id and a `waivers` list; every entry keeps who granted it, why, and
    when it expires, because a time-boxed exception nobody can date is not time-boxed.

    `--expires` is deliberately not passed: the starter policy caps a waiver at fourteen
    days and refuses anything beyond it, so a literal date here would be a test that expires
    rather than a contract that holds. Omitting it takes the policy's own maximum."""
    assert rig_cli("govern", "init", "--org", "acme", "--team", "team-a",
                   cwd=rig_git_repo).returncode == 0
    result = rig_cli("govern", "waiver", "grant", "flaky-suite",
                     "--criterion", "tests_pass_or_explained",
                     "--reason", "the upstream suite is flaky this week", cwd=rig_git_repo)
    assert result.returncode == 0, result.stderr
    payload = read_json_file(rig_git_repo / ".rig" / "waivers.json", what="`govern waiver grant`")
    assert_document(payload, schema="rig.waivers/v2", required={"schema", "waivers"},
                    what="the .rig/waivers.json `govern waiver grant` writes")
    [waiver] = payload["waivers"]
    assert {"criteria", "scope", "reason", "granted_by", "granted_at", "expires",
            "revoked"} <= set(waiver)


def test_group3_the_rig_evidence_entry_point_emits_rig_field_study_v1_for_one_observation(
        rig_git_repo, rig_entry_point):
    """`rig-evidence` is its own console script, bound in pyproject.toml to
    `rig_workbench.evidence:main` — a user reaches it without going through `rig-wb`, so it
    is pinned through the module the script names. `arm` is the field study's whole point:
    a RIG observation and a bare one, kept apart."""
    result = rig_entry_point("rig-evidence", "record", "--arm", "rig", "--outcome", "ok",
                             "--defects-caught", "2", "--project", "demo", cwd=rig_git_repo)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert_document(payload, schema="rig.field-study/v1",
                    required={"schema", "ts", "arm", "outcome", "project"},
                    what="`rig-evidence record`")
    assert payload["arm"] == "rig"


def test_group3_the_rig_evidence_entry_point_summarises_in_rig_mission_control_v1(
        rig_git_repo, rig_entry_point):
    """`rig-evidence summary --json` and `rig-mission-control --json` answer under the same
    id from two different entry points, which is deliberate: it is one control-plane view
    and a reader should not have to learn two names for it. `rig-mission-control` adds
    `assurance` and `operations` blocks, and the shared floor is what is pinned here."""
    result = rig_entry_point("rig-evidence", "summary", "--json", cwd=rig_git_repo)
    assert result.returncode == 0, result.stderr
    assert_document(json.loads(result.stdout), schema="rig.mission-control/v1",
                    required={"schema", "generated_at", "repo", "core", "production",
                              "field_study", "fleet"},
                    what="`rig-evidence summary --json`")


def test_group3_the_rig_evidence_entry_point_saves_the_fleet_it_measures_as_rig_fleet_v1(
        rig_git_repo, rig_entry_point):
    """`fleet-config` writes the list of repositories the fleet rollup walks. The id is
    checked on read — `fleet_snapshot` refuses a config that is not `rig.fleet/v1` — so this
    is one of the places where the string is not decoration but the gate on the file."""
    result = rig_entry_point("rig-evidence", "fleet-config", "--project", ".",
                             "--since-days", "90", cwd=rig_git_repo)
    assert result.returncode == 0, result.stderr
    payload = read_json_file(rig_git_repo / ".rig" / "fleet.json",
                             what="`rig-evidence fleet-config`")
    assert_document(payload, schema="rig.fleet/v1",
                    required={"schema", "projects", "since_days"},
                    what="the .rig/fleet.json `rig-evidence fleet-config` writes")


def test_group3_the_rig_mission_control_entry_point_emits_rig_mission_control_v1(
        rig_git_repo, rig_entry_point):
    """The second console script, `rig_workbench.mission_control:main`. The read-only
    control-plane view: `assurance` and `operations` are the two blocks it adds over
    `rig-evidence summary`, and they are what a dashboard reads."""
    result = rig_entry_point("rig-mission-control", "--json", cwd=rig_git_repo)
    assert result.returncode == 0, result.stderr
    assert_document(json.loads(result.stdout), schema="rig.mission-control/v1",
                    required={"schema", "generated_at", "repo", "core", "production",
                              "field_study", "fleet", "assurance", "operations"},
                    what="`rig-mission-control --json`")


def test_group3_wb_import_records_the_external_producer_as_rig_byoo_import_v1_on_the_task(
        rig_git_repo, rig_cli):
    """`wb import` registers a change rig did not produce, and the block it writes into the
    task record is the provenance of that: who claimed to produce it, which commit was
    verified, and — flatly, on every import — `claims_gate_effect: none`, because what a
    producer says about its own work never reaches the gate. That constant is the contract
    the id exists to carry.

    The commit is made on a side branch and `main` is checked back out first, because the
    command refuses a `--head` that resolves to the same commit as `--base`: an import with
    nothing to verify is not an import."""
    head = _external_commit(rig_git_repo)
    result = rig_cli("wb", "import", "--head", head, "--type", "feature",
                     "--producer", "some-orchestrator", "--input", "an external change",
                     "--slug", "external", cwd=rig_git_repo)
    assert result.returncode == 0, result.stderr
    [run_dir] = sorted((rig_git_repo / ".rig" / "runs").iterdir())
    record = read_json_file(run_dir / "task.json", what="`wb import`")
    assert_document(
        record["import"], schema="rig.byoo-import/v1",
        required={"schema", "producer", "producer_runtime", "run_id", "source_url",
                  "head_commit", "head_requested", "head_ref", "head_symbolic", "claims",
                  "claims_gate_effect", "diff_summary", "imported_at"},
        what="the import block `wb import` writes into task.json")
    assert record["import"]["head_commit"] == head
    assert record["import"]["claims_gate_effect"] == "none"


def test_group3_registering_a_knowledge_candidate_appends_rig_org_knowledge_v1_to_the_ledger(
        rig_git_repo, rig_cli):
    """The org-knowledge path: `--register` enters a *supported* candidate into the
    promotion lifecycle, and the lifecycle lives in the append-only
    `.rig/org-knowledge.jsonl`. Every line carries the id, the `event` that produced it and
    the `candidate` as submitted — current state is derived by replaying them, so a line
    that lost its id is a line nothing can replay."""
    write_json(rig_git_repo / "evidence.json", {
        "schema": "rig.knowledge-candidate-evidence/v1",
        "records": [{"id": "obs-1",
                     "observation": "the gate caught an unrelated refactor twice",
                     "applicable_context": ["python"],
                     "proposed_rules": ["keep refactors out of a bugfix diff"],
                     "observed_benefits": ["smaller diffs to review"],
                     "known_exceptions": ["a rename the fix requires"],
                     "scope": ["this repository"]}]})
    write_json(rig_git_repo / "candidate.json", {
        "schema": "rig.knowledge-candidate/v1",
        "triggering_evidence": [{"path": "evidence.json", "record": "obs-1"}],
        "applicable_context": ["python"],
        "proposed_rule": "keep refactors out of a bugfix diff",
        "expected_benefit": "smaller diffs to review",
        "confidence": 0.5, "evidence_count": 1,
        "known_exceptions": ["a rename the fix requires"],
        "scope": ["this repository"]})
    result = rig_cli("wb", "knowledge-candidate", "candidate.json", "--register", "--json",
                     cwd=rig_git_repo)
    assert result.returncode == 0, result.stderr
    ledger = rig_git_repo / ".rig" / "org-knowledge.jsonl"
    assert ledger.is_file(), "`--register` wrote no .rig/org-knowledge.jsonl"
    [line] = ledger.read_text(encoding="utf-8").splitlines()
    entry = json.loads(line)
    assert_document(entry, schema="rig.org-knowledge/v1",
                    required={"schema", "id", "ts", "event", "candidate", "assessment"},
                    what="the .rig/org-knowledge.jsonl line `--register` appends")
    assert entry["event"] == "register"
    assert entry["candidate"]["schema"] == "rig.knowledge-candidate/v1"


# ── the gap, held open on purpose ────────────────────────────────────────────
def test_the_ids_this_file_does_not_reach_stay_named_with_the_reason_they_are_not_reached():
    """NOT_PINNED is a claim about the tree, so it is checked rather than left as prose. If
    one of these becomes reachable from a command — a `queue graph --json`, say — this fails
    and the entry moves into a real test instead of quietly staying an excuse."""
    for entry in NOT_PINNED:
        assert set(entry) == {"schema", "command", "reason"}, entry
        assert entry["reason"].strip(), entry["schema"]
    assert {entry["schema"] for entry in NOT_PINNED} == {
        "rig.assurance-graph/v1", "rig.queue-dependencies/v1", "rig.mission-worker/v1"}, (
        "the set of ids this file admits it does not reach has changed. That is either "
        "progress worth turning into a test or a new gap worth explaining — say which.")
