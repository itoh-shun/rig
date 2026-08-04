import copy
import datetime as dt
import json
import pathlib
import hashlib
import os
import subprocess
import sys
import types

import pytest

from test_eval_cases import valid_case


NOW = dt.datetime(2026, 8, 5, 1, 0, tzinfo=dt.timezone.utc)
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def trusted_eval_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", "test-only-eval-attestation-key-32-bytes")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "eval@test.invalid"], cwd=tmp_path,
                   check=True)
    subprocess.run(["git", "config", "user.name", "eval-test"], cwd=tmp_path, check=True)
    (tmp_path / ".eval-root").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", ".eval-root"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "eval fixture"], cwd=tmp_path, check=True)


def draft_case():
    case = copy.deepcopy(valid_case())
    case["status"] = "draft"
    case["id"] = "mock-regression"
    case["provenance"]["source_task_id"] = "rig-20260805-mock"
    case["red_thresholds"] = {"max_success_rate": 1 / 3}
    case["deterministic_checks"] = ["contains:scenario"]
    case["semantic_rubric"] = []
    return case


def resign(result):
    from rig_workbench.eval.cases import canonical_json
    from rig_workbench.eval.attestation import sign_result_attestation

    result.pop("attestation", None)
    result.pop("result_sha256", None)
    result["result_sha256"] = hashlib.sha256(canonical_json(result).encode()).hexdigest()
    result["attestation"] = sign_result_attestation(result)


def forge_rehash_without_key(result):
    from rig_workbench.eval.cases import canonical_json

    attestation = result.pop("attestation")
    result.pop("result_sha256", None)
    result["result_sha256"] = hashlib.sha256(canonical_json(result).encode()).hexdigest()
    result["attestation"] = attestation


def run_eval_cli(args, cwd):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH")])
    ))
    return subprocess.run(
        [sys.executable, "-m", "rig_workbench.cli", "eval", *args], cwd=cwd,
        env=env, capture_output=True, text=True, timeout=30,
    )


def test_mock_baseline_runs_target_and_clean_three_times_and_writes_canonical_result(tmp_path):
    from rig_workbench.eval.cases import canonical_json
    from rig_workbench.eval.runner import run_case

    case = draft_case()
    output, result = run_case(
        case, repo=tmp_path, provider="mock", model="fixture", repeat=3,
        phase="baseline", now=NOW,
    )

    assert result["eval_result_schema_version"] == 1
    assert [row["outcome"] for row in result["target"]] == ["fail", "fail", "pass"]
    assert [row["outcome"] for row in result["clean"]] == ["pass", "pass", "pass"]
    assert result["summary"]["target_failure_rate"] == pytest.approx(2 / 3)
    assert result["summary"]["clean_success_rate"] == 1.0
    assert output.parent == tmp_path / ".rig" / "evals" / "results" / case["id"]
    assert output.read_text(encoding="utf-8") == canonical_json(result)


def test_compare_accepts_two_of_three_red_and_three_of_three_current(tmp_path):
    from rig_workbench.eval.compare import compare_results
    from rig_workbench.eval.runner import run_case

    case = draft_case()
    _base_path, baseline = run_case(
        case, repo=tmp_path, provider="mock", model="fixture", repeat=3,
        phase="baseline", now=NOW,
    )
    _current_path, current = run_case(
        case, repo=tmp_path, provider="mock", model="fixture", repeat=3,
        phase="current", now=NOW,
    )

    report = compare_results(baseline, current, case=case, now=NOW)

    assert report["status"] == "pass"
    assert report["baseline_red"] is True
    assert report["current_target_green"] is True
    assert report["current_clean_green"] is True


