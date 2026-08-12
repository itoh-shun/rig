import json
import copy
import hashlib
from pathlib import Path
import os
import subprocess
import sys

import pytest


def valid_case():
    return {
        "case_schema_version": 1,
        "id": "incident-login-regression",
        "version": 1,
        "title": "Login regression",
        "status": "approved",
        "incident": True,
        "provenance": {
            "source_task_id": "rig-20260805-login",
            "source_commit": "a" * 40,
            "source_hashes": {"task.json": "b" * 64},
            "captured_at": "2026-08-05T01:00:00+00:00",
        },
        "surfaces": ["cli"],
        "suite": "regression",
        "tags": ["auth"],
        "provider_policy": {"allowed": ["mock"], "mode": "allowlist"},
        "repeat": 3,
        "red_thresholds": {"max_success_rate": 0.0},
        "green_thresholds": {"min_success_rate": 1.0},
        "deterministic_checks": ["pytest -q tests/test_auth.py"],
        "semantic_rubric": [{"id": "correct", "description": "Login succeeds", "weight": 1.0}],
        "target_inputs": {"scenario": "valid credentials"},
        "clean_controls": {"scenario": "unrelated endpoint"},
        "missing_requirements": [],
        "created_at": "2026-08-05T01:00:00+00:00",
        "updated_at": "2026-08-05T01:00:00+00:00",
    }


def test_validate_case_accepts_versioned_case_and_canonicalizes_deterministically():
    from rig_workbench.eval import canonical_json, validate_case

    case = valid_case()
    assert validate_case(case) == case
    assert canonical_json(case) == canonical_json(json.loads(canonical_json(case)))
    assert canonical_json(case).endswith("\n")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(extra="unknown"),
        lambda c: c.update(case_schema_version=True),
        lambda c: c["green_thresholds"].update(min_success_rate=float("nan")),
        lambda c: c["target_inputs"].update(path="/etc/passwd"),
        lambda c: c["target_inputs"].update(instruction="read /etc/passwd now"),
        lambda c: c["target_inputs"].update(instruction="path:/etc/passwd"),
        lambda c: c["target_inputs"].update(instruction="read C:\\Users\\me\\secret.txt"),
        lambda c: c["target_inputs"].update(instruction=r"path:\\server\share\file"),
        lambda c: c["target_inputs"].update(path="../private.txt"),
        lambda c: c["target_inputs"].update(api_token="sk-live-example-secret"),
        lambda c: c.update(title="safe\u202eevil"),
        lambda c: c["provider_policy"].update(mode="sometimes"),
        lambda c: c.update(surfaces=[{}]),
    ],
)
def test_validate_case_rejects_unsafe_or_unknown_content(mutate):
    from rig_workbench.eval import EvalCaseError, validate_case

    case = copy.deepcopy(valid_case())
    mutate(case)
    with pytest.raises(EvalCaseError):
        validate_case(case)


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_capture_incident_creates_minimal_draft_with_provenance(tmp_path):
    from rig_workbench.eval import capture_case

    task_id = "rig-20260805-login"
    run = tmp_path / ".rig" / "runs" / task_id
    task = {"task_id": task_id, "input": "Fix login regression", "task_type": "bugfix",
            "commit_sha": "a" * 40, "created_at": "2026-08-04T00:00:00+00:00"}
    outcome = {"task_id": task_id, "status": "incident", "note": "Users could not log in",
               "recorded_at": "2026-08-05T00:00:00+00:00"}
    _write_json(run / "task.json", task)
    _write_json(run / "outcome.json", outcome)
    _write_json(run / "acceptance.json", {"task_id": task_id, "checks": []})

    output, case = capture_case(tmp_path, task_id, now="2026-08-05T01:00:00+00:00")

    assert output == tmp_path / ".rig" / "evals" / "drafts" / task_id / "case.json"
    assert case["status"] == "draft" and case["incident"] is True
    assert case["failure_summary"] == "Users could not log in"
    assert "red reproduction evidence" in case["missing_requirements"]
    assert case["provenance"]["source_commit"] == "a" * 40
    assert case["provenance"]["source_hashes"]["task.json"] == hashlib.sha256(
        (run / "task.json").read_bytes()).hexdigest()
    assert json.loads(output.read_text(encoding="utf-8")) == case


