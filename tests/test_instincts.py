"""Continuous cross-session instinct-learning layer (#306).

Covers the pure functions (add/decay/select-for-injection, the
learning-forbidden filter) and the CLI end-to-end in a throwaway repo.
"""

import datetime
import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench.instincts import (_instinct_is_learnable,
                                               add_instinct, decay_instincts,
                                               load_instincts,
                                               select_for_injection)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def run_cli(args, cwd):
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


# ---- _instinct_is_learnable (the learning-forbidden filter) ------------------

def test_plain_text_is_learnable():
    ok, reason = _instinct_is_learnable("Prefer Grep over rg for this repo's search tooling")
    assert ok is True and reason == ""


def test_secret_shaped_text_is_rejected():
    ok, reason = _instinct_is_learnable("the key is sk-ant-abcdefghijklmnopqrstuvwxyz012345")
    assert ok is False
    assert "secret" in reason


def test_local_absolute_path_is_rejected():
    ok, reason = _instinct_is_learnable("the config lives at /home/alice/.config/thing.yaml")
    assert ok is False
    assert "local absolute path" in reason


def test_env_assignment_is_rejected():
    ok, reason = _instinct_is_learnable("set RIG_HOME=/some/path before running tests")
    assert ok is False
    assert "ENV_VAR=value" in reason


def test_overlong_text_is_rejected():
    ok, reason = _instinct_is_learnable("x" * 301)
    assert ok is False
    assert "300" in reason


# ---- add_instinct / decay_instincts / select_for_injection (pure functions) --

def test_add_instinct_rejects_and_raises(tmp_path):
    with pytest.raises(ValueError, match="secret"):
        add_instinct(tmp_path, "token is sk-ant-abcdefghijklmnopqrstuvwxyz012345", "", None, 0.5)


def test_add_instinct_records_with_defaults(tmp_path):
    rec = add_instinct(tmp_path, "search with Grep, not rg", "faster in this repo", "rig-1", 0.6)
    assert rec["status"] == "active"
    assert rec["confidence"] == 0.6
    assert rec["source_task_ids"] == ["rig-1"]
    assert rec["hit_count"] == 1
    loaded = load_instincts(tmp_path)
    assert len(loaded) == 1 and loaded[0]["id"] == rec["id"]


def test_supersedes_mutes_the_old_instinct(tmp_path):
    old = add_instinct(tmp_path, "old pattern text", "", None, 0.8)
    add_instinct(tmp_path, "new corrected pattern text", "", None, 0.8, supersedes=old["id"])
    loaded = {r["id"]: r for r in load_instincts(tmp_path)}
    assert loaded[old["id"]]["status"] == "muted"
    assert "superseded" in loaded[old["id"]]["decay_reason"]


def test_decay_lowers_confidence_after_threshold_days(tmp_path):
    add_instinct(tmp_path, "aging pattern", "", None, 0.9)
    future = datetime.datetime.now().astimezone() + datetime.timedelta(days=31)
    n = decay_instincts(tmp_path, now=future)
    assert n == 1
    rec = load_instincts(tmp_path)[0]
    assert rec["confidence"] == pytest.approx(0.8)
    assert rec["status"] == "active"


def test_decay_expires_below_floor(tmp_path):
    add_instinct(tmp_path, "low confidence pattern", "", None, 0.25)
    future = datetime.datetime.now().astimezone() + datetime.timedelta(days=31)
    decay_instincts(tmp_path, now=future)
    rec = load_instincts(tmp_path)[0]
    assert rec["status"] == "expired"
    assert rec["confidence"] < 0.2


def test_decay_is_a_noop_when_recently_seen(tmp_path):
    add_instinct(tmp_path, "fresh pattern", "", None, 0.9)
    n = decay_instincts(tmp_path)  # "now" == first_seen == last_seen
    assert n == 0
    assert load_instincts(tmp_path)[0]["confidence"] == 0.9


def test_select_for_injection_excludes_below_threshold(tmp_path):
    add_instinct(tmp_path, "high confidence", "", None, 0.8)
    add_instinct(tmp_path, "low confidence", "", None, 0.5)
    selected, total = select_for_injection(tmp_path)
    assert [r["text"] for r in selected] == ["high confidence"]
    assert total == len("high confidence")