def test_compare_rejects_identity_integrity_freshness_and_insufficient_evidence(tmp_path):
    from rig_workbench.eval import EvalCaseError
    from rig_workbench.eval.compare import compare_results
    from rig_workbench.eval.runner import run_case

    case = draft_case()
    _p, baseline = run_case(case, repo=tmp_path, provider="mock", model="fixture",
                            repeat=3, phase="baseline", now=NOW)
    _p, current = run_case(case, repo=tmp_path, provider="mock", model="fixture",
                           repeat=3, phase="current", now=NOW)

    clean_bad = copy.deepcopy(current)
    clean_bad["clean"][0]["outcome"] = "fail"
    clean_bad["clean"][0]["returncode"] = 1
    clean_bad["summary"]["clean_success_rate"] = 2 / 3
    clean_bad["summary"]["clean_false_positive_rate"] = 1 / 3
    resign(clean_bad)
    assert compare_results(baseline, clean_bad, case=case, now=NOW)["status"] == "regression"

    no_red = copy.deepcopy(baseline)
    no_red["target"] = copy.deepcopy(current["target"])
    no_red["summary"]["target_success_rate"] = 1.0
    no_red["summary"]["target_failure_rate"] = 0.0
    resign(no_red)
    assert compare_results(no_red, current, case=case, now=NOW)["status"] == "regression"

    bad_results = []
    for field, value in (("provider", "codex"), ("model", "other"),
                         ("case_hash", "0" * 64), ("source_commit", "9" * 40)):
        changed = copy.deepcopy(current)
        changed[field] = value
        resign(changed)
        bad_results.append(changed)
    tampered = copy.deepcopy(current)
    tampered["result_sha256"] = "0" * 64
    bad_results.append(tampered)
    one = copy.deepcopy(current)
    one["repeat"] = 1
    one["target"] = one["target"][:1]
    one["clean"] = one["clean"][:1]
    resign(one)
    bad_results.append(one)
    for started in (NOW - dt.timedelta(days=31), NOW + dt.timedelta(minutes=6)):
        changed = copy.deepcopy(current)
        changed["started_at"] = started.isoformat()
        resign(changed)
        bad_results.append(changed)
    inconsistent = copy.deepcopy(current)
    inconsistent["summary"]["target_success_rate"] = 0.0
    resign(inconsistent)
    bad_results.append(inconsistent)

    for bad in bad_results:
        with pytest.raises(EvalCaseError):
            compare_results(baseline, bad, case=case, now=NOW)


def test_promote_requires_measured_judge_and_keeps_draft(tmp_path):
    from rig_workbench.eval import EvalCaseError
    from rig_workbench.eval.cases import canonical_json
    from rig_workbench.eval.promote import promote_case
    from rig_workbench.eval.runner import run_case

    case = draft_case()
    case["semantic_rubric"] = [
        {"id": "correct", "description": "Output is correct", "weight": 1.0}
    ]
    draft = tmp_path / ".rig" / "evals" / "drafts" / case["id"] / "case.json"
    draft.parent.mkdir(parents=True)
    draft.write_text(canonical_json(case), encoding="utf-8")
    _p, baseline = run_case(case, repo=tmp_path, provider="mock", model="fixture",
                            repeat=3, phase="baseline", now=NOW)
    _p, unmeasured = run_case(case, repo=tmp_path, provider="mock", model="fixture",
                              repeat=3, phase="current", now=NOW)
    with pytest.raises(EvalCaseError, match="judge"):
        promote_case(tmp_path, case["id"], baseline, unmeasured, now=NOW)

    def judge(_case, _payload, _output):
        return {"status": "measured", "criteria": [
            {"id": "correct", "status": "pass", "score": 1.0},
        ]}

    measured_repo = tmp_path / "measured"
    measured_draft = measured_repo / ".rig" / "evals" / "drafts" / case["id"] / "case.json"
    measured_draft.parent.mkdir(parents=True)
    measured_draft.write_text(canonical_json(case), encoding="utf-8")
    _p, measured_base = run_case(
        case, repo=measured_repo, provider="mock", model="fixture", repeat=3,
        phase="baseline", now=NOW, judge_adapter=judge,
    )
    _p, measured_current = run_case(
        case, repo=measured_repo, provider="mock", model="fixture", repeat=3,
        phase="current", now=NOW, judge_adapter=judge,
    )

    promoted_path, promoted = promote_case(
        measured_repo, case["id"], measured_base, measured_current, now=NOW
    )

    assert promoted["status"] == "approved"
    assert promoted_path == measured_repo / "evals" / "cases" / case["id"] / "case.json"
    assert measured_draft.exists()
    from rig_workbench.eval.cases import evaluation_spec_hash
    from rig_workbench.eval.compare import compare_results
    assert evaluation_spec_hash(promoted) == measured_current["case_hash"]
    assert compare_results(
        measured_base, measured_current, case=promoted, now=NOW
    )["status"] == "pass"

    bypass_repo = tmp_path / "bypass"
    bypass_draft = bypass_repo / ".rig" / "evals" / "drafts" / case["id"] / "case.json"
    bypass_draft.parent.mkdir(parents=True)
    bypass_draft.write_text(canonical_json(case), encoding="utf-8")
    bypass_base = copy.deepcopy(measured_base)
    bypass_current = copy.deepcopy(measured_current)
    for result in (bypass_base, bypass_current):
        for row in result["target"]:
            row["judge"]["criteria"][0]["id"] = "unrelated"
        resign(result)
    with pytest.raises(EvalCaseError, match="rubric|criteria"):
        promote_case(bypass_repo, case["id"], bypass_base, bypass_current, now=NOW)