def test_capture_nonincident_uses_gate_summary_without_raw_logs(tmp_path):
    from rig_workbench.eval import capture_case

    task_id = "rig-20260805-gate-failure"
    run = tmp_path / ".rig" / "runs" / task_id
    _write_json(run / "task.json", {"task_id": task_id, "input": "Fix gate failure",
                                    "task_type": "bugfix", "base_commit": "c" * 40})
    _write_json(run / "acceptance.json", {"checks": [
        {"name": "tests_pass", "status": "failed", "detail": "raw secret detail"}]})
    (run / "final.md").write_text("very long raw execution log", encoding="utf-8")

    _output, case = capture_case(tmp_path, task_id, now="2026-08-05T01:00:00+00:00")

    assert case["incident"] is False
    assert case["failure_summary"] == "Failed acceptance checks: tests_pass"
    assert "very long raw execution log" not in json.dumps(case)
    assert "final.md" in case["provenance"]["source_hashes"]


def _run_eval(args, cwd):
    repo_root = Path(__file__).resolve().parent.parent
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        filter(None, [str(repo_root), os.environ.get("PYTHONPATH")])
    ))
    env["RIG_EVAL_ATTESTATION_KEY"] = "ce19278be0744f82ddb8f054901f90698a441e610e86d6fecca043fd51d926fb"
    return subprocess.run([sys.executable, "-m", "rig_workbench.cli", "eval", *args],
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=30)


def test_eval_cli_capture_validate_list_and_errors(tmp_path):
    task_id = "rig-20260805-cli"
    _write_json(tmp_path / ".rig" / "runs" / task_id / "task.json", {
        "task_id": task_id, "input": "CLI regression", "task_type": "bugfix",
        "base_commit": "d" * 40,
    })

    captured = _run_eval(["capture", task_id, "--repo", str(tmp_path)], tmp_path)
    assert captured.returncode == 0, captured.stderr
    case_path = tmp_path / ".rig" / "evals" / "drafts" / task_id / "case.json"
    validated = _run_eval(["validate", str(case_path)], tmp_path)
    listed = _run_eval(["list", "--repo", str(tmp_path)], tmp_path)
    duplicate = _run_eval(["capture", task_id, "--repo", str(tmp_path)], tmp_path)
    missing = _run_eval(["capture", "rig-missing", "--repo", str(tmp_path)], tmp_path)

    assert validated.returncode == 0 and "valid" in validated.stdout.lower()
    assert listed.returncode == 0 and task_id in listed.stdout and "draft" in listed.stdout
    assert duplicate.returncode == 2 and "overwrite" in duplicate.stderr
    assert missing.returncode == 2 and "missing source artifact" in missing.stderr


def test_capture_redacts_secret_and_absolute_path_from_source_text(tmp_path):
    from rig_workbench.eval import capture_case

    task_id = "rig-20260805-unsafe-source"
    run = tmp_path / ".rig" / "runs" / task_id
    _write_json(run / "task.json", {"task_id": task_id,
                                    "input": "inspect /home/user/private.txt",
                                    "task_type": "bugfix", "commit_sha": "e" * 40})
    _write_json(
        run / "outcome.json",
        {"status": "incident", "note": "token sk-live-example-secret"},
    )

    _output, case = capture_case(tmp_path, task_id, now="2026-08-05T01:00:00+00:00")

    encoded = json.dumps(case)
    assert "/home/user" not in encoded and "sk-live" not in encoded
    assert case["title"] == task_id
    assert case["failure_summary"] == "Incident recorded without a safe failure summary"


