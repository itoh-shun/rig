"""A caller-authored cross-repository change graph has an executable order (#441)."""

import json
import pathlib
import subprocess
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = ROOT / "scripts" / "workbench.py"
SCHEMA = "rig.change-graph/v1"


def _node(node_id, repository, component, revision):
    return {
        "id": node_id,
        "repository": repository,
        "component": component,
        "base": f"git:{'0' * 40}",
        "target": f"git:{revision * 40}",
        "required_change": f"change {component}",
        "assurance_target": f"assurance:{node_id}",
        "status": "planned",
    }


def _dependency(**changes):
    value = {
        "id": "db-before-api",
        "kind": "migration-before",
        "predecessor": "db",
        "successor": "api",
        "compatibility": {
            "requirement": "api accepts schema v2",
            "status": "satisfied",
            "evidence": "contract:test-api-schema-v2",
        },
    }
    value.update(changes)
    return value


def _graph(**changes):
    value = {
        "schema": SCHEMA,
        "id": "feature-441-stage-1",
        "nodes": [
            _node("db", "acme/db", "migration", "1"),
            _node("api", "acme/api", "service", "2"),
        ],
        "dependencies": [_dependency()],
    }
    value.update(changes)
    return value


def _run(tmp_path, graph):
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(graph) + "\n", encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(WORKBENCH), "change-graph", str(path), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_command_refuses_absent_dependencies_and_accepts_the_control(tmp_path):
    rejected = _graph()
    del rejected["dependencies"]
    result = _run(tmp_path, rejected)
    assert result.returncode == 1
    assert "dependencies" in result.stderr

    control = _run(tmp_path, _graph())
    assert control.returncode == 0, control.stderr
    report = json.loads(control.stdout)
    assert report["status"] == "executable"
    assert report["stages"] == [["db"], ["api"]]


@pytest.mark.parametrize("replacement,says", [
    (None, "predecessor"),
    ("", "non-blank node id"),
    ([], "non-blank node id"),
    ("missing", "is not a node"),
])
def test_command_refuses_missing_empty_wrong_typed_and_unresolved_endpoints_with_a_control(
        tmp_path, replacement, says):
    accepted = _run(tmp_path, _graph())
    assert accepted.returncode == 0, accepted.stderr
    assert json.loads(accepted.stdout)["stages"] == [["db"], ["api"]]

    graph = _graph()
    if replacement is None:
        del graph["dependencies"][0]["predecessor"]
    else:
        graph["dependencies"][0]["predecessor"] = replacement
    rejected = _run(tmp_path, graph)
    assert rejected.returncode == 1
    assert says in rejected.stderr


def test_command_enumerates_closed_schema_refusals_with_an_accepted_control(tmp_path):
    cases = []
    for field in ("schema", "id", "nodes", "dependencies"):
        value = _graph()
        del value[field]
        cases.append((f"missing root {field}", value, field))
    for field, bad in (("id", ""), ("nodes", []), ("dependencies", [])):
        cases.append((f"empty root {field}", _graph(**{field: bad}), field))
    for field, bad in (("id", 3), ("nodes", {}), ("dependencies", {})):
        cases.append((f"wrong root {field}", _graph(**{field: bad}), field))
    cases.append(("unknown root key", _graph(discovery="automatic"), "unknown key"))

    for index, node_field in enumerate((
        "id", "repository", "component", "base", "target", "required_change",
        "assurance_target", "status",
    )):
        value = _graph()
        del value["nodes"][0][node_field]
        cases.append((f"missing node {node_field}", value, f"nodes[0].{node_field}"))
        value = _graph()
        value["nodes"][0][node_field] = []
        cases.append((f"wrong node {node_field}", value, f"nodes[0].{node_field}"))
        if node_field != "status":
            value = _graph()
            value["nodes"][0][node_field] = ""
            cases.append((f"empty node {node_field}", value, f"nodes[0].{node_field}"))

    for dependency_field in ("id", "kind", "predecessor", "successor", "compatibility"):
        value = _graph()
        del value["dependencies"][0][dependency_field]
        cases.append((f"missing dependency {dependency_field}", value,
                      f"dependencies[0].{dependency_field}"))
        value = _graph()
        value["dependencies"][0][dependency_field] = []
        cases.append((f"wrong dependency {dependency_field}", value,
                      f"dependencies[0].{dependency_field}"))

    for compatibility_field in ("requirement", "status", "evidence"):
        value = _graph()
        del value["dependencies"][0]["compatibility"][compatibility_field]
        cases.append((f"missing compatibility {compatibility_field}", value,
                      f"compatibility.{compatibility_field}"))
        value = _graph()
        value["dependencies"][0]["compatibility"][compatibility_field] = []
        cases.append((f"wrong compatibility {compatibility_field}", value,
                      f"compatibility.{compatibility_field}"))

    unresolved = _graph()
    unresolved["dependencies"][0]["successor"] = "missing"
    cases.append(("unresolved endpoint", unresolved, "is not a node"))

    for label, graph, message in cases:
        rejected = _run(tmp_path, graph)
        assert rejected.returncode == 1, label
        assert message in rejected.stderr, (label, rejected.stderr)
        accepted = _run(tmp_path, _graph())
        assert accepted.returncode == 0, (label, accepted.stderr)


