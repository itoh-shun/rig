"""A prompt-bearing pack could not be authored from scratch at all.

`validate_pack` refuses prompt material without an approved evaluation case, and that gate is
the point of the design: it makes an installed pack's quality a measurement rather than a
claim. The machinery to satisfy it exists and is thorough — capture, run, compare, promote,
with `promote_case` refusing evidence that does not pass its red/green/clean gates and a
rubric that was never judged.

It could not be pointed at a pack. `promote_case` read the draft from `<repo>/.rig/evals/
drafts/` and wrote the approved case to `<repo>/evals/cases/` — one `repo`, both paths. Aim
it at the pack and the draft becomes an undeclared file inside it, which `pack validate` and
`pack sync` each refuse, correctly. Aim it at the project and the case lands in the
repository's own evidence tree, not the pack's.

So `--into` moves the destination and nothing else. Every gate stays where it was; the safety
property is `compare_results` refusing evidence that does not pass, which has nothing to do
with the directory the result is written to.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import subprocess

import pytest

from rig_workbench.eval.cases import EvalCaseError
from rig_workbench.eval.promote import promote_case
from rig_workbench.packs.cli import init_pack
from rig_workbench.packs.sync import sync_manifest
from rig_workbench.packs.validation import validate_pack

CASE_ID = "demo-persona-case"

PERSONA = """---
name: hello
description: A demonstration reviewer persona.
---

# persona: hello

## facet: persona / hello

