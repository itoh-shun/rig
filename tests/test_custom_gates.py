"""Project-level custom acceptance criteria via `.rig/gates.json` (issue #283).

Covers: additive composition into build_acceptance (preset keys and task_type
keys, origin: project, descriptions), absent file = exact no-op, and hard
validation errors for every malformed shape (a silently ignored gate criterion
is the worst failure mode, so these must die, not warn).
"""

import json
import subprocess
import sys

import pytest

from rig_workbench.workbench.state import build_acceptance, load_project_gates

REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def write_gates(root, data) -> None:
    d = root / ".rig"
    d.mkdir(exist_ok=True)
    (d / "gates.json").write_text(
        data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def names(acc):
    return [c["name"] for c in acc["checks"]]


# ── happy path ────────────────────────────────────────────────────────────────
def test_absent_file_is_noop(tmp_path):
    assert load_project_gates(tmp_path) == {}
    assert build_acceptance("t1", "feature", tmp_path) == build_acceptance("t1", "feature")


def test_preset_key_adds_criterion_with_project_origin(tmp_path):
    write_gates(tmp_path, {"extra_criteria": {"standard": ["license_check_passed"]},
                           "descriptions": {"license_check_passed": "OSS license scan is green"}})
    acc = build_acceptance("t1", "feature", tmp_path)
    check = next(c for c in acc["checks"] if c["name"] == "license_check_passed")
    assert check["status"] == "pending"  # defaults to pending like built-ins
    assert check["origin"] == "project"
    assert check["description"] == "OSS license scan is green"
    # built-ins carry no origin marker
    builtin = next(c for c in acc["checks"] if c["name"] == "task_intent_satisfied")
    assert "origin" not in builtin


def test_preset_key_applies_to_every_task_type_using_that_preset(tmp_path):
    write_gates(tmp_path, {"extra_criteria": {"standard": ["license_check_passed"]}})
    for task_type in ("feature", "bugfix", "documentation"):
        assert "license_check_passed" in names(build_acceptance("t", task_type, tmp_path))
    # review preset does not include standard
    assert "license_check_passed" not in names(build_acceptance("t", "review", tmp_path))


def test_task_type_key_applies_only_to_that_task_type(tmp_path):
    write_gates(tmp_path, {"extra_criteria": {"performance": ["benchmark_recorded"]}})
    assert "benchmark_recorded" in names(build_acceptance("t", "performance", tmp_path))
    assert "benchmark_recorded" not in names(build_acceptance("t", "bugfix", tmp_path))


def test_duplicate_of_builtin_is_deduped_and_stays_builtin(tmp_path):
    write_gates(tmp_path, {"extra_criteria": {"standard": ["no_secret_leak"]}})
    acc = build_acceptance("t", "feature", tmp_path)
    dupes = [c for c in acc["checks"] if c["name"] == "no_secret_leak"]
    assert len(dupes) == 1
    assert "origin" not in dupes[0]


# ── validation (hard errors) ──────────────────────────────────────────────────
def expect_die(tmp_path, capsys, *fragments):
    with pytest.raises(SystemExit):
        load_project_gates(tmp_path)
    err = capsys.readouterr().err
    for frag in fragments:
        assert frag in err, f"expected {frag!r} in error: {err}"


def test_invalid_json_dies(tmp_path, capsys):
    write_gates(tmp_path, "{not json")
    expect_die(tmp_path, capsys, ".rig/gates.json", "not valid JSON")


def test_non_object_dies(tmp_path, capsys):
    write_gates(tmp_path, "[1, 2]")
    expect_die(tmp_path, capsys, "must be a JSON object")


def test_unknown_top_level_key_dies(tmp_path, capsys):
    write_gates(tmp_path, {"extra_critera": {"standard": ["x"]}})  # typo must not be ignored
    expect_die(tmp_path, capsys, "unknown key 'extra_critera'")


def test_remove_key_dies_naming_additive_only_posture(tmp_path, capsys):
    write_gates(tmp_path, {"remove": ["no_secret_leak"]})
    expect_die(tmp_path, capsys, "additive only", "security posture")


def test_unknown_preset_name_dies(tmp_path, capsys):
    write_gates(tmp_path, {"extra_criteria": {"no_such_preset": ["a_criterion"]}})
    expect_die(tmp_path, capsys, "no_such_preset", "neither a gate preset")


def test_non_list_criteria_dies(tmp_path, capsys):
    write_gates(tmp_path, {"extra_criteria": {"standard": "license_check_passed"}})
    expect_die(tmp_path, capsys, "extra_criteria['standard']", "must be a list")


def test_non_slug_criterion_id_dies(tmp_path, capsys):
    write_gates(tmp_path, {"extra_criteria": {"standard": ["License Check!"]}})
    expect_die(tmp_path, capsys, "License Check!", "not a slug")


def test_description_for_unknown_criterion_dies(tmp_path, capsys):
    write_gates(tmp_path, {"descriptions": {"totally_unknown_thing": "?"}})
    expect_die(tmp_path, capsys, "totally_unknown_thing", "matches no")


def test_openapi_paths_must_be_relative_string_list(tmp_path, capsys):
    write_gates(tmp_path, {"openapi_paths": ["/etc/openapi.json"]})
    expect_die(tmp_path, capsys, "repo-relative")
    write_gates(tmp_path, {"openapi_paths": "openapi.json"})
    expect_die(tmp_path, capsys, "openapi_paths", "list")


# ── end to end through the CLI (scratch git repo) ─────────────────────────────
def test_cli_new_and_status_show_project_criterion(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       cwd=repo, check=True, capture_output=True, text=True)
    git("init", "-q")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    write_gates(repo, {"extra_criteria": {"standard": ["license_check_passed"]},
                       "descriptions": {"license_check_passed": "license scan green"}})

    def cli(*args):
        return subprocess.run([sys.executable, str(WORKBENCH), *args],
                              cwd=repo, capture_output=True, text=True, timeout=60)

    r = cli("new", "add feature", "--type", "feature", "--no-worktree")
    assert r.returncode == 0, r.stderr
    acc_files = list((repo / ".rig" / "runs").glob("*/acceptance.json"))
    assert len(acc_files) == 1
    acc = json.loads(acc_files[0].read_text(encoding="utf-8"))
    custom = next(c for c in acc["checks"] if c["name"] == "license_check_passed")
    assert custom["origin"] == "project" and custom["status"] == "pending"

    r = cli("status")
    assert r.returncode == 0, r.stderr
    assert "license_check_passed [project]" in r.stdout

    r = cli("gates")
    assert r.returncode == 0, r.stderr
    assert "license_check_passed" in r.stdout and "origin: project" in r.stdout

    # Malformed file: `new` must abort before creating any run state (no partial task dir).
    runs_before = {p.name for p in (repo / ".rig" / "runs").iterdir()}
    write_gates(repo, {"remove": ["no_secret_leak"]})
    r = cli("new", "another", "--type", "bugfix", "--no-worktree", "--slug", "another")
    assert r.returncode != 0
    assert "additive only" in r.stderr
    assert {p.name for p in (repo / ".rig" / "runs").iterdir()} == runs_before