def test_eval_cli_mock_run_compare_and_unmeasured_promote_exit(tmp_path):
    from rig_workbench.eval.cases import canonical_json

    case = draft_case()
    case["semantic_rubric"] = [
        {"id": "correct", "description": "Output is correct", "weight": 1.0}
    ]
    draft = tmp_path / ".rig" / "evals" / "drafts" / case["id"] / "case.json"
    draft.parent.mkdir(parents=True)
    draft.write_text(canonical_json(case), encoding="utf-8")
    common = [case["id"], "--provider", "mock", "--model", "fixture",
              "--repeat", "3", "--repo", str(tmp_path)]
    judge_command = (
        'python3 -c "import json; print(json.dumps({\'status\':\'measured\','
        '\'criteria\':[{\'id\':\'correct\',\'status\':\'pass\',\'score\':1.0}]}))"'
    )
    judge_args = ["--judge-provider", "command", "--judge-model", "fixture",
                  "--judge-command", judge_command]
    baseline_run = run_eval_cli([
        "run", *common, "--phase", "baseline", *judge_args,
    ], tmp_path)
    current_run = run_eval_cli([
        "run", *common, "--phase", "current", *judge_args,
    ], tmp_path)
    assert baseline_run.returncode == current_run.returncode == 0
    baseline_path = pathlib.Path(baseline_run.stdout.strip().splitlines()[-1])
    current_path = pathlib.Path(current_run.stdout.strip().splitlines()[-1])

    compared = run_eval_cli([
        "compare", "--baseline", str(baseline_path), "--current", str(current_path),
        "--repo", str(tmp_path),
    ], tmp_path)
    assert compared.returncode == 0 and json.loads(compared.stdout)["status"] == "pass"

    insufficient = run_eval_cli([
        "run", case["id"], "--provider", "mock", "--model", "fixture",
        "--repeat", "1", "--phase", "current", "--repo", str(tmp_path),
    ], tmp_path)
    assert insufficient.returncode == 2 and "at least 3" in insufficient.stderr

    bad_current = json.loads(current_path.read_text(encoding="utf-8"))
    bad_current["clean"][0]["outcome"] = "fail"
    bad_current["clean"][0]["returncode"] = 1
    bad_current["summary"]["clean_success_rate"] = 2 / 3
    bad_current["summary"]["clean_false_positive_rate"] = 1 / 3
    resign(bad_current)
    bad_path = tmp_path / "bad-current.json"
    bad_path.write_text(canonical_json(bad_current), encoding="utf-8")
    regressed = run_eval_cli([
        "compare", "--baseline", str(baseline_path), "--current", str(bad_path),
        "--repo", str(tmp_path),
    ], tmp_path)
    assert regressed.returncode == 1 and json.loads(regressed.stdout)["status"] == "regression"

    promoted = run_eval_cli([
        "promote", case["id"], "--baseline", str(baseline_path),
        "--current", str(current_path), "--repo", str(tmp_path),
    ], tmp_path)
    assert promoted.returncode == 0