You review a scenario and say whether it is correct.
"""

#: Emitting a measured rubric from a subprocess is how the runner's `command` judge is driven
#: without reaching a provider. Same shape the wheel smoke test uses.
JUDGE_COMMAND = (
    'python3 -c "import json; print(json.dumps({\'status\':\'measured\','
    '\'criteria\':[{\'id\':\'correct\',\'status\':\'pass\',\'score\':1.0}]}))"'
)


def _draft(case_id: str = CASE_ID, *, check: str = "contains:scenario") -> dict:
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return {
        "case_schema_version": 1, "id": case_id, "version": 1,
        "title": "Demo persona case", "status": "draft", "incident": True,
        "provenance": {"source_task_id": "rig-demo", "source_commit": "a" * 40,
                       "source_hashes": {"task.json": "b" * 64}, "captured_at": stamp},
        "surfaces": ["cli"], "suite": "demo", "tags": ["demo"],
        # The binding `validate_pack` checks: a pack's case must name surfaces the pack owns.
        "prompt_surfaces": ["persona:hello"],
        "prompt_composition": ["persona:hello"],
        "prompt_entrypoint": "hello",
        "provider_policy": {"mode": "allowlist", "allowed": ["mock"]},
        "repeat": 3, "red_thresholds": {"max_success_rate": 1 / 3},
        "green_thresholds": {"min_success_rate": 1.0},
        "deterministic_checks": [check],
        "semantic_rubric": [
            {"id": "correct", "description": "Output is correct", "weight": 1.0}],
        "target_inputs": {"scenario": "target"},
        "clean_controls": {"scenario": "clean"},
        "missing_requirements": [],
        "failure_summary": "Demo", "created_at": stamp, "updated_at": stamp,
    }


def _write_draft(repo: pathlib.Path, case: dict) -> pathlib.Path:
    path = repo / ".rig" / "evals" / "drafts" / case["id"] / "case.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(case, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def authored(tmp_path, monkeypatch):
    """A project holding a reviewer pack with one persona, and a matching draft case.

    The pack does not validate in this state, and that is the starting condition the whole
    file is about — the assets are written, the manifest is correct, and the evidence gate is
    still shut.
    """
    monkeypatch.setenv(
        "RIG_EVAL_ATTESTATION_KEY",
        "f6bcb502ae6752cd1867ce9e1f6f20675653ef74e538495c83a1a33a43efd691")
    repo = tmp_path / "project"
    repo.mkdir()
    for command in (["git", "init", "-q"],
                    ["git", "config", "user.email", "a@b.invalid"],
                    ["git", "config", "user.name", "t"]):
        subprocess.run(command, cwd=repo, check=True)

    pack = init_pack("demo-pack", kind="project", type_="reviewer",
                     root=repo / ".rig" / "packs")
    (pack / "facets/personas/hello.md").write_text(PERSONA, encoding="utf-8")
    sync_manifest(pack)

    draft = _write_draft(repo, _draft())
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    return repo, pack, draft


def _measure(repo: pathlib.Path, phase: str, case_id: str = CASE_ID) -> dict:
    """Run the case for one phase and read the result it wrote."""
    from rig_workbench.eval.cli import cmd_eval

    import contextlib
    import io

    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        code = cmd_eval(["run", case_id, "--provider", "mock", "--model", "fixture",
                     "--repeat", "3", "--phase", phase, "--repo", str(repo),
                     "--judge-provider", "command", "--judge-model", "fixture",
                     "--judge-command", JUDGE_COMMAND])
    assert code == 0, captured.getvalue()
    path = pathlib.Path(captured.getvalue().strip().splitlines()[-1])
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_prompt_bearing_pack_can_be_authored_end_to_end(authored):
    """The acceptance test, and the thing that was impossible. Scaffold, write a persona,
    sync, measure, promote into the pack, sync again — and the pack validates, backed by
    evidence that actually passed rather than by an assertion that it would."""
    repo, pack, _draft_path = authored
    with pytest.raises(Exception, match="requires at least one evaluation case"):
        validate_pack(pack)

    baseline, current = _measure(repo, "baseline"), _measure(repo, "current")
    destination, promoted = promote_case(repo, CASE_ID, baseline, current, into=pack)
    sync_manifest(pack)

    assert destination == pack / "evals" / "cases" / CASE_ID / "case.json"
    assert promoted["status"] == "approved"
    assert validate_pack(pack)["assets"]["eval-case"] == [f"evals/cases/{CASE_ID}/case.json"]


def test_the_draft_stays_in_the_project_where_it_is_nobody_s_undeclared_file(authored):
    """`--into` moves the destination, not the source. A pack may hold nothing it has not
    declared, so a draft staged inside one is refused by `pack validate` and `pack sync`
    alike — which is exactly why the draft cannot simply live next to the case it becomes."""
    repo, pack, draft_path = authored

    promote_case(repo, CASE_ID, _measure(repo, "baseline"), _measure(repo, "current"),
                 into=pack)

    assert draft_path.is_file()
    assert not (pack / ".rig").exists()


def test_evidence_that_does_not_meet_the_thresholds_is_refused_into_a_pack(authored):
    """The property that makes this a destination argument rather than a hole: promotion
    refuses evidence that does not pass, and refuses it into a pack exactly as into a
    repository. The safety lives in `compare_results`, which never learns where the result
    would have been written.

    The evidence here is genuinely measured and genuinely short — a deterministic check that
    the mock provider's output cannot satisfy, so the green threshold is missed honestly
    rather than by editing a result."""
    repo, pack, _draft_path = authored
    case_id = "demo-unreachable-case"
    _write_draft(repo, _draft(case_id, check="contains:this-string-never-appears"))

    baseline = _measure(repo, "baseline", case_id)
    current = _measure(repo, "current", case_id)

    with pytest.raises(EvalCaseError, match="red/green/clean"):
        promote_case(repo, case_id, baseline, current, into=pack)
    assert not (pack / "evals" / "cases" / case_id).exists()


def test_tampered_evidence_is_refused_before_the_thresholds_are_consulted(authored):
    """Results are attested, so editing one to make it look passing fails at the signature
    rather than at the comparison. Worth pinning separately: it is a stronger refusal than
    the threshold check and it sits in front of it, so a test that tampers with a result is
    not testing the thresholds at all — which is what an earlier version of the test above
    was accidentally doing."""
    repo, pack, _draft_path = authored
    baseline = _measure(repo, "baseline")
    tampered = _measure(repo, "current")
    tampered["target"] = []

    with pytest.raises(EvalCaseError, match="attestation"):
        promote_case(repo, CASE_ID, baseline, tampered, into=pack)
    assert not (pack / "evals" / "cases" / CASE_ID).exists()


def test_a_destination_that_is_not_a_pack_is_refused(authored, tmp_path):
    """A mistyped path would otherwise write an approved case into an ordinary directory where
    nothing reads it, and the author's next `pack validate` would report the case as missing
    rather than misplaced — a confident wrong answer to the question they are asking."""
    repo, _pack, _draft_path = authored
    not_a_pack = tmp_path / "somewhere"
    not_a_pack.mkdir()

    with pytest.raises(EvalCaseError, match="not a pack directory"):
        promote_case(repo, CASE_ID, _measure(repo, "baseline"), _measure(repo, "current"),
                     into=not_a_pack)


def test_without_into_the_case_still_lands_in_the_repository(authored):
    """The existing behaviour, unchanged. `--into` is an addition; a caller that does not pass
    it must see exactly what it saw before."""
    repo, _pack, _draft_path = authored

    destination, _promoted = promote_case(
        repo, CASE_ID, _measure(repo, "baseline"), _measure(repo, "current"))

    assert destination == repo / "evals" / "cases" / CASE_ID / "case.json"
