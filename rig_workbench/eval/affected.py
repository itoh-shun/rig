"""Deterministic prompt-surface impact analysis."""

from __future__ import annotations

import io
import json
import pathlib
import re
import subprocess
import tarfile
import tempfile
from typing import Any

from .cases import EvalCaseError, canonical_json, validate_case
from .execution import GIT_DETERMINISTIC

REGISTRY_VERSION = 2
_SURFACE_PREFIXES = (
    ("skills/engine/facets/instructions/", "instruction"),
    ("skills/engine/facets/personas/", "persona"),
    ("skills/engine/facets/policies/", "policy"),
    ("skills/engine/facets/output-contracts/", "contract"),
    ("skills/engine/facets/knowledge/", "wiki"),
    ("skills/engine/patterns/", "pattern"),
    ("skills/engine/recipes/", "recipe"),
    ("skills/engine/agents/", "agent"),
    ("agents/", "agent"),
    ("commands/", "command"),
)

# Roots whose **direct children** are surfaces while their subdirectories are not.
#
# `skills/engine/` holds the engine's own prose — SKILL.md, which decides
# PARSE/RESOLVE/COMPOSE/RUN for every single run, and PACKS.md, which SKILL.md
# itself sends the reader to. Every registered root above is a *subdirectory* of
# this one, so the two documents that govern all of them were the only prompt
# surfaces in the repository that the registry could not see: editing one line of
# a persona registered as an affected surface, while rewriting §6 of SKILL.md
# reported `noop`. That is the same defect the ratchet was built to remove
# (#383/#384), pointing the other way — there, a check that fired on everything
# distinguished nothing; here, the check does not fire on the file that matters
# most.
#
# Stated as a rule about the directory rather than as a list of two filenames on
# purpose: an explicit list reproduces the hole the moment somebody adds a third
# engine document. Subdirectories are excluded because they are either already
# registered above, or are not prompt surfaces at all (`corpora/` is drill
# fixture data — evidence the gate consumes, not prose the model reads).
_SURFACE_FLAT_ROOTS = (
    ("skills/engine/", "engine"),
)
_KNOWN_SUFFIXES = {".md", ".yaml", ".yml"}
# The declaration of what the surfaces are, checked in so a change to the gate's
# field of view shows up in a diff.
REGISTRY_REL = "evals/prompt-surfaces.json"


def prompt_surface_registry() -> dict:
    return {
        "prompt_surface_registry_version": REGISTRY_VERSION,
        "roots": [
            {"prefix": prefix, "kind": kind, "recursive": True,
             "extensions": sorted(_KNOWN_SUFFIXES)}
            for prefix, kind in _SURFACE_PREFIXES
        ] + [
            {"prefix": prefix, "kind": kind, "recursive": False,
             "extensions": sorted(_KNOWN_SUFFIXES)}
            for prefix, kind in _SURFACE_FLAT_ROOTS
        ],
    }