def test_command_executor_is_shell_safe_times_out_caps_and_redacts(tmp_path):
    from rig_workbench.eval.runner import run_case

    case = draft_case()
    case["provider_policy"] = {"mode": "any", "allowed": []}
    case["deterministic_checks"] = ["exit:0"]

    injection_repo = tmp_path / "injection"
    injection_repo.mkdir()
    _p, injection = run_case(
        case, repo=injection_repo, provider="command", model="fixture", repeat=3,
        phase="current", command="echo safe; touch injected-marker", now=NOW,
    )
    assert not (injection_repo / "injected-marker").exists()
    assert all(row["outcome"] == "pass" for row in injection["target"])

    timeout_repo = tmp_path / "timeout"
    timeout_repo.mkdir()
    _p, timed = run_case(
        case, repo=timeout_repo, provider="command", model="fixture", repeat=3,
        phase="current", command='python3 -c "import time; time.sleep(1)"',
        timeout_s=0.01, now=NOW,
    )
    assert all(row["infra_status"] == "timeout" for row in timed["target"])

    cap_repo = tmp_path / "cap"
    cap_repo.mkdir()
    _p, capped = run_case(
        case, repo=cap_repo, provider="command", model="fixture", repeat=3,
        phase="current", command='python3 -c "print(\'x\' * 6000)"', now=NOW,
    )
    assert capped["target"][0]["stdout"]["truncated"] is True
    assert len(capped["target"][0]["stdout"]["text"].encode()) <= 4096

    secret_repo = tmp_path / "secret"
    secret_repo.mkdir()
    _p, redacted = run_case(
        case, repo=secret_repo, provider="command", model="fixture", repeat=3,
        phase="current", command='python3 -c "print(\'access_token=small-value\')"',
        now=NOW,
    )
    stdout = redacted["target"][0]["stdout"]
    assert stdout["redacted"] is True and stdout["text"] == "[REDACTED]"
    assert "small-value" not in json.dumps(redacted)


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_real_provider_reuses_adapter_argv_with_shell_false(monkeypatch, tmp_path, provider):
    from rig_workbench.eval import runner

    case = draft_case()
    case["provider_policy"] = {"mode": "any", "allowed": []}
    case["deterministic_checks"] = ["contains:ok", "not_contains:bad", "regex:o+k",
                                    "json", "schema:ok", "exit:0"]
    seen = []
    monkeypatch.setattr(
        runner, "build_bare_attempt",
        lambda selected, goal, repo, model: types.SimpleNamespace(
            argv=(selected, "--model", model, goal)
        ),
    )
    monkeypatch.setattr(runner.shutil, "which", lambda executable: f"/bin/{executable}")
    monkeypatch.setattr(runner, "_git_identity", lambda _repo: ("a" * 40, "b" * 40, "available"))
    monkeypatch.setattr(runner, "execution_diff_sha256", lambda *_args, **_kwargs: "c" * 64)

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '{"ok":true}', "")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    _path, result = runner.run_case(
        case, repo=tmp_path, provider=provider, model="fixture", repeat=3,
        phase="current", now=NOW,
    )

    assert all(row["outcome"] == "pass" for row in result["target"])
    assert seen and all(kwargs["shell"] is False for _argv, kwargs in seen)
    assert all(argv[0] == provider for argv, _kwargs in seen)


