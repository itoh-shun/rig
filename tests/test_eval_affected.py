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


# ── divergence: what the branch changed, not what the base branch did (#367) ──


def _diverged(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str]:
    """A branch that forked, then watched the base branch move on without it."""
    repo, _fork = _repo(tmp_path)
    (repo / "commands").mkdir()
    (repo / "skills" / "engine" / "recipes").mkdir(parents=True)
    (repo / "commands" / "alpha.md").write_text("a\n", encoding="utf-8")
    (repo / "skills" / "engine" / "recipes" / "bugfix.md").write_text("r\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "surfaces")
    trunk = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")

    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "commands" / "alpha.md").write_text("a\nbranch\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "branch touches alpha")

    _git(repo, "checkout", "-q", trunk)
    for index in range(3):
        (repo / "skills" / "engine" / "recipes" / "bugfix.md").write_text(
            f"r\ntrunk {index}\n", encoding="utf-8"
        )
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "-m", f"trunk moves {index}")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_a_surface_the_base_branch_changed_is_not_charged_to_the_branch(tmp_path):
    """Diffing against the base tip counts the base branch's own work as the
    branch's, which is what makes a long-diverged PR unpassable."""
    from rig_workbench.eval.affected import analyze_affected

    repo, base_tip = _diverged(tmp_path)
    result = analyze_affected(repo, base=base_tip, head="feature", require_cases=True)

    assert result["changed_files"] == ["commands/alpha.md"]
    assert result["uncovered"] == ["commands/alpha.md"]
    assert "skills/engine/recipes/bugfix.md" not in result["changed_files"]


def test_the_fork_point_used_for_the_comparison_is_reported(tmp_path):
    from rig_workbench.eval.affected import analyze_affected

    repo, base_tip = _diverged(tmp_path)
    result = analyze_affected(repo, base=base_tip, head="feature")
    assert result["merge_base"] != base_tip
    assert result["merge_base"] == _git(repo, "merge-base", base_tip, "feature")


def test_each_blocking_path_names_the_commit_that_changed_it(tmp_path):
    """A wall of paths is unactionable; the commit behind each one is a triage
    list."""
    from rig_workbench.eval.affected import analyze_affected

    repo, base_tip = _diverged(tmp_path)
    result = analyze_affected(repo, base=base_tip, head="feature", require_cases=True)
    commits = result["surface_commits"]
    assert list(commits) == ["commands/alpha.md"]
    assert commits["commands/alpha.md"] == [_git(repo, "log", "--format=%h", "-1", "feature")]


def test_a_covered_run_reports_no_commit_attribution(tmp_path):
    from rig_workbench.eval.affected import analyze_affected

    repo, base_tip = _diverged(tmp_path)
    result = analyze_affected(repo, base=base_tip, head="feature")
    assert result["surface_commits"] == {}


def test_an_up_to_date_branch_behaves_exactly_as_before(tmp_path):
    """With no divergence the fork point is the base, so nothing changes."""
    from rig_workbench.eval.affected import analyze_affected

    repo, _base = _repo(tmp_path)
    (repo / "commands").mkdir()
    (repo / "commands" / "alpha.md").write_text("a\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "surfaces")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "commands" / "alpha.md").write_text("a\nb\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "touch")

    result = analyze_affected(repo, base=base, head="feature", require_cases=True)
    assert result["merge_base"] == base
    assert result["changed_files"] == ["commands/alpha.md"]


