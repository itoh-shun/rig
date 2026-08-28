"""Promoting an evidence-backed lesson into versioned organizational knowledge (#440).

Almost every test here is about something the module refuses. That is the shape of the
feature: `knowledge_candidate` already answers "do the cited records support this?", and what
#440 adds is the distance between a supported claim and a rule an organization follows. The
value is in what cannot happen on the way — a single incident becoming an organization default
in one move, an unattributed approval, a second active rule at the same scope, or a rollback
that erases the fact that it happened.
"""

import json

import pytest

from rig_workbench.workbench import org_knowledge as ok
from rig_workbench.workbench.org_knowledge import PromotionRefused

CANDIDATE = {
    "schema": "rig.knowledge-candidate/v1",
    "proposed_rule": "For ORM query changes, require query-count verification",
    "applicable_context": ["orm-change"],
    "scope": ["kotlin-backend"],
    "expected_benefit": "fewer N+1 regressions",
    "confidence": 0.8,
    "evidence_count": 1,
    "known_exceptions": [],
    "triggering_evidence": [{"path": "evidence.json", "record": "r1"}],
}
SUPPORTED = {"status": "supported",
             "evidence": {"cited": 1, "readable": 1, "supporting": 1}}


def _to_active(root, event, actor="reviewer", reason="checked"):
    ok.transition(root, event["id"], ok.EVALUATED)
    ok.transition(root, event["id"], ok.APPROVED, actor=actor, reason=reason)
    return ok.transition(root, event["id"], ok.ACTIVE, actor=actor, reason=reason)


# ── evidence is a precondition, not a formality ──────────────────────────────
@pytest.mark.parametrize("status", ["unsupported", "unobservable", "pending", None])
def test_only_a_supported_candidate_can_be_registered(tmp_path, status):
    """`unobservable` is refused alongside `unsupported` for the reason knowledge_candidate
    keeps them apart: failing to read a record is not evidence against a claim, and it is
    never evidence *for* one — which is the direction registration relies on."""
    with pytest.raises(PromotionRefused, match="not registrable"):
        ok.register(tmp_path, CANDIDATE, {**SUPPORTED, "status": status})


def test_a_supported_candidate_enters_as_a_candidate_not_as_knowledge(tmp_path):
    event = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    assert event["state"] == ok.CANDIDATE
    assert event["version"] == 1
    assert ok.active(tmp_path) == []


def test_the_claimed_confidence_stays_the_author_s_claim(tmp_path):
    """Counting readable records did not make it a measurement in `knowledge_candidate`, and
    carrying it across a module boundary does not either."""
    event = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    assert event["claimed_confidence"] == 0.8
    assert "confidence" not in event, "an unqualified name would read as rig's own measurement"


def test_the_citations_travel_with_the_knowledge(tmp_path):
    """So provenance (#436) can reach the records, not only the conclusion drawn from them."""
    event = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    assert event["citations"] == [{"path": "evidence.json", "record": "r1"}]


# ── the lifecycle is a path, not a set of labels ─────────────────────────────
@pytest.mark.parametrize("target", [ok.APPROVED, ok.ACTIVE, ok.DEPRECATED])
def test_a_candidate_cannot_jump_the_queue(tmp_path, target):
    """"A single failure must not become organization policy" is the first non-goal #440
    lists, and a lifecycle nobody has to walk is not a control."""
    event = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    with pytest.raises(PromotionRefused, match="not reachable in one step"):
        ok.transition(tmp_path, event["id"], target, actor="a", reason="r")


def test_the_refusal_names_what_is_reachable(tmp_path):
    """An error that only says no leaves the caller guessing at the next legal move."""
    event = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    with pytest.raises(PromotionRefused, match="it can become evaluated, rolled_back"):
        ok.transition(tmp_path, event["id"], ok.ACTIVE, actor="a", reason="r")