def test_select_for_injection_respects_char_limit(tmp_path):
    add_instinct(tmp_path, "a" * 300, "", None, 0.9)
    add_instinct(tmp_path, "b" * 300, "", None, 0.85)
    selected, total = select_for_injection(tmp_path)
    assert len(selected) == 1  # second would exceed the 500-char cap
    assert total == 300


def test_select_for_injection_bumps_hit_count_and_last_seen(tmp_path):
    add_instinct(tmp_path, "used pattern", "", None, 0.9)
    select_for_injection(tmp_path)
    rec = load_instincts(tmp_path)[0]
    assert rec["hit_count"] == 2  # 1 from add_instinct + 1 from selection
    # last_seen was refreshed to "now", so decay won't fire immediately after injection.
    assert decay_instincts(tmp_path) == 0


def test_select_for_injection_excludes_muted_and_expired(tmp_path):
    a = add_instinct(tmp_path, "muted one", "", None, 0.9)
    add_instinct(tmp_path, "active one", "", None, 0.9, supersedes=a["id"])
    selected, _ = select_for_injection(tmp_path)
    assert [r["text"] for r in selected] == ["active one"]


# ---- CLI end-to-end ----------------------------------------------------------

def test_cli_add_then_list(git_repo):
    r = run_cli(["instincts", "--add", "prefer Grep over rg here", "--evidence", "repo convention",
                "--confidence", "0.75"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "instinct recorded" in r.stdout

    r = run_cli(["instincts"], git_repo)
    assert r.returncode == 0
    assert "prefer Grep over rg here" in r.stdout
    assert "next injection" in r.stdout


def test_cli_add_rejects_secret_with_nonzero_exit(git_repo):
    r = run_cli(["instincts", "--add", "the token is sk-ant-abcdefghijklmnopqrstuvwxyz012345"], git_repo)
    assert r.returncode != 0
    assert "rejected" in (r.stdout + r.stderr)


def test_cli_mute_and_expire(git_repo):
    run_cli(["instincts", "--add", "some pattern text"], git_repo)
    instincts = load_instincts(git_repo)
    tid = instincts[0]["id"]

    r = run_cli(["instincts", "--mute", tid], git_repo)
    assert r.returncode == 0
    assert load_instincts(git_repo)[0]["status"] == "muted"

    r = run_cli(["instincts", "--expire", tid], git_repo)
    assert r.returncode == 0
    assert load_instincts(git_repo)[0]["status"] == "expired"


def test_cli_mute_unknown_id_errors(git_repo):
    r = run_cli(["instincts", "--mute", "in-doesnotexist"], git_repo)
    assert r.returncode != 0
    assert "not found" in (r.stdout + r.stderr)


def test_cli_supersedes_excludes_muted_from_inject_preview(git_repo):
    run_cli(["instincts", "--add", "old text", "--confidence", "0.9"], git_repo)
    old_id = load_instincts(git_repo)[0]["id"]
    run_cli(["instincts", "--add", "new corrected text", "--confidence", "0.9",
            "--supersedes", old_id], git_repo)

    r = run_cli(["instincts", "--inject-preview", "--json"], git_repo)
    assert r.returncode == 0
    data = json.loads(r.stdout)
    texts = [s["text"] for s in data["selected"]]
    assert "new corrected text" in texts
    assert "old text" not in texts


def test_cli_inject_preview_json_empty_when_nothing_qualifies(git_repo):
    r = run_cli(["instincts", "--inject-preview", "--json"], git_repo)
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data == {"selected": [], "total_chars": 0}


def test_cli_decay_reports_count(git_repo):
    run_cli(["instincts", "--add", "aging text", "--confidence", "0.9"], git_repo)
    tid = load_instincts(git_repo)[0]["id"]
    d = git_repo / ".rig" / "instincts.jsonl"
    recs = [json.loads(ln) for ln in d.read_text(encoding="utf-8").splitlines()]
    ancient = (datetime.datetime.now().astimezone() - datetime.timedelta(days=40)).isoformat(timespec="seconds")
    for r in recs:
        if r["id"] == tid:
            r["last_seen"] = ancient
    d.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")

    r = run_cli(["instincts", "--decay"], git_repo)
    assert r.returncode == 0
    assert "Decayed 1 instinct" in r.stdout