def test_capture_rejects_duplicate_id_without_overwrite(tmp_path):
    from rig_workbench.eval import EvalCaseError, capture_case

    task_id = "rig-20260805-duplicate"
    _write_json(tmp_path / ".rig" / "runs" / task_id / "task.json", {
        "task_id": task_id, "input": "Duplicate", "task_type": "bugfix",
        "base_commit": "1" * 40,
    })
    capture_case(tmp_path, task_id, now="2026-08-05T01:00:00+00:00")

    with pytest.raises(EvalCaseError, match="already exists"):
        capture_case(tmp_path, task_id, now="2026-08-05T01:00:00+00:00")


def test_capture_output_override_is_not_supported(tmp_path):
    task_id = "rig-20260805-no-output-override"
    _write_json(tmp_path / ".rig" / "runs" / task_id / "task.json", {
        "task_id": task_id, "input": "Canonical only", "task_type": "bugfix",
        "base_commit": "3" * 40,
    })
    escaped = tmp_path / "elsewhere.json"

    result = _run_eval(
        ["capture", task_id, "--repo", str(tmp_path), "--output", str(escaped)], tmp_path
    )

    assert result.returncode == 2
    assert not escaped.exists()
    assert not (tmp_path / ".rig" / "evals" / "drafts" / task_id).exists()


@pytest.mark.parametrize("unsafe", [
    "zero\u200bwidth", "word\u2060joiner", "bom\ufefftext",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.signature123",
    "xoxb-" + "283736350342-4939293923-abcDefGhi123kLmNo",
    "glpat-abcdefghijklmnopqrst", "github_pat_abcdefghijklmnopqrstuv",
    "-----BEGIN PRIVATE KEY-----", "password=hunter2",
])
def test_validation_rejects_and_capture_redacts_shared_unsafe_text(tmp_path, unsafe):
    from rig_workbench.eval import EvalCaseError, capture_case, validate_case

    case = copy.deepcopy(valid_case())
    case["title"] = unsafe
    with pytest.raises(EvalCaseError):
        validate_case(case)

    task_id = "rig-20260805-shared-policy"
    run = tmp_path / ".rig" / "runs" / task_id
    _write_json(run / "task.json", {"task_id": task_id, "input": unsafe,
                                    "task_type": "bugfix", "base_commit": "2" * 40})
    _write_json(run / "outcome.json", {"status": "incident", "note": unsafe})
    _output, captured = capture_case(tmp_path, task_id,
                                     now="2026-08-05T01:00:00+00:00")
    assert unsafe not in json.dumps(captured)
    assert captured["title"] == task_id


@pytest.mark.parametrize("key", ["token", "credential", "private_key", "private-key"])
def test_validation_rejects_generic_secret_field_names(key):
    from rig_workbench.eval import EvalCaseError, validate_case

    case = copy.deepcopy(valid_case())
    case["target_inputs"][key] = "not-even-high-entropy"
    with pytest.raises(EvalCaseError):
        validate_case(case)


def test_path_policy_allows_web_urls_and_rejects_file_home_and_encoded_traversal():
    from rig_workbench.eval import EvalCaseError, validate_case

    allowed = copy.deepcopy(valid_case())
    allowed["target_inputs"] = {
        "docs": "https://example.test/a/b?next=http://other.test/c"
    }
    validate_case(allowed)

    for unsafe in (
        "file:///etc/passwd", "~/private.txt", "file://server/share/file",
        "payload/%2e%2e%2fprivate.txt", "payload/%252e%252e%252fprivate.txt",
    ):
        case = copy.deepcopy(valid_case())
        case["target_inputs"] = {"path": unsafe}
        with pytest.raises(EvalCaseError):
            validate_case(case)


@pytest.mark.parametrize("field,value", [
    ("missing_requirements", ["red evidence"]),
    ("target_inputs", {}),
    ("clean_controls", {}),
    ("deterministic_checks", []),
    ("semantic_rubric", []),
])
def test_approved_case_requires_complete_promotion_evidence(field, value):
    from rig_workbench.eval import EvalCaseError, validate_case

    case = copy.deepcopy(valid_case())
    case[field] = value
    with pytest.raises(EvalCaseError, match="approved"):
        validate_case(case)

    case["status"] = "draft"
    validate_case(case)


