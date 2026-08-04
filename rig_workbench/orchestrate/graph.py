"""orchestrate graph: deterministic core and extension-pack topology."""

from __future__ import annotations

import json
import pathlib
import re
import sys

from . import config
from .recipes import parse_frontmatter

_WIKI_LINK_RE = re.compile(r"\[\[([a-z0-9-]+)(?:\|[^\]]*)?\]\]")
_TIER_RANK = {tier: index for index, tier in enumerate(
    ("project", "user", "org", "official", "core")
)}


def _graph_body_links(path: pathlib.Path) -> list[str]:
    """Extract body/frontmatter wiki references, preserving first occurrence."""
    text = path.read_text(encoding="utf-8")
    seen: list[str] = []
    for slug in _WIKI_LINK_RE.findall(text):
        if slug not in seen:
            seen.append(slug)
    return seen


def _surface_kind(kind: str) -> str:
    return "contract" if kind == "output-contract" else kind


def _asset_name(kind: str, relative: str) -> str:
    from rig_workbench.packs.model import ASSET_DIRS

    path = pathlib.PurePosixPath(relative)
    name = str(path.relative_to(pathlib.PurePosixPath(ASSET_DIRS[kind])).with_suffix(""))
    if kind == "eval-case" and name.endswith("/case"):
        name = name[:-5]
    return name


def _owned_asset_id(tier: str, pack_id: str, logical_id: str) -> str:
    """Return the stable identity for one asset supplied by one pack."""
    return f"asset:{tier}:{pack_id}:{logical_id}"


