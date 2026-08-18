"""Resolved workflow / assurance graph — `rig.assurance-graph/v1` (#426).

The graph is a projection of a projection: structure and step outcomes from the run's
own records, everything about the gate and the decision through the Assurance Receipt.
These tests hold it to that — no third opinion, no invented structure, and the two
provider slots never collapsed into one.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from rig_workbench.workbench import graph
from rig_workbench.workbench.state import sign_provenance

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _write(d: pathlib.Path, name: str, payload: dict) -> None:
    (d / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


@pytest.fixture
def task(tmp_path):
    """A finished `feature` task: serial steps, a four-way review fan-out, gate passed."""
    task_id = "rig-20260101-000000-example"
    d = tmp_path / ".rig" / "runs" / task_id
    d.mkdir(parents=True)
    _write(d, "task.json", {
        "task_id": task_id, "input": "add a thing", "task_type": "feature",
        "recipe": "feature", "base_branch": "master", "base_commit": "a" * 40,
        "branch": f"rig/{task_id}", "status": "accepted", "actor": "alice",
    })
    _write(d, "acceptance.json", {
        "task_id": task_id, "presets": ["standard", "feature"], "status": "passed",
        "checks": [{"name": "tests_pass_or_explained", "status": "passed", "detail": ""}],
    })
    # The shipped `feature` recipe's step ids, in order.
    _write(d, "steps.json", {"seeded": True, "steps": [
        {"name": "inspect", "status": "passed", "personas": ["orchestrator"]},
        {"name": "clarify-requirements", "status": "passed", "personas": ["orchestrator"]},
        {"name": "design", "status": "passed", "personas": ["implementer"]},
        {"name": "implement", "status": "passed", "personas": ["implementer"]},
        {"name": "test", "status": "passed", "personas": ["implementer"]},
        {"name": "update-docs-if-needed", "status": "passed", "personas": ["implementer"]},
        {"name": "review-diff", "status": "passed", "personas": [
            "security-reviewer", "design-reviewer", "test-reviewer",
            "behavioral-correctness-reviewer"]},
        {"name": "acceptance", "status": "passed", "personas": ["implementer"]},
    ]})
    record = {"task_id": task_id, "gate_status": "passed", "forced": False}
    _write(d, "provenance.json", {"record": record,
                                  "signature": sign_provenance(tmp_path, record),
                                  "algo": "HMAC-SHA256"})
    # The recipe has to be readable from the tmp repo for the structure join.
    recipes = tmp_path / "skills" / "engine" / "recipes"
    recipes.mkdir(parents=True)
    shutil.copy(REPO_ROOT / "skills" / "engine" / "recipes" / "feature.md",
                recipes / "feature.md")
    return tmp_path, task_id


def _nodes(g, kind):
    return [n for n in g["nodes"] if n["kind"] == kind]


def _by_id(g):
    return {n["id"]: n for n in g["nodes"]}


# ---- the envelope -----------------------------------------------------------

def test_graph_carries_its_own_schema_name(task):
    root, task_id = task
    assert graph.build_graph(root, task_id)["schema"] == "rig.assurance-graph/v1"


def test_the_model_is_presentation_neutral(task):
    """A second client must not have to adopt this one's stylesheet to read it."""
    root, task_id = task
    blob = json.dumps(graph.build_graph(root, task_id)).lower()
    for leak in ("color", "#ff", "px", "css", "class=", "style=", "width", "svg"):
        assert leak not in blob, leak


# ---- shape ------------------------------------------------------------------

def test_a_review_step_becomes_a_fanout_with_one_member_per_reviewer(task):
    root, task_id = task
    g = graph.build_graph(root, task_id)
    fanout = _nodes(g, "fanout")
    assert len(fanout) == 1
    assert fanout[0]["label"] == "review-diff"
    assert len(fanout[0]["members"]) == 4
    assert {n["label"] for n in _nodes(g, "reviewer")} == {
        "security-reviewer", "design-reviewer", "test-reviewer",
        "behavioral-correctness-reviewer"}


