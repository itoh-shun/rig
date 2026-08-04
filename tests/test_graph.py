"""Structure-level tests for rig_workbench.orchestrate.graph.build_brick_graph.

Runs against the shipped skills tree (RIG_HOME pinned to the repo in conftest).
Asserts shapes and invariants only — no dependence on human-facing strings.
"""

import pathlib
import shutil

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


def test_graph_uses_resolved_pack_winner_and_owner_topology(
    tmp_path, monkeypatch, capsys,
):
    from test_pack_sdk_phase4d import _resource_pack, _typed_dependency_pack
    from rig_workbench.packs import resolver
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml

    project = tmp_path / "project"
    project_packs = project / ".rig/packs"
    user_packs = tmp_path / "user-packs"
    official_packs = tmp_path / "official-packs"
    core_packs = tmp_path / "core-packs"
    top = _typed_dependency_pack(project_packs, "project-top")
    shadowed_core = top / "patterns/serial.md"
    shadowed_core.write_text("# project serial\n", encoding="utf-8")
    shadowed_dependency_entrypoint = top / "recipes/dependency-flow.md"
    shadowed_dependency_entrypoint.write_text(
        "---\nname: dependency-flow\nsteps:\n"
        "  - id: shadow\n    instruction: shared\n    pattern: serial\n"
        "---\n# shadow dependency entrypoint\n",
        encoding="utf-8",
    )
    _raw, top_manifest = read_json_yaml(top / "pack.yaml")
    top_manifest["assets"]["pattern"] = ["patterns/serial.md"]
    top_manifest["assets"]["recipe"].append("recipes/dependency-flow.md")
    top_manifest["assets"]["recipe"].sort()
    top_manifest["hashes"]["patterns/serial.md"] = digest(shadowed_core)
    top_manifest["hashes"]["recipes/dependency-flow.md"] = digest(
        shadowed_dependency_entrypoint
    )
    for reference in top_manifest["references"]:
        if reference["kind"] == "pattern" and reference["id"] == "serial":
            reference["pack"] = "project-top"
    top_manifest["references"].sort(
        key=lambda item: (item["pack"], item["kind"], item["id"])
    )
    (top / "pack.yaml").write_text(canonical(top_manifest), encoding="utf-8")
    dependency = _typed_dependency_pack(user_packs, "dependency")
    _typed_dependency_pack(
        project_packs, "child",
        dependency=[{"id": "dependency", "range": ">=1.0.0"}],
        external_owner="dependency",
    )
    official = _typed_dependency_pack(official_packs, "official-top")
    _raw, official_manifest = read_json_yaml(official / "pack.yaml")
    official_manifest["kind"] = "official"
    (official / "pack.yaml").write_text(canonical(official_manifest), encoding="utf-8")
    _resource_pack(official_packs, "official-docs", kind="official")
    _resource_pack(core_packs, "core-docs", kind="core")
    monkeypatch.setattr(resolver, "pack_roots", lambda _project=None: [
        ("project", project_packs), ("user", user_packs),
        ("official", official_packs), ("core", core_packs),
    ])

    result = build_brick_graph(project=project)
    nodes = {node["id"]: node for node in result["nodes"]}
    triples = {(edge["from"], edge["rel"], edge["to"])
               for edge in result["edges"]}

    assert nodes["instruction:shared"]["path"] == (
        "pack://project/project-top/facets/instructions/shared.md"
    )
    assert nodes["pattern:serial"]["path"] == "pack://project/project-top/patterns/serial.md"
    assert nodes["recipe:dependency-flow"]["path"] == (
        "pack://project/project-top/recipes/dependency-flow.md"
    )
    assert nodes["pack:project:project-top"]["trust"] == "unverified"
    assert ("pack:project:project-top", "owns", "instruction:shared") in triples
    assert ("pack:user:dependency", "offers-shadowed", "instruction:shared") in triples
    assert ("pack:official:official-top", "offers-shadowed", "instruction:shared") in triples
    assert ("pack:core:rig-core", "offers-shadowed", "pattern:serial") in triples
    assert ("pack:project:child", "depends-on", "pack:user:dependency") in triples
    assert ("pack:project:child", "references-owner", "pack:user:dependency") in triples
    assert ("pack:project:child", "references-owner", "pack:core:rig-core") in triples
    assert ("pack:project:child", "entrypoint", "recipe:child-flow") in triples
    assert (
        "pack:user:dependency", "entrypoint-owned",
        "asset:user:dependency:recipe:dependency-flow",
    ) in triples
    assert (
        "pack:project:child", "references-owned",
        "asset:user:dependency:instruction:shared",
    ) in triples
    assert (
        "pack:project:child", "references-owned",
        "asset:core:rig-core:pattern:serial",
    ) in triples
    assert (
        "asset:project:child:recipe:child-flow", "uses-instruction",
        "asset:user:dependency:instruction:shared",
    ) in triples
    assert (
        "asset:project:child:recipe:child-flow", "uses-pattern",
        "asset:core:rig-core:pattern:serial",
    ) in triples
    assert (
        "asset:user:dependency:recipe:dependency-flow", "active-alias",
        "recipe:dependency-flow",
    ) not in triples
    assert (
        "asset:user:dependency:recipe:dependency-flow", "shadowed-alias",
        "recipe:dependency-flow",
    ) in triples
    assert ("pack:official:official-docs", "resource",
            "resource:official-docs/guide") in triples
    assert ("pack:core:core-docs", "resource", "resource:core-docs/guide") in triples
    assert ("recipe:review-only", "gated-by", "pattern:review-gate") in triples
    owned_shared = nodes["asset:user:dependency:instruction:shared"]
    assert owned_shared == {
        "id": "asset:user:dependency:instruction:shared",
        "kind": "owned-asset", "asset_kind": "instruction",
        "logical_id": "instruction:shared",
        "path": "pack://user/dependency/facets/instructions/shared.md",
        "owner": "pack:user:dependency", "tier": "user", "trust": "unverified",
        "provenance": {
            "owner": "pack:user:dependency", "tier": "user",
            "uri": "pack://user/dependency/facets/instructions/shared.md",
        },
    }
    assert all(not pathlib.Path(node["path"]).is_absolute() for node in result["nodes"])
    assert result == build_brick_graph(project=project)

    from rig_workbench.packs.resolver import resolve_bound_asset, resolve_owned_asset

    child_recipe = project_packs / "child/recipes/child-flow.md"
    assert resolve_bound_asset(
        "instruction", "shared", child_recipe, project=project,
    ).path == dependency / "facets/instructions/shared.md"
    assert resolve_owned_asset(
        "recipe", "dependency-flow", "dependency", project=project,
    ).path == dependency / "recipes/dependency-flow.md"

    from rig_workbench.orchestrate import config
    from rig_workbench.orchestrate.graph import cmd_graph
    monkeypatch.setattr(config, "INVOCATION_CWD", project)
    cmd_graph(["--focus", "pack/project-top"])
    assert "◈ pack:project:project-top" in capsys.readouterr().out

    shutil.rmtree(top)
    after_uninstall = build_brick_graph(project=project)
    remaining = {node["id"]: node for node in after_uninstall["nodes"]}
    assert remaining["instruction:shared"]["path"] == (
        "pack://user/dependency/facets/instructions/shared.md"
    )
    assert remaining["pattern:serial"]["path"] == "skills/rig/patterns/serial.md"