def build_brick_graph(
    project: pathlib.Path | str | None = None, *, mode: str = "resolved",
) -> dict:
    """Build the active brick graph, or the immutable source-tree core graph.

    ``resolved`` overlays the resolver's validated pack collection on shipped
    core logical IDs. ``core`` is an explicit hermetic mode for source-tree
    regression analysis. Invalid pack collections propagate ``PackError``.
    """
    if mode not in {"resolved", "core"}:
        raise ValueError(f"unknown graph mode: {mode}")

    skills = config.RIG_HOME / "skills" / "rig"
    facets = skills / "facets"
    dirs = {
        "persona": facets / "personas",
        "instruction": facets / "instructions",
        "pattern": skills / "patterns",
        "policy": facets / "policies",
        "contract": facets / "output-contracts",
        "wiki": facets / "knowledge" / "wiki",
        "recipe": skills / "recipes",
        "agent": config.RIG_HOME / "agents",
        "command": config.RIG_HOME / "commands",
    }
    # Candidate metadata stays internal. Graph paths are relative or pack://;
    # real install paths are never serialised.
    candidates: dict[str, list[dict]] = {}
    for kind, directory in dirs.items():
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.md")):
            stem = str(path.relative_to(directory).with_suffix(""))
            if stem.startswith("_") or ("/" in stem and stem.split("/")[-1].startswith("_")):
                continue
            node_id = f"{kind}:{stem}"
            candidates.setdefault(node_id, []).append({
                "kind": kind, "path": path,
                "public_path": str(path.relative_to(config.RIG_HOME)),
                "tier": "core", "owner": None, "pack_id": "rig-core",
                "trust": "verified-bundled", "order": 1,
            })

    pack_records = []
    if mode == "resolved":
        from rig_workbench.packs.resolver import resolved_collection

        pack_records = resolved_collection(project=project)
        for record in pack_records:
            manifest = record.manifest
            owner = f"pack:{record.tier}:{record.id}"
            for asset_kind, paths in manifest["assets"].items():
                for relative in paths:
                    name = _asset_name(asset_kind, relative)
                    kind = _surface_kind(asset_kind)
                    # Resource names are owner-qualified because their public
                    # resolver contract is (pack_id, name), not a global ID.
                    node_id = (
                        f"resource:{record.id}/{name}"
                        if asset_kind == "resource" else f"{kind}:{name}"
                    )
                    candidates.setdefault(node_id, []).append({
                        "kind": kind, "path": record.path / relative,
                        "public_path": f"pack://{record.tier}/{record.id}/{relative}",
                        "tier": record.tier, "owner": owner, "pack_id": record.id,
                        "trust": record.verification_status, "order": 0,
                    })

        # Give the legacy shipped surfaces an explicit provenance owner when
        # they coexist with extension packs. Core-only mode remains byte-for-
        # byte compatible with the historical graph shape.
        if pack_records:
            for items in candidates.values():
                for item in items:
                    if item["owner"] is None:
                        item["owner"] = "pack:core:rig-core"

    def candidate_key(item: dict) -> tuple:
        return (_TIER_RANK[item["tier"]], item["order"], item["public_path"])

    winners = {node_id: sorted(items, key=candidate_key)[0]
               for node_id, items in candidates.items()}
    nodes: dict[str, dict] = {
        node_id: {"id": node_id, "kind": winner["kind"], "path": winner["public_path"]}
        for node_id, winner in winners.items()
    }
    edges: list[dict] = []

    def add_edge(src: str, rel: str, dst: str) -> None:
        edge = {"from": src, "rel": rel, "to": dst}
        if edge not in edges:
            edges.append(edge)

    # Pack-level provenance and topology. Only the resolver-approved records
    # are used; graph does no tier scanning or partial validation of its own.
    pack_by_id = {record.id: f"pack:{record.tier}:{record.id}" for record in pack_records}
    if pack_records:
        from rig_workbench import __version__

        core_pack_id = "pack:core:rig-core"
        pack_by_id["rig-core"] = core_pack_id
        nodes[core_pack_id] = {
            "id": core_pack_id, "kind": "pack", "path": "pack://core/rig-core",
            "tier": "core", "version": __version__, "trust": "verified-bundled",
        }
    for record in pack_records:
        pack_id = f"pack:{record.tier}:{record.id}"
        nodes[pack_id] = {
            "id": pack_id, "kind": "pack",
            "path": f"pack://{record.tier}/{record.id}",
            "tier": record.tier, "version": record.manifest["version"],
            "trust": record.verification_status,
        }

    # Logical nodes retain the historical active-winner view. Every supplied
    # pack asset also has an immutable owner-qualified identity so a typed
    # reference cannot be redirected by a higher-tier namesake.
    owned_by_owner_logical: dict[tuple[str, str], str] = {}
    for logical_id, items in sorted(candidates.items()):
        winner = winners[logical_id]
        for item in sorted(items, key=candidate_key):
            if item["owner"] is None:
                continue
            identity = _owned_asset_id(item["tier"], item["pack_id"], logical_id)
            owned_by_owner_logical[(item["owner"], logical_id)] = identity
            nodes[identity] = {
                "id": identity, "kind": "owned-asset", "asset_kind": item["kind"],
                "logical_id": logical_id, "path": item["public_path"],
                "owner": item["owner"], "tier": item["tier"], "trust": item["trust"],
                "provenance": {
                    "owner": item["owner"], "tier": item["tier"],
                    "uri": item["public_path"],
                },
            }
            add_edge(item["owner"], "owns-asset", identity)
            alias_rel = "active-alias" if item is winner else "shadowed-alias"
            add_edge(identity, alias_rel, logical_id)

    typed_owners: dict[tuple[str, str], str] = {}

    def owned_identity(owner: str, logical_id: str) -> str:
        existing = owned_by_owner_logical.get((owner, logical_id))
        if existing is not None:
            return existing
        _prefix, tier, pack_id = owner.split(":", 2)
        return _owned_asset_id(tier, pack_id, logical_id)

    for record in pack_records:
        pack_id = f"pack:{record.tier}:{record.id}"
        for dependency in record.manifest["dependencies"]:
            add_edge(pack_id, "depends-on", pack_by_id[dependency["id"]])
        for entrypoint in record.manifest.get("entrypoints", []):
            logical = f"{_surface_kind(entrypoint['kind'])}:{entrypoint['target']}"
            add_edge(pack_id, "entrypoint", logical)
            add_edge(pack_id, "entrypoint-owned", owned_identity(pack_id, logical))
        for reference in record.manifest.get("references", []):
            target = f"{_surface_kind(reference['kind'])}:{reference['id']}"
            owner = pack_by_id[reference["pack"]]
            typed_owners[(pack_id, target)] = owner
            add_edge(pack_id, "references", target)
            add_edge(pack_id, "references-owner", owner)
            add_edge(pack_id, "references-owned", owned_identity(owner, target))

    for node_id, items in candidates.items():
        winner = winners[node_id]
        for item in sorted(items, key=candidate_key):
            if item["owner"] is None:
                continue
            add_edge(item["owner"], "owns" if item is winner else "offers-shadowed", node_id)
            if item["kind"] == "resource":
                add_edge(item["owner"], "resource", node_id)

    persona_base: dict[str, list[str]] = {}
    for node_id in nodes:
        if node_id.startswith("persona:"):
            persona_base.setdefault(node_id.split("/")[-1].split(":")[-1], []).append(node_id)

    def persona_id(name: str) -> str:
        if f"persona:{name}" in nodes:
            return f"persona:{name}"
        hits = persona_base.get(name, [])
        return hits[0] if len(hits) == 1 else f"persona:{name}"

    def add_metadata_edge(winner: dict, src: str, rel: str, dst: str) -> None:
        """Emit the legacy logical triple plus its exact owner-bound form."""
        add_edge(src, rel, dst)
        owner = winner["owner"]
        if owner is None:
            return
        owned_src = owned_identity(owner, src)
        declared_owner = typed_owners.get((owner, dst))
        owned_dst = owned_identity(declared_owner, dst) if declared_owner else dst
        add_edge(owned_src, rel, owned_dst)

    # Parse relation metadata only from the active winner for each logical ID.
    for node_id, winner in sorted(winners.items()):
        kind, path = winner["kind"], winner["path"]
        if kind == "persona":
            fm = parse_frontmatter(path)
            for entry in fm.get("inject") or []:
                match = _WIKI_LINK_RE.fullmatch(str(entry))
                if match:
                    add_metadata_edge(winner, node_id, "injects", f"wiki:{match.group(1)}")
        elif kind == "wiki":
            for slug in _graph_body_links(path):
                if f"wiki:{slug}" != node_id:
                    add_metadata_edge(winner, node_id, "links-to", f"wiki:{slug}")
        elif kind == "recipe":
            fm = parse_frontmatter(path)
            if fm.get("extends"):
                add_metadata_edge(winner, node_id, "extends", f"recipe:{fm['extends']}")
            for step in fm.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                if step.get("instruction"):
                    add_metadata_edge(
                        winner, node_id, "uses-instruction",
                        f"instruction:{step['instruction']}",
                    )
                if step.get("pattern"):
                    add_metadata_edge(winner, node_id, "uses-pattern", f"pattern:{step['pattern']}")
                if step.get("gate") not in (None, "—", "-"):
                    add_metadata_edge(winner, node_id, "gated-by", f"pattern:{step['gate']}")
                for persona in step.get("personas") or []:
                    add_metadata_edge(
                        winner, node_id, "uses-persona", persona_id(str(persona)),
                    )
                for policy in step.get("policies") or []:
                    add_metadata_edge(winner, node_id, "applies-policy", f"policy:{policy}")
                if step.get("output_contract"):
                    add_metadata_edge(
                        winner, node_id, "emits-contract",
                        f"contract:{step['output_contract']}",
                    )
        elif kind == "agent":
            stem = node_id.split(":", 1)[1]
            possible = [stem]
            if stem.endswith("-reviewer"):
                possible.append(stem[:-len("-reviewer")])
            target = next((persona_id(value) for value in possible
                           if persona_id(value) in nodes), persona_id(possible[-1]))
            add_metadata_edge(winner, node_id, "mirrors", target)
        elif kind == "command":
            text = path.read_text(encoding="utf-8")
            for name in sorted(set(re.findall(r"facets/instructions/([a-z0-9-]+)", text))):
                add_metadata_edge(winner, node_id, "references", f"instruction:{name}")

    for edge in edges:
        edge["resolved"] = edge["to"] in nodes
    return {
        "nodes": sorted(nodes.values(), key=lambda item: (item["kind"], item["id"])),
        "edges": sorted(edges, key=lambda item: (item["from"], item["rel"], item["to"])),
    }