def test_fanout_edges_are_distinguishable_from_sequence_edges(task):
    """AC: serial and parallel fan-out must be tellable apart without guessing."""
    root, task_id = task
    g = graph.build_graph(root, task_id)
    kinds = {e["kind"] for e in g["edges"]}
    assert kinds == {"sequence", "fanout"}
    fanout_edges = [e for e in g["edges"] if e["kind"] == "fanout"]
    assert len(fanout_edges) == 4
    assert all(e["from"] == "step:review-diff" for e in fanout_edges)


def test_serial_steps_are_chained_in_recorded_order(task):
    root, task_id = task
    g = graph.build_graph(root, task_id)
    seq = [(e["from"], e["to"]) for e in g["edges"] if e["kind"] == "sequence"]
    assert ("task", "isolate") in seq
    assert ("isolate", "step:inspect") in seq
    assert ("step:inspect", "step:clarify-requirements") in seq
    assert ("step:acceptance", "gate:acceptance") in seq
    assert ("gate:acceptance", "decision") in seq


def test_execution_and_verification_sit_in_different_lanes(task):
    root, task_id = task
    by_id = _by_id(graph.build_graph(root, task_id))
    assert by_id["step:implement"]["lane"] == "execution"
    assert by_id["step:review-diff"]["lane"] == "verification"
    assert by_id["gate:acceptance"]["lane"] == "gate"
    assert by_id["decision"]["lane"] == "decision"


def test_a_reviewer_step_is_recognised_by_its_personas_not_its_id(tmp_path):
    """A custom recipe may call its review step anything; the reviewers still show."""
    task_id = "rig-20260101-000000-custom"
    d = tmp_path / ".rig" / "runs" / task_id
    d.mkdir(parents=True)
    _write(d, "task.json", {"task_id": task_id, "input": "x", "task_type": "feature",
                            "recipe": "", "status": "running", "branch": "b"})
    _write(d, "steps.json", {"seeded": True, "steps": [
        {"name": "second-opinion", "status": "passed",
         "personas": ["security-reviewer", "design-reviewer"]},
    ]})
    g = graph.build_graph(tmp_path, task_id)
    node = _by_id(g)["step:second-opinion"]
    assert node["kind"] == "fanout"
    assert node["lane"] == "verification"


# ---- structure is read, never assumed ---------------------------------------

def test_the_structure_source_is_stated_with_what_it_does_not_prove(task):
    """Matching step ids show the recipe still declares the same steps. They do not
    show the step bodies are the ones that ran, because the run recorded a recipe name
    and never a revision — so the value says `as-currently-defined` and carries why."""
    root, task_id = task
    g = graph.build_graph(root, task_id)
    assert g["recipe"]["structure_resolved_from"] == "recipe-as-currently-defined"
    assert "did not record which revision" in g["recipe"]["structure_caveat"]
    assert _by_id(g)["step:review-diff"]["pattern"] == "parallel-fanout"


def test_an_in_place_recipe_edit_is_not_presented_as_the_run_s_own_shape(task):
    """The case matching-ids cannot catch: same steps, one switched to serial after
    the run. The graph shows the current definition and must say that is what it is."""
    root, task_id = task
    recipe = root / "skills" / "engine" / "recipes" / "feature.md"
    recipe.write_text(recipe.read_text(encoding="utf-8")
                      .replace("pattern: parallel-fanout", "pattern: serial"),
                      encoding="utf-8")
    g = graph.build_graph(root, task_id)
    assert _by_id(g)["step:review-diff"]["pattern"] == "serial"      # the recipe today
    assert g["recipe"]["structure_resolved_from"] == "recipe-as-currently-defined"
    assert "would be shown as though it had always been that way" in \
        g["recipe"]["structure_caveat"]


def test_every_structure_source_value_explains_itself(task):
    root, task_id = task
    g = graph.build_graph(root, task_id)
    assert g["recipe"]["structure_resolved_from"] in graph.STRUCTURE_SOURCES
    assert g["recipe"]["structure_caveat"] == \
        graph.STRUCTURE_SOURCES[g["recipe"]["structure_resolved_from"]]


