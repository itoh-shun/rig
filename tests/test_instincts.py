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

from rig_workbench.workbench.instincts import (_host_instincts_path,
                                               _instinct_is_learnable,
                                               add_instinct, decay_instincts,
                                               demote_instinct, load_instincts,
                                               load_host_instincts,
                                               promote_instinct,
                                               select_for_injection)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"


def run_cli(args, cwd):
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


@pytest.fixture(autouse=True)
def isolated_host_tier(tmp_path, monkeypatch):
    """Give each test its own host tier.

    conftest already keeps the suite off the developer's real `~/.rig/instincts.jsonl`;
    this narrows it further to one directory per test, so two tests in this file cannot
    see each other's promoted records. Subprocesses inherit it through os.environ.
    """
    host_home = tmp_path / "host-home"
    monkeypatch.setenv("RIG_USER_HOME", str(host_home))
    return host_home


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


# ---- host tier: promotion, injection order, undo (T9) ------------------------

def test_promote_moves_the_record_out_of_the_project_tier(git_repo):
    add_instinct(git_repo, "subagents sometimes return an idle notification instead of a result",
                 "observed twice in one session", None, 0.85)
    target = load_instincts(git_repo)[0]["id"]

    promoted = promote_instinct(git_repo, target)

    assert promoted["id"] == target
    assert promoted["promoted_at"]
    assert [r["id"] for r in load_host_instincts()] == [target]
    assert load_instincts(git_repo) == []


def test_promoted_instinct_is_injected_from_an_unrelated_repo(git_repo, tmp_path):
    add_instinct(git_repo, "this machine has no jq; use python3 to read JSON",
                 "jq missing", None, 0.9)
    promote_instinct(git_repo, load_instincts(git_repo)[0]["id"])

    other = tmp_path / "other-repo"
    (other / ".rig").mkdir(parents=True)

    selected, _ = select_for_injection(other)

    assert [s["text"] for s in selected] == ["this machine has no jq; use python3 to read JSON"]


def _raw_instinct(rec_id: str, text: str, confidence: float) -> dict:
    return {"id": rec_id, "text": text, "evidence": "e", "source_task_ids": [],
            "confidence": confidence, "first_seen": "2026-01-01T00:00:00+09:00",
            "last_seen": "2026-01-01T00:00:00+09:00", "hit_count": 1,
            "decay_reason": None, "status": "active", "supersedes": []}


def _write_store(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                    encoding="utf-8")


def test_project_tier_wins_ties_against_the_host_tier(git_repo):
    """Equal confidence must not let a promoted record displace a local one — the tier
    is only a tie-break, so promotion widens reach without silently outranking what the
    repo learned about itself.

    The ids are written by hand, and the host one sorts *first* alphabetically. With
    `add_instinct` they would be content hashes, so the third sort key (id) would decide
    the order about half the time and an implementation that dropped the tier key would
    pass on the coin flip.
    """
    _write_store(_host_instincts_path(), [_raw_instinct("in-aaaaaaaaaa", "host fact", 0.9)])
    _write_store(git_repo / ".rig" / "instincts.jsonl",
                 [_raw_instinct("in-zzzzzzzzzz", "project fact", 0.9)])

    selected, _ = select_for_injection(git_repo)

    assert [s["text"] for s in selected] == ["project fact", "host fact"]


def test_promotion_does_not_shrink_the_budget_available_to_project_instincts(git_repo, tmp_path):
    """The 500-char injection budget is the reason promotion is per-record. A host
    instinct that loses on confidence must not consume budget a project one needs."""
    seed = tmp_path / "seed-repo"
    (seed / ".rig").mkdir(parents=True)
    add_instinct(seed, "L" * 280, "e", None, 0.75)
    promote_instinct(seed, load_instincts(seed)[0]["id"])
    add_instinct(git_repo, "P" * 280, "e", None, 0.95)

    selected, total = select_for_injection(git_repo)

    assert [s["text"] for s in selected] == ["P" * 280]  # a second 280 would blow the 500 cap
    assert total == 280


def test_host_records_shadowed_by_a_project_id_survive_a_write_back(git_repo, tmp_path):
    """`_load_tiered` hides a host record whose id already exists in the project tier.
    Saving the host file after an injection must still write the hidden record back."""
    seed = tmp_path / "seed-repo"
    (seed / ".rig").mkdir(parents=True)
    add_instinct(seed, "shadowed", "e", None, 0.9)
    shadowed = load_instincts(seed)[0]["id"]
    add_instinct(seed, "also promoted", "e", None, 0.95)
    kept = [r for r in load_instincts(seed) if r["id"] != shadowed][0]["id"]
    promote_instinct(seed, shadowed)
    promote_instinct(seed, kept)
    # give the project tier a record carrying the same id as the shadowed host one
    project_copy = dict(load_host_instincts()[0], text="project copy")
    (git_repo / ".rig").mkdir(parents=True, exist_ok=True)
    (git_repo / ".rig" / "instincts.jsonl").write_text(
        json.dumps(project_copy, ensure_ascii=False) + "\n", encoding="utf-8")

    select_for_injection(git_repo)

    assert sorted(r["id"] for r in load_host_instincts()) == sorted([shadowed, kept])


def test_demote_returns_the_record_to_the_current_repo(git_repo):
    add_instinct(git_repo, "not actually a host fact", "e", None, 0.8)
    target = load_instincts(git_repo)[0]["id"]
    promote_instinct(git_repo, target)

    demoted = demote_instinct(git_repo, target)

    assert "promoted_at" not in demoted
    assert [r["id"] for r in load_instincts(git_repo)] == [target]
    assert load_host_instincts() == []