def test_unavailable_real_provider_fails_closed(monkeypatch, tmp_path):
    from rig_workbench.eval import runner

    case = draft_case()
    case["provider_policy"] = {"mode": "any", "allowed": []}
    monkeypatch.setattr(runner.shutil, "which", lambda _executable: None)

    _path, result = runner.run_case(
        case, repo=tmp_path, provider="codex", model="fixture", repeat=3,
        phase="current", now=NOW,
    )

    assert all(row["infra_status"] == "unavailable" for row in result["target"])
    assert result["summary"]["target_success_rate"] == 0.0


def test_result_atomic_failure_leaves_no_partial_files(monkeypatch, tmp_path):
    from rig_workbench.eval import EvalCaseError, runner

    def fail_replace(_source, _destination):
        raise OSError("injected result replace failure")

    monkeypatch.setattr(runner.os, "replace", fail_replace)
    with pytest.raises(EvalCaseError, match="filesystem"):
        runner.run_case(
            draft_case(), repo=tmp_path, provider="mock", model="fixture", repeat=3,
            phase="current", now=NOW,
        )

    results = tmp_path / ".rig" / "evals" / "results"
    assert not list(results.rglob("*.json"))
    assert not list(results.rglob("*.tmp"))


def test_promotion_atomic_failure_keeps_draft_and_leaves_no_partial_case(monkeypatch, tmp_path):
    from rig_workbench.eval import EvalCaseError, promote
    from rig_workbench.eval.cases import canonical_json
    from rig_workbench.eval.runner import run_case

    case = draft_case()
    case["semantic_rubric"] = [
        {"id": "correct", "description": "Output is correct", "weight": 1.0}
    ]
    draft = tmp_path / ".rig" / "evals" / "drafts" / case["id"] / "case.json"
    draft.parent.mkdir(parents=True)
    draft.write_text(canonical_json(case), encoding="utf-8")

    def judge(_case, _payload, _output):
        return {"status": "measured", "criteria": [
            {"id": "correct", "status": "pass", "score": 1.0},
        ]}

    _p, baseline = run_case(case, repo=tmp_path, provider="mock", model="fixture",
                            repeat=3, phase="baseline", now=NOW, judge_adapter=judge)
    _p, current = run_case(case, repo=tmp_path, provider="mock", model="fixture",
                           repeat=3, phase="current", now=NOW, judge_adapter=judge)

    monkeypatch.setattr(
        promote.os, "replace",
        lambda _source, _destination: (_ for _ in ()).throw(OSError("injected")),
    )
    with pytest.raises(EvalCaseError, match="filesystem"):
        promote.promote_case(tmp_path, case["id"], baseline, current, now=NOW)

    assert draft.exists()
    promoted_dir = tmp_path / "evals" / "cases" / case["id"]
    assert not promoted_dir.exists()


def test_output_secret_crossing_cap_boundary_is_fully_redacted(tmp_path):
    from rig_workbench.eval.runner import run_case

    case = draft_case()
    case["provider_policy"] = {"mode": "any", "allowed": []}
    case["deterministic_checks"] = ["exit:0"]
    command = 'python3 -c "print(\'x\' * 4090 + \'access_token=small-value\')"'

    _path, result = run_case(
        case, repo=tmp_path, provider="command", model="fixture", repeat=3,
        phase="current", command=command, now=NOW,
    )

    output = result["target"][0]["stdout"]
    assert output["redacted"] is True
    assert output["text"] == "[REDACTED]"
    assert "access_" not in json.dumps(result)