def test_a_recipe_that_drifted_since_the_run_is_not_pinned_onto_it(task):
    """Pairing a renamed recipe's steps by position would attach one step's pattern
    to another step's outcome. Saying the structure is unusable is the honest answer."""
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    steps = json.loads((d / "steps.json").read_text(encoding="utf-8"))
    steps["steps"].insert(0, {"name": "a-step-the-recipe-no-longer-has",
                              "status": "passed", "personas": ["implementer"]})
    _write(d, "steps.json", steps)
    g = graph.build_graph(root, task_id)
    assert g["recipe"]["structure_resolved_from"] == "recipe-drifted"
    assert _by_id(g)["step:implement"]["pattern"] is None


def test_an_unreadable_recipe_leaves_pattern_null_rather_than_serial(tmp_path):
    """`null` is "nobody wrote it down". `serial` would be a claim about the run."""
    task_id = "rig-20260101-000000-norecipe"
    d = tmp_path / ".rig" / "runs" / task_id
    d.mkdir(parents=True)
    _write(d, "task.json", {"task_id": task_id, "input": "x", "task_type": "feature",
                            "recipe": "no-such-recipe", "status": "running", "branch": "b"})
    _write(d, "steps.json", {"seeded": True, "steps": [
        {"name": "implement", "status": "running", "personas": ["implementer"]}]})
    g = graph.build_graph(tmp_path, task_id)
    assert g["recipe"]["structure_resolved_from"] == "unrecorded"
    assert _by_id(g)["step:implement"]["pattern"] is None


# ---- no third opinion --------------------------------------------------------

def test_the_gate_node_reports_the_recorded_status_not_a_recomputed_one(task):
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    acc = json.loads((d / "acceptance.json").read_text(encoding="utf-8"))
    acc["status"] = "failed"                 # the one criterion still says passed
    _write(d, "acceptance.json", acc)
    g = graph.build_graph(root, task_id)
    assert _by_id(g)["gate:acceptance"]["status"] == "failed"


def test_the_decision_node_comes_from_the_receipt(task):
    root, task_id = task
    from rig_workbench.workbench import assurance
    root_, task_id_ = task
    g = graph.build_graph(root, task_id)
    receipt = assurance.build_receipt(root_, task_id_)
    decision = _by_id(g)["decision"]
    assert decision["label"] == receipt["final_status"]["value"]
    assert decision["detail"] == receipt["final_status"]["basis"]


def test_the_gate_node_leads_back_to_the_authoritative_records(task):
    root, task_id = task
    references = _by_id(graph.build_graph(root, task_id))["gate:acceptance"]["references"]
    assert [c["name"] for c in references["criteria"]] == ["tests_pass_or_explained"]
    assert references["provenance"]["verified"] is True
    assert isinstance(references["evidence"], list)


def test_the_graph_holds_no_verdict_of_its_own(task):
    """Every status in the graph must trace to a record; none is computed here."""
    root, task_id = task
    g = graph.build_graph(root, task_id)
    allowed = {"passed", "failed", "warning", "pending", "running", "skipped"}
    assert {n["status"] for n in g["nodes"]} <= allowed


# ---- the two providers stay apart -------------------------------------------

def test_execution_and_verification_providers_are_never_one_slot(task):
    """The trust boundary is that the thing which wrote the change is not the thing
    which judged it. One merged "provider: unknown" erases exactly that question."""
    root, task_id = task
    providers = graph.build_graph(root, task_id)["providers"]
    assert set(providers) == {"execution", "verification"}
    assert providers["execution"]["observed"] is False
    assert providers["verification"]["observed"] is False
    assert providers["execution"]["reason"] != providers["verification"]["reason"]


def test_a_reviewer_with_no_recorded_verdict_says_so(task):
    root, task_id = task
    g = graph.build_graph(root, task_id)
    for node in _nodes(g, "reviewer"):
        assert node["verdict"] is None
        assert "no per-reviewer verdict recorded" in node["detail"]


