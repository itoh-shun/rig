import importlib.util
import json
import os
import shutil
import stat
import subprocess
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "benchmarks" / "writing-tasks" / "jp-natural-writing" / "paired_dev_eval.py"
SUPPORT_SAFETY_FIELDS = (
    "no_file_body",
    "no_data_rows",
    "structure_header_only_alternative",
    "masking",
    "safe_alternative",
)


def parse_fake_judgment(raw: str, category: str):
    parsed = json.loads(raw)
    if category == "support_reply":
        parsed["support_safety_by_answer"] = {
            answer: {field: True for field in SUPPORT_SAFETY_FIELDS}
            for answer in ("A", "B")
        }
    return parsed


def load_module():
    spec = importlib.util.spec_from_file_location("paired_dev_eval", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_dev_cases(
    path: Path, *, count: int = 10, split: str = "dev", support: bool = False
) -> None:
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": f"case-{index}",
                        "split": split,
                        "category": "support_reply"
                        if support and index == count - 1
                        else "synthetic",
                        "prompt": f"request-{index}",
                    }
                    for index in range(count)
                ]
            }
        ),
        encoding="utf-8",
    )


def trusted_executable_args(module, directory: Path) -> list[str]:
    codex = directory / "codex"
    claude = directory / "claude"
    interpreter = Path("/bin/sh")
    interpreter_sha256 = module.sha256_file(interpreter.resolve())
    return [
        "--reference-executable",
        str(codex.resolve()),
        "--reference-executable-sha256",
        module.sha256_file(codex),
        "--reference-interpreter",
        str(interpreter),
        "--reference-interpreter-sha256",
        interpreter_sha256,
        "--candidate-executable",
        str(claude.resolve()),
        "--candidate-executable-sha256",
        module.sha256_file(claude),
        "--candidate-interpreter",
        str(interpreter),
        "--candidate-interpreter-sha256",
        interpreter_sha256,
        "--judge-executable",
        str(claude.resolve()),
        "--judge-executable-sha256",
        module.sha256_file(claude),
        "--judge-interpreter",
        str(interpreter),
        "--judge-interpreter-sha256",
        interpreter_sha256,
    ]


def write_trusted_executables(directory: Path, marker: str = "trusted") -> None:
    directory.mkdir()
    for executable_name in ("codex", "claude"):
        executable = directory / executable_name
        executable.write_text(f"#!/bin/sh\n# {marker}\n", encoding="utf-8")
        executable.chmod(0o700)


def create_completed_fake_run(
    module,
    tmp_path,
    *,
    support: bool = False,
    candidate_safety: dict[str, bool] | None = None,
    reference_safety: dict[str, bool] | None = None,
):
    cases_path = tmp_path / "parity_cases.dev.json"
    write_dev_cases(cases_path, support=support)
    cases = module.load_dev_cases(cases_path, expected_path=cases_path)
    protocol = module.load_protocol()
    parity = module._load_parity()
    assets = {
        "persona": "synthetic-persona",
        "instruction": "synthetic-instruction",
        "framework": "synthetic-framework",
        "language": "synthetic-language",
    }

    class Spec:
        def __init__(self, role):
            self.role = role

    specs = {role: Spec(role) for role in ("reference", "candidate", "judge")}
    providers = {
        role: {
            "provider": f"fake-{role}",
            "requested_model": f"model-{role}",
            "reported_model": f"model-{role}",
            "provider_spec_sha256": role[0] * 64,
        }
        for role in specs
    }

    def runner(spec, prompt, _attempts):
        if spec.role == "judge":
            verdict = {
                "winner": "A" if "reference_first" in prompt else "B",
                "confidence": 1.0,
                "dimensions": {
                    dimension: "draw" for dimension in protocol["dimensions"]
                },
                "reason": "private-reason-sentinel",
            }
            if "support_reply" in prompt:
                candidate_values = candidate_safety or {
                    field: True for field in SUPPORT_SAFETY_FIELDS
                }
                reference_values = reference_safety or {
                    field: False for field in SUPPORT_SAFETY_FIELDS
                }
                verdict["support_safety"] = (
                    {"A": reference_values, "B": candidate_values}
                    if "reference_first" in prompt
                    else {"A": candidate_values, "B": reference_values}
                )
            return json.dumps(verdict)
        return f"output-{module.sha256_text(prompt)[:12]}"

    def judgment_prompt(request, reference, candidate, order):
        mapping = (
            {"A": "reference", "B": "candidate", "draw": "draw"}
            if order == "reference_first"
            else {"A": "candidate", "B": "reference", "draw": "draw"}
        )
        prompt = f"judge:{order}:" + ":".join(
            module.sha256_text(value) for value in (request, reference, candidate, order)
        )
        return prompt, mapping

    run_dir = tmp_path / "run"
    kwargs = {
        "run_dir": run_dir,
        "run_mode": "iterative_dev",
        "run_id": "tamper-run",
        "fingerprint_inputs": {
            "protocol": {"version": 1},
            "inputs": {
                "planned_generation_calls": module.plan_generation_calls(
                    cases, assets, protocol
                )
            },
        },
        "cases": cases,
        "assets": assets,
        "protocol": protocol,
        "specs": specs,
        "providers": providers,
        "runner": runner,
        "judgment_prompt_fn": judgment_prompt,
        "parse_judgment_fn": lambda raw, category: module.parse_raw_judgment_then_normalize(
            raw, parity, protocol, category=category
        ),
        "normalize_winner_fn": lambda winner, mapping: mapping[winner],
        "max_attempts": 3,
        "parallel": 6,
    }
    module.run_evaluation(**kwargs)
    return run_dir, kwargs


def test_load_dev_cases_requires_exact_allowed_path_and_ten_dev_cases(tmp_path):
    module = load_module()
    allowed = tmp_path / "parity_cases.dev.json"
    write_dev_cases(allowed)

    cases = module.load_dev_cases(allowed, expected_path=allowed)
    assert len(cases) == 10
    assert all(case["split"] == "dev" for case in cases)

    alias = tmp_path / "alias.dev.json"
    alias.write_text(allowed.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="exact dedicated dev cases path"):
        module.load_dev_cases(alias, expected_path=allowed)

    write_dev_cases(allowed, count=9)
    with pytest.raises(ValueError, match="exactly 10"):
        module.load_dev_cases(allowed, expected_path=allowed)

    write_dev_cases(allowed, split="train")
    with pytest.raises(ValueError, match="dev-only"):
        module.load_dev_cases(allowed, expected_path=allowed)

    real = tmp_path / "real-dev.json"
    write_dev_cases(real)
    allowed.unlink()
    allowed.symlink_to(real)
    with pytest.raises(ValueError, match="regular non-symlink"):
        module.load_dev_cases(allowed, expected_path=allowed)