def test_command_rejects_a_cycle_and_accepts_the_acyclic_control(tmp_path):
    cyclic = _graph()
    cyclic["dependencies"].append({
        **_dependency(
            id="api-before-db",
            kind="must-accept-before",
            predecessor="api",
            successor="db",
        ),
    })
    rejected = _run(tmp_path, cyclic)
    assert rejected.returncode == 1
    report = json.loads(rejected.stdout)
    assert report["status"] == "not-executable"
    assert report["cycles"] == [["api", "db"]]

    accepted = _run(tmp_path, _graph())
    assert accepted.returncode == 0
    assert json.loads(accepted.stdout)["status"] == "executable"


def test_command_keeps_unmet_and_unobservable_constraints_separate(tmp_path):
    unmet = _graph()
    unmet["dependencies"][0]["compatibility"].update({
        "status": "unmet",
        "evidence": "contract:test-api-rejects-schema-v2",
    })
    unmet_result = _run(tmp_path, unmet)
    assert unmet_result.returncode == 1
    unmet_report = json.loads(unmet_result.stdout)
    assert unmet_report["status"] == "not-executable"
    assert unmet_report["unmet"] == ["db-before-api"]
    assert unmet_report["unobservable"] == []

    unknown = _graph()
    unknown["dependencies"][0]["compatibility"].update({
        "status": "unobservable",
        "evidence": None,
    })
    unknown_result = _run(tmp_path, unknown)
    assert unknown_result.returncode == 2
    unknown_report = json.loads(unknown_result.stdout)
    assert unknown_report["status"] == "unobservable"
    assert unknown_report["unmet"] == []
    assert unknown_report["unobservable"] == ["db-before-api"]

    accepted = _run(tmp_path, _graph())
    assert accepted.returncode == 0
    assert json.loads(accepted.stdout)["unmet"] == []


def test_command_groups_deploy_together_and_orders_the_group(tmp_path):
    graph = _graph()
    graph["nodes"].append(_node("web", "acme/web", "frontend", "3"))
    graph["dependencies"].append(_dependency(
        id="api-with-web",
        kind="deploy-together",
        predecessor="api",
        successor="web",
        compatibility={
            "requirement": "web and api release manifests name the same protocol",
            "status": "satisfied",
            "evidence": "contract:release-manifest-v2",
        },
    ))
    result = _run(tmp_path, graph)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["stages"] == [["db"], ["api", "web"]]


def test_command_refuses_ambiguous_or_unsupported_declarations_with_controls(tmp_path):
    cases = []
    duplicate_node = _graph()
    duplicate_node["nodes"][1]["id"] = "db"
    cases.append((duplicate_node, "duplicate"))
    duplicate_dependency = _graph()
    duplicate_dependency["dependencies"].append(_dependency())
    cases.append((duplicate_dependency, "duplicate"))
    self_edge = _graph()
    self_edge["dependencies"][0]["successor"] = "db"
    cases.append((self_edge, "cannot relate a node to itself"))
    unsupported_kind = _graph()
    unsupported_kind["dependencies"][0]["kind"] = "run-whenever"
    cases.append((unsupported_kind, "dependencies[0].kind"))
    unsupported_status = _graph()
    unsupported_status["dependencies"][0]["compatibility"]["status"] = "compatible"
    cases.append((unsupported_status, "compatibility.status"))
    unknown_node = _graph()
    unknown_node["nodes"][0]["discovered"] = True
    cases.append((unknown_node, "unknown key"))
    unknown_dependency = _graph()
    unknown_dependency["dependencies"][0]["rollout"] = "safe"
    cases.append((unknown_dependency, "unknown key"))
    conclusion_without_requirement = _graph()
    del conclusion_without_requirement["dependencies"][0]["compatibility"]["requirement"]
    conclusion_without_requirement["dependencies"][0]["compatibility"]["conclusion"] = (
        "compatible")
    cases.append((conclusion_without_requirement, "requirement"))

    for graph, message in cases:
        rejected = _run(tmp_path, graph)
        assert rejected.returncode == 1
        assert message in rejected.stderr
        assert _run(tmp_path, _graph()).returncode == 0


def test_command_refuses_duplicate_json_keys_and_accepts_the_control(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"schema":"rig.change-graph/v1","schema":"rig.change-graph/v1"}\n',
        encoding="utf-8",
    )
    rejected = subprocess.run(
        [sys.executable, str(WORKBENCH), "change-graph", str(path), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode == 1
    assert "names 'schema' twice" in rejected.stderr
    assert _run(tmp_path, _graph()).returncode == 0
