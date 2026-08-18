"""Bring Your Own Orchestrator — import + assurance contract (#429).

Rig's claim here is that it can be the acceptance boundary for a change it did not
produce. Two things have to be true for that to be worth anything, and neither is
provable by reading the feature: an imported task must not get a cheaper gate than any
other, and an external caller must be able to tell "rig said no" apart from "rig could
not answer".

These tests hold both structurally. The gate criteria of an imported task are compared
against the ones `build_acceptance` composes for the same type; the producer's own
claims are checked to leave the gate exactly where they found it; and the accept, gate
and governance sources are read to confirm no producer name reaches a conditional
there. The mapping the contract publishes is checked against the receipt's own
vocabulary rather than a hand-copy, because a hand-copy is what drifts.
"""

import argparse
import json
import pathlib
import subprocess

import pytest

from rig_workbench.workbench import assurance, contract, lifecycle
from rig_workbench.workbench import import_task as byoo
from rig_workbench.workbench.state import build_acceptance


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A repository with `main`, and an external branch carrying a change rig did not make."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "tester")
    (root / "app.txt").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-q", "-b", "external/feature-x")
    (root / "app.txt").write_text("hello world\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "feat: extend the greeting")
    _git(root, "checkout", "-q", "main")
    return root


def _args(**over) -> argparse.Namespace:
    base = dict(head="external/feature-x", base="main", type="feature",
                producer="some-orchestrator", producer_runtime=None,
                producer_run_id=None, producer_url=None, producer_claim=None,
                summary=None, input=None, slug="byoo-demo", reason=None,
                budget_minutes=None, caller="external-ci")
    base.update(over)
    return argparse.Namespace(**base)


def _import(repo, monkeypatch, **over) -> pathlib.Path:
    monkeypatch.chdir(repo)
    byoo.cmd_import(_args(**over))
    runs = sorted((repo / ".rig" / "runs").iterdir())
    return runs[-1]


def _task(run: pathlib.Path) -> dict:
    return json.loads((run / "task.json").read_text(encoding="utf-8"))


def _check(moved: dict, kind: str) -> dict:
    return next(c for c in moved["checks"] if c["kind"] == kind)


# ── 1. the change rig did not produce becomes an ordinary task ────────────────
def test_the_task_branch_is_created_at_the_imported_commit(repo, monkeypatch):
    """The single decision the whole feature rests on: `base..branch` is the external
    change, so every sensor, the gate and `accept` see a task they cannot tell apart."""
    head = _git(repo, "rev-parse", "external/feature-x")
    run = _import(repo, monkeypatch)
    task = _task(run)
    assert _git(repo, "rev-parse", task["branch"]) == head
    assert _git(repo, "rev-parse", task["base_commit"]) == _git(repo, "rev-parse", "main")


def test_an_imported_task_gets_the_same_gate_as_any_other(repo, monkeypatch):
    """No criterion is dropped for being someone else's work. Compared against the
    gate composer itself, so adding a criterion to a preset cannot quietly exempt
    imports from it."""
    run = _import(repo, monkeypatch)
    acc = json.loads((run / "acceptance.json").read_text(encoding="utf-8"))
    expected = build_acceptance(_task(run)["task_id"], "feature", repo)
    assert [c["name"] for c in acc["checks"]] == [c["name"] for c in expected["checks"]]
    assert acc["presets"] == expected["presets"]


def test_an_imported_task_is_isolated_like_any_other(repo, monkeypatch):
    run = _import(repo, monkeypatch)
    task = _task(run)
    assert pathlib.Path(task["worktree_path"]).is_dir()
    assert task["branch"] == f"rig/{task['task_id']}"


def test_a_change_already_in_the_base_is_refused(repo, monkeypatch):
    """Importing something the base already contains would register a task with an
    empty diff and let it collect a green gate over nothing."""
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit):
        byoo.cmd_import(_args(head="main", base="main"))
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "external/feature-x")
    with pytest.raises(SystemExit):
        byoo.cmd_import(_args(head="external/feature-x", base="main"))