def test_prepare_run_writes_immutable_secure_manifest_and_guards_final_fresh(tmp_path):
    module = load_module()
    inputs = {
        "protocol": {"version": 1, "orders": ["reference_first", "candidate_first"]},
        "inputs": {"dev_cases": {"count": 10, "file_sha256": "a" * 64}},
    }
    run_dir = tmp_path / "iterative"
    manifest = module.prepare_run(
        run_dir,
        run_mode="iterative_dev",
        fingerprint_inputs=inputs,
        run_id="run-one",
    )
    manifest_path = run_dir / "manifest.json"
    assert manifest["fingerprint"] == module.canonical_sha256(inputs)
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600

    resumed = module.prepare_run(
        run_dir,
        run_mode="iterative_dev",
        fingerprint_inputs=inputs,
        run_id="ignored-on-resume",
    )
    assert resumed == manifest

    changed = json.loads(json.dumps(inputs))
    changed["protocol"]["version"] = 2
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        module.prepare_run(
            run_dir,
            run_mode="iterative_dev",
            fingerprint_inputs=changed,
            run_id="run-two",
        )
    fresh_dir = tmp_path / "final"
    fresh_dir.mkdir(mode=0o700)
    (fresh_dir / "old-checkpoint.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="empty artifact directory"):
        module.prepare_run(
            fresh_dir,
            run_mode="final_fresh_dev",
            fingerprint_inputs=inputs,
            run_id="run-final",
        )


def test_run_lock_excludes_concurrent_writer_and_attempt_budget_survives_resume(tmp_path):
    module = load_module()
    run_dir = tmp_path / "run"
    with module.RunLock(run_dir):
        with pytest.raises(RuntimeError, match="already locked"):
            with module.RunLock(run_dir):
                pass

    journal_path = run_dir / "attempts.jsonl"
    journal = module.AttemptJournal(
        journal_path, fingerprint="f" * 64, lifetime_attempt_budget=2
    )
    first = journal.start("call-one", {"phase": "generation"})
    journal.finish(first, {"status": "success", "output_sha256": "a" * 64})
    resumed = module.AttemptJournal(
        journal_path, fingerprint="f" * 64, lifetime_attempt_budget=2
    )
    second = resumed.start("call-two", {"phase": "generation"})
    resumed.finish(second, {"status": "success", "output_sha256": "b" * 64})
    with pytest.raises(RuntimeError, match="lifetime attempt budget exhausted"):
        resumed.start("call-three", {"phase": "generation"})


def test_run_lock_rejects_symlink_and_untrusted_run_directory(tmp_path):
    module = load_module()
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    outside = tmp_path / "outside.lock"
    outside.write_text("outside", encoding="utf-8")
    (run_dir / "run.lock").symlink_to(outside)
    with pytest.raises((OSError, ValueError), match="run.lock|artifact|symlink"):
        with module.RunLock(run_dir):
            raise AssertionError("symlink lock must not be acquired")
    assert outside.read_text(encoding="utf-8") == "outside"

    (run_dir / "run.lock").unlink()
    run_dir.chmod(0o755)
    with pytest.raises(ValueError, match="0700"):
        with module.RunLock(run_dir):
            raise AssertionError("untrusted run directory must not be used")


def test_run_lock_rejects_hardlinked_lock_file(tmp_path):
    module = load_module()
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    outside = tmp_path / "outside.lock"
    outside.write_text("", encoding="utf-8")
    outside.chmod(0o600)
    os.link(outside, run_dir / "run.lock")
    with pytest.raises(ValueError, match="run.lock|secure"):
        with module.RunLock(run_dir):
            raise AssertionError("hardlinked lock must not be acquired")


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_attempt_journal_rejects_linked_calls_artifact(tmp_path, link_kind):
    module = load_module()
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("", encoding="utf-8")
    outside.chmod(0o600)
    calls = run_dir / "calls.jsonl"
    if link_kind == "symlink":
        calls.symlink_to(outside)
    else:
        os.link(outside, calls)
    with pytest.raises((OSError, ValueError), match="calls.jsonl|artifact|secure"):
        module.AttemptJournal(calls, fingerprint="f" * 64)
    assert outside.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_secure_json_rejects_linked_checkpoint_artifact(tmp_path, link_kind):
    module = load_module()
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    outside = tmp_path / "outside.json"
    outside.write_text('{"outside":true}\n', encoding="utf-8")
    outside.chmod(0o600)
    checkpoint = run_dir / "checkpoint.json"
    if link_kind == "symlink":
        checkpoint.symlink_to(outside)
    else:
        os.link(outside, checkpoint)
    with pytest.raises((OSError, ValueError), match="checkpoint.json|artifact|secure"):
        module.save_secure_json(checkpoint, {"safe": True})
    assert json.loads(outside.read_text(encoding="utf-8")) == {"outside": True}


def test_verified_run_dirfd_survives_run_directory_path_swap(tmp_path):
    module = load_module()
    run_dir = tmp_path / "run"
    original = tmp_path / "original"
    with module.RunLock(run_dir) as lock:
        run_dir.rename(original)
        run_dir.mkdir(mode=0o700)
        module.save_secure_json(
            run_dir / "checkpoint.json",
            {"bound": "original-dirfd"},
            run_dir_fd=lock.dir_descriptor,
        )
    assert json.loads((original / "checkpoint.json").read_text(encoding="utf-8")) == {
        "bound": "original-dirfd"
    }
    assert not (run_dir / "checkpoint.json").exists()

def test_invoke_provider_journals_hashes_metadata_and_no_raw_content(tmp_path):
    module = load_module()
    journal_path = tmp_path / "attempts.jsonl"
    journal = module.AttemptJournal(journal_path, fingerprint="f" * 64)
    prompt = "sensitive-prompt-sentinel"
    output = "sensitive-output-sentinel"
    observed = []

    def fake_runner(spec, actual_prompt, attempts):
        records = journal_path.read_text(encoding="utf-8").splitlines()
        assert len(records) == 1
        assert json.loads(records[0])["event"] == "attempt_started"
        observed.append((spec, actual_prompt, attempts))
        return output

    result = module.invoke_provider(
        journal=journal,
        logical_call_id="gen:case-0:reference",
        phase="generation",
        prompt=prompt,
        spec=object(),
        provider_metadata={"provider": "fake", "requested_model": "model-a"},
        context={"case_id": "case-0", "arm": "reference", "order": None},
        runner=fake_runner,
        max_attempts=1,
    )

    assert result == output
    assert observed == [(observed[0][0], prompt, 1)]
    assert stat.S_IMODE(journal_path.stat().st_mode) == 0o600
    serialized = journal_path.read_text(encoding="utf-8")
    assert prompt not in serialized
    assert output not in serialized
    records = [json.loads(line) for line in serialized.splitlines()]
    assert [record["event"] for record in records] == ["attempt_started", "attempt_finished"]
    assert records[0]["prompt_sha256"] == module.sha256_text(prompt)
    assert records[1]["output_sha256"] == module.sha256_text(output)
    assert records[0]["provider"] == "fake"
    assert records[0]["requested_model"] == "model-a"
    assert records[1]["status"] == "success"


def test_invoke_provider_counts_each_failed_and_successful_retry(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    journal = module.AttemptJournal(tmp_path / "attempts.jsonl", fingerprint="f" * 64)
    invocations = 0

    def flaky_runner(_spec, _prompt, attempts):
        nonlocal invocations
        assert attempts == 1
        invocations += 1
        if invocations == 1:
            raise TimeoutError("synthetic timeout")
        return "safe-output"

    assert module.invoke_provider(
        journal=journal,
        logical_call_id="gen:case-0:reference",
        phase="generation",
        prompt="safe-prompt",
        spec=object(),
        provider_metadata={"provider": "fake", "requested_model": "model-a"},
        context={"case_id": "case-0", "arm": "reference", "order": None},
        runner=flaky_runner,
        max_attempts=2,
    ) == "safe-output"
    records = journal.records()
    starts = [record for record in records if record["event"] == "attempt_started"]
    finishes = [record for record in records if record["event"] == "attempt_finished"]
    assert [record["attempt_no"] for record in starts] == [1, 2]
    assert [record["status"] for record in finishes] == ["error", "success"]


def test_resume_uses_only_remaining_logical_attempt_and_lifetime_backoff(
    tmp_path, monkeypatch
):
    module = load_module()
    journal_path = tmp_path / "attempts.jsonl"
    initial = module.AttemptJournal(
        journal_path, fingerprint="f" * 64, lifetime_attempt_budget=10
    )
    for _ in range(2):
        started = initial.start("logical-call", {"phase": "generation"})
        initial.finish(
            started,
            {"status": "error", "error_type": "TimeoutError", "output_sha256": None},
        )

    resumed = module.AttemptJournal(
        journal_path, fingerprint="f" * 64, lifetime_attempt_budget=10
    )
    sleeps = []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    calls = 0

    def succeeds(_spec, _prompt, _attempts):
        nonlocal calls
        calls += 1
        return "safe-output"

    assert module.invoke_provider(
        journal=resumed,
        logical_call_id="logical-call",
        phase="generation",
        prompt="safe-prompt",
        spec=object(),
        provider_metadata={"provider": "fake", "requested_model": "model"},
        context={"case_id": "case-0", "arm": "reference", "order": None},
        runner=succeeds,
        max_attempts=3,
    ) == "safe-output"
    assert calls == 1
    assert sleeps == [4]

    called_again = False

    def forbidden(*_args):
        nonlocal called_again
        called_again = True
        raise AssertionError("fourth logical attempt must not run")

    with pytest.raises(RuntimeError, match="logical attempt budget exhausted"):
        module.invoke_provider(
            journal=resumed,
            logical_call_id="logical-call",
            phase="generation",
            prompt="safe-prompt",
            spec=object(),
            provider_metadata={"provider": "fake", "requested_model": "model"},
            context={"case_id": "case-0", "arm": "reference", "order": None},
            runner=forbidden,
            max_attempts=3,
        )
    assert called_again is False


def test_invalid_judge_dimensions_are_retried_and_never_marked_success(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    protocol = module.load_protocol()
    journal = module.AttemptJournal(tmp_path / "attempts.jsonl", fingerprint="f" * 64)
    responses = [
        json.dumps(
            {
                "winner": "A",
                "confidence": 1.0,
                "dimensions": {"naturalness": "A"},
                "reason": "",
            }
        ),
        json.dumps(
            {
                "winner": "draw",
                "confidence": 1.0,
                "dimensions": {dimension: "draw" for dimension in protocol["dimensions"]},
                "reason": "",
            }
        ),
    ]

    call = module.invoke_provider_audited(
        journal=journal,
        logical_call_id="judge:case-0:base_writer:reference_first",
        phase="judgment",
        prompt="safe-judge-prompt",
        spec=object(),
        provider_metadata={"provider": "fake", "requested_model": "judge-model"},
        context={"case_id": "case-0", "arm": "base_writer", "order": "reference_first"},
        runner=lambda _spec, _prompt, _attempts: responses.pop(0),
        parser=lambda value: module.parse_and_validate_judgment(
            value, parse_fake_judgment, protocol, "synthetic"
        ),
        max_attempts=2,
    )
    assert call["parsed"]["winner"] == "draw"
    finishes = [
        record for record in journal.records() if record["event"] == "attempt_finished"
    ]
    assert [record["status"] for record in finishes] == ["invalid", "success"]


def test_fingerprint_inputs_bind_protocol_source_cases_assets_config_and_providers(tmp_path):
    module = load_module()
    protocol = module.load_protocol(module.PROTOCOL_PATH)
    cases_path = tmp_path / "parity_cases.dev.json"
    write_dev_cases(cases_path)
    cases = module.load_dev_cases(cases_path, expected_path=cases_path)
    config_path = tmp_path / "providers.json"
    config_path.write_text('{"audit":"config"}', encoding="utf-8")
    asset_paths = {}
    for name in ("persona", "instruction", "framework", "language"):
        path = tmp_path / f"{name}.md"
        path.write_text(f"synthetic-{name}", encoding="utf-8")
        asset_paths[name] = path
    providers = {
        role: {
            "provider": f"provider-{role}",
            "requested_model": f"model-{role}",
            "provider_spec_sha256": role[0] * 64,
            "trusted_executable_path": f"/trusted/{role}",
            "resolved_executable_path": f"/trusted/{role}",
            "executable_sha256": role[0] * 64,
            "launcher_chain": [
                {
                    "kind": "executable",
                    "trusted_path": f"/trusted/{role}",
                    "resolved_path": f"/trusted/{role}",
                    "sha256": role[0] * 64,
                }
            ],
        }
        for role in ("reference", "candidate", "judge")
    }

    inputs = module.build_fingerprint_inputs(
        protocol=protocol,
        protocol_path=module.PROTOCOL_PATH,
        cases=cases,
        cases_path=cases_path,
        config_path=config_path,
        asset_paths=asset_paths,
        providers=providers,
        evaluator_path=module.MODULE_PATH,
        parity_path=module.PARITY_PATH,
        judge_prompt="synthetic-judge-template",
    )
    assert set(inputs) == {"protocol", "inputs"}
    assert inputs["protocol"]["evaluator_source_sha256"] == module.sha256_file(
        module.MODULE_PATH
    )
    assert inputs["inputs"]["dev_cases"]["count"] == 10
    assert len(inputs["inputs"]["planned_generation_calls"]) == 50
    assert all(
        len(call["prompt_sha256"]) == 64
        for call in inputs["inputs"]["planned_generation_calls"]
    )

    before = module.canonical_sha256(inputs)
    asset_paths["language"].write_text("changed-language", encoding="utf-8")
    changed = module.build_fingerprint_inputs(
        protocol=protocol,
        protocol_path=module.PROTOCOL_PATH,
        cases=cases,
        cases_path=cases_path,
        config_path=config_path,
        asset_paths=asset_paths,
        providers=providers,
        evaluator_path=module.MODULE_PATH,
        parity_path=module.PARITY_PATH,
        judge_prompt="synthetic-judge-template",
    )
    assert module.canonical_sha256(changed) != before


def test_base_writer_and_policy_arms_have_exact_ordered_components():
    module = load_module()
    protocol = module.load_protocol()
    assert list(protocol["arms"]) == [
        "base_writer",
        "framework",
        "language",
        "combined",
    ]
    assets = {
        "persona": "persona",
        "instruction": "instruction",
        "framework": "framework",
        "language": "language",
    }
    expected = {
        "base_writer": ["persona", "instruction", "request"],
        "framework": ["persona", "instruction", "request", "framework"],
        "language": ["persona", "instruction", "request", "language"],
        "combined": [
            "persona",
            "instruction",
            "request",
            "framework",
            "language",
        ],
    }
    for arm, component_names in expected.items():
        _prompt, components = module.compose_candidate_prompt(
            "request", arm, assets, protocol
        )
        assert [component["name"] for component in components] == component_names
    assert protocol["common_candidate_components"] == [
        "persona",
        "instruction",
        "request",
    ]
    assert protocol["experimental_factors"] == ["framework", "language"]


@pytest.mark.parametrize(
    "text",
    [
        "以下に回答案を作成しました。本文",
        "  ご依頼いただいた文面をまとめます：本文",
        "回答案です。本文",
        "文面例を提示します。本文",
    ],
)
def test_opening_meta_detector_flags_versioned_opening_variants(text):
    module = load_module()
    result = module.detect_opening_meta(text, module.load_protocol())
    assert result["version"] == 1
    assert result["matched"] is True
    assert result["rule_id"]


@pytest.mark.parametrize(
    "text",
    [
        "以下の条件を満たす必要があります。",
        "本文です。回答案です。",
        "ご依頼ありがとうございます。",
        "回答は一つとは限りません。",
    ],
)
def test_opening_meta_detector_rejects_hard_negatives_and_late_occurrences(text):
    module = load_module()
    result = module.detect_opening_meta(text, module.load_protocol())
    assert result == {"version": 1, "matched": False, "rule_id": None}


def test_opening_meta_transition_table_is_paired_by_framework_stratum():
    module = load_module()
    rows = [
        {"base_writer": True, "framework": False, "language": False, "combined": False},
        {"base_writer": False, "framework": True, "language": True, "combined": False},
        {"base_writer": True, "framework": True, "language": False, "combined": False},
        {"base_writer": False, "framework": False, "language": True, "combined": True},
    ]
    transitions = module.opening_meta_transitions(rows)
    assert transitions["framework_without_language"] == {
        "removed": 1,
        "introduced": 1,
        "stayed_present": 1,
        "stayed_absent": 1,
        "net_removed_rate": 0.0,
    }
    assert transitions["framework_with_language"] == {
        "removed": 1,
        "introduced": 0,
        "stayed_present": 1,
        "stayed_absent": 2,
        "net_removed_rate": 0.25,
    }


def test_run_evaluation_records_complete_audit_provenance_with_fake_providers(tmp_path):
    module = load_module()
    cases_path = tmp_path / "parity_cases.dev.json"
    write_dev_cases(cases_path)
    cases = module.load_dev_cases(cases_path, expected_path=cases_path)
    protocol = module.load_protocol()
    assets = {
        "persona": "synthetic-persona",
        "instruction": "synthetic-instruction",
        "framework": "synthetic-framework",
        "language": "synthetic-language",
    }

    class Spec:
        def __init__(self, role):
            self.role = role

    specs = {role: Spec(role) for role in ("reference", "candidate", "judge")}
    providers = {
        role: {
            "provider": f"fake-{role}",
            "requested_model": f"model-{role}",
            "reported_model": f"model-{role}",
            "provider_spec_sha256": role[0] * 64,
        }
        for role in specs
    }
    run_dir = tmp_path / "run"
    lock = threading.Lock()
    calls = []

    def fake_runner(spec, prompt, attempts):
        assert (run_dir / "manifest.json").is_file()
        with lock:
            calls.append((spec.role, module.sha256_text(prompt), attempts))
        if spec.role == "judge":
            return json.dumps(
                {
                    "winner": "draw",
                    "confidence": 1.0,
                    "dimensions": {dimension: "draw" for dimension in protocol["dimensions"]},
                    "reason": "",
                }
            )
        return f"synthetic-output-{module.sha256_text(prompt)[:12]}"

    def fake_judgment_prompt(request, reference, candidate, order):
        mapping = (
            {"A": "reference", "B": "candidate", "draw": "draw"}
            if order == "reference_first"
            else {"A": "candidate", "B": "reference", "draw": "draw"}
        )
        prompt = "judge:" + ":".join(
            module.sha256_text(value) for value in (request, reference, candidate, order)
        )
        return prompt, mapping

    result = module.run_evaluation(
        run_dir=run_dir,
        run_mode="iterative_dev",
        run_id="synthetic-run",
        fingerprint_inputs={
            "protocol": {"version": 1},
            "inputs": {
                "count": 10,
                "planned_generation_calls": module.plan_generation_calls(
                    cases, assets, protocol
                ),
            },
        },
        cases=cases,
        assets=assets,
        protocol=protocol,
        specs=specs,
        providers=providers,
        runner=fake_runner,
        judgment_prompt_fn=fake_judgment_prompt,
        parse_judgment_fn=parse_fake_judgment,
        normalize_winner_fn=lambda winner, mapping: mapping[winner],
        max_attempts=3,
        parallel=6,
    )

    assert len(calls) == 130
    assert result["counts"] == {
        "cases": 10,
        "reference_generations": 10,
        "candidate_generations": 40,
        "generations_total": 50,
        "judgments": 80,
        "orders_per_arm_case": 2,
    }
    assert all(
        proof["judgment_uses"] == 8
        and proof["distinct_reference_ids"] == 1
        and proof["distinct_reference_hashes"] == 1
        for proof in result["provenance"]["reference_reuse"].values()
    )
    assert result["provenance"]["attempts"] == {
        "started": 130,
        "finished": 130,
        "succeeded": 130,
        "failed": 0,
        "indeterminate": 0,
    }
    checkpoint = json.loads((run_dir / "checkpoint.json").read_text(encoding="utf-8"))
    assert len(checkpoint["generations"]) == 50
    assert len(checkpoint["judgments"]) == 80
    for name in ("manifest.json", "calls.jsonl", "checkpoint.json", "result.json"):
        assert stat.S_IMODE((run_dir / name).stat().st_mode) == 0o600
    public_artifacts = "".join(
        (run_dir / name).read_text(encoding="utf-8")
        for name in ("manifest.json", "calls.jsonl", "result.json")
    )
    assert "request-0" not in public_artifacts
    assert "synthetic-output-" not in public_artifacts


@pytest.mark.parametrize("tamper", ["text", "winner", "dimension", "order"])
def test_resume_rejects_checkpoint_tampering_before_provider_call(tmp_path, tamper):
    module = load_module()
    run_dir, kwargs = create_completed_fake_run(module, tmp_path)
    checkpoint_path = run_dir / "checkpoint.json"
    state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if tamper == "text":
        state["generations"]["case-0::reference"]["text"] += "-tampered"
    else:
        verdict = state["judgments"]["case-0::base_writer::reference_first"]
        if tamper == "winner":
            verdict["winner"] = "B" if verdict["winner"] != "B" else "A"
        elif tamper == "dimension":
            verdict["dimensions"]["naturalness"] = "invalid"
        else:
            verdict["order"] = "candidate_first"
    module.save_secure_json(checkpoint_path, state)
    called = False

    def forbidden_runner(*_args):
        nonlocal called
        called = True
        raise AssertionError("provider must not run after checkpoint tampering")

    kwargs["runner"] = forbidden_runner
    with pytest.raises(ValueError, match="checkpoint integrity"):
        module.run_evaluation(**kwargs)
    assert called is False


def test_provider_audit_metadata_hashes_executable_and_env_names_not_values(tmp_path):
    module = load_module()

    executable = tmp_path / "fake-cli"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)

    class Spec:
        role = "candidate"
        identity = "declared-identity"
        argv = (str(executable), "--token", "secret-argv-sentinel")
        configured_argv = ("fake-cli", "--token", "secret-argv-sentinel")
        trusted_executable_path = str(executable.resolve())
        resolved_executable_path = str(executable.resolve())
        executable_sha256 = module.sha256_file(executable)
        launcher_chain = [
            {
                "kind": "executable",
                "trusted_path": str(executable.resolve()),
                "resolved_path": str(executable.resolve()),
                "sha256": executable_sha256,
            }
        ]
        audit_role = "candidate"
        input_mode = "stdin"
        output_mode = "stdout"
        timeout_sec = 30
        cwd_mode = "temp"
        env = (("ANTHROPIC_API_KEY", "secret-env-sentinel"),)

    metadata = module.provider_audit_metadata(
        Spec(),
        {"provider": "fake-provider", "model": "fake-model"},
        environ={"PATH": "/safe/bin", "ANTHROPIC_API_KEY": "ambient-secret"},
    )
    serialized = json.dumps(metadata)
    assert metadata["provider"] == "fake-provider"
    assert metadata["requested_model"] == "fake-model"
    assert len(metadata["provider_spec_sha256"]) == 64
    assert metadata["reported_model"] is None
    assert metadata["resolved_executable_path"] == str(executable.resolve())
    assert metadata["executable_sha256"] == module.sha256_file(executable)
    assert metadata["effective_environment_keys"] == ["ANTHROPIC_API_KEY", "PATH"]
    assert "secret-argv-sentinel" not in serialized
    assert "secret-env-sentinel" not in serialized
    assert "ambient-secret" not in serialized


def test_secure_provider_adapter_uses_stdin_isolated_cwd_and_allowlisted_env(tmp_path):
    module = load_module()
    executable = tmp_path / "claude"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o700)

    class Spec:
        role = "candidate"
        audit_role = "candidate"
        resolved_executable_path = str(executable.resolve())
        executable_sha256 = module.sha256_file(executable)
        launcher_chain = [
            {
                "kind": "executable",
                "sha256": executable_sha256,
            }
        ]
        launcher_fds = (os.open(executable, os.O_RDONLY),)
        interpreter_args = ()
        configured_argv = ("claude", "-p", "--safe-mode", "--no-session-persistence")
        argv = (resolved_executable_path, "-p", "--safe-mode", "--no-session-persistence")
        output_mode = "stdout"
        timeout_sec = 30
        cwd_mode = "temp"
        env = (("ANTHROPIC_API_KEY", "allowed-secret"),)

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        assert stat.S_IMODE(Path(kwargs["cwd"]).stat().st_mode) == 0o700
        return subprocess.CompletedProcess(argv, 0, stdout="safe-output", stderr="")

    output = module.secure_run_provider(
        Spec(),
        "private-prompt-sentinel",
        attempts=1,
        environ={
            "PATH": "/safe/bin",
            "LANG": "C.UTF-8",
            "UNRELATED_SECRET": "must-not-propagate",
        },
        run_command=fake_run,
    )
    assert output == "safe-output"
    assert captured["argv"][0].startswith("/proc/self/fd/")
    assert captured["pass_fds"] == Spec.launcher_fds
    assert "private-prompt-sentinel" not in captured["argv"]
    assert captured["input"] == "private-prompt-sentinel"
    assert captured["shell"] is False
    assert set(captured["env"]) == {"PATH", "LANG", "ANTHROPIC_API_KEY"}
    assert captured["env"]["PATH"] == "/usr/bin:/bin"
    assert "UNRELATED_SECRET" not in captured["env"]


def test_main_wires_pinned_real_executables_and_role_isolated_environments(
    tmp_path, monkeypatch
):
    module = load_module()
    trusted = tmp_path / "trusted"
    substituted = tmp_path / "substituted"
    trusted.mkdir()
    substituted.mkdir()
    for directory, marker in ((trusted, "trusted"), (substituted, "substituted")):
        for executable_name in ("codex", "claude"):
            executable = directory / executable_name
            executable.write_text(f"#!/bin/sh\n# {marker}\n", encoding="utf-8")
            executable.chmod(0o700)

    captured = {}

    def fake_run_evaluation(**kwargs):
        captured.update(kwargs)
        return {
            "fingerprint": "f" * 64,
            "counts": {"cases": 10},
            "language_acceptance_observed": {"accepted": False},
        }

    monkeypatch.setenv("PATH", str(substituted))
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret-sentinel")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret-sentinel")
    monkeypatch.setattr(module, "run_evaluation", fake_run_evaluation)
    assert (
        module.main(
            [
                "--run-dir",
                str(tmp_path / "run"),
                *trusted_executable_args(module, trusted),
            ]
        )
        == 0
    )

    serialized_inputs = json.dumps(captured["fingerprint_inputs"])
    assert "openai-secret-sentinel" not in serialized_inputs
    assert "anthropic-secret-sentinel" not in serialized_inputs
    assert captured["fingerprint_inputs"]["inputs"]["trusted_executable_pins"] == {
        role: {"launcher_chain": captured["providers"][role]["launcher_chain"]}
        for role in ("reference", "candidate", "judge")
    }
    reference_keys = captured["providers"]["reference"]["effective_environment_keys"]
    candidate_keys = captured["providers"]["candidate"]["effective_environment_keys"]
    assert {"OPENAI_API_KEY", "PATH"}.issubset(reference_keys)
    assert "ANTHROPIC_API_KEY" not in reference_keys
    assert {"ANTHROPIC_API_KEY", "PATH"}.issubset(candidate_keys)
    assert "OPENAI_API_KEY" not in candidate_keys

    monkeypatch.setenv("PATH", str(substituted))
    invoked = {}

    def fake_run(argv, **kwargs):
        invoked["argv"] = argv
        invoked["env"] = kwargs["env"]
        invoked["pass_fds"] = kwargs["pass_fds"]
        invoked["launcher_bytes"] = [
            Path(f"/proc/self/fd/{descriptor}").read_bytes()
            for descriptor in kwargs["pass_fds"]
        ]
        if "-o" in argv:
            Path(argv[argv.index("-o") + 1]).write_text("output", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="output", stderr="")

    for role in ("reference", "candidate", "judge"):
        spec = captured["specs"][role]
        invoked.clear()
        assert module.secure_run_provider(
            spec, "private", environ=dict(os.environ), run_command=fake_run
        ) == "output"
        expected_name = "codex" if role == "reference" else "claude"
        assert invoked["argv"][0].startswith("/proc/self/fd/")
        assert invoked["argv"][1].startswith("/proc/self/fd/")
        assert (trusted / expected_name).read_bytes() in invoked["launcher_bytes"]
        assert Path("/bin/sh").resolve().read_bytes() in invoked["launcher_bytes"]
        assert invoked["env"]["PATH"] == "/usr/bin:/bin"
        if role == "reference":
            assert "OPENAI_API_KEY" in invoked["env"]
            assert "ANTHROPIC_API_KEY" not in invoked["env"]
        else:
            assert "ANTHROPIC_API_KEY" in invoked["env"]
            assert "OPENAI_API_KEY" not in invoked["env"]


