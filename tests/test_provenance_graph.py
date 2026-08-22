"""#436 — why a change exists, what supports it, and what happened after it shipped.

Grouped by what they hold: the document is a closed schema, a guess and an observation are not
the same edge, the graph holds no second copy of a verdict, and invalidation is carried rather
than deleted.
"""

import dataclasses
import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench import provenance_graph as graph_mod
from rig_workbench.workbench.provenance_graph import (COMMIT, CONFIRMED, EVIDENCE, GOAL,
                                                      IMPLEMENTS, INFERRED, INTENT,
                                                      INVALIDATES, REQUIREMENT, SATISFIES,
                                                      DERIVED_FROM, SCHEMA,
                                                      VERIFIED_BY, Edge, Node,
                                                      invalidated, load, trace, validate)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


OID = "a" * 40


def _signed_record(root, task_id):
    """A `provenance.json` this repository would accept, signed with its own key.

    Built through `state` rather than by hand: what makes a provenance record valid is the
    repository's answer, and a fixture that decided it separately would be testing the
    fixture."""
    from rig_workbench.workbench import state

    body = {"task_id": task_id, "accepted_at": "2026-08-22T00:00:00+09:00"}
    key = state.load_or_create_provenance_key(pathlib.Path(root))
    import hashlib
    import hmac
    signature = hmac.new(key, state._provenance_payload(body), hashlib.sha256).hexdigest()
    return json.dumps({"algo": "HMAC-SHA256", "record": body, "signature": signature})


def _edges(entries):
    """The edge dicts out of a partition. Each entry is `{"edge": ..., "unresolved": ...}` —
    two dimensions, and most tests are about one of them."""
    return [entry["edge"] for entry in entries]


_UNSET = object()


def _node(node_id, kind=COMMIT, label=_UNSET):
    """`label=None` means the document holds `null`, not "use the default" — coercing here
    would test the helper rather than the schema."""
    return {"id": node_id, "kind": kind,
            "label": f"the {kind} {node_id}" if label is _UNSET else label}


def _edge(source, target, relation=IMPLEMENTS, basis=CONFIRMED, authority="receipt:task-1"):
    return {"source": source, "target": target, "relation": relation, "basis": basis,
            "authority": authority}


_DEFAULT = object()


def _graph(nodes=_DEFAULT, edges=_DEFAULT, **extra):
    return {"schema": SCHEMA,
            "nodes": [_node("c1"), _node("r1", REQUIREMENT)] if nodes is _DEFAULT
            else list(nodes),
            "edges": [_edge("c1", "r1")] if edges is _DEFAULT else list(edges),
            **extra}


# ── the document is closed ───────────────────────────────────────────────────
def test_a_valid_graph_has_no_problems():
    assert validate(_graph()) == []


def test_a_graph_of_nothing_relates_nothing():
    assert any("relates nothing" in p for p in validate(_graph(nodes=[], edges=[])))


def test_two_nodes_under_one_id_are_two_answers_to_what_an_edge_points_at():
    problems = validate(_graph(nodes=[_node("c1"), _node("c1", INTENT)], edges=[]))
    assert any("more than once" in p for p in problems), problems


def test_an_edge_to_nothing_is_a_chain_that_ends_without_saying_so():
    problems = validate(_graph(edges=[_edge("c1", "nowhere")]))
    assert any("not a node in this graph" in p for p in problems), problems


def test_a_node_relating_to_itself_is_refused():
    """A chain that returns to where it started explains nothing, and every traversal here
    would have to guard against it."""
    problems = validate(_graph(edges=[_edge("c1", "c1")]))
    assert any("relates to itself" in p for p in problems), problems


@pytest.mark.parametrize("kind", [None, "vibes", "", " commit "])
def test_a_kind_nobody_defined_is_refused(kind):
    assert any("kind" in p for p in validate(_graph(nodes=[_node("c1", kind)], edges=[]))), kind


@pytest.mark.parametrize("relation", [None, "relates-to", "", " implements "])
def test_a_relation_nobody_defined_is_refused(relation):
    problems = validate(_graph(edges=[_edge("c1", "r1", relation=relation)]))
    assert any("relation" in p for p in problems), relation


def test_a_node_with_no_label_is_a_graph_nobody_follows():
    for label in (None, "", "   ", 42):
        assert any("has to say what this is" in p
                   for p in validate(_graph(nodes=[_node("c1", label=label)], edges=[]))), label


def test_a_key_this_schema_does_not_define_is_refused_rather_than_dropped():
    assert any("'weight'" in p for p in validate(_graph(edges=[_edge("c1", "r1") |
                                                               {"weight": 3}])))
    assert any("'confidence'" in p for p in validate(_graph(nodes=[_node("c1") |
                                                                   {"confidence": 1}],
                                                            edges=[])))
    assert any("'version'" in p for p in validate(_graph(version=2)))


def test_the_accepted_keys_are_the_ones_the_records_actually_have():
    assert graph_mod.NODE_FIELDS == {f.name for f in dataclasses.fields(Node)}
    assert graph_mod.EDGE_FIELDS == {f.name for f in dataclasses.fields(Edge)}


def _run(tmp_path, graph, node, *flags, graph_text=None):
    path = tmp_path / "graph.json"
    path.write_text(graph_text if graph_text is not None else json.dumps(graph),
                    encoding="utf-8")
    return subprocess.run([sys.executable, str(WORKBENCH), "provenance", str(path), node,
                           *flags],
                          capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)


# ── a guess and an observation are not the same edge ─────────────────────────
@pytest.mark.parametrize("basis", [None, "", "probably", "maybe-confirmed"])
def test_an_edge_that_cannot_say_how_it_was_established_is_refused(basis):
    """It would read exactly like one somebody checked, and a reader following a chain would
    have no way to see where it stopped being evidence."""
    problems = validate(_graph(edges=[_edge("c1", "r1", basis=basis)]))
    assert any("basis" in p for p in problems), basis


@pytest.mark.parametrize("authority", [None, "", "   ", " padded ", 42, "somebody",
                                      "receipt:", "unknown-kind:x"])
def test_an_edge_that_cannot_say_who_established_it_is_refused(authority):
    """"Something concluded it" is not a source, and neither is a name with no kind — a reader
    has to be able to tell what they would go and look at."""
    problems = validate(_graph(edges=[_edge("c1", "r1", authority=authority)]))
    assert any("authority" in p for p in problems), authority


def test_the_trace_returns_the_two_kinds_apart():
    """Merging them is the failure this module exists to prevent."""
    result = trace(_graph(nodes=[_node("c1"), _node("r1", REQUIREMENT), _node("g1", GOAL)],
                          edges=[_edge("c1", "r1", basis=CONFIRMED),
                                 _edge("c1", "g1", relation=SATISFIES, basis=INFERRED,
                                       authority="agent:planner")]),
                   "c1")
    assert [e["target"] for e in _edges(result["upstream"][CONFIRMED])] == ["r1"]
    assert [e["target"] for e in _edges(result["upstream"][INFERRED])] == ["g1"]


def test_an_inferred_edge_is_never_returned_as_confirmed():
    result = trace(_graph(edges=[_edge("c1", "r1", basis=INFERRED, authority="agent:x")]), "c1")
    assert _edges(result["upstream"][CONFIRMED]) == []
    assert len(result["upstream"][INFERRED]) == 1


def test_the_basis_is_on_every_line_not_in_a_heading(tmp_path):
    """A reader scrolls past a heading and reads the lines."""
    result = _run(tmp_path, _graph(edges=[_edge("c1", "r1", basis=INFERRED,
                                                authority="agent:x")]), "c1")
    assert result.returncode == 0, (result.stdout, result.stderr)
    for line in result.stdout.splitlines():
        if "implements" in line:
            assert f"[{INFERRED}]" in line, line


# ── it does not hold a second copy of the verdict ────────────────────────────
@pytest.mark.parametrize("key", ["status", "verdict", "passed", "result", "outcome", "gate",
                                 "final_status", "Verdict", "STATUS"])
def test_an_edge_may_not_carry_a_verdict(key):
    """Two places answering "did this verify" is one too many, and the copy is the one that
    goes stale. `assurance.py` is the authority and an edge names it."""
    problems = validate(_graph(edges=[_edge("c1", "r1") | {key: "passed"}]))
    assert any("would put a verdict in the graph" in p for p in problems), (key, problems)


def test_an_edge_to_evidence_names_the_receipt_and_stops():
    graph = _graph(nodes=[_node("c1"), _node("e1", EVIDENCE)],
                   edges=[_edge("c1", "e1", relation=VERIFIED_BY,
                                authority="receipt:rig-20260822-000000-a-task")])
    assert validate(graph) == []
    edge = load(graph)[1][0]
    assert set(edge.as_dict()) == graph_mod.EDGE_FIELDS
    assert "receipt:" in edge.authority