def _merge_base(root: pathlib.Path, base: str, head: str) -> str:
    """The commit this branch actually forked from.

    Diffing against the base *tip* attributes everything the base branch did
    since the fork to this branch as well. On a branch that diverged a hundred
    commits ago that is most of the prompt layer, so the gate demands cases for
    surfaces the author never opened — which is what makes a release-scale PR
    structurally unpassable (#367). The fork point is what "this branch changed"
    means. Falls back to the base when there is no common ancestor.
    """
    revision = "HEAD" if head == "working" else head
    try:
        completed = subprocess.run(
            ["git", "merge-base", base, revision], cwd=root,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return base
    value = completed.stdout.strip()
    if completed.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", value):
        return base
    return value


def _changed_files(root: pathlib.Path, base: str, head: str) -> list[str]:
    for value, label in ((base, "base"), (head, "head")):
        if not isinstance(value, str) or not value or "\n" in value or "\x00" in value:
            raise EvalCaseError(f"affected {label} revision is invalid")
    # Same pins as the signed diff: which files this reports decides which cases
    # are affected, and `diff.renames` alone changes that answer — a rename is one
    # path under detection and two without it. `core.quotePath` decides whether a
    # non-ASCII path arrives in a form any surface prefix can match.
    args = ["git", *GIT_DETERMINISTIC,
            "diff", "--name-only", "--relative", "--no-ext-diff", "--no-textconv",
            _merge_base(root, base, head)]
    if head != "working":
        args.append(head)
    args.append("--")
    try:
        completed = subprocess.run(
            args, cwd=root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvalCaseError("cannot compute affected git diff") from exc
    if completed.returncode != 0:
        raise EvalCaseError("cannot compute affected git diff")
    paths = set(completed.stdout.splitlines())
    if head == "working":
        untracked = subprocess.run(
            ["git", *GIT_DETERMINISTIC, "ls-files", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, shell=False,
        )
        if untracked.returncode != 0:
            raise EvalCaseError("cannot enumerate untracked affected files")
        paths.update(untracked.stdout.splitlines())
    safe = [path for path in paths if path and "\n" not in path and "\x00" not in path]
    return sorted(safe)


def _resolved_head(root: pathlib.Path, head: str) -> str:
    revision = "HEAD" if head == "working" else head
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", f"{revision}^{{commit}}"], cwd=root,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvalCaseError("cannot resolve affected head revision") from exc
    value = completed.stdout.strip()
    if completed.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise EvalCaseError("cannot resolve affected head revision")
    return value


def _classify(path: str, prefix: str, kind: str) -> dict:
    suffix = pathlib.PurePosixPath(path).suffix
    relative = path[len(prefix):]
    name = str(pathlib.PurePosixPath(relative).with_suffix(""))
    resolved_kind = kind if suffix in _KNOWN_SUFFIXES and name else "unknown"
    return {"path": path, "kind": resolved_kind, "id": f"{resolved_kind}:{name}"}


def _surface(path: str) -> dict | None:
    if path == REGISTRY_REL:
        # Not a prompt surface: it is the declaration of what the prompt surfaces
        # *are*. Judged by `_registry_narrowings` instead — no eval case can be
        # written for a registry, so demanding one made it permanently unpassable.
        return None
    for prefix, kind in _SURFACE_PREFIXES:
        if path.startswith(prefix):
            return _classify(path, prefix, kind)
    if path.startswith("skills/engine/facets/"):
        return {"path": path, "kind": "unknown", "id": f"unknown:{path}"}
    # Checked after the recursive roots so a registered subdirectory always wins:
    # a recipe stays `recipe:<name>` rather than becoming `engine:recipes/<name>`.
    for prefix, kind in _SURFACE_FLAT_ROOTS:
        if path.startswith(prefix) and "/" not in path[len(prefix):]:
            return _classify(path, prefix, kind)
    return None


def prompt_surface_digests(root: pathlib.Path, revision: str) -> dict[str, str]:
    """Every prompt surface in `revision`'s tree, mapped to its git object id.

    Signed into the evidence so that "has this measurement's tree moved?" can be
    answered from content instead of from ancestry. Ancestry cannot survive a
    squash or rebase merge — both drop the measured commit out of the history
    entirely, and the evidence travelling with them then names a commit that is
    either gone or no longer an ancestor of anything. Content survives both,
    because a squash of a branch reproduces its files exactly.

    Recorded for the whole surface set rather than only the surfaces the change
    touched: the gate's affected set is computed from whichever base CI hands it,
    the measurement's from whichever base the maintainer passed, and the two need
    not agree. A path present in the gate's set and missing from a narrower
    recording would be indistinguishable from a file created after the
    measurement, which has to fail. Recording all of them makes the absence
    itself meaningful — surfaces missing here did not exist when this was
    measured.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-tree", "-r", "-z", revision], cwd=root, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=30, shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvalCaseError("cannot read prompt surface digests") from exc
    if completed.returncode != 0:
        raise EvalCaseError("cannot read prompt surface digests")
    digests: dict[str, str] = {}
    for entry in completed.stdout.split("\0"):
        if not entry or "\t" not in entry:
            continue
        metadata, path = entry.split("\t", 1)
        fields = metadata.split(" ")
        if len(fields) != 3 or fields[1] != "blob":
            continue                   # submodule or tree: not a file we can hash
        if _surface(path) is not None:
            digests[path] = fields[2]
    return digests


def _graph(
    root: pathlib.Path, *, mode: str = "source-tree",
) -> tuple[dict[str, dict], list[dict]]:
    """Use a hermetic source-tree graph for prompt regression analysis.

    Installed extension tiers are intentionally excluded: affected-case
    selection must describe the checked-out source tree, not ambient user or
    project pack state.
    """
    if mode != "source-tree":
        raise ValueError(f"unknown affected graph mode: {mode}")
    try:
        from rig_workbench.orchestrate import config
        from rig_workbench.orchestrate.graph import build_brick_graph
        if config.RIG_HOME.resolve() == root.resolve():
            graph = build_brick_graph(project=root, mode="core")
            return ({node["path"]: node for node in graph["nodes"]}, graph["edges"])
    except (OSError, ValueError):
        pass
    # Fixture/project adapter: derive the same relations needed for reverse impact.
    try:
        from rig_workbench.orchestrate.recipes import parse_frontmatter
        nodes: dict[str, dict] = {}
        for prefix, kind in _SURFACE_PREFIXES:
            directory = root / prefix
            if not directory.is_dir():
                continue
            for path in sorted(directory.rglob("*")):
                if path.is_file() and path.suffix in _KNOWN_SUFFIXES:
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
    except (OSError, UnicodeError, ValueError):
        return {}, []


def _surfaces_at(root: pathlib.Path, revision: str, destination: pathlib.Path) -> int | None:
    """Write `revision`'s prompt surfaces into `destination`; count them, or None.

    `_graph` reads frontmatter off the filesystem, so answering "what did the
    graph look like at that commit" means putting that commit's surfaces on a
    filesystem. One `git archive` of the whole tree rather than a `git show` per
    file: the per-file form costs a process per surface per revision — measured at
    0.73s for 200 files against 0.013s for the archive — and the whole tree is
    asked for because `git archive` treats a pathspec that matches nothing as
    fatal, which every fixture repo with only `skills/engine/` in it would be.

    Members are filtered here rather than handed to `tar` wholesale: only regular
    files, only paths the registry calls a surface, and no path that climbs out of
    the destination. That is the same stance `prompt_surface_digests` takes when it
    skips anything that is not a blob — a symlink or a gitlink in the tree is not a
    prompt this analysis can read, and it is not going to be followed to find out.
    """
    try:
        completed = subprocess.run(
            ["git", "archive", "--format=tar", revision], cwd=root,
            capture_output=True, timeout=30, shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    written = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:") as archive:
            for member in archive:
                if not member.isfile() or _surface(member.name) is None:
                    continue
                parts = pathlib.PurePosixPath(member.name).parts
                if not parts or ".." in parts or parts[0] in {"/", ""}:
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    continue
                target = destination.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(extracted.read())
                written += 1
    except (OSError, tarfile.TarError, ValueError):
        return None
    return written


def _graph_at(root: pathlib.Path, revision: str) -> tuple[dict[str, dict], list[dict]] | None:
    """The brick graph as it stands in `revision`'s tree, or None if unreadable.

    None rather than an empty graph, and the distinction is the whole point: the
    caller turns None into a named failure, while an empty graph would mean "the
    base branch wired nothing up" and quietly restore the bypass this reading
    exists to close. `_graph`'s fixture adapter answers a tree it cannot parse with
    `({}, [])`, so a tree that has surfaces in it and yielded no nodes is that
    silence, not an answer.
    """
    with tempfile.TemporaryDirectory(prefix="rig-eval-graph-") as directory:
        tree = pathlib.Path(directory)
        extracted = _surfaces_at(root, revision, tree)
        if extracted is None:
            return None
        nodes, edges = _graph(tree)
        if extracted and not nodes:
            return None
        return nodes, edges


def _landing_graph(
    head: tuple[dict[str, dict], list[dict]],
    base: tuple[dict[str, dict], list[dict]] | None,
    fork: tuple[dict[str, dict], list[dict]] | None,
) -> tuple[dict[str, dict], list[dict]] | None:
    """The reference graph the merge would put on the base branch.

    `_landing_coverage` corrected the *case set* and left the other half of the
    question where it was. Whether a surface is covered is not decided by the case
    set alone: a persona is covered because some recipe references it and a case
    binds that recipe, and that reference is an edge in this graph. Read only off
    the branch's tree, the landing view judged coverage against the branch's
    topology — so forking from before the base branch wired the reference, and
    editing only the persona, reported `debt` and merged green, with the merge
    restoring the reference the branch never touched.

    Same three-way reading as the case set, at the granularity of a single edge:
    `head | (base - fork)`. Edges rather than reachability, deliberately — merging
    "which recipes reach this surface" per surface would lose the case where the
    branch removes one reference while the base branch adds a different one to the
    same pair, and read the sum as unchanged. And the same monotone half as
    `_landing_coverage`: an edge the base branch *deleted* is not subtracted, so
    coverage can be over-approximated and never under-approximated. Over-approxi-
    mation here can only make `landing_covered` true, which is `coverage_stale` and
    fatal — it cannot hide anything, only ask for a re-measurement that the base
    branch's own push would ask for a moment later.

    The two revisions are read by the same reader, which is what makes the
    subtraction safe: `_graph` describes the rig repository itself through
    `build_brick_graph` and every other tree through its adapter, and the two do
    not agree edge for edge. Any such difference is present in `base` and in `fork`
    alike and cancels; what survives is only what the base branch genuinely added.
    Node ids are then translated into the branch's spelling by path, because that
    is what the reachability walk starts from.
    """
    if base is None or fork is None:
        return None
    head_nodes, head_edges = head
    base_nodes, base_edges = base
    gained = ({(edge["from"], edge["to"]) for edge in base_edges}
              - {(edge["from"], edge["to"]) for edge in fork[1]})
    if not gained:
        return head_nodes, head_edges
    path_of = {node["id"]: path for path, node in base_nodes.items()}

    def canonical(node_id: str) -> str:
        path = path_of.get(node_id)
        return head_nodes[path]["id"] if path in head_nodes else node_id

    nodes = {**{path: node for path, node in base_nodes.items() if path not in head_nodes},
             **head_nodes}
    edges = list(head_edges)
    seen = {(edge["from"], edge["to"]) for edge in head_edges}
    for source, target in sorted(gained):
        edge = (canonical(source), canonical(target))
        if edge not in seen:
            seen.add(edge)
            edges.append({"from": edge[0], "to": edge[1]})
    return nodes, edges


def _recipes_by_surface(root: pathlib.Path, surfaces: list[dict]) -> dict[str, list[str]]:
    return _reachable_recipes(_graph(root), surfaces)


def _reachable_recipes(
    graph: tuple[dict[str, dict], list[dict]], surfaces: list[dict],
) -> dict[str, list[str]]:
    nodes_by_path, edges = graph
    reverse: dict[str, set[str]] = {}
    for edge in edges:
        reverse.setdefault(edge["to"], set()).add(edge["from"])
    result: dict[str, list[str]] = {}
    for surface in surfaces:
        changed_id = (
            nodes_by_path[surface["path"]]["id"]
            if surface["path"] in nodes_by_path else surface["id"]
        )
        queue = [changed_id]
        visited = set(queue)
        recipes: set[str] = set()
        while queue:
            node = queue.pop(0)
            if node.startswith("recipe:"):
                recipes.add(node.split(":", 1)[1])
            for parent in sorted(reverse.get(node, set())):
                if parent not in visited:
                    visited.add(parent)
                    queue.append(parent)
        result[surface["path"]] = sorted(recipes)
    return result


def _surface_commits(
    root: pathlib.Path, merge_base: str, head: str, paths: list[str],
) -> dict[str, list[str]]:
    """Which commits touched each uncovered path, newest first.

    A large PR that fails this gate otherwise reports a wall of paths with no
    way in. Naming the commit behind each one turns it into a triage list —
    the author can see which change owes a case, rather than the whole branch.
    Only computed for the paths that are actually blocking.
    """
    if not paths:
        return {}
    revision = "HEAD" if head == "working" else head
    result: dict[str, list[str]] = {}
    for path in sorted(set(paths)):
        try:
            completed = subprocess.run(
                ["git", "log", "--format=%h", "--max-count=5",
                 f"{merge_base}..{revision}", "--", path],
                cwd=root, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=15, shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            commits = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
            if commits:
                result[path] = commits
    return result


def _registry_at(root: pathlib.Path, revision: str) -> dict[str, dict] | None:
    """prefix → its declared root at `revision`, or None if unreadable.

    Same stance as `_coverage_at`: None means the question could not be answered,
    and the caller then declines to accuse the change of anything.
    """
    try:
        blob = subprocess.run(
            ["git", "show", f"{revision}:{REGISTRY_REL}"], cwd=root,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if blob.returncode != 0:
        return None                    # not present at the base — nothing to lose
    try:
        value = json.loads(blob.stdout)
    except json.JSONDecodeError:
        return None
    roots = value.get("roots") if isinstance(value, dict) else None
    if not isinstance(roots, list):
        return None
    return {r["prefix"]: r for r in roots if isinstance(r, dict) and isinstance(r.get("prefix"), str)}


def _registry_narrowings(before: dict[str, dict] | None, after: dict) -> list[str]:
    """What this change took away from the gate's field of view.

    Editing the registry used to be fatal outright, on the reasoning that changing
    what the gate can see is not a coverage question. True, and the consequence was
    that **the registry could never be extended without failing the job** — the
    exact shape #383 was: a check nobody can pass, whose real lesson is that this
    job gets merged past. It taught that on the one change class that widens the
    gate's coverage.

    So the same rule the rest of this module uses applies to the registry itself:
    it is monotonic. Adding a root, or widening one, is the direction the gate is
    supposed to move and passes. Removing a root, renaming its kind (which silently
    orphans every case bound to the old ids), or narrowing its extensions or its
    recursion is coverage going *down*, and stays fatal.
    """
    if before is None:
        return []
    after_by_prefix = {r["prefix"]: r for r in after.get("roots", [])}
    lost: list[str] = []
    for prefix, root in sorted(before.items()):
        now = after_by_prefix.get(prefix)
        if now is None:
            lost.append(f"root removed: {prefix} (was {root.get('kind')})")
            continue
        if now.get("kind") != root.get("kind"):
            lost.append(f"kind renamed: {prefix} {root.get('kind')} -> {now.get('kind')} "
                        "(orphans every case bound to the old ids)")
        dropped = set(root.get("extensions") or []) - set(now.get("extensions") or [])
        if dropped:
            lost.append(f"extensions dropped: {prefix} ({', '.join(sorted(dropped))})")
        if root.get("recursive") and not now.get("recursive", True):
            lost.append(f"no longer recursive: {prefix}")
    return lost


def _coverage_at(root: pathlib.Path, revision: str) -> dict[str, set[str]] | None:
    """case id → the prompt surfaces it covered at `revision`, or None if unreadable.

    Read from the git tree rather than the working copy, and read at two revisions:
    the base branch's tip, which is the coverage this change has to still deliver,
    and the fork point, which is the only way to tell coverage this change *removed*
    from coverage it simply never had. `_landing_coverage` combines them.

    None means the question could not be answered — a blobless clone, a git that
    would not answer. `_regressions` declines to accuse anyone of a regression it
    cannot demonstrate; `analyze_affected` reports the unanswerable comparison
    itself rather than passing quietly, because the base-tip reading is what stands
    between a stale fork and an unmeasured prompt surface.
    """
    try:
        listing = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", revision, "--", "evals/cases/"],
            cwd=root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15, shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listing.returncode != 0:
        return None
    coverage: dict[str, set[str]] = {}
    for path in listing.stdout.splitlines():
        if not path.endswith("/case.json"):
            continue
        try:
            blob = subprocess.run(
                ["git", "show", f"{revision}:{path}"], cwd=root, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=15, shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if blob.returncode != 0:
            return None
        try:
            value = json.loads(blob.stdout)
        except json.JSONDecodeError:
            continue        # a malformed case at the base is not this change's fault
        if not isinstance(value, dict) or value.get("status") != "approved":
            continue
        case_id = value.get("id")
        surfaces = value.get("prompt_surfaces")
        if isinstance(case_id, str) and isinstance(surfaces, list):
            coverage[case_id] = {s for s in surfaces if isinstance(s, str)}
    return coverage


def _landing_coverage(head: dict[str, set[str]], base: dict[str, set[str]] | None,
                      fork: dict[str, set[str]] | None) -> dict[str, set[str]] | None:
    """The coverage the merge would put on the base branch, case by case.

    Every comparison below this line asks a question about *state* — does the tree
    that ends up on trunk still cover what the base branch covers — and the answer
    has to be read off the tree the merge produces, not off the branch tip. The two
    differ precisely where the branch is behind: a case the base branch gained after
    this branch forked is missing from the branch and present after the merge,
    because none of the three merge buttons removes it.

    That gap was the bypass. Comparing the branch tip against the **fork point**
    made "the case did not exist yet" and "the case is gone" the same answer, so
    forking from before a case was written and editing only the prompt reported
    `debt` — no case for this surface — and merged green, with the push to the
    default branch going red on the very evidence check the PR never reached. The
    same shape as #402 and the same shape #411 removed from the evidence ratchet.

    So the reference is the base tip, and the branch is charged for what it *takes
    away* rather than for what it never had: a surface is present after the merge
    if the branch has it, or if the base branch has it and the fork point did not.
    Written as `head | (base - fork)`, which makes coverage regression reduce to
    `(base & fork) - head` — exactly "the branch dropped it and the base branch
    still has it".

    That is the monotone half of a three-way merge, and the other half is left out
    on purpose: coverage the base branch *deleted* after the fork is not subtracted
    from what the branch carries, so this can over-state what lands and never
    under-state it. A true merge would drop it, which would let a case deleted on
    the base branch excuse an unmeasured edit here. Over-statement costs nothing in
    the other direction: `coverage_stale` is only reached for a surface this branch
    does not cover, and `_regressions` only reads elements of `base`, so neither an
    accusation nor a silence can be manufactured from the difference.

    None means the question could not be answered; the caller turns that into a
    named failure rather than a pass, because this is now the check that stands
    between a stale fork and an unmeasured prompt.
    """
    if base is None or fork is None:
        return None
    landing = {case_id: set(surfaces) for case_id, surfaces in head.items()}
    for case_id, surfaces in base.items():
        gained = surfaces - fork.get(case_id, set())
        if gained:
            landing.setdefault(case_id, set()).update(gained)
    return landing


def _landing_registry(after: dict, base: dict[str, dict] | None,
                      fork: dict[str, dict] | None) -> dict:
    """The same three-way reading of the registry, root by root.

    `after` is the registry the checked-out code declares. A root the base branch
    added after this branch forked is not in it and will be in the merge, so it is
    restored here rather than read as this change removing it — the identical
    correction `_landing_coverage` makes, applied per attribute because a root can
    be widened as well as added.

    Unlike coverage, being behind here needs no equivalent of `coverage_stale`:
    what the merge lands is the base branch's *wider* field of view, so nothing the
    gate could see stops being seen. One shape is not corrected — a root the fork
    point did not declare and both sides do, where the branch's own declaration
    wins whole and an extension the base branch added to it is not merged in — and
    a branch behind on that reads as a narrowing. It needs `_KNOWN_SUFFIXES` itself
    to have widened since the fork, and the remedy is the same merge. Coverage
    staleness is fatal because the opposite is true there — the merge lands the base
    branch's case together with this branch's unmeasured edit to the surface it
    covers.
    """
    if base is None:
        return after
    fork = fork or {}
    roots = {root["prefix"]: dict(root) for root in after.get("roots", [])
             if isinstance(root, dict) and isinstance(root.get("prefix"), str)}
    for prefix, declared in base.items():
        earlier = fork.get(prefix)
        landing = roots.get(prefix)
        if landing is None:
            if earlier is None:
                roots[prefix] = dict(declared)      # the base branch added it
            continue
        if earlier is None:
            continue                # both sides declared it; this branch's wins
        widened = set(declared.get("extensions") or []) - set(earlier.get("extensions") or [])
        if widened:
            landing["extensions"] = sorted(set(landing.get("extensions") or []) | widened)
        if declared.get("recursive") and not earlier.get("recursive", True):
            landing["recursive"] = True
        if (declared.get("kind") != earlier.get("kind")
                and landing.get("kind") == earlier.get("kind")):
            landing["kind"] = declared.get("kind")
    return {**after, "roots": sorted(roots.values(), key=lambda item: item["prefix"])}


def _coverage_matches(
    coverage: dict[str, set[str]], surfaces: list[dict], recipes: list[str],
    recipes_by_surface: dict[str, list[str]],
) -> tuple[dict[str, list[str]], dict[str, list[str]], dict[str, bool]]:
    """Which cases in `coverage` answer for each affected surface and recipe.

    One predicate, asked twice: once of the cases this branch carries, and once of
    the coverage the merge would land. Both readings have to agree on what "covered"
    means — a second, separately written copy of the direct/recipe/indirect rules is
    how the landing view would come to disagree with the branch view about the one
    surface an attacker picks.
    """
    recipe_matches = {
        recipe: [case_id for case_id, bound in sorted(coverage.items())
                 if f"recipe:{recipe}" in bound]
        for recipe in recipes
    }
    matched: dict[str, list[str]] = {}
    covered: dict[str, bool] = {}
    for surface in surfaces:
        found = [case_id for case_id, bound in sorted(coverage.items())
                 if surface["id"] in bound]
        if surface["kind"] == "recipe":
            found.extend(recipe_matches.get(surface["id"].split(":", 1)[-1], []))
        matched[surface["path"]] = found
        covered[surface["path"]] = bool(found) or any(
            recipe_matches[recipe] for recipe in recipes_by_surface[surface["path"]]
        )
    return recipe_matches, matched, covered


def _regressions(before: dict[str, set[str]] | None,
                 after: dict[str, set[str]]) -> list[str]:
    """Coverage the change took away: a case deleted, or one that dropped a surface.

    This is the half of the ratchet that stays a hard failure. Not having written
    a case yet is a starting position; deleting one somebody already earned with a
    measured red→green run is a step backwards, and a coverage gate that permits
    steps backwards is not a ratchet.
    """
    if before is None:
        return []
    lost: list[str] = []
    for case_id, surfaces in sorted(before.items()):
        if case_id not in after:
            lost.append(f"case:{case_id} (deleted; covered {', '.join(sorted(surfaces)) or 'nothing'})")
            continue
        dropped = surfaces - after[case_id]
        if dropped:
            lost.append(f"case:{case_id} (no longer covers {', '.join(sorted(dropped))})")
    return lost


def _load_cases(root: pathlib.Path) -> list[dict]:
    cases: list[dict] = []
    tier = root / "evals" / "cases"
    if not tier.is_dir():
        return cases
    for path in sorted(tier.glob("*/case.json")):
        try:
            raw = path.read_text(encoding="utf-8")
            value = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvalCaseError(f"cannot read affected case: {path}") from exc
        validate_case(value)
        if value["status"] != "approved" or raw != canonical_json(value):
            raise EvalCaseError(f"affected coverage case is not approved canonical JSON: {path}")
        cases.append(value)
    return cases


def analyze_affected(
    repo: pathlib.Path | str, *, base: str, head: str = "working",
    require_cases: bool = False, ratchet: bool = False,
    evidence_dir: pathlib.Path | str | None = None,
) -> dict:
    """Which prompt surfaces a change touches, and whether cases cover them.

    `require_cases` is the strict form: every affected surface must already have a
    case, or the change is `uncovered`. Correct as a destination and unreachable as
    a starting point — with an empty `evals/cases/` it fails every change that
    touches a prompt surface, including the ones that add the first case. A sensor
    that fires on everything reports nothing, and teaches people to merge past it.

    `ratchet` is the same requirement expressed as a direction rather than a
    threshold. A surface nobody has written a case for yet is **debt**: counted,
    named, not fatal. Coverage that this change *removes* is a **regression**, and
    still fatal. Debt can only be paid down and coverage can only go up, which is
    the same monotonic rule the policy layer uses — and unlike a threshold, it
    produces a number that moves from the first day.

    Two references, deliberately, and the split is the whole of the fix for the
    fork-point bypass:

    * **what this change changes** is diffed from `merge-base(base, head)`. That is
      not a compromise with #367 but what all three merge buttons do — merge and
      squash are three-way from the fork point, rebase replays the branch's own
      diffs — so the fork point is literally the set of edits that will land, and
      diffing from the tip would charge this branch for the base branch's work.
    * **what this change must still cover** is compared against `base`, the base
      branch's tip, through `_landing_coverage`. Coverage is state rather than a
      diff, and the state that matters is the one the merge produces. Against the
      fork point, forking from before a case existed and editing only the prompt
      read as `debt` and merged green.

    Being behind the base branch on a case that covers a surface this change edits
    is therefore **stale**, and fatal: the case comes back with the merge and the
    edit is unmeasured. It is not debt, because somebody did write that case.

    "Covered" is read off the landing tree on both counts — the cases through
    `_landing_coverage`, and the wiring that makes a case answer for a surface it
    does not name through `_landing_graph`. Correcting only the first left the same
    bypass one step further out: fork from before the base branch pointed a recipe
    at a persona, edit only the persona, and the branch's own tree honestly reports
    that nothing reaches it.

    On a push to the default branch this all collapses back to the old reading, so
    long as `github.event.before` is an ancestor of what was pushed: `merge-base`
    returns `before` itself, `base - fork` is empty, and both landing views are the
    pushed tree. A force-push is the exception — ancestry is what the argument rests
    on — and there the push is judged like any other divergent history, which is to
    say a case the rewrite dropped is named rather than passed over.
    """
    try:
        root = pathlib.Path(repo).resolve()
    except OSError as exc:
        raise EvalCaseError("cannot resolve affected repository") from exc
    changed = _changed_files(root, base, head)
    resolved_head = _resolved_head(root, head)
    merge_base = _merge_base(root, base, head)
    surfaces = [surface for path in changed if (surface := _surface(path)) is not None]
    head_graph = _graph(root)
    recipes_by_surface = _reachable_recipes(head_graph, surfaces)
    recipes = sorted({recipe for values in recipes_by_surface.values() for recipe in values})
    cases = _load_cases(root)
    selected: list[str] = []
    uncovered: list[str] = []
    debt: list[str] = []
    # Keyed by path so a surface that is stale both directly and through a recipe
    # is reported once, the way `debt` is deduplicated by being a set of paths.
    stale_by_path: dict[str, str] = {}
    demand = require_cases or ratchet
    head_coverage = {case["id"]: set(case.get("prompt_surfaces", [])) for case in cases}
    # Only under the ratchet. Strict mode already fails every affected surface this
    # branch does not cover, whatever the base branch says about it, so it has
    # nothing to gain from the landing view and keeps its exact old meaning.
    base_coverage = _coverage_at(root, base) if ratchet else None
    landing_coverage = (
        _landing_coverage(head_coverage, base_coverage, _coverage_at(root, merge_base))
        if ratchet else None
    )
    # Both arguments of "is this covered?" get the same correction, or the fix is
    # half a fix: the case set says which cases exist, the graph says which surfaces
    # they reach, and reading the second one off the branch judges the merge by the
    # branch's wiring. Gated on `ratchet` like the coverage read above — strict mode
    # keeps its exact old meaning, and neither mode pays for two `git archive`s it
    # would not consult.
    landing_graph = (
        _landing_graph(head_graph, _graph_at(root, base), _graph_at(root, merge_base))
        if ratchet else None
    )
    landing_by_surface = (_reachable_recipes(landing_graph, surfaces)
                          if landing_graph is not None else {})
    landing_recipe_list = sorted(
        {recipe for values in landing_by_surface.values() for recipe in values})
    recipe_matches, matched_by_path, covered_by_path = _coverage_matches(
        head_coverage, surfaces, recipes, recipes_by_surface)
    landing_recipe_matches, landing_matched, landing_covered = (
        _coverage_matches(landing_coverage, surfaces, landing_recipe_list, landing_by_surface)
        if landing_coverage is not None and landing_graph is not None else ({}, {}, {})
    )
    for surface in surfaces:
        path = surface["path"]
        if surface["kind"] == "unknown":
            # Not a coverage question: a file under a registered root whose kind the
            # registry does not recognise is a surface nobody is even tracking. That
            # stays fatal in both modes — a ratchet on an unmeasured thing is nothing.
            uncovered.append(path)
        elif demand and not covered_by_path[path]:
            if landing_covered.get(path):
                # Covered once this lands, and not covered by anything this branch
                # carries: the merge puts the base branch's case next to this
                # branch's unmeasured edit to the surface it covers. Fatal rather
                # than debt — debt is a surface nobody has written a case for, and
                # somebody has written this one. Merging the base branch in and
                # re-measuring is the answer, and is what the default branch's own
                # push would demand a moment later.
                owed = sorted(set(landing_matched[path]).union(
                    case_id for recipe in landing_by_surface[path]
                    for case_id in landing_recipe_matches[recipe]) - set(matched_by_path[path]))
                stale_by_path[path] = (
                    f"{path} (covered on the base branch by "
                    f"{', '.join('case:' + item for item in owed) or 'a case'}, "
                    "not by this change)")
            else:
                (debt if ratchet else uncovered).append(path)
        selected.extend(matched_by_path[path])
    for recipe in recipes:
        matched = recipe_matches[recipe]
        if demand and not matched:
            recipe_paths = [item["path"] for item in surfaces]
            if landing_recipe_matches.get(recipe):
                # Only the surfaces that actually reach this recipe. Charging every
                # affected path was harmless while the target was `debt` — a superset
                # of an exit-0 count — and is not once the target is fatal: it would
                # name a recipe an unrelated path has nothing to do with, and take
                # that path out of `coverage_debt`, which is the number CI publishes.
                # Read off the landing graph, not the branch's: the sentence being
                # printed is a claim about what the base branch covers, so "reaches
                # this recipe" has to mean what it means there.
                reaching = [path for path in recipe_paths
                            if recipe in landing_by_surface.get(path, ())]
                for path in reaching or [f"recipe:{recipe}"]:
                    stale_by_path.setdefault(
                        path,
                        f"{path} (recipe:{recipe} is covered on the base branch by "
                        f"{', '.join('case:' + item for item in landing_recipe_matches[recipe])}, "
                        "not by this change)")
            else:
                (debt if ratchet else uncovered).extend(recipe_paths or [f"recipe:{recipe}"])
        selected.extend(matched)
    selected = sorted(set(selected))
    stale = [stale_by_path[path] for path in sorted(stale_by_path)]
    debt = sorted(set(debt) - set(uncovered) - set(stale_by_path))
    # A question that could not be answered is an accusation rather than a shrug,
    # the stance `_evidence_ratchet_failures` takes and for the same reason: with
    # the fork point gone as the reference, this comparison is the only thing that
    # notices a branch forked from before a case existed. `_regressions` keeps the
    # softer stance for its own `None` because it is one guard among several.
    coverage_unreadable = ratchet and (landing_coverage is None or landing_graph is None)
    regressions = (_regressions(base_coverage, landing_coverage)
                   if landing_coverage is not None else [])
    # The registry is monotonic too, in both modes. Widening what the gate can see
    # is the direction it is meant to move; narrowing it is coverage going down.
    registry_changed = REGISTRY_REL in changed
    base_registry = _registry_at(root, base) if registry_changed else None
    registry_narrowings = (
        _registry_narrowings(base_registry,
                             _landing_registry(prompt_surface_registry(), base_registry,
                                               _registry_at(root, merge_base)))
        if registry_changed else []
    )
    evidence: dict[str, str] = {}
    if evidence_dir is not None:
        evidence_root = pathlib.Path(evidence_dir)
        for case_id in selected:
            found = False
            if evidence_root.is_dir():
                for path in evidence_root.rglob("*.json"):
                    try:
                        value: Any = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        continue
                    if isinstance(value, dict) and value.get("case_id") == case_id:
                        found = True
                        break
            evidence[case_id] = "present" if found else "absent"
    if uncovered or regressions or registry_narrowings or stale or coverage_unreadable:
        status = "uncovered"
    elif not surfaces:
        status = "noop"
    elif debt:
        # Deliberately its own status rather than folded into `pass`: the run is
        # allowed to proceed, and the number is still reported so paying it down is
        # visible progress instead of a silence that looks like coverage.
        status = "debt"
    else:
        status = "pass"
    return {
        "eval_affected_schema_version": 2,
        "registry_version": REGISTRY_VERSION,
        # Reported rather than inferred from `changed_files`: a reader checking why
        # the gate's field of view moved should not have to know the registry's path.
        "registry_changed": registry_changed,
        "registry_narrowings": registry_narrowings,
        "base": base, "head": head, "resolved_head": resolved_head,
        # The fork point the *diff* used — what this branch changes is measured from
        # here, while what it has to still cover is measured against `base`. Printed
        # so a surprising result can be checked against it instead of guessed at.
        "merge_base": merge_base,
        "changed_files": changed,
        "affected_surfaces": sorted(surfaces, key=lambda item: item["path"]),
        "affected_recipes": recipes, "affected_cases": selected,
        "uncovered": sorted(set(uncovered)),
        "coverage_debt": debt,
        "coverage_regressions": regressions,
        # Surfaces the base branch already has a case for and this change does not:
        # behind, not undocumented. Its own list rather than folded into `uncovered`
        # so the report says which of the two it is, and what clears it.
        "coverage_stale": stale,
        "coverage_base_unreadable": coverage_unreadable,
        "evidence_status": evidence,
        "surface_commits": _surface_commits(root, merge_base, head,
                                            [*uncovered, *debt, *sorted(stale_by_path)]),
        "status": status,
    }