def test_env_shebang_uses_pinned_interpreter_and_not_malicious_path(
    tmp_path, monkeypatch
):
    module = load_module()
    trusted = tmp_path / "trusted"
    malicious = tmp_path / "malicious"
    trusted.mkdir()
    malicious.mkdir()
    for executable_name in ("codex", "claude"):
        script = trusted / executable_name
        script.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        script.chmod(0o700)
    trusted_node = trusted / "node"
    shutil.copyfile(Path("/bin/true").resolve(), trusted_node)
    trusted_node.chmod(0o700)
    malicious_node = malicious / "node"
    malicious_node.write_text("#!/bin/sh\n# malicious\n", encoding="utf-8")
    malicious_node.chmod(0o700)
    args = ["--run-dir", str(tmp_path / "run")]
    for role, executable_name in (
        ("reference", "codex"),
        ("candidate", "claude"),
        ("judge", "claude"),
    ):
        executable = trusted / executable_name
        args.extend(
            [
                f"--{role}-executable",
                str(executable),
                f"--{role}-executable-sha256",
                module.sha256_file(executable),
                f"--{role}-interpreter",
                str(trusted_node),
                f"--{role}-interpreter-sha256",
                module.sha256_file(trusted_node),
            ]
        )
    captured = {}

    def fake_run_evaluation(**kwargs):
        captured.update(kwargs)
        return {
            "fingerprint": "f" * 64,
            "counts": {"cases": 10},
            "language_acceptance_observed": {"accepted": False},
        }

    monkeypatch.setenv("PATH", str(malicious))
    monkeypatch.setattr(module, "run_evaluation", fake_run_evaluation)
    assert module.main(args) == 0
    invocation = {}

    def inspect_run(argv, **kwargs):
        invocation["argv"] = argv
        invocation["env"] = kwargs["env"]
        invocation["bytes"] = [
            Path(f"/proc/self/fd/{descriptor}").read_bytes()
            for descriptor in kwargs["pass_fds"]
        ]
        return subprocess.CompletedProcess(argv, 0, stdout="output", stderr="")

    assert module.secure_run_provider(
        captured["specs"]["candidate"], "private", run_command=inspect_run
    ) == "output"
    assert invocation["argv"][:2] == [
        f"/proc/self/fd/{captured['specs']['candidate'].launcher_fds[0]}",
        f"/proc/self/fd/{captured['specs']['candidate'].launcher_fds[1]}",
    ]
    assert invocation["bytes"][0] == trusted_node.read_bytes()
    assert b"malicious" not in invocation["bytes"][0]
    assert invocation["env"]["PATH"] == "/usr/bin:/bin"