def test_the_edge_schema_has_nowhere_to_put_a_verdict():
    """Stated as a property of the shape rather than a list of forbidden spellings that has to
    keep up with whatever somebody thinks of next."""
    assert graph_mod.EDGE_FIELDS == {"source", "target", "relation", "basis", "authority"}


# ── invalidation is carried, not deleted ─────────────────────────────────────
def _stale_graph():
    return _graph(nodes=[_node("c1"), _node("e1", EVIDENCE), _node("e2", EVIDENCE)],
                  edges=[_edge("c1", "e1", relation=VERIFIED_BY, authority="receipt:t1"),
                         _edge("e2", "e1", relation=INVALIDATES,
                               authority="receipt:t1 target moved")])


def test_an_invalidated_edge_is_still_recorded():
    """Removing it would leave a chain that reads as though nothing had ever supported the
    change, which is a different and worse claim than "this was supported and then stopped"."""
    result = trace(_stale_graph(), "c1")
    assert [e["target"] for e in _edges(result["upstream"][CONFIRMED])] == ["e1"]
    assert [(i["edge"]["source"], i["edge"]["target"])
            for i in result["invalidated"]] == [("c1", "e1")]


def test_invalidated_is_not_subtracted_from_the_others():
    """An edge that was confirmed and later invalidated is both, and a caller deciding what to
    trust needs to know it was ever there."""
    result = trace(_stale_graph(), "c1")
    assert _edges(result["upstream"][CONFIRMED]) and result["invalidated"]
    assert _edges(result["upstream"][CONFIRMED])[0] == result["invalidated"][0]["edge"]


def test_an_untouched_edge_is_not_invalidated():
    graph = _graph(nodes=[_node("c1"), _node("e1", EVIDENCE), _node("e2", EVIDENCE),
                          _node("r1", REQUIREMENT)],
                   edges=[_edge("c1", "r1"),
                          _edge("c1", "e1", relation=VERIFIED_BY, authority="receipt:t1"),
                          _edge("e2", "e1", relation=INVALIDATES, authority="receipt:a-later-run")])
    assert [(e.source, e.target) for e in invalidated(load(graph)[1])] == [("c1", "e1")]


def test_the_invalidates_edge_is_not_itself_a_step_in_the_chain():
    """It is a statement about the graph, not a link somebody would follow from a commit to a
    requirement."""
    result = trace(_stale_graph(), "e1")
    assert all(e["relation"] != INVALIDATES
               for basis in (CONFIRMED, INFERRED)
               for e in _edges(result["downstream"][basis]))


# ── the command exits with the answer ────────────────────────────────────────
def test_a_trace_exits_zero(tmp_path):
    result = _run(tmp_path, _graph(), "c1")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "r1" in result.stdout


def test_a_node_nobody_recorded_is_not_traceable(tmp_path):
    """Exit 1 rather than 2: the file was fine, the question was about something that is not
    in it."""
    result = _run(tmp_path, _graph(), "nowhere")
    assert result.returncode == 1, (result.returncode, result.stdout)
    assert "not a node" in json.loads(result.stdout)["error"]


def test_a_graph_that_is_not_one_is_its_own_status(tmp_path):
    result = _run(tmp_path, {"schema": "wrong"}, "c1")
    assert result.returncode == 1, (result.returncode, result.stdout)
    assert "not a provenance graph" in json.loads(result.stdout)["error"]


def test_a_graph_that_cannot_be_read_is_an_execution_error(tmp_path):
    result = subprocess.run(
        [sys.executable, str(WORKBENCH), "provenance", str(tmp_path / "absent.json"), "c1"],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode == 2
    assert "execution-error" in result.stdout


def test_a_document_naming_one_key_twice_is_refused(tmp_path):
    """JSON allows a key twice and `json.loads` keeps the last one silently, so an edge whose
    `basis` appears twice reaches the trace saying only the last one."""
    text = ('{"schema": "%s", "nodes": [{"id": "c1", "kind": "commit", "label": "l"}], '
            '"edges": [{"source": "c1", "target": "c1", "relation": "implements", '
            '"basis": "inferred", "basis": "confirmed", "authority": "a"}]}' % SCHEMA)
    result = _run(tmp_path, None, "c1", graph_text=text)
    assert result.returncode == 2, (result.returncode, result.stdout)
    assert "twice" in json.loads(result.stdout)["error"]


@pytest.mark.parametrize("direction,expected", [
    ("up", ("r1", [])), ("down", ([], "c1")), ("both", ("r1", []))])
def test_the_direction_decides_which_way_the_chain_is_followed(tmp_path, direction, expected):
    result = trace(_graph(), "c1" if direction != "down" else "r1", direction)
    up = [e["target"] for e in _edges(result["upstream"][CONFIRMED])]
    down = [e["source"] for e in _edges(result["downstream"][CONFIRMED])]
    assert (up or []) == ([expected[0]] if expected[0] else [])
    assert (down or []) == ([expected[1]] if expected[1] else [])


def test_a_direction_nobody_defined_is_refused():
    with pytest.raises(ValueError, match="not one of up, down, both"):
        trace(_graph(), "c1", "sideways")


# ── it does not infer ────────────────────────────────────────────────────────
def test_the_judgement_touches_nothing_and_calls_no_model():
    """Deciding that this commit implements that requirement is reading two things and
    concluding a third. A module that did it would leave nothing a gate could check."""
    import ast
    tree = ast.parse((REPO_ROOT / "rig_workbench" / "workbench"
                      / "provenance_graph.py").read_text(encoding="utf-8"))
    reaching = {"subprocess", "socket", "http", "urllib", "requests", "os", "open"}
    judging = {"validate", "load", "trace", "invalidated", "node_problems", "edge_problems"}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in judging):
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        names |= {a.name.split(".")[0] for n in ast.walk(node)
                  if isinstance(n, ast.Import) for a in n.names}
        assert not names & reaching, (node.name, sorted(names & reaching))
    module_level = {a.name.split(".")[0] for n in tree.body
                    if isinstance(n, ast.Import) for a in n.names}
    module_level |= {(n.module or "").split(".")[0] for n in tree.body
                     if isinstance(n, ast.ImportFrom)}
    assert not module_level & reaching, module_level


# ── gaps the mutation sweep found ────────────────────────────────────────────
def test_the_downstream_trace_keeps_the_two_kinds_apart_too():
    """The same rule read from the other end. A chain is followed both ways, and merging on
    one of them is merging."""
    result = trace(_graph(nodes=[_node("c1"), _node("r1", REQUIREMENT), _node("g1", GOAL)],
                          edges=[_edge("c1", "r1", basis=CONFIRMED),
                                 _edge("g1", "r1", relation=SATISFIES, basis=INFERRED,
                                       authority="agent:planner")]),
                   "r1")
    assert [e["source"] for e in _edges(result["downstream"][CONFIRMED])] == ["c1"]
    assert [e["source"] for e in _edges(result["downstream"][INFERRED])] == ["g1"]


def test_an_edge_out_of_an_invalidated_node_is_stale_as_well():
    """Staleness is about the node, not about which end of an edge it happens to sit on: an
    edge *from* evidence that no longer holds is no more trustworthy than one to it."""
    graph = _graph(nodes=[_node("e1", EVIDENCE), _node("d1", COMMIT), _node("e2", EVIDENCE)],
                   edges=[_edge("e1", "d1", relation=VERIFIED_BY, authority="receipt:t1"),
                          _edge("e2", "e1", relation=INVALIDATES, authority="receipt:a-later-run")])
    assert [(e.source, e.target) for e in invalidated(load(graph)[1])] == [("e1", "d1")]


def test_a_graph_that_does_not_say_what_it_is_is_refused():
    """The schema id is what tells a reader which vocabulary `implements` belongs to."""
    for schema in ("rig.something-else/v1", None, ""):
        assert any("schema" in p for p in validate(_graph(schema=schema))), repr(schema)


@pytest.mark.parametrize("node_id", [None, "", "   ", " padded ", 42])
def test_a_node_with_no_id_is_refused(node_id):
    """Edges point at ids, so two nodes that are both `""` are one node to a lookup and two to
    everything else."""
    problems = validate(_graph(nodes=[_node(node_id)], edges=[]))
    assert any("has to name something" in p for p in problems), node_id


