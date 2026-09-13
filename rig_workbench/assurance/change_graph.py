"""Validate a caller-authored cross-repository change graph and order it (#441).

This module does not discover repositories, generate changes, execute nodes, or verify an
integration.  It answers only whether the dependencies and compatibility constraints written
in the submitted document admit an execution order.  Such an order is not a claim that
executing it is safe.
"""

from __future__ import annotations

import json
import pathlib
import re

from .synthesis import _no_duplicate_keys

SCHEMA = "rig.change-graph/v1"
REPORT_SCHEMA = "rig.change-graph-assessment/v1"
ROOT_KEYS = frozenset({"schema", "id", "nodes", "dependencies"})
NODE_KEYS = frozenset({
    "id", "repository", "component", "base", "target", "required_change",
    "assurance_target", "status",
})
DEPENDENCY_KEYS = frozenset({"id", "kind", "predecessor", "successor", "compatibility"})
COMPATIBILITY_KEYS = frozenset({"requirement", "status", "evidence"})
NODE_STATUSES = frozenset({"planned", "accepted", "rejected", "unobservable"})
DEPENDENCY_KINDS = frozenset({
    "must-accept-before", "compatible-with", "migration-before", "deploy-together", "blocks",
})
COMPATIBILITY_STATUSES = frozenset({"satisfied", "unmet", "unobservable"})
IMMUTABLE_GIT = re.compile(r"git:[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _unknown(problems: list[str], where: str, value: dict, allowed: frozenset[str]) -> None:
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        problems.append(f"{where}: unknown key(s) {', '.join(unknown)}")


def read(path: pathlib.Path | str) -> object:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=_no_duplicate_keys("change graph"))


def validate(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        return [f"graph: expected an object, got {type(payload).__name__}"]
    problems: list[str] = []
    _unknown(problems, "graph", payload, ROOT_KEYS)
    if payload.get("schema") != SCHEMA:
        problems.append(f"schema: expected {SCHEMA!r}, got {payload.get('schema')!r}")
    if not _text(payload.get("id")):
        problems.append("id: expected a non-blank graph id")

    nodes = payload.get("nodes")
    known: set[str] = set()
    if not isinstance(nodes, list):
        problems.append("nodes: expected a list")
        nodes = []
    elif len(nodes) < 2:
        problems.append("nodes: expected at least two cross-repository change nodes")
    for index, node in enumerate(nodes):
        where = f"nodes[{index}]"
        if not isinstance(node, dict):
            problems.append(f"{where}: expected an object")
            continue
        _unknown(problems, where, node, NODE_KEYS)
        for field in ("id", "repository", "component", "required_change", "assurance_target"):
            if not _text(node.get(field)):
                problems.append(f"{where}.{field}: expected a non-blank string")
        node_id = node.get("id")
        if _text(node_id):
            if node_id in known:
                problems.append(f"{where}.id: duplicate {node_id!r}")
            known.add(node_id)
        for field in ("base", "target"):
            value = node.get(field)
            if not _text(value) or not IMMUTABLE_GIT.fullmatch(value):
                problems.append(
                    f"{where}.{field}: expected an immutable git:<40-or-64-lowercase-hex> identity")
        if not isinstance(node.get("status"), str) or node.get("status") not in NODE_STATUSES:
            problems.append(
                f"{where}.status: expected one of {', '.join(sorted(NODE_STATUSES))}")

    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, list):
        problems.append("dependencies: expected a list of declared constraints")
    elif not dependencies:
        problems.append("dependencies: expected at least one declared constraint")
    else:
        dependency_ids: set[str] = set()
        for index, dependency in enumerate(dependencies):
            where = f"dependencies[{index}]"
            if not isinstance(dependency, dict):
                problems.append(f"{where}: expected an object")
                continue
            _unknown(problems, where, dependency, DEPENDENCY_KEYS)
            dependency_id = dependency.get("id")
            if not _text(dependency_id):
                problems.append(f"{where}.id: expected a non-blank string")
            elif dependency_id in dependency_ids:
                problems.append(f"{where}.id: duplicate {dependency_id!r}")
            else:
                dependency_ids.add(dependency_id)
            kind = dependency.get("kind")
            if not isinstance(kind, str) or kind not in DEPENDENCY_KINDS:
                problems.append(
                    f"{where}.kind: expected one of {', '.join(sorted(DEPENDENCY_KINDS))}")
            for field in ("predecessor", "successor"):
                endpoint = dependency.get(field)
                if not _text(endpoint):
                    problems.append(f"{where}.{field}: expected a non-blank node id")
                elif endpoint not in known:
                    problems.append(f"{where}.{field}: {endpoint!r} is not a node in this graph")
            if (dependency.get("predecessor") == dependency.get("successor")
                    and _text(dependency.get("predecessor"))):
                problems.append(f"{where}: a dependency cannot relate a node to itself")

            compatibility = dependency.get("compatibility")
            if not isinstance(compatibility, dict):
                problems.append(f"{where}.compatibility: expected an object")
                continue
            _unknown(problems, f"{where}.compatibility", compatibility, COMPATIBILITY_KEYS)
            if not _text(compatibility.get("requirement")):
                problems.append(
                    f"{where}.compatibility.requirement: expected a caller-declared non-blank "
                    "constraint; a status cannot create its requirement")
            status = compatibility.get("status")
            if not isinstance(status, str) or status not in COMPATIBILITY_STATUSES:
                problems.append(
                    f"{where}.compatibility.status: expected one of "
                    f"{', '.join(sorted(COMPATIBILITY_STATUSES))}")
            evidence = compatibility.get("evidence")
            if status == "unobservable":
                if evidence is not None:
                    problems.append(
                        f"{where}.compatibility.evidence: expected null when status is "
                        "unobservable")
            elif not _text(evidence):
                problems.append(
                    f"{where}.compatibility.evidence: expected a non-blank reference for a "
                    "decided constraint")
    return problems