def test_a_recorded_reviewer_verdict_is_shown(task):
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    _write(d, "review.json", {"task_id": task_id, "verdicts": [
        {"persona": "security-reviewer", "verdict": "REJECT"}]})
    by_id = _by_id(graph.build_graph(root, task_id))
    assert by_id["step:review-diff/security-reviewer"]["verdict"] == "REJECT"
    assert by_id["step:review-diff/design-reviewer"]["verdict"] is None


@pytest.mark.parametrize("verdict,expected", [
    ("APPROVE", "passed"),
    ("REJECT", "failed"),
    ("APPROVE_WITH_CONDITIONS", "warning"),
    ("SOMETHING_NEW", "pending"),
])
def test_a_reviewers_verdict_decides_their_node_status(task, verdict, expected):
    """The panel shows a glyph and a colour long before anyone reads the label. A
    rejecting reviewer drawn in green is the worst thing this graph could say."""
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    _write(d, "review.json", {"task_id": task_id, "verdicts": [
        {"persona": "security-reviewer", "verdict": verdict}]})
    node = _by_id(graph.build_graph(root, task_id))["step:review-diff/security-reviewer"]
    assert node["status"] == expected


def test_the_verdict_vocabulary_stays_in_step_with_the_gate_s():
    """A verdict rig accepts but this map has never heard of would render as `pending`
    forever. The guard lives here rather than as an import-time assert: `mission_server`
    imports this module at load, so an assert would take the page down to prevent one
    node reading `pending` — and `-O` would strip it anyway. Failing the build is the
    right place for drift."""
    from rig_workbench.workbench.config import VALID_VERDICT
    assert set(graph._VERDICT_STATUS) == set(VALID_VERDICT)


def test_importing_the_module_never_raises_on_an_unknown_verdict(monkeypatch):
    """Whatever the vocabulary does, Mission Control still starts."""
    import importlib

    from rig_workbench.workbench import config
    monkeypatch.setattr(config, "VALID_VERDICT", ("APPROVE", "REJECT", "SOMETHING_NEW"))
    importlib.reload(graph)          # must not raise
    assert graph.SCHEMA == "rig.assurance-graph/v1"


@pytest.mark.parametrize("verdict", ["approve", "APPROVE ", " APPROVE", "Approve"])
def test_a_verdict_rig_would_have_refused_to_write_reads_as_pending(task, verdict):
    """`rig-wb review` validates against `VALID_VERDICT` before writing, so these can
    only come from a hand-edited file. Normalising them here would accept values rig
    itself rejects; `pending` says the record was not understood."""
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    _write(d, "review.json", {"task_id": task_id, "verdicts": [
        {"persona": "security-reviewer", "verdict": verdict}]})
    node = _by_id(graph.build_graph(root, task_id))["step:review-diff/security-reviewer"]
    assert node["status"] == "pending"


def test_two_verdicts_for_one_persona_do_not_collapse_silently(task):
    """`rig-wb review` upserts, so it cannot produce this; a hand-edited file can. The
    last row wins — matching the writer — but a conflicting pair must not vanish."""
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    _write(d, "review.json", {"task_id": task_id, "verdicts": [
        {"persona": "security-reviewer", "verdict": "REJECT"},
        {"persona": "security-reviewer", "verdict": "APPROVE"},
    ]})
    node = _by_id(graph.build_graph(root, task_id))["step:review-diff/security-reviewer"]
    assert node["verdict"] == "APPROVE"                 # last wins, like cmd_review
    assert node["status"] == "passed"
    assert "more than one verdict" in node["detail"]


def test_a_single_verdict_carries_no_duplicate_warning(task):
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    _write(d, "review.json", {"task_id": task_id, "verdicts": [
        {"persona": "security-reviewer", "verdict": "APPROVE"}]})
    node = _by_id(graph.build_graph(root, task_id))["step:review-diff/security-reviewer"]
    assert node["detail"] is None


