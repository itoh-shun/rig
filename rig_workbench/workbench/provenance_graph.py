"""Why a change exists, what supports it, and what happened after it shipped (#436).

Git records what changed. A receipt records what a task achieved. Neither records why the
change was wanted, which requirement it was answering, or how it behaved in production, and a
reader reconstructing that from six files is reconstructing it differently each time.

**It does not infer.** Deciding that this commit implements that requirement is reading two
things and concluding a third — an agent's work, and a module that called a model to do it
would leave nothing a gate could check and nothing a mutation could falsify. Edges arrive
already drawn, and what lives here is the schema, the separation, and the refusals.

**A guess and an observation are not the same edge.** The tempting rendering of "an agent
thought so" is an edge that looks exactly like one somebody checked, and a reader following a
chain has no way to tell where it stopped being evidence. So every edge says how it was
established and who established it, an edge that cannot say is refused, and a query returns the
two kinds apart rather than merged.

**And `confirmed` names a kind of thing somebody could go and look at.** An authority is written
as `receipt:…`, `git:…`, `person:…`, `policy:…` or `agent:…`, and an `agent:` authority cannot
be `confirmed` however sure it sounded — a conclusion is not an observation with a different
adjective. What this does *not* do is resolve the reference: whether that receipt exists is a
question for something with the repository in front of it, and the guarantee here is that a
confirmed edge points at a kind of thing that can be resolved rather than at prose.

**It gives a second copy of the verdict nowhere to live.** An edge to evidence names the
receipt and stops. Copying "passed" into the graph would make two places that answer "did this
verify", which drift, and the one that drifts is always the copy — `assurance.py` is the
authority, and this points at it. So the schema defines no field for a verdict and refuses a
document that adds one. What it cannot do is police free text: a node labelled `"passed"`
validates, because a label is prose for a human and nothing here reads it. The guarantee is
that no *field* carries a verdict, which is what a consumer can rely on, and it is worth stating
at that width rather than one wider.

**Invalidation is a fact the graph carries, not a deletion.** Evidence goes stale: the target
moved, the gate was overridden, a later run said otherwise. Removing the edge would leave a
chain that reads as though nothing had ever supported the change, which is a different and
worse claim than "this was supported and then stopped being".
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata

SCHEMA = "rig.provenance-graph/v1"

#: The kinds of thing a node can be. Closed, because a kind nobody defined would be accepted,
#: dropped, and leave a reader believing the graph held something it does not.
GOAL = "goal"
INTENT = "intent"
REQUIREMENT = "requirement"
DECISION = "decision"
TASK = "task"
COMMIT = "commit"
EVIDENCE = "evidence"
APPROVAL = "approval"
DEPLOYMENT = "deployment"
OUTCOME = "outcome"
KINDS = (GOAL, INTENT, REQUIREMENT, DECISION, TASK, COMMIT, EVIDENCE, APPROVAL, DEPLOYMENT,
         OUTCOME)

#: How one node relates to another. Closed for the same reason, and directional: `satisfies`
#: reads one way and means something else read the other.
DERIVED_FROM = "derived-from"
SATISFIES = "satisfies"
IMPLEMENTS = "implements"
VERIFIED_BY = "verified-by"
APPROVED_BY = "approved-by"
DEPLOYED_AS = "deployed-as"
MEASURED_BY = "measured-by"
INVALIDATES = "invalidates"
RELATIONS = (DERIVED_FROM, SATISFIES, IMPLEMENTS, VERIFIED_BY, APPROVED_BY, DEPLOYED_AS,
             MEASURED_BY, INVALIDATES)

#: How an edge came to be drawn. `CONFIRMED` means something checked it — a receipt, a git
#: object, a person. `INFERRED` means somebody concluded it, which is worth recording and is
#: not the same claim.
CONFIRMED, INFERRED = "confirmed", "inferred"
BASES = (CONFIRMED, INFERRED)

#: The kinds of thing an authority can be, written as a prefix: `receipt:rig-…`, `git:<oid>`,
#: `person:someone`, `agent:planner`. Closed, and split by what each kind can support — a
#: `confirmed` edge may name only a kind somebody could go and resolve, and an agent's
#: conclusion is not one of those however sure it sounded.
RESOLVABLE_AUTHORITIES = ("receipt", "git", "person", "policy")
DECLARED_AUTHORITIES = ("agent",)
AUTHORITY_KINDS = RESOLVABLE_AUTHORITIES + DECLARED_AUTHORITIES

#: A git object id: 40 hex for sha-1, 64 for sha-256. A `git:` authority is an object and not
#: a revision expression — `git:HEAD` and `git:main` resolve now and name something else later,
#: which is the opposite of what an authority in a provenance record is for.
OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def _is_name(value: object) -> bool:
    """A name: a non-blank string, exactly itself trimmed, with nothing that draws a line.

    The control characters matter more here than in most schemas. The report writes one line
    per edge with its basis on it, so an authority containing a newline writes a second line
    that says whatever it likes — and a reader scanning for `[confirmed]` finds one. That
    defeats the distinction this module exists to draw, using nothing but the renderer.

    The rule is by Unicode category rather than by a list of characters, and it is a superset
    of `injection.INVISIBLE_RE` — checked by a test rather than assumed. Calling that pattern
    here as well would be a second check that can only agree, which reads as two protections
    and is one.
    """
    if not (isinstance(value, str) and bool(value.strip()) and value == value.strip()):
        return False
    # `Zs` other than a plain space too: a name holding a no-break space looks identical to
    # one holding a space, and two names that look identical are two names to a comparison and
    # one to a reader.
    return not any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp")
                   or (unicodedata.category(ch) == "Zs" and ch != " ")
                   for ch in value)


#: Fields an edge may never carry. A `status` or a `verdict` here would be a second place that
#: answers "did this verify", and the copy is the one that goes stale.
_FORBIDDEN_EDGE_KEYS = ("status", "verdict", "passed", "result", "outcome", "gate",
                        "final_status")


def node_problems(node_id, kind, label, where: str = "node") -> list[str]:
    """Everything wrong with one node, wherever it came from."""
    problems: list[str] = []
    if not _is_name(node_id):
        problems.append(f"{where}: id {node_id!r} has to name something, exactly")
    if kind not in KINDS:
        problems.append(f"{where}: kind {kind!r} is not one of {', '.join(KINDS)}")
    if not _is_name(label):
        problems.append(
            f"{where}: label {label!r} has to say what this is. A graph of identifiers nobody "
            f"can read is a graph nobody follows")
    return problems


def edge_problems(source, target, relation, basis, authority, where: str = "edge") -> list[str]:
    """Everything wrong with one edge, wherever it came from.

    One function rather than a rule in `validate` and a hope on the programmatic path — the
    two modules before this one took four review rounds each to learn that a check on one
    ingestion path is a check on one ingestion path.

    What this cannot hold is anything about the *graph*: whether an id is unique, whether an
    endpoint exists. An `Edge` does not know what else is in the document, so those live in
    `validate`, and every path that has a graph goes through it.
    """
    problems: list[str] = []
    for name, value in (("source", source), ("target", target)):
        if not _is_name(value):
            problems.append(f"{where}: {name} {value!r} has to name a node, exactly")
    if relation not in RELATIONS:
        problems.append(f"{where}: relation {relation!r} is not one of {', '.join(RELATIONS)}")
    if basis not in BASES:
        problems.append(
            f"{where}: basis {basis!r} is not one of {', '.join(BASES)}. An edge that cannot "
            f"say whether anybody checked it reads exactly like one somebody did")
    if not _is_name(authority):
        problems.append(
            f"{where}: authority {authority!r} has to name what established this — a receipt, "
            f"a git object, a person. 'Something concluded it' is not a source")
    else:
        kind, _, rest = authority.partition(":")
        if kind not in AUTHORITY_KINDS or not rest.strip():
            problems.append(
                f"{where}: authority {authority!r} does not say what kind of thing it is. "
                f"Write it as one of {', '.join(k + ':…' for k in AUTHORITY_KINDS)}, so a "
                f"reader can tell what they would have to go and look at")
        elif basis == CONFIRMED and kind in DECLARED_AUTHORITIES:
            problems.append(
                f"{where}: {authority!r} is {CONFIRMED!r}, but {kind}: is something that "
                f"concluded rather than something that checked. An agent that was sure is "
                f"still an agent that was sure — write {INFERRED!r}")
    if source == target and _is_name(source):
        problems.append(
            f"{where}: {source!r} relates to itself. A chain that returns to where it started "
            f"explains nothing, and every traversal here would have to guard against it")
    return problems


@dataclasses.dataclass(frozen=True)
class Node:
    """One thing the graph knows about."""

    id: str
    kind: str
    label: str

    def __post_init__(self) -> None:
        problems = node_problems(self.id, self.kind, self.label)
        if problems:
            raise ValueError("\n  ".join(problems))

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label}


@dataclasses.dataclass(frozen=True)
class Edge:
    """One relationship, and how anybody knows it holds.

    `basis` and `authority` are required together and neither has a default: an edge that
    cannot say how it was established looks exactly like one somebody checked, which is the
    whole failure this module exists to prevent.

    There is no field for a verdict. An edge to evidence names the receipt and stops, because
    two places answering "did this verify" is one place too many and the copy is the one that
    goes stale.
    """

    source: str
    target: str
    relation: str
    basis: str
    authority: str

    def __post_init__(self) -> None:
        problems = edge_problems(self.source, self.target, self.relation, self.basis,
                                 self.authority)
        if problems:
            raise ValueError("\n  ".join(problems))

    def as_dict(self) -> dict:
        return {"source": self.source, "target": self.target, "relation": self.relation,
                "basis": self.basis, "authority": self.authority}


NODE_FIELDS = frozenset(f.name for f in dataclasses.fields(Node))
EDGE_FIELDS = frozenset(f.name for f in dataclasses.fields(Edge))
DOCUMENT_KEYS = frozenset({"schema", "nodes", "edges"})


def validate(payload: dict) -> list[str]:
    """Every way this is not a provenance graph, not the first one."""
    problems: list[str] = []
    if not isinstance(payload, dict):
        return [f"graph: expected an object, got {type(payload).__name__}"]

    unresolvable = [k for k in payload if not isinstance(k, str)]
    if unresolvable:
        # `sorted` on mixed key types raises, and `validate` promises a list of problems. JSON
        # cannot produce these; a caller building the dict can, and that is the other
        # ingestion path this module is supposed to answer the same way.
        problems.append(
            f"graph: {', '.join(repr(k) for k in unresolvable)} is not a key a document can "
            f"have")
    unknown_root = sorted(set(payload) - DOCUMENT_KEYS - set(unresolvable), key=str)
    if unknown_root:
        problems.append(
            f"graph: {', '.join(repr(k) for k in unknown_root)} is not part of {SCHEMA}. A key "
            f"this schema does not define would be dropped rather than honoured")
    if payload.get("schema") != SCHEMA:
        problems.append(f"schema: expected {SCHEMA!r}, got {payload.get('schema')!r}")

    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        problems.append("nodes: expected a list")
        nodes = []
    elif not nodes:
        problems.append("nodes: a graph of nothing relates nothing")

    known: set = set()
    for position, item in enumerate(nodes):
        where = f"nodes[{position}]"
        if not isinstance(item, dict):
            problems.append(f"{where}: expected an object, got {type(item).__name__}")
            continue
        unreadable = [k for k in item if not isinstance(k, str)]
        if unreadable:
            problems.append(
                f"{where}: {', '.join(repr(k) for k in unreadable)} is not a key a node can "
                f"have")
        unknown = sorted(set(item) - NODE_FIELDS - set(unreadable), key=str)
        if unknown:
            problems.append(f"{where}: {', '.join(repr(k) for k in unknown)} is not part of "
                            f"a node")
        node_id = item.get("id")
        if _is_name(node_id):
            if node_id in known:
                problems.append(
                    f"{where}: {node_id!r} appears more than once. Two nodes under one id is "
                    f"two answers to what an edge points at")
            else:
                known.add(node_id)
        problems.extend(node_problems(node_id, item.get("kind"), item.get("label"), where))

    edges = payload.get("edges")
    if not isinstance(edges, list):
        problems.append("edges: expected a list")
        edges = []

    for position, item in enumerate(edges):
        where = f"edges[{position}]"
        if not isinstance(item, dict):
            problems.append(f"{where}: expected an object, got {type(item).__name__}")
            continue
        unreadable = [k for k in item if not isinstance(k, str)]
        if unreadable:
            problems.append(
                f"{where}: {', '.join(repr(k) for k in unreadable)} is not a key an edge can "
                f"have")
        smuggled = sorted(k for k in item
                          if isinstance(k, str) and k.lower() in _FORBIDDEN_EDGE_KEYS)
        if smuggled:
            problems.append(
                f"{where}: {', '.join(repr(k) for k in smuggled)} would put a verdict in the "
                f"graph. An edge to evidence names the receipt and stops; two places answering "
                f"'did this verify' is one too many, and the copy is the one that goes stale")
        unknown = sorted(set(item) - EDGE_FIELDS - set(smuggled) - set(unreadable), key=str)
        if unknown:
            problems.append(f"{where}: {', '.join(repr(k) for k in unknown)} is not part of "
                            f"an edge")
        problems.extend(edge_problems(item.get("source"), item.get("target"),
                                      item.get("relation"), item.get("basis"),
                                      item.get("authority"), where))
        for name in ("source", "target"):
            value = item.get(name)
            if _is_name(value) and value not in known:
                problems.append(
                    f"{where}: {name} {value!r} is not a node in this graph. An edge to "
                    f"nothing is a chain that ends without saying so")
    return problems


def load(payload: dict) -> tuple[tuple[Node, ...], tuple[Edge, ...]]:
    """Build the graph from a validated document. Raises `ValueError` if it is not one."""
    problems = validate(payload)
    if problems:
        raise ValueError("not a provenance graph:\n  " + "\n  ".join(problems))
    return (tuple(Node(**{k: item[k] for k in NODE_FIELDS}) for item in payload["nodes"]),
            tuple(Edge(**{k: item[k] for k in EDGE_FIELDS}) for item in payload["edges"]))


def invalidated(edges: tuple[Edge, ...]) -> dict:
    """The edges some other edge says no longer hold, and which edge says so.

    An `invalidates` edge names the node whose supporting edges went stale. The supporting
    edges are not removed: a chain with the evidence deleted reads as though nothing had ever
    supported the change, which is a different and worse claim than "this was supported and
    then stopped being".

    The invalidating edge is kept with them rather than collapsed to its target, because it
    has a `basis` of its own — somebody may have *concluded* the evidence went stale — and
    dropping that would make a guess about staleness read exactly like a confirmed one, which
    is the rule this module is built on applied to the one place it applies to itself.
    """
    stale: dict = {}
    for statement in edges:
        if statement.relation != INVALIDATES:
            continue
        for edge in edges:
            if edge.relation == INVALIDATES:
                continue
            if edge.source == statement.target or edge.target == statement.target:
                stale.setdefault(edge, []).append(statement)
    return stale


def _answer(resolve, authority: str):
    """What the resolver says, refusing anything that is not one of the three answers.

    Only `False` demotes, so a resolver that accidentally returned `0` or `""` would leave an
    authority confirmed while the result said the authorities had been looked up. A contract
    that only one caller happens to honour is a contract nobody checks.
    """
    verdict = resolve(authority)
    if verdict is not True and verdict is not False and verdict is not None:
        raise ValueError(
            f"a resolver answers True, False or None; {authority!r} got {verdict!r}. Anything "
            f"else would leave an authority confirmed while the answer said it was checked")
    return verdict


#: What became of one authority. `NOT_CHECKED` is the honest third answer: nobody supplied a
#: resolver, or the kind is one this machine cannot look up.
FOUND, MISSING, NOT_CHECKED = "found", "missing", "not-checked"


def _resolution(basis: str, authority: str, resolve) -> str:
    """What became of this authority. `NOT_CHECKED` when nobody looked, or nobody could."""
    if resolve is None or basis != CONFIRMED:
        return NOT_CHECKED
    verdict = _answer(resolve, authority)
    return FOUND if verdict is True else MISSING if verdict is False else NOT_CHECKED


def _reachable(edges: tuple[Edge, ...], start: str, forward: bool, resolve) -> list[tuple]:
    """Every edge on a chain out of `start`, with how well it is reachable and whether it
    stands up.

    Two dimensions, kept apart. The partition answers "can you rely on getting here": a chain
    is as good as its weakest link, so a step that was somebody's conclusion — or one whose
    authority nobody could find — makes everything past it unreliable. `unresolved` answers
    "why not", because "an agent concluded this" and "this names a receipt that is not there"
    are different problems with different fixes, and a reader deciding what to do next needs
    to tell them apart.

    Breadth-first over `(node, path basis)` states and cycle-safe: self-edges are refused at
    validation, but a longer loop is not, and keying the visited set on the node alone would
    expand only whichever route the file listed first. `invalidates` is not walked — it is a
    statement about the graph, not a link somebody would follow.
    """
    seen_states, seen_pairs, ordered = {(start, CONFIRMED, False)}, set(), []
    frontier = [(start, CONFIRMED, False)]
    while frontier:
        node_id, so_far, lost_so_far = frontier.pop(0)
        for edge in edges:
            if edge.relation == INVALIDATES:
                continue
            here, there = (edge.source, edge.target) if forward else (edge.target, edge.source)
            if here != node_id:
                continue
            found = _resolution(edge.basis, edge.authority, resolve)
            missing = found == MISSING
            stands = edge.basis == CONFIRMED and not missing
            through = CONFIRMED if so_far == CONFIRMED and stands else INFERRED
            lost = lost_so_far or missing
            if (edge, through, lost) not in seen_pairs:
                seen_pairs.add((edge, through, lost))
                ordered.append((edge, through, found, lost_so_far))
            if (there, through, lost) not in seen_states:
                seen_states.add((there, through, lost))
                frontier.append((there, through, lost))
    return ordered


def _looked_up(reached: dict) -> bool:
    """Whether every confirmed authority the trace reached was actually looked up.

    `None` from a resolver is not resolution, and reporting it as such is how a graph full of
    `person:` authorities comes back saying it was checked.
    """
    # The partitions, plus the statements that said an edge went stale. The invalidated
    # *edges* are the same walked ones and would be a second look at the same list — but an
    # `invalidates` edge is never walked, so its authority reaches the reader without ever
    # having been counted, and "everything was looked up" would be answered without it.
    seen = [item for way in ("upstream", "downstream") for basis in BASES
            for item in reached[way][basis]]
    seen += [{"edge": said["statement"], "resolution": said["resolution"]}
             for item in reached["invalidated"] for said in item["invalidated_by"]]
    confirmed = [item for item in seen if item["edge"]["basis"] == CONFIRMED]
    return bool(confirmed) and all(item["resolution"] != NOT_CHECKED for item in confirmed)


def trace(payload: dict, node_id: str, direction: str = "both", resolve=None) -> dict:
    """The chain out of this node, kept apart by how anybody knows each step.

    Followed to the end rather than one hop: a commit that implements a requirement that
    satisfies a goal answers "why does this exist" with the goal, and stopping at the
    requirement answers it with a restatement.

    Four lists rather than one, because merging them is the failure this module exists to
    prevent: a reader following a chain has to see where it stopped being something somebody
    checked. The list an edge lands in is about **reaching it from the node asked about**, not
    about the edge alone — an edge somebody checked, sitting past a step somebody concluded, is
    only as reachable as that conclusion. `invalidated` is a fifth, and it is not subtracted from the others — an edge that
    was confirmed and later invalidated is both, and a caller deciding what to trust needs to
    know it was ever there. Each entry there carries what said so, with its own basis.
    """
    if direction not in ("up", "down", "both"):
        raise ValueError(f"direction {direction!r} is not one of up, down, both")
    nodes, edges = load(payload)
    if node_id not in {node.id for node in nodes}:
        raise ValueError(f"{node_id!r} is not a node in this graph")

    stale = invalidated(edges)
    reached: dict = {"upstream": {CONFIRMED: [], INFERRED: []},
                     "downstream": {CONFIRMED: [], INFERRED: []},
                     "invalidated": []}
    walked: list[tuple] = []
    for way, asked, forward in (("upstream", "up", True), ("downstream", "down", False)):
        if direction not in (asked, "both"):
            continue
        for edge, through, found, lost_before in _reachable(edges, node_id, forward=forward,
                                                            resolve=resolve):
            reached[way][through].append({"edge": edge.as_dict(), "resolution": found,
                                          "path_unresolved": lost_before})
            walked.append((edge, through, found, lost_before))

    for edge, through, found, lost_before in walked:
        # `path_basis` here too: an invalidated edge somebody checked, sitting past a step
        # somebody concluded, is no more reachable than that conclusion — and the invalidation
        # section is exactly where a reader is deciding what to stop trusting.
        already = any(item["edge"] == edge.as_dict() and item["path_basis"] == through
                      and item["path_unresolved"] == lost_before
                      for item in reached["invalidated"])
        if edge in stale and not already:
            # Resolution reaches here too. A missing receipt presented as the stale
            # relationship's authority — or as the authority claiming it went stale — is the
            # same unchecked assertion wearing the same `[confirmed]`, in the section where a
            # reader is deciding what to stop trusting.
            reached["invalidated"].append({
                "edge": edge.as_dict(), "path_basis": through,
                "resolution": found, "path_unresolved": lost_before,
                "invalidated_by": [
                    {"statement": statement.as_dict(),
                     "resolution": _resolution(statement.basis, statement.authority,
                                               resolve)}
                    for statement in stale[edge]]})
    return {"schema": SCHEMA, "node": node_id, "direction": direction,
            # Derived from what actually happened rather than from having been handed a
            # resolver: `person:` and `policy:` are kinds this machine cannot look up, so a
            # trace full of them was not checked however willing the caller was.
            "authorities_looked_up": _looked_up(reached), **reached}


def repository_resolver(root):
    """A resolver backed by this checkout: `receipt:` is a run directory, `git:` an object.

    `None` for the kinds it cannot answer — a `person:` cannot be looked up on this machine,
    and a `policy:` lives wherever the policy lives. Saying so is the point: the alternative is
    demoting an authority for being the sort of thing a program cannot check.

    The one function here that touches anything. Everything else takes what it needs as an
    argument, which is what lets a test state a repository rather than need one.
    """
    import pathlib as _pathlib
    import subprocess

    def resolves(authority: str):
        kind, _, rest = authority.partition(":")
        if kind == "receipt":
            # A run name, not a path. `receipt:/etc/thing` makes `pathlib` discard everything
            # before it, and `..` walks out of the store — either way an accessible
            # `task.json` anywhere would confirm an edge that named it.
            if rest != _pathlib.PurePosixPath(rest).name or rest in (".", ".."):
                return False
            runs = (_pathlib.Path(root) / ".rig" / "runs").resolve()
            try:
                candidate = (runs / rest).resolve()
                candidate.relative_to(runs)
            except (OSError, ValueError):
                return False
            # The final component too: `runs/name/task.json -> /accessible/file` keeps the
            # directory inside the store and reads a file outside it, which is the same
            # borrowing one level down.
            # The record itself must be a real file and not a link. The directory check above
            # already keeps the *path* inside the store, so what is left is a link standing in
            # for a record — pointing outside, or at another run's, which is a smaller
            # borrowing and still one.
            # `task.json` is written when a run *starts*; an edge saying a change was
            # verified by a run that is merely underway is not evidence of anything. What
            # marks a run as having produced something is `provenance.json`, and whether that
            # record stands up is a question this repository already answers — asked here
            # rather than answered a second time.
            record = candidate / "provenance.json"
            if not record.is_file() or record.is_symlink():
                return False
            # And it has to be that run's record. A zero-byte file, a JSON list, or another
            # task's `task.json` copied in would otherwise confirm the edge that named this
            # one — "a file exists here" is not "this receipt exists".
            try:
                import json as _json
                stored = _json.loads(record.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
            if not isinstance(stored, dict):
                return False
            body, signature = stored.get("record"), stored.get("signature")
            if not isinstance(body, dict) or not _is_name(signature):
                return False
            if body.get("task_id") != rest:
                return False
            from .state import verify_provenance
            try:
                return verify_provenance(_pathlib.Path(root), body, signature) is True
            except Exception:  # noqa: BLE001 — an unreadable key is not a verification
                return False
        if kind == "git":
            # A leading `-` would be an option rather than an object, and confirmation would
            # then depend on how git parses arguments instead of on whether the object exists.
            if not OBJECT_ID.fullmatch(rest):
                return False
            return subprocess.run(["git", "cat-file", "-e", f"{rest}^{{object}}"],
                                  cwd=str(root), capture_output=True).returncode == 0
        return None
    return resolves


#: What `provenance` returns. `1` is a graph that is not one, or a node nobody recorded: both
#: mean the question cannot be answered. A node that is in the graph and has no relations yet
#: is not one of those — a goal nobody has implemented is a real state, and the answer to it is
#: an empty chain rather than a failure.
TRACED, NOT_TRACEABLE, EXECUTION_ERROR = 0, 1, 2


def cmd_provenance(args) -> "NoReturn":  # noqa: F821
    """Trace a node's provenance, with confirmed and inferred kept apart.

    Exits rather than returns, for the reason `cmd_synthesis` does: the dispatcher calls
    subcommands for their effect and discards what they hand back.
    """
    import json
    import pathlib
    import sys

    from .state import repo_root
    from .synthesis import _no_duplicate_keys

    try:
        payload = json.loads(pathlib.Path(args.graph).read_text(encoding="utf-8"),
                             object_pairs_hook=_no_duplicate_keys("graph"))
        resolve = None if args.no_resolve else repository_resolver(repo_root())
    except Exception as exc:  # noqa: BLE001 — every failure to read is one status
        print(json.dumps({"schema": SCHEMA, "status": "execution-error",
                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(EXECUTION_ERROR)

    try:
        result = trace(payload, args.node, args.direction, resolve)
    except ValueError as exc:
        print(json.dumps({"schema": SCHEMA, "status": "not-traceable",
                          "error": str(exc)}, ensure_ascii=False))
        sys.exit(NOT_TRACEABLE)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"provenance of {result['node']} ({result['direction']}"
              + ("" if result["authorities_looked_up"]
                 else ", some authorities not looked up") + "):")
        for way in ("upstream", "downstream"):
            for basis in BASES:
                for item in result[way][basis]:
                    edge = item["edge"]
                    other = edge["target"] if way == "upstream" else edge["source"]
                    # The basis is on every line, not in a heading somebody scrolls past —
                    # and it is the basis of *reaching this from the node asked about*. When
                    # the edge itself was checked and the path to it was not, the line says
                    # both, because "somebody verified this link" is still worth reading.
                    # Why it is not something you can rely on, when the edge itself says it
                    # was checked: nobody could find what it names, or the way here ran
                    # through somebody's conclusion. Different problems, different fixes.
                    why = ""
                    if basis == INFERRED and edge["basis"] == CONFIRMED:
                        why = (" — names something nobody could find"
                               if item["resolution"] == MISSING
                               else " — reached through an unresolved authority"
                               if item["path_unresolved"]
                               else " — reached through an inferred step")
                    print(f"    {way:<10} [{basis}] {edge['relation']} {other} "
                          f"(per {edge['authority']}){why}")
        for item in result["invalidated"]:
            edge = item["edge"]
            # The stale edge's own basis too: an inferred edge appearing in this section
            # without it would be the one line in the report that does not say how it was
            # established.
            through = ""
            if item["path_basis"] == INFERRED and edge["basis"] == CONFIRMED:
                # The same three-way answer the ordinary lines give. Naming the wrong reason
                # here sends a reader looking for an inference that is not there.
                through = (" — names something nobody could find"
                           if item["resolution"] == MISSING
                           else " — reached through an unresolved authority"
                           if item["path_unresolved"]
                           else " — reached through an inferred step")
            shown = ("unresolved" if item["resolution"] == MISSING
                     else item["path_basis"])
            print(f"    invalidated  [{shown}] {edge['relation']} "
                  f"{edge['source']} → {edge['target']} (per {edge['authority']}){through} "
                  f"— still recorded, no longer holding")
            for said in item["invalidated_by"]:
                # The invalidation has a basis of its own: somebody may have concluded the
                # evidence went stale rather than observed it — and may have named a receipt
                # nobody can find.
                statement = said["statement"]
                basis = ("unresolved" if said["resolution"] == MISSING
                         else statement["basis"])
                print(f"                   per [{basis}] {statement['authority']}")
    sys.exit(TRACED)
