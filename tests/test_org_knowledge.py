"""The promotion lifecycle a supported knowledge candidate goes through (#440, stage 2).

`knowledge_candidate.assess` says a supported candidate is still not approved and not
organizational knowledge. These tests are about the distance between those two: the path
is one step at a time, approval is a named human act, conflicts are presented rather than
resolved, the ledger only grows, and instincts are a different layer this never touches.
"""

import ast
import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench import org_knowledge as ok
from rig_workbench.workbench.knowledge_candidate import CANDIDATE_SCHEMA, EVIDENCE_SCHEMA

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = ROOT / "scripts" / "workbench.py"


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _supported_candidate(root: pathlib.Path, *, rule="run focused tests before review",
                         scope=("bugfix",), name="candidate.json") -> pathlib.Path:
    _write(root / "evidence.json", {
        "schema": EVIDENCE_SCHEMA,
        "records": [{
            "id": "run-1",
            "observation": "review failed after two repair cycles",
            "applicable_context": ["python", "review-gate"],
            "proposed_rules": [rule],
            "observed_benefits": ["fewer late review failures"],
            "known_exceptions": ["documentation-only changes"],
            "scope": sorted(set(scope) | {"feature"}),
        }],
    })
    path = root / name
    _write(path, {
        "schema": CANDIDATE_SCHEMA,
        "triggering_evidence": [{"path": "evidence.json", "record": "run-1"}],
        "applicable_context": ["python"],
        "proposed_rule": rule,
        "expected_benefit": "fewer late review failures",
        "confidence": 0.7,
        "evidence_count": 1,
        "known_exceptions": ["documentation-only changes"],
        "scope": list(scope),
    })
    return path


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".rig").mkdir()
    return tmp_path


# ── the path ─────────────────────────────────────────────────────────────────
def test_only_a_supported_candidate_enters_and_it_enters_as_candidate(repo):
    record = ok.register(repo, _supported_candidate(repo))
    assert record["state"] == ok.CANDIDATE
    assert record["assessment"]["claimed_confidence"] == 0.7
    assert record["assessment"]["verified_confidence"] is None

    unsupported = _supported_candidate(repo, name="wide.json")
    doc = json.loads(unsupported.read_text())
    doc["scope"] = ["bugfix", "release"]            # wider than the record allows
    unsupported.write_text(json.dumps(doc))
    with pytest.raises(ok.OrgKnowledgeError, match="unsupported, not supported"):
        ok.register(repo, unsupported)


def test_the_path_is_walked_one_step_at_a_time_and_a_refusal_names_the_next_steps(repo):
    record = ok.register(repo, _supported_candidate(repo))
    rid = record["id"]
    with pytest.raises(ok.OrgKnowledgeError, match="can only move to: evaluated"):
        ok.promote(repo, rid, ok.ACTIVE, actor="a", reason="r")
    assert ok.promote(repo, rid, ok.EVALUATED)["state"] == ok.EVALUATED
    assert ok.promote(repo, rid, ok.APPROVED, actor="alice", reason="reviewed")["state"] == ok.APPROVED
    assert ok.promote(repo, rid, ok.ACTIVE, actor="alice", reason="rolling out")["state"] == ok.ACTIVE
    assert ok.promote(repo, rid, ok.DEPRECATED, actor="alice", reason="superseded")["state"] == ok.DEPRECATED
    with pytest.raises(ok.OrgKnowledgeError, match="terminal state"):
        ok.promote(repo, rid, ok.ACTIVE, actor="alice", reason="again")


def test_approval_is_a_named_human_act_and_a_model_may_only_reach_evaluated(repo):
    rid = ok.register(repo, _supported_candidate(repo))["id"]
    ok.promote(repo, rid, ok.EVALUATED)                      # no name needed
    for actor, reason in ((None, "r"), ("a", None), ("  ", "r"), ("a", "")):
        with pytest.raises(ok.OrgKnowledgeError, match="named human act"):
            ok.promote(repo, rid, ok.APPROVED, actor=actor, reason=reason)
    assert ok.replay(repo)[rid]["state"] == ok.EVALUATED


def test_rollback_leaves_the_promotion_in_the_record(repo):
    rid = ok.register(repo, _supported_candidate(repo))["id"]
    ok.promote(repo, rid, ok.EVALUATED)
    ok.promote(repo, rid, ok.APPROVED, actor="alice", reason="ok")
    ok.promote(repo, rid, ok.ACTIVE, actor="alice", reason="ship")
    ok.promote(repo, rid, ok.ROLLED_BACK, actor="bob", reason="regression in prod")
    steps = [h["to"] for h in ok.history(repo, rid)["history"]]
    assert steps == [ok.CANDIDATE, ok.EVALUATED, ok.APPROVED, ok.ACTIVE, ok.ROLLED_BACK]
    assert ok.active_rules(repo) == []