def test_a_change_the_base_has_already_absorbed_is_refused(repo, monkeypatch):
    """Distinct from the identical-commit case above: here the base has moved *past* the
    imported head, so the two SHAs differ and only the ancestry check catches it. Without
    it rig would register a task whose diff is empty and let it collect a green gate over
    nothing."""
    _git(repo, "merge", "-q", "external/feature-x")
    (repo / "later.txt").write_text("later\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "main moved on")
    monkeypatch.chdir(repo)
    assert _git(repo, "rev-parse", "main") != _git(repo, "rev-parse", "external/feature-x")
    with pytest.raises(SystemExit):
        byoo.cmd_import(_args(head="external/feature-x", base="main"))


def test_a_type_with_nowhere_to_put_the_change_is_refused(repo, monkeypatch):
    """`review` routes to a worktree-less capability. An imported change has nowhere to
    live without one, and registering it anyway would produce a task whose branch, diff
    and accept path do not exist."""
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit):
        byoo.cmd_import(_args(type="review"))


def test_an_unresolvable_head_is_refused(repo, monkeypatch):
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit):
        byoo.cmd_import(_args(head="refs/heads/nothing-here"))


# ── 2. the producer's own verdict is never rig's ──────────────────────────────
def test_a_producer_claiming_it_passed_leaves_the_gate_exactly_where_it_was(repo, monkeypatch):
    """The claim `tests=passed` is recorded and changes nothing. This is the whole
    trust boundary: an orchestrator that grades its own work does not thereby pass."""
    run = _import(repo, monkeypatch,
                  producer_claim=["tests=passed", "review=approved", "gate=passed"])
    acc = json.loads((run / "acceptance.json").read_text(encoding="utf-8"))
    assert {c["status"] for c in acc["checks"]} == {"pending"}
    assert acc["status"] != "passed"


def test_every_recorded_claim_carries_its_own_lack_of_effect(repo, monkeypatch):
    """`gate_effect: none` lives in the record, not in the documentation. A field that
    has to be explained elsewhere to be read correctly will be read without it."""
    run = _import(repo, monkeypatch, producer_claim=["tests=passed"])
    claims = _task(run)["import"]["claims"]
    assert claims == [{"name": "tests", "value": "passed", "gate_effect": "none"}]


def test_the_receipt_shows_the_claim_beside_the_verdict_never_inside_it(repo, monkeypatch):
    run = _import(repo, monkeypatch, producer_claim=["tests=passed"])
    receipt = assurance.build_receipt(repo, _task(run)["task_id"])
    external = receipt["producer"]["external"]
    assert external["claims_gate_effect"] == "none"
    assert receipt["gates"]["status"] == "pending"
    rendered = assurance.render_markdown(receipt)
    assert "gate_effect: none" in rendered


#: Where an imported task's gate, accept and governance decisions are made. None of
#: them may consult who produced the change.
_DECISION_MODULES = (
    "workbench/accept.py", "workbench/lifecycle.py", "workbench/state.py",
    "workbench/secrets.py", "workbench/hardening.py", "workbench/injection.py",
    "workbench/destructive.py", "workbench/schema_diff.py", "workbench/anchors.py",
    "workbench/prompt_regression.py", "govern/enforce.py", "govern/approval.py",
)

#: Reading the producer at all inside a decision module is the failure, whether or not
#: the value is compared to a name. A field that is read is a field that can be
#: branched on tomorrow.
_FORBIDDEN = ('task["import"]', 'task.get("import")', "IMPORT_KEY",
              '"producer"', "'producer'", "producer_runtime")


