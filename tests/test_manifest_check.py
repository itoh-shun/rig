"""validate.py's manifest value-key checks over `.claude/rig.md` (#341).

Manifest keys are silently swallowed at RESOLVE/COMPOSE time when malformed,
so check_manifest() catches the mechanically-determinable subset (type/enum/
ordering) before a run. See facets/instructions/validate.md §2.
"""

import pathlib

import pytest

from rig_workbench.validation import state as validation_state
from rig_workbench.validation.manifest import check_manifest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _reset_validation_state():
    validation_state.results.clear()
    validation_state._pass = validation_state._warn = validation_state._fail = 0
    yield
    validation_state.results.clear()
    validation_state._pass = validation_state._warn = validation_state._fail = 0


def _write_manifest(tmp_path: pathlib.Path, frontmatter: str) -> pathlib.Path:
    path = tmp_path / "rig.md"
    path.write_text(f"---\n{frontmatter}---\n\n# manifest\n", encoding="utf-8")
    return path


def test_missing_manifest_is_silently_skipped(tmp_path):
    check_manifest(tmp_path / "does-not-exist.md")
    assert validation_state.results == []


def test_manifest_without_checkable_keys_is_silently_skipped(tmp_path):
    # `default_recipe` used to be the example of an unchecked key; #372 made it
    # one of the checked ones, so this needs a key that genuinely is not.
    manifest = _write_manifest(tmp_path, 'org_dir: "/srv/shared"\n')
    check_manifest(manifest)
    assert validation_state.results == []


def test_malformed_yaml_frontmatter_fails(tmp_path):
    manifest = tmp_path / "rig.md"
    manifest.write_text("---\ndefault_backend: [unterminated\n---\n", encoding="utf-8")
    check_manifest(manifest)
    assert validation_state._fail == 1


@pytest.mark.parametrize("value", ["manual", "workflow"])
def test_default_backend_valid_values_pass(tmp_path, value):
    manifest = _write_manifest(tmp_path, f"default_backend: {value}\n")
    check_manifest(manifest)
    assert validation_state._fail == 0
    assert validation_state._pass == 1


def test_default_backend_typo_fails(tmp_path):
    manifest = _write_manifest(tmp_path, "default_backend: manul\n")
    check_manifest(manifest)
    assert validation_state._fail == 1
    assert "default_backend" in validation_state.results[0]
    assert "manul" in validation_state.results[0]


@pytest.mark.parametrize("value", ["low", "mid"])
def test_default_budget_valid_values_pass(tmp_path, value):
    manifest = _write_manifest(tmp_path, f"default_budget: {value}\n")
    check_manifest(manifest)
    assert validation_state._fail == 0


def test_default_budget_invalid_value_fails(tmp_path):
    manifest = _write_manifest(tmp_path, "default_budget: lo\n")
    check_manifest(manifest)
    assert validation_state._fail == 1


@pytest.mark.parametrize("value", ["true", "false"])
def test_default_orchestrate_boolean_passes(tmp_path, value):
    manifest = _write_manifest(tmp_path, f"default_orchestrate: {value}\n")
    check_manifest(manifest)
    assert validation_state._fail == 0


def test_default_orchestrate_string_type_fails(tmp_path):
    manifest = _write_manifest(tmp_path, 'default_orchestrate: "yes"\n')
    check_manifest(manifest)
    assert validation_state._fail == 1


def test_default_orchestrate_integer_type_fails(tmp_path):
    manifest = _write_manifest(tmp_path, "default_orchestrate: 1\n")
    check_manifest(manifest)
    assert validation_state._fail == 1


def test_worktree_enabled_boolean_passes(tmp_path):
    manifest = _write_manifest(tmp_path, "worktree:\n  enabled: true\n")
    check_manifest(manifest)
    assert validation_state._fail == 0


def test_worktree_enabled_string_type_fails(tmp_path):
    manifest = _write_manifest(tmp_path, 'worktree:\n  enabled: "yes"\n')
    check_manifest(manifest)
    assert validation_state._fail == 1


