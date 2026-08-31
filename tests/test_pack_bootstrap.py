"""A new prompt-bearing pack could not be measured with its own prompt.

Validating a prompt-bearing pack requires an approved evaluation case. Approving a case
requires evidence that passes. Evidence bound to *this pack's* prompt comes only from
`pack test`, the one path that calls `compose_case_prompt` and passes it as `prompt_prefix` —
and `pack test` validates first. So the pack could not be measured before it was approved, or
approved without being measured.

The other path does not close the loop: `rig-wb eval run` never passes `prompt_prefix`, so
its evidence records `prompt_binding_sha256 = sha256("")` — a measurement of the bare model on
the case input, which says nothing about the pack. `validate_pack` does not check the binding,
so approving on it produces a pack that validates on evidence which never ran its prompt.

`pack test --draft` breaks the circle at its narrowest point, and these tests pin how narrow
it is.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from rig_workbench.packs.cli import init_pack
from rig_workbench.packs.model import PackError
from rig_workbench.packs.sync import sync_manifest
from rig_workbench.packs.validation import validate_pack

PERSONA = ("---\nname: demo-reviewer\ndescription: A demonstration reviewer.\n---\n\n"
           "# persona: demo-reviewer\n\nYou review a scenario and say whether it is correct.\n")


def _write_draft(project: pathlib.Path, case_id: str, *, status: str = "draft",
                 surfaces: list[str]) -> pathlib.Path:
    path = project / ".rig" / "evals" / "drafts" / case_id / "case.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "case_schema_version": 1, "id": case_id, "version": 1, "title": "t",
        "status": status, "incident": False, "surfaces": ["cli"], "suite": "s", "tags": [],
        "prompt_surfaces": surfaces, "prompt_composition": surfaces,
        "prompt_entrypoint": "x",
        "provider_policy": {"allowed": [], "mode": "any"}, "repeat": 3,
        "red_thresholds": {"max_success_rate": 0.0},
        "green_thresholds": {"min_success_rate": 1.0},
        "deterministic_checks": ["contains:x"],
        "semantic_rubric": [{"id": "r", "description": "d", "weight": 1.0}],
        "target_inputs": {"a": "b"}, "clean_controls": {"a": "c"},
        "missing_requirements": [], "failure_summary": "f",
        "provenance": {"source_task_id": "t", "source_commit": "a" * 40,
                       "source_hashes": {"task.json": "b" * 64},
                       "captured_at": "2026-08-29T00:00:00+00:00"},
        "created_at": "2026-08-29T00:00:00+00:00",
        "updated_at": "2026-08-29T00:00:00+00:00",
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return path


def _prompt_pack(tmp_path: pathlib.Path) -> pathlib.Path:
    """A structurally sound pack that carries prompt material and no approved case."""
    pack = init_pack("demo-pack", kind="project", type_="reviewer", root=tmp_path / "packs")
    (pack / "facets/personas/demo-reviewer.md").write_text(PERSONA, encoding="utf-8")
    sync_manifest(pack)
    return pack


def test_a_prompt_bearing_pack_without_a_case_is_still_refused_by_default(tmp_path):
    """The rule is not weakened. Every caller that reaches a user — install, publish, invoke,
    plain `validate` — keeps requiring the approved case."""
    with pytest.raises(PackError, match="requires at least one evaluation case"):
        validate_pack(_prompt_pack(tmp_path))


def test_the_bootstrap_drops_that_rule_and_keeps_every_other(tmp_path):
    """`require_evaluation=False` exists for one caller and clears one thing. A pack that is
    broken for any other reason must still be refused, or the bootstrap becomes a way to
    measure something that could never ship."""
    pack = _prompt_pack(tmp_path)

    assert validate_pack(pack, require_evaluation=False)["id"] == "demo-pack"

    (pack / "facets/personas/undeclared.md").write_text(PERSONA, encoding="utf-8")
    with pytest.raises(PackError, match="drift"):
        validate_pack(pack, require_evaluation=False)


def test_entrypoint_coverage_is_relaxed_by_the_same_flag_and_only_it(tmp_path):
    """With no approved case there are no evaluation surfaces, so every entrypoint fails
    coverage — the same missing case in different words. A bootstrap that cleared only the
    first rule still could not compose a prompt, which is the entire point of the run."""
    pack = init_pack("entry-pack", kind="project", type_="skill", root=tmp_path / "packs")
    (pack / "facets/personas/demo-reviewer.md").write_text(PERSONA, encoding="utf-8")
    (pack / "facets/instructions/x.md").write_text(
        "---\nname: x\ndescription: A demonstration instruction.\n---\n\n"
        "# instruction: x\n\nDo the demonstration thing.\n", encoding="utf-8")
    (pack / "recipes/demo.md").write_text(
        "---\nname: demo\ndescription: demo\nsteps:\n  - id: one\n    instruction: x\n---\n",
        encoding="utf-8")
    sync_manifest(pack)
    manifest = json.loads((pack / "pack.yaml").read_text(encoding="utf-8"))
    manifest["entrypoints"] = [{"id": "demo", "kind": "recipe", "target": "demo"}]
    manifest["references"] = [{"kind": "instruction", "id": "x", "pack": "entry-pack"}]
    from rig_workbench.packs.manifest import canonical
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")

    with pytest.raises(PackError, match="evaluation case|evaluation coverage"):
        validate_pack(pack)
    assert validate_pack(pack, require_evaluation=False)["id"] == "entry-pack"


def test_a_draft_must_be_bound_to_surfaces_the_pack_owns(tmp_path):
    """Without this the bootstrap would measure another pack's prompt and write evidence that
    looks like it belongs here — worse than the circle it exists to break."""
    from rig_workbench.packs.tester import _cases_to_run

    pack = _prompt_pack(tmp_path)
    manifest = validate_pack(pack, require_evaluation=False)
    project = tmp_path / "project"
    _write_draft(project, "foreign", surfaces=["persona:somebody-elses-reviewer"])

    with pytest.raises(PackError, match="not bound to surfaces this pack owns"):
        _cases_to_run(pack, manifest, [], project, "foreign")


def test_an_approved_case_is_not_a_draft(tmp_path):
    """`--draft` names the thing that has not been approved yet. Accepting an approved case
    here would let the bootstrap re-measure something the pack already ships, under a flag
    whose whole meaning is 'not yet measured'."""
    from rig_workbench.packs.tester import _cases_to_run

    pack = _prompt_pack(tmp_path)
    manifest = validate_pack(pack, require_evaluation=False)
    project = tmp_path / "project"
    _write_draft(project, "already", status="approved",
                 surfaces=["persona:demo-reviewer"])

    with pytest.raises(PackError, match="takes a draft"):
        _cases_to_run(pack, manifest, [], project, "already")


def test_a_real_provider_draft_run_summarizes_the_case_it_ran(tmp_path, monkeypatch):
    """A draft has no approved-case path, but the real-provider summary still uses it."""
    from rig_workbench.packs.manifest import canonical, read_json_yaml
    from rig_workbench.packs.tester import test_pack

    pack = _prompt_pack(tmp_path)
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    manifest["entrypoints"] = [
        {"id": "x", "kind": "persona", "target": "demo-reviewer"},
    ]
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    project = tmp_path / "project"
    case_id = "first-evidence"
    _write_draft(project, case_id, surfaces=["persona:demo-reviewer"])
    calls = []

    def execute(**kwargs):
        calls.append((kwargs["kind"], kwargs["index"]))
        return 0, "x", "", None

    monkeypatch.setattr("rig_workbench.eval.runner._execute", execute)
    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", "c" * 64)

    summary, code = test_pack(
        pack, project=project, provider="codex", model="fixture",
        result_dir=tmp_path / "results", allow_paid_provider=True, draft=case_id,
    )

    assert calls == [(kind, index) for kind in ("target", "clean") for index in range(1, 4)]
    assert code == 1 and summary["status"] == "quality_failed"
    assert summary["cases"] == [case_id]
    assert len(summary["result_paths"]) == 1
    assert pathlib.Path(summary["result_paths"][0]).is_file()


def test_evidence_from_eval_run_is_bound_to_nothing():
    """The fact that makes the bootstrap necessary rather than convenient, pinned so it cannot
    be forgotten: `eval run` composes no prompt, so its binding is the digest of the empty
    string. Evidence like that measures the model, not the pack."""
    from rig_workbench.eval import cli as eval_cli
    import inspect

    source = inspect.getsource(eval_cli)
    assert "prompt_prefix" not in source, (
        "eval run now composes a prompt; the bootstrap's rationale needs revisiting")
    assert hashlib.sha256(b"").hexdigest() == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
