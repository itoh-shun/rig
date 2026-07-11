"""Deterministic secret scanner backing `no_secret_leak` (issue #273, scoped).

Covers: every named pattern class, masking (the raw secret never appears in a
finding), the generic entropy detector (catches random base64, skips lockfile
hash paths), clean-tree no-findings, diff-scoped scanning, and the fail-grade
gate integration in a scratch repo (secret in the task diff → no_secret_leak
failed with a masked excerpt; explicit --set passed is the escape hatch).
"""

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from rig_workbench.workbench.secrets import (apply_secret_sensor,
                                             entropy_allowlisted, mask,
                                             scan_diff_text,
                                             scan_line, scan_paths,
                                             scan_worktree_diff,
                                             shannon_entropy)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"

# One representative sample per pattern class (all synthetic).
SAMPLES = [
    ("aws_access_key", "AKIAIOSFODNN7EXAMPLE"),
    ("aws_access_key", "ASIAJ4X9K2M7Q1R5T8W3"),
    ("private_key_pem", "-----BEGIN RSA PRIVATE KEY-----"),
    ("private_key_pem", "-----BEGIN OPENSSH PRIVATE KEY-----"),
    ("github_token", "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"),
    ("github_token", "github_pat_11ABCDEFG0abcdefghijklmnopqrstuv"),
    ("slack_token", "xoxb-" + "283736350342-4939293923-abcDefGhi123kLmNo"),
    ("anthropic_api_key", "sk-ant-api03-AbCd1234EfGh5678IjKl9012MnOp"),
    ("openai_api_key", "sk-Ab12Cd34Ef56Gh78Ij90Kl12Mn34Op56"),
    ("google_api_key", "AIzaSyD9x2Qw8Rt4Yv6Ub1Zc3Ln5Mo7Pq9Sr0Tu"),
    ("jwt", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjIzIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVadQssw5c"),
]

RANDOM_B64_40 = "R9k2mVxZ8qLpW3nYtB7cJdF5hGsA1uEoNiP4KrTe"  # 40 chars, high entropy


# ── pattern classes ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind,secret", SAMPLES, ids=[f"{k}-{i}" for i, (k, _) in enumerate(SAMPLES)])
def test_each_pattern_class_detected(kind, secret):
    findings = scan_line(f'token = "{secret}"  # planted', "src/config.py", 7)
    assert any(f["kind"] == kind for f in findings), findings
    f = next(f for f in findings if f["kind"] == kind)
    assert f["path"] == "src/config.py" and f["line"] == 7


def test_named_pattern_not_double_reported_as_entropy():
    secret = "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"  # 40 chars, also entropy-shaped
    findings = scan_line(f"x = '{secret}'", "a.py", 1)
    assert [f["kind"] for f in findings] == ["github_token"]


def test_plain_prose_line_is_clean():
    assert scan_line("the quick brown fox jumps over the lazy dog", "a.py", 1) == []


# ── masking ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind,secret", SAMPLES, ids=[f"{k}-{i}" for i, (k, _) in enumerate(SAMPLES)])
def test_masking_raw_secret_absent_from_finding(kind, secret):
    findings = scan_line(secret, "a.py", 1)
    for f in findings:
        assert secret not in f["masked_excerpt"]
        assert secret not in json.dumps(f)          # nowhere in the whole finding
        assert secret[4:-2] not in f["masked_excerpt"]  # the middle is really gone


def test_mask_shape():
    assert mask("AKIAIOSFODNN7EXAMPLE").startswith("AKIA")
    assert mask("AKIAIOSFODNN7EXAMPLE").endswith("LE")
    assert mask("short") == "*****"  # ≤8 chars: fully masked


# ── entropy detector ──────────────────────────────────────────────────────────
def test_entropy_detector_catches_random_40char_base64(tmp_path):
    assert shannon_entropy(RANDOM_B64_40) > 4.5  # sanity: the fixture really is high-entropy
    p = tmp_path / "settings.py"
    p.write_text(f'SIGNING_KEY = "{RANDOM_B64_40}"\n', encoding="utf-8")
    findings = scan_paths([tmp_path])
    assert any(f["kind"] == "high_entropy" for f in findings)
    assert all(RANDOM_B64_40 not in f["masked_excerpt"] for f in findings)


def test_entropy_detector_skips_lockfile_hash_paths(tmp_path):
    sha512 = "sha512-" + RANDOM_B64_40 + "mB7xQ2kVjNfR5tYcW9zL3pD1gHsE8uAoKi=="
    for name in ("package-lock.json", "Cargo.lock", "go.sum"):
        (tmp_path / name).write_text(f'"integrity": "{sha512}"\n', encoding="utf-8")
    assert scan_paths([tmp_path]) == []
    # path-part based allowlisting too (vendored / VCS trees)
    assert entropy_allowlisted("node_modules/pkg/dist/index.js")
    assert entropy_allowlisted(".git/objects/pack/whatever.idx")
    assert not entropy_allowlisted("src/settings.py")


def test_named_patterns_still_fire_inside_lockfiles():
    # Only the entropy heuristic is allowlisted; a real token is a leak anywhere.
    findings = scan_line("resolved: AKIAIOSFODNN7EXAMPLE", "yarn.lock", 3)
    assert [f["kind"] for f in findings] == ["aws_access_key"]


def test_hex_lockfile_hash_vs_source_file():
    hex64 = "a3f1c9e2b8d4470a5e6f1029c3b7d8e4f0a1b2c3d4e5f60718293a4b5c6d7e8f"
    assert scan_line(hex64, "go.sum", 1) == []                       # allowlisted path
    assert any(f["kind"] == "high_entropy" for f in scan_line(hex64, "src/app.py", 1))


# ── clean tree ────────────────────────────────────────────────────────────────
def test_clean_tree_has_no_findings(tmp_path):
    (tmp_path / "app.py").write_text(
        "def add(a, b):\n    return a + b\n\nGREETING = 'hello world'\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# demo\n\nA perfectly ordinary readme.\n", encoding="utf-8")
    assert scan_paths([tmp_path]) == []


def test_binary_file_is_skipped(tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01" + b"AKIAIOSFODNN7EXAMPLE")
    assert scan_paths([tmp_path]) == []


# ── diff-scoped scan ──────────────────────────────────────────────────────────
def test_scan_diff_text_reports_added_lines_with_new_file_lines():
    diff = (
        "diff --git a/src/cfg.py b/src/cfg.py\n"
        "index 111..222 100644\n"
        "--- a/src/cfg.py\n"
        "+++ b/src/cfg.py\n"
        "@@ -0,0 +10,2 @@\n"
        "+AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"
        "+OTHER = 1\n"
        "@@ -20 +30 @@\n"
        "-OLD = 'AKIAJ4X9K2M7Q1R5T8W3'\n"
        "+NEW = 'nothing secret here'\n"
    )
    findings = scan_diff_text(diff)
    assert len(findings) == 1  # removed lines are not scanned
    assert findings[0] == {"path": "src/cfg.py", "line": 10, "kind": "aws_access_key",
                           "masked_excerpt": findings[0]["masked_excerpt"]}
    assert "AKIAIOSFODNN7EXAMPLE" not in findings[0]["masked_excerpt"]


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True, text=True)


def make_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                         capture_output=True, text=True).stdout.strip()
    return repo, sha