def cmd_graph(args):
    """graph [--json] [--focus <name>]: display the active typed graph."""
    graph = build_brick_graph(project=config.INVOCATION_CWD)
    if "--json" in args:
        print(json.dumps(graph, ensure_ascii=False, indent=2))
        return
    if "--focus" in args:
        name = args[args.index("--focus") + 1]
        ids = {
            node["id"] for node in graph["nodes"]
            if node["id"] == name
            or node["id"].split(":", 1)[-1] == name
            or node["id"].split(":", 1)[-1].split("/")[-1] == name
            or node["id"].rsplit(":", 1)[-1] == name
            or (node["kind"] == "pack" and name in {
                node["id"].rsplit(":", 1)[-1],
                f"pack/{node['id'].rsplit(':', 1)[-1]}",
                node["id"].split(":", 1)[-1].replace(":", "/"),
            })
        }
        if not ids:
            print(f"[graph] no node matches focus: {name}")
            raise SystemExit(1)
        for node_id in sorted(ids):
            print(f"◈ {node_id}")
            for edge in graph["edges"]:
                if edge["from"] == node_id:
                    suffix = "" if edge["resolved"] else "  (unresolved)"
                    print(f"  → {edge['rel']} → {edge['to']}{suffix}")
            for edge in graph["edges"]:
                if edge["to"] == node_id:
                    print(f"  ← {edge['rel']} ← {edge['from']}")
        return
    kinds: dict[str, int] = {}
    for node in graph["nodes"]:
        kinds[node["kind"]] = kinds.get(node["kind"], 0) + 1
    rels: dict[str, int] = {}
    unresolved = [edge for edge in graph["edges"] if not edge["resolved"]]
    for edge in graph["edges"]:
        rels[edge["rel"]] = rels.get(edge["rel"], 0) + 1
    print("Brick graph (typed; derived from frontmatter/steps, never hand-written)")
    print(f"  nodes: {len(graph['nodes'])}  (" + " / ".join(
        f"{kind} {count}" for kind, count in sorted(kinds.items())) + ")")
    print(f"  edges: {len(graph['edges'])}  (" + " / ".join(
        f"{rel} {count}" for rel, count in sorted(rels.items())) + ")")
    print(f"  unresolved edges: {len(unresolved)}")
    for edge in unresolved:
        print(f"    ✗ {edge['from']} → {edge['rel']} → {edge['to']}")
    print("  one-hop exploration: graph --focus <name> / machine-readable: graph --json")