def test_compare_rejects_forged_outcome_infra_evidence_and_output_invariants(tmp_path):
    from rig_workbench.eval import EvalCaseError
    from rig_workbench.eval.compare import compare_results
    from rig_workbench.eval.runner import run_case

    case = draft_case()
    _p, baseline = run_case(case, repo=tmp_path, provider="mock", model="fixture",
                            repeat=3, phase="baseline", now=NOW)
    _p, current = run_case(case, repo=tmp_path, provider="mock", model="fixture",
                           repeat=3, phase="current", now=NOW)

    attacks = []
    forged = copy.deepcopy(baseline)
    forged["target"][0]["outcome"] = "pass"
    forged["summary"]["target_success_rate"] = 2 / 3
    forged["summary"]["target_failure_rate"] = 1 / 3
    resign(forged)
    attacks.append((forged, current))

    infra = copy.deepcopy(baseline)
    infra["target"][0]["infra_status"] = "timeout"
    infra["target"][0]["returncode"] = 124
    resign(infra)
    attacks.append((infra, current))

    oversized = copy.deepcopy(current)
    oversized["target"][0]["stdout"]["text"] = "x" * 4097
    resign(oversized)
    attacks.append((baseline, oversized))

    fake_redacted = copy.deepcopy(current)
    fake_redacted["target"][0]["stdout"].update(redacted=True, text="leaked")
    resign(fake_redacted)
    attacks.append((baseline, fake_redacted))

    for bad_baseline, bad_current in attacks:
        with pytest.raises(EvalCaseError):
            compare_results(bad_baseline, bad_current, case=case, now=NOW)


def test_compare_rejects_joint_forged_commit_unknown_provider_and_executor_mismatch(tmp_path):
    from rig_workbench.eval import EvalCaseError
    from rig_workbench.eval.compare import compare_results
    from rig_workbench.eval.runner import run_case

    case = draft_case()
    _p, baseline = run_case(case, repo=tmp_path, provider="mock", model="fixture",
                            repeat=3, phase="baseline", now=NOW)
    _p, current = run_case(case, repo=tmp_path, provider="mock", model="fixture",
                           repeat=3, phase="current", now=NOW)

    forged_base = copy.deepcopy(baseline)
    forged_current = copy.deepcopy(current)
    for result in (forged_base, forged_current):
        result["source_commit"] = "8" * 40
        result["source_base_commit"] = "8" * 40
        resign(result)
    with pytest.raises(EvalCaseError, match="commit"):
        compare_results(forged_base, forged_current, case=case, now=NOW)

    unknown_base = copy.deepcopy(baseline)
    unknown_current = copy.deepcopy(current)
    for result in (unknown_base, unknown_current):
        result["provider"] = "ollama"
        resign(result)
    with pytest.raises(EvalCaseError, match="provider"):
        compare_results(unknown_base, unknown_current, case=case, now=NOW)

    other_executor = copy.deepcopy(current)
    other_executor["executor_version"] = "different"
    resign(other_executor)
    with pytest.raises(EvalCaseError, match="executor_version"):
        compare_results(baseline, other_executor, case=case, now=NOW)


def test_run_normalizes_repo_resolve_oserror(monkeypatch, tmp_path):
    from rig_workbench.eval import EvalCaseError, runner

    monkeypatch.setattr(
        runner.pathlib.Path, "resolve",
        lambda _self: (_ for _ in ()).throw(OSError("injected resolve failure")),
    )
    with pytest.raises(EvalCaseError, match="resolving repository"):
        runner.run_case(
            draft_case(), repo=tmp_path, provider="mock", model="fixture", repeat=3,
            phase="current", now=NOW,
        )


def test_cli_normalizes_repo_resolve_oserror_to_exit_two(monkeypatch, capsys):
    from rig_workbench.eval import cli as eval_cli

    monkeypatch.setattr(
        eval_cli.pathlib.Path, "resolve",
        lambda _self: (_ for _ in ()).throw(OSError("injected resolve failure")),
    )
    code = eval_cli.cmd_eval([
        "run", "case", "--provider", "mock", "--model", "fixture",
        "--repeat", "3", "--phase", "current", "--repo", "repo",
    ])

    assert code == 2
    assert "resolving repository" in capsys.readouterr().err