@pytest.mark.parametrize("field", ["source", "target"])
@pytest.mark.parametrize("value", [None, "", "   ", " c1 ", 42])
def test_an_edge_endpoint_that_names_nothing_is_refused(field, value):
    problems = validate(_graph(edges=[_edge("c1", "r1") | {field: value}]))
    assert any("has to name a node" in p for p in problems), (field, value)


@pytest.mark.parametrize("kwargs,fragment", [
    ({"id": " padded "}, "has to name something"),
    ({"kind": "vibes"}, "kind"),
    ({"label": ""}, "has to say what this is"),
])
def test_a_node_cannot_be_built_in_a_state_the_document_would_be_refused_in(kwargs, fragment):
    fields = {"id": "c1", "kind": COMMIT, "label": "a commit"} | kwargs
    with pytest.raises(ValueError, match=fragment):
        Node(**fields)


@pytest.mark.parametrize("kwargs,fragment", [
    ({"source": " padded "}, "has to name a node"),
    ({"relation": "relates-to"}, "relation"),
    ({"basis": "probably"}, "basis"),
    ({"authority": ""}, "authority"),
    ({"target": "c1"}, "relates to itself"),
])
def test_an_edge_cannot_be_built_in_a_state_the_document_would_be_refused_in(kwargs, fragment):
    """One rule both paths reach. The two modules before this one took four review rounds each
    to learn that a check on one ingestion path is a check on one ingestion path."""
    fields = {"source": "c1", "target": "r1", "relation": IMPLEMENTS, "basis": CONFIRMED,
              "authority": "receipt:t1"} | kwargs
    with pytest.raises(ValueError, match=fragment):
        Edge(**fields)


def test_up_and_down_are_different_questions():
    """`up` asks what this rests on and `down` asks what rests on it. Answering both to either
    would make every trace look like the whole graph."""
    graph = _graph(nodes=[_node("c1"), _node("r1", REQUIREMENT), _node("e1", EVIDENCE)],
                   edges=[_edge("c1", "r1"),
                          _edge("e1", "c1", relation=VERIFIED_BY, authority="receipt:t1")])
    up = trace(graph, "c1", "up")
    assert [e["target"] for e in _edges(up["upstream"][CONFIRMED])] == ["r1"]
    assert up["downstream"][CONFIRMED] == [], "up must not answer down"

    down = trace(graph, "c1", "down")
    assert [e["source"] for e in _edges(down["downstream"][CONFIRMED])] == ["e1"]
    assert _edges(down["upstream"][CONFIRMED]) == [], "down must not answer up"


def test_the_report_says_which_edges_no_longer_hold(tmp_path):
    result = _run(tmp_path, _stale_graph(), "c1", "--no-resolve")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "invalidated" in result.stdout
    assert "no longer holding" in result.stdout


# ── what round 1 found ───────────────────────────────────────────────────────
def test_a_guess_that_something_went_stale_is_not_a_confirmed_one():
    """The rule this module is built on, applied to the one place it applies to itself.
    Collapsing an `invalidates` edge to its target drops its basis, and a conclusion that
    evidence went stale then reads exactly like an observation that it did."""
    graph = _graph(nodes=[_node("c1"), _node("e1", EVIDENCE), _node("e2", EVIDENCE)],
                   edges=[_edge("c1", "e1", relation=VERIFIED_BY, authority="receipt:t1"),
                          _edge("e2", "e1", relation=INVALIDATES, basis=INFERRED,
                                authority="agent:reviewer")])
    result = trace(graph, "c1")
    [item] = result["invalidated"]
    assert item["edge"]["basis"] == CONFIRMED, "the supporting edge was confirmed"
    assert [s["statement"]["basis"] for s in item["invalidated_by"]] == [INFERRED]
    assert [s["statement"]["authority"] for s in item["invalidated_by"]] == ["agent:reviewer"]


def test_the_report_says_who_said_the_edge_no_longer_holds(tmp_path):
    graph = _graph(nodes=[_node("c1"), _node("e1", EVIDENCE), _node("e2", EVIDENCE)],
                   edges=[_edge("c1", "e1", relation=VERIFIED_BY, authority="receipt:t1"),
                          _edge("e2", "e1", relation=INVALIDATES, basis=INFERRED,
                                authority="agent:reviewer")])
    result = _run(tmp_path, graph, "c1", "--no-resolve")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert f"per [{INFERRED}] agent:reviewer" in result.stdout, result.stdout


def test_a_chain_is_followed_to_the_end_not_one_hop():
    """A commit that implements a requirement that satisfies a goal answers "why does this
    exist" with the goal; stopping at the requirement answers it with a restatement."""
    graph = _graph(nodes=[_node("c1"), _node("r1", REQUIREMENT), _node("g1", GOAL)],
                   edges=[_edge("c1", "r1"),
                          _edge("r1", "g1", relation=SATISFIES, authority="policy:the brief")])
    up = trace(graph, "c1", "up")
    assert {e["target"] for e in _edges(up["upstream"][CONFIRMED])} == {"r1", "g1"}

    down = trace(graph, "g1", "down")
    assert {e["source"] for e in _edges(down["downstream"][CONFIRMED])} == {"r1", "c1"}


def test_a_step_further_along_keeps_its_own_basis():
    """Otherwise a chain would be as confirmed as its first link."""
    graph = _graph(nodes=[_node("c1"), _node("r1", REQUIREMENT), _node("g1", GOAL)],
                   edges=[_edge("c1", "r1", basis=CONFIRMED),
                          _edge("r1", "g1", relation=SATISFIES, basis=INFERRED,
                                authority="agent:planner")])
    up = trace(graph, "c1", "up")
    assert [e["target"] for e in _edges(up["upstream"][CONFIRMED])] == ["r1"]
    assert [e["target"] for e in _edges(up["upstream"][INFERRED])] == ["g1"]


def test_a_loop_does_not_make_the_traversal_run_forever():
    """Self-edges are refused; a longer loop is not, and a traversal that revisited a node
    would return the same edge twice and never finish."""
    graph = _graph(nodes=[_node("a"), _node("b", REQUIREMENT), _node("c", GOAL)],
                   edges=[_edge("a", "b"), _edge("b", "c", relation=SATISFIES,
                                                 authority="policy:the brief"),
                          _edge("c", "a", relation=DERIVED_FROM, authority="policy:the brief")])
    up = trace(graph, "a", "up")
    assert len(up["upstream"][CONFIRMED]) == 3
    assert len({(e["source"], e["target"]) for e in _edges(up["upstream"][CONFIRMED])}) == 3


@pytest.mark.parametrize("where", ["root", "node", "edge"])
def test_a_key_that_is_not_a_string_is_reported_rather_than_raised(where):
    """`validate` promises a list of problems. JSON cannot produce such a key; a caller
    building the dict can, and that is the other ingestion path this module answers."""
    graph = _graph()
    if where == "root":
        graph[42] = "x"
    elif where == "node":
        graph["nodes"][0][42] = "x"
    else:
        graph["edges"][0][42] = "x"
    problems = validate(graph)
    assert any("is not a key" in p for p in problems), (where, problems)


def test_a_label_is_prose_and_the_guarantee_is_about_fields():
    """Worth stating at the width the schema can hold: no *field* carries a verdict, which is
    what a consumer can rely on. A node labelled "passed" validates, because a label is prose
    for a human and nothing here reads it."""
    assert validate(_graph(nodes=[_node("e1", EVIDENCE, label="passed"), _node("c1")],
                           edges=[_edge("c1", "e1", relation=VERIFIED_BY,
                                        authority="receipt:t1")])) == []
    assert "verdict" not in graph_mod.EDGE_FIELDS and "status" not in graph_mod.EDGE_FIELDS


# ── what round 2 found ───────────────────────────────────────────────────────
def test_the_authority_kinds_are_the_ones_the_prose_names():
    """Parameterising from the tuple would let a documented kind be removed with its own test
    case, leaving the suite green and `person:…` gone."""
    assert set(graph_mod.RESOLVABLE_AUTHORITIES) == {"receipt", "git", "person", "policy"}
    assert set(graph_mod.DECLARED_AUTHORITIES) == {"agent"}


@pytest.mark.parametrize("kind", ["agent"])
def test_something_that_concluded_cannot_be_confirmed(kind):
    """A conclusion is not an observation with a different adjective. `basis="confirmed"` plus
    `authority="agent:guess"` used to print as `[confirmed] … (per agent:guess)`."""
    problems = validate(_graph(edges=[_edge("c1", "r1", basis=CONFIRMED,
                                            authority=f"{kind}:planner")]))
    assert any("still an agent that was sure" in p for p in problems), kind