@pytest.mark.parametrize("failure", ["missing-path", "wrong-digest"])
def test_main_rejects_invalid_trusted_pin_before_provider_config_load(
    tmp_path, monkeypatch, failure
):
    module = load_module()
    trusted = tmp_path / "trusted"
    write_trusted_executables(trusted)
    args = ["--run-dir", str(tmp_path / "run"), *trusted_executable_args(module, trusted)]
    if failure == "missing-path":
        args[args.index("--reference-executable") + 1] = str(
            (tmp_path / "missing" / "codex").resolve()
        )
    else:
        args[args.index("--reference-executable-sha256") + 1] = "0" * 64

    config_loaded = False

    def forbidden_config_load(*_args, **_kwargs):
        nonlocal config_loaded
        config_loaded = True
        raise AssertionError("provider config must not load after invalid trust pin")

    monkeypatch.setattr(module, "_load_provider_bundle", forbidden_config_load)
    with pytest.raises(ValueError, match="trusted executable"):
        module.main(args)
    assert config_loaded is False


def test_provider_refuses_post_pin_executable_mutation(tmp_path, monkeypatch):
    module = load_module()
    trusted = tmp_path / "trusted"
    write_trusted_executables(trusted)
    captured = {}

    def fake_run_evaluation(**kwargs):
        captured.update(kwargs)
        return {
            "fingerprint": "f" * 64,
            "counts": {"cases": 10},
            "language_acceptance_observed": {"accepted": False},
        }

    monkeypatch.setattr(module, "run_evaluation", fake_run_evaluation)
    assert (
        module.main(
            [
                "--run-dir",
                str(tmp_path / "run"),
                *trusted_executable_args(module, trusted),
            ]
        )
        == 0
    )
    replacement = tmp_path / "replacement-claude"
    replacement.write_text("#!/bin/sh\n# mutated\n", encoding="utf-8")
    replacement.chmod(0o700)
    os.replace(replacement, trusted / "claude")
    captured_launcher = {}

    def inspect_run(argv, **kwargs):
        captured_launcher["bytes"] = [
            Path(f"/proc/self/fd/{descriptor}").read_bytes()
            for descriptor in kwargs["pass_fds"]
        ]
        return subprocess.CompletedProcess(argv, 0, stdout="output", stderr="")

    assert module.secure_run_provider(
        captured["specs"]["candidate"], "private", run_command=inspect_run
    ) == "output"
    assert b"trusted" in captured_launcher["bytes"][1]
    assert b"mutated" not in captured_launcher["bytes"][1]


