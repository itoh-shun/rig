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
    assert r.returncode == 0, r.stdout + r.stderr   # warning-grade: the gate is pending, not failed
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
    assert r.returncode == 0, r.stdout + r.stderr          # agreement, so no refusal
    for _ in range(3):                                     # (a) three more bare runs
        assert cli(repo, wt_root, "gate", task_id).returncode == 0
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
    assert r.returncode == 0, r.stdout + r.stderr
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
               "--set", f"no_gate_tampering=warning:{reason}").returncode == 0
    before = status_of(repo, task_id, "no_gate_tampering")
    assert before["note"] == reason

    # a second warning-grade finding appears; the status does not move
    (wt / "tests" / "test_two.py").write_text("def test_y():\n    pass\n", encoding="utf-8")
    commit(wt, "weaken the other test")
    assert cli(repo, wt_root, "gate", task_id).returncode == 0

    after = status_of(repo, task_id, "no_gate_tampering")
    assert after["status"] == "warning"
    assert after["note"] == reason                       # still the operator's
    assert after["tamper_findings"] != before["tamper_findings"]   # the change is here


# ── nothing measured is not a pass ────────────────────────────────────────────
def test_an_empty_criteria_set_does_not_pass_vacuously(tmp_path):
    """The non-vacuity check for everything above: a gate whose criteria list is empty has
    no sensor to contradict and no declaration to refuse, and it must not therefore be a
    gate that passes. `gate_status` answers "skipped" for an empty check list, which
    `cmd_gate` used to carry into task.json as `gate_passed` and which accept.py's
    `gate_ok` still counts as met — a pass nothing was measured for."""
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