def test_removing_the_producer_record_changes_no_gate_outcome(repo, monkeypatch):
    """The behavioural half of the same claim, and the stronger one: evaluate the gate on
    an imported task, take the producer record away entirely, evaluate again, and compare.
    Anything in the gate path that read the producer — directly, through a helper, through
    an alias the source scan below would not recognise — makes these two differ."""
    run = _import(repo, monkeypatch,
                  producer="loud-orchestrator", producer_runtime="pi",
                  producer_claim=["tests=passed", "review=approved"])
    task_id = _task(run)["task_id"]
    gate_args = argparse.Namespace(task_id=task_id, set=None)
    lifecycle.cmd_gate(gate_args)
    with_producer = (run / "acceptance.json").read_text(encoding="utf-8")

    task = _task(run)
    task.pop("import")
    (run / "task.json").write_text(json.dumps(task), encoding="utf-8")
    lifecycle.cmd_gate(gate_args)
    without_producer = (run / "acceptance.json").read_text(encoding="utf-8")

    def _without_timestamp(text):
        record = json.loads(text)
        record.pop("checked_at", None)
        return record

    assert _without_timestamp(with_producer) == _without_timestamp(without_producer)


def test_no_decision_path_can_see_who_produced_the_change(repo):
    """Structural, not aspirational. A gate that is lenient when a particular
    orchestrator calls it is not a gate, and it would be lenient exactly where nobody
    is watching — so the check is that the value never reaches the code that decides."""
    pkg = pathlib.Path(assurance.__file__).resolve().parent.parent
    offenders = []
    for rel in _DECISION_MODULES:
        source = (pkg / rel).read_text(encoding="utf-8")
        offenders += [f"{rel}: {token}" for token in _FORBIDDEN if token in source]
    assert offenders == []