def test_unrelated_histories_fall_back_to_the_base_instead_of_erroring(tmp_path):
    from rig_workbench.eval.affected import analyze_affected

    repo, base = _repo(tmp_path)
    _git(repo, "checkout", "-q", "--orphan", "detached")
    (repo / "commands").mkdir()
    (repo / "commands" / "alpha.md").write_text("a\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "unrelated")

    result = analyze_affected(repo, base=base, head="detached")
    assert result["merge_base"] == base
    assert "commands/alpha.md" in result["changed_files"]


# ── the engine's own prose (registry version 2) ──────────────────────────────
# Every root the registry knew about was a *subdirectory* of `skills/engine/`, so
# the two documents that govern all of them — SKILL.md, which decides
# PARSE/RESOLVE/COMPOSE/RUN for every run, and PACKS.md, which SKILL.md itself
# sends the reader to — were the only prompt surfaces in the repository the gate
# could not see. Touching one line of a persona registered as affected; rewriting
# SKILL.md §6 reported `noop`. That is the ratchet's own defect pointing the other
# way: #383/#384 fixed a check that fired on everything and distinguished nothing,
# and this fixes a check that did not fire on the file that matters most.


def _engine_repo(tmp_path: pathlib.Path):
    repo, _ = _repo(tmp_path)
    engine = repo / "skills" / "engine"
    (engine / "recipes").mkdir(parents=True)
    (engine / "corpora" / "fixture").mkdir(parents=True)
    (engine / "SKILL.md").write_text("engine prose\n", encoding="utf-8")
    (engine / "PACKS.md").write_text("pack prose\n", encoding="utf-8")
    (engine / "recipes" / "auth.md").write_text(
        "---\nname: auth\nsteps: []\n---\n", encoding="utf-8")
    (engine / "corpora" / "fixture" / "seed.md").write_text("fixture\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "engine")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_the_engine_document_is_an_affected_surface(tmp_path):
    """The hole itself: rewriting SKILL.md used to report `noop`."""
    from rig_workbench.eval.affected import analyze_affected

    repo, base = _engine_repo(tmp_path)
    (repo / "skills" / "engine" / "SKILL.md").write_text("rewritten\n", encoding="utf-8")

    result = analyze_affected(repo, base=base, ratchet=True)
    assert [s["id"] for s in result["affected_surfaces"]] == ["engine:SKILL"]
    assert result["status"] == "debt"
    assert result["coverage_debt"] == ["skills/engine/SKILL.md"]


def test_the_pack_document_the_engine_points_at_counts_too(tmp_path):
    from rig_workbench.eval.affected import analyze_affected

    repo, base = _engine_repo(tmp_path)
    (repo / "skills" / "engine" / "PACKS.md").write_text("rewritten\n", encoding="utf-8")
    assert [s["id"] for s in analyze_affected(repo, base=base)["affected_surfaces"]] \
        == ["engine:PACKS"]


def test_a_registered_subdirectory_still_wins_over_the_engine_root(tmp_path):
    """Order matters: a recipe must stay `recipe:auth`, not become
    `engine:recipes/auth`, or every case binding in the repository breaks."""
    from rig_workbench.eval.affected import analyze_affected

    repo, base = _engine_repo(tmp_path)
    (repo / "skills" / "engine" / "recipes" / "auth.md").write_text(
        "---\nname: auth\nsteps: []\nx: 1\n---\n", encoding="utf-8")
    assert [s["id"] for s in analyze_affected(repo, base=base)["affected_surfaces"]] \
        == ["recipe:auth"]


def test_engine_subdirectories_that_are_not_prose_stay_out(tmp_path):
    """`corpora/` is drill fixture data — evidence the gate consumes, not prose the
    model reads. A recursive root would have swept it in and demanded cases for it."""
    from rig_workbench.eval.affected import analyze_affected

    repo, base = _engine_repo(tmp_path)
    (repo / "skills" / "engine" / "corpora" / "fixture" / "seed.md").write_text(
        "changed\n", encoding="utf-8")
    result = analyze_affected(repo, base=base, ratchet=True)
    assert result["affected_surfaces"] == []
    assert result["status"] == "noop"


def test_the_engine_document_is_debt_under_ratchet_and_fatal_under_the_old_form(tmp_path):
    """Why this could not have shipped before #384. Under `--require-cases` the very
    first change to SKILL.md would fail the job with no way to pass it; as debt it is
    counted, named, and exit 0."""
    from rig_workbench.eval.affected import analyze_affected

    repo, base = _engine_repo(tmp_path)
    (repo / "skills" / "engine" / "SKILL.md").write_text("rewritten\n", encoding="utf-8")

    assert analyze_affected(repo, base=base, ratchet=True)["status"] == "debt"
    assert analyze_affected(repo, base=base, require_cases=True)["status"] == "uncovered"


def test_a_case_bound_to_the_engine_document_pays_the_debt_down(tmp_path):
    """Debt has to be payable, or it is just a permanent warning nobody can clear."""
    from rig_workbench.eval.affected import analyze_affected

    repo, base = _engine_repo(tmp_path)
    _case(repo, "engine:SKILL")
    (repo / "skills" / "engine" / "SKILL.md").write_text("rewritten\n", encoding="utf-8")

    result = analyze_affected(repo, base=base, ratchet=True)
    assert result["status"] == "pass"
    assert result["coverage_debt"] == []
    assert result["affected_cases"] == ["affected-case"]


# ── the registry is monotonic too ────────────────────────────────────────────
# Editing `evals/prompt-surfaces.json` used to be fatal outright, on the reasoning
# that changing what the gate can see is not a coverage question. True, and the
# consequence was that the registry could never be extended without failing the
# job — #383's shape exactly, aimed at the one change class that *widens* the
# gate's coverage. So the registry gets the rule the rest of the module uses.


def _registry_repo(tmp_path: pathlib.Path):
    from rig_workbench.eval.affected import REGISTRY_REL, prompt_surface_registry
    from rig_workbench.eval.cases import canonical_json

    repo, _ = _repo(tmp_path)
    path = repo / REGISTRY_REL
    path.parent.mkdir(parents=True)
    path.write_text(canonical_json(prompt_surface_registry()), encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "registry")
    return repo, _git(repo, "rev-parse", "HEAD"), path


def test_widening_the_registry_is_allowed_and_reported(tmp_path):
    """The change that closes a blind spot must be mergeable. It was not."""
    from rig_workbench.eval.affected import analyze_affected, prompt_surface_registry
    from rig_workbench.eval.cases import canonical_json

    repo, base, path = _registry_repo(tmp_path)
    narrower = copy.deepcopy(prompt_surface_registry())
    narrower["roots"] = narrower["roots"][:3]
    path.write_text(canonical_json(narrower), encoding="utf-8")   # base is the narrow one
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "narrow base")
    base = _git(repo, "rev-parse", "HEAD")
    path.write_text(canonical_json(prompt_surface_registry()), encoding="utf-8")

    result = analyze_affected(repo, base=base, ratchet=True)
    assert result["registry_changed"] is True
    assert result["registry_narrowings"] == []
    assert result["status"] == "noop"


@pytest.mark.parametrize("mode", [{"ratchet": True}, {"require_cases": True}])
def test_removing_a_root_is_a_narrowing_and_stays_fatal(tmp_path, mode, monkeypatch):
    """Coverage going *down* is the direction that must never pass — the same rule
    as `coverage_regressions`, applied to the field of view rather than the cases."""
    from rig_workbench.eval import affected as affected_module
    from rig_workbench.eval.affected import analyze_affected
    from rig_workbench.eval.cases import canonical_json

    repo, base, path = _registry_repo(tmp_path)
    monkeypatch.setattr(affected_module, "_SURFACE_FLAT_ROOTS", ())
    path.write_text(canonical_json(affected_module.prompt_surface_registry()),
                    encoding="utf-8")

    result = analyze_affected(repo, base=base, **mode)
    assert result["status"] == "uncovered"
    assert any("root removed: skills/engine/" in line
               for line in result["registry_narrowings"])


def test_renaming_a_kind_counts_as_a_narrowing(tmp_path, monkeypatch):
    """It orphans every case bound to the old ids without deleting a single case,
    so `coverage_regressions` cannot see it."""
    from rig_workbench.eval import affected as affected_module
    from rig_workbench.eval.affected import analyze_affected
    from rig_workbench.eval.cases import canonical_json

    repo, base, path = _registry_repo(tmp_path)
    monkeypatch.setattr(affected_module, "_SURFACE_PREFIXES",
                        (("skills/engine/recipes/", "workflow"),))
    path.write_text(canonical_json(affected_module.prompt_surface_registry()),
                    encoding="utf-8")

    result = analyze_affected(repo, base=base, ratchet=True)
    assert result["status"] == "uncovered"
    assert any("kind renamed" in line for line in result["registry_narrowings"])


def test_a_registry_that_cannot_be_read_at_the_base_accuses_nobody(tmp_path):
    """Same stance as `coverage_regressions`: a comparison that cannot be made is
    not evidence of a regression."""
    from rig_workbench.eval.affected import REGISTRY_REL, prompt_surface_registry
    from rig_workbench.eval.affected import analyze_affected
    from rig_workbench.eval.cases import canonical_json

    repo, base = _repo(tmp_path)                 # no registry at the base at all
    path = repo / REGISTRY_REL
    path.parent.mkdir(parents=True)
    path.write_text(canonical_json(prompt_surface_registry()), encoding="utf-8")

    result = analyze_affected(repo, base=base, ratchet=True)
    assert result["registry_changed"] is True
    assert result["registry_narrowings"] == []
    assert result["status"] == "noop"