@pytest.mark.parametrize("kind", ["receipt", "git", "person", "policy"])
def test_a_confirmed_edge_names_something_somebody_could_look_at(kind):
    assert validate(_graph(edges=[_edge("c1", "r1", basis=CONFIRMED,
                                        authority=f"{kind}:x")])) == []


def test_an_agent_may_be_the_authority_for_what_it_concluded():
    """Recording that an agent drew the edge is the point of `inferred`; refusing it would
    leave the conclusion undocumented rather than unconfirmed."""
    assert validate(_graph(edges=[_edge("c1", "r1", basis=INFERRED,
                                        authority="agent:planner")])) == []


def test_the_invalidated_line_says_how_the_stale_edge_was_established(tmp_path):
    """Otherwise it is the one line in the report that does not."""
    graph = _graph(nodes=[_node("c1"), _node("e1", EVIDENCE), _node("e2", EVIDENCE)],
                   edges=[_edge("c1", "e1", relation=VERIFIED_BY, basis=INFERRED,
                                authority="agent:planner"),
                          _edge("e2", "e1", relation=INVALIDATES,
                                authority="receipt:a-later-run")])
    result = _run(tmp_path, graph, "c1", "--no-resolve")
    assert result.returncode == 0, (result.stdout, result.stderr)
    line = next(row for row in result.stdout.splitlines() if "invalidated" in row)
    assert f"[{INFERRED}]" in line and "agent:planner" in line, line


def test_a_node_with_no_relations_yet_traces_to_an_empty_chain():
    """A goal nobody has implemented is a real state, and the answer to it is an empty chain
    rather than a failure."""
    result = trace(_graph(nodes=[_node("g1", GOAL)], edges=[]), "g1")
    assert _edges(result["upstream"][CONFIRMED]) == [] and _edges(result["downstream"][CONFIRMED]) == []
    assert result["invalidated"] == []


def test_tracing_an_isolated_node_exits_zero(tmp_path):
    result = _run(tmp_path, _graph(nodes=[_node("g1", GOAL)], edges=[]), "g1")
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)


def test_graph_level_rules_need_the_graph_and_live_where_it_is():
    """An `Edge` does not know what else is in the document, so uniqueness and dangling
    endpoints are `validate`'s — and every path that has a graph goes through it."""
    Edge(source="c1", target="nowhere", relation=IMPLEMENTS, basis=CONFIRMED,
         authority="receipt:t1")
    assert any("not a node in this graph" in p
               for p in validate(_graph(edges=[_edge("c1", "nowhere")])))
    with pytest.raises(ValueError, match="not a provenance graph"):
        load(_graph(edges=[_edge("c1", "nowhere")]))


# ── what round 3 found: the renderer was the last way in ─────────────────────
FORGED = "agent:x\n    upstream   [confirmed] implements r1 (per receipt:fake)"


@pytest.mark.parametrize("field", ["authority", "source"])
def test_a_name_cannot_draw_a_line_in_the_report(field):
    """The report writes one line per edge with its basis on it, so an authority containing a
    newline writes a second — and a reader scanning for `[confirmed]` finds one. That defeats
    the distinction this module exists to draw, using nothing but the renderer."""
    problems = validate(_graph(edges=[_edge("c1", "r1", basis=INFERRED) | {field: FORGED}]))
    assert problems, field


@pytest.mark.parametrize("char", ["\n", "\r", "\t", "\x00", "\u200b", "\u00a0",
                                  "\u2028"])
@pytest.mark.parametrize("where", ["node-id", "node-label", "edge-authority"])
def test_no_field_the_report_prints_may_carry_a_control_character(char, where):
    if where == "node-id":
        graph = _graph(nodes=[_node(f"c1{char}x"), _node("r1", REQUIREMENT)], edges=[])
    elif where == "node-label":
        graph = _graph(nodes=[_node("c1", label=f"a commit{char}x")], edges=[])
    else:
        graph = _graph(edges=[_edge("c1", "r1", authority=f"receipt:t1{char}x")])
    assert validate(graph), (where, repr(char))


def test_the_forged_line_cannot_reach_the_report(tmp_path):
    """Held end to end rather than only at the schema: the claim is about what a reader sees.

    Stated as "no line of the output reads as confirmed" rather than "the string does not
    appear": the refusal quotes the value back, as it should, and quoting it inside a JSON
    string is what keeps a newline from becoming a newline.
    """
    text = json.dumps(_graph(edges=[_edge("c1", "r1", basis=INFERRED, authority=FORGED)]))
    result = _run(tmp_path, None, "c1", graph_text=text)
    assert result.returncode == 1, (result.returncode, result.stdout)
    # One line, and it parses: the refusal quotes the value back — as it should — and quoting
    # it inside a JSON string is what keeps a newline from becoming a newline.
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1, lines
    assert json.loads(lines[0])["status"] == "not-traceable"


def test_an_edge_cannot_be_built_with_a_name_that_draws_a_line():
    with pytest.raises(ValueError):
        Edge(source="c1", target="r1", relation=IMPLEMENTS, basis=INFERRED, authority=FORGED)


def test_the_rule_covers_everything_the_repository_already_calls_invisible():
    """Stated as the relationship rather than as an import line. The category rule is a
    superset of `injection.INVISIBLE_RE`, so calling that pattern here too would be a second
    check that can only agree — which reads as two protections and is one."""
    from rig_workbench.workbench.injection import INVISIBLE_RE

    missed = [chr(c) for c in range(0x110000)
              if INVISIBLE_RE.search(chr(c)) and graph_mod._is_name(f"x{chr(c)}y")]
    assert missed == [], [hex(ord(c)) for c in missed]


# ── what round 4 found: a chain is as good as its weakest link ───────────────
def _laundering_graph():
    """`c1 -[inferred]-> r1 -[confirmed]-> g1`: reaching `g1` from `c1` depends on the guess,
    however carefully the second step was checked."""
    return _graph(nodes=[_node("c1"), _node("r1", REQUIREMENT), _node("g1", GOAL)],
                  edges=[_edge("c1", "r1", basis=INFERRED, authority="agent:planner"),
                         _edge("r1", "g1", relation=SATISFIES, basis=CONFIRMED,
                               authority="policy:the brief")])


def test_a_confirmed_step_past_an_inferred_one_is_not_confirmed_reachability():
    """Reporting it as confirmed would launder the inference into the part of the answer a
    reader trusts."""
    up = trace(_laundering_graph(), "c1", "up")
    assert _edges(up["upstream"][CONFIRMED]) == []
    assert {e["target"] for e in _edges(up["upstream"][INFERRED])} == {"r1", "g1"}


def test_the_edge_keeps_its_own_basis_even_when_the_path_is_inferred():
    """The partition is about reaching it; "somebody verified this link" is still worth
    reading, and the entry still says so."""
    up = trace(_laundering_graph(), "c1", "up")
    to_goal = next(e for e in _edges(up["upstream"][INFERRED]) if e["target"] == "g1")
    assert to_goal["basis"] == CONFIRMED


def test_reaching_it_from_somewhere_else_can_be_confirmed():
    """The partition is about the node asked about, so the same edge answers differently from
    a different starting point — which is what makes it a statement about reachability."""
    up = trace(_laundering_graph(), "r1", "up")
    assert [e["target"] for e in _edges(up["upstream"][CONFIRMED])] == ["g1"]


def test_the_report_says_the_link_was_checked_and_the_way_to_it_was_not(tmp_path):
    result = _run(tmp_path, _laundering_graph(), "c1", "--direction", "up")
    assert result.returncode == 0, (result.stdout, result.stderr)
    line = next(row for row in result.stdout.splitlines() if "g1" in row)
    assert f"[{INFERRED}]" in line and "reached through an inferred step" in line, line


def test_a_wholly_confirmed_chain_stays_confirmed():
    graph = _graph(nodes=[_node("c1"), _node("r1", REQUIREMENT), _node("g1", GOAL)],
                   edges=[_edge("c1", "r1"),
                          _edge("r1", "g1", relation=SATISFIES,
                                authority="policy:the brief")])
    up = trace(graph, "c1", "up")
    assert {e["target"] for e in _edges(up["upstream"][CONFIRMED])} == {"r1", "g1"}
    assert _edges(up["upstream"][INFERRED]) == []


