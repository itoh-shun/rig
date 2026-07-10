"""Machine-verified OpenAPI schema diff backing public_api_changes_documented (issue #288).

Covers: operation-level diff on synthetic before/after specs (added path,
removed method, changed param/required/response codes), graceful parse
degradation, and the acceptance-state sensor (warning when undocumented,
clean skip when no schema file exists or nothing changed).
"""

import json
import subprocess

import pytest

from rig_workbench.workbench.schema_diff import (apply_schema_sensor,
                                                 diff_specs, parse_spec,
                                                 schema_paths, summarize)

BASE_SPEC = {
    "openapi": "3.0.0",
    "paths": {
        "/pets": {
            "get": {
                "parameters": [{"name": "limit", "in": "query", "required": False}],
                "responses": {"200": {"description": "ok"}},
            },
            "delete": {"responses": {"204": {"description": "deleted"}}},
        },
    },
}

HEAD_SPEC = {
    "openapi": "3.0.0",
    "paths": {
        "/pets": {
            "get": {
                "parameters": [
                    {"name": "limit", "in": "query", "required": True},
                    {"name": "q", "in": "query"},
                ],
                "responses": {"200": {"description": "ok"}, "404": {"description": "missing"}},
            },
        },
        "/owners": {"post": {"responses": {"201": {"description": "created"}}}},
    },
}


# ── pure diff ─────────────────────────────────────────────────────────────────
def test_diff_detects_added_path_removed_method_and_changed_op():
    d = diff_specs(BASE_SPEC, HEAD_SPEC)
    assert d["added"] == ["POST /owners"]
    assert d["removed"] == ["DELETE /pets"]
    assert len(d["changed"]) == 1 and d["changed"][0]["op"] == "GET /pets"
    changes = d["changed"][0]["changes"]
    assert "param added: q" in changes
    assert any(c.startswith("param changed: limit") and "required" in c for c in changes)
    assert "response added: 404" in changes


def test_identical_specs_diff_empty():
    d = diff_specs(BASE_SPEC, BASE_SPEC)
    assert d == {"added": [], "removed": [], "changed": []}
    assert summarize(d) == []


def test_summarize_flattens_all_categories():
    lines = summarize(diff_specs(BASE_SPEC, HEAD_SPEC))
    assert any(line.startswith("added: POST /owners") for line in lines)
    assert any(line.startswith("removed: DELETE /pets") for line in lines)
    assert any(line.startswith("changed: GET /pets") for line in lines)


def test_parse_spec_json_and_invalid():
    assert parse_spec(json.dumps(BASE_SPEC), "openapi.json") == BASE_SPEC
    assert parse_spec("{broken", "openapi.json") is None
    assert parse_spec("[1]", "openapi.json") is None  # non-object document


# ── git-backed sensor ─────────────────────────────────────────────────────────
def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True, text=True)


def make_repo(tmp_path, schema_rel="openapi.json"):
    """Git repo with BASE_SPEC committed at `schema_rel`; returns (repo, base_sha)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    p = repo / schema_rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(BASE_SPEC), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                         capture_output=True, text=True).stdout.strip()
    return repo, sha


def make_state(repo, sha):
    task = {"worktree_path": str(repo), "base_commit": sha}
    acc = {"checks": [{"name": "public_api_changes_documented", "status": "pending", "detail": ""}]}
    return task, acc


@pytest.fixture
def run_d(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    return d


def test_changed_schema_without_diff_md_records_warning(tmp_path, run_d):
    repo, sha = make_repo(tmp_path)
    (repo / "openapi.json").write_text(json.dumps(HEAD_SPEC), encoding="utf-8")
    task, acc = make_state(repo, sha)
    notes = apply_schema_sensor(repo, run_d, task, acc)
    check = acc["checks"][0]
    assert check["status"] == "warning"  # warning-grade sensor, never failed
    assert check["detail"]
    assert any("added: POST /owners" in line for line in check["api_diff"])
    assert notes and "recorded as warning" in notes[0]


def test_changed_schema_with_diff_md_keeps_status_but_records_finding(tmp_path, run_d):
    repo, sha = make_repo(tmp_path)
    (repo / "openapi.json").write_text(json.dumps(HEAD_SPEC), encoding="utf-8")
    (run_d / "diff.md").write_text("## Summary\nAdded POST /owners.\n", encoding="utf-8")
    task, acc = make_state(repo, sha)
    notes = apply_schema_sensor(repo, run_d, task, acc)
    check = acc["checks"][0]
    assert check["status"] == "pending"
    assert check["api_diff"]  # machine finding surfaced in acceptance state
    assert notes and "confirm they are documented" in notes[0]


def test_explicit_failed_status_is_never_overridden(tmp_path, run_d):
    repo, sha = make_repo(tmp_path)
    (repo / "openapi.json").write_text(json.dumps(HEAD_SPEC), encoding="utf-8")
    task, acc = make_state(repo, sha)
    acc["checks"][0]["status"] = "failed"
    apply_schema_sensor(repo, run_d, task, acc)
    assert acc["checks"][0]["status"] == "failed"


def test_no_schema_file_is_clean_skip(tmp_path, run_d):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "src.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                         capture_output=True, text=True).stdout.strip()
    task, acc = make_state(repo, sha)
    notes = apply_schema_sensor(repo, run_d, task, acc)
    assert notes == []
    assert acc["checks"][0]["status"] == "pending"
    assert "api_diff" not in acc["checks"][0]


def test_unchanged_schema_is_clean_skip(tmp_path, run_d):
    repo, sha = make_repo(tmp_path)
    task, acc = make_state(repo, sha)
    assert apply_schema_sensor(repo, run_d, task, acc) == []
    assert acc["checks"][0]["status"] == "pending"


def test_criterion_absent_is_noop(tmp_path, run_d):
    repo, sha = make_repo(tmp_path)
    (repo / "openapi.json").write_text(json.dumps(HEAD_SPEC), encoding="utf-8")
    task = {"worktree_path": str(repo), "base_commit": sha}
    acc = {"checks": [{"name": "no_secret_leak", "status": "pending", "detail": ""}]}
    assert apply_schema_sensor(repo, run_d, task, acc) == []


def test_openapi_paths_from_gates_json_overrides_autodetect(tmp_path, run_d):
    repo, sha = make_repo(tmp_path, schema_rel="spec/service.json")
    rig = repo / ".rig"
    rig.mkdir()
    (rig / "gates.json").write_text(json.dumps({"openapi_paths": ["spec/service.json"]}),
                                    encoding="utf-8")
    assert schema_paths(repo, sha, {"openapi_paths": ["spec/service.json"]}) == ["spec/service.json"]
    (repo / "spec" / "service.json").write_text(json.dumps(HEAD_SPEC), encoding="utf-8")
    task, acc = make_state(repo, sha)
    notes = apply_schema_sensor(repo, run_d, task, acc)
    assert acc["checks"][0]["status"] == "warning"
    assert any("spec/service.json" in line for line in notes)


def test_autodetect_finds_schema_only_present_at_base_ref(tmp_path, run_d):
    """A schema deleted by the task still triggers the sensor (removed API)."""
    repo, sha = make_repo(tmp_path)
    (repo / "openapi.json").unlink()
    task, acc = make_state(repo, sha)
    apply_schema_sensor(repo, run_d, task, acc)
    check = acc["checks"][0]
    assert check["status"] == "warning"
    assert any("removed: GET /pets" in line for line in check["api_diff"])