def test_a_failed_import_leaves_no_worktree_branch_or_run_directory(repo, monkeypatch):
    """A command that fails after creating a worktree leaves a branch and a run directory
    nobody asked for, and cleaning that up means knowing rig's own layout. Everything past
    the worktree is state only this command created, so it is removed on the way out."""
    monkeypatch.chdir(repo)
    monkeypatch.setattr(byoo, "load_recipe_steps",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    before = set(_git(repo, "branch", "--format=%(refname:short)").split())
    with pytest.raises(RuntimeError):
        byoo.cmd_import(_args())
    assert set(_git(repo, "branch", "--format=%(refname:short)").split()) == before
    assert not (repo / ".rig" / "runs").exists() or \
        list((repo / ".rig" / "runs").iterdir()) == []
    assert "rig-" not in _git(repo, "worktree", "list")


def test_an_unreadable_summary_is_refused_before_anything_is_created(repo, monkeypatch):
    """The realistic version of the above: the file the operator named is not there. It is
    read before the worktree exists, so the failure costs nothing to recover from."""
    monkeypatch.chdir(repo)
    before = set(_git(repo, "branch", "--format=%(refname:short)").split())
    with pytest.raises(SystemExit):
        byoo.cmd_import(_args(summary=str(repo / "does-not-exist.md")))
    assert set(_git(repo, "branch", "--format=%(refname:short)").split()) == before
    assert not (repo / ".rig" / "runs").exists() or \
        list((repo / ".rig" / "runs").iterdir()) == []


# ── 3. a name is not a commit ─────────────────────────────────────────────────
def test_a_branch_is_recorded_as_the_movable_name_it_is(repo, monkeypatch):
    run = _import(repo, monkeypatch, head="external/feature-x")
    imported = _task(run)["import"]
    assert imported["head_symbolic"] is True
    assert imported["head_ref"] == "refs/heads/external/feature-x"
    assert imported["head_commit"] == _git(repo, "rev-parse", "external/feature-x")


def test_a_commit_is_recorded_as_one_and_cannot_move(repo, monkeypatch):
    sha = _git(repo, "rev-parse", "external/feature-x")
    run = _import(repo, monkeypatch, head=sha)
    imported = _task(run)["import"]
    assert imported["head_symbolic"] is False
    receipt = assurance.build_receipt(repo, _task(run)["task_id"])
    producer_ref = _check(assurance.target_moved(repo, receipt), "producer-ref")
    assert producer_ref["applicable"] is False and producer_ref["moved"] is False


def test_a_commit_added_to_the_task_worktree_after_the_import_is_noticed(repo, monkeypatch):
    """The branch rig owns is the one `accept` squash-merges. Checking only the
    producer's ref would let the receipt name the commit rig was handed while a
    different one is what lands — an identity recorded but never re-compared."""
    sha = _git(repo, "rev-parse", "external/feature-x")
    run = _import(repo, monkeypatch, head=sha)
    task = _task(run)
    wt = pathlib.Path(task["worktree_path"])
    (wt / "added-later.txt").write_text("not what rig was handed\n", encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "someone kept working in the worktree")
    moved = assurance.target_moved(repo, assurance.build_receipt(repo, task["task_id"]))
    assert moved["moved"] is True
    assert _check(moved, "producer-ref")["moved"] is False
    assert _check(moved, "task-branch")["moved"] is True
    assert contract.build(repo, task["task_id"])["target_moved"]["moved"] is True


def test_an_immutable_import_never_reports_nothing_moved_from_a_check_that_did_not_run(
        repo, monkeypatch):
    """`moved: False` has to mean two checks ran and agreed, not that both were skipped.
    An immutable commit removes the producer-ref failure mode; it does not remove the
    task branch."""
    sha = _git(repo, "rev-parse", "external/feature-x")
    run = _import(repo, monkeypatch, head=sha)
    moved = assurance.target_moved(repo, assurance.build_receipt(repo, _task(run)["task_id"]))
    assert moved["applicable"] is True
    assert _check(moved, "task-branch")["applicable"] is True


def test_a_task_branch_that_is_gone_says_so_rather_than_that_nothing_moved(repo, monkeypatch):
    """`accept` and `discard` remove the branch. Reporting that as "did not move" would
    be an absence dressed as a measurement."""
    run = _import(repo, monkeypatch)
    task = _task(run)
    receipt = assurance.build_receipt(repo, task["task_id"])
    _git(repo, "worktree", "remove", "--force", task["worktree_path"])
    _git(repo, "branch", "-D", task["branch"])
    branch_check = _check(assurance.target_moved(repo, receipt), "task-branch")
    assert branch_check["applicable"] is False
    assert "no longer resolves" in branch_check["reason"]


def test_a_target_that_moved_after_verification_stops_being_fresh(repo, monkeypatch):
    """The change a digest cannot detect. `verify` compares file contents, and a branch
    moving rewrites nothing on disk — yet it is the change that matters most to a
    caller asking about that branch."""
    run = _import(repo, monkeypatch)
    receipt = assurance.build_receipt(repo, _task(run)["task_id"])
    assert assurance.verify(repo, receipt)["fresh"] is True
    _git(repo, "checkout", "-q", "external/feature-x")
    (repo / "app.txt").write_text("hello world, again\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "producer kept going")
    _git(repo, "checkout", "-q", "main")
    result = assurance.verify(repo, receipt)
    assert result["fresh"] is False
    assert result["target_moved"]["moved"] is True
    assert result["final_status"] == "invalidated"


def test_a_moved_target_is_never_reported_as_a_changed_path(repo, monkeypatch):
    """`changed` is a list of file paths other tools already parse. Slipping a ref name
    into it would be an unannounced type change."""
    run = _import(repo, monkeypatch)
    receipt = assurance.build_receipt(repo, _task(run)["task_id"])
    _git(repo, "branch", "-f", "external/feature-x", "main")
    result = assurance.verify(repo, receipt)
    assert result["target_moved"]["moved"] is True
    assert "refs/heads/external/feature-x" not in result["changed"]
    assert result["changed"] == [] and result["missing"] == []


def test_a_ref_that_stopped_resolving_counts_as_moved(repo, monkeypatch):
    run = _import(repo, monkeypatch)
    receipt = assurance.build_receipt(repo, _task(run)["task_id"])
    _git(repo, "branch", "-D", "external/feature-x")
    moved = assurance.target_moved(repo, receipt)
    assert moved["moved"] is True
    assert _check(moved, "producer-ref")["resolves_to"] is None


# ── 4. accept has to stay reachable ───────────────────────────────────────────
def test_a_headless_import_still_satisfies_the_structural_diff_summary_requirement(repo, monkeypatch):
    """`accept` treats a missing diff summary as structural — not overridable even with
    `--force` — so without a derived one every imported task would be permanently
    unacceptable and this whole flow would stop one step short of its point."""
    run = _import(repo, monkeypatch)
    text = (run / "diff.md").read_text(encoding="utf-8")
    assert text.strip()
    assert _task(run)["import"]["diff_summary"] == "derived"


def test_a_derived_summary_says_that_nobody_reviewed_it(repo, monkeypatch):
    """Deriving one is a convenience, not a review. The file `accept` reads and the
    receipt digests has to say so, or the convenience becomes a claim."""
    run = _import(repo, monkeypatch)
    text = (run / "diff.md").read_text(encoding="utf-8")
    assert "No reviewer wrote this" in text
    assert "feat: extend the greeting" in text


def test_an_authored_summary_is_recorded_as_authored(repo, monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    summary.write_text("# real\n\n## summary\n\nA person wrote this.\n", encoding="utf-8")
    run = _import(repo, monkeypatch, summary=str(summary))
    assert _task(run)["import"]["diff_summary"] == "authored"
    assert "A person wrote this." in (run / "diff.md").read_text(encoding="utf-8")


# ── 5. external strings cannot lie about themselves ───────────────────────────
#: Written as escapes, not as literal characters. `scan-injection` treats invisible
#: unicode in a diff as fail-grade and is right to — a reviewer cannot see what is not
#: rendered — and a test fixture is not an exemption from that. The escape is legible in
#: the source and reaches the code under test as the same code point.
@pytest.mark.parametrize("field,value", [
    ("producer", "orch\u200bestrator"),          # zero-width space
    ("producer", "orchestrator\nACCEPTED: yes"),
    ("producer", "o" * 65),
    ("producer_run_id", "run\u202e4711"),        # right-to-left override
    ("producer_url", "https://example.invalid/\nfake"),
])
def test_a_deceptive_provenance_string_is_refused_rather_than_cleaned(repo, monkeypatch,
                                                                      field, value):
    """Refused, not stripped. Quietly rewriting would hand back a value nobody typed,
    and these are printed to the terminal and rendered into the receipt."""
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit):
        byoo.cmd_import(_args(**{field: value}))


def test_a_malformed_claim_is_refused(repo, monkeypatch):
    monkeypatch.chdir(repo)
    with pytest.raises(SystemExit):
        byoo.cmd_import(_args(producer_claim=["tests-passed"]))


# ── 6. the receipt tells a declaration from a measurement ─────────────────────
def test_independence_is_declared_separate_and_never_independent(repo, monkeypatch):
    """A weaker claim wearing its own weakness. Rig verified the commit; it did not
    verify who produced it, and `independent` would assert exactly the property the
    trust boundary exists to establish."""
    run = _import(repo, monkeypatch)
    receipt = assurance.build_receipt(repo, _task(run)["task_id"])
    independence = receipt["verifier"]["independence"]
    assert independence["verdict"] == "declared-separate"
    assert "did not verify" in independence["basis"]


def test_a_task_rig_produced_itself_keeps_saying_unrecorded(repo, monkeypatch):
    """The new verdict must not leak onto ordinary tasks — those still record nothing
    about who reviewed them."""
    run = _import(repo, monkeypatch)
    task = _task(run)
    task.pop("import")
    (run / "task.json").write_text(json.dumps(task), encoding="utf-8")
    receipt = assurance.build_receipt(repo, task["task_id"])
    assert receipt["verifier"]["independence"]["verdict"] == "unrecorded"
    assert receipt["producer"]["external"] is None
    assert receipt["target"]["import"] is None


def test_a_declared_runtime_stops_the_receipt_saying_rig_records_none(repo, monkeypatch):
    """The mirror image of #428's rule: a recorded fact reported as absent is as
    misleading as an absent one reported as a fact."""
    run = _import(repo, monkeypatch, producer_runtime="pi")
    receipt = assurance.build_receipt(repo, _task(run)["task_id"])
    runtime = receipt["producer"]["runtime"]
    assert runtime["observed"] is True
    assert runtime["id"] == "pi" and runtime["declared"] is True


def test_an_import_without_a_runtime_says_why_rather_than_nothing(repo, monkeypatch):
    run = _import(repo, monkeypatch)
    runtime = assurance.build_receipt(repo, _task(run)["task_id"])["producer"]["runtime"]
    assert runtime["observed"] is False
    assert "--producer-runtime" in runtime["reason"]


def test_the_verified_head_is_pinned_before_anything_lands(repo, monkeypatch):
    """`record-commit` links the commit that lands after accept; the import pins the
    commit rig was handed. `source` keeps them apart, because a reader who cannot tell
    a verified change from a merged one cannot audit either."""
    run = _import(repo, monkeypatch)
    head = assurance.build_receipt(repo, _task(run)["task_id"])["target"]["head"]
    assert head["observed"] and head["source"] == "import"
    assert head["commit"] == _git(repo, "rev-parse", "external/feature-x")


def test_a_landed_commit_still_wins_over_the_import_pin(repo, monkeypatch):
    run = _import(repo, monkeypatch)
    task = _task(run)
    task["commit_sha"] = _git(repo, "rev-parse", "main")
    (run / "task.json").write_text(json.dumps(task), encoding="utf-8")
    head = assurance.build_receipt(repo, task["task_id"])["target"]["head"]
    assert head["source"] == "record-commit"


# ── 7. the machine contract ───────────────────────────────────────────────────
def test_the_mapping_covers_every_status_the_receipt_can_emit(repo):
    """The bug this exists to prevent has already happened once in this codebase: a
    status table missing a pair, and settled tasks reported as still running. Derived
    from the receipt's own vocabulary rather than restated, so a new value fails here
    instead of falling through to a friendly default."""
    assert set(contract.STATUS) == contract.final_status_vocabulary()


def test_the_vocabulary_is_read_from_the_receipt_and_not_from_the_mapping(monkeypatch):
    """The guard above is only worth something if the two sides are independent. Derive
    the vocabulary from `STATUS` itself and the comparison passes no matter what the
    receipt does — so this adds a status to the receipt's table and checks that the
    vocabulary grows with it and the guard would fire."""
    extended = dict(assurance._FINAL_STATUS)
    extended[("accepted", "invented-gate-state")] = "invented-final-status"
    monkeypatch.setattr(assurance, "_FINAL_STATUS", extended)
    assert "invented-final-status" in contract.final_status_vocabulary()
    assert set(contract.STATUS) != contract.final_status_vocabulary()


def test_an_override_is_never_reported_as_a_pass():
    """A human accepting over a failed gate applied the change; rig did not vouch for
    it. Telling a caller `acceptable` would record an assurance nobody gave."""
    for value in ("accepted-over-failed-gate", "accepted-over-unresolved-gate",
                  "accepted-without-gate"):
        assert contract.STATUS[value] == contract.NOT_ACCEPTABLE


def test_work_still_in_flight_is_never_reported_as_a_refusal():
    """Folding `pending` into `not-acceptable` makes a poller read "still running" as
    "refused"; folding it into `acceptable` merges something no gate has ruled on."""
    for value in ("awaiting-acceptance", "waiting-approval", "in-progress"):
        assert contract.STATUS[value] == contract.PENDING


def test_only_a_cleared_gate_is_acceptable():
    assert [k for k, v in contract.STATUS.items() if v == contract.ACCEPTABLE] == ["acceptable"]


def _run_contract(repo, monkeypatch, task_id=None, as_json=True) -> int:
    monkeypatch.chdir(repo)
    args = argparse.Namespace(task_id=task_id, json=as_json)
    with pytest.raises(SystemExit) as exc:
        contract.cmd_contract(args)
    return exc.value.code


def test_a_task_rig_cannot_read_is_an_execution_error_not_a_refusal(repo, monkeypatch, capsys):
    """The reason this is not a `--json` flag on `receipt`. `die` exits 1 for a bad task
    id, corrupt state and an unmet gate alike, so a caller reading 1 cannot tell a
    refusal from an outage — and both wrong readings are costly."""
    code = _run_contract(repo, monkeypatch, task_id="no-such-task")
    assert code == contract.EXIT_CODE[contract.EXECUTION_ERROR] == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "execution-error"
    assert payload["verified_head"] is None


def test_a_pending_task_gets_its_own_exit_code(repo, monkeypatch, capsys):
    _import(repo, monkeypatch)
    capsys.readouterr()  # the import banner is not this test's output
    code = _run_contract(repo, monkeypatch)
    assert code == contract.EXIT_CODE[contract.PENDING] == 3
    assert json.loads(capsys.readouterr().out)["status"] == "pending"


def test_the_contract_names_the_verified_head_and_the_receipt_it_came_from(repo, monkeypatch):
    run = _import(repo, monkeypatch)
    result = contract.build(repo, _task(run)["task_id"])
    assert result["verified_head"] == _git(repo, "rev-parse", "external/feature-x")
    assert result["producer"] == "some-orchestrator"
    assert (repo / result["receipt"]).is_file()


def test_the_receipt_it_points_at_is_the_one_it_answered_from(repo, monkeypatch):
    """A contract pointing at an older receipt would send a caller to read a different
    answer than the one it acted on."""
    run = _import(repo, monkeypatch)
    result = contract.build(repo, _task(run)["task_id"])
    stored = json.loads((repo / result["receipt"]).read_text(encoding="utf-8"))
    assert stored["final_status"]["value"] == result["final_status"]


def test_a_moved_target_takes_acceptable_away_again(repo, monkeypatch):
    """rig verified a commit; the caller is asking about a name that now means
    something else. Answering `acceptable` would answer a question nobody asked."""
    run = _import(repo, monkeypatch)
    task_id = _task(run)["task_id"]
    task = _task(run)
    task["status"] = "accepted"
    (run / "task.json").write_text(json.dumps(task), encoding="utf-8")
    acc = json.loads((run / "acceptance.json").read_text(encoding="utf-8"))
    for check in acc["checks"]:
        check["status"] = "passed"
    acc["status"] = "passed"
    (run / "acceptance.json").write_text(json.dumps(acc), encoding="utf-8")
    assert contract.build(repo, task_id)["status"] == contract.ACCEPTABLE
    _git(repo, "branch", "-f", "external/feature-x", "main")
    result = contract.build(repo, task_id)
    assert result["status"] == contract.NOT_ACCEPTABLE
    assert result["final_status"] == "acceptable"
    assert result["target_moved"]["moved"] is True


def test_an_unmapped_status_is_an_execution_error_not_a_guess(repo, monkeypatch):
    """Rounding an unknown state to the nearest familiar word is how a caller acts on
    a status nobody has considered."""
    run = _import(repo, monkeypatch)
    task_id = _task(run)["task_id"]
    monkeypatch.setattr(assurance, "_final_status",
                        lambda *a, **k: {"value": "something-new", "basis": "x"})
    result = contract.build(repo, task_id)
    assert result["status"] == contract.EXECUTION_ERROR
    assert "something-new" in result["reason"]


def test_the_contract_is_the_same_one_for_every_caller(repo, monkeypatch):
    """Reused by the next orchestrator, or it is not a contract. Nothing in the result
    varies with who produced the change except the recorded name."""
    first = contract.build(repo, _task(_import(repo, monkeypatch,
                                               producer="orchestrator-a"))["task_id"])
    second = contract.build(repo, _task(_import(repo, monkeypatch, slug="byoo-demo-b",
                                                producer="orchestrator-b"))["task_id"])
    varies = {k for k in first if first[k] != second[k]}
    # `target_moved` names the task's own branch, so it differs by task identity; what
    # must not differ is the answer it produced.
    assert varies <= {"task_id", "producer", "receipt", "reason", "target_moved"}
    assert first["status"] == second["status"]
    assert first["target_moved"]["moved"] == second["target_moved"]["moved"]
