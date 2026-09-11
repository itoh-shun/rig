"""The real brick graph: a source tree read into nodes and edges.

`eval/affected.py` asks "which recipes can reach this prompt surface?", and answering it
means reading the orchestrator's own data model — `RIG_HOME`, `build_brick_graph`, and the
frontmatter of a recipe or a persona. That is a cross-pillar dependency, and until this
module existed it lived as three function-local imports inside the judgement layer, where
`tests/test_layering_contract.py`'s rule counts it exactly as it counts a module-level one
(「関数内 import は循環を消したのではなく隠している」).

So the dependency is **inverted rather than moved**, on the shape
`govern/conformance.py` uses for its run records: `affected.py` states what it needs as its
own small protocol — a tree in, nodes and edges out — and the callers hand this in.
`tests/test_layering_contract.py` declares this module a shell for the same reason
`PORT_ADAPTERS` declares `ports/local.py` one: an adapter exists precisely to hold what the
protocol may not.

**The layout comes in as an argument, and is not read from `affected` here.** Which path
prefixes hold prompt surfaces, and which extensions count, is eval's declaration — it is
checked into `evals/prompt-surfaces.json` and the gate's whole field of view — so this
module takes it from its caller rather than importing it. Two consequences, both wanted:
nothing here imports `eval.affected`, so the inverted edge stays one-way and no import
cycle can come back through this module; and there is exactly one definition of the
layout, rather than one here that could drift from the one the registry publishes.
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Collection, Sequence

from ..orchestrate import config
from ..orchestrate.graph import build_brick_graph
from ..orchestrate.recipes import parse_frontmatter


def source_tree_graph(
    root: pathlib.Path, *, prefixes: Sequence[tuple[str, str]],
    suffixes: Collection[str], strict: bool = False,
) -> tuple[dict[str, dict], list[dict]] | None:
    """A hermetic source-tree graph for prompt regression analysis.

    Installed extension tiers are intentionally excluded: affected-case selection must
    describe the checked-out source tree, not ambient user or project pack state.

    `strict` is the caller's statement about what an unreadable tree means, and the two
    answers mean opposite things (`affected._graph_at` says why). Reading a *revision*, an
    empty graph is indistinguishable from "the base branch wired nothing up", so `strict`
    returns None and the caller turns that into a named failure. Reading the *working
    tree*, an empty graph costs a demand the gate would otherwise make — the failure is
    toward asking for less — so an unreadable tree is an empty graph and the run carries
    on.
    """
    try:
        if config.RIG_HOME.resolve() == root.resolve():
            graph = build_brick_graph(project=root, mode="core")
            return ({node["path"]: node for node in graph["nodes"]}, graph["edges"])
    except (OSError, ValueError):
        pass
    # Fixture/project adapter: derive the same relations needed for reverse impact.
    try:
        nodes: dict[str, dict] = {}
        for prefix, kind in prefixes:
            directory = root / prefix
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if path.is_file() and path.suffix in suffixes:
                    name = str(path.relative_to(directory).with_suffix(""))
                    node_id = f"{kind}:{name}"
                    nodes[path.relative_to(root).as_posix()] = {
                        "id": node_id, "kind": kind,
                        "path": path.relative_to(root).as_posix(),
                    }
        edges: list[dict] = []
        for node in nodes.values():
            path = root / node["path"]
            if node["kind"] == "recipe":
                fm = parse_frontmatter(path)
                if fm.get("extends"):
                    edges.append({"from": node["id"], "to": f"recipe:{fm['extends']}"})
                for step in fm.get("steps") or []:
                    if not isinstance(step, dict):
                        continue
                    for field, kind in (("instruction", "instruction"),
                                        ("pattern", "pattern"),
                                        ("output_contract", "contract")):
                        if step.get(field):
                            edges.append({"from": node["id"],
                                          "to": f"{kind}:{step[field]}"})
                    # A gate is a pattern too, reached through a second field. Same
                    # sentinel as `build_brick_graph`: a step with no gate spells it
                    # as a placeholder dash, and a plain truth test grows an edge to
                    # `pattern:—`. Missing this field made every gate in the
                    # repository invisible to the revision reader — which is the
                    # whole of the coverage a `gate:` earns — while `pattern:` on the
                    # same step was seen, so whether the ratchet held came down to
                    # which of two fields the wiring used.
                    if step.get("gate") not in (None, "—", "-"):
                        edges.append({"from": node["id"],
                                      "to": f"pattern:{step['gate']}"})
                    for persona in step.get("personas") or []:
                        edges.append({"from": node["id"], "to": f"persona:{persona}"})
                    for policy in step.get("policies") or []:
                        edges.append({"from": node["id"], "to": f"policy:{policy}"})
            elif node["kind"] == "persona":
                fm = parse_frontmatter(path)
                for value in fm.get("inject") or []:
                    match = re.fullmatch(r"\[\[([a-z0-9-]+)(?:\|[^]]*)?\]\]", str(value))
                    if match:
                        candidates = [item["id"] for item in nodes.values()
                                      if item["kind"] == "wiki"
                                      and item["id"].split(":", 1)[1].endswith(match.group(1))]
                        target = candidates[0] if len(candidates) == 1 else f"wiki:{match.group(1)}"
                        edges.append({"from": node["id"], "to": target})
        return nodes, edges
    except Exception as exc:
        if strict:
            # Anything at all: `parse_frontmatter` hands `yaml.safe_load` straight
            # through, so a broken revision raises `YAMLError` — not a `ValueError`
            # — and a scalar where a mapping belongs raises `AttributeError`. The
            # question being answered is "could this tree be read", and every one of
            # those is the same no. Wider than the list because the list is a moving
            # target: it would have to name whatever `yaml` raises next. A defect in
            # this function is caught too, and reported as an unreadable base rather
            # than as a traceback — the same direction, and the price of not having
            # to keep an exhaustive list correct.
            return None
        if not isinstance(exc, (OSError, UnicodeError, ValueError)):
            raise
        return {}, []


#: The one source there is. Named so a caller passes a value rather than a function it
#: has to know the module of, exactly as `ports.local`'s adapter instances are named.
SOURCE_TREE_GRAPH = source_tree_graph