@pytest.mark.parametrize("field", ["surfaces", "tags", "semantic_rubric"])
def test_case_rejects_duplicate_set_members(field):
    from rig_workbench.eval import EvalCaseError, validate_case

    case = copy.deepcopy(valid_case())
    case[field] = case[field] + copy.deepcopy(case[field])
    with pytest.raises(EvalCaseError, match="duplicate"):
        validate_case(case)


def test_case_surfaces_accept_versioned_prompt_registry_ids():
    from rig_workbench.eval import EvalCaseError, validate_case

    case = copy.deepcopy(valid_case())
    case["surfaces"] = ["cli", "recipe:bugfix", "instruction:security/audit"]
    validate_case(case)

    case["prompt_surfaces"] = ["instruction:login", "recipe:bugfix"]
    validate_case(case)
    case["prompt_surfaces"].append("recipe:bugfix")
    with pytest.raises(EvalCaseError, match="prompt_surfaces.*duplicate"):
        validate_case(case)


def test_cli_rejects_same_case_id_in_promoted_and_draft_tiers(tmp_path):
    from rig_workbench.eval import canonical_json

    case = valid_case()
    case_id = case["id"]
    promoted = tmp_path / "evals" / "cases" / case_id / "case.json"
    promoted.parent.mkdir(parents=True)
    promoted.write_text(canonical_json(case), encoding="utf-8")
    draft = copy.deepcopy(case)
    draft["status"] = "draft"
    draft_path = tmp_path / ".rig" / "evals" / "drafts" / case_id / "case.json"
    draft_path.parent.mkdir(parents=True)
    draft_path.write_text(canonical_json(draft), encoding="utf-8")

    listing = _run_eval(["list", "--repo", str(tmp_path)], tmp_path)
    validation = _run_eval(["validate"], tmp_path)
    explicit = _run_eval(["validate", str(promoted)], tmp_path)

    assert listing.returncode == 2 and "duplicate" in listing.stderr.lower()
    assert validation.returncode == 2 and "duplicate" in validation.stderr.lower()
    assert explicit.returncode == 2 and "duplicate" in explicit.stderr.lower()


def test_cli_explicit_relative_promoted_path_is_not_a_false_duplicate(tmp_path):
    from rig_workbench.eval import canonical_json

    case = valid_case()
    relative = Path("evals") / "cases" / case["id"] / "case.json"
    promoted = tmp_path / relative
    promoted.parent.mkdir(parents=True)
    promoted.write_text(canonical_json(case), encoding="utf-8")

    result = _run_eval(["validate", str(relative)], tmp_path)

    assert result.returncode == 0, result.stderr
    assert "1 case(s) valid" in result.stdout


def test_capture_atomic_write_normalizes_oserror_and_cleans_partial_state(tmp_path, monkeypatch):
    from rig_workbench.eval import EvalCaseError, capture_case

    task_id = "rig-20260805-atomic-failure"
    _write_json(tmp_path / ".rig" / "runs" / task_id / "task.json", {
        "task_id": task_id, "input": "Atomic failure", "task_type": "bugfix",
        "base_commit": "4" * 40,
    })

    def fail_replace(_source, _destination):
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(EvalCaseError, match="filesystem"):
        capture_case(tmp_path, task_id, now="2026-08-05T01:00:00+00:00")

    draft = tmp_path / ".rig" / "evals" / "drafts" / task_id
    assert not draft.exists()