def test_worktree_without_enabled_key_is_skipped(tmp_path):
    manifest = _write_manifest(tmp_path, "worktree:\n  base: main\n")
    check_manifest(manifest)
    assert validation_state.results == []


def test_size_thresholds_partial_override_uses_defaults_for_ordering(tmp_path):
    # S_max=50 < M_max(default 200) < L_max(default 400): should pass.
    manifest = _write_manifest(tmp_path, "size_thresholds:\n  S_max: 50\n")
    check_manifest(manifest)
    assert validation_state._fail == 0


def test_size_thresholds_violates_ordering_against_default_fails(tmp_path):
    # S_max=300 >= M_max(default 200): violates S_max < M_max.
    manifest = _write_manifest(tmp_path, "size_thresholds:\n  S_max: 300\n")
    check_manifest(manifest)
    assert validation_state._fail == 1
    assert "既定" in validation_state.results[0]


def test_size_thresholds_non_integer_fails(tmp_path):
    manifest = _write_manifest(tmp_path, "size_thresholds:\n  S_max: not-a-number\n")
    check_manifest(manifest)
    assert validation_state._fail == 1


def test_size_thresholds_non_positive_fails(tmp_path):
    manifest = _write_manifest(tmp_path, "size_thresholds:\n  S_max: 0\n")
    check_manifest(manifest)
    assert validation_state._fail == 1


def test_size_thresholds_boolean_is_rejected_despite_being_an_int_subclass(tmp_path):
    manifest = _write_manifest(tmp_path, "size_thresholds:\n  S_max: true\n")
    check_manifest(manifest)
    assert validation_state._fail == 1


def test_size_thresholds_fully_specified_ascending_passes(tmp_path):
    manifest = _write_manifest(
        tmp_path, "size_thresholds:\n  S_max: 80\n  M_max: 250\n  L_max: 500\n"
    )
    check_manifest(manifest)
    assert validation_state._fail == 0


def test_multiple_violations_in_one_manifest_are_all_reported(tmp_path):
    manifest = _write_manifest(
        tmp_path, "default_backend: manul\ndefault_budget: lo\n"
    )
    check_manifest(manifest)
    assert validation_state._fail == 2


def test_default_manifest_path_is_dotclaude_rig_md_under_root(monkeypatch, tmp_path):
    from rig_workbench.validation import manifest as manifest_module

    monkeypatch.setattr(manifest_module, "ROOT", tmp_path)
    (tmp_path / ".claude").mkdir()
    _write_manifest(tmp_path / ".claude", "default_backend: manul\n")
    check_manifest()
    assert validation_state._fail == 1


# ── default_max_retries (#360) ──────────────────────────────────────────


@pytest.mark.parametrize("value", ["1", "3", "10"])
def test_default_max_retries_valid_integers_pass(tmp_path, value):
    manifest = _write_manifest(tmp_path, f"default_max_retries: {value}\n")
    check_manifest(manifest)
    assert validation_state._fail == 0
    assert validation_state._pass == 1


@pytest.mark.parametrize("value", ["0", "-1", '"3"', "2.5", "true"])
def test_default_max_retries_rejects_anything_that_is_not_a_positive_int(tmp_path, value):
    """`true` matters on its own: bool is an int subclass, so it would pass a
    naive isinstance check and silently mean 1 retry."""
    manifest = _write_manifest(tmp_path, f"default_max_retries: {value}\n")
    check_manifest(manifest)
    assert validation_state._fail == 1
    assert "default_max_retries" in validation_state.results[0]


def test_default_max_retries_omitted_is_not_checked(tmp_path):
    manifest = _write_manifest(tmp_path, "default_backend: manual\n")
    check_manifest(manifest)
    assert validation_state._pass == 1
    assert "1 value key(s)" in validation_state.results[0]


# ── default_recipe / default_personas[] tier resolution (#372) ──────────


