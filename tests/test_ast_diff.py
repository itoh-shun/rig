"""AST-based semantic diff summary for Python (#280).

Unit tests for the pure semantic_diff()/format_summary() functions, plus a
subprocess smoke test of the workbench.py diff integration.
"""

import importlib.util
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"

_SPEC = importlib.util.spec_from_file_location("ast_diff", REPO_ROOT / "scripts" / "ast_diff.py")
ast_diff = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ast_diff)


def test_added_and_removed_functions():
    base = "def a():\n    pass\n"
    new = "def a():\n    pass\ndef b():\n    pass\n"
    r = ast_diff.semantic_diff(base, new)
    assert r["supported"] is True
    assert r["added"] == ["b"] and r["removed"] == []

    r2 = ast_diff.semantic_diff(new, base)
    assert r2["removed"] == ["b"]


def test_signature_change_is_distinguished_from_body_change():
    base = "def a(x):\n    return x\n"
    new_sig = "def a(x, y):\n    return x\n"
    r = ast_diff.semantic_diff(base, new_sig)
    assert r["changed"] == ["a"] and r["signature_changed"] == ["a"]

    new_body = "def a(x):\n    return x + 1\n"
    r2 = ast_diff.semantic_diff(base, new_body)
    assert r2["changed"] == ["a"] and r2["signature_changed"] == []


def test_cosmetic_only_change_detected():
    base = "def a():\n    return 1\n"
    new = "def a():\n\n    return 1  # comment\n"
    r = ast_diff.semantic_diff(base, new)
    assert r["cosmetic_only"] is True
    assert "no semantic change" in ast_diff.format_summary(r, "f.py")


def test_identical_source_is_not_flagged_cosmetic():
    src = "def a():\n    return 1\n"
    r = ast_diff.semantic_diff(src, src)
    assert r["cosmetic_only"] is False  # base == new, not "differs only cosmetically"


def test_class_level_defs_are_qualified_by_class_name():
    base = "class C:\n    def m(self):\n        pass\n"
    new = "class C:\n    def m(self, x):\n        pass\n"
    r = ast_diff.semantic_diff(base, new)
    assert r["signature_changed"] == ["C.m"]


def test_unparseable_source_reports_unsupported():
    r = ast_diff.semantic_diff("def a(:\n", "def a():\n    pass\n")
    assert r["supported"] is False
    assert "parse error" in r["reason"]
    assert "AST parse unsupported" in ast_diff.format_summary(r, "f.py")


def test_format_summary_lists_additions_removals_and_changes():
    base = "def a():\n    pass\ndef b():\n    pass\n"
    new = "def a(x):\n    pass\ndef c():\n    pass\n"
    r = ast_diff.semantic_diff(base, new)
    out = ast_diff.format_summary(r, "f.py")
    assert "+ added: c" in out
    assert "- removed: b" in out
    assert "signature changed: a" in out


# ---- workbench.py diff integration -----------------------------------------

def run_cli(args, cwd):
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "mod.py").write_text("def a():\n    pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "mod.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_diff_command_shows_semantic_diff_section_for_modified_python_file(git_repo):
    r = run_cli(["new", "test task", "--type", "feature", "--no-worktree"], git_repo)
    assert r.returncode == 0
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    (git_repo / "mod.py").write_text("def a(x):\n    return x\n", encoding="utf-8")
    r = run_cli(["diff", task_id], git_repo)
    assert r.returncode == 0
    assert "Semantic diff (Python, #280):" in r.stdout
    assert "mod.py" in r.stdout
    assert "signature changed: a" in r.stdout


def test_diff_command_has_no_semantic_section_without_python_changes(git_repo):
    r = run_cli(["new", "test task", "--type", "feature", "--no-worktree"], git_repo)
    assert r.returncode == 0
    task_id = next((git_repo / ".rig" / "runs").iterdir()).name
    (git_repo / "readme.txt").write_text("hello\n", encoding="utf-8")
    r = run_cli(["diff", task_id], git_repo)
    assert r.returncode == 0
    assert "Semantic diff (Python, #280):" not in r.stdout