def assess(payload: dict) -> dict:
    problems = validate(payload)
    if problems:
        raise ValueError("not a change graph:\n  " + "\n  ".join(problems))
    node_ids = [node["id"] for node in payload["nodes"]]
    parent = {node_id: node_id for node_id in node_ids}

    def find(node_id):
        while parent[node_id] != node_id:
            parent[node_id] = parent[parent[node_id]]
            node_id = parent[node_id]
        return node_id

    def union(left, right):
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for dependency in payload["dependencies"]:
        if dependency["kind"] == "deploy-together":
            union(dependency["predecessor"], dependency["successor"])

    groups: dict[str, list[str]] = {}
    for node_id in node_ids:
        groups.setdefault(find(node_id), []).append(node_id)
    group_for = {node_id: find(node_id) for node_id in node_ids}
    outgoing = {group: set() for group in groups}
    self_cycles: set[str] = set()
    directional = DEPENDENCY_KINDS - {"compatible-with", "deploy-together"}
    for dependency in payload["dependencies"]:
        if dependency["kind"] not in directional:
            continue
        source = group_for[dependency["predecessor"]]
        target = group_for[dependency["successor"]]
        if source == target:
            self_cycles.add(source)
        else:
            outgoing[source].add(target)

    # Iterative Kosaraju names actual cycles, not every downstream node left behind by Kahn's
    # algorithm, and does not turn a large caller document into Python recursion failure.
    reverse = {group: set() for group in groups}
    for source, targets in outgoing.items():
        for target in targets:
            reverse[target].add(source)
    seen: set[str] = set()
    finishing: list[str] = []
    for start in sorted(groups):
        if start in seen:
            continue
        stack = [(start, False)]
        while stack:
            group, expanded = stack.pop()
            if expanded:
                finishing.append(group)
                continue
            if group in seen:
                continue
            seen.add(group)
            stack.append((group, True))
            stack.extend((target, False) for target in sorted(outgoing[group], reverse=True)
                         if target not in seen)

    cyclic_groups: list[list[str]] = []
    assigned: set[str] = set()
    for start in reversed(finishing):
        if start in assigned:
            continue
        component: list[str] = []
        stack = [start]
        assigned.add(start)
        while stack:
            group = stack.pop()
            component.append(group)
            for source in sorted(reverse[group], reverse=True):
                if source not in assigned:
                    assigned.add(source)
                    stack.append(source)
        if len(component) > 1 or component[0] in self_cycles:
            cyclic_groups.append(sorted(
                node for member in component for node in groups[member]))

    indegree = {group: 0 for group in groups}
    for targets in outgoing.values():
        for target in targets:
            indegree[target] += 1
    stages: list[list[str]] = []
    ready = sorted(group for group, degree in indegree.items() if degree == 0)
    visited = 0
    while ready:
        stages.append(sorted(node for group in ready for node in groups[group]))
        visited += len(ready)
        following: list[str] = []
        for group in ready:
            for target in sorted(outgoing[group]):
                indegree[target] -= 1
                if indegree[target] == 0:
                    following.append(target)
        ready = sorted(following)

    unmet = [item["id"] for item in payload["dependencies"]
             if item["compatibility"]["status"] == "unmet"]
    unobservable = [item["id"] for item in payload["dependencies"]
                    if item["compatibility"]["status"] == "unobservable"]
    rejected_nodes = [node["id"] for node in payload["nodes"]
                      if node["status"] == "rejected"]
    unobservable_nodes = [node["id"] for node in payload["nodes"]
                          if node["status"] == "unobservable"]
    if unmet or rejected_nodes or cyclic_groups or visited != len(groups):
        status = "not-executable"
    elif unobservable or unobservable_nodes:
        status = "unobservable"
    else:
        status = "executable"
    return {
        "schema": REPORT_SCHEMA,
        "status": status,
        "stages": stages if status == "executable" else None,
        "cycles": sorted(cyclic_groups),
        "unmet": unmet,
        "unobservable": unobservable,
        "rejected_nodes": rejected_nodes,
        "unobservable_nodes": unobservable_nodes,
        "guarantee": (
            "the declared dependencies admit the reported order, and no node in it is "
            "rejected or unobservable"
            if status == "executable" else None
        ),
        "does_not_guarantee": (
            "that executing this order is safe, that any change is correct, or that cross-repo "
            "integration or feature assurance has been verified"
        ),
    }


def cmd_change_graph(args) -> "NoReturn":  # noqa: F821
    import sys

    try:
        payload = read(args.graph)
    except Exception as exc:  # noqa: BLE001
        print(f"[REJECTED] graph could not be read: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
    problems = validate(payload)
    if problems:
        print("\n".join(f"[REJECTED] {problem}" for problem in problems), file=sys.stderr)
        sys.exit(1)
    report = assess(payload)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"change graph: {report['status']}")
        for cycle in report["cycles"]:
            print(f"  cycle: {' -> '.join(cycle)}")
        for dependency in report["unmet"]:
            print(f"  unmet: {dependency}")
        for dependency in report["unobservable"]:
            print(f"  unobservable: {dependency}")
        for node in report["rejected_nodes"]:
            print(f"  rejected node: {node}")
        for node in report["unobservable_nodes"]:
            print(f"  unobservable node: {node}")
        print(f"does not guarantee: {report['does_not_guarantee']}")
    sys.exit(0 if report["status"] == "executable" else
             2 if report["status"] == "unobservable" else 1)