def test_promote_rejects_an_unknown_id(git_repo):
    with pytest.raises(KeyError):
        promote_instinct(git_repo, "in-nosuchthing")


def test_promote_rejects_a_record_already_in_the_host_tier(git_repo, tmp_path):
    add_instinct(git_repo, "dupe", "e", None, 0.8)
    target = load_instincts(git_repo)[0]["id"]
    promote_instinct(git_repo, target)
    # re-create the same id in the project tier, as a second repo might hold
    (git_repo / ".rig" / "instincts.jsonl").write_text(
        json.dumps(load_host_instincts()[0], ensure_ascii=False) + "\n", encoding="utf-8")

    with pytest.raises(ValueError):
        promote_instinct(git_repo, target)


def test_decay_reaches_the_host_tier(git_repo, tmp_path):
    add_instinct(git_repo, "aging host fact", "e", None, 0.9)
    promote_instinct(git_repo, load_instincts(git_repo)[0]["id"])
    host_path = _host_instincts_path()
    recs = [json.loads(ln) for ln in host_path.read_text(encoding="utf-8").splitlines()]
    recs[0]["last_seen"] = (datetime.datetime.now().astimezone()
                            - datetime.timedelta(days=40)).isoformat(timespec="seconds")
    host_path.write_text(json.dumps(recs[0], ensure_ascii=False) + "\n", encoding="utf-8")

    assert decay_instincts(git_repo) == 1
    assert load_host_instincts()[0]["confidence"] < 0.9


def test_cli_mute_reaches_a_promoted_instinct(git_repo):
    run_cli(["instincts", "--add", "noisy host fact", "--confidence", "0.9"], git_repo)
    target = load_instincts(git_repo)[0]["id"]
    assert run_cli(["instincts", "--promote", target], git_repo).returncode == 0

    r = run_cli(["instincts", "--mute", target], git_repo)

    assert r.returncode == 0, r.stdout + r.stderr
    assert load_host_instincts()[0]["status"] == "muted"


def test_cli_promote_then_demote_round_trips(git_repo):
    run_cli(["instincts", "--add", "round trip", "--confidence", "0.8"], git_repo)
    target = load_instincts(git_repo)[0]["id"]

    assert run_cli(["instincts", "--promote", target], git_repo).returncode == 0
    assert load_instincts(git_repo) == []
    assert run_cli(["instincts", "--demote", target], git_repo).returncode == 0
    assert [r["id"] for r in load_instincts(git_repo)] == [target]


def test_demote_rejects_an_id_that_is_not_in_the_host_tier(git_repo):
    add_instinct(git_repo, "still local", "e", None, 0.8)

    with pytest.raises(KeyError):
        demote_instinct(git_repo, load_instincts(git_repo)[0]["id"])


def test_demote_rejects_an_id_this_repo_already_holds(git_repo):
    """Another repo can hold the same id in its project tier — bringing the host copy
    down on top of it would leave two records claiming to be one."""
    add_instinct(git_repo, "collides", "e", None, 0.8)
    target = load_instincts(git_repo)[0]["id"]
    promote_instinct(git_repo, target)
    _write_store(git_repo / ".rig" / "instincts.jsonl",
                 [_raw_instinct(target, "a local record with the same id", 0.8)])

    with pytest.raises(ValueError):
        demote_instinct(git_repo, target)


def test_cli_reports_a_failed_demote_without_a_traceback(git_repo):
    r = run_cli(["instincts", "--demote", "in-nosuchthing"], git_repo)

    assert r.returncode != 0
    assert "[ERROR]" in r.stdout
    assert "Traceback" not in (r.stdout + r.stderr)


# ---- what --promote actually reports ----------------------------------------

def test_promote_says_so_when_the_budget_is_already_full(git_repo):
    """"Promoted" reads as "it will be injected now", and usually it will not — the
    budget is the binding constraint, not the tier."""
    _write_store(git_repo / ".rig" / "instincts.jsonl", [
        _raw_instinct("in-aaaaaaaaaa", "A" * 260, 0.95),
        _raw_instinct("in-bbbbbbbbbb", "B" * 240, 0.95),
        _raw_instinct("in-cccccccccc", "C" * 100, 0.9),
    ])

    r = run_cli(["instincts", "--promote", "in-cccccccccc"], git_repo)

    assert r.returncode == 0, r.stdout + r.stderr
    assert "does NOT fit here" in r.stdout
    assert "500 chars" in r.stdout


def test_promote_says_so_when_the_record_does_fit(git_repo):
    _write_store(git_repo / ".rig" / "instincts.jsonl",
                 [_raw_instinct("in-cccccccccc", "short enough", 0.9)])

    r = run_cli(["instincts", "--promote", "in-cccccccccc"], git_repo)

    assert "It fits the budget here" in r.stdout
    assert "does NOT fit" not in r.stdout


def test_asking_whether_it_fits_is_not_a_use(git_repo):
    """`injection_standing` must not bump hit_count or refresh last_seen — asking the
    question would otherwise push back the record's decay."""
    from rig_workbench.workbench.instincts import injection_standing

    add_instinct(git_repo, "a pattern", "e", None, 0.9)
    before = load_instincts(git_repo)[0]

    injection_standing(git_repo, before["id"])

    after = load_instincts(git_repo)[0]
    assert (after["hit_count"], after["last_seen"]) == (before["hit_count"], before["last_seen"])
