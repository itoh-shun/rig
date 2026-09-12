"""A criterion a sensor backs is the sensor's to answer, not the operator's to declare.

The first dogfood run of rig on itself wrote `gate <id> --set no_gate_tampering=passed`
and got a pass: every sensor took the set of criteria the operator had just set by hand
and, for those, recorded the declaration over its own measurement. The scan still ran and
still printed its findings, and the check said `passed` anyway — the gate reported what it
had been told rather than what it had measured. That run happened to be clean, so the
outcome was right by luck, which is the only reason it was not worse.

What is pinned here is the rule `cmd_gate` enforces now (rig_workbench/workbench/
lifecycle.py — `sensor_contradictions`, whose docstring carries the reasoning for choosing
refusal over a recorded override):

  * the sensors run after the `--set` loop and write over it, so a sensor-backed criterion
    always carries its sensor's answer, whatever was declared;
  * on top of that, a declaration contradicting one of the five fail-grade sensors
    (`REFUSABLE_CRITERIA`) is REFUSED — the difference is shown and the command exits the
    usage-error code, which is NOT the failed-gate code a script branches on;
  * the refusal costs the operator that one criterion, not the batch: everything else in
    the invocation is recorded, and so is the sensor's own finding;
  * a warning-grade sensor never refuses. `public_api_changes_documented` is annotated by
    the schema sensor, and an annotation must not be able to reject a whole `gate` call;
  * a `--set` no sensor contradicts is recorded exactly as given, which is every one of the
    ten declaration-only criteria of a `feature` gate, and a sensor-backed criterion whose
    sensor found nothing;
  * a declaration STRICTER than the sensor stands — the rule is against talking a finding
    down, not against an operator who knows something the scan cannot see. For the two
    sensors that write on every evaluation rather than only on a finding, that is
    `ja_prose.record`, pinned in tests/test_ja_prose_gate.py;
  * and a gate with no criteria at all is refused rather than passed, because "skipped"
    used to carry the task to `gate_passed` with nothing measured.

Driven through the real CLI in a scratch repo: the defect was a wiring defect between
`cmd_gate` and the sensors, and only a process that runs both can show it is gone.
"""

import json
import os
import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"

# Written out rather than imported from `rig_workbench.exitcodes`, for the reason
# tests/test_exit_code_surface.py gives at length: a caller outside this repo has the
# integers and nothing else, and comparing the source tree to itself pins nothing.
REJECTED = 1  # rig judged the work and the answer is no — here, a failed gate
ERROR = 2     # rig could not produce an answer — bad usage, state that is not there
PENDING = 3   # rig ran and there is no verdict yet — criteria are still `pending`

# Every scenario below trips the anti-tamper sensor, whose trigger is a PATH in the task
# diff (`.rig/gates.json`, a deleted test file) rather than a pattern in file content. The
# rule under test is criterion-agnostic — `sensor_contradictions` compares declarations
# against results and knows nothing about which sensor produced one — so one sensor is
# enough to pin it, and the secret / injection / destructive sensors pin the same refusal
# through their own CLI in tests/test_secret_scan.py, test_injection_scan.py and
# test_destructive_scan.py. Reaching for those here would mean writing a credential-shaped
# or destroyer-shaped line into rig's own tree, which its own gate then has to be argued
# out of — the exact conversation this file exists to make unnecessary.

# The `feature` gate's criteria that no sensor backs, spelled out rather than derived from
# GATE_PRESETS minus a list of sensors: deriving them would make this file agree with rig
# about which criteria are declarations instead of pinning which ones are. The other five
# of the fifteen (no_secret_leak, no_gate_tampering, no_injection_markers,
# no_destructive_operation, public_api_changes_documented) are the sensor-backed ones.
DECLARATION_ONLY = (
    "task_intent_satisfied", "no_unrelated_diff", "diff_summary_written",
    "risk_summary_written", "tests_pass_or_explained", "no_type_errors_or_explained",
    "requirement_summary_written", "implementation_matches_requirement",
    "tests_added_or_explained", "migration_or_backward_compatibility_considered",
)


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True, text=True)


