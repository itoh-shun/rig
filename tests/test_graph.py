"""Structure-level tests for rig_workbench.orchestrate.graph.build_brick_graph.

Runs against the shipped skills tree (RIG_HOME pinned to the repo in conftest).
Asserts shapes and invariants only — no dependence on human-facing strings.
"""

import pytest

from rig_workbench.orchestrate.graph import build_brick_graph

REL_VOCAB = {"extends", "injects", "links-to", "uses-instruction", "uses-pattern",
             "gated-by", "applies-policy", "emits-contract", "uses-persona",
             "references", "mirrors"}


@pytest.fixture(scope="module")
def graph():
    return build_brick_graph()


def test_graph_top_level_shape(graph):
    assert set(graph) == {"nodes", "edges"}
    assert isinstance(graph["nodes"], list) and graph["nodes"]
    assert isinstance(graph["edges"], list) and graph["edges"]


def test_node_shape_and_id_convention(graph):
    kinds = set()
    for n in graph["nodes"]:
        assert set(n) == {"id", "kind", "path"}
        assert n["id"].startswith(n["kind"] + ":")
        kinds.add(n["kind"])
    # the shipped tree exercises at least the core brick kinds
    assert {"recipe", "persona", "wiki", "pattern"} <= kinds
    # ids are unique
    ids = [n["id"] for n in graph["nodes"]]
    assert len(ids) == len(set(ids))


def test_edge_shape_and_rel_vocabulary(graph):
    node_ids = {n["id"] for n in graph["nodes"]}
    for e in graph["edges"]:
        assert set(e) == {"from", "rel", "to", "resolved"}
        assert e["rel"] in REL_VOCAB
        assert e["from"] in node_ids            # sources are always real nodes
        assert e["resolved"] == (e["to"] in node_ids)
    # no unresolved edges in the shipped tier (mirrors selftest W golden check)
    assert sum(1 for e in graph["edges"] if not e["resolved"]) == 0


def test_review_only_recipe_edges(graph):
    triples = {(e["from"], e["rel"], e["to"]) for e in graph["edges"]}
    assert ("recipe:review-only", "gated-by", "pattern:review-gate") in triples
    assert ("recipe:review-only", "uses-persona", "persona:security-reviewer") in triples


def test_graph_deterministic_and_sorted(graph):
    assert build_brick_graph() == graph
    assert graph["nodes"] == sorted(graph["nodes"], key=lambda x: (x["kind"], x["id"]))
    assert graph["edges"] == sorted(graph["edges"], key=lambda x: (x["from"], x["rel"], x["to"]))