def test_dry_run_reports_reviewed_executable_pins(tmp_path, monkeypatch, capsys):
    module = load_module()
    trusted = tmp_path / "trusted"
    malicious = tmp_path / "malicious"
    write_trusted_executables(trusted)
    write_trusted_executables(malicious, marker="malicious")
    monkeypatch.setenv("PATH", str(malicious))

    assert (
        module.main(
            [
                "--run-dir",
                str(tmp_path / "unused"),
                "--dry-run",
                *trusted_executable_args(module, trusted),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["support_safety"] == {
        "category": "support_reply",
        "fields": list(SUPPORT_SAFETY_FIELDS),
        "required_judgments": 8,
        "acceptance_gate": "all_candidate_fields_true_all_arms_both_orders",
    }
    for role, executable_name in (
        ("reference", "codex"),
        ("candidate", "claude"),
        ("judge", "claude"),
    ):
        pin = report["providers"][role]["trusted_executable"]
        assert pin["path"] == str((trusted / executable_name).resolve())
        assert pin["sha256"] == module.sha256_file(trusted / executable_name)
        assert [entry["kind"] for entry in report["providers"][role]["launcher_chain"]] == [
            "interpreter",
            "executable",
        ]


def test_protocol_pins_exact_provider_model_executable_and_isolation_flags(tmp_path):
    module = load_module()
    protocol = module.load_protocol()
    parity = module._load_parity()
    trusted = tmp_path / "trusted"
    write_trusted_executables(trusted)
    raw_pins = {
        role: {
            "path": trusted / ("codex" if role == "reference" else "claude"),
            "sha256": module.sha256_file(
                trusted / ("codex" if role == "reference" else "claude")
            ),
            "interpreter_path": Path("/bin/sh"),
            "interpreter_sha256": module.sha256_file(Path("/bin/sh").resolve()),
        }
        for role in ("reference", "candidate", "judge")
    }
    specs, providers = module._load_provider_bundle(
        module.CONFIG_PATH,
        parity,
        module.validate_trusted_executable_pins(raw_pins),
    )
    module.validate_provider_protocol(specs, providers, protocol)

    assert {"--safe-mode", "--no-session-persistence"}.issubset(
        set(specs["candidate"].argv)
    )
    assert {"--ignore-user-config", "--ephemeral", "read-only"}.issubset(
        set(specs["reference"].argv)
    )

    candidate = specs["candidate"]
    drifted = SimpleNamespace(
        **{
            name: getattr(candidate, name)
            for name in (
                "role",
                "identity",
                "input_mode",
                "output_mode",
                "timeout_sec",
                "cwd_mode",
                "env",
                "audit_role",
                "resolved_executable_path",
                "trusted_executable_path",
                "executable_sha256",
                "argv",
            )
        },
        configured_argv=tuple(
            arg for arg in candidate.configured_argv if arg != "--safe-mode"
        ),
    )
    with pytest.raises(ValueError, match="provider protocol mismatch"):
        module.validate_provider_protocol(
            {**specs, "candidate": drifted}, providers, protocol
        )


@pytest.mark.parametrize(
    "dimensions",
    [
        {"naturalness": "A"},
        {
            "correctness": "A",
            "naturalness": "invalid",
            "context_fit": "A",
            "conciseness": "A",
            "tone": "A",
        },
    ],
)
def test_main_wires_raw_schema_validation_before_parity_normalization(
    tmp_path, monkeypatch, dimensions
):
    module = load_module()
    trusted = tmp_path / "trusted"
    write_trusted_executables(trusted)
    captured = {}

    def fake_run_evaluation(**kwargs):
        captured.update(kwargs)
        return {
            "fingerprint": "f" * 64,
            "counts": {"cases": 10},
            "language_acceptance_observed": {"accepted": False},
        }

    monkeypatch.setattr(module, "run_evaluation", fake_run_evaluation)
    assert (
        module.main(
            [
                "--run-dir",
                str(tmp_path / "run"),
                *trusted_executable_args(module, trusted),
            ]
        )
        == 0
    )
    raw = json.dumps(
        {
            "winner": "A",
            "confidence": 1.0,
            "dimensions": dimensions,
            "reason": "",
        }
    )
    with pytest.raises(ValueError, match="raw judge"):
        captured["parse_judgment_fn"](raw, "business_chat")


def test_support_safety_normalizes_candidate_independent_of_answer_order():
    module = load_module()
    protocol = module.load_protocol()
    parity = module._load_parity()
    fields = protocol["support_safety"]["fields"]
    answer_a = {field: index % 2 == 0 for index, field in enumerate(fields)}
    answer_b = {field: not value for field, value in answer_a.items()}
    raw = json.dumps(
        {
            "winner": "draw",
            "confidence": 1.0,
            "dimensions": {
                dimension: "draw" for dimension in protocol["dimensions"]
            },
            "reason": "",
            "support_safety": {"A": answer_a, "B": answer_b},
        }
    )
    parsed = module.parse_raw_judgment_then_normalize(
        raw, parity, protocol, category="support_reply"
    )
    reference_first = module.normalize_support_safety(
        parsed,
        {"A": "reference", "B": "candidate", "draw": "draw"},
    )
    candidate_first = module.normalize_support_safety(
        parsed,
        {"A": "candidate", "B": "reference", "draw": "draw"},
    )
    assert reference_first == {"candidate": answer_b, "reference": answer_a}
    assert candidate_first == {"candidate": answer_a, "reference": answer_b}


@pytest.mark.parametrize("defect", ["missing", "extra", "nonboolean"])
def test_support_safety_strict_raw_schema_rejects_and_retries(
    tmp_path, monkeypatch, defect
):
    module = load_module()
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    protocol = module.load_protocol()
    parity = module._load_parity()
    fields = protocol["support_safety"]["fields"]
    invalid_safety = {
        answer: {field: True for field in fields} for answer in ("A", "B")
    }
    if defect == "missing":
        invalid_safety["A"].pop(fields[0])
    elif defect == "extra":
        invalid_safety["A"]["unexpected"] = True
    else:
        invalid_safety["A"][fields[0]] = 1

    def raw(safety):
        return json.dumps(
            {
                "winner": "draw",
                "confidence": 1.0,
                "dimensions": {
                    dimension: "draw" for dimension in protocol["dimensions"]
                },
                "reason": "",
                "support_safety": safety,
            }
        )

    responses = [
        raw(invalid_safety),
        raw({answer: {field: True for field in fields} for answer in ("A", "B")}),
    ]
    journal = module.AttemptJournal(tmp_path / "calls.jsonl", fingerprint="f" * 64)
    call = module.invoke_provider_audited(
        journal=journal,
        logical_call_id="judge:support:base_writer:reference_first",
        phase="judgment",
        prompt="safe",
        spec=object(),
        provider_metadata={"provider": "fake", "requested_model": "judge"},
        context={"case_id": "support", "arm": "base_writer", "order": "reference_first"},
        runner=lambda *_args: responses.pop(0),
        parser=lambda value: module.parse_raw_judgment_then_normalize(
            value, parity, protocol, category="support_reply"
        ),
        max_attempts=2,
    )
    assert all(call["parsed"]["support_safety_by_answer"]["A"].values())
    assert [
        record["status"]
        for record in journal.records()
        if record["event"] == "attempt_finished"
    ] == ["invalid", "success"]


def test_support_safety_gate_ignores_reference_equivalence_and_aggregates_only(tmp_path):
    module = load_module()
    run_dir, _kwargs = create_completed_fake_run(
        module,
        tmp_path,
        support=True,
        candidate_safety={field: True for field in SUPPORT_SAFETY_FIELDS},
        reference_safety={field: False for field in SUPPORT_SAFETY_FIELDS},
    )
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    safety = result["support_safety"]
    assert safety["passed"] is True
    assert safety["judgments"] == 8
    assert all(value["all"] for value in safety["fields"].values())
    assert result["language_acceptance_observed"]["checks"]["support_safety"] is True
    assert all(
        result["arms"][arm]["candidate_preference"] == 0.0
        for arm in ("base_writer", "framework", "language", "combined")
    )
    assert all(
        "support_reply" in result["arms"][arm]["categories"]
        for arm in ("base_writer", "framework", "language", "combined")
    )
    serialized = json.dumps(safety)
    assert "reference" not in serialized
    assert "reason" not in serialized
    assert "private-reason-sentinel" not in json.dumps(result)
    checkpoint = (run_dir / "checkpoint.json").read_text(encoding="utf-8")
    assert "private-reason-sentinel" in checkpoint


def test_one_failed_support_safety_boolean_blocks_acceptance(tmp_path):
    module = load_module()
    candidate = {field: True for field in SUPPORT_SAFETY_FIELDS}
    candidate["masking"] = False
    run_dir, _kwargs = create_completed_fake_run(
        module, tmp_path, support=True, candidate_safety=candidate
    )
    result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert result["support_safety"]["fields"]["masking"] == {
        "true": 0,
        "total": 8,
        "all": False,
    }
    checks = result["language_acceptance_observed"]["checks"]
    assert next(iter(checks)) == "support_safety"
    assert checks["support_safety"] is False
    assert result["language_acceptance_observed"]["accepted"] is False


def test_resume_rejects_tampered_normalized_support_safety_before_calls(tmp_path):
    module = load_module()
    run_dir, kwargs = create_completed_fake_run(module, tmp_path, support=True)
    checkpoint_path = run_dir / "checkpoint.json"
    state = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    key = "case-9::base_writer::reference_first"
    state["judgments"][key]["support_safety"]["candidate"]["masking"] = False
    module.save_secure_json(checkpoint_path, state)
    called = False

    def forbidden_runner(*_args):
        nonlocal called
        called = True
        raise AssertionError("provider must not run after support checkpoint tampering")

    kwargs["runner"] = forbidden_runner
    with pytest.raises(ValueError, match="checkpoint integrity"):
        module.run_evaluation(**kwargs)
    assert called is False


def test_cli_has_no_case_or_split_override_and_tracked_dev_artifact_is_valid(tmp_path):
    module = load_module()
    trusted = tmp_path / "trusted"
    write_trusted_executables(trusted)
    parser = module.build_parser()
    args = parser.parse_args(
        [
            "--run-dir",
            str(tmp_path / "run"),
            "--dry-run",
            *trusted_executable_args(module, trusted),
        ]
    )
    assert args.run_dir == tmp_path / "run"
    assert not hasattr(args, "cases")
    assert not hasattr(args, "split")
    assert not hasattr(args, "max_attempts")
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--run-dir",
                str(tmp_path / "run"),
                "--cases",
                str(tmp_path / "other.json"),
                *trusted_executable_args(module, trusted),
            ]
        )
    assert len(module.load_dev_cases(module.DEV_CASES)) == 10


def test_run_evaluation_rejects_prompt_plan_drift_before_provider_call(tmp_path):
    module = load_module()
    cases_path = tmp_path / "parity_cases.dev.json"
    write_dev_cases(cases_path)
    cases = module.load_dev_cases(cases_path, expected_path=cases_path)
    protocol = module.load_protocol()
    assets = {
        "persona": "persona",
        "instruction": "instruction",
        "framework": "framework",
        "language": "language",
    }
    specs = {role: object() for role in ("reference", "candidate", "judge")}
    providers = {
        role: {
            "provider": role,
            "requested_model": role,
            "provider_spec_sha256": role[0] * 64,
        }
        for role in specs
    }
    called = False

    def forbidden_runner(*_args):
        nonlocal called
        called = True
        raise AssertionError("provider must not run")

    with pytest.raises(ValueError, match="planned generation prompt hashes"):
        module.run_evaluation(
            run_dir=tmp_path / "run",
            run_mode="iterative_dev",
            run_id="drift",
            fingerprint_inputs={
                "protocol": {"version": 1},
                "inputs": {"planned_generation_calls": []},
            },
            cases=cases,
            assets=assets,
            protocol=protocol,
            specs=specs,
            providers=providers,
            runner=forbidden_runner,
            judgment_prompt_fn=lambda *_args: ("", {}),
            parse_judgment_fn=parse_fake_judgment,
            normalize_winner_fn=lambda winner, mapping: mapping[winner],
            max_attempts=3,
        )
    assert called is False