def test_result_attestation_binds_git_execution_and_rejects_rehash_forgery(tmp_path):
    from rig_workbench.eval import EvalCaseError
    from rig_workbench.eval.attestation import verify_result_attestation
    from rig_workbench.eval.compare import compare_results
    from rig_workbench.eval.runner import run_case

    case = draft_case()
    _p, baseline = run_case(case, repo=tmp_path, provider="mock", model="fixture",
                            repeat=3, phase="baseline", now=NOW)
    _p, current = run_case(case, repo=tmp_path, provider="mock", model="fixture",
                           repeat=3, phase="current", now=NOW)

    assert baseline["execution_status"] == "available"
    assert len(baseline["execution_commit"]) == 40
    assert verify_result_attestation(baseline) is True

    forged = copy.deepcopy(current)
    forged["summary"]["target_success_rate"] = 0.0
    forged["summary"]["target_failure_rate"] = 1.0
    forge_rehash_without_key(forged)
    with pytest.raises(EvalCaseError, match="attestation"):
        compare_results(baseline, forged, case=case, now=NOW)


def test_run_repeat_must_exactly_match_case(tmp_path):
    from rig_workbench.eval import EvalCaseError
    from rig_workbench.eval.runner import run_case

    case = draft_case()
    case["repeat"] = 4
    with pytest.raises(EvalCaseError, match="match case"):
        run_case(case, repo=tmp_path, provider="mock", model="fixture", repeat=3,
                 phase="current", now=NOW)

    with pytest.raises(EvalCaseError, match="execution base"):
        run_case(draft_case(), repo=tmp_path, provider="mock", model="fixture",
                 repeat=3, phase="current", execution_base="missing-ref", now=NOW)


def test_compare_requires_passing_semantic_evidence_for_target_and_clean_baseline_and_current(
    tmp_path,
):
    from rig_workbench.eval import EvalCaseError
    from rig_workbench.eval.compare import compare_results
    from rig_workbench.eval.runner import run_case

    case = draft_case()
    case["semantic_rubric"] = [
        {"id": "correct", "description": "Output is correct", "weight": 1.0}
    ]
    def judge(_case, _payload, _output):
        return {"status": "measured", "criteria": [
            {"id": "correct", "status": "pass", "score": 1.0}
        ]}
    _p, baseline = run_case(case, repo=tmp_path, provider="mock", model="fixture",
                            repeat=3, phase="baseline", judge_adapter=judge, now=NOW)
    _p, current = run_case(case, repo=tmp_path, provider="mock", model="fixture",
                           repeat=3, phase="current", judge_adapter=judge, now=NOW)
    for result_name, kind in (("baseline", "clean"), ("current", "target")):
        changed = copy.deepcopy(baseline if result_name == "baseline" else current)
        changed[kind][0]["judge"]["criteria"][0]["status"] = "fail"
        resign(changed)
        args = (changed, current) if result_name == "baseline" else (baseline, changed)
        with pytest.raises(EvalCaseError, match="semantic.*failed"):
            compare_results(*args, case=case, now=NOW)


def test_attestation_default_key_is_atomic_private_and_rejects_missing_insecure_or_symlink(
    tmp_path, monkeypatch,
):
    from rig_workbench.eval import EvalCaseError
    from rig_workbench.eval.attestation import sign_result_attestation, verify_result_attestation

    monkeypatch.delenv("RIG_EVAL_ATTESTATION_KEY")
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    payload = {"result_sha256": "a" * 64}
    payload["attestation"] = sign_result_attestation(payload)
    key = state / "rig" / "eval-attestation.key"
    assert key.stat().st_mode & 0o777 == 0o600
    assert verify_result_attestation(payload) is True

    key.unlink()
    with pytest.raises(EvalCaseError, match="unavailable"):
        verify_result_attestation(payload)
    key.write_bytes(b"x" * 32)
    key.chmod(0o644)
    with pytest.raises(EvalCaseError, match="0600"):
        sign_result_attestation({"value": 1})
    key.unlink()
    target = state / "real-key"
    target.write_bytes(b"y" * 32)
    target.chmod(0o600)
    key.symlink_to(target)
    with pytest.raises(EvalCaseError, match="symlink"):
        sign_result_attestation({"value": 1})


