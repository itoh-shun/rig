"""Portable Assurance Receipt — `rig.assurance-receipt/v1` (#428).

The receipt is a projection: it answers "why is this acceptable?" out of records that
already decided it. These tests hold it to being only that — no second judgment, no
absence rendered as success, and no isolation claim stronger than what was enforced.
"""

import json
import pathlib

import pytest

from rig_workbench.eval.cases import ISOLATION_RANK
from rig_workbench.workbench import assurance
from rig_workbench.workbench.state import sign_provenance


def _task_dir(root: pathlib.Path, task_id: str) -> pathlib.Path:
    d = root / ".rig" / "runs" / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write(d: pathlib.Path, name: str, payload: dict) -> None:
    (d / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


@pytest.fixture
def task(tmp_path):
    """A minimal accepted task: worktree-isolated, gate passed, provenance signed."""
    task_id = "rig-20260101-000000-example"
    d = _task_dir(tmp_path, task_id)
    _write(d, "task.json", {
        "task_id": task_id, "input": "do the thing", "task_type": "bugfix",
        "recipe": "bugfix", "base_branch": "master", "base_commit": "a" * 40,
        "branch": f"rig/{task_id}", "worktree_path": str(tmp_path / "wt"),
        "status": "accepted", "actor": "alice",
        "created_at": "2026-01-01T00:00:00+09:00",
        "accepted_at": "2026-01-01T01:00:00+09:00",
    })
    _write(d, "acceptance.json", {
        "task_id": task_id, "task_type": "bugfix", "presets": ["standard", "bugfix"],
        "status": "passed", "checked_at": "2026-01-01T00:59:00+09:00",
        "checks": [
            {"name": "tests_pass_or_explained", "status": "passed", "detail": ""},
            {"name": "no_gate_tampering", "status": "passed", "detail": "reviewed",
             "tamper_override": True, "tamper_findings": ["test_file_modified"]},
        ],
    })
    _write(d, "steps.json", {"steps": [
        {"name": "implement", "status": "passed"},
        {"name": "review-diff", "status": "passed"},
    ], "seeded": True})
    record = {"task_id": task_id, "gate_status": "passed", "forced": False,
              "accepted_at": "2026-01-01T01:00:00+09:00"}
    _write(d, "provenance.json", {"record": record,
                                  "signature": sign_provenance(tmp_path, record),
                                  "algo": "HMAC-SHA256"})
    (d / "diff.md").write_text("# diff\n", encoding="utf-8")
    (d / "risk.md").write_text("# risk\n", encoding="utf-8")
    return tmp_path, task_id


# ---- the envelope -----------------------------------------------------------

def test_receipt_carries_its_own_schema_name(task):
    root, task_id = task
    assert assurance.build_receipt(root, task_id)["schema"] == "rig.assurance-receipt/v1"


def test_every_top_level_section_is_present(task):
    root, task_id = task
    receipt = assurance.build_receipt(root, task_id)
    assert {"task", "target", "producer", "verifier", "isolation", "gates",
            "approvals", "provenance", "evidence", "sources", "intent",
            "final_status"} <= set(receipt)


# ---- absence is not success -------------------------------------------------

def test_a_consumer_ignoring_the_wrapper_cannot_read_absence_as_success(task):
    """The point of the wrapper: unmeasured must not be truthy-and-empty.

    A reader that treats `receipt["producer"]["runtime"]` as a value gets a dict
    saying it was not observed — not "", not 0, not None, and not a default that
    would sit in a report looking like a measurement nobody took.
    """
    root, task_id = task
    receipt = assurance.build_receipt(root, task_id)
    for block in (receipt["producer"]["runtime"], receipt["producer"]["harness"],
                  receipt["verifier"]["identity"]):
        assert block["observed"] is False
        assert block["reason"]
        assert not {"value", "status", "result"} & set(block)
        # Nothing here may be mistaken for a pass by a consumer doing the laziest
        # possible check.
        assert str(block).lower().count("pass") == 0


def test_independence_is_unrecorded_rather_than_claimed(task):
    root, task_id = task
    independence = assurance.build_receipt(root, task_id)["verifier"]["independence"]
    assert independence["verdict"] == "unrecorded"
    assert independence["verdict"] != "independent"
    assert independence["basis"]


def test_an_unlinked_head_commit_is_unobserved_and_not_immutable(task):
    root, task_id = task
    receipt = assurance.build_receipt(root, task_id)
    assert receipt["target"]["head"]["observed"] is False
    assert receipt["target"]["immutable"] is False


# ---- a worktree is not a sandbox --------------------------------------------

def test_isolation_does_not_borrow_the_eval_sandbox_vocabulary(task):
    """`os-enforced` means an OS held the boundary. A worktree did not."""
    root, task_id = task
    isolation = assurance.build_receipt(root, task_id)["isolation"]
    assert isolation["mode"] == "git-worktree"
    assert isolation["mode"] not in ISOLATION_RANK
    assert "os-enforced" not in json.dumps(isolation)


def test_a_no_worktree_task_says_it_wrote_to_the_main_tree(task):
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    data = json.loads((d / "task.json").read_text(encoding="utf-8"))
    data.pop("branch")
    _write(d, "task.json", data)
    assert assurance.build_receipt(root, task_id)["isolation"]["mode"] == "main-tree"


# ---- projection, not judgment ----------------------------------------------

def test_gate_status_is_copied_not_recomputed(task):
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    data = json.loads((d / "acceptance.json").read_text(encoding="utf-8"))
    data["status"] = "failed"          # criteria still all say passed
    _write(d, "acceptance.json", data)
    receipt = assurance.build_receipt(root, task_id)
    # The receipt reports what the gate recorded; it does not look at the criteria
    # and decide the recorded status was wrong.
    assert receipt["gates"]["status"] == "failed"


def test_an_unmapped_status_combination_is_shown_rather_than_rounded(task):
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    data = json.loads((d / "task.json").read_text(encoding="utf-8"))
    data["status"] = "some-future-status"
    _write(d, "task.json", data)
    final = assurance.build_receipt(root, task_id)["final_status"]
    assert final["value"] == "in-progress"
    assert "some-future-status" in final["basis"]
    assert "no mapping" in final["basis"]


def test_accepting_over_a_failed_gate_is_not_called_acceptable(task):
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    data = json.loads((d / "acceptance.json").read_text(encoding="utf-8"))
    data["status"] = "failed"
    _write(d, "acceptance.json", data)
    final = assurance.build_receipt(root, task_id)["final_status"]
    assert final["value"] == "accepted-over-failed-gate"


def test_an_overridden_criterion_is_a_field_not_buried_in_prose(task):
    root, task_id = task
    gates = assurance.build_receipt(root, task_id)["gates"]
    assert gates["overridden"] == ["no_gate_tampering"]
    entry = next(c for c in gates["criteria"] if c["name"] == "no_gate_tampering")
    assert entry["overridden"] is True
    assert entry["overridden_sensor_findings"] == ["test_file_modified"]


# ---- provenance is referenced, not re-signed --------------------------------

def test_the_existing_accept_signature_is_reported_rather_than_replaced(task):
    root, task_id = task
    prov = assurance.build_receipt(root, task_id)["provenance"]
    assert prov["verified"] is True
    assert prov["algorithm"] == "HMAC-SHA256"
    # The receipt itself carries no signature of its own: a second signed record of
    # the same facts is a second thing that can disagree.
    assert "attestation" not in assurance.build_receipt(root, task_id)
    assert "signature" not in assurance.build_receipt(root, task_id)


def test_a_tampered_provenance_record_is_reported_as_not_verifying(task):
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    blob = json.loads((d / "provenance.json").read_text(encoding="utf-8"))
    blob["record"]["gate_status"] = "passed_after_the_fact"
    _write(d, "provenance.json", blob)
    assert assurance.build_receipt(root, task_id)["provenance"]["verified"] is False


# ---- freshness --------------------------------------------------------------

def test_a_receipt_over_untouched_sources_is_fresh(task):
    root, task_id = task
    receipt = assurance.build_receipt(root, task_id)
    assert assurance.verify(root, receipt)["fresh"] is True


def test_changing_a_projected_source_invalidates_the_receipt(task):
    """Content, not mtime — the check has to survive a checkout that rewrites bytes."""
    root, task_id = task
    receipt = assurance.build_receipt(root, task_id)
    d = root / ".rig" / "runs" / task_id
    data = json.loads((d / "acceptance.json").read_text(encoding="utf-8"))
    data["status"] = "failed"
    _write(d, "acceptance.json", data)
    result = assurance.verify(root, receipt)
    assert result["fresh"] is False
    assert result["final_status"] == "invalidated"
    assert any("acceptance.json" in p for p in result["changed"])


def test_rewriting_a_source_with_identical_bytes_stays_fresh(task):
    root, task_id = task
    receipt = assurance.build_receipt(root, task_id)
    p = root / ".rig" / "runs" / task_id / "acceptance.json"
    p.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    assert assurance.verify(root, receipt)["fresh"] is True


def test_a_deleted_evidence_file_invalidates_the_receipt(task):
    root, task_id = task
    receipt = assurance.build_receipt(root, task_id)
    (root / ".rig" / "runs" / task_id / "risk.md").unlink()
    result = assurance.verify(root, receipt)
    assert result["fresh"] is False
    assert any("risk.md" in p for p in result["missing"])


def test_a_receipt_of_an_unknown_schema_is_refused(task):
    root, _ = task
    result = assurance.verify(root, {"schema": "rig.assurance-receipt/v2", "sources": []})
    assert result["fresh"] is False
    assert "v2" in result["reason"]


# ---- one model, two renderings ---------------------------------------------

def test_the_markdown_is_rendered_from_the_receipt_and_not_from_the_files(task):
    """Both outputs must be derivable from the JSON alone, or they can drift."""
    root, task_id = task
    receipt = assurance.build_receipt(root, task_id)
    md = assurance.render_markdown(json.loads(json.dumps(receipt)))
    assert task_id in md
    assert receipt["final_status"]["value"] in md
    assert "unrecorded" in md               # the independence verdict survives rendering
    assert "**overridden**" in md           # so does the override


def test_the_rendering_never_prints_an_empty_value_for_an_observed_block():
    line = assurance._render_value(assurance.observed(unrelated="x"), "missing_key")
    assert line.strip()
    assert "recorded" in line


def test_evidence_references_carry_digests(task):
    root, task_id = task
    for entry in assurance.build_receipt(root, task_id)["evidence"]:
        assert entry["sha256"]
        assert entry["scope"] in ("task", "repository")


def test_repository_evidence_is_absent_when_no_criterion_consults_it(task):
    """`evals/evidence/` belongs to the repo, not to a task that never touched it."""
    root, task_id = task
    (root / "evals" / "evidence" / "some-case").mkdir(parents=True)
    (root / "evals" / "evidence" / "some-case" / "current.json").write_text("{}\n",
                                                                            encoding="utf-8")
    receipt = assurance.build_receipt(root, task_id)
    assert all(e["scope"] == "task" for e in receipt["evidence"])


# ---- producer capture at task creation --------------------------------------

def _new_task(cwd, *extra):
    import subprocess
    import sys
    return subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).resolve().parent.parent / "scripts" / "workbench.py"),
         "new", "fix a thing", "--type", "bugfix", "--no-worktree", *extra],
        capture_output=True, text=True, cwd=cwd, timeout=120)