def test_isolation_is_carried_verbatim_from_the_receipt(task):
    """A client must not be able to badge a worktree and an OS sandbox alike."""
    root, task_id = task
    node = _by_id(graph.build_graph(root, task_id))["isolate"]
    assert node["isolation"]["mode"] == "git-worktree"
    assert "os-enforced" not in json.dumps(node)


# ---- Mission Control ---------------------------------------------------------

def test_mission_control_task_detail_carries_the_graph(task):
    from rig_workbench.mission_server import task_detail
    root, task_id = task
    detail = task_detail(root, task_id)
    assert detail["graph"]["schema"] == "rig.assurance-graph/v1"
    assert detail["graph"]["nodes"]


def test_a_graph_failure_does_not_take_the_task_detail_down(task, monkeypatch):
    from rig_workbench import mission_server
    root, task_id = task
    monkeypatch.setattr(mission_server.assurance_graph, "build_graph",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    detail = mission_server.task_detail(root, task_id)
    assert detail["task"]["task_id"] == task_id
    assert "nope" in detail["graph"]["error"]
    assert detail["graph"]["nodes"] == []


def test_the_browser_page_renders_the_graph_and_still_parses():
    """`interactive_html()` returning a string is not evidence the JS is valid."""
    from rig_workbench.mission_ui import interactive_html
    page = interactive_html("token")
    assert "renderGraph(d.graph)" in page
    assert "Resolved workflow" in page
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    from rig_workbench.mission_ui import JS_TEMPLATE
    js = JS_TEMPLATE.replace("__CSRF__", json.dumps("t"))
    proc = subprocess.run([node, "--check", "-"], input=js, capture_output=True,
                          text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


#: Calls whose output is already safe: `cls`/`statusIcon` return a literal the code
#: chose rather than anything from the graph, and `chip` builds its own markup — whose
#: interpolations this same scan checks, since it is defined in the body being scanned.
#: Everything else interpolated into the panel has to go through `esc`.
_ALREADY_SAFE = ("cls(", "statusIcon(", "chip(", "slot(", "'fanout'", "'member'")


def test_every_graph_value_reaches_the_page_through_the_escaper():
    """The page escapes everything it prints; the new panel must not be the exception.

    A task input containing `<` renders safely everywhere else on this page. Checked
    by scanning the panel's own interpolations rather than by grepping for a spelling,
    so `esc(x)` and `esc(x||'')` both count and a raw `${n.label}` cannot slip past.
    """
    import re

    from rig_workbench.mission_ui import JS_TEMPLATE
    body = JS_TEMPLATE.split("function renderGraph")[1].split("function statusIcon")[0]
    interpolations = re.findall(r"\$\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", body)
    assert interpolations, "the panel interpolates nothing — the scan is not looking at it"
    for expr in interpolations:
        if not re.search(r"\b[ngl]\b|\.(label|kind|detail|verdict|name|status)", expr):
            continue                                  # no graph data in this one
        if any(safe in expr for safe in _ALREADY_SAFE):
            continue
        assert "esc(" in expr, f"unescaped interpolation: ${{{expr}}}"


# ---- the head nodes are read, not assumed -----------------------------------

@pytest.mark.parametrize("task_status,expected", [
    ("accepted", "passed"), ("discarded", "skipped"),
    ("running", "running"), ("something-new", "pending"),
])
def test_the_task_node_shows_the_recorded_task_status(task, task_status, expected):
    """A hardcoded `passed` here put a green head on a discarded run — the first node
    a reader's eye lands on."""
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    data = json.loads((d / "task.json").read_text(encoding="utf-8"))
    data["status"] = task_status
    _write(d, "task.json", data)
    assert _by_id(graph.build_graph(root, task_id))["task"]["status"] == expected


def test_a_no_worktree_run_does_not_show_a_passing_isolation_step(task):
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    data = json.loads((d / "task.json").read_text(encoding="utf-8"))
    data.pop("branch")
    _write(d, "task.json", data)
    node = _by_id(graph.build_graph(root, task_id))["isolate"]
    assert node["label"] == "main-tree"
    assert node["status"] == "skipped"


# ---- approvals are shown, never adjudicated ---------------------------------

def test_a_denial_alongside_an_approval_is_not_rendered_as_approved(task):
    """Whether one denial sinks three approvals is `govern`'s judgment. Deciding it
    here would be the duplicated approval logic the issue rules out."""
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    data = json.loads((d / "task.json").read_text(encoding="utf-8"))
    data["status"] = "running"
    _write(d, "task.json", data)
    _write(d, "approvals.json", {"task_id": task_id, "decisions": [
        {"actor": "bob", "decision": "approve", "roles": ["reviewer"]},
        {"actor": "carol", "decision": "deny", "roles": ["architect"]},
    ]})
    node = _by_id(graph.build_graph(root, task_id))["approval"]
    assert node["status"] == "pending"
    assert node["references"]["denied"] == ["carol"]
    assert "govern" in node["decided_by"]


def test_an_accepted_task_shows_the_approval_rule_as_enforced_by_accept(task):
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    _write(d, "approvals.json", {"task_id": task_id, "decisions": [
        {"actor": "bob", "decision": "approve", "roles": ["reviewer"]}]})
    node = _by_id(graph.build_graph(root, task_id))["approval"]
    assert node["status"] == "passed"
    assert "accept is the only path that enforces" in node["detail"]


# ---- the gate leads to the reviewers' own record ----------------------------

def test_the_gate_references_review_json_when_the_reviewers_left_one(task):
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    _write(d, "review.json", {"task_id": task_id, "verdicts": [
        {"persona": "security-reviewer", "verdict": "APPROVE"}]})
    review = _by_id(graph.build_graph(root, task_id))["gate:acceptance"]["references"]["review"]
    assert review["path"].endswith("review.json")
    assert review["verdicts"] == 1


def test_the_gate_review_reference_is_absent_rather_than_empty_when_none_exists(task):
    root, task_id = task
    references = _by_id(graph.build_graph(root, task_id))["gate:acceptance"]["references"]
    assert references["review"] is None


def test_the_graphed_repository_s_own_recipe_wins_over_the_installed_one(task):
    """Mission Control can serve a checkout that is not the rig doing the serving.
    Reading that repository's run against this one's recipes would describe a workflow
    it never had."""
    root, task_id = task
    recipe = root / "skills" / "engine" / "recipes" / "feature.md"
    recipe.write_text(recipe.read_text(encoding="utf-8")
                      .replace("- id: review-diff", "- id: renamed-in-this-checkout"),
                      encoding="utf-8")
    g = graph.build_graph(root, task_id)
    # The local recipe no longer matches the run, so nothing is pinned onto it —
    # which only happens if the local file was the one consulted.
    assert g["recipe"]["structure_resolved_from"] == "recipe-drifted"


def test_a_standing_denial_stays_visible_on_an_accepted_task(task):
    """`accept` blocks on an unsatisfied approval before `--force` is considered — but
    only where a policy layer is active. With governance off nothing was evaluated, so
    a recorded denial must remain readable rather than being implied away by `passed`."""
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    _write(d, "approvals.json", {"task_id": task_id, "decisions": [
        {"actor": "carol", "decision": "deny", "roles": ["architect"], "note": "no"}]})
    node = _by_id(graph.build_graph(root, task_id))["approval"]
    assert node["references"]["denied"] == ["carol"]
    assert "1 denied" in node["detail"]
    assert "where the repository has one" in node["detail"]


def test_a_row_that_names_a_persona_is_read_even_when_the_name_is_odd(task):
    """Falsiness is not absence. A hand-edited row naming persona `0` still names one,
    and skipping it would report a verdict that is in the file as not recorded."""
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    _write(d, "review.json", {"task_id": task_id, "verdicts": [
        {"persona": 0, "verdict": "REJECT"},
        {"persona": "security-reviewer", "verdict": "APPROVE"},
    ]})
    verdicts, duplicated = graph._reviewer_verdicts(
        json.loads((d / "review.json").read_text(encoding="utf-8")))
    assert verdicts["0"] == "REJECT"
    assert not duplicated