# ── conflicts are presented, not resolved ────────────────────────────────────
def test_the_same_rule_active_in_an_overlapping_scope_blocks_a_second_activation(repo):
    first = ok.register(repo, _supported_candidate(repo, name="a.json"))["id"]
    second = ok.register(repo, _supported_candidate(repo, scope=("bugfix", "feature"),
                                                    name="b.json"))["id"]
    for rid in (first, second):
        ok.promote(repo, rid, ok.EVALUATED)
        ok.promote(repo, rid, ok.APPROVED, actor="alice", reason="ok")
    ok.promote(repo, first, ok.ACTIVE, actor="alice", reason="ship")
    with pytest.raises(ok.OrgKnowledgeError) as refused:
        ok.promote(repo, second, ok.ACTIVE, actor="alice", reason="ship")
    assert first in str(refused.value) and "Deprecate" in str(refused.value)
    # The way through is deliberate, and only then does the second become active.
    ok.promote(repo, first, ok.DEPRECATED, actor="alice", reason="replaced by " + second)
    assert ok.promote(repo, second, ok.ACTIVE, actor="alice", reason="ship")["state"] == ok.ACTIVE


def test_a_different_rule_or_a_disjoint_scope_is_not_a_conflict(repo):
    a = ok.register(repo, _supported_candidate(repo, name="a.json"))["id"]
    b = ok.register(repo, _supported_candidate(repo, scope=("feature",), name="b.json"))["id"]
    for rid in (a, b):
        ok.promote(repo, rid, ok.EVALUATED)
        ok.promote(repo, rid, ok.APPROVED, actor="alice", reason="ok")
        ok.promote(repo, rid, ok.ACTIVE, actor="alice", reason="ship")
    assert {r["id"] for r in ok.active_rules(repo)} == {a, b}


# ── the ledger only grows ────────────────────────────────────────────────────
def test_the_ledger_is_append_only_and_state_is_derived_from_it(repo):
    rid = ok.register(repo, _supported_candidate(repo))["id"]
    before = ok.ledger_path(repo).read_text()
    ok.promote(repo, rid, ok.EVALUATED)
    after = ok.ledger_path(repo).read_text()
    assert after.startswith(before) and after.count("\n") == before.count("\n") + 1
    assert ok.listing(repo)[0]["state"] == ok.EVALUATED


def test_a_tampered_ledger_is_refused_rather_than_read_around(repo):
    rid = ok.register(repo, _supported_candidate(repo))["id"]
    path = ok.ledger_path(repo)
    path.write_text(path.read_text() + '{"schema": "other", "event": "transition"}\n')
    with pytest.raises(ok.OrgKnowledgeError, match="not a rig.org-knowledge/v1 event"):
        ok.replay(repo)
    assert rid  # the id existed; the ledger as a whole is what is refused


# ── a separate layer from instincts ──────────────────────────────────────────
def test_the_module_never_touches_the_instinct_store():
    """Inspected as code, not grepped: the docstring names instincts to say why it stays
    away from them, so a text search would flag the explanation."""
    source = (ROOT / "rig_workbench" / "workbench" / "org_knowledge.py").read_text()
    tree = ast.parse(source)
    strings = [node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)
               and not (isinstance(node, ast.Constant) and node.value.strip().startswith(("The", "`", "A ", "\n")))]
    assert not any("instinct" in s for s in strings if len(s) < 200), strings
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                for alias in node.names} | {node.module for node in ast.walk(tree)
                                             if isinstance(node, ast.ImportFrom) and node.module}
    assert not any("instinct" in name for name in imported)


# ── the CLI routes it, and the assessment path is untouched ──────────────────
def test_the_subcommand_registers_promotes_lists_and_narrates(repo, monkeypatch):
    candidate = _supported_candidate(repo)
    env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}

    def run(*argv):
        return subprocess.run([sys.executable, str(WORKBENCH), "knowledge-candidate", *argv],
                              cwd=repo, capture_output=True, text=True, env=env)

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    assess = run(str(candidate))
    assert assess.returncode == 0 and "supported" in assess.stdout
    registered = run(str(candidate), "--register", "--json")
    assert registered.returncode == 0, registered.stderr
    rid = json.loads(registered.stdout)["id"]
    refused = run("--promote", rid, "--to", "approved")
    assert refused.returncode == 1 and "can only move to: evaluated" in refused.stderr
    assert run("--promote", rid, "--to", "evaluated").returncode == 0
    listing = run("--list", "--json")
    assert [row["state"] for row in json.loads(listing.stdout)] == ["evaluated"]
    story = run("--history", rid)
    assert "→ evaluated" in story.stdout
    assert run("--active").stdout.strip() == "no active organizational knowledge"
    bare = run()
    assert bare.returncode == 1 and "candidate path is required" in bare.stderr