# ── what round 5 found: the path basis had two more places to go ─────────────
def _invalidated_past_a_guess():
    """`c1 -[inferred]-> r1 -[confirmed]-> e1`, and `e1`'s support invalidated."""
    return _graph(nodes=[_node("c1"), _node("r1", REQUIREMENT), _node("e1", EVIDENCE),
                         _node("e2", EVIDENCE)],
                  edges=[_edge("c1", "r1", basis=INFERRED, authority="agent:planner"),
                         _edge("r1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                               authority="receipt:t1"),
                         _edge("e2", "e1", relation=INVALIDATES,
                               authority="receipt:a-later-run")])


def test_the_invalidation_section_says_how_the_edge_was_reached():
    """It is exactly where a reader is deciding what to stop trusting, so "you could only get
    here on a guess" belongs on the line."""
    result = trace(_invalidated_past_a_guess(), "c1", "up")
    [item] = result["invalidated"]
    assert item["edge"]["basis"] == CONFIRMED
    assert item["path_basis"] == INFERRED


def test_the_invalidated_line_says_it_too(tmp_path):
    # `--no-resolve`: this is about the path basis, and looking the receipts up would answer a
    # different question — they are fixtures and do not exist.
    result = _run(tmp_path, _invalidated_past_a_guess(), "c1", "--direction", "up",
                  "--no-resolve")
    assert result.returncode == 0, (result.stdout, result.stderr)
    line = next(row for row in result.stdout.splitlines() if "invalidated" in row)
    assert f"[{INFERRED}]" in line and "reached through an inferred step" in line, line


def _diamond(order):
    """One node reachable by a checked route and a concluded one. Which arrives first is a
    fact about the file, and must not be a fact about the answer."""
    edges = [_edge("c1", "sure", basis=CONFIRMED, authority="receipt:t1"),
             _edge("c1", "guess", basis=INFERRED, authority="agent:planner"),
             _edge("sure", "shared", relation=SATISFIES, authority="policy:the brief"),
             _edge("guess", "shared", relation=SATISFIES, authority="policy:the brief"),
             _edge("shared", "g1", relation=SATISFIES, authority="policy:the brief")]
    return _graph(nodes=[_node("c1"), _node("sure", REQUIREMENT), _node("guess", REQUIREMENT),
                         _node("shared", REQUIREMENT), _node("g1", GOAL)],
                  edges=[edges[i] for i in order])


def test_a_node_reachable_both_ways_is_reported_both_ways():
    """Concealing either route would answer "how do I know" with only one of its answers."""
    result = trace(_diamond([0, 1, 2, 3, 4]), "c1", "up")
    to_goal_confirmed = [e for e in _edges(result["upstream"][CONFIRMED]) if e["target"] == "g1"]
    to_goal_inferred = [e for e in _edges(result["upstream"][INFERRED]) if e["target"] == "g1"]
    assert to_goal_confirmed and to_goal_inferred


def test_the_answer_does_not_depend_on_the_order_the_edges_were_written():
    """`seen` on the node alone expanded whichever route arrived first, so the partition of
    everything past a shared node turned on the file's ordering. Two readings of one document
    have to agree."""
    forward = trace(_diamond([0, 1, 2, 3, 4]), "c1", "up")
    reversed_ = trace(_diamond([4, 3, 2, 1, 0]), "c1", "up")
    for basis in (CONFIRMED, INFERRED):
        assert sorted(map(str, _edges(forward["upstream"][basis]))) == \
            sorted(map(str, _edges(reversed_["upstream"][basis]))), basis


def test_the_json_output_carries_the_whole_answer(tmp_path):
    result = _run(tmp_path, _invalidated_past_a_guess(), "c1", "--direction", "up", "--json")
    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["schema"] == SCHEMA
    assert payload["node"] == "c1" and payload["direction"] == "up"
    assert set(payload["upstream"]) == {CONFIRMED, INFERRED}
    assert payload["authorities_looked_up"] is True
    assert _edges(payload["upstream"][CONFIRMED]) == []
    assert {e["target"] for e in _edges(payload["upstream"][INFERRED])} == {"r1", "e1"}
    assert payload["invalidated"][0]["path_basis"] == INFERRED


def test_one_edge_is_reported_once_per_way_it_can_be_reached_and_no_more():
    """A node reachable both ways is expanded twice, and an *inferred* edge out of it comes
    back as inferred from either — the same answer, and it belongs in the list once."""
    graph = _graph(nodes=[_node("c1"), _node("sure", REQUIREMENT), _node("guess", REQUIREMENT),
                          _node("shared", REQUIREMENT), _node("g1", GOAL)],
                   edges=[_edge("c1", "sure", basis=CONFIRMED, authority="receipt:t1"),
                          _edge("c1", "guess", basis=INFERRED, authority="agent:planner"),
                          _edge("sure", "shared", relation=SATISFIES,
                                authority="policy:the brief"),
                          _edge("guess", "shared", relation=SATISFIES,
                                authority="policy:the brief"),
                          _edge("shared", "g1", relation=SATISFIES, basis=INFERRED,
                                authority="agent:planner")])
    up = trace(graph, "c1", "up")
    to_goal = [e for e in _edges(up["upstream"][INFERRED]) if e["target"] == "g1"]
    assert len(to_goal) == 1, to_goal


# ── what round 6 found: naming a receipt is not there being one ──────────────
def _receipt_graph(name="rig-does-not-exist"):
    return _graph(nodes=[_node("c1"), _node("e1", EVIDENCE)],
                  edges=[_edge("c1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                               authority=f"receipt:{name}")])


def test_a_reference_nobody_could_find_is_not_confirmation():
    """`receipt:this-does-not-exist` used to print under `[confirmed]`, giving an unchecked
    assertion the presentation of verified evidence."""
    result = trace(_receipt_graph(), "c1", "up", resolve=lambda authority: False)
    assert _edges(result["upstream"][CONFIRMED]) == []
    assert [e["target"] for e in _edges(result["upstream"][INFERRED])] == ["e1"]
    assert result["authorities_looked_up"] is True


def test_a_reference_somebody_found_stays_confirmed():
    result = trace(_receipt_graph(), "c1", "up", resolve=lambda authority: True)
    assert [e["target"] for e in _edges(result["upstream"][CONFIRMED])] == ["e1"]
    assert _edges(result["upstream"][INFERRED]) == []


def test_a_kind_the_resolver_cannot_check_is_not_demoted_for_it():
    """A `person:` cannot be looked up on this machine, and treating "not a kind I can check"
    the same as "checked and absent" would punish the honest answer."""
    graph = _graph(edges=[_edge("c1", "r1", basis=CONFIRMED, authority="person:someone")])
    result = trace(graph, "c1", "up", resolve=lambda authority: None)
    assert [e["target"] for e in _edges(result["upstream"][CONFIRMED])] == ["r1"]
    assert _edges(result["upstream"][INFERRED]) == []


def test_without_a_resolver_the_answer_says_the_authorities_were_not_looked_up():
    result = trace(_receipt_graph(), "c1", "up")
    assert result["authorities_looked_up"] is False
    assert [e["target"] for e in _edges(result["upstream"][CONFIRMED])] == ["e1"]


def test_the_report_says_when_nothing_was_looked_up(tmp_path):
    result = _run(tmp_path, _receipt_graph(), "c1", "--no-resolve")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "some authorities not looked up" in result.stdout


def test_the_command_looks_a_receipt_up_and_finds_it_missing(tmp_path):
    result = _run(tmp_path, _receipt_graph(), "c1", "--direction", "up", "--json")
    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["authorities_looked_up"] is True
    assert _edges(payload["upstream"][CONFIRMED]) == []
    assert [e["target"] for e in _edges(payload["upstream"][INFERRED])] == ["e1"]


def test_the_repository_resolver_answers_for_the_kinds_it_can(monkeypatch, tmp_path):
    """`receipt:` is a run directory and `git:` an object; the others are `None`, which is the
    honest answer rather than a demotion."""
    import subprocess as real_subprocess

    run = tmp_path / ".rig" / "runs" / "rig-a-task"
    run.mkdir(parents=True)
    (run / "provenance.json").write_text(_signed_record(tmp_path, "rig-a-task"), encoding="utf-8")

    class _Result:
        returncode = 0

    monkeypatch.setattr(real_subprocess, "run", lambda *a, **k: _Result())
    resolves = graph_mod.repository_resolver(tmp_path)
    assert resolves("receipt:rig-a-task") is True
    assert resolves("receipt:rig-not-a-task") is False
    assert resolves(f"git:{OID}") is True
    assert resolves("person:someone") is None
    assert resolves("policy:the brief") is None


# ── what round 7 found: resolution stopped before the invalidation section ───
def _stale_with_missing_receipts():
    return _graph(nodes=[_node("c1"), _node("e1", EVIDENCE), _node("e2", EVIDENCE)],
                  edges=[_edge("c1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                               authority="receipt:not-a-task"),
                         _edge("e2", "e1", relation=INVALIDATES, basis=CONFIRMED,
                               authority="receipt:also-not-a-task")])


def test_the_invalidation_section_looks_its_authorities_up_too():
    """A missing receipt presented as the stale relationship's authority — or as the authority
    claiming it went stale — is the same unchecked assertion wearing the same `[confirmed]`, in
    the section where a reader is deciding what to stop trusting."""
    result = trace(_stale_with_missing_receipts(), "c1", "up", resolve=lambda a: False)
    [item] = result["invalidated"]
    assert item["resolution"] == graph_mod.MISSING
    assert [s["resolution"] for s in item["invalidated_by"]] == [graph_mod.MISSING]


def test_a_found_receipt_in_the_invalidation_section_stays_found():
    result = trace(_stale_with_missing_receipts(), "c1", "up", resolve=lambda a: True)
    [item] = result["invalidated"]
    assert item["resolution"] != graph_mod.MISSING
    assert [s["resolution"] for s in item["invalidated_by"]] == [graph_mod.FOUND]


def test_the_invalidated_lines_say_unresolved(tmp_path):
    result = _run(tmp_path, _stale_with_missing_receipts(), "c1", "--direction", "up")
    assert result.returncode == 0, (result.stdout, result.stderr)
    lines = result.stdout.splitlines()
    stale_line = next(row for row in lines if "invalidated " in row)
    assert "[unresolved]" in stale_line, stale_line
    said_line = next(row for row in lines if "per [" in row and "also-not-a-task" in row)
    assert "[unresolved]" in said_line, said_line


def test_resolution_reaches_the_downstream_direction_too():
    """The loop runs over both ways; covering only `up` would let one of them be dropped."""
    graph = _graph(nodes=[_node("c1"), _node("e1", EVIDENCE)],
                   edges=[_edge("c1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                                authority="receipt:not-a-task")])
    down = trace(graph, "e1", "down", resolve=lambda a: False)
    assert _edges(down["downstream"][CONFIRMED]) == []
    assert [e["source"] for e in _edges(down["downstream"][INFERRED])] == ["c1"]


def test_the_downstream_json_says_so_too(tmp_path):
    graph = _graph(nodes=[_node("c1"), _node("e1", EVIDENCE)],
                   edges=[_edge("c1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                                authority="receipt:not-a-task")])
    result = _run(tmp_path, graph, "e1", "--direction", "down", "--json")
    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert _edges(payload["downstream"][CONFIRMED]) == []
    assert [e["source"] for e in _edges(payload["downstream"][INFERRED])] == ["c1"]


@pytest.mark.parametrize("verdict", [0, 1, "", "yes", [], {}, 0.0])
def test_a_resolver_that_answers_something_else_is_refused(verdict):
    """Only `False` demotes, so a resolver returning `0` would leave an authority confirmed
    while the answer said the authorities had been looked up. A contract only one caller
    happens to honour is a contract nobody checks."""
    with pytest.raises(ValueError, match="True, False or None"):
        trace(_receipt_graph(), "c1", "up", resolve=lambda a: verdict)


# ── what round 8 found: resolution had one more place not to reach ───────────
def test_a_confirmed_edge_on_an_inferred_path_is_still_looked_up():
    """It used to sit in the inferred partition with `basis: confirmed` and no resolution, so
    a missing receipt survived by being reached the long way round."""
    graph = _graph(nodes=[_node("c1"), _node("r1", REQUIREMENT), _node("e1", EVIDENCE)],
                   edges=[_edge("c1", "r1", basis=INFERRED, authority="agent:planner"),
                          _edge("r1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                                authority="receipt:not-a-task")])
    up = trace(graph, "c1", "up", resolve=lambda a: False)
    entry = next(e for e in up["upstream"][INFERRED] if e["edge"]["target"] == "e1")
    assert entry["resolution"] == graph_mod.MISSING


def test_an_unresolved_authority_makes_everything_past_it_unreliable():
    """A step whose authority nobody can find is not a step you can rely on, so the chain
    beyond it is not either — the same rule as an inferred step, for a different reason."""
    graph = _graph(nodes=[_node("c1"), _node("e1", EVIDENCE), _node("g1", GOAL)],
                   edges=[_edge("c1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                                authority="receipt:not-a-task"),
                          _edge("e1", "g1", relation=SATISFIES, basis=CONFIRMED,
                                authority="policy:the brief")])
    up = trace(graph, "c1", "up", resolve=lambda a: a.startswith("policy"))
    assert _edges(up["upstream"][CONFIRMED]) == []
    assert {e["target"] for e in _edges(up["upstream"][INFERRED])} == {"e1", "g1"}


def test_the_two_dimensions_stay_apart():
    """"An agent concluded this" and "this names a receipt that is not there" are different
    problems with different fixes."""
    graph = _graph(nodes=[_node("c1"), _node("r1", REQUIREMENT), _node("e1", EVIDENCE)],
                   edges=[_edge("c1", "r1", basis=INFERRED, authority="agent:planner"),
                          _edge("c1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                                authority="receipt:not-a-task")])
    up = trace(graph, "c1", "up", resolve=lambda a: False)
    by_target = {e["edge"]["target"]: e["resolution"] for e in up["upstream"][INFERRED]}
    assert by_target == {"r1": graph_mod.NOT_CHECKED, "e1": graph_mod.MISSING}


def test_the_report_says_which_of_the_two_it_was(tmp_path):
    graph = _graph(nodes=[_node("c1"), _node("e1", EVIDENCE)],
                   edges=[_edge("c1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                                authority="receipt:not-a-task")])
    result = _run(tmp_path, graph, "c1", "--direction", "up")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "names something nobody could find" in result.stdout, result.stdout


@pytest.mark.parametrize("name", ["/etc/passwd", "../../secret", "..", ".", "a/b",
                                  "../runs/real-task"])
def test_a_receipt_authority_is_a_run_name_and_not_a_path(tmp_path, name):
    """`receipt:/etc/thing` makes `pathlib` discard everything before it, and `..` walks out of
    the store — either way an accessible `task.json` anywhere would confirm an edge that named
    it."""
    escape = tmp_path / "provenance.json"
    escape.write_text(_signed_record(tmp_path, name), encoding="utf-8")
    (tmp_path / ".rig" / "runs").mkdir(parents=True)
    resolves = graph_mod.repository_resolver(tmp_path)
    assert resolves(f"receipt:{name}") is False, name


def test_a_receipt_that_is_a_real_run_still_resolves(tmp_path):
    run = tmp_path / ".rig" / "runs" / "rig-a-task"
    run.mkdir(parents=True)
    (run / "provenance.json").write_text(_signed_record(tmp_path, "rig-a-task"), encoding="utf-8")
    assert graph_mod.repository_resolver(tmp_path)("receipt:rig-a-task") is True


def test_the_git_resolver_asks_for_any_object_not_only_a_commit(monkeypatch, tmp_path):
    """A tree or a blob is a real object, and `^{commit}` would reject one while the module
    promises any."""
    import subprocess as real_subprocess

    calls = []

    class _Result:
        returncode = 0

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs.get("cwd")))
        return _Result()

    monkeypatch.setattr(real_subprocess, "run", fake_run)
    graph_mod.repository_resolver(tmp_path)(f"git:{OID}")
    argv, cwd = calls[0]
    assert argv[:3] == ["git", "cat-file", "-e"]
    assert argv[3] == f"{OID}^{{object}}", argv
    # In this repository: without `cwd` the answer would be about whatever checkout the
    # process happened to be standing in.
    assert cwd == str(tmp_path), cwd


def test_a_run_directory_that_is_a_symlink_out_of_the_store_does_not_resolve(tmp_path):
    """The name is a plain name and the path still leaves: `resolve()` follows the link, so
    only checking where it lands catches this one."""
    runs = tmp_path / ".rig" / "runs"
    runs.mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "provenance.json").write_text(_signed_record(tmp_path, "borrowed"),
                                       encoding="utf-8")
    (runs / "borrowed").symlink_to(outside)
    assert graph_mod.repository_resolver(tmp_path)("receipt:borrowed") is False


def test_the_store_itself_is_not_a_receipt(tmp_path):
    """`receipt:.` lands exactly on the runs directory, which `relative_to` is happy with —
    only "a run name, not a path" catches it."""
    runs = tmp_path / ".rig" / "runs"
    runs.mkdir(parents=True)
    (runs / "provenance.json").write_text(_signed_record(tmp_path, "."), encoding="utf-8")
    assert graph_mod.repository_resolver(tmp_path)("receipt:.") is False


# ── what round 9 found: the reason has to travel too ─────────────────────────
def _past_a_missing_receipt():
    """`c1 → e1` names a receipt nobody can find; `e1 → g1` was checked. Nothing here was
    inferred, so "reached through an inferred step" would be a false explanation."""
    return _graph(nodes=[_node("c1"), _node("e1", EVIDENCE), _node("g1", GOAL)],
                  edges=[_edge("c1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                               authority="receipt:not-a-task"),
                         _edge("e1", "g1", relation=SATISFIES, basis=CONFIRMED,
                               authority="policy:the brief")])


def test_the_reason_a_later_step_is_unreliable_travels_with_it():
    """Saying the wrong reason is worse than saying nothing: a reader told "an agent concluded
    this" goes looking for an inference that is not there."""
    up = trace(_past_a_missing_receipt(), "c1", "up",
               resolve=lambda a: a.startswith("policy"))
    entry = next(e for e in up["upstream"][INFERRED] if e["edge"]["target"] == "g1")
    assert entry["resolution"] != graph_mod.MISSING, "this edge's own authority was found"
    assert entry["path_unresolved"] is True, "the way here was not"


def test_the_report_names_the_right_reason(tmp_path):
    graph = _past_a_missing_receipt()
    result = _run(tmp_path, graph, "c1", "--direction", "up")
    assert result.returncode == 0, (result.stdout, result.stderr)
    line = next(row for row in result.stdout.splitlines() if "g1" in row)
    assert "reached through an unresolved authority" in line, line
    assert "inferred step" not in line, line


def test_an_actual_inference_still_says_so(tmp_path):
    graph = _graph(nodes=[_node("c1"), _node("r1", REQUIREMENT), _node("g1", GOAL)],
                   edges=[_edge("c1", "r1", basis=INFERRED, authority="agent:planner"),
                          _edge("r1", "g1", relation=SATISFIES, basis=CONFIRMED,
                                authority="policy:the brief")])
    result = _run(tmp_path, graph, "c1", "--direction", "up")
    line = next(row for row in result.stdout.splitlines() if "g1" in row)
    assert "reached through an inferred step" in line, line


def test_the_json_carries_both_reasons_apart():
    up = trace(_past_a_missing_receipt(), "c1", "up", resolve=lambda a: a.startswith("policy"))
    by_target = {e["edge"]["target"]: (e["resolution"], e["path_unresolved"])
                 for e in up["upstream"][INFERRED]}
    assert by_target == {"e1": (graph_mod.MISSING, False),
                         "g1": (graph_mod.FOUND, True)}


@pytest.mark.parametrize("rest", ["--help", "-h", "--exec=rm"])
def test_a_git_authority_that_looks_like_an_option_does_not_resolve(tmp_path, rest,
                                                                   monkeypatch):
    """Confirmation would otherwise depend on how git parses arguments rather than on whether
    the object exists. `subprocess.run` is stubbed to succeed, so the guard is what has to
    refuse it — otherwise this passes because there is no repository here."""
    import subprocess as real_subprocess

    class _Result:
        returncode = 0

    monkeypatch.setattr(real_subprocess, "run", lambda *a, **k: _Result())
    assert graph_mod.repository_resolver(tmp_path)(f"git:{rest}") is False


def test_the_reason_survives_a_third_step():
    """It has to keep travelling, not merely reach the next node: `lost = missing` alone would
    forget it one hop later."""
    graph = _graph(nodes=[_node("c1"), _node("e1", EVIDENCE), _node("r1", REQUIREMENT),
                          _node("g1", GOAL)],
                   edges=[_edge("c1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                                authority="receipt:not-a-task"),
                          _edge("e1", "r1", relation=SATISFIES, basis=CONFIRMED,
                                authority="policy:the brief"),
                          _edge("r1", "g1", relation=SATISFIES, basis=CONFIRMED,
                                authority="policy:the brief")])
    up = trace(graph, "c1", "up", resolve=lambda a: a.startswith("policy"))
    far = next(e for e in up["upstream"][INFERRED] if e["edge"]["target"] == "g1")
    assert far["resolution"] != graph_mod.MISSING and far["path_unresolved"] is True


def test_the_downstream_report_names_the_other_end(tmp_path):
    """`upstream` prints what an edge points at and `downstream` prints what points at it —
    swapping them would show every reader the node they already asked about."""
    result = _run(tmp_path, _graph(), "r1", "--direction", "down")
    line = next(row for row in result.stdout.splitlines() if "downstream" in row)
    assert "c1" in line and "r1" not in line.split("]")[-1], line


# ── what round 10 found: two more places a fix had not reached ───────────────
def _invalidated_past_a_missing_receipt():
    """Every edge confirmed; the way to the invalidated one runs through a receipt nobody can
    find. "Reached through an inferred step" would be a false explanation here too."""
    return _graph(nodes=[_node("c1"), _node("e1", EVIDENCE), _node("e2", EVIDENCE),
                         _node("e3", EVIDENCE)],
                  edges=[_edge("c1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                               authority="receipt:not-a-task"),
                         _edge("e1", "e2", relation=VERIFIED_BY, basis=CONFIRMED,
                               authority="policy:the brief"),
                         _edge("e3", "e2", relation=INVALIDATES, basis=CONFIRMED,
                               authority="policy:a later run")])


def test_the_invalidation_entry_carries_the_reason_too():
    result = trace(_invalidated_past_a_missing_receipt(), "c1", "up",
                   resolve=lambda a: a.startswith("policy"))
    item = next(i for i in result["invalidated"] if i["edge"]["target"] == "e2")
    assert item["resolution"] != graph_mod.MISSING
    assert item["path_unresolved"] is True


def test_the_invalidated_line_names_the_right_reason(tmp_path):
    result = _run(tmp_path, _invalidated_past_a_missing_receipt(), "c1", "--direction", "up")
    assert result.returncode == 0, (result.stdout, result.stderr)
    line = next(row for row in result.stdout.splitlines()
                if "invalidated " in row and "e2" in row)
    assert "reached through an unresolved authority" in line, line
    assert "inferred step" not in line, line


def test_a_symlinked_receipt_file_does_not_confirm_an_outside_record(tmp_path):
    """The directory stays inside the store and the file does not — the same borrowing one
    level down."""
    runs = tmp_path / ".rig" / "runs"
    (runs / "a-task").mkdir(parents=True)
    outside = tmp_path / "elsewhere.json"
    outside.write_text(_signed_record(tmp_path, "a-task"), encoding="utf-8")
    (runs / "a-task" / "provenance.json").symlink_to(outside)
    assert graph_mod.repository_resolver(tmp_path)("receipt:a-task") is False


def test_a_real_receipt_file_still_confirms(tmp_path):
    runs = tmp_path / ".rig" / "runs"
    (runs / "a-task").mkdir(parents=True)
    (runs / "a-task" / "provenance.json").write_text(_signed_record(tmp_path, "a-task"),
                                               encoding="utf-8")
    assert graph_mod.repository_resolver(tmp_path)("receipt:a-task") is True


def test_a_run_directory_with_no_record_does_not_confirm(tmp_path):
    runs = tmp_path / ".rig" / "runs"
    (runs / "a-task").mkdir(parents=True)
    assert graph_mod.repository_resolver(tmp_path)("receipt:a-task") is False


def test_a_receipt_file_linked_from_another_run_does_not_confirm(tmp_path):
    """Borrowing inside the store is a smaller borrowing and still one: `receipt:a` would be
    confirmed by `b`'s record."""
    runs = tmp_path / ".rig" / "runs"
    (runs / "b").mkdir(parents=True)
    (runs / "b" / "provenance.json").write_text(_signed_record(tmp_path, "b"), encoding="utf-8")
    (runs / "a").mkdir()
    (runs / "a" / "provenance.json").symlink_to(runs / "b" / "provenance.json")
    resolves = graph_mod.repository_resolver(tmp_path)
    assert resolves("receipt:b") is True
    assert resolves("receipt:a") is False


# ── what round 11 found: a file existing is not a receipt existing ───────────
@pytest.mark.parametrize("contents,why", [
    ("", "zero bytes"),
    ("not json", "not json at all"),
    ("[]", "a list"),
    ('"a-task"', "a scalar"),
    ('{}', "a record that does not say which task it is"),
])
def test_a_file_existing_is_not_this_receipt_existing(tmp_path, contents, why):
    runs = tmp_path / ".rig" / "runs" / "a-task"
    runs.mkdir(parents=True)
    (runs / "provenance.json").write_text(contents, encoding="utf-8")
    assert graph_mod.repository_resolver(tmp_path)("receipt:a-task") is False, why


def test_a_git_object_that_is_not_there_does_not_resolve(tmp_path, monkeypatch):
    """`<= 0` would read a git killed by a signal as a yes, and nothing exercised a plain
    failure — the option-like inputs return before git is invoked."""
    import subprocess as real_subprocess

    class _Result:
        def __init__(self, returncode):
            self.returncode = returncode

    for code, expected in ((1, False), (128, False), (-9, False), (0, True)):
        monkeypatch.setattr(real_subprocess, "run", lambda *a, c=code, **k: _Result(c))
        assert graph_mod.repository_resolver(tmp_path)(f"git:{OID}") is expected, code


# ── what round 12 found ──────────────────────────────────────────────────────
@pytest.mark.parametrize("shape", ["name-only", "unsigned", "forged-signature",
                                  "another-task"])
def test_a_hand_written_record_is_not_a_signed_one(tmp_path, shape):
    """Repeating the directory name in a file is what forging one looks like. What makes a
    provenance record valid is this repository's own signature over it, and that question is
    asked rather than answered a second time."""
    runs = tmp_path / ".rig" / "runs" / "a-task"
    runs.mkdir(parents=True)
    if shape == "name-only":
        record = '{"task_id": "a-task"}'
    elif shape == "unsigned":
        record = json.dumps({"algo": "HMAC-SHA256", "record": {"task_id": "a-task"}})
    elif shape == "forged-signature":
        record = json.dumps({"algo": "HMAC-SHA256", "record": {"task_id": "a-task"},
                             "signature": "0" * 64})
    else:
        record = _signed_record(tmp_path, "another-task")
    (runs / "provenance.json").write_text(record, encoding="utf-8")
    assert graph_mod.repository_resolver(tmp_path)("receipt:a-task") is False, shape


@pytest.mark.parametrize("rest", ["HEAD", "main", "HEAD@{1}", "abc123", "A" * 40,
                                  "a" * 39, "a" * 41, "--help", "-h"])
def test_a_git_authority_is_an_object_and_not_a_revision(tmp_path, rest, monkeypatch):
    """`git:HEAD` resolves now and names something else later, which is the opposite of what an
    authority in a provenance record is for."""
    import subprocess as real_subprocess

    class _Result:
        returncode = 0

    monkeypatch.setattr(real_subprocess, "run", lambda *a, **k: _Result())
    assert graph_mod.repository_resolver(tmp_path)(f"git:{rest}") is False, rest


def test_a_sha256_object_id_is_still_an_object_id(tmp_path, monkeypatch):
    import subprocess as real_subprocess

    class _Result:
        returncode = 0

    monkeypatch.setattr(real_subprocess, "run", lambda *a, **k: _Result())
    assert graph_mod.repository_resolver(tmp_path)(f"git:{'c' * 64}") is True


# ── what round 13 found ──────────────────────────────────────────────────────
def test_a_run_that_only_started_is_not_a_receipt(tmp_path):
    """`task.json` is written when a run begins. An edge saying a change was verified by a run
    that is merely underway is not evidence of anything."""
    runs = tmp_path / ".rig" / "runs" / "a-task"
    runs.mkdir(parents=True)
    (runs / "task.json").write_text(json.dumps({"task_id": "a-task", "status": "running"}),
                                    encoding="utf-8")
    assert graph_mod.repository_resolver(tmp_path)("receipt:a-task") is False


def test_a_kind_this_machine_cannot_look_up_is_not_reported_as_looked_up():
    """`person:` and `policy:` always answer `None`. Calling that resolution is how a graph
    full of them comes back saying it was checked."""
    graph = _graph(edges=[_edge("c1", "r1", basis=CONFIRMED, authority="person:someone")])
    result = trace(graph, "c1", "up", resolve=lambda a: None)
    assert result["authorities_looked_up"] is False
    [entry] = result["upstream"][CONFIRMED]
    assert entry["resolution"] == graph_mod.NOT_CHECKED


def test_a_trace_whose_authorities_were_all_found_says_so():
    graph = _graph(edges=[_edge("c1", "r1", basis=CONFIRMED, authority="receipt:a-task")])
    assert trace(graph, "c1", "up", resolve=lambda a: True)["authorities_looked_up"] is True


def test_one_unlookupable_authority_is_enough_to_say_not_all_of_them_were():
    graph = _graph(nodes=[_node("c1"), _node("r1", REQUIREMENT), _node("e1", EVIDENCE)],
                   edges=[_edge("c1", "r1", basis=CONFIRMED, authority="receipt:a-task"),
                          _edge("c1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                                authority="person:someone")])
    result = trace(graph, "c1", "up",
                   resolve=lambda a: True if a.startswith("receipt") else None)
    assert result["authorities_looked_up"] is False


def test_the_header_says_it(tmp_path):
    graph = _graph(edges=[_edge("c1", "r1", basis=CONFIRMED, authority="person:someone")])
    result = _run(tmp_path, graph, "c1")
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "some authorities not looked up" in result.stdout


def test_a_graph_of_inferences_alone_was_not_checked():
    """Nothing confirmed means nothing was looked up, and saying otherwise would let an
    all-guesswork graph claim it had been."""
    graph = _graph(edges=[_edge("c1", "r1", basis=INFERRED, authority="agent:planner")])
    assert trace(graph, "c1", "up", resolve=lambda a: True)["authorities_looked_up"] is False


def test_an_authority_only_the_invalidation_section_shows_still_counts():
    """The summary is about every confirmed authority the trace reached, and an edge that is
    also invalidated is one of them."""
    graph = _graph(nodes=[_node("c1"), _node("e1", EVIDENCE), _node("e2", EVIDENCE)],
                   edges=[_edge("c1", "e1", relation=VERIFIED_BY, basis=INFERRED,
                                authority="agent:planner"),
                          _edge("e2", "e1", relation=INVALIDATES, basis=CONFIRMED,
                                authority="person:someone")])
    result = trace(graph, "c1", "up", resolve=lambda a: None)
    assert result["authorities_looked_up"] is False


def test_a_key_that_cannot_be_read_is_not_a_verification(tmp_path, monkeypatch):
    """Failing to check is not checking. An exception from the verifier used to come back as
    a confirmed receipt."""
    runs = tmp_path / ".rig" / "runs" / "a-task"
    runs.mkdir(parents=True)
    (runs / "provenance.json").write_text(_signed_record(tmp_path, "a-task"), encoding="utf-8")
    from rig_workbench.workbench import state

    def explode(*args, **kwargs):
        raise OSError("the key is unreadable")

    monkeypatch.setattr(state, "verify_provenance", explode)
    assert graph_mod.repository_resolver(tmp_path)("receipt:a-task") is False


def test_the_invalidated_suffix_names_the_reason_not_just_the_bracket(tmp_path):
    """The bracket says how reliable it is and the suffix says why; confusing the two would
    label a missing receipt as an inference in the same breath as marking it unresolved."""
    graph = _graph(nodes=[_node("c1"), _node("e1", EVIDENCE), _node("e2", EVIDENCE)],
                   edges=[_edge("c1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                                authority="receipt:not-a-task"),
                          _edge("e2", "e1", relation=INVALIDATES, basis=CONFIRMED,
                                authority="policy:the brief")])
    result = _run(tmp_path, graph, "c1", "--direction", "up")
    line = next(row for row in result.stdout.splitlines() if "invalidated " in row)
    assert "names something nobody could find" in line, line
    assert "inferred step" not in line, line


# ── what round 14 found: an invalidator's authority is reached too ───────────
def test_an_invalidator_nobody_could_look_up_counts_against_the_summary():
    """An `invalidates` edge is never walked, so its authority reaches the reader without
    having been counted — and "everything was looked up" would be answered without it."""
    graph = _graph(nodes=[_node("c1"), _node("e1", EVIDENCE), _node("e2", EVIDENCE)],
                   edges=[_edge("c1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                                authority="receipt:a-task"),
                          _edge("e2", "e1", relation=INVALIDATES, basis=CONFIRMED,
                                authority="person:someone")])
    result = trace(graph, "c1", "up",
                   resolve=lambda a: True if a.startswith("receipt") else None)
    assert [e["target"] for e in _edges(result["upstream"][CONFIRMED])] == ["e1"]
    assert result["authorities_looked_up"] is False


def test_an_invalidator_that_was_looked_up_does_not_count_against_it():
    graph = _graph(nodes=[_node("c1"), _node("e1", EVIDENCE), _node("e2", EVIDENCE)],
                   edges=[_edge("c1", "e1", relation=VERIFIED_BY, basis=CONFIRMED,
                                authority="receipt:a-task"),
                          _edge("e2", "e1", relation=INVALIDATES, basis=CONFIRMED,
                                authority="receipt:b-task")])
    assert trace(graph, "c1", "up", resolve=lambda a: True)["authorities_looked_up"] is True