def test_scan_worktree_diff_sees_uncommitted_and_untracked(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / "app.py").write_text("x = 1\nTOKEN = '" + "xoxb-" + "283736350342-4939293923-abcDefGhi123kLmNo'\n",
                                 encoding="utf-8")  # uncommitted edit
    (repo / "new.env").write_text("KEY=AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")  # untracked
    kinds = {f["kind"] for f in scan_worktree_diff(repo, sha)}
    assert {"slack_token", "aws_access_key"} <= kinds


def test_scan_worktree_diff_clean_worktree_no_findings(tmp_path):
    repo, sha = make_repo(tmp_path)
    assert scan_worktree_diff(repo, sha) == []


# ── gate sensor (unit) ────────────────────────────────────────────────────────
def make_state(repo, sha):
    task = {"worktree_path": str(repo), "base_commit": sha}
    acc = {"checks": [{"name": "no_secret_leak", "status": "pending", "detail": ""}]}
    return task, acc


def test_sensor_fails_check_on_secret_in_diff(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / "cfg.py").write_text("AWS = 'AKIAIOSFODNN7EXAMPLE'\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "leak")
    task, acc = make_state(repo, sha)
    notes = apply_secret_sensor(repo, tmp_path, task, acc)
    check = acc["checks"][0]
    assert check["status"] == "failed"  # fail-grade, unlike the schema sensor
    assert check["secret_findings"]
    assert "AKIAIOSFODNN7EXAMPLE" not in "\n".join(notes + check["secret_findings"])
    assert any("no_secret_leak failed" in n for n in notes)


def test_sensor_respects_explicit_pass_and_sticks(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / "cfg.py").write_text("AWS = 'AKIAIOSFODNN7EXAMPLE'\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "leak")
    task, acc = make_state(repo, sha)
    # escape hatch: user reviewed and explicitly set passed in this invocation
    acc["checks"][0]["status"] = "passed"
    notes = apply_secret_sensor(repo, tmp_path, task, acc, explicit_set={"no_secret_leak"})
    assert acc["checks"][0]["status"] == "passed"
    assert acc["checks"][0]["secret_override"] is True
    assert any("manual override" in n for n in notes)
    # ...and the override survives later evaluations without --set
    notes = apply_secret_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "passed"


def test_sensor_resets_its_own_failure_when_secret_removed(tmp_path):
    repo, sha = make_repo(tmp_path)
    (repo / "cfg.py").write_text("AWS = 'AKIAIOSFODNN7EXAMPLE'\n", encoding="utf-8")
    task, acc = make_state(repo, sha)
    apply_secret_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "failed"
    (repo / "cfg.py").unlink()
    apply_secret_sensor(repo, tmp_path, task, acc)
    assert acc["checks"][0]["status"] == "pending"
    assert "secret_findings" not in acc["checks"][0]


def test_sensor_noop_without_criterion_or_worktree(tmp_path):
    repo, sha = make_repo(tmp_path)
    acc = {"checks": [{"name": "tests_pass_or_explained", "status": "pending", "detail": ""}]}
    assert apply_secret_sensor(repo, tmp_path, {"worktree_path": str(repo), "base_commit": sha}, acc) == []
    task, acc = make_state(repo, sha)
    assert apply_secret_sensor(repo, tmp_path, {"worktree_path": None, "base_commit": sha}, acc) == []


# ── end to end through the CLI (scratch repo, real worktree) ──────────────────
def cli(repo, wt_root, *args):
    env = dict(os.environ, RIG_WORKTREE_ROOT=str(wt_root))
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          cwd=repo, capture_output=True, text=True, timeout=60, env=env)


def test_gate_integration_secret_in_diff_fails_no_secret_leak(tmp_path):
    repo, _sha = make_repo(tmp_path)
    wt_root = tmp_path / "wt"

    r = cli(repo, wt_root, "new", "add config", "--type", "feature", "--slug", "add-config")
    assert r.returncode == 0, r.stderr
    task_id = re.search(r"task_id: (\S+)", r.stdout).group(1)
    wt = wt_root / task_id
    assert wt.is_dir()

    (wt / "config.py").write_text('AWS_SECRET = "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "plant secret")

    r = cli(repo, wt_root, "gate", task_id)
    assert r.returncode == 1  # findings must block: gate is FAILED
    assert "no_secret_leak" in r.stdout
    assert "potential secret(s) detected" in r.stdout
    assert "AKIAIOSFODNN7EXAMPLE" not in r.stdout + r.stderr  # masked everywhere
    assert "AKIA" in r.stdout  # masked excerpt shows the prefix

    acc = json.loads((repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == "no_secret_leak")
    assert check["status"] == "failed"
    assert check["secret_findings"]
    assert all("AKIAIOSFODNN7EXAMPLE" not in ln for ln in check["secret_findings"])

    # scan-secrets --diff exposes the same findings, masked, exit 1
    r = cli(repo, wt_root, "scan-secrets", "--diff", task_id)
    assert r.returncode == 1
    assert "aws_access_key" in r.stdout and "AKIAIOSFODNN7EXAMPLE" not in r.stdout

    # documented escape hatch: explicit --set no_secret_leak=passed after review
    r = cli(repo, wt_root, "gate", task_id, "--set", "no_secret_leak=passed")
    assert r.returncode == 0, r.stdout + r.stderr
    acc = json.loads((repo / ".rig" / "runs" / task_id / "acceptance.json").read_text(encoding="utf-8"))
    check = next(c for c in acc["checks"] if c["name"] == "no_secret_leak")
    assert check["status"] == "passed" and check.get("secret_override") is True


def test_scan_secrets_cli_clean_paths_exits_zero(tmp_path):
    repo, _sha = make_repo(tmp_path)
    r = cli(repo, tmp_path / "wt", "scan-secrets", ".")
    assert r.returncode == 0, r.stderr
    assert "No potential secrets found." in r.stdout