def _git_out(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout


def make_repo(tmp_path):
    """Scratch repo whose base commit holds the surfaces the sensors guard."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / ".rig").mkdir(parents=True)
    _git(repo, "init", "-q")
    # `accept --force` squash-merges through rig's own git calls, which carry none of the
    # `-c` pair `_git` passes; on a runner with no global identity that commit dies with
    # "empty ident name". The identity belongs in the repo, which the task worktrees share.
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "tests" / "test_app.py").write_text(
        "def test_x():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    (repo / ".rig" / "gates.json").write_text('{"extra_criteria": {}}\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def cli(repo, wt_root, *args):
    env = dict(os.environ, RIG_WORKTREE_ROOT=str(wt_root))
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          cwd=repo, capture_output=True, text=True, timeout=60, env=env)


def new_task(repo, wt_root, slug="authority"):
    r = cli(repo, wt_root, "new", "add a config knob", "--type", "feature", "--slug", slug)
    assert r.returncode == 0, r.stdout + r.stderr
    task_id = re.search(r"task_id: (\S+)", r.stdout).group(1)
    return task_id, wt_root / task_id


def commit(wt, message="work"):
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", message)


def acceptance(repo, task_id):
    return json.loads((repo / ".rig" / "runs" / task_id / "acceptance.json")
                      .read_text(encoding="utf-8"))


def status_of(repo, task_id, name):
    return next(c for c in acceptance(repo, task_id)["checks"] if c["name"] == name)


# ── a sensor that detects cannot be declared away ─────────────────────────────
def test_a_detecting_sensor_refuses_a_hand_written_pass(tmp_path):
    """The reproduction, inverted: the task diff edits the gate's own config, and
    `no_gate_tampering` is declared `passed` by hand — the exact pair of moves the first
    dogfood run made, which the gate reported as a pass."""
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    task_id, wt = new_task(repo, wt_root)

    (wt / ".rig" / "gates.json").write_text('{"extra_criteria": {"standard": []}}\n',
                                            encoding="utf-8")
    commit(wt, "edit the gate config")

    r = cli(repo, wt_root, "gate", task_id,
            "--set", "no_gate_tampering=passed:reviewed, intentional",
            "--set", "task_intent_satisfied=passed")
    assert r.returncode == ERROR, r.stdout + r.stderr

    # The difference is shown in the operator's terms: what they set, what the machine
    # measured, and the finding it measured it from.
    out = r.stdout + r.stderr
    assert "no_gate_tampering: you set 'passed' — the sensor measured 'failed'" in out
    assert "gate_config_modified" in out

    # The declaration is what was refused. The measurement behind it is recorded, and so
    # is the rest of the batch — being wrong about one criterion does not cost the others.
    check = status_of(repo, task_id, "no_gate_tampering")
    assert check["status"] == "failed"
    assert check["tamper_findings"]
    assert status_of(repo, task_id, "task_intent_satisfied")["status"] == "passed"
    # And so is the head the sensors measured it on. The refusal comes after the save, so a
    # refused `--set` is still an evaluation — a gate whose head went unrecorded here would
    # be refused by `accept` as unknown for a reason that has nothing to do with the work.
    assert acceptance(repo, task_id)["evaluated_head"] == _git_out(wt, "rev-parse", "HEAD").strip()


def test_a_warning_grade_finding_cannot_be_declared_passed(tmp_path):
    """Warning-grade too, not only the fail-grade findings: deleting an existing test file
    on a feature task is `warning`, and `passed` is still not a status the operator may
    write over it. Without this, every fail-grade finding has a one-word downgrade
    available to it the moment a sensor grades something warning."""
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    task_id, wt = new_task(repo, wt_root)

    (wt / "tests" / "test_app.py").unlink()
    commit(wt, "delete the test file")

    r = cli(repo, wt_root, "gate", task_id, "--set", "no_gate_tampering=passed")
    assert r.returncode == ERROR, r.stdout + r.stderr
    assert "you set 'passed' — the sensor measured 'warning'" in r.stdout + r.stderr
    assert status_of(repo, task_id, "no_gate_tampering")["status"] == "warning"


def test_the_refusal_does_not_share_an_exit_code_with_a_failed_gate(tmp_path):
    """The two stops a caller must be able to tell apart, driven in one repo.

    state.py:28-42 keeps `die` and `reject` separate precisely so a script reading `$?`
    can: 1 is a verdict on the work, 2 is "rig could not produce an answer" — a usage
    error. A refused `--set` is the second: the operator wrote a status the machine
    contradicts, which says nothing about whether this task's gate passes. Were both 1,
    a caller could not tell "your gate failed" from "your command was wrong", and the
    two call for opposite next moves.
    """
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    task_id, wt = new_task(repo, wt_root)

    (wt / ".rig" / "gates.json").write_text('{"extra_criteria": {"standard": []}}\n',
                                            encoding="utf-8")
    commit(wt, "edit the gate config")

    refused = cli(repo, wt_root, "gate", task_id, "--set", "no_gate_tampering=passed")
    failed = cli(repo, wt_root, "gate", task_id)
    empty = _with_empty_gate(repo, wt_root, task_id)

    assert failed.returncode == REJECTED, failed.stdout + failed.stderr
    assert refused.returncode == ERROR, refused.stdout + refused.stderr
    assert empty.returncode == ERROR, empty.stdout + empty.stderr
    assert refused.returncode != failed.returncode


def _with_empty_gate(repo, wt_root, task_id):
    acc_path = repo / ".rig" / "runs" / task_id / "acceptance.json"
    acc = json.loads(acc_path.read_text(encoding="utf-8"))
    acc["checks"] = []
    acc_path.write_text(json.dumps(acc), encoding="utf-8")
    return cli(repo, wt_root, "gate", task_id)


# ── a warning-grade sensor annotates; it does not refuse ──────────────────────
def test_a_warning_grade_sensor_downgrade_does_not_refuse_the_batch(tmp_path):
    """The schema sensor is warning-grade by design (README.md:234): it downgrades
    `public_api_changes_documented` to `warning` when the API moved and diff.md does not
    say so, and it never fails the gate on its own. An annotation that could reject a
    whole `gate` invocation would be a blocker wearing a warning's name — so the batch
    below, which names every settable criterion on exactly that task, has to apply.
    """
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    (repo / "openapi.json").write_text(json.dumps(
        {"openapi": "3.0.0", "paths": {"/a": {"get": {"responses": {"200": {}}}}}}),
        encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add the schema")
    task_id, wt = new_task(repo, wt_root)

    # the API moves, and no diff.md is written — the sensor's downgrade condition
    (wt / "openapi.json").write_text(json.dumps(
        {"openapi": "3.0.0", "paths": {"/a": {"get": {"responses": {"200": {}}}},
                                       "/b": {"post": {"responses": {"201": {}}}}}}),
        encoding="utf-8")
    commit(wt, "add POST /b")
    assert not (repo / ".rig" / "runs" / task_id / "diff.md").exists()

    everything = ("no_secret_leak", "no_gate_tampering", "no_injection_markers",
                  "no_destructive_operation", "public_api_changes_documented",
                  *DECLARATION_ONLY)
    r = cli(repo, wt_root, "gate", task_id,
            *(arg for name in everything for arg in ("--set", f"{name}=passed")))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "set by hand" not in r.stdout + r.stderr

    api = status_of(repo, task_id, "public_api_changes_documented")
    assert api["status"] == "warning"          # the sensor's annotation, recorded
    assert api["api_diff"]                     # with what it saw
    # and the words under that status are the sensor's own, not the `passed` the operator
    # asked for — the detail goes with the status, for every sensor that writes one
    assert api["detail"] == ("machine-detected API schema changes are not documented "
                             "(diff.md missing/empty)")
    assert api["by"] == "schema-sensor"
    assert acceptance(repo, task_id)["status"] == "passed_with_warnings"
    for name in DECLARATION_ONLY:              # and the rest of the batch applied
        assert status_of(repo, task_id, name)["status"] == "passed"


def test_taking_a_criterion_over_from_a_sensor_drops_the_sensors_explanation(tmp_path):
    """`--set` with no `:detail` does not touch the detail, so a sensor's words stayed
    under a status the operator had just written — `--set no_secret_leak=passed` on a diff
    whose secret was removed read "1 potential secret(s) detected" under a pass. The
    operator's silence is not the sensor's sentence, and `by` is what tells the two apart.
    """
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    task_id, wt = new_task(repo, wt_root)

    (wt / "tests" / "test_app.py").unlink()          # warning-grade tamper finding
    commit(wt, "delete the test file")
    r = cli(repo, wt_root, "gate", task_id)
    # A warning-grade finding is not a failure: the gate is PENDING (3, the not-yet-judged
    # code) because the other fourteen criteria are unrecorded — not REJECTED, and not the
    # ERROR a refused declaration takes.
    assert r.returncode == PENDING, r.stdout + r.stderr
    sensor_written = status_of(repo, task_id, "no_gate_tampering")
    assert sensor_written["status"] == "warning"
    assert sensor_written["detail"].startswith("(tamper sensor)")
    assert sensor_written["by"] == "tamper-sensor"

    # the finding goes away, and the operator records the criterion without a reason
    _git(wt, "checkout", "HEAD~1", "--", "tests/test_app.py")
    commit(wt, "restore the test file")
    cli(repo, wt_root, "gate", task_id, "--set", "no_gate_tampering=passed")
    check = status_of(repo, task_id, "no_gate_tampering")
    assert (check["status"], check["by"]) == ("passed", "operator")
    assert check["detail"] == ""

    # ...and a criterion no sensor touches follows the same rule: a declaration with no
    # reason leaves none behind, rather than keeping the one written for the old status
    cli(repo, wt_root, "gate", task_id, "--set", "task_intent_satisfied=passed:the knob works")
    assert status_of(repo, task_id, "task_intent_satisfied")["note"] == "the knob works"
    cli(repo, wt_root, "gate", task_id, "--set", "task_intent_satisfied=warning")
    check = status_of(repo, task_id, "task_intent_satisfied")
    assert (check["status"], check["detail"]) == ("warning", "")
    assert "note" not in check

    # A record written before `by` existed says nothing about who wrote it, and the
    # answer this rule needs is "not the operator" — a stale sensor sentence under a
    # status they just set explains it wrongly whether or not the field is there.
    acc_path = repo / ".rig" / "runs" / task_id / "acceptance.json"
    acc = json.loads(acc_path.read_text(encoding="utf-8"))
    legacy = next(c for c in acc["checks"] if c["name"] == "no_gate_tampering")
    legacy.pop("by", None)
    legacy["detail"] = "(tamper sensor) 2 test-weakening pattern(s) in the diff — review them"
    legacy["status"] = "warning"
    acc_path.write_text(json.dumps(acc), encoding="utf-8")

    cli(repo, wt_root, "gate", task_id, "--set", "no_gate_tampering=passed")
    check = status_of(repo, task_id, "no_gate_tampering")
    assert (check["status"], check["by"], check["detail"]) == ("passed", "operator", "")


def test_the_operators_note_is_its_own_field_and_outlives_the_sensors_detail(tmp_path):
    """(a) and (c). A reason recorded beside a finding is the operator's, and `note` is
    where it lives — not merged into the sensor's `detail`, which the sensor rewrites on
    every evaluation and which therefore kept such a sentence for exactly one run.
    """
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    task_id, wt = new_task(repo, wt_root)

    (wt / "tests" / "test_app.py").unlink()          # warning-grade tamper finding
    (wt / "tests" / "test_new.py").write_text("def test_x():\n    assert 1 + 1 == 2\n",
                                              encoding="utf-8")
    commit(wt, "move the test")

    reason = "reviewed - the test moved to tests/test_new.py"
    r = cli(repo, wt_root, "gate", task_id, "--set", f"no_gate_tampering=warning:{reason}")
    assert r.returncode == PENDING, r.stdout + r.stderr    # agreement, so no refusal
    for _ in range(3):                                     # (a) three more bare runs
        assert cli(repo, wt_root, "gate", task_id).returncode == PENDING
    check = status_of(repo, task_id, "no_gate_tampering")
    assert check["status"] == "warning"
    assert check["note"] == reason                         # untouched, run after run
    assert check["detail"].startswith("(tamper sensor)")   # the sensor's, entirely
    assert reason not in check["detail"]
    assert check["by"] == "tamper-sensor"
    assert f"note (operator): {reason}" in cli(repo, wt_root, "status", task_id).stdout

    # (c) a fresh declaration with no reason is not still explained by the old one
    cli(repo, wt_root, "gate", task_id, "--set", "no_gate_tampering=warning")
    assert "note" not in status_of(repo, task_id, "no_gate_tampering")


def test_a_note_does_not_outlive_the_status_it_was_written_about(tmp_path):
    """(b). The sequence that made string-merging wrong: a reason recorded when the tree
    was clean, then a finding, then a bare `--set` — the old sentence must not end up
    under the new status, explaining a state that no longer exists.
    """
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    task_id, wt = new_task(repo, wt_root)
    (wt / "feature.py").write_text("def f():\n    return 42\n", encoding="utf-8")
    commit(wt, "add a feature")

    cli(repo, wt_root, "gate", task_id, "--set", "no_gate_tampering=passed:all clear")
    assert status_of(repo, task_id, "no_gate_tampering")["note"] == "all clear"

    (wt / "tests" / "test_app.py").unlink()
    commit(wt, "delete the test")
    cli(repo, wt_root, "gate", task_id, "--set", "no_gate_tampering=warning")

    check = status_of(repo, task_id, "no_gate_tampering")
    assert check["status"] == "warning"
    assert "note" not in check
    assert check["detail"].startswith("(tamper sensor)") and "all clear" not in check["detail"]


def test_a_refused_declaration_takes_its_note_with_it(tmp_path):
    """(d). The refusal path is unchanged, and the reason offered for the status that was
    refused goes with it: a claim about a `passed` that never happened explains nothing.
    """
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    task_id, wt = new_task(repo, wt_root)
    (wt / ".rig" / "gates.json").write_text('{"extra_criteria": {"standard": []}}\n',
                                            encoding="utf-8")
    commit(wt, "edit the gate config")

    r = cli(repo, wt_root, "gate", task_id,
            "--set", "no_gate_tampering=passed:reviewed, intentional")
    assert r.returncode == ERROR, r.stdout + r.stderr
    check = status_of(repo, task_id, "no_gate_tampering")
    assert check["status"] == "failed"
    assert "note" not in check
    assert check["detail"].startswith("(tamper sensor)")


def test_the_schema_sensor_keeps_an_agreeing_operators_note(tmp_path):
    """(e). Warning-grade and refusal-free, but the same two fields: the sensor owns the
    detail it writes, and the reason the operator gave for the same `warning` is theirs.
    """
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    (repo / "openapi.json").write_text(json.dumps(
        {"openapi": "3.0.0", "paths": {"/a": {"get": {"responses": {"200": {}}}}}}),
        encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add the schema")
    task_id, wt = new_task(repo, wt_root)

    (wt / "openapi.json").write_text(json.dumps(
        {"openapi": "3.0.0", "paths": {"/a": {"get": {"responses": {"200": {}}}},
                                       "/b": {"post": {"responses": {"201": {}}}}}}),
        encoding="utf-8")
    commit(wt, "add POST /b")

    reason = "documented in the ADR, not in diff.md"
    r = cli(repo, wt_root, "gate", task_id,
            "--set", f"public_api_changes_documented=warning:{reason}")
    assert r.returncode == PENDING, r.stdout + r.stderr
    check = status_of(repo, task_id, "public_api_changes_documented")
    assert check["status"] == "warning"
    assert check["note"] == reason
    assert check["detail"] == ("machine-detected API schema changes are not documented "
                               "(diff.md missing/empty)")
    assert check["by"] == "schema-sensor"
    assert check["api_diff"]


# ── a clean sensor leaves the declaration alone ───────────────────────────────
def test_a_clean_sensor_accepts_a_hand_written_pass(tmp_path):
    """The other half: with nothing in the diff for the sensors to find, the five
    sensor-backed criteria are recorded exactly as declared and the gate passes."""
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    task_id, wt = new_task(repo, wt_root)

    (wt / "feature.py").write_text("def f():\n    return 42\n", encoding="utf-8")
    commit(wt, "add a feature")

    sensor_backed = ("no_secret_leak", "no_gate_tampering", "no_injection_markers",
                     "no_destructive_operation", "public_api_changes_documented")
    r = cli(repo, wt_root, "gate", task_id,
            *(arg for name in sensor_backed + DECLARATION_ONLY
              for arg in ("--set", f"{name}=passed")))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "set by hand" not in r.stdout + r.stderr
    assert acceptance(repo, task_id)["status"] == "passed"
    for name in sensor_backed:
        assert status_of(repo, task_id, name)["status"] == "passed"


def test_declaration_only_criteria_are_untouched_by_the_rule(tmp_path):
    """The ten criteria no sensor backs keep behaving exactly as they did, including while
    a sensor is failing another criterion in the same gate: each records the status and
    the detail it was given, `warning:未確認` included."""
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    task_id, wt = new_task(repo, wt_root)

    (wt / ".rig" / "gates.json").write_text('{"extra_criteria": {"standard": []}}\n',
                                            encoding="utf-8")
    commit(wt, "edit the gate config")

    declared = {name: ("warning:未確認" if i % 3 == 2 else
                       "failed:not done" if i % 3 == 1 else "passed:checked")
                for i, name in enumerate(DECLARATION_ONLY)}
    r = cli(repo, wt_root, "gate", task_id,
            *(arg for name, value in declared.items() for arg in ("--set", f"{name}={value}")))
    # The gate is failed because the tamper sensor failed its own criterion — never
    # because a declaration-only criterion was refused.
    assert r.returncode == REJECTED, r.stdout + r.stderr
    assert "set by hand" not in r.stdout + r.stderr
    assert len(DECLARATION_ONLY) == 10
    for name, value in declared.items():
        want_status, want_detail = value.split(":", 1)
        check = status_of(repo, task_id, name)
        assert (check["status"], check["detail"]) == (want_status, want_detail), name
    assert status_of(repo, task_id, "no_gate_tampering")["status"] == "failed"


def test_a_declaration_stricter_than_the_sensor_is_kept(tmp_path):
    """`failed` over a sensor that found nothing is not a contradiction the gate refuses.
    The rule exists to stop a finding being talked down, and an operator who knows
    something the scan cannot see must still be able to write it down."""
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    task_id, wt = new_task(repo, wt_root)

    (wt / "feature.py").write_text("def f():\n    return 42\n", encoding="utf-8")
    commit(wt, "add a feature")

    r = cli(repo, wt_root, "gate", task_id,
            "--set", "no_secret_leak=failed:a credential was committed to another branch")
    assert r.returncode == REJECTED, r.stdout + r.stderr
    assert "set by hand" not in r.stdout + r.stderr
    check = status_of(repo, task_id, "no_secret_leak")
    assert check["status"] == "failed"
    assert check["detail"] == "a credential was committed to another branch"


# ── the one bypass, and what it costs ─────────────────────────────────────────
def test_accept_force_over_a_sensor_failure_is_written_to_the_audit_ledger(tmp_path):
    """Everything above points the operator at `accept --force`, and this is the claim
    that makes that an answer rather than a deflection: the bypass exists, it works, and
    it is written down where an audit looks. `accept.py`'s `accept_force` entry is what
    a gate-time override erased — `gate_status` said `passed`, so `accept` saw nothing to
    force and wrote nothing at all.
    """
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    task_id, wt = new_task(repo, wt_root)
    # `accept` requires a clean main tree, and `new` no longer ignores `.rig/` on its own
    (repo / ".gitignore").write_text(".rig/\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ignore .rig")

    (wt / ".rig" / "gates.json").write_text('{"extra_criteria": {"standard": []}}\n',
                                            encoding="utf-8")
    commit(wt, "edit the gate config")

    # every criterion but the one the sensor failed
    rest = [n for n in DECLARATION_ONLY] + ["no_secret_leak", "no_injection_markers",
                                            "no_destructive_operation",
                                            "public_api_changes_documented"]
    r = cli(repo, wt_root, "gate", task_id, *(a for n in rest for a in ("--set", f"{n}=passed")))
    assert r.returncode == REJECTED, r.stdout + r.stderr
    assert status_of(repo, task_id, "no_gate_tampering")["status"] == "failed"
    (repo / ".rig" / "runs" / task_id / "diff.md").write_text(
        "# diff summary\n\nedits the gate config on purpose.\n", encoding="utf-8")

    # the gate is the verdict, and it is delivered
    refused = cli(repo, wt_root, "accept", task_id)
    assert refused.returncode == REJECTED, refused.stdout + refused.stderr
    assert "no_gate_tampering" in refused.stdout + refused.stderr
    assert not (repo / ".rig" / "audit.jsonl").exists()

    forced = cli(repo, wt_root, "accept", task_id, "--force")
    assert forced.returncode == 0, forced.stdout + forced.stderr

    entries = [json.loads(ln) for ln in
               (repo / ".rig" / "audit.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    forced_entries = [e for e in entries if e["action"] == "accept_force"]
    assert len(forced_entries) == 1
    entry = forced_entries[0]
    assert entry["task_id"] == task_id
    assert entry["gate_status"] == "failed"
    # `bypassed` names the accept requirement that was overridden; `failed_checks` names
    # the criteria underneath it, which is the half an auditor needs.
    assert entry["bypassed"] == ["acceptance_gate_not_failed"]
    assert "no_gate_tampering" in entry["failed_checks"]

    task = json.loads((repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    assert task["forced"] is True and task["status"] == "accepted"
    provenance = json.loads((repo / ".rig" / "runs" / task_id / "provenance.json")
                            .read_text(encoding="utf-8"))
    assert provenance["record"]["forced"] is True
    assert provenance["record"]["gate_status"] == "failed"
    assert {"name": "no_gate_tampering", "status": "failed"} in provenance["record"]["checks"]


def test_a_note_survives_the_same_status_finding_more(tmp_path):
    """Deliberate, and worth a test of its own: only the status decides. A re-evaluation
    that finds more under the SAME status keeps the note, because the operator wrote it
    about the status they declared — which still stands — and what changed is in `detail`
    and in the findings beside it (state.record_sensor_status)."""
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    (repo / "tests" / "test_two.py").write_text("def test_y():\n    assert 2 + 2 == 4\n",
                                                encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "a second test at base")
    task_id, wt = new_task(repo, wt_root)

    (wt / "tests" / "test_app.py").unlink()
    commit(wt, "delete one test")
    reason = "reviewed - both moved to tests/test_new.py"
    assert cli(repo, wt_root, "gate", task_id,
               "--set", f"no_gate_tampering=warning:{reason}").returncode == PENDING
    before = status_of(repo, task_id, "no_gate_tampering")
    assert before["note"] == reason

    # a second warning-grade finding appears; the status does not move
    (wt / "tests" / "test_two.py").write_text("def test_y():\n    pass\n", encoding="utf-8")
    commit(wt, "weaken the other test")
    assert cli(repo, wt_root, "gate", task_id).returncode == PENDING

    after = status_of(repo, task_id, "no_gate_tampering")
    assert after["status"] == "warning"
    assert after["note"] == reason                       # still the operator's
    assert after["tamper_findings"] != before["tamper_findings"]   # the change is here


# ── nothing measured is not a pass ────────────────────────────────────────────
def test_an_empty_criteria_set_does_not_pass_vacuously(tmp_path):
    """The non-vacuity check for everything above: a gate whose criteria list is empty has
    no sensor to contradict and no declaration to refuse, and it must not therefore be a
    gate that passes. `gate_status` answers "skipped" for an empty check list, which
    `cmd_gate` used to carry into task.json as `gate_passed` — a pass nothing was
    measured for. The other half of that hole, a gate whose every criterion was declared
    `skipped` by hand, is pinned by
    `test_a_gate_whose_every_criterion_is_skipped_is_refused_by_accept` below."""
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    task_id, wt = new_task(repo, wt_root)

    (wt / "feature.py").write_text("def f():\n    return 42\n", encoding="utf-8")
    commit(wt, "add a feature")

    acc_path = repo / ".rig" / "runs" / task_id / "acceptance.json"
    acc = json.loads(acc_path.read_text(encoding="utf-8"))
    assert len(acc["checks"]) == 15  # the feature gate, before it is emptied
    acc["checks"] = []
    acc_path.write_text(json.dumps(acc), encoding="utf-8")

    r = cli(repo, wt_root, "gate", task_id)
    assert r.returncode == ERROR, r.stdout + r.stderr
    assert "no criteria" in r.stdout + r.stderr
    assert "PASSED" not in r.stdout
    task = json.loads((repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    assert task["status"] == "running"


def test_a_gate_whose_every_criterion_is_skipped_is_refused_by_accept(tmp_path):
    """`skipped` was the third status accept.py's `gate_ok` counted as satisfaction.

    Measured before the fix: fifteen `--set <criterion>=skipped` pairs, a gate that
    reports SKIPPED, and `accept` squash-merging with `✓ acceptance_gate_not_failed` and
    no `--force` — no audit entry, no `forced: true`, and a provenance record saying the
    task was accepted cleanly. `--set` cannot write `passed` over a sensor (the rule the
    rest of this file pins), but `skipped` is a status every criterion accepts, so the
    whole gate could be declared away one word at a time.

    The empty-criteria refusal above and this one are the two halves of the same hole:
    there, nothing was asked; here, nothing was answered.
    """
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    task_id, wt = new_task(repo, wt_root)
    (repo / ".gitignore").write_text(".rig/\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ignore .rig")
    (wt / "feature.py").write_text("def f():\n    return 42\n", encoding="utf-8")
    commit(wt, "add a feature")

    everything = ("no_secret_leak", "no_gate_tampering", "no_injection_markers",
                  "no_destructive_operation", "public_api_changes_documented",
                  *DECLARATION_ONLY)
    r = cli(repo, wt_root, "gate", task_id,
            *(a for n in everything for a in ("--set", f"{n}=skipped")))
    # 3, the no-verdict code: a gate everybody declined to answer is not a green one, and
    # `gate` must not tell a caller the opposite of what `accept` is about to say.
    assert r.returncode == PENDING, r.stdout + r.stderr
    assert acceptance(repo, task_id)["status"] == "skipped"
    assert "15 criteria skipped (not judged, never a pass)" in r.stdout
    # And the task does not move to `gate_passed`: `eval/capture.py` counts that state with
    # no failed checks as `explicitly_successful`, which would file this as a success.
    task_state = json.loads((repo / ".rig" / "runs" / task_id / "task.json")
                            .read_text(encoding="utf-8"))
    assert task_state["status"] == "running"
    (repo / ".rig" / "runs" / task_id / "diff.md").write_text(
        "# diff summary\n\nthe task's own work.\n", encoding="utf-8")

    refused = cli(repo, wt_root, "accept", task_id)
    out = refused.stdout + refused.stderr
    # A verdict on the work, so `reject`'s 1 — not the usage-error 2 a refused `--set`
    # takes, and not a 0 with a merge behind it.
    assert refused.returncode == REJECTED, out
    assert "✗ acceptance_gate_not_failed" in out
    assert "the gate judged nothing" in out
    # The refusal names no unmet criterion, because there is none to name: every check
    # carries the status the operator chose for it.
    assert "unmet:" not in out
    # And it stops at the fact. The tail that used to follow it ("…and a gate that judged
    # nothing is not a gate that was met") restated the sentence it was attached to.
    assert "is not a gate that was met" not in out
    assert _git_out(repo, "status", "--porcelain") == ""

    # And the one door past it is the audited one.
    forced = cli(repo, wt_root, "accept", task_id, "--force")
    assert forced.returncode == 0, forced.stdout + forced.stderr
    audit = [json.loads(line) for line in
             (repo / ".rig" / "audit.jsonl").read_text(encoding="utf-8").splitlines() if line]
    entry = next(e for e in audit if e["action"] == "accept_force")
    assert entry["gate_status"] == "skipped"
    assert "acceptance_gate_not_failed" in entry["bypassed"]
    task = json.loads((repo / ".rig" / "runs" / task_id / "task.json")
                      .read_text(encoding="utf-8"))
    assert task["forced"] is True


# ── a verdict is about the commits it was measured against ───────────────────
def _repo_ready_for_accept(tmp_path):
    """A repo whose main tree is clean and ignores `.rig/`, plus a task with one commit."""
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    task_id, wt = new_task(repo, wt_root)
    (repo / ".gitignore").write_text(".rig/\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ignore .rig")
    (wt / "app.py").write_text("x = 2\n", encoding="utf-8")
    commit(wt, "the task's own work")
    return repo, wt_root, task_id, wt


def test_a_gate_that_judged_an_older_head_is_not_spent_on_a_newer_one(tmp_path):
    """`accept` squashes the branch as it stands; the gate judged it as it stood.

    Nothing connected the two. Measured before the fix: pass the whole gate, commit again
    in the worktree, and `accept` applied the second commit under a verdict — sensors
    included — that had never seen it. acceptance.json recorded `checked_at` and no head,
    so there was nothing to compare and no way for a later reader to tell which commits
    the verdict was about.

    This is a head-identity check, not a sensor re-run: the criteria keep exactly the
    statuses `gate` wrote, and re-running `gate` is what clears it.
    """
    repo, wt_root, task_id, wt = _repo_ready_for_accept(tmp_path)
    _ready_to_accept(repo, wt_root, task_id)

    judged = acceptance(repo, task_id)["evaluated_head"]
    assert judged == _git_out(wt, "rev-parse", "HEAD").strip()

    (wt / "app.py").write_text("x = 3\n", encoding="utf-8")
    commit(wt, "one more change the gate never saw")
    moved = _git_out(wt, "rev-parse", "HEAD").strip()
    assert moved != judged

    refused = cli(repo, wt_root, "accept", task_id)
    out = refused.stdout + refused.stderr
    assert refused.returncode == REJECTED, out
    assert "✗ gate_judged_this_head" in out
    # The branch moved with the worktree, so the refusal is about the ref that is squashed.
    assert f"judged {judged[:12]}" in out
    assert f"which accept squashes, is at {moved[:12]}" in out
    assert "Re-run" in out and "gate" in out
    # The gate's own answers are untouched — this refusal is about identity, not verdicts.
    assert acceptance(repo, task_id)["status"] == "passed"
    assert _git_out(repo, "status", "--porcelain") == ""

    # Re-running the gate over the head that is actually about to be squashed clears it.
    _ready_to_accept(repo, wt_root, task_id)
    assert acceptance(repo, task_id)["evaluated_head"] == moved
    accepted = cli(repo, wt_root, "accept", task_id)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def test_forcing_past_a_moved_head_writes_both_shas_into_the_audit_ledger(tmp_path):
    """The bypass stays accountable, and an auditor reading it later does not have to go
    back to a worktree that may no longer exist to learn which commits were measured."""
    repo, wt_root, task_id, wt = _repo_ready_for_accept(tmp_path)
    _ready_to_accept(repo, wt_root, task_id)
    judged = acceptance(repo, task_id)["evaluated_head"]
    (wt / "app.py").write_text("x = 3\n", encoding="utf-8")
    commit(wt, "one more change the gate never saw")
    moved = _git_out(wt, "rev-parse", "HEAD").strip()

    forced = cli(repo, wt_root, "accept", task_id, "--force")
    assert forced.returncode == 0, forced.stdout + forced.stderr
    entry = next(json.loads(line) for line in
                 (repo / ".rig" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
                 if line and json.loads(line)["action"] == "accept_force")
    assert "gate_judged_this_head" in entry["bypassed"]
    assert entry["evaluated_head"] == judged
    assert entry["worktree_head"] == moved


def test_a_run_whose_acceptance_json_records_no_head_is_unknown_not_matching(tmp_path):
    """An acceptance.json written before the gate recorded a head — an older run, or one
    a tool wrote by hand. rig cannot show a difference it never measured, and must not
    therefore report a match: unknown is refused, and `--force` is the same one door."""
    repo, wt_root, task_id, wt = _repo_ready_for_accept(tmp_path)
    _ready_to_accept(repo, wt_root, task_id)

    acc_path = repo / ".rig" / "runs" / task_id / "acceptance.json"
    acc = json.loads(acc_path.read_text(encoding="utf-8"))
    assert acc.pop("evaluated_head")
    acc_path.write_text(json.dumps(acc), encoding="utf-8")

    refused = cli(repo, wt_root, "accept", task_id)
    out = refused.stdout + refused.stderr
    assert refused.returncode == REJECTED, out
    assert "✗ gate_judged_this_head" in out
    assert "records no head" in out
    assert "rig treats that as unmet, not as a match" in out
    assert _git_out(repo, "status", "--porcelain") == ""

    forced = cli(repo, wt_root, "accept", task_id, "--force")
    assert forced.returncode == 0, forced.stdout + forced.stderr


def test_a_branch_moved_under_a_detached_worktree_is_refused(tmp_path):
    """The measured bypass: the head check guarded the wrong ref.

    `accept` squashes `task["branch"]`. The first version of this check compared the gate's
    recorded head with the WORKTREE's HEAD, and those are two refs. Detach the worktree at
    the judged sha and point the branch at a commit the gate never saw, and every signal
    the check looked at agreed: the worktree was clean, its HEAD equalled `evaluated_head`,
    `✓ gate_judged_this_head` printed, accept exited 0, and the unmeasured file was staged
    into the main tree with no audit entry behind it.
    """
    repo, wt_root, task_id, wt = _repo_ready_for_accept(tmp_path)
    _ready_to_accept(repo, wt_root, task_id)
    judged = _git_out(wt, "rev-parse", "HEAD").strip()
    assert acceptance(repo, task_id)["evaluated_head"] == judged

    # The reviewer's sequence, exactly: commit the unmeasured change, put the worktree back
    # on the judged commit, and move the branch to the new one.
    (wt / "evil.py").write_text("# never measured by any sensor\n", encoding="utf-8")
    commit(wt, "a change the gate never saw")
    evil = _git_out(wt, "rev-parse", "HEAD").strip()
    _git(wt, "checkout", "--detach", judged)
    _git(repo, "branch", "-f", f"rig/{task_id}", evil)
    assert _git_out(wt, "rev-parse", "HEAD").strip() == judged     # the old check's input
    assert _git_out(repo, "rev-parse", f"rig/{task_id}").strip() == evil   # what gets squashed

    refused = cli(repo, wt_root, "accept", task_id)
    out = refused.stdout + refused.stderr
    assert refused.returncode == REJECTED, out
    assert "✗ gate_judged_this_head" in out
    # All three shas, because the operator cannot act on a difference they cannot see.
    assert judged[:12] in out and evil[:12] in out
    assert "accept squashes the branch, not the worktree HEAD" in out
    # Nothing reached the main tree, and nothing was recorded as a force.
    assert _git_out(repo, "status", "--porcelain") == ""
    assert not (repo / ".rig" / "audit.jsonl").exists()
    assert not (repo / "evil.py").exists()


def test_a_worktree_that_is_not_on_its_branch_tip_is_refused_on_its_own(tmp_path):
    """The narrower half of the same fact, with the branch left where the gate found it.

    The worktree is detached one commit behind its own branch tip. The gate judged the
    branch tip, so `evaluated_head` and the tip agree — and the tree the operator is
    looking at is still not the change being merged, which is a refusal of its own.
    """
    repo, wt_root, task_id, wt = _repo_ready_for_accept(tmp_path)
    behind = _git_out(wt, "rev-parse", "HEAD").strip()
    (wt / "second.py").write_text("x = 2\n", encoding="utf-8")
    commit(wt, "a second commit")
    tip = _git_out(wt, "rev-parse", "HEAD").strip()
    _ready_to_accept(repo, wt_root, task_id)
    assert acceptance(repo, task_id)["evaluated_head"] == tip

    _git(wt, "checkout", "--detach", behind)

    refused = cli(repo, wt_root, "accept", task_id)
    out = refused.stdout + refused.stderr
    assert refused.returncode == REJECTED, out
    assert "✗ gate_judged_this_head" in out
    assert behind[:12] in out and tip[:12] in out
    assert "are not the same commit" in out
    assert _git_out(repo, "status", "--porcelain") == ""


def test_a_gate_run_against_a_detached_worktree_records_the_branch_it_was_not_about(tmp_path):
    """What `gate` itself measured, when the two refs already disagreed at evaluation time.

    The verdict is about the worktree diff, so it is about the worktree's HEAD. Recording
    the branch tip alongside it is what lets the refusal say *why* the verdict cannot be
    spent here, instead of showing a difference with no account of where it came from.
    """
    repo, wt_root, task_id, wt = _repo_ready_for_accept(tmp_path)
    judged = _git_out(wt, "rev-parse", "HEAD").strip()
    (wt / "later.py").write_text("x = 9\n", encoding="utf-8")
    commit(wt, "a later commit")
    later = _git_out(wt, "rev-parse", "HEAD").strip()
    _git(wt, "checkout", "--detach", judged)

    _ready_to_accept(repo, wt_root, task_id)
    acc = acceptance(repo, task_id)
    assert acc["evaluated_head"] == judged
    assert acc["evaluated_branch_tip"] == later

    refused = cli(repo, wt_root, "accept", task_id)
    out = refused.stdout + refused.stderr
    assert refused.returncode == REJECTED, out
    assert "The gate itself measured a detached worktree" in out

    # Back on the branch, a re-run of `gate` clears the field and the refusal with it.
    _git(wt, "checkout", f"rig/{task_id}")
    _ready_to_accept(repo, wt_root, task_id)
    assert "evaluated_branch_tip" not in acceptance(repo, task_id)
    assert cli(repo, wt_root, "accept", task_id).returncode == 0


def test_a_no_worktree_run_records_the_main_trees_head_and_accepts(tmp_path):
    """`state.task_head`'s fallback, driven through the CLI instead of a hand-written fixture.

    A `--no-worktree` task has no worktree and no branch, so the head the gate records is
    the main tree's and there is nothing for the branch-tip comparison to resolve. Every
    other test of this file reaches that branch by writing `worktree_path` into task.json
    by hand, which proves the fixture and not the code.
    """
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    (repo / ".gitignore").write_text(".rig/\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ignore .rig")
    r = cli(repo, wt_root, "new", "a change made in the main tree", "--type", "feature",
            "--slug", "no-worktree", "--no-worktree")
    assert r.returncode == 0, r.stdout + r.stderr
    task_id = re.search(r"task_id: (\S+)", r.stdout).group(1)
    task = json.loads((repo / ".rig" / "runs" / task_id / "task.json").read_text(encoding="utf-8"))
    assert task["worktree_path"] is None and task["branch"] is None

    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "the work, in the main tree")

    _ready_to_accept(repo, wt_root, task_id)
    assert acceptance(repo, task_id)["evaluated_head"] == _git_out(repo, "rev-parse", "HEAD").strip()

    # accept gets past the checklist — `gate_judged_this_head` is met — and stops on the
    # one thing that is actually missing, which is a worktree to take a diff from.
    r = cli(repo, wt_root, "accept", task_id)
    out = r.stdout + r.stderr
    assert "✓ gate_judged_this_head" in out
    assert "has no worktree" in out


def test_the_squash_is_given_the_sha_the_check_approved_not_the_branch_name(tmp_path, monkeypatch):
    """The check and the sink have to read one value, not the same name twice.

    `gate_judged_this_head` resolves the branch to a sha and compares it. Everything
    between that and `git merge --squash` is a window in which the ref can move, and
    passing the NAME to the merge made the sink resolve it a second time. Measured at a
    0.25s delay, one attempt in one: `git update-ref refs/heads/rig/<task> <evil>` inside
    that window staged an unmeasured commit, rc=0, no audit line.

    Driven in process rather than through the CLI because the window is the point: the
    `git` helper is wrapped so the ref moves *between* the two reads, deterministically,
    instead of hoping a subprocess lands inside ~40ms.
    """
    import argparse as _argparse

    from rig_workbench.workbench import accept as accept_mod

    repo, wt_root, task_id, wt = _repo_ready_for_accept(tmp_path)
    _ready_to_accept(repo, wt_root, task_id)
    judged = _git_out(wt, "rev-parse", "HEAD").strip()

    # A commit the gate never saw, parked on a detached ref so the branch still points at
    # the judged commit when `accept` runs its check.
    _git(wt, "checkout", "--detach", judged)
    (wt / "evil.py").write_text("# never measured by any sensor\n", encoding="utf-8")
    commit(wt, "the racer's commit")
    evil = _git_out(wt, "rev-parse", "HEAD").strip()
    _git(wt, "checkout", f"rig/{task_id}")
    assert _git_out(repo, "rev-parse", f"rig/{task_id}").strip() == judged

    real_git, calls = accept_mod.git, []

    def racing_git(argv, **kwargs):
        calls.append(list(argv))
        if argv[:2] == ["merge", "--squash"]:
            # The window, closed by hand: the ref moves after the check approved it and
            # before the merge runs.
            _git(repo, "update-ref", f"refs/heads/rig/{task_id}", evil)
        return real_git(argv, **kwargs)

    monkeypatch.setattr(accept_mod, "git", racing_git)
    monkeypatch.chdir(repo)
    accept_mod.cmd_accept(_argparse.Namespace(task_id=task_id, force=False))

    # The argv the sink was given is the sha the check approved, not the name it read.
    squash = next(c for c in calls if c[:2] == ["merge", "--squash"])
    assert squash == ["merge", "--squash", judged], squash
    assert f"rig/{task_id}" not in squash

    # And the measured outcome: the racer's commit did not reach the main tree.
    staged = _git_out(repo, "diff", "--staged", "--name-only").split()
    assert "evil.py" not in staged, staged
    assert not (repo / "evil.py").exists()


# ── a criterion nobody judged is not a criterion that passed ─────────────────
def test_one_passed_and_the_rest_skipped_is_not_a_passed_gate(tmp_path):
    """The narrower bypass under the all-skipped one, and the reason the fix is in
    `gate_status` rather than in another refusal.

    Measured: `--set` one criterion `passed` and the other fourteen `skipped`, and the gate
    scored `passed` outright — `accept` then applied it with nothing in the output, nothing
    in the audit ledger and nothing in provenance to say that fourteen of the fifteen were
    never judged. Blocking it would have been wrong (a skip is often the honest answer, and
    "warning never blocks accept" is a rule this run does not get to rewrite); making it
    unmissable is the fix.
    """
    repo, wt_root, task_id, wt = _repo_ready_for_accept(tmp_path)
    everything = ("no_secret_leak", "no_gate_tampering", "no_injection_markers",
                  "no_destructive_operation", "public_api_changes_documented",
                  *DECLARATION_ONLY)
    declared = {n: ("passed" if n == "task_intent_satisfied" else "skipped") for n in everything}
    r = cli(repo, wt_root, "gate", task_id,
            *(a for n, v in declared.items() for a in ("--set", f"{n}={v}")))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[PASSED_WITH_WARNINGS]" in r.stdout
    assert "14 criteria skipped (not judged, never a pass)" in r.stdout
    assert acceptance(repo, task_id)["status"] == "passed_with_warnings"
    (repo / ".rig" / "runs" / task_id / "diff.md").write_text(
        "# diff summary\n\nthe task's own work.\n", encoding="utf-8")

    # It still accepts unforced — a warning has never blocked accept — and it says so.
    accepted = cli(repo, wt_root, "accept", task_id)
    out = accepted.stdout + accepted.stderr
    assert accepted.returncode == 0, out
    assert "14 criteria nobody judged" in out
    assert "no_secret_leak" in out
    assert not (repo / ".rig" / "audit.jsonl").exists()          # not a force

    # And the signed record carries the names, where a later reader looks.
    prov = json.loads((repo / ".rig" / "runs" / task_id / "provenance.json")
                      .read_text(encoding="utf-8"))["record"]
    assert prov["gate_status"] == "passed_with_warnings"
    assert prov["skipped_criteria"] == sorted(n for n, v in declared.items() if v == "skipped")
    assert "task_intent_satisfied" not in prov["skipped_criteria"]


# ── a squash that never ran is not a conflict ────────────────────────────────
#
# `accept --force` above lands its change through `git merge --squash`, and the repo
# fixture carries a repo-local identity for exactly that reason (see `make_repo`). The
# two tests below are the cases where that merge returns non-zero *without* a conflict.
# `accept` used to call every one of them "squash merge conflicted (divergence from
# base)" and advise a rebase: measured on a runner with no committer identity, an
# operator was told to rebase a branch whose only problem was an unset `user.name`. The
# conflict branch itself is drift and is pinned in tests/test_base_drift.py.


def _ready_to_accept(repo, wt_root, task_id):
    """Pass the gate for real and write the diff summary `accept` requires."""
    everything = ("no_secret_leak", "no_gate_tampering", "no_injection_markers",
                  "no_destructive_operation", "public_api_changes_documented",
                  *DECLARATION_ONLY)
    r = cli(repo, wt_root, "gate", task_id,
            *(a for n in everything for a in ("--set", f"{n}=passed")))
    assert r.returncode == 0, r.stdout + r.stderr
    (repo / ".rig" / "runs" / task_id / "diff.md").write_text(
        "# diff summary\n\nthe task's own work.\n", encoding="utf-8")


def test_a_squash_with_no_committer_identity_says_so_and_advises_git_config(tmp_path):
    """The measured case, inverted: `make_repo`'s identity line deleted. No repo-local
    config, an empty HOME and `GIT_CONFIG_GLOBAL=/dev/null`, and a branch that merges
    cleanly — so git refuses to run the merge at all (exit 128, "Please tell me who you
    are") and there is nothing whatsoever to resolve in the worktree."""
    repo, wt_root = tmp_path / "repo", tmp_path / "wt"
    repo.mkdir()
    # `_git` carries the identity on the command line, so the setup commits land and the
    # repository itself still holds none: a CI runner after a bare `git init`.
    _git(repo, "init", "-q")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    task_id, wt = new_task(repo, wt_root)
    # `accept` requires a clean main tree, and `new` no longer ignores `.rig/` on its own —
    # without the entry, `add -A` commits the run state instead of ignoring it.
    (repo / ".gitignore").write_text(".rig/\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ignore .rig")
    (wt / "app.py").write_text("x = 2\n", encoding="utf-8")
    commit(wt, "the task's own work")
    _ready_to_accept(repo, wt_root, task_id)

    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    env = dict(os.environ, RIG_WORKTREE_ROOT=str(wt_root), HOME=str(empty_home),
               GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    r = subprocess.run([sys.executable, str(WORKBENCH), "accept", task_id],
                       cwd=repo, capture_output=True, text=True, timeout=60, env=env)
    out = r.stdout + r.stderr
    assert r.returncode == ERROR, out
    assert "no committer identity" in out
    assert "empty ident name" in out and "Please tell me who you are" in out
    assert f"git -C {repo} config user.name" in out
    assert f"git -C {repo} config user.email" in out
    # Not a conflict, and no trip into the worktree: both were the old message's advice.
    assert "conflicted" not in out
    assert "rebase" not in out and "merge master" not in out
    assert _git_out(repo, "status", "--porcelain") == ""


def test_any_other_squash_failure_surfaces_git_stderr_and_the_exit_code(tmp_path):
    """Neither a conflict nor an identity: a locked index, which is what a crashed git or
    a second process leaves behind. rig has nothing useful to say about that, so it says
    what git said and what git returned rather than inventing a third diagnosis."""
    repo, wt_root = make_repo(tmp_path), tmp_path / "wt"
    task_id, wt = new_task(repo, wt_root)
    # `accept` requires a clean main tree, and `new` no longer ignores `.rig/` on its own —
    # without the entry, `add -A` commits the run state instead of ignoring it.
    (repo / ".gitignore").write_text(".rig/\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "ignore .rig")
    (wt / "app.py").write_text("x = 2\n", encoding="utf-8")
    commit(wt, "the task's own work")
    _ready_to_accept(repo, wt_root, task_id)

    lock = repo / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")
    try:
        r = cli(repo, wt_root, "accept", task_id)
    finally:
        lock.unlink(missing_ok=True)

    out = r.stdout + r.stderr
    assert r.returncode == ERROR, out
    assert "not from a conflict or a missing identity" in out
    assert "Unable to write index" in out          # git's own words, verbatim
    assert "git exit 1" in out                     # and the status it exited with
    assert "conflicted in" not in out and "rebase" not in out
    assert _git_out(repo, "status", "--porcelain") == ""