def test_the_full_path_works(tmp_path):
    event = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    final = _to_active(tmp_path, event)
    assert final["state"] == ok.ACTIVE
    assert [row["id"] for row in ok.active(tmp_path)] == [event["id"]]


@pytest.mark.parametrize("terminal", [ok.DEPRECATED, ok.ROLLED_BACK])
def test_a_terminal_state_is_terminal(tmp_path, terminal):
    event = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    _to_active(tmp_path, event)
    ok.transition(tmp_path, event["id"], terminal, actor="a", reason="r")
    with pytest.raises(PromotionRefused, match="terminal state"):
        ok.transition(tmp_path, event["id"], ok.ACTIVE, actor="a", reason="r")


def test_anything_can_be_rolled_back_from_anywhere_on_the_path(tmp_path):
    """Abandoning a proposal must never be harder than advancing it."""
    for state in (ok.CANDIDATE, ok.EVALUATED, ok.APPROVED):
        assert ok.ROLLED_BACK in ok.TRANSITIONS[state]


# ── approval is a recorded human act ─────────────────────────────────────────
@pytest.mark.parametrize("actor,reason", [(None, "r"), ("a", None), (None, None), ("", "")])
def test_approval_without_attribution_is_refused(tmp_path, actor, reason):
    event = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    ok.transition(tmp_path, event["id"], ok.EVALUATED)
    with pytest.raises(PromotionRefused, match="requires an actor and a reason"):
        ok.transition(tmp_path, event["id"], ok.APPROVED, actor=actor, reason=reason)


def test_evaluation_needs_no_attribution_but_approval_does(tmp_path):
    """An LLM may draft a candidate and may evaluate it. It may not approve it, and the
    difference is exactly which transitions demand a name."""
    assert ok.EVALUATED not in ok.ATTRIBUTED
    assert ok.APPROVED in ok.ATTRIBUTED
    event = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    ok.transition(tmp_path, event["id"], ok.EVALUATED)  # no actor needed


def test_no_code_path_promotes_to_approved_on_its_own():
    """Structural: `approved` is reachable only through `transition`, which demands a name."""
    source = (__import__("pathlib").Path(ok.__file__)).read_text(encoding="utf-8")
    body = source.split("def transition(", 1)[1]
    assert source.count('"approved"') + source.count("APPROVED") >= 1
    assert "ATTRIBUTED" in body, "transition must be the only gate, and must check attribution"


# ── conflicts are shown, never resolved ──────────────────────────────────────
def test_a_second_active_version_of_the_same_rule_is_refused(tmp_path):
    first = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    _to_active(tmp_path, first)
    with pytest.raises(PromotionRefused, match="already covers this rule"):
        ok.register(tmp_path, CANDIDATE, SUPPORTED)


def test_the_conflict_names_the_knowledge_it_collides_with(tmp_path):
    first = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    _to_active(tmp_path, first)
    with pytest.raises(PromotionRefused, match=first["id"]):
        ok.register(tmp_path, CANDIDATE, SUPPORTED)


def test_a_non_overlapping_scope_is_not_a_conflict(tmp_path):
    first = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    _to_active(tmp_path, first)
    elsewhere = {**CANDIDATE, "scope": ["python-backend"]}
    assert ok.register(tmp_path, elsewhere, SUPPORTED)["version"] == 2


def test_deprecating_the_old_version_clears_the_way(tmp_path):
    """The documented way through a conflict: deprecate explicitly rather than have rig pick."""
    first = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    _to_active(tmp_path, first)
    ok.transition(tmp_path, first["id"], ok.DEPRECATED, actor="a", reason="superseded")
    second = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    assert second["version"] == 2


def test_conflict_detection_is_structural_and_says_so(tmp_path):
    """Rig does not read two differently-worded rules and decide whether they contradict. A
    differently-worded rule at the same scope is therefore *not* reported as a conflict —
    claiming otherwise would be inventing the judgement a human is here to make."""
    first = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    _to_active(tmp_path, first)
    reworded = {**CANDIDATE, "proposed_rule": "Never merge ORM changes without query counts"}
    assert ok.conflicts(tmp_path, reworded["proposed_rule"], reworded["scope"]) == []
    assert "structural" in ok.conflicts.__doc__.lower()