def test_graph_invalid_collection_fails_closed(tmp_path, monkeypatch):
    from test_pack_sdk_phase4d import _typed_dependency_pack
    from rig_workbench.packs import resolver
    from rig_workbench.packs.model import PackError

    project = tmp_path / "project"
    packs = project / ".rig/packs"
    _typed_dependency_pack(
        packs, "orphan", dependency=[{"id": "missing", "range": ">=1.0.0"}],
        external_owner="missing",
    )
    monkeypatch.setattr(resolver, "pack_roots", lambda _project=None: [("project", packs)])
    with pytest.raises(PackError, match="missing dependency"):
        build_brick_graph(project=project)


def test_core_graph_mode_is_immune_to_installed_pack_state(tmp_path, monkeypatch):
    from test_pack_sdk_phase4d import _typed_dependency_pack
    from rig_workbench.packs import resolver

    project = tmp_path / "project"
    packs = project / ".rig/packs"
    _typed_dependency_pack(packs, "ambient")
    monkeypatch.setattr(resolver, "pack_roots", lambda _project=None: [("project", packs)])
    core = build_brick_graph(project=project, mode="core")
    assert not any(node["kind"] == "pack" for node in core["nodes"])
    assert not any(node["path"].startswith("pack://") for node in core["nodes"])
