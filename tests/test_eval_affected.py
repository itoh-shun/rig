import copy
import datetime as dt
import json
import pathlib
import subprocess

import pytest

from test_eval_cases import valid_case


def _git(repo: pathlib.Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, check=True,
                               capture_output=True, text=True)
    return completed.stdout.strip()


def _repo(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "eval@test.invalid")
    _git(repo, "config", "user.name", "eval-test")
    marker = repo / "README.md"
    marker.write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    return repo, _git(repo, "rev-parse", "HEAD")


def _case(repo: pathlib.Path, binding: str) -> dict:
    from rig_workbench.eval.cases import canonical_json

    case = copy.deepcopy(valid_case())
    case["id"] = "affected-case"
    case["target_inputs"] = {"prompt_surface_fixture": "explicit binding fixture"}
    case["prompt_surfaces"] = [binding]
    path = repo / "evals" / "cases" / case["id"] / "case.json"
    path.parent.mkdir(parents=True)
    path.write_text(canonical_json(case), encoding="utf-8")
    return case


def test_affected_direct_indirect_wiki_unknown_and_nonprompt_noop(tmp_path):
    from rig_workbench.eval.affected import analyze_affected

    repo, base = _repo(tmp_path)
    recipe = repo / "skills" / "engine" / "recipes" / "auth.md"
    instruction = repo / "skills" / "engine" / "facets" / "instructions" / "login.md"
    persona = repo / "skills" / "engine" / "facets" / "personas" / "reviewer.md"
    wiki = repo / "skills" / "engine" / "facets" / "knowledge" / "wiki" / "auth.md"
    for path in (recipe, instruction, persona, wiki):
        path.parent.mkdir(parents=True, exist_ok=True)
    instruction.write_text("---\nname: login\n---\n", encoding="utf-8")
    wiki.write_text("---\nname: auth\n---\n", encoding="utf-8")
    persona.write_text("---\nname: reviewer\ninject: [\"[[auth]]\"]\n---\n", encoding="utf-8")
    recipe.write_text(
        "---\nname: auth\nsteps:\n  - id: review\n    instruction: login\n"
        "    personas: [reviewer]\n---\n", encoding="utf-8",
    )
    _case(repo, "recipe:auth")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "prompt graph")
    base = _git(repo, "rev-parse", "HEAD")

    wiki.write_text(wiki.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
    report = analyze_affected(repo, base=base, require_cases=True)
    assert report["affected_recipes"] == ["auth"]
    assert report["affected_cases"] == ["affected-case"]
    assert report["status"] == "pass"
    assert len(report["resolved_head"]) == 40

    _git(repo, "checkout", "--", wiki.relative_to(repo).as_posix())
    unknown = repo / "skills" / "engine" / "facets" / "new-surface" / "x.md"
    unknown.parent.mkdir(parents=True)
    unknown.write_text("unknown\n", encoding="utf-8")
    uncovered = analyze_affected(repo, base=base, require_cases=True)
    assert uncovered["status"] == "uncovered"
    assert len(uncovered["resolved_head"]) == 40
    assert unknown.relative_to(repo).as_posix() in uncovered["uncovered"]

    unknown.unlink()
    (repo / "ordinary.py").write_text("print('ok')\n", encoding="utf-8")
    noop = analyze_affected(repo, base=base, require_cases=True)
    assert noop["status"] == "noop" and noop["affected_cases"] == []
    assert len(noop["resolved_head"]) == 40


def test_pattern_and_output_contract_reverse_map_to_recipe_case(tmp_path):
    from rig_workbench.eval.affected import analyze_affected

    repo, _base = _repo(tmp_path)
    pattern = repo / "skills" / "engine" / "patterns" / "guard.md"
    contract = repo / "skills" / "engine" / "facets" / "output-contracts" / "verdict.md"
    recipe = repo / "skills" / "engine" / "recipes" / "guarded.md"
    for path in (pattern, contract, recipe):
        path.parent.mkdir(parents=True, exist_ok=True)
    pattern.write_text("---\nname: guard\n---\n", encoding="utf-8")
    contract.write_text("---\nname: verdict\n---\n", encoding="utf-8")
    recipe.write_text(
        "---\nname: guarded\nsteps:\n  - id: check\n    pattern: guard\n"
        "    output_contract: verdict\n---\n", encoding="utf-8",
    )
    _case(repo, "recipe:guarded")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "graph")
    base = _git(repo, "rev-parse", "HEAD")
    pattern.write_text(pattern.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
    report = analyze_affected(repo, base=base, require_cases=True)
    assert report["affected_recipes"] == ["guarded"]
    assert report["affected_cases"] == ["affected-case"]


def test_affected_is_deterministic_and_reports_absent_evidence(tmp_path):
    from rig_workbench.eval.affected import analyze_affected, prompt_surface_registry

    registry_path = pathlib.Path(__file__).resolve().parent.parent / "evals" / "prompt-surfaces.json"
    assert json.loads(registry_path.read_text(encoding="utf-8")) == prompt_surface_registry()

    repo, base = _repo(tmp_path)
    recipe = repo / "skills" / "engine" / "recipes" / "sample.md"
    recipe.parent.mkdir(parents=True)
    recipe.write_text("---\nname: sample\nsteps: []\n---\n", encoding="utf-8")
    _case(repo, "recipe:sample")
    first = analyze_affected(
        repo, base=base, require_cases=True, evidence_dir=repo / "evidence"
    )
    second = analyze_affected(
        repo, base=base, require_cases=True, evidence_dir=repo / "evidence"
    )
    assert first == second
    assert first["evidence_status"] == {"affected-case": "absent"}


def test_affected_draft_only_is_uncovered_and_mixed_surfaces_do_not_cross_cover(tmp_path):
    from rig_workbench.eval.affected import analyze_affected
    from rig_workbench.eval.cases import canonical_json

    repo, base = _repo(tmp_path)
    draft = copy.deepcopy(valid_case())
    draft["id"] = "draft-only"
    draft["status"] = "draft"
    draft["target_inputs"] = {"prompt_surface": "recipe:covered"}
    draft_path = repo / ".rig" / "evals" / "drafts" / draft["id"] / "case.json"
    draft_path.parent.mkdir(parents=True)
    draft_path.write_text(canonical_json(draft), encoding="utf-8")
    recipe = repo / "skills" / "engine" / "recipes" / "covered.md"
    recipe.parent.mkdir(parents=True)
    recipe.write_text("---\nname: covered\nsteps: []\n---\n", encoding="utf-8")
    report = analyze_affected(repo, base=base, require_cases=True)
    assert report["affected_cases"] == [] and report["status"] == "uncovered"

    approved = _case(repo, "recipe:covered")
    approved_path = repo / "evals" / "cases" / approved["id"] / "case.json"
    approved["target_inputs"] = {"misleading_text": "command:unrelated recipe:not-bound"}
    approved_path.write_text(canonical_json(approved), encoding="utf-8")
    command = repo / "commands" / "unrelated.md"
    command.parent.mkdir(parents=True)
    command.write_text("unrelated prompt\n", encoding="utf-8")
    mixed = analyze_affected(repo, base=base, require_cases=True)
    assert approved["id"] in mixed["affected_cases"]
    assert command.relative_to(repo).as_posix() in mixed["uncovered"]


def test_eval_gate_rejects_absent_and_mock_then_accepts_signed_real_provider(
    tmp_path, monkeypatch,
):
    from rig_workbench.eval.gate import evaluate_gate
    from rig_workbench.eval.runner import make_judge_adapter, run_case

    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", "gate-test-attestation-key-at-least-32-bytes")
    repo, base = _repo(tmp_path)
    (repo / "middle.txt").write_text("middle\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "middle base")
    base = _git(repo, "rev-parse", "HEAD")
    recipe = repo / "skills" / "engine" / "recipes" / "sample.md"
    recipe.parent.mkdir(parents=True)
    recipe.write_text("---\nname: sample\nsteps: []\n---\n", encoding="utf-8")
    case = _case(repo, "recipe:sample")
    case["provider_policy"] = {"mode": "allowlist", "allowed": ["command"]}
    case["deterministic_checks"] = ["contains:prompt_surface"]
    case["clean_controls"] = {"prompt_surface": "control"}
    from rig_workbench.eval.cases import canonical_json
    case_path = repo / "evals" / "cases" / case["id"] / "case.json"
    case_path.write_text(canonical_json(case), encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "prompt change")

    absent, absent_code = evaluate_gate(
        repo, base=base, head="HEAD", evidence_dir=repo / "missing"
    )
    assert absent_code == 1 and "evidence_absent" in " ".join(absent["failures"])

    now = dt.datetime.now(dt.timezone.utc)
    mock_case = copy.deepcopy(case)
    mock_case["provider_policy"] = {"mode": "any", "allowed": []}
    _path, mock = run_case(
        mock_case, repo=repo, provider="mock", model="fixture", repeat=3,
        phase="current", judge_adapter=make_judge_adapter(
            provider="mock", model="fixture", repo=repo
        ), now=now, execution_base=base,
    )
    mock_dir = tmp_path / "mock-evidence"
    mock_dir.mkdir()
    (mock_dir / "current.json").write_text(canonical_json(mock), encoding="utf-8")
    mocked, mocked_code = evaluate_gate(
        repo, base=base, head="HEAD", evidence_dir=mock_dir
    )
    assert mocked_code == 1 and "mock_evidence_forbidden" in " ".join(mocked["failures"])

    command = 'python3 -c "import os; print(os.environ[\'RIG_EVAL_INPUT\'])"'
    judge_command = (
        'python3 -c "import json; print(json.dumps({\'status\':\'measured\','
        '\'criteria\':[{\'id\':\'correct\',\'status\':\'pass\',\'score\':1.0}]}))"'
    )
    _path, real = run_case(
        case, repo=repo, provider="command", model="fixture", repeat=3,
        phase="current", command=command,
        judge_adapter=make_judge_adapter(
            provider="command", model="fixture", repo=repo, command=judge_command
        ),
        now=now, execution_base=base,
    )
    evidence = tmp_path / "real-evidence"
    evidence.mkdir()
    (evidence / "current.json").write_text(canonical_json(real), encoding="utf-8")
    passed, passed_code = evaluate_gate(
        repo, base=base, head="HEAD", evidence_dir=evidence,
        provider="command", model="fixture", judge_provider="command",
        judge_model="fixture",
    )
    assert passed_code == 0 and passed["status"] == "pass", passed
    assert real["execution_base_commit"] == base

    working, working_code = evaluate_gate(
        repo, base=base, head="working", evidence_dir=evidence,
        provider="command", model="fixture", judge_provider="command",
        judge_model="fixture",
    )
    assert working_code == 0 and working["status"] == "pass"
    recipe.write_text(recipe.read_text(encoding="utf-8") + "changed after signing\n",
                      encoding="utf-8")
    reused, reused_code = evaluate_gate(
        repo, base=base, head="working", evidence_dir=evidence,
        provider="command", model="fixture", judge_provider="command",
        judge_model="fixture",
    )
    assert reused_code == 1
    assert "execution_identity_mismatch" in " ".join(reused["failures"])


def test_affected_run_is_nonmock_and_atomic(tmp_path, monkeypatch):
    from rig_workbench.eval import EvalCaseError
    from rig_workbench.eval.affected_run import run_affected
    from rig_workbench.eval.cases import canonical_json

    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", "affected-run-key-at-least-thirty-two-bytes")
    repo, base = _repo(tmp_path)
    recipe = repo / "skills" / "engine" / "recipes" / "atomic.md"
    recipe.parent.mkdir(parents=True)
    recipe.write_text("---\nname: atomic\nsteps: []\n---\n", encoding="utf-8")
    case = _case(repo, "recipe:atomic")
    case["provider_policy"] = {
        "mode": "allowlist", "allowed": ["command"], "models": ["fixture"],
        "judge_providers": ["command"], "judge_models": ["fixture"],
    }
    case["deterministic_checks"] = ["contains:prompt_surface_fixture"]
    case["clean_controls"] = {"prompt_surface_fixture": "control"}
    case_path = repo / "evals" / "cases" / case["id"] / "case.json"
    case_path.write_text(canonical_json(case), encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "prompt")

    with pytest.raises(EvalCaseError, match="forbids mock"):
        run_affected(
            repo, base=base, head="HEAD", provider="mock", model="fixture",
            judge_provider="command", judge_model="fixture",
        )
    failed, code, destination = run_affected(
        repo, base=base, head="HEAD", provider="command", model="fixture",
        judge_provider="command", judge_model="fixture", provider_command="false",
        judge_command="false",
    )
    assert code in {1, 2} and destination is None
    assert not list((repo / ".rig" / "evals" / "results").glob(".affected-run.*"))

    command = 'python3 -c "import os; print(os.environ[\'RIG_EVAL_INPUT\'])"'
    judge_command = (
        'python3 -c "import json; print(json.dumps({\'status\':\'measured\','
        '\'criteria\':[{\'id\':\'correct\',\'status\':\'pass\',\'score\':1.0}]}))"'
    )
    report, code, destination = run_affected(
        repo, base=base, head="HEAD", provider="command", model="fixture",
        judge_provider="command", judge_model="fixture", provider_command=command,
        judge_command=judge_command,
    )
    assert code == 0 and report["status"] == "pass"
    assert destination is not None and destination.is_dir()
    first_destination = destination
    assert first_destination.name == f"affected-{_git(repo, 'rev-parse', 'HEAD')}"

    recipe.write_text(recipe.read_text(encoding="utf-8") + "next commit\n", encoding="utf-8")
    _git(repo, "add", recipe.relative_to(repo).as_posix())
    _git(repo, "commit", "-q", "-m", "second prompt head")
    second_head = _git(repo, "rev-parse", "HEAD")
    second, second_code, second_destination = run_affected(
        repo, base=base, head="HEAD", provider="command", model="fixture",
        judge_provider="command", judge_model="fixture", provider_command=command,
        judge_command=judge_command,
    )
    assert second_code == 0 and second["resolved_head"] == second_head
    assert second_destination is not None and second_destination != first_destination
    with pytest.raises(EvalCaseError, match="already exist.*commit"):
        run_affected(
            repo, base=base, head="HEAD", provider="command", model="fixture",
            judge_provider="command", judge_model="fixture", provider_command=command,
            judge_command=judge_command,
        )