def test_real_provider_nonzero_is_infrastructure_failure(monkeypatch, tmp_path):
    from rig_workbench.eval import runner

    case = draft_case()
    case["provider_policy"] = {"mode": "any", "allowed": []}
    monkeypatch.setattr(runner, "_git_identity", lambda _repo: ("a" * 40, "b" * 40, "available"))
    monkeypatch.setattr(runner, "execution_diff_sha256", lambda *_args, **_kwargs: "c" * 64)
    monkeypatch.setattr(runner.shutil, "which", lambda _executable: "/bin/codex")
    monkeypatch.setattr(
        runner, "build_bare_attempt",
        lambda *_args: types.SimpleNamespace(argv=("codex", "exec")),
    )
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 2, "", "failed"),
    )
    _path, result = runner.run_case(
        case, repo=tmp_path, provider="codex", model="fixture", repeat=3,
        phase="current", now=NOW,
    )
    assert all(row["infra_status"] == "provider_error" for row in result["target"])


def test_non_git_execution_cannot_be_compared_or_promoted(monkeypatch, tmp_path):
    from rig_workbench.eval import EvalCaseError, runner
    from rig_workbench.eval.compare import compare_results

    case = draft_case()
    monkeypatch.setattr(runner, "_git_identity", lambda _repo: (None, None, "unavailable"))
    _p, baseline = runner.run_case(
        case, repo=tmp_path, provider="mock", model="fixture", repeat=3,
        phase="baseline", now=NOW,
    )
    _p, current = runner.run_case(
        case, repo=tmp_path, provider="mock", model="fixture", repeat=3,
        phase="current", now=NOW,
    )
    with pytest.raises(EvalCaseError, match="git execution identity"):
        compare_results(baseline, current, case=case, now=NOW)


def test_command_judge_is_shell_free_bounded_and_fail_closed(monkeypatch, tmp_path):
    from rig_workbench.eval import runner

    case = draft_case()
    case["semantic_rubric"] = [
        {"id": "correct", "description": "Output is correct", "weight": 1.0}
    ]
    seen = []

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv, 0,
            json.dumps({"status": "measured", "criteria": [
                {"id": "correct", "status": "pass", "score": 1}
            ]}), "",
        )

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    judge = runner.make_judge_adapter(
        provider="command", model="fixture", repo=tmp_path, command="python3 judge.py"
    )
    assert judge(case, "input", "output")["status"] == "measured"
    assert seen[0][1]["shell"] is False
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv, 0, "x" * 5000 + " access_token=secret-value", ""
        ),
    )
    assert judge(case, "input", "output") == {"status": "error", "criteria": []}


def test_command_evaluator_and_judge_cannot_read_attestation_key(tmp_path):
    from rig_workbench.eval.runner import make_judge_adapter, run_case

    case = draft_case()
    case["provider_policy"] = {"mode": "any", "allowed": []}
    case["deterministic_checks"] = ["contains:absent"]
    probe = (
        'python3 -c "import os; print(\'present\' if '
        '\'RIG_EVAL_ATTESTATION_KEY\' in os.environ else \'absent\')"'
    )
    _path, result = run_case(
        case, repo=tmp_path, provider="command", model="fixture", repeat=3,
        phase="current", command=probe, now=NOW,
    )
    assert result["target"][0]["stdout"]["text"].strip() == "absent"

    semantic = draft_case()
    semantic["semantic_rubric"] = [
        {"id": "correct", "description": "Output is correct", "weight": 1.0}
    ]
    judge_probe = (
        'python3 -c "import json,os; print(json.dumps({\'status\':\'measured\','
        '\'criteria\':[{\'id\':\'correct\',\'status\':\'pass\' if '
        '\'RIG_EVAL_ATTESTATION_KEY\' not in os.environ else \'fail\','
        '\'score\':1.0}]}))"'
    )
    judge = make_judge_adapter(
        provider="command", model="fixture", repo=tmp_path, command=judge_probe,
    )
    evidence = judge(semantic, "input", "output")
    assert evidence == {"status": "measured", "criteria": [
        {"id": "correct", "status": "pass", "score": 1.0}
    ]}