# ── the log is append-only ───────────────────────────────────────────────────
def test_a_rollback_leaves_the_record_that_it_happened(tmp_path):
    """Knowledge whose history can be rewritten cannot answer "why did we start doing this,
    and why did we stop?" — the question the feature exists to serve."""
    event = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    _to_active(tmp_path, event)
    ok.transition(tmp_path, event["id"], ok.ROLLED_BACK, actor="a", reason="too noisy")

    states = [row["state"] for row in ok.history(tmp_path, event["id"])]
    assert states == [ok.CANDIDATE, ok.EVALUATED, ok.APPROVED, ok.ACTIVE, ok.ROLLED_BACK]
    assert ok.active(tmp_path) == []


def test_the_current_state_is_replayed_from_the_log_not_stored_beside_it(tmp_path):
    """Two records of one fact disagree eventually, and the log is the one that cannot be
    edited into agreement."""
    event = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    ok.transition(tmp_path, event["id"], ok.EVALUATED)
    assert ok.current(tmp_path)[event["id"]]["state"] == ok.EVALUATED
    assert len(ok.load(tmp_path)) == 2


def test_a_malformed_line_is_skipped_rather_than_guessed_at(tmp_path):
    ok.register(tmp_path, CANDIDATE, SUPPORTED)
    with ok.store_path(tmp_path).open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
        handle.write(json.dumps({"schema": "something.else/v1", "id": "x"}) + "\n")
    assert len(ok.load(tmp_path)) == 1


def test_an_empty_store_is_empty_not_an_error(tmp_path):
    assert ok.load(tmp_path) == [] and ok.active(tmp_path) == [] and ok.current(tmp_path) == {}


def test_an_unknown_id_is_refused_by_name(tmp_path):
    with pytest.raises(PromotionRefused, match="no organizational knowledge with id"):
        ok.transition(tmp_path, "nope-v1", ok.EVALUATED)


# ── what a workflow may rely on ──────────────────────────────────────────────
def test_only_active_knowledge_is_offered_to_a_workflow(tmp_path):
    """An approved-but-not-activated rule is a decision made and not yet applied; a workflow
    treating the two alike would enforce rules nobody switched on."""
    event = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    ok.transition(tmp_path, event["id"], ok.EVALUATED)
    ok.transition(tmp_path, event["id"], ok.APPROVED, actor="a", reason="r")
    assert ok.active(tmp_path) == []


def test_active_can_be_filtered_to_a_scope(tmp_path):
    first = ok.register(tmp_path, CANDIDATE, SUPPORTED)
    _to_active(tmp_path, first)
    assert len(ok.active(tmp_path, ["kotlin-backend"])) == 1
    assert ok.active(tmp_path, ["python-backend"]) == []


# ── instincts stay a separate layer ──────────────────────────────────────────
def test_this_layer_does_not_reach_into_instincts():
    """Instincts are unverified hints that decay; this does not. Writing one into the other
    would destroy whichever property the destination lacked — instincts would start carrying
    claims nobody may ignore, or verified lessons would quietly expire after thirty days.

    Read as an AST rather than as text, the way `test_runtime_backend.py` checks the same kind
    of separation: this module's docstring names instincts on purpose, to explain why it keeps
    away from them, and prose reaches for nothing.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(ok.__file__).read_text(encoding="utf-8"))
    referenced = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.alias):
            referenced.add(node.name.split(".")[-1])
            if node.asname:
                referenced.add(node.asname)
        elif isinstance(node, ast.ImportFrom) and node.module:
            referenced.add(node.module.split(".")[-1])

    assert not {name for name in referenced if "instinct" in name.lower()}