@pytest.fixture
def fresh_repo(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def _only_task(root):
    runs = sorted((root / ".rig" / "runs").iterdir())
    return json.loads((runs[0] / "task.json").read_text(encoding="utf-8"))


def test_a_declared_caller_is_recorded_as_a_declaration(fresh_repo, monkeypatch):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("RIG_CALLER", raising=False)
    assert _new_task(fresh_repo, "--caller", "acme-harness").returncode == 0
    caller = _only_task(fresh_repo)["caller"]
    assert caller["id"] == "acme-harness"
    assert caller["declared"] is True


def test_an_inferred_caller_is_not_recorded_as_a_declaration(fresh_repo, monkeypatch):
    """The distinction is the whole point: a guess must not read like a statement."""
    monkeypatch.delenv("RIG_CALLER", raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert _new_task(fresh_repo).returncode == 0
    caller = _only_task(fresh_repo)["caller"]
    assert caller["id"] == "claude-code"
    assert caller["declared"] is False
    assert caller["source"] != "flag:--caller"


def test_a_plain_terminal_records_unknown_rather_than_a_guess(fresh_repo, monkeypatch):
    for var in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "RIG_CALLER"):
        monkeypatch.delenv(var, raising=False)
    assert _new_task(fresh_repo).returncode == 0
    assert _only_task(fresh_repo)["caller"]["id"] == "unknown"


# ---- the field names and status vocabularies rig actually uses ---------------

def test_a_recorded_commit_is_read_from_the_field_record_commit_writes(task):
    """`record-commit` writes `commit_sha`. Reading any other name reports a task
    that has a linked commit as one that does not — a false negative that looks
    like modesty."""
    import subprocess
    root, task_id = task
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "f.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "c"], cwd=root, check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                         capture_output=True, text=True, check=True).stdout.strip()
    d = root / ".rig" / "runs" / task_id
    data = json.loads((d / "task.json").read_text(encoding="utf-8"))
    data["commit_sha"] = sha
    _write(d, "task.json", data)
    target = assurance.build_receipt(root, task_id)["target"]
    assert target["head"]["observed"] is True
    assert target["head"]["commit"] == sha
    assert target["immutable"] is True


def test_a_commit_git_can_no_longer_resolve_is_observed_but_not_immutable(task):
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    data = json.loads((d / "task.json").read_text(encoding="utf-8"))
    data["commit_sha"] = "b" * 40
    _write(d, "task.json", data)
    target = assurance.build_receipt(root, task_id)["target"]
    assert target["head"]["observed"] is True
    assert target["head"]["resolvable"] is False
    assert target["immutable"] is False


@pytest.mark.parametrize("gate_status,expected", [
    ("passed", "acceptable"),
    ("passed_with_warnings", "acceptable"),
    ("failed", "accepted-over-failed-gate"),
    ("pending", "accepted-over-unresolved-gate"),
    ("skipped", "accepted-without-gate"),
])
def test_every_gate_status_an_accepted_task_can_hold_has_a_word(task, gate_status, expected):
    """`state.gate_status` can return five things. A pair this table forgets falls
    through to `in-progress`, which describes a settled task as still running."""
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    data = json.loads((d / "acceptance.json").read_text(encoding="utf-8"))
    data["status"] = gate_status
    _write(d, "acceptance.json", data)
    final = assurance.build_receipt(root, task_id)["final_status"]
    assert final["value"] == expected
    assert final["value"] != "in-progress"


def test_a_running_task_with_a_passing_gate_is_awaiting_acceptance(task):
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    data = json.loads((d / "task.json").read_text(encoding="utf-8"))
    data["status"] = "running"
    _write(d, "task.json", data)
    assert assurance.build_receipt(root, task_id)["final_status"]["value"] == "awaiting-acceptance"


def test_recorded_decisions_with_no_approval_read_as_waiting_on_a_person(task):
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    data = json.loads((d / "task.json").read_text(encoding="utf-8"))
    data["status"] = "running"
    _write(d, "task.json", data)
    _write(d, "approvals.json", {"task_id": task_id, "decisions": [
        {"actor": "bob", "decision": "deny", "roles": ["reviewer"], "note": "no"},
    ]})
    final = assurance.build_receipt(root, task_id)["final_status"]
    assert final["value"] == "waiting-approval"
    assert "none of them an approval" in final["basis"]


def test_an_approved_task_is_not_reported_as_waiting(task):
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    data = json.loads((d / "task.json").read_text(encoding="utf-8"))
    data["status"] = "running"
    _write(d, "task.json", data)
    _write(d, "approvals.json", {"task_id": task_id, "decisions": [
        {"actor": "bob", "decision": "approve", "roles": ["reviewer"]},
    ]})
    assert assurance.build_receipt(root, task_id)["final_status"]["value"] == "awaiting-acceptance"


# ---- a record appearing later is a change too --------------------------------

def test_a_source_that_did_not_exist_yet_invalidates_the_receipt_once_written(task):
    """The realistic case: a receipt taken mid-run, then `accept` writes provenance.

    An absent source recorded as absent is what makes this detectable; omitting it
    would leave the receipt fresh through the most material change a task can undergo.
    """
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    (d / "provenance.json").unlink()
    receipt = assurance.build_receipt(root, task_id)
    assert assurance.verify(root, receipt)["fresh"] is True
    _write(d, "provenance.json", {"record": {"task_id": task_id}, "signature": "x",
                                  "algo": "HMAC-SHA256"})
    result = assurance.verify(root, receipt)
    assert result["fresh"] is False
    assert any("provenance.json" in p for p in result["changed"])


def test_a_source_absent_at_build_and_still_absent_is_not_a_change(task):
    root, task_id = task
    receipt = assurance.build_receipt(root, task_id)
    # approvals.json never existed for this task; that must not read as a change.
    assert any(s["sha256"] is None for s in receipt["sources"])
    assert assurance.verify(root, receipt)["fresh"] is True


def test_a_pending_signature_cannot_mask_an_already_settled_task(task):
    """`waiting-approval` says "the gate is satisfied and only a person is left".

    A discarded task, or one whose gate failed, is not waiting on anybody — and
    describing it as waiting would hide the outcome that was actually recorded.
    """
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    _write(d, "approvals.json", {"task_id": task_id, "decisions": [
        {"actor": "bob", "decision": "deny", "roles": ["reviewer"]},
    ]})
    for task_status, gate_status, expected in (
        ("discarded", "passed", "discarded"),
        ("running", "failed", "rejected"),
        ("running", "pending", "in-progress"),
    ):
        data = json.loads((d / "task.json").read_text(encoding="utf-8"))
        data["status"] = task_status
        _write(d, "task.json", data)
        acc = json.loads((d / "acceptance.json").read_text(encoding="utf-8"))
        acc["status"] = gate_status
        _write(d, "acceptance.json", acc)
        final = assurance.build_receipt(root, task_id)["final_status"]
        assert final["value"] == expected, (task_status, gate_status, final)


# ---- Mission Control -------------------------------------------------------

def test_mission_control_task_detail_carries_the_receipt(task):
    from rig_workbench.mission_server import task_detail
    root, task_id = task
    detail = task_detail(root, task_id)
    receipt = detail["assurance"]["receipt"]
    assert receipt["schema"] == "rig.assurance-receipt/v1"
    assert receipt["final_status"]["value"] == "acceptable"


def test_mission_control_builds_fresh_rather_than_serving_a_stale_file(task):
    """A receipt written earlier can be out of date; serving it unlabelled would
    undo the freshness check it carries."""
    from rig_workbench.mission_server import task_detail
    root, task_id = task
    d = root / ".rig" / "runs" / task_id
    stale = assurance.build_receipt(root, task_id)
    (d / "assurance.json").write_text(json.dumps(stale), encoding="utf-8")
    acc = json.loads((d / "acceptance.json").read_text(encoding="utf-8"))
    acc["status"] = "failed"
    _write(d, "acceptance.json", acc)
    detail = task_detail(root, task_id)
    assert detail["assurance"]["receipt"]["gates"]["status"] == "failed"
    assert detail["assurance"]["stored"]["fresh"] is False


def test_a_receipt_failure_does_not_take_the_task_detail_down(task, monkeypatch):
    from rig_workbench import mission_server
    root, task_id = task

    def boom(*_a, **_k):
        raise RuntimeError("nope")

    monkeypatch.setattr(mission_server.assurance, "build_receipt", boom)
    detail = mission_server.task_detail(root, task_id)
    assert detail["task"]["task_id"] == task_id     # the page still renders
    assert detail["assurance"]["receipt"] is None
    assert "nope" in detail["assurance"]["error"]


# ---- the goal, read back beside what the gate ruled on (#476) ---------------

def test_a_task_with_no_contract_says_so_rather_than_looking_like_one_with_no_goal(task):
    root, task_id = task
    assert assurance.build_receipt(root, task_id)["intent"]["observed"] is False


def test_the_contract_is_projected_beside_what_the_gate_ruled_on(task):
    """Copied, not judged: the receipt reports what the contract said and what the gate
    recorded, and never decides whether the one satisfied the other."""
    root, task_id = task
    _write(_task_dir(root, task_id), "intent.json", {
        "schema": "rig.intent-contract/v1", "goal": "do the thing",
        "requirements": [{"text": "tests pass", "origin": "explicit-user",
                          "source": "issue #1",
                          "evidence": ["tests_pass_or_explained"]}]})
    block = assurance.build_receipt(root, task_id)["intent"]
    assert block["observed"] is True
    assert block["goal"] == "do the thing"
    [requirement] = block["requirements"]
    assert requirement["declared"] is True
    assert [c["criterion"] for c in requirement["checked_by"]] == ["tests_pass_or_explained"]
    assert "satisfied" not in json.dumps(block), "the receipt makes no verdict here"


def test_a_contract_naming_one_key_twice_is_not_read_as_its_last_value(task):
    """`intent-derive` refuses that, and a receipt reading the same file with a plainer
    parser would present the parser's choice as what the contract recorded."""
    root, task_id = task
    (_task_dir(root, task_id) / "intent.json").write_text(
        '{"schema": "rig.intent-contract/v1", "goal": "g", "requirements": '
        '[{"text": "t", "origin": "inferred", "origin": "explicit-user", '
        '"source": "s", "evidence": []}]}', encoding="utf-8")
    block = assurance.build_receipt(root, task_id)["intent"]
    assert block["observed"] is False
    assert "there and cannot be read" in block["reason"]


def test_the_contract_file_is_one_of_the_digested_sources(task):
    """So a receipt built before the contract was written can be told from one built after,
    by content rather than by mtime."""
    root, task_id = task
    receipt = assurance.build_receipt(root, task_id)
    assert any(s["path"].endswith("intent.json") for s in receipt["sources"])


def test_the_markdown_reads_the_intent_block_aloud(task):
    """The renderer's claim is that it is the same model as the JSON read aloud, so a section
    the JSON gained and the page did not is that claim going false."""
    root, task_id = task
    _write(_task_dir(root, task_id), "intent.json", {
        "schema": "rig.intent-contract/v1", "goal": "do the thing",
        "assumptions": ["the API is stable"],
        "requirements": [{"text": "tests pass", "origin": "explicit-user",
                          "source": "issue #1", "evidence": ["tests_pass_or_explained"]},
                         {"text": "rig guessed this", "origin": "inferred"}],
        "ambiguities": [{"question": "which users?", "resolved_by": "asking"}]})
    page = assurance.render_markdown(assurance.build_receipt(root, task_id))
    assert "## Intent" in page
    assert "do the thing" in page and "the API is stable" in page
    assert "[explicit-user] tests pass (per issue #1)" in page
    assert "shown by: tests_pass_or_explained" in page
    assert "this gate ruled on: tests_pass_or_explained (passed)" in page
    assert "[inferred] rig guessed this" in page
    assert "names nothing that would show it" in page
    # "would be settled by": `resolved_by` says what *would* close the question, and a
    # page that read it as though it had been closed would turn an open ambiguity into a
    # decision nobody is recorded making.
    assert "open: which users? (would be settled by asking)" in page


def test_the_markdown_says_when_no_contract_was_recorded(task):
    root, task_id = task
    page = assurance.render_markdown(assurance.build_receipt(root, task_id))
    assert "## Intent" in page and "not recorded" in page


def test_the_markdown_says_when_the_contract_cannot_be_read(task):
    root, task_id = task
    (_task_dir(root, task_id) / "intent.json").write_text("not json", encoding="utf-8")
    page = assurance.render_markdown(assurance.build_receipt(root, task_id))
    assert "there and cannot be read" in page


def test_the_markdown_tells_evidence_apart_from_what_the_gate_ruled_on(task):
    """A requirement resting on a test nobody wired to this gate and one resting on nothing
    are different requirements, and a page that printed only the gate's view made them the
    same one."""
    root, task_id = task
    _write(_task_dir(root, task_id), "intent.json", {
        "schema": "rig.intent-contract/v1", "goal": "do the thing",
        "non_goals": ["rewriting the parser"],
        "requirements": [{"text": "unwired", "origin": "explicit-user", "source": "issue #1",
                          "evidence": ["test_login"]},
                         {"text": "nothing at all", "origin": "explicit-user",
                          "source": "issue #2"}]})
    page = assurance.render_markdown(assurance.build_receipt(root, task_id))
    assert "shown by: test_login" in page
    assert "names nothing that would show it" in page
    assert page.count("this gate ruled on none of it") == 2
    assert "not this: rewriting the parser" in page


# ── what round 6 found: the page was enumerating the contract's fields again ─────
#: One distinctive value per rendered field, and the marker it has to leave on the page.
#: Keyed by field name and checked against `_INTENT_RENDERED` below, so a field added to the
#: page without a value here — or a value here for a field the page stopped rendering — is a
#: failure rather than a quiet gap.
_RENDERS = {
    "goal": ({"goal": "SENTINEL-GOAL"}, "SENTINEL-GOAL"),
    "assumptions": ({"assumptions": ["SENTINEL-ASSUMPTION"]}, "SENTINEL-ASSUMPTION"),
    "requirements": ({"requirements": [{"text": "SENTINEL-REQUIREMENT",
                                        "origin": "explicit-user", "source": "issue #1"}]},
                     "SENTINEL-REQUIREMENT"),
    "non_goals": ({"non_goals": ["SENTINEL-NON-GOAL"]}, "SENTINEL-NON-GOAL"),
    "ambiguities": ({"ambiguities": [{"question": "SENTINEL-QUESTION",
                                      "resolved_by": "SENTINEL-RESOLUTION"}]},
                    "SENTINEL-QUESTION"),
}


def test_the_page_accounts_for_every_field_a_contract_has():
    """Five rounds derived one layer each and the next round found another. This is the guard
    that ends that: a field the contract gains is either rendered or explicitly withheld, and
    the check runs at import so nobody can add one without deciding.
    """
    import dataclasses

    from rig_workbench.workbench import intent

    fields = {f.name for f in dataclasses.fields(intent.IntentContract)}
    assert assurance._unrendered(fields, assurance._INTENT_RENDERED,
                                 assurance._INTENT_WITHHELD) is None
    gap = assurance._unrendered(fields | {"deadline"}, assurance._INTENT_RENDERED,
                                assurance._INTENT_WITHHELD)
    assert gap is not None and "deadline" in gap and "_INTENT_WITHHELD" in gap
    assert set(_RENDERS) == set(assurance._INTENT_RENDERED), (
        "every field the page claims to render needs a value here that proves it does")


@pytest.mark.parametrize("field", sorted(_RENDERS))
def test_each_rendered_contract_field_reaches_the_page(task, field):
    """Declaring a field rendered is not rendering it. This puts a distinctive value in each
    one and looks for it on the page, so deleting the line that prints it fails here rather
    than leaving the declaration true and the page short."""
    root, task_id = task
    fragment, marker = _RENDERS[field]
    _write(_task_dir(root, task_id), "intent.json",
           {"schema": "rig.intent-contract/v1", "goal": "do the thing",
            "requirements": [{"text": "tests pass", "origin": "explicit-user",
                              "source": "issue #1"}],
            **fragment})
    page = assurance.render_markdown(assurance.build_receipt(root, task_id))
    assert marker in page


def test_a_criterion_the_gate_recorded_twice_is_marked_where_the_block_is_built(task):
    """Marked in `_gates`, not by each reader. The Gates section lists every record and the
    Intent section looks one up by name; a rule written in both places is one the two will
    eventually disagree about, and this is a page whose only value is that it agrees with
    itself."""
    root, task_id = task
    d = _task_dir(root, task_id)
    data = json.loads((d / "acceptance.json").read_text(encoding="utf-8"))
    data["checks"].append({"name": "tests_pass_or_explained", "status": "failed",
                           "detail": "and again"})
    _write(d, "acceptance.json", data)
    _write(d, "intent.json", {
        "schema": "rig.intent-contract/v1", "goal": "do the thing",
        "requirements": [{"text": "tests pass", "origin": "explicit-user",
                          "source": "issue #1", "evidence": ["tests_pass_or_explained"]}]})
    receipt = assurance.build_receipt(root, task_id)
    assert receipt["gates"]["recorded_more_than_once"] == ["tests_pass_or_explained"]
    [checked] = receipt["intent"]["requirements"][0]["checked_by"]
    assert checked["ambiguous"] is True and checked["status"] is None
    page = assurance.render_markdown(receipt)
    assert "tests_pass_or_explained (recorded more than once — no single verdict)" in page


# ── what round 7 found: the guard's own way out ──────────────────────────────────
def test_a_withheld_field_has_to_say_why():
    """`_INTENT_WITHHELD` was a set. Adding a name to it satisfied every check and the page
    said nothing — so a field could still be left off quietly, by the mechanism written to
    stop exactly that."""
    gap = assurance._unrendered({"goal", "owner"}, assurance._INTENT_RENDERED, {"owner": ""})
    assert gap is not None and "without saying why" in gap
    gap = assurance._unrendered({"goal", "owner"}, assurance._INTENT_RENDERED,
                                {"owner": "   "})
    assert gap is not None, "a blank reason is not a reason"
    assert assurance._unrendered({"owner"}, frozenset(),
                                 {"owner": "an internal id nobody reads"}) is None


def test_a_withheld_field_is_named_on_the_page(task, monkeypatch):
    """The reason belongs where a reader of the receipt is, not only in the source. A page
    that looks complete while omitting a field the JSON carries is this section's claim going
    false, whether or not somebody wrote down why."""
    root, task_id = task
    _write(_task_dir(root, task_id), "intent.json", {
        "schema": "rig.intent-contract/v1", "goal": "do the thing",
        "requirements": [{"text": "tests pass", "origin": "explicit-user",
                          "source": "issue #1"}]})
    monkeypatch.setattr(assurance, "_INTENT_WITHHELD",
                        {"owner": "an internal id, not part of what was asked for"})
    page = assurance.render_markdown(assurance.build_receipt(root, task_id))
    assert "not shown here: owner — an internal id, not part of what was asked for" in page


def test_nothing_is_withheld_today(task):
    """And with nothing withheld the page says nothing about withholding — the note is a
    disclosure, not a fixture."""
    root, task_id = task
    _write(_task_dir(root, task_id), "intent.json", {
        "schema": "rig.intent-contract/v1", "goal": "do the thing",
        "requirements": [{"text": "tests pass", "origin": "explicit-user",
                          "source": "issue #1"}]})
    assert assurance._INTENT_WITHHELD == {}
    page = assurance.render_markdown(assurance.build_receipt(root, task_id))
    assert "not shown here" not in page


def test_two_identical_rulings_are_still_not_a_verdict(task):
    """The rule is *any* repetition, and the other duplicate tests use records that disagree —
    so marking only disagreeing repeats would have left them all passing while two identical
    records went back to reading as one verdict. A gate that ruled on one criterion twice did
    not produce a record a single verdict can be read out of, whatever the two rulings say."""
    root, task_id = task
    d = _task_dir(root, task_id)
    data = json.loads((d / "acceptance.json").read_text(encoding="utf-8"))
    data["checks"].append({"name": "tests_pass_or_explained", "status": "passed", "detail": ""})
    _write(d, "acceptance.json", data)
    _write(d, "intent.json", {
        "schema": "rig.intent-contract/v1", "goal": "do the thing",
        "requirements": [{"text": "tests pass", "origin": "explicit-user",
                          "source": "issue #1", "evidence": ["tests_pass_or_explained"]}]})
    receipt = assurance.build_receipt(root, task_id)
    assert receipt["gates"]["recorded_more_than_once"] == ["tests_pass_or_explained"]
    [checked] = receipt["intent"]["requirements"][0]["checked_by"]
    assert checked["ambiguous"] is True and checked["status"] is None