def test_capture_incident_outcome_has_priority_over_failed_gate_and_reject(tmp_path):
    from rig_workbench.eval import capture_case

    task_id = "rig-20260805-priority"
    run = tmp_path / ".rig" / "runs" / task_id
    _write_json(run / "task.json", {"task_id": task_id, "input": "Priority",
                                    "task_type": "bugfix", "base_commit": "5" * 40})
    _write_json(run / "outcome.json", {
        "status": "incident", "note": "Production users saw stale authorization",
    })
    _write_json(run / "acceptance.json", {"checks": [
        {"name": "tests_pass", "status": "failed", "detail": "test failure"},
    ]})
    _write_json(run / "review.json", {"verdicts": [
        {"persona": "security-reviewer", "verdict": "REJECT"},
    ]})

    _output, case = capture_case(tmp_path, task_id,
                                 now="2026-08-05T01:00:00+00:00")

    assert case["incident"] is True and case["suite"] == "incident"
    assert case["failure_summary"] == "Production users saw stale authorization"
    assert "tests_pass" not in case["failure_summary"]
    assert "security-reviewer" not in case["failure_summary"]
    assert case["target_inputs"]["failure_family"] == "production:incident"
    assert case["repeat"] == 3 and case["clean_controls"]
    assert case["deterministic_checks"] == ["contains:task_intent"]


def test_capture_uses_failure_taxonomy_and_rejects_success_without_opt_in(tmp_path):
    from rig_workbench.eval import EvalCaseError, capture_case

    failed_id = "rig-20260805-taxonomy"
    _write_json(tmp_path / ".rig" / "runs" / failed_id / "task.json", {
        "task_id": failed_id, "input": "Taxonomy failure", "task_type": "bugfix",
        "base_commit": "8" * 40,
    })
    runs = tmp_path / ".rig" / "runs.jsonl"
    runs.parent.mkdir(parents=True, exist_ok=True)
    runs.write_text(json.dumps({
        "task_id": failed_id, "final": "ESCALATE",
        "failure_mode": "verification:incorrect-implementation",
    }) + "\n", encoding="utf-8")
    _path, failed = capture_case(tmp_path, failed_id,
                                 now="2026-08-05T01:00:00+00:00")
    assert failed["target_inputs"]["failure_family"] == (
        "verification:incorrect-implementation"
    )
    assert "runs.jsonl" in failed["provenance"]["source_hashes"]

    success_id = "rig-20260805-success"
    _write_json(tmp_path / ".rig" / "runs" / success_id / "task.json", {
        "task_id": success_id, "input": "Successful task", "task_type": "bugfix",
        "base_commit": "9" * 40, "status": "accepted",
    })
    _write_json(tmp_path / ".rig" / "runs" / success_id / "outcome.json", {
        "status": "ok", "note": "healthy",
    })
    with pytest.raises(EvalCaseError, match="successful task"):
        capture_case(tmp_path, success_id, now="2026-08-05T01:00:00+00:00")
    _path, opted = capture_case(
        tmp_path, success_id, now="2026-08-05T01:00:00+00:00",
        allow_nonincident=True,
    )
    assert opted["incident"] is False and opted["status"] == "draft"


def test_capture_to_reproduce_is_one_command_red_baseline(tmp_path):
    task_id = "rig-20260805-reproduce"
    run = tmp_path / ".rig" / "runs" / task_id
    _write_json(run / "task.json", {
        "task_id": task_id, "input": "Reproduce stuck gate", "task_type": "bugfix",
        "base_commit": "a" * 40, "status": "gate_failed",
    })
    _write_json(run / "acceptance.json", {"checks": [
        {"name": "tests_pass", "status": "failed", "detail": "bounded"},
    ]})
    captured = _run_eval(["capture", task_id, "--repo", str(tmp_path)], tmp_path)
    assert captured.returncode == 0, captured.stderr
    reproduced = _run_eval([
        "reproduce", task_id, "--provider", "mock", "--model", "fixture",
        "--repo", str(tmp_path), "--allow-mock",
    ], tmp_path)
    assert reproduced.returncode == 1, reproduced.stderr
    result_path = Path(reproduced.stdout.strip().splitlines()[-1])
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["phase"] == "baseline"
    assert result["summary"]["target_success_rate"] <= 1 / 3

    refused = _run_eval([
        "reproduce", task_id, "--provider", "mock", "--model", "fixture",
        "--repo", str(tmp_path),
    ], tmp_path)
    assert refused.returncode == 2 and "dev probe" in refused.stderr

    mock_judge_refused = _run_eval([
        "reproduce", task_id, "--provider", "command", "--model", "fixture",
        "--command", "false", "--judge-provider", "mock", "--judge-model", "fixture",
        "--repo", str(tmp_path),
    ], tmp_path)
    assert mock_judge_refused.returncode == 2 and "mock judge" in mock_judge_refused.stderr
    mock_judge_probe = _run_eval([
        "reproduce", task_id, "--provider", "command", "--model", "fixture",
        "--command", "false", "--judge-provider", "mock", "--judge-model", "fixture",
        "--repo", str(tmp_path), "--allow-mock",
    ], tmp_path)
    assert mock_judge_probe.returncode == 1