def test_unresolvable_default_recipe_fails(tmp_path):
    manifest = _write_manifest(tmp_path, "default_recipe: bugifx\n")
    check_manifest(manifest)
    assert validation_state._fail == 1
    assert "bugifx" in validation_state.results[0]
    assert "interactive" in validation_state.results[0]


def test_resolvable_default_recipe_passes(tmp_path):
    manifest = _write_manifest(tmp_path, "default_recipe: bugfix\n")
    check_manifest(manifest)
    assert validation_state._fail == 0
    assert validation_state._pass == 1


@pytest.mark.parametrize("value", ["interactive", '""'])
def test_reserved_and_empty_default_recipe_are_not_resolved(tmp_path, value):
    """`interactive` is the reserved word for "ask me", not a recipe name."""
    manifest = _write_manifest(tmp_path, f"default_recipe: {value}\n")
    check_manifest(manifest)
    assert validation_state.results == []


def test_unresolvable_default_persona_fails_naming_the_element(tmp_path):
    manifest = _write_manifest(
        tmp_path, "default_personas:\n  - security-reviewer\n  - scurity-reviewer\n"
    )
    check_manifest(manifest)
    assert validation_state._fail == 1
    assert "scurity-reviewer" in validation_state.results[0]


def test_resolvable_default_personas_pass(tmp_path):
    manifest = _write_manifest(
        tmp_path, "default_personas:\n  - security-reviewer\n  - test-reviewer\n"
    )
    check_manifest(manifest)
    assert validation_state._fail == 0
    assert validation_state._pass == 1


# ── knowledge.* path existence (#363) ───────────────────────────────────
# WARN, not FAIL: the run completes with less context than asked for, which is
# exactly why a typo here survives.


def _project_manifest(tmp_path: pathlib.Path, frontmatter: str) -> pathlib.Path:
    """A manifest at its real location, so paths resolve against the repo root."""
    claude = tmp_path / ".claude"
    claude.mkdir()
    path = claude / "rig.md"
    path.write_text(f"---\n{frontmatter}---\n\n# manifest\n", encoding="utf-8")
    return path


def test_missing_knowledge_paths_warn_but_do_not_fail(tmp_path):
    manifest = _project_manifest(tmp_path, (
        "default_backend: manual\n"
        "knowledge:\n"
        '  context_file: "docs/CONTEXTT.md"\n'
        '  adr_dir: "docs/decisions/"\n'
        "  design_docs:\n"
        '    - "docs/architecture.mdd"\n'
    ))
    check_manifest(manifest)
    assert validation_state._fail == 0
    assert validation_state._warn == 3
    assert validation_state._pass == 1


def test_existing_knowledge_paths_pass(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "CONTEXT.md").write_text("x", encoding="utf-8")
    (tmp_path / "docs" / "decisions").mkdir()
    (tmp_path / "docs" / "architecture.md").write_text("x", encoding="utf-8")
    manifest = _project_manifest(tmp_path, (
        "knowledge:\n"
        '  context_file: "docs/CONTEXT.md"\n'
        '  adr_dir: "docs/decisions"\n'
        "  design_docs:\n"
        '    - "docs/architecture.md"\n'
    ))
    check_manifest(manifest)
    assert validation_state._warn == 0
    assert validation_state._pass == 1
    assert "3 value key(s)" in validation_state.results[0]


def test_empty_knowledge_keys_are_skipped(tmp_path):
    manifest = _project_manifest(tmp_path, (
        "default_backend: manual\n"
        "knowledge:\n"
        '  context_file: ""\n'
        "  design_docs: []\n"
    ))
    check_manifest(manifest)
    assert validation_state._warn == 0
    assert "1 value key(s)" in validation_state.results[0]


def test_a_knowledge_warning_does_not_suppress_a_value_key_failure(tmp_path):
    """WARN and FAIL are collected separately; one must not hide the other."""
    manifest = _project_manifest(tmp_path, (
        "default_backend: manul\n"
        "knowledge:\n"
        '  context_file: "docs/nope.md"\n'
    ))
    check_manifest(manifest)
    assert validation_state._warn == 1
    assert validation_state._fail == 1