def test_capture_cli_normalizes_source_filesystem_error_to_exit_two(tmp_path):
    task_id = "rig-20260805-source-io"
    task_path = tmp_path / ".rig" / "runs" / task_id / "task.json"
    task_path.mkdir(parents=True)

    result = _run_eval(["capture", task_id, "--repo", str(tmp_path)], tmp_path)

    assert result.returncode == 2
    assert "invalid source artifact" in result.stderr
    assert "Traceback" not in result.stderr


def test_capture_redacts_unsafe_failed_check_and_reviewer_names(tmp_path):
    from rig_workbench.eval import capture_case

    task_id = "rig-20260805-unsafe-summary"
    run = tmp_path / ".rig" / "runs" / task_id
    _write_json(run / "task.json", {"task_id": task_id, "input": "Safe intent",
                                    "task_type": "bugfix", "base_commit": "6" * 40})
    token = "xoxb-" + "283736350342-4939293923-abcDefGhi123kLmNo"
    _write_json(run / "acceptance.json", {"checks": [
        {"name": token, "status": "failed"},
    ]})
    _write_json(run / "review.json", {"verdicts": [
        {"persona": "review /home/user/private", "verdict": "REJECT"},
    ]})

    _output, case = capture_case(tmp_path, task_id,
                                 now="2026-08-05T01:00:00+00:00")

    encoded = json.dumps(case)
    assert token not in encoded and "/home/user" not in encoded
    assert case["failure_summary"] == "No production incident recorded"


@pytest.mark.parametrize("assignment", [
    "access_token=small-value",
    "client_secret: small-value",
    "AWS_SECRET_ACCESS_KEY=small-value",
    "https://example.test/cb?access_token=small-value",
    "https://example.test/cb?access%5Ftoken=small-value",
])
def test_scalar_credential_assignment_lhs_is_rejected(assignment):
    from rig_workbench.eval import EvalCaseError, validate_case

    case = copy.deepcopy(valid_case())
    case["target_inputs"] = {"input": assignment}
    with pytest.raises(EvalCaseError):
        validate_case(case)


@pytest.mark.parametrize("source_field", ["input", "note"])
def test_capture_redacts_scalar_credential_assignment_from_title_or_incident_note(
    tmp_path, source_field
):
    from rig_workbench.eval import capture_case

    task_id = f"rig-20260805-credential-{source_field}"
    assignment = "https://example.test/cb?access%5Ftoken=small-value"
    task_input = assignment if source_field == "input" else "Safe title"
    note = assignment if source_field == "note" else "Safe incident note"
    run = tmp_path / ".rig" / "runs" / task_id
    _write_json(run / "task.json", {"task_id": task_id, "input": task_input,
                                    "task_type": "bugfix", "base_commit": "7" * 40})
    _write_json(run / "outcome.json", {"status": "incident", "note": note})

    _output, case = capture_case(tmp_path, task_id,
                                 now="2026-08-05T01:00:00+00:00")

    assert assignment not in json.dumps(case)
    if source_field == "input":
        assert case["title"] == task_id
    else:
        assert case["failure_summary"] == "Incident recorded without a safe failure summary"
