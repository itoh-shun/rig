"""The tamper-evident ledger, and the conformance report built on top of it.

v1's `.rig/audit.jsonl` could be edited with a text editor and nothing would
know. These tests are the difference: every way of quietly rewriting history —
editing an entry, deleting one, reordering them, appending an unsigned one — has
to show up in `verify`.
"""

import ast
import contextlib
import datetime
import hashlib
import hmac
import io
import json
import os
import pathlib
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from collections import Counter

import pytest

from rig_workbench.govern import conformance as conf
from rig_workbench.govern import ledger
from rig_workbench.workbench.reporting import read_all_tasks
from rig_workbench.workbench.state import load_or_create_provenance_key


def evaluate_project(root, **kw):
    """`conf.evaluate_project` with the run records wired in, the way the shell wires them.

    `conformance` scores run records and no longer reads them (`conformance.RunRecords`
    says why), so every caller supplies the reader: `govern/cli.py`, `evidence.py`, and
    this file. It is the same function on the same path the module used to call for
    itself, so what these tests exercise is unchanged.
    """
    return conf.evaluate_project(root, records=read_all_tasks(conf.runs_dir(root)), **kw)


def rollup(roots, **kw):
    """`conf.rollup` with the same reader, which is what it now asks its caller for."""
    return conf.rollup(roots, read_records=read_all_tasks, **kw)


def seed(tmp_path, n=3, key=True):
    if key:
        (tmp_path / ".rig").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".rig" / "provenance.key").write_bytes(b"k" * 32)
    for i in range(n):
        ledger.append(tmp_path, "accept", actor="alice", subject=f"task-{i}",
                      org="acme", team="team-a", data={"i": i})


def lines(tmp_path):
    return ledger.ledger_path(tmp_path).read_text(encoding="utf-8").splitlines()


def rewrite(tmp_path, entries):
    ledger.ledger_path(tmp_path).write_text(
        "\n".join(json.dumps(e, ensure_ascii=False, sort_keys=True) for e in entries) + "\n",
        encoding="utf-8")


# ── chain integrity ──────────────────────────────────────────────────────────
def test_a_fresh_ledger_verifies(tmp_path):
    seed(tmp_path)
    result = ledger.verify(tmp_path)
    assert result.ok and result.entries == 3 and result.signed == 3


def test_entries_chain_to_their_predecessor(tmp_path):
    seed(tmp_path)
    entries = ledger.read_ledger(tmp_path)
    assert entries[0]["prev"] == ledger.GENESIS
    assert entries[1]["prev"] == entries[0]["hash"]
    assert entries[2]["prev"] == entries[1]["hash"]


def test_editing_an_entry_is_detected(tmp_path):
    seed(tmp_path)
    entries = ledger.read_ledger(tmp_path)
    entries[1]["data"] = {"i": "tampered"}
    rewrite(tmp_path, entries)
    result = ledger.verify(tmp_path)
    assert not result.ok
    assert any("edited after the fact" in p for p in result.problems)


def test_deleting_an_entry_is_detected(tmp_path):
    seed(tmp_path)
    entries = ledger.read_ledger(tmp_path)
    del entries[1]
    rewrite(tmp_path, entries)
    result = ledger.verify(tmp_path)
    assert not result.ok
    assert any("the chain is cut here" in p for p in result.problems)
    assert any("removed or reordered" in p for p in result.problems)


def test_reordering_entries_is_detected(tmp_path):
    seed(tmp_path)
    entries = ledger.read_ledger(tmp_path)
    entries[0], entries[1] = entries[1], entries[0]
    rewrite(tmp_path, entries)
    assert not ledger.verify(tmp_path).ok


def test_an_appended_forgery_without_the_key_is_detected(tmp_path):
    """Someone who can write the file but not read `.rig/provenance.key` can build a
    correct hash chain — they cannot produce the signature."""
    seed(tmp_path)
    entries = ledger.read_ledger(tmp_path)
    forged = dict(entries[-1])
    forged.update({"seq": len(entries), "subject": "task-forged", "prev": entries[-1]["hash"],
                   "data": {}})
    forged.pop("sig")
    forged["hash"] = ledger.entry_hash(forged)
    rewrite(tmp_path, entries + [forged])
    result = ledger.verify(tmp_path)
    assert not result.ok
    assert any("unsigned" in p for p in result.problems)


def test_a_wrong_signature_is_detected(tmp_path):
    seed(tmp_path)
    entries = ledger.read_ledger(tmp_path)
    entries[2]["sig"] = "0" * 64
    rewrite(tmp_path, entries)
    assert any("signature does not verify" in p for p in ledger.verify(tmp_path).problems)


def test_a_malformed_line_is_reported(tmp_path):
    seed(tmp_path)
    with ledger.ledger_path(tmp_path).open("a", encoding="utf-8") as f:
        f.write("not json\n")
    assert any("not valid JSON" in p for p in ledger.verify(tmp_path).problems)


def test_a_ledger_without_a_key_still_chains(tmp_path):
    seed(tmp_path, key=False)
    result = ledger.verify(tmp_path)
    assert result.ok and result.signed == 0


def test_a_signed_ledger_whose_key_has_been_deleted_does_not_verify(tmp_path):
    """Deleting the key is cheaper than forging a signature, and it used to work.

    The chain is SHA-256 and needs no secret: an attacker rewrites the entries, recomputes
    every `hash`, and removes `.rig/provenance.key` so the signature pass never runs. The
    exact sequence below — two signed entries cut down to one, the chain recomputed by
    hand, the key deleted — returned `ok=True` and "ledger intact — 1 entries, unsigned".
    """
    seed(tmp_path, n=2)
    kept = [json.loads(line) for line in lines(tmp_path)][:1]
    kept[0]["subject"] = "task-rewritten"
    kept[0].pop("hash")
    kept[0]["hash"] = ledger.entry_hash(kept[0])
    rewrite(tmp_path, kept)
    ledger.key_path(tmp_path).unlink()

    result = ledger.verify(tmp_path)
    assert not result.ok
    assert any("signatures but" in problem and "absent" in problem for problem in result.problems)
    # And a repository that never signed anything is still intact, not broken by this.
    other = tmp_path / "unsigned"
    seed(other, n=2, key=False)
    assert ledger.verify(other).ok


def test_a_key_that_cannot_be_read_is_reported_instead_of_skipped(tmp_path):
    """The compensating check `_key`'s docstring names.

    `_key` swallows an `OSError` into `None`, and `verify` reads the same `None` as "this
    repository has no key" — so a key that is present and unreadable used to turn every
    signature check off and still answer "ledger intact ... unsigned". Here the key is a
    directory, which is the cheapest unreadable key there is.
    """
    seed(tmp_path, key=False)
    ledger.key_path(tmp_path).mkdir(parents=True, exist_ok=True)
    result = ledger.verify(tmp_path)
    assert not result.ok
    assert any("could not be read" in problem for problem in result.problems)
    assert "BROKEN" in result.summary()


def test_the_strict_secret_read_would_refuse_the_ledger_key_as_repositories_hold_it(tmp_path):
    """Why `_key` stays on `read_bytes`, measured rather than asserted in prose.

    `.rig/` is created by `mkdir(parents=True, exist_ok=True)` under the ambient umask and
    `load_or_create_provenance_key` chmods the key file only, so a real repository has a
    0600 key inside a 0755 directory. `read_secret_bytes` refuses that — it verifies the
    directory before it looks at the file — and `_key` would turn the refusal into `None`,
    leaving every ledger silently unsigned. Swapping the call is a migration (narrow the
    directory first), and this test is what fails if somebody swaps it instead.
    """
    from rig_workbench.ports.local import LOCAL_FILES

    (tmp_path / ".rig").mkdir(parents=True, exist_ok=True)
    key = ledger.key_path(tmp_path)
    key.write_bytes(b"k" * 32)
    key.chmod(0o600)
    (tmp_path / ".rig").chmod(0o755)

    with pytest.raises(OSError, match="mode 0700"):
        LOCAL_FILES.read_secret_bytes(key)
    # ...while the read the ledger actually makes works, and the chain is signed.
    seed(tmp_path, n=1, key=False)
    assert ledger.verify(tmp_path).signed == 1


def test_an_empty_ledger_verifies(tmp_path):
    assert ledger.verify(tmp_path).ok


# ── export ───────────────────────────────────────────────────────────────────
def test_export_jsonl_round_trips(tmp_path):
    seed(tmp_path)
    parsed = [json.loads(line) for line in ledger.export(tmp_path).splitlines()]
    assert [e["subject"] for e in parsed] == ["task-0", "task-1", "task-2"]


def test_export_csv_has_a_header_and_quotes_commas(tmp_path):
    seed(tmp_path, 1)
    ledger.append(tmp_path, "waiver.grant", actor="a,b", subject="w1")
    text = ledger.export(tmp_path, fmt="csv")
    assert text.splitlines()[0].startswith("seq,ts,actor,action")
    assert '"a,b"' in text


def test_export_can_filter_by_action(tmp_path):
    seed(tmp_path, 2)
    ledger.append(tmp_path, "waiver.grant", actor="alice", subject="w1")
    text = ledger.export(tmp_path, action="waiver.grant")
    assert len(text.splitlines()) == 1 and "w1" in text


def test_export_markdown_is_a_table(tmp_path):
    seed(tmp_path, 1)
    assert ledger.export(tmp_path, fmt="markdown").startswith("| seq |")


# ── conformance ──────────────────────────────────────────────────────────────
def govern_repo(tmp_path, **policy_overrides):
    (tmp_path / ".rig" / "policy").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".rig" / "org.json").write_text(json.dumps(
        {"schema": "rig.org/v2", "org": "acme", "team": "team-a",
         "policy_layers": [".rig/policy/org.json"]}), encoding="utf-8")
    doc = {"schema": "rig.policy/v2", "id": "acme", "scope": "org", "org": "acme",
           "roles": {"dev": ["accept", "approve"]}, "members": {"alice": ["dev"]}}
    doc.update(policy_overrides)
    (tmp_path / ".rig" / "policy" / "org.json").write_text(json.dumps(doc), encoding="utf-8")
    return tmp_path


def add_task(tmp_path, task_id, **fields):
    """A run directory holding a record every reader can use.

    `input` is here because conformance now reads runs through `read_all_tasks`
    (#493), whose `REQUIRED_FIELDS` is the one rule for a usable record. Without it these
    fixtures wrote a record no shipped run looks like, and the checks below would have
    measured an empty task list while still reporting PASS.
    """
    d = tmp_path / ".rig" / "runs" / task_id
    d.mkdir(parents=True, exist_ok=True)
    task = {"task_id": task_id, "task_type": "feature", "status": "accepted",
            "input": f"do {task_id}",
            "actor": "alice", "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "updated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    task.update(fields)
    (d / "task.json").write_text(json.dumps(task), encoding="utf-8")
    return d


def check(report, check_id):
    return next(c for c in report.checks if c.id == check_id)


def test_an_unbound_repository_fails_the_binding_check(tmp_path):
    report = evaluate_project(tmp_path)
    assert report.verdict == conf.FAIL
    assert check(report, "org_binding").verdict == conf.FAIL


def test_a_bound_repository_with_a_policy_passes_the_structural_checks(tmp_path):
    report = evaluate_project(govern_repo(tmp_path))
    assert check(report, "org_binding").verdict == conf.PASS
    assert check(report, "policy_layers").verdict == conf.PASS
    assert check(report, "rbac_roles").verdict == conf.PASS


def test_a_project_local_policy_with_no_org_layer_is_flagged(tmp_path):
    repo = govern_repo(tmp_path, scope="project")
    report = evaluate_project(repo)
    assert check(report, "policy_layers").verdict == conf.FAIL
    assert "no common bar" in check(report, "policy_layers").detail


def test_roles_with_no_members_fail(tmp_path):
    repo = govern_repo(tmp_path, members={})
    assert check(evaluate_project(repo), "rbac_roles").verdict == conf.FAIL


def test_a_forced_accept_shows_up_in_the_force_rate(tmp_path):
    repo = govern_repo(tmp_path)
    add_task(repo, "t1")
    add_task(repo, "t2", forced=True)
    result = check(evaluate_project(repo), "force_rate")
    assert result.verdict == conf.FAIL       # 50% — well past the 25% line
    assert "1/2" in result.detail


def test_a_clean_history_passes_the_force_rate(tmp_path):
    repo = govern_repo(tmp_path)
    add_task(repo, "t1")
    assert check(evaluate_project(repo), "force_rate").verdict == conf.PASS


def test_an_accepted_run_that_skipped_a_required_criterion_is_caught(tmp_path):
    repo = govern_repo(tmp_path, require_criteria={"feature": ["threat_model_reviewed"]})
    d = add_task(repo, "t1")
    (d / "acceptance.json").write_text(json.dumps(
        {"task_id": "t1", "presets": ["standard", "feature"],
         "checks": [{"name": "no_secret_leak", "status": "passed"}]}), encoding="utf-8")
    result = check(evaluate_project(repo), "required_criteria")
    assert result.verdict == conf.FAIL
    assert "threat_model_reviewed" in result.evidence[0]


def test_an_accepted_run_that_carried_the_criterion_passes(tmp_path):
    repo = govern_repo(tmp_path, require_criteria={"feature": ["threat_model_reviewed"]})
    d = add_task(repo, "t1")
    (d / "acceptance.json").write_text(json.dumps(
        {"task_id": "t1", "presets": ["standard", "feature"],
         "checks": [{"name": "threat_model_reviewed", "status": "passed"}]}), encoding="utf-8")
    assert check(evaluate_project(repo), "required_criteria").verdict == conf.PASS


def test_an_accepted_run_without_its_required_approvals_is_caught(tmp_path):
    repo = govern_repo(tmp_path, approvals={"feature": {"quorum": 2}})
    add_task(repo, "t1")
    result = check(evaluate_project(repo), "approvals")
    assert result.verdict == conf.FAIL and "0/2" in result.evidence[0]


def test_a_broken_ledger_fails_conformance(tmp_path):
    repo = govern_repo(tmp_path)
    seed(repo, 2)
    entries = ledger.read_ledger(repo)
    entries[0]["actor"] = "mallory"
    rewrite(repo, entries)
    assert check(evaluate_project(repo), "audit_ledger").verdict == conf.FAIL


def test_a_live_waiver_is_surfaced_as_a_warning(tmp_path):
    from rig_workbench.govern import waiver
    from rig_workbench.govern.policy import effective_policy

    repo = govern_repo(tmp_path, waivers={"max_days": 30})
    eff = effective_policy(repo)
    waiver.grant(repo, eff, waiver_id="w1", actor="alice", criteria=["tests_pass_or_explained"],
                 reason="flaky runner",
                 expires=(datetime.date.today() + datetime.timedelta(days=5)).isoformat())
    result = check(evaluate_project(repo), "waivers")
    assert result.verdict == conf.WARN and "w1" in result.evidence[0]


def test_legacy_access_json_is_flagged_as_a_second_source_of_truth(tmp_path):
    repo = govern_repo(tmp_path)
    (repo / ".rig" / "access.json").write_text(json.dumps({"default": ["alice"]}), encoding="utf-8")
    assert check(evaluate_project(repo), "legacy_access").verdict == conf.WARN


# ── rollup: the team A / team B / team C view ────────────────────────────────
def test_rollup_groups_projects_by_team(tmp_path):
    roots = []
    for team, project in (("team-a", "svc-1"), ("team-a", "svc-2"), ("team-b", "svc-3")):
        repo = tmp_path / project
        repo.mkdir()
        govern_repo(repo)
        binding = json.loads((repo / ".rig" / "org.json").read_text(encoding="utf-8"))
        binding["team"] = team
        (repo / ".rig" / "org.json").write_text(json.dumps(binding), encoding="utf-8")
        roots.append(repo)
    result = rollup(roots)
    assert sorted(result.teams) == ["team-a", "team-b"]
    assert len(result.teams["team-a"]) == 2
    md = result.markdown()
    assert "| team-a | 2 |" in md and "svc-3" in md
    assert 0.0 <= result.score <= 1.0


def test_rollup_json_carries_per_team_scores(tmp_path):
    repo = tmp_path / "svc"
    repo.mkdir()
    govern_repo(repo)
    payload = rollup([repo]).to_dict()
    assert payload["projects"] == 1
    assert "team-a" in payload["teams"]
    assert payload["teams"]["team-a"]["projects"] == 1


def test_a_project_whose_policy_does_not_load_scores_zero(tmp_path):
    """It stops after one or two checks, so scoring the fraction that ran would
    report a broken project as 100% — the most misleading number here."""
    repo = govern_repo(tmp_path)
    (repo / ".rig" / "policy" / "org.json").write_text("{ broken", encoding="utf-8")
    report = evaluate_project(repo)
    assert report.verdict == conf.FAIL
    assert report.score == 0.0
    assert report.findings == ["policy_error"]


def test_a_loosening_layer_shows_up_in_the_team_column(tmp_path):
    repo = tmp_path / "svc"
    repo.mkdir()
    govern_repo(repo, approvals={"feature": {"quorum": 2}})
    (repo / ".rig" / "policy" / "team.json").write_text(json.dumps(
        {"schema": "rig.policy/v2", "id": "team-a", "scope": "team", "org": "acme",
         "team": "team-a", "approvals": {"feature": {"quorum": 1}}}), encoding="utf-8")
    binding = json.loads((repo / ".rig" / "org.json").read_text(encoding="utf-8"))
    binding["policy_layers"].append(".rig/policy/team.json")
    (repo / ".rig" / "org.json").write_text(json.dumps(binding), encoding="utf-8")
    result = rollup([repo])
    assert result.to_dict()["teams"]["team-a"]["findings"] == ["policy_error"]
    assert "policy_error" in result.markdown()
    assert result.score == 0.0


def test_an_unbound_project_drags_the_rollup_down(tmp_path):
    good = tmp_path / "good"
    good.mkdir()
    govern_repo(good)
    bad = tmp_path / "bad"
    bad.mkdir()
    result = rollup([good, bad])
    assert any(r.verdict == conf.FAIL for r in result.reports)
    assert result.score < 1.0


# ── the bound on a run of identical events ───────────────────────────────────
def repeat(root, n, **overrides):
    event = {"actor": "eve", "subject": "rig-x",
             "data": {"reason": "approval requirement not met (0/1)"}}
    event.update(overrides)
    for _ in range(n):
        ledger.append(root, "accept_refused", **event)
    return ledger.ledger_path(root).read_text(encoding="utf-8").splitlines()


def test_a_thousand_identical_appends_are_bounded_to_four_lines(tmp_path):
    """Measured at 66bd4fe: 1000 identical appends wrote 369,890 bytes over 1000 lines in
    2.5s, and the time is quadratic because every append re-reads the file for `prev`. A
    caller who is being refused can loop, so the event is capped."""
    lines = repeat(tmp_path, 1000)
    assert len(lines) == ledger.REPEAT_CAP + 1
    assert ledger.ledger_path(tmp_path).stat().st_size < 2000
    assert json.loads(lines[-1])["collapsed"] == ledger.REPEAT_CAP + 1
    assert "collapsed" not in json.loads(lines[0])


def test_the_bounded_chain_still_verifies(tmp_path):
    (tmp_path / ".rig").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".rig" / "provenance.key").write_bytes(b"k" * 32)
    repeat(tmp_path, 50)
    result = ledger.verify(tmp_path)
    assert result.ok and result.entries == ledger.REPEAT_CAP + 1
    assert result.signed == ledger.REPEAT_CAP + 1


def test_a_suppressed_append_says_so_without_writing(tmp_path):
    """The candidate comes back, not the last entry on disk: a caller is told which event
    was refused and how much of it is on record."""
    repeat(tmp_path, ledger.REPEAT_CAP + 1)
    before = ledger.ledger_path(tmp_path).read_text(encoding="utf-8")
    entry = ledger.append(tmp_path, "accept_refused", actor="eve", subject="rig-x",
                          data={"reason": "approval requirement not met (0/1)"})
    assert entry["suppressed"] is True
    assert entry["collapsed"] == ledger.REPEAT_CAP + 1
    assert (entry["action"], entry["subject"]) == ("accept_refused", "rig-x")
    assert ledger.ledger_path(tmp_path).read_text(encoding="utf-8") == before


def test_both_listings_show_that_the_count_is_capped(tmp_path):
    """A capped run that reads as an ordinary tail is a count read as complete: measured,
    50 refused forces left four lines and `wb audit` printed `4 / 4 total` over them."""
    plain = ledger.collapsed_note({"action": "accept_refused"})
    capped = ledger.collapsed_note({"action": "accept_refused",
                                    "collapsed": ledger.REPEAT_CAP + 1})
    assert plain == ""
    assert "not recorded" in capped and str(ledger.REPEAT_CAP) in capped


def test_an_event_in_between_does_not_restart_the_count(tmp_path):
    """The cap counts every occurrence of an event, not a consecutive run of it. Counting
    the tail only bounded a loop of one event: alternating two, neither is ever at the tail
    twice — measured, 200 alternating calls wrote 200 lines, none collapsed."""
    repeat(tmp_path, 10)
    ledger.append(tmp_path, "accept", actor="alice", subject="rig-y", data={})
    lines = repeat(tmp_path, 10)
    assert len(lines) == (ledger.REPEAT_CAP + 1) + 1
    assert [json.loads(line)["seq"] for line in lines] == list(range(len(lines)))


def test_alternating_two_events_is_bounded_too(tmp_path):
    for _ in range(100):
        ledger.append(tmp_path, "accept_refused", actor="eve", subject="A", data={})
        ledger.append(tmp_path, "accept_refused", actor="eve", subject="B", data={})
    lines = ledger.ledger_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 * (ledger.REPEAT_CAP + 1)


def test_the_same_event_tomorrow_is_recorded_again(tmp_path):
    """The cap is per event per day. Without the date in the key it would silence an event
    that legitimately recurs next week; with it, a campaign that runs for days stays
    visible as days."""
    class _Day:
        def __init__(self, day): self.day = day
        def stamp(self): return f"2026-09-{self.day:02d}T10:00:00+00:00"

    for day in (12, 13):
        for _ in range(50):
            ledger.append(tmp_path, "accept_refused", actor="eve", subject="rig-x",
                          data={}, clock=_Day(day))
    lines = ledger.ledger_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 * (ledger.REPEAT_CAP + 1)


def test_events_that_differ_are_never_collapsed(tmp_path):
    for i in range(10):
        ledger.append(tmp_path, "accept_refused", actor="eve", subject=f"rig-{i}", data={})
    assert len(ledger.ledger_path(tmp_path).read_text(encoding="utf-8").splitlines()) == 10


def test_the_plain_audit_log_is_bounded_the_same_way_and_still_counts(tmp_path):
    """`.rig/audit.jsonl` has no chain to protect it and every reader of it counts lines:
    `workbench audit`, `stats`, `digest` and `cockpit` all go through `force_bypass_counter`.
    A collapsed line keeps the shape of the run it closes, so they count it unchanged."""
    from rig_workbench.workbench.reporting import force_bypass_counter
    from rig_workbench.workbench.state import _load_audit, audit_append, audit_path

    for i in range(1000):
        audit_append(tmp_path, {"ts": f"2026-09-12T10:00:{i % 60:02d}+00:00",
                                "action": "accept_force", "task_id": "rig-x",
                                "bypassed": ["no_unrelated_diff"]})
    events = _load_audit(tmp_path)
    assert len(events) == ledger.REPEAT_CAP + 1
    assert audit_path(tmp_path).stat().st_size < 1000
    assert events[-1]["collapsed"] == ledger.REPEAT_CAP + 1
    assert force_bypass_counter(events) == (4, Counter({"no_unrelated_diff": 4}))


# ── the audit log's cap decides that file and nothing else ───────────────────
def bound_repo(tmp_path):
    (tmp_path / ".rig").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".rig" / "org.json").write_text(
        json.dumps({"schema": "rig.org/v2", "org": "acme", "team": "team-a"}),
        encoding="utf-8")
    return {"action": "accept_force", "task_id": "rig-x", "bypassed": ["no_unrelated_diff"]}


def test_a_corrupt_audit_file_does_not_swallow_the_record(tmp_path):
    """The cap gave `audit_append` a read, and a read can fail where an append cannot. A
    `.rig/audit.jsonl` holding one 0xff byte raised `UnicodeDecodeError` out of `_load_audit`
    and straight out of `accept` — which by then has squashed and written `status: accepted`,
    so a forced bypass applied with no record in either file."""
    from rig_workbench.workbench.state import audit_append, audit_path

    event = bound_repo(tmp_path)
    audit_path(tmp_path).write_bytes(b"\xff\n")
    audit_append(tmp_path, {**event, "ts": "2026-09-12T10:00:00+00:00"})
    assert b'"action": "accept_force"' in audit_path(tmp_path).read_bytes()
    assert len(ledger.read_ledger(tmp_path)) == 1


def test_hand_written_audit_lines_cannot_suppress_the_chain(tmp_path):
    """`.rig/audit.jsonl` is unsigned and hand-writable — this change's own premise. It used
    to decide whether the chain recorded anything: four look-alike lines pasted into it
    suppressed a real `accept_force` from the ledger (measured, audit 4 → 4, ledger 0 → 0)."""
    from rig_workbench.workbench.state import _load_audit, audit_append, audit_path

    event = bound_repo(tmp_path)
    with audit_path(tmp_path).open("w", encoding="utf-8") as f:
        for i in range(ledger.REPEAT_CAP + 1):
            f.write(json.dumps({**event, "ts": f"2026-09-12T10:00:0{i}+00:00"},
                               sort_keys=True) + "\n")
    audit_append(tmp_path, {**event, "ts": "2026-09-12T10:00:09+00:00"})
    assert len(_load_audit(tmp_path)) == ledger.REPEAT_CAP + 1   # this file is capped
    assert len(ledger.read_ledger(tmp_path)) == 1                # the chain is not told


def test_the_chain_caps_against_its_own_record(tmp_path):
    from rig_workbench.workbench.state import audit_append

    event = bound_repo(tmp_path)
    for i in range(6):
        audit_append(tmp_path, {**event, "ts": f"2026-09-12T10:00:0{i}+00:00"})
    assert len(ledger.read_ledger(tmp_path)) == ledger.REPEAT_CAP + 1
    ledger.append(tmp_path, "policy.init", actor="olivia", subject="acme", data={})
    audit_append(tmp_path, {**event, "ts": "2026-09-12T10:00:09+00:00"})
    # An unrelated entry in between neither restarts the count nor lets one more through.
    assert len(ledger.read_ledger(tmp_path)) == ledger.REPEAT_CAP + 2


def test_a_capped_run_is_still_counted_as_the_events_it_stands_for(tmp_path):
    from rig_workbench.workbench.reporting import force_bypass_counter
    from rig_workbench.workbench.state import _load_audit, audit_append

    event = bound_repo(tmp_path)
    for i in range(1000):
        audit_append(tmp_path, {**event, "ts": f"2026-09-12T10:00:{i % 60:02d}+00:00"})
    events = _load_audit(tmp_path)
    assert len(events) == ledger.REPEAT_CAP + 1
    # The last line stands for the whole of what the cap let through, not for one event.
    assert force_bypass_counter(events) == (ledger.REPEAT_CAP + 1,
                                            Counter({"no_unrelated_diff": ledger.REPEAT_CAP + 1}))


def test_an_accepted_run_whose_approvals_the_chain_does_not_attest_is_an_offender(tmp_path):
    """Conformance re-runs the same arithmetic `accept` runs, so it has to reconcile too.
    Dropping `attested=` from its `evaluate` call changed nothing any other test noticed:
    the check already failed a run with too few decisions, and a *forged* decision is one it
    scored as satisfied — which is the audit reading the same file the forger wrote.

    Keyed, so the chain is in a state to attest; the decision names a qualified approver who
    is not the author, so nothing but the missing entry can be what refuses it.
    """
    repo = govern_repo(tmp_path, roles={"dev": ["accept"], "reviewer": ["approve"]},
                       members={"alice": ["dev"], "bob": ["reviewer"]},
                       approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}})
    (repo / ".rig").mkdir(parents=True, exist_ok=True)
    ledger.key_path(repo).write_bytes(b"k" * 32)
    run = add_task(repo, "t1")
    (run / "approvals.json").write_text(json.dumps(
        {"task_id": "t1", "decisions": [
            {"actor": "bob", "decision": "approve", "roles": ["reviewer"], "head": None,
             "branch_tip": None, "note": "never happened",
             "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}]}),
        encoding="utf-8")

    result = check(evaluate_project(repo), "approvals")
    assert result.verdict == conf.FAIL
    assert "t1" in result.evidence[0] and "0/1" in result.evidence[0]

    # The same decision, once the chain attests it, is clean — so the offence is the
    # missing entry and not the fixture.
    ledger.append(repo, "approval.grant", actor="bob", subject="t1", org="acme",
                  team="team-a",
                  data={"task_type": "feature", "note": "", "decision": "approve",
                        "head": None, "branch_tip": None})
    assert check(evaluate_project(repo), "approvals").verdict == conf.PASS


def test_a_hand_written_collapsed_count_cannot_inflate_the_force_rate(tmp_path):
    """`collapsed` comes back out of an unsigned file that anyone with the checkout can
    edit — the premise this whole change rests on. One line claiming a million would
    otherwise be a million forced accepts in `stats`, `digest` and `cockpit`."""
    from rig_workbench.workbench.reporting import force_bypass_counter
    from rig_workbench.workbench.state import _load_audit, audit_path

    audit_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    audit_path(tmp_path).write_text(json.dumps(
        {"ts": "2026-09-12T10:00:00+00:00", "action": "accept_force", "task_id": "rig-x",
         "bypassed": ["no_unrelated_diff"], "collapsed": 1000000}) + "\n", encoding="utf-8")
    counted, by_bypass = force_bypass_counter(_load_audit(tmp_path))
    assert counted == ledger.REPEAT_CAP + 1
    assert by_bypass["no_unrelated_diff"] == ledger.REPEAT_CAP + 1


# ── a key that is not a key ──────────────────────────────────────────────────
def test_a_zero_byte_key_is_refused_rather_than_signed_with(tmp_path):
    """HMAC accepts an empty secret, so `.rig/provenance.key` at zero bytes used to sign.

    Measured before the fix, on a repository whose key file was empty: `_key` returned
    `b""`, the appended entry carried a `sig`, `verify` answered `ok=True`, "ledger intact —
    1 entries, 1 signed", and that signature recomputed byte-for-byte under
    `hmac.new(b"", entry["hash"].encode("ascii"), sha256)` — a secret anybody can create
    with `touch`, reported as a signature.
    """
    (tmp_path / ".rig").mkdir(parents=True, exist_ok=True)
    ledger.key_path(tmp_path).write_bytes(b"")
    entry = ledger.append(tmp_path, "accept", actor="alice", subject="task-0")

    assert ledger._key(tmp_path) is None
    assert "sig" not in entry
    forged = hmac.new(b"", entry["hash"].encode("ascii"), hashlib.sha256).hexdigest()
    assert forged not in ledger.ledger_path(tmp_path).read_text(encoding="utf-8")

    result = ledger.verify(tmp_path)
    assert not result.ok and result.signed == 0
    # The same problem the unreadable key is reported with, and one problem, not two.
    assert [p for p in result.problems if "could not be read" in p] == result.problems
    assert "BROKEN" in result.summary()


def test_a_key_too_short_to_be_a_key_is_refused_the_same_way(tmp_path):
    """`key or None` was a length test of one: it passed anything non-empty, so
    `echo > .rig/provenance.key` — one newline — signed, `verify` answered `ok=True, signed=1`,
    and the security lane brute-forced that key in five guesses. `MIN_KEY_BYTES` is 128 bits,
    the conventional floor for an HMAC secret, and it refuses nothing rig writes: both
    writers generate `secrets.token_bytes(32)`.
    """
    (tmp_path / ".rig").mkdir(parents=True, exist_ok=True)
    ledger.key_path(tmp_path).write_bytes(b"\n")
    entry = ledger.append(tmp_path, "accept", actor="alice", subject="task-0")
    assert ledger._key(tmp_path) is None and "sig" not in entry
    result = ledger.verify(tmp_path)
    assert not result.ok and result.signed == 0
    assert [p for p in result.problems if "could not be read" in p] == result.problems

    # THE FLOOR ITSELF, AS A NUMBER AND NOT AS A REFERENCE TO ITSELF. Written relative to
    # the constant, these assertions moved with it: the test lane set `MIN_KEY_BYTES` to 15
    # and then to 2 and every suite still passed, so what was pinned was "not zero and not
    # one byte" and the 128-bit argument in the docstring was unverifiable from the tests.
    # Lowering the floor is a decision somebody has to come here and make.
    assert ledger.MIN_KEY_BYTES == 16          # 128 bits; see the constant for why
    assert ledger.usable_key(b"k" * 15) is None
    assert ledger.usable_key(b"k" * 16) == b"k" * 16


def test_an_empty_key_is_still_a_repository_that_signs(tmp_path):
    """`signs_here` asks about the path, not the bytes, and it must go on doing so: it is
    what `approval.ledger_attestations` reads to decide whether the chain is held to
    attest decisions. Answering "no key" for an empty one would hand that decision back to
    the looser reading exactly where the stricter one is called for."""
    (tmp_path / ".rig").mkdir(parents=True, exist_ok=True)
    ledger.key_path(tmp_path).write_bytes(b"")
    assert ledger.signs_here(tmp_path) is True


def test_a_real_key_still_signs_and_verifies(tmp_path):
    """The other half: refusing the empty file refuses nothing else."""
    seed(tmp_path, n=2)
    assert ledger.key_path(tmp_path).stat().st_size == 32
    result = ledger.verify(tmp_path)
    assert result.ok and result.signed == 2


def test_a_present_but_unusable_key_is_not_reported_as_an_absent_one(tmp_path):
    """Two different events, and the problem used to name the wrong one.

    Entries carrying `sig` with no readable key is the shape an attacker makes by rewriting
    the chain and deleting the key, and the problem says so — "the key was removed, or this
    is a checkout that never had it". A key that is *present* and below the floor reaches the
    same branch and is not that event at all; reading "absent" over a file that is sitting
    right there sends whoever is holding the incident somewhere else.
    """
    seed(tmp_path, n=1)                                   # signed, with a real key
    assert ledger.verify(tmp_path).signed == 1
    ledger.key_path(tmp_path).write_bytes(b"short")       # present, unusable

    problems = ledger.verify(tmp_path).problems
    assert any("is present and is not a usable key" in p for p in problems)
    assert not any("is absent" in p for p in problems)

    # …and the key actually gone still reads as gone, so this distinguishes the two rather
    # than replacing one wording with another.
    ledger.key_path(tmp_path).unlink()
    assert any("is absent" in p for p in ledger.verify(tmp_path).problems)


def _key_problem(root, shape):
    """A one-entry *signed* ledger, then `shape` at the key path; return the key problem.

    The ledger is signed first and the key replaced afterwards, because that is the state
    an operator is reading this problem in: entries that carry `sig` and a key path that no
    longer yields the secret they were signed with.
    """
    seed(root, n=1)
    assert ledger.verify(root).signed == 1
    ledger.key_path(root).unlink()
    shape(ledger.key_path(root))
    problems = [p for p in ledger.verify(root).problems
                if p.startswith(".rig/provenance.key exists")]
    assert len(problems) == 1, problems
    return problems[0]


def _short_file(p):
    p.write_bytes(b"12345678")


def _directory(p):
    p.mkdir()


def _denied_regular_file(monkeypatch):
    """A 32-byte key at the path that this process may not open.

    A suite running as root cannot be denied by `chmod`, so the denial is attached to the
    inode: this one file refuses to open, every other file in the tree reads normally.
    """
    def shape(p):
        p.write_bytes(bytes(range(32)))
        denied = p.stat().st_ino
        real = pathlib.Path.read_bytes

        def guarded(self):
            if self.stat().st_ino == denied:
                raise PermissionError(13, "Permission denied")
            return real(self)

        monkeypatch.setattr(pathlib.Path, "read_bytes", guarded)
    return shape


def test_the_unusable_key_problem_says_which_of_the_three_situations_it_is(tmp_path,
                                                                          monkeypatch):
    """What `govern audit verify` prints when the key path yields no key, per situation.

    One clause covered all of them — "it is unreadable, or shorter than the 16 bytes a
    signing key must have" — and measured on the previous shape the three problems below
    were byte-identical. Nothing asserted that text, which is how it survived the commit
    that split the same collapse on the signing side.

    They are three different events with three different next steps. The short file was
    measured: the bytes were counted, they are under `MIN_KEY_BYTES`, `usable_key` is the
    one rule every reader applies, and those entries are unverifiable for good. The regular
    file that would not open was measured by nobody — a whole 32-byte key behind a mode, an
    owner, or an untraversable directory arrives here with its bytes intact and verifies
    again the moment the permissions do — so the problem claims nothing and asks for the
    permissions. The directory was never read as a key at all, because both readers gate on
    `is_file`, so no signature in this ledger was made with what is sitting there. Its
    enumeration is open and identical to the signer's, because a FIFO, a device and a
    dangling symlink reach this branch too; while presence was `is_dir`, a directory was
    the only kind that could, and the line named it alone.

    The clause they share is the one that is true of all three: no signature was checked,
    and the hash chain still was.
    """
    short = _key_problem(tmp_path / "short", _short_file)
    assert short == (
        ".rig/provenance.key exists but could not be read as a key (it holds 8 byte(s), "
        "below the 16 bytes a signing key must have), so no signature was checked; the "
        "hash chain was still checked. There is nothing to repair on that file — every "
        "reader refuses it and entries signed with it can never be verified again. The "
        "next `accept` sets it aside under .rig/provenance.key.unusable, numbered past any "
        "already there, and then generates a key, or refuses without generating one if it "
        "cannot move it; this problem is then replaced by `signature does not verify` on "
        "every entry signed before it, or by `unsigned, but this repository has a "
        "provenance key` where there were none, and neither goes away")

    denied = _key_problem(tmp_path / "denied", _denied_regular_file(monkeypatch))
    assert denied == (
        ".rig/provenance.key exists but could not be read as a key (the permissions may "
        "not allow it), so no signature was checked; the hash chain was still checked. Fix "
        "the permissions on it and on the directories above it, then re-run; its contents "
        "are unread, so whether these entries still verify is unknown until something can "
        "read it")

    directory = _key_problem(tmp_path / "dir", _directory)
    assert directory == (
        ".rig/provenance.key exists but could not be read as a key (it is not a regular "
        "file, such as a FIFO, a directory, a device, or a symlink that resolves to "
        "nothing), so no signature was checked; the hash chain was still checked. Find "
        "out what is at the key path — no entry in this ledger was signed with it, "
        "because a path of that kind is never read as a key, so there is no key to "
        "recover. Identify it rather than opening it, because reading a FIFO blocks until "
        "something writes. The next `accept` sets it aside under "
        ".rig/provenance.key.unusable, numbered past any already there, and then generates "
        "a key, or refuses without generating one if it cannot move it; this problem is "
        "then replaced by `signature does not verify` on every entry signed before it, or "
        "by `unsigned, but this repository has a provenance key` where there were none, "
        "and neither goes away")

    three = (short, denied, directory)
    for problem in three:
        # The clause that holds for every shape reaching this path, and the collapsed one
        # that did not.
        assert "so no signature was checked; the hash chain was still checked" in problem
        assert "unreadable, or shorter than" not in problem
        # Every line opens with something to do, which is what an operator reads it for.
        # The length line used to end on what was measured and left "so what do I fix?"
        # answered only in a code comment.
        assert problem.split("hash chain was still checked. ", 1)[1].split()[0] in (
            "There", "Fix", "Find")

    # **How much they share, and not merely that they share the true clause.** The
    # operator prose says which parts to read to tell them apart, so what is identical has
    # to be pinned as identical and what distinguishes them as distinguishing. Asserting
    # containment alone let the prose claim the shared part was smaller than it is.
    stem = ".rig/provenance.key exists but could not be read as a key ("
    assert len(stem) == 59
    assert {p[:59] for p in three} == {stem}          # …and no more than that is shared
    assert len({p[59:59 + 20] for p in three}) == 3   # the parenthesis diverges at once
    remedies = {p.split("hash chain was still checked. ", 1)[1] for p in three}
    assert len(remedies) == 3
    # Only the measured one may say the records are gone; only the one `is_file` answered
    # `False` about may say nothing in the ledger was signed with what is there.
    assert [p for p in three if "can never be verified again" in p] == [short]
    assert [p for p in three if "no entry in this ledger was signed" in p] == [directory]


def test_a_key_this_process_cannot_stat_is_not_reported_as_a_non_regular_file(tmp_path):
    """The claim the directory branch makes needs a `stat` that answered, and here none did.

    A genuine 32-byte key, symlinked through a directory this process may not traverse.
    `Path.is_file()` swallows `ENOENT`, `ENOTDIR`, `EBADF` and `ELOOP` and re-raises the
    rest, so `EACCES` comes straight out of it — measured before this change, in the child
    below: `PermissionError: [Errno 13] Permission denied: .../.rig/provenance.key`, raised
    out of `verify`, which is a function whose whole contract is to report problems.

    Putting the `stat` inside the `try` is only half of it. Answering "not a regular file"
    for a `stat` that never answered would print "nothing in this ledger was signed with
    what is there" over a live key, telling the operator their signatures were never made
    rather than that they cannot be read — and the ledger *was* signed with that key, so
    fixing the mode on the directory makes every one of them verify. Unknown routes to the
    branch that claims nothing, and it does so by being the `else`.

    The denial has to be real rather than monkeypatched: `mode 0o000` does not stop root,
    so `verify` runs in a forked child that drops to an unprivileged uid first.
    """
    import contextlib
    import io
    import os
    import select
    import shutil
    import signal
    import tempfile

    nobody = 65534
    root = pathlib.Path(tempfile.mkdtemp())       # not tmp_path: the whole chain has to be
    vault = root / "vault"                        # traversable by the unprivileged child
    try:
        os.chmod(root, 0o755)
        seed(root, n=1)
        assert ledger.verify(root).signed == 1
        os.chmod(root / ".rig", 0o755)
        os.chmod(ledger.ledger_path(root), 0o644)
        ledger.key_path(root).unlink()
        vault.mkdir()
        (vault / "real.key").write_bytes(b"k" * 32)       # the key it was signed with
        ledger.key_path(root).symlink_to(vault / "real.key")
        os.chmod(vault, 0o000)

        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:                                              # pragma: no cover
            try:
                os.close(read_fd)
                if os.getuid() == 0:
                    os.setgid(nobody)
                    os.setuid(nobody)
                with contextlib.redirect_stdout(io.StringIO()):
                    result = ledger.verify(root)
                os.write(write_fd, ("ok=%s\n" % result.ok
                                    + "\n".join(result.problems)).encode())
            except BaseException as exc:                          # noqa: BLE001
                os.write(write_fd, f"raised={type(exc).__name__}: {exc}".encode())
            finally:
                os._exit(0)
        os.close(write_fd)
        # Bounded: an unbounded `read` on a child that never writes turns a failure into a
        # hung suite, and the repository sets no global test timeout to catch it.
        answer = ""
        if select.select([read_fd], [], [], 60)[0]:
            answer = os.read(read_fd, 65536).decode()
        else:
            os.kill(pid, signal.SIGKILL)
        os.close(read_fd)
        os.waitpid(pid, 0)
        assert answer, "the child produced nothing within 60s"

        first, _, printed = answer.partition("\n")
        assert first == "ok=False", answer        # reported, not raised, and not "intact"
        assert "could not be read as a key (the permissions may not allow it)" in printed
        assert "no entry in this ledger was signed" not in printed
        assert "not a regular file" not in printed
        # …and the key really is one, so the claim withheld above is the claim that would
        # have been false.
        os.chmod(vault, 0o700)
        assert (vault / "real.key").read_bytes() == b"k" * 32
        assert ledger.usable_key((vault / "real.key").read_bytes()) is not None
    finally:
        os.chmod(vault, 0o700)
        shutil.rmtree(root, ignore_errors=True)


def test_both_readers_of_the_key_path_delegate_to_the_one_observation(tmp_path,
                                                                      monkeypatch):
    """`_key` here and `state._observe_key_file` are `observe_key_file`, and stay that way.

    The signer's warning and the ledger's problem have to tell the same three situations
    apart, and they were two implementations: the warning was split first and this problem
    went on collapsing two of the three, which is the drift a second implementation is.

    **Agreement over a handful of shapes is not delegation** — a faithful reimplementation
    agrees everywhere it is asked, which is how the two sides agreed right up until one of
    them was edited. The sentinel below is bytes that are on no disk here, so only a real
    call to the observer can return them. The shapes are still driven, because delegating
    to something that answers wrongly is no better.
    """
    from rig_workbench.workbench import state

    for name, shape in (("short", _short_file), ("dir", _directory), ("absent", None)):
        root = tmp_path / name
        (root / ".rig").mkdir(parents=True)
        if shape is not None:
            shape(ledger.key_path(root))
        assert ledger._key(root) == ledger.usable_key(
            ledger.observe_key_file(ledger.key_path(root))[0])
        assert state._observe_key_file(ledger.key_path(root)) == \
            ledger.observe_key_file(ledger.key_path(root))

    assert ledger.observe_key_file(ledger.key_path(tmp_path / "short"))[1] == "regular"
    assert ledger.observe_key_file(ledger.key_path(tmp_path / "dir"))[1] == "other"
    assert ledger.observe_key_file(ledger.key_path(tmp_path / "absent"))[1] == "other"

    sentinel = b"sentinel-bytes-no-file-here"
    monkeypatch.setattr(ledger, "observe_key_file", lambda p, **kw: (sentinel, "regular"))
    assert ledger._key(tmp_path / "absent") == sentinel
    assert state._observe_key_file(ledger.key_path(tmp_path / "absent")) \
        == (sentinel, "regular")
    # The third reader. `state.provenance_key` is what `verify-provenance` calls, and it
    # was a fourth hand-written copy of the same read until this change — the one a review
    # caught the prose claiming had already been unified. It goes through the observer too,
    # so "every read of this path is one function" is a statement about all of them.
    assert state.provenance_key(tmp_path / "absent") == sentinel

    # …and the kind is the ledger's type by reference, not a second `str` beside it. The
    # identity check alone is vacuous while both are `str` — it passes for a copy — so the
    # binding itself is what is asserted: one assignment, and its value is a plain name.
    assert state._KeyFileKind is ledger.KeyFileKind
    tree = ast.parse(pathlib.Path(state.__file__).read_text(encoding="utf-8"))
    bound = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
             and any(getattr(t, "id", None) == "_KeyFileKind" for t in node.targets)]
    assert len(bound) == 1 and isinstance(bound[0].value, ast.Name), \
        "_KeyFileKind must alias ledger.KeyFileKind, not restate it"
    from_ledger = {(a.asname or a.name) for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom)
                   and (node.module or "").endswith("govern.ledger")
                   for a in node.names}
    assert bound[0].value.id in from_ledger, \
        f"_KeyFileKind is bound to {bound[0].value.id!r}, not to a name imported from the ledger"

    # That import is `state`'s one module-level reach into `govern`, and what makes it safe
    # is a property of the ledger's import closure, not of the line: no `workbench` module
    # is in it, so it cannot close a cycle. Measured in a fresh interpreter, because this
    # one has the whole tree loaded and would agree with anything.
    probe = subprocess.run(
        [sys.executable, "-c", "import rig_workbench.govern.ledger, sys; "
         "print(' '.join(sorted(m for m in sys.modules if m.startswith('rig_workbench'))))"],
        capture_output=True, text=True, cwd=pathlib.Path(__file__).resolve().parents[1])
    assert probe.returncode == 0, probe.stderr
    pulled = probe.stdout.split()
    assert pulled, probe.stdout
    assert not [m for m in pulled if m.startswith("rig_workbench.workbench")], pulled
    # …and it is not the small closure a comment here once claimed, which is why the
    # no-cycle argument has to rest on the line above rather than on "only the ports".
    assert len(pulled) > 3, pulled


def test_the_presence_question_answers_rather_than_raising_when_it_cannot_look(tmp_path,
                                                                               monkeypatch):
    """`key_path_present` is guarded for the reason the observation's own `stat` is, and it
    takes a refusal the other way from the helper it replaces.

    `verify` asks it to tell "present and unusable" from "gone". `Path.is_dir()`, which
    used to answer that, re-raises `EACCES` exactly as `is_file` does, and `_is_dir`'s
    guard spent the refusal on `False` — the answer that says the key is gone. The port
    answers `"unknown"`, the caller reads `!= "absent"`, and so a `stat` that never settled
    lands on *something is there*, which is the only one of the two readings that can be
    established.

    **The mutation this pins**, run rather than named: flipping `key_path_present` to
    `files.presence(p) == "present"` — the one-word edit that spends the refusal the old
    way — turns the assertion below back into `is absent, so no signature could be checked
    (the key was removed, ...)` over a FIFO whose `lstat` was refused, and this test fails
    on it.
    """
    from rig_workbench.ports.local import LOCAL_FILES

    seed(tmp_path, n=1)
    assert ledger.verify(tmp_path).signed == 1
    ledger.key_path(tmp_path).unlink()
    os.mkfifo(ledger.key_path(tmp_path))              # `is_file` False, `is_dir` False
    denied = ledger.key_path(tmp_path)

    # The bare `pathlib` probes this replaced, on the path this test then refuses: both of
    # them re-raise, which is what the port must not do one call further in.
    real_is_dir, real_is_symlink = pathlib.Path.is_dir, pathlib.Path.is_symlink

    def refusing(real):
        def probe(self):
            if self == denied:
                raise PermissionError(13, "Permission denied")
            return real(self)
        return probe

    monkeypatch.setattr(pathlib.Path, "is_dir", refusing(real_is_dir))
    monkeypatch.setattr(pathlib.Path, "is_symlink", refusing(real_is_symlink))
    real_lstat = os.lstat
    monkeypatch.setattr(os, "lstat", lambda path, *a, **k: (
        (_ for _ in ()).throw(PermissionError(13, "Permission denied"))
        if pathlib.Path(path) == denied else real_lstat(path, *a, **k)))

    with pytest.raises(PermissionError):
        denied.is_dir()
    with pytest.raises(PermissionError):
        denied.is_symlink()
    assert LOCAL_FILES.presence(denied) == "unknown"          # answered, not raised
    assert ledger.key_path_present(denied) is True            # …and unknown counts as there

    result = ledger.verify(tmp_path)                          # reported, not raised
    assert not result.ok
    assert not any("is absent" in p for p in result.problems)
    assert any(p.startswith(".rig/provenance.key exists but could not be read as a key")
               for p in result.problems)


def test_presence_is_the_one_question_and_both_sides_ask_it_through_the_port(tmp_path,
                                                                             monkeypatch):
    """`state._key_path_present` is `ledger.key_path_present` is `FileStore.presence`.

    Agreement over a handful of shapes is not delegation — the two sides agreed on regular
    files and on directories right up until one of them was edited, which is the whole
    defect. So the answer injected at the port below is one no filesystem produces for the
    file that is really there, and both callers have to come back carrying it. A reader
    that reached round the port answers from the file and fails.
    """
    from rig_workbench.ports import local as ports_local
    from rig_workbench.workbench import state

    p = tmp_path / ".rig" / "provenance.key"
    p.parent.mkdir(parents=True)
    assert ledger.key_path_present(p) is state._key_path_present(p) is False
    p.write_bytes(b"k" * 32)
    assert ledger.key_path_present(p) is state._key_path_present(p) is True

    for answer, expected in (("absent", False), ("unknown", True), ("present", True)):
        monkeypatch.setattr(ports_local.LocalFileStore, "presence",
                            lambda self, path, _a=answer: _a)
        assert ledger.key_path_present(p) is expected, answer
        assert state._key_path_present(p) is expected, answer
        monkeypatch.undo()

    # …and an injected store reaches the ledger side without the local adapter at all.
    class OnlyUnknown:
        def presence(self, path):
            return "unknown"

    assert ledger.key_path_present(tmp_path / "nowhere", files=OnlyUnknown()) is True


def test_asking_whether_this_repository_signs_answers_rather_than_raising(tmp_path,
                                                                          monkeypatch):
    """`signs_here` is the last bare `is_file` on this path, and it is on a decision path.

    `approval.ledger_attestations` asks it before deciding whether to hold decisions to the
    chain, and `Path.is_file()` re-raises `EACCES`, so a key behind a directory this process
    may not traverse came back as `PermissionError` out of an approval check — a crash where
    a verdict was wanted.

    **A refusal answers `True`, and it is the same direction `key_path_present` takes.**
    Both are asked whether to read a `stat` that never answered as *nothing is there*, and
    both refuse to: this one because answering "no key" hands the decision back to the
    looser reading exactly where the stricter one is called for, and that one because
    "gone" is the half an operator acts on. (The helper this sentence used to contrast
    with, `_is_dir`, took a refusal the other way and has been removed with the reading it
    was spent on.) So where nothing can be established this repository signs, and the chain
    stays required.
    """
    (tmp_path / ".rig").mkdir(parents=True)
    ledger.key_path(tmp_path).write_bytes(b"k" * 32)
    assert ledger.signs_here(tmp_path) is True
    denied = ledger.key_path(tmp_path)
    real = pathlib.Path.is_file

    def refuses(self):
        if self == denied:
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(pathlib.Path, "is_file", refuses)
    with pytest.raises(PermissionError):
        denied.is_file()                       # the bare call, which is what it used to be
    assert ledger.signs_here(tmp_path) is True            # …and the stricter reading holds
    monkeypatch.undo()

    # The other half: a key that is genuinely gone still reads as gone, so the guard has
    # not turned this into a function that always says yes.
    ledger.key_path(tmp_path).unlink()
    assert ledger.signs_here(tmp_path) is False


def test_a_refused_key_check_leaves_the_chain_enforced_at_the_decision(tmp_path,
                                                                       monkeypatch):
    """The decision `signs_here` exists for, not just the function that answers it.

    `approval.ledger_attestations` turns it into `enforced` — whether a decision the chain
    does not attest is refused — and that is the thing a provoker would want off. Pinning
    the helper says the crash is gone; pinning this says enforcement did not go with it.

    The shape is a repository that signs, whose key is behind something this process may
    not traverse, and an empty ledger — so `any("sig" in e)` cannot rescue the answer and
    `signs_here` is the only thing holding `enforced` up.
    """
    from rig_workbench.govern import approval

    (tmp_path / ".rig").mkdir(parents=True)
    ledger.key_path(tmp_path).write_bytes(b"k" * 32)
    assert approval.ledger_attestations(tmp_path).enforced is True     # the ordinary state

    denied = ledger.key_path(tmp_path)
    real = pathlib.Path.is_file

    def refuses(self):
        if self == denied:
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(pathlib.Path, "is_file", refuses)
    assert approval.ledger_attestations(tmp_path).entries == ()
    assert approval.ledger_attestations(tmp_path).enforced is True     # …and still enforced
    monkeypatch.undo()

    # And it is `signs_here` doing it, not `chain_required`: the same call on a repository
    # with no key at all is not enforced, which is what the refusal must not be read as.
    ledger.key_path(tmp_path).unlink()
    assert approval.ledger_attestations(tmp_path).enforced is False


def _key_problem_of(result):
    found = [p for p in result.problems if p.startswith(".rig/provenance.key exists")]
    assert len(found) == 1, result.problems
    return found[0]


def test_the_remedies_promise_only_what_the_next_accept_does(tmp_path, monkeypatch):
    """Drive the fixture, run the loader, and assert what `verify` reports afterwards.

    **What this test measures is what those remedies are allowed to say.** Asserting the
    text of a remedy pins its spelling and nothing about the world, and two promises got
    through that way: a set-aside filename that is not knowable from where the sentence is
    written, and "this problem stops once it has", which is true and is the wrong half.

    Measured here, on a two-entry signed ledger: after the loader runs, the key problem is
    gone and both entries report `signature does not verify` — permanently, since the bytes
    that signed them are in the set-aside file and nothing reads it. That is the most
    alarming line this tool prints. An agent quoting "the problem stops" tells somebody to
    run `accept` and it clears up; what they get is a ledger that reads as tampered with.

    And the name: allocation is highest-already-there plus one, so a repository that has
    set a key aside once gets `provenance.key.unusable-2`. The signer can name the file
    because it interpolates after its own `rename`; `verify` has renamed nothing and a
    concurrent `accept` can take the next name first, so it names the shape.
    """
    for name, shape in (("short", _short_file), ("dir", _directory)):
        root = tmp_path / name
        seed(root, n=2)
        assert ledger.verify(root).signed == 2
        ledger.key_path(root).unlink()
        shape(ledger.key_path(root))

        promised = _key_problem_of(ledger.verify(root))
        # The two clauses this test is the evidence for. Rewriting either to promise more
        # than what is measured below fails here.
        assert "numbered past any already there" in promised
        assert ("this problem is then replaced by `signature does not verify` on every "
                "entry signed before it, or by `unsigned, but this repository has a "
                "provenance key` where there were none, and neither goes away") in promised
        assert "this problem stops once it has" not in promised

        with contextlib.redirect_stdout(io.StringIO()):
            assert len(load_or_create_provenance_key(root)) == 32

        after = ledger.verify(root)
        assert not any(p.startswith(".rig/provenance.key exists") for p in after.problems)
        assert after.signed == 0 and not after.ok
        assert [p for p in after.problems if "signature does not verify" in p] == [
            "entry #0: signature does not verify", "entry #1: signature does not verify"]
        # …and it stays that way: the key that signed them is in the set-aside file and
        # nothing reads it, so re-running changes nothing.
        assert ledger.verify(root).problems == after.problems
        aside = sorted(x.name for x in (root / ".rig").glob("provenance.key.unusable*"))
        assert aside == ["provenance.key.unusable"]

    # **The aftermath's second shape, driven and not just spelled.** The clause naming it
    # was added from a measurement that lived in a comment: every ledger above carries
    # signatures, so the only occurrence of this wording anywhere was inside an expected
    # literal, and rewording the line the code really prints was caught by nothing here.
    # That is the same defect as the two this test exists for, one clause later.
    root = tmp_path / "unsigned"
    (root / ".rig").mkdir(parents=True)
    ledger.key_path(root).write_bytes(b"12345678")            # short from the start, so
    ledger.append(root, "accept", actor="alice", subject="task-0")   # nothing gets signed
    ledger.append(root, "accept", actor="alice", subject="task-1")
    assert [("sig" in e) for e in ledger.read_ledger(root)] == [False, False]
    promised = _key_problem_of(ledger.verify(root))
    assert ("or by `unsigned, but this repository has a provenance key` where there were "
            "none") in promised
    with contextlib.redirect_stdout(io.StringIO()):
        load_or_create_provenance_key(root)
    after = ledger.verify(root)
    assert after.problems == ["entry #0: unsigned, but this repository has a provenance key",
                              "entry #1: unsigned, but this repository has a provenance key"]
    assert not after.ok and after.signed == 0
    assert ledger.verify(root).problems == after.problems     # "and neither goes away"
    # …and the clause the constant names for signed ledgers does not fire on this shape,
    # which is why it is named as a second thing rather than folded into the first.
    assert not any("signature does not verify" in p for p in after.problems)

    # **The name the remedy may not print.** One aside already there, and the file lands on
    # the next number — so a sentence naming `.rig/provenance.key.unusable` outright is
    # wrong for exactly the operator who has been here before.
    root = tmp_path / "again"
    seed(root, n=1)
    ledger.key_path(root).unlink()
    (root / ".rig" / "provenance.key.unusable").write_bytes(b"an earlier one")
    _short_file(ledger.key_path(root))
    with contextlib.redirect_stdout(io.StringIO()):
        load_or_create_provenance_key(root)
    assert sorted(x.name for x in (root / ".rig").glob("provenance.key.unusable*")) == [
        "provenance.key.unusable", "provenance.key.unusable-2"]

    # The third remedy is the one that promises the entries come back, and it does not go
    # through `accept` at all: read the file and they verify again, which is why that
    # branch must never borrow either clause above.
    root = tmp_path / "denied"
    seed(root, n=2)                                  # …and the key it signed with stays
    hidden = ledger.key_path(root).stat().st_ino     # put behind a denial, not replaced
    real = pathlib.Path.read_bytes

    def guarded(self):
        if self.stat().st_ino == hidden:
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", guarded)
    denied = _key_problem_of(ledger.verify(root))
    assert "Fix the permissions on it and on the directories above it, then re-run" in denied
    assert "signature does not verify`" not in denied and "numbered past" not in denied
    monkeypatch.undo()                               # …and the operator fixes them
    restored = ledger.verify(root)
    assert restored.ok and restored.signed == 2, restored.problems


#: The kinds a key path can hold, beyond a key. `_character_device` needs `mknod`, which an
#: unprivileged suite does not have; it is dropped there rather than faked, because a symlink
#: to `/dev/null` is a *symlink* and would test the shape below it twice.
def _character_device(p):
    os.mknod(p, 0o600 | stat.S_IFCHR, os.makedev(1, 3))


def _socket(p):
    """A unix socket at the key path — bound short, then renamed onto it.

    A reviewer found this shape reaching the not-a-regular-file branch while appearing in
    no enumeration and no fixture; the enumeration stays open (`such as`) and this is the
    fixture.

    **Short, because `AF_UNIX` caps the bind path at about 108 bytes** and a `tmp_path` key
    path measures 97 on this suite — close enough that binding in place would start failing
    on a machine whose temp root is a few characters longer, which is a shape that stops
    being tested without saying so. `rename` moves the inode like any other, so the socket
    lands at the full path whatever its length.

    **And under the same root the target is, because `rename` does not cross filesystems.**
    `mkdtemp()` with no `dir` follows `TMPDIR`, which is where pytest derives `tmp_path`
    from, so the two are on one filesystem by construction rather than by luck; a literal
    `/tmp` here would not move with `TMPDIR` and would raise `EXDEV` on a runner that sets
    it. `--basetemp` can still put them apart, so the rename falls back to binding in
    place — which works whenever the path fits, and that is the ordinary case.
    """
    short = pathlib.Path(tempfile.mkdtemp()) / "s"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.bind(str(short))
        try:
            os.rename(short, p)
        except OSError:                     # EXDEV: a basetemp on another filesystem
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.bind(str(p))
    finally:
        shutil.rmtree(short.parent, ignore_errors=True)


def _symlink_loop(p):
    p.symlink_to(p)


def _nothing(p):
    return None


def _key_shapes():
    shapes = [("absent", _nothing), ("short", _short_file), ("directory", _directory),
              ("fifo", _fifo), ("dangling", _dangling_symlink), ("loop", _symlink_loop),
              ("socket", _socket)]
    probe = pathlib.Path(tempfile.mkdtemp()) / "dev"
    try:
        _character_device(probe)
    except OSError:
        return shapes
    finally:
        shutil.rmtree(probe.parent, ignore_errors=True)
    return shapes + [("device", _character_device)]


def test_a_non_directory_above_the_key_path_is_absence_and_not_a_kind(tmp_path):
    """`ENOTDIR` at the caller, where the port-level table only reaches the adapter.

    It is one of the two errnos `presence` reads as a settled absence, and the argument for
    it is that the lookup answered: something that is not a directory is in the way, so
    nothing can be at the path either way. This is what that costs or saves where an
    operator sees it.

    A `.rig` that is a regular file is the reachable shape — the key path and the ledger
    path share that one component, so there is no ledger to read either, and the honest
    verdict is a repository with nothing in it rather than one with a broken key.

    **The mutation this pins**, run: dropping `NotADirectoryError` from the adapter's
    absent clause sends `ENOTDIR` to the fall-through, `key_present` becomes true, and
    `verify` turns `ledger intact — 0 entries, unsigned` into `ledger BROKEN` with `it is
    not a regular file, such as a FIFO, …` asserted over a path that cannot hold anything
    at all — a kind claimed about a lookup that did not fail, which is the branch that is
    least entitled to guess.
    """
    from rig_workbench.ports.local import LOCAL_FILES

    root = tmp_path / "repo"
    root.mkdir()
    (root / ".rig").write_text("not a directory", encoding="utf-8")

    # The caller first, so that is what fails under the mutation above: the port-level
    # table already has this errno, and what this test is for is where an operator meets it.
    result = ledger.verify(root)
    assert result.summary() == "ledger intact — 0 entries, unsigned"
    assert result.problems == []
    assert result.ok and result.entries == 0

    assert LOCAL_FILES.presence(ledger.key_path(root)) == "absent"
    assert ledger.key_path_present(ledger.key_path(root)) is False


def test_a_sibling_linking_a_key_does_not_get_a_kind_asserted_over_it(tmp_path):
    """The order of `verify`'s two looks at the path, pinned by the interleaving it decides.

    They are two calls, so a sibling between them is reported from a state that never
    existed. The one that matters is an ordinary concurrent `accept`: it sets an unusable
    file aside and links a fresh key, and asked *after* the observation the presence
    question then answered `present` about the new key while `key_kind` still said `other`
    about the FIFO — so `verify` printed `it is not a regular file … no entry in this
    ledger was signed with it` over a live 32-byte key. That is the one claim
    `KeyFileKind`'s note says must never be made on an unestablished reading, and the
    branch making it is the only one entitled to claim a kind at all.

    Asked first, presence answers `absent` before the sibling runs, the observation then
    reads the key that really is there, and there is no key problem to get wrong.

    **The mutation this pins**, run: moving `key_path_present` back below
    `observe_key_file` fails this test with the kind line asserted over the live key.

    **What it does not close**, and the `verify` comment says so: a key *removed* between
    the two calls still reaches that branch. Nothing here does that — the loader returns on
    a usable key and only renames what it read as unusable — so it wants a hand `rm`.
    """
    seed(tmp_path, n=1)
    assert ledger.verify(tmp_path).signed == 1
    key_file = ledger.key_path(tmp_path)
    signed_with = key_file.read_bytes()
    key_file.unlink()
    os.mkfifo(key_file)                       # what the sibling `accept` is about to move

    class LinksAKeyMidLook:
        """`LOCAL_FILES`, with the sibling's rename-and-link inside the presence call."""

        def __init__(self) -> None:
            self.fired = False

        def __getattr__(self, name):
            from rig_workbench.ports.local import LOCAL_FILES
            return getattr(LOCAL_FILES, name)

        def presence(self, path):
            from rig_workbench.ports.local import LOCAL_FILES
            if pathlib.Path(path) == key_file and not self.fired:
                self.fired = True
                answer = LOCAL_FILES.presence(path)
                key_file.unlink()             # the set-aside's rename …
                key_file.write_bytes(signed_with)          # … and the link after it
                return answer
            return LOCAL_FILES.presence(path)

    files = LinksAKeyMidLook()
    result = ledger.verify(tmp_path, files=files)
    assert files.fired, "the sibling never ran, so this asserts nothing"
    assert ledger.usable_key(key_file.read_bytes()) is not None   # …a live key is there
    assert not [p for p in result.problems if "no entry in this ledger was signed" in p], (
        "a kind was asserted over a key this process never established the kind of")
    assert result.ok, result.problems


def test_the_two_commands_do_not_contradict_each_other_about_the_key_path(tmp_path, capsys):
    """Every shape, driven through `govern audit verify` and through `accept`'s key loader.

    The two do not print the same words — one sets the file aside and the other only reports
    — but they must not say different things about whether anything is *there*. They did.
    Measured on this tree at 2ced317, on a one-entry signed ledger, per shape:

    * FIFO, character device, dangling symlink, self-referential symlink, unix socket —
      `verify` printed `.rig/provenance.key is absent, so no signature could be checked
      (the key was removed, or this is a checkout that never had it)` while the loader on
      the same repository set that same path aside as *not a regular file*. Five shapes,
      one contradiction each, and "removed" sends an operator looking for a backup of a key
      that was never there.
    * A directory and a short regular file agreed already, because `is_dir` and `is_file`
      are the two questions that used to decide presence.

    After: all seven (or eight, where `mknod` is permitted) agree. The loop below asserts
    the equivalence in both directions, so a later widening of one side alone fails here
    even if every message is still well-formed on its own.

    **The socket is here because a reviewer found it with no fixture and no enumeration.**
    The printed line's list is open — `such as` — and it always covered this shape; what
    was missing was anything driving it, so it was added rather than written down.

    **The mutation this pins**, run: dropping `key_path_present` back to `key_kind !=
    "other" or _is_dir(...)` — i.e. presence decided by `is_dir` again — fails this test on
    the FIFO with `verify said gone and the signer set it aside`.
    """
    for name, shape in _key_shapes():
        reporting, signing = tmp_path / f"{name}-verify", tmp_path / f"{name}-accept"
        for root in (reporting, signing):
            seed(root, n=1)
            assert ledger.verify(root).signed == 1
            ledger.key_path(root).unlink()
            shape(ledger.key_path(root))

        problems = ledger.verify(reporting).problems
        gone = [p for p in problems if "is absent, so no signature could be checked" in p]
        there = [p for p in problems
                 if p.startswith(".rig/provenance.key exists but could not be read as a key")]

        capsys.readouterr()
        load_or_create_provenance_key(signing)
        warned = [ln for ln in capsys.readouterr().out.splitlines()
                  if ln.startswith("[WARN]") and "It has been moved to" in ln]

        assert not (gone and warned), f"{name}: verify said gone and the signer set it aside"
        assert bool(there) == bool(warned), (
            f"{name}: verify {'did' if there else 'did not'} report something at the path "
            f"and the signer {'did' if warned else 'did not'}")
        assert bool(gone) != bool(there), f"{name}: {problems}"
        if name == "absent":
            assert gone and not warned
        else:
            assert there and len(warned) == 1, (name, problems, warned)

        # **And where both speak, the enumeration is one string, not two that agree today.**
        # This is the branch whose wording claimed a kind, and it claimed the wrong one for
        # four of these shapes for as long as only a directory could reach it.
        kinds = ("it is not a regular file, such as a FIFO, a directory, a device, or a "
                 "symlink that resolves to nothing")
        if name not in ("absent", "short"):
            assert kinds in there[0], (name, there[0])
            assert kinds in warned[0], (name, warned[0])


def _fifo(p):
    os.mkfifo(p)


def _dangling_symlink(p):
    p.symlink_to(p.parent / "nothing-is-here")


def test_the_operator_prose_quotes_the_problems_this_code_actually_reports(tmp_path,
                                                                           monkeypatch):
    """The facet's fenced block, compared against the three problems `verify` emits.

    This is the gap the collapsed clause came through on the signing side: the message and
    the prose quoting it were tied by nothing but somebody re-reading both, so a wrong tail
    was wrong in two places and corrected in prose rather than in code. The same tie is put
    on this side at the same time as the split, so anyone editing either has to edit the
    other. `verify` prints the path relative, so there is nothing to substitute — the
    comparison is byte for byte.

    **What is still held by a reader and not by this file**, in two classes, because naming
    only the first is how the second got through twice.

    *A sentence that means its opposite.* Seven are load-bearing, reversible by a one-word
    edit, and would stay green — written down because five rounds of review found the
    unheld half every time, and an unwritten list is the one nobody checks. The guard below
    keys on remedies, on `accept`, on filenames and on deletion, and none of these is any
    of those. **One came off rather than on:** the count of kinds is derived from the printed
    line at the end of this test now — every `N 種` in the passage, the bullet's and the
    paragraph's alike, since the digit appears in both places and the first form of that
    check only read bullets — so reversing the bullet to "only a directory" takes its digit
    with it and fails there; what is left of it on this list is the clause saying the list
    of kinds is open, which no count can hold.

    * `本物の鍵が無傷で残っている場合がある` on the permissions bullet. Reversed, it is
      permission to delete a live key.
    * `読めるようになるまで、署名の正否を決めない` on the same bullet. Reversed, it is
      permission to call a ledger tampered with on the strength of a failed `open`.
    * `消えたことを直ったと読まない` on the length bullet. Reversed, it is a false all-clear
      over a state that reads as tampering, and unlike the entry below nothing near it
      contradicts the reversal.
    * `直すものは無い`, also on the length bullet — the weakest of the seven, since the
      sentence after it says the entries are gone for good either way.
    * `これが全部ではない` on the kind bullet. Dropped, four reads as the whole set, and an
      operator meeting a shape that is not one of the four — a unix socket is the one
      nobody has written down — reads the line as not applying to them. The count beside it
      is held below; this clause is what says the count is not a census.
    * `種類を `stat` できなかった場合もここに出る` on the permissions bullet. Reversed, a
      refused `stat` routes to the kind bullet, which is the one branch entitled to say
      nothing in the ledger was signed with what is there — said over a live key behind a
      mode, which is what `KeyFileKind` exists to forbid.
    * `unknown になり、「ある」側として扱う` in the paragraph under the bullets — the same
      claim as the entry above, in the second of the two places a reader has to check, and
      counted separately for exactly that reason. Reversed there, the prose says a refused
      `stat` means the key is gone while the code reports it present.

    Three of the seven are on the permissions bullet on purpose: it is the branch with the
    least machine coverage, because it is the only one whose remedy names no `accept`, no
    file and no deletion, so the derived rules below have nothing of its own to key on.

    *An instruction no printed line contains.* A bullet can also add a step rather than
    invert a claim — "delete the key and run `accept`", "remove the key file and re-run" —
    and a reviewer reached the same incident twice that way, through a different token each
    time. The two derived rules below close the routes that were demonstrated, by refusing
    a bullet any `accept`, any filename and any deletion its own line does not carry. They
    are not a proof that no third route exists.

    Neither class is reachable by a fixture: the table and the derived rules hold *which
    branch may say what*, not whether a sentence means what it says.
    """
    facet = (pathlib.Path(__file__).resolve().parents[1] / "skills" / "engine" / "facets"
             / "instructions" / "workbench-ops.md")
    lines = facet.read_text(encoding="utf-8").splitlines()
    anchors = [i for i, ln in enumerate(lines)
               if ln.startswith(".rig/provenance.key exists but could not be read as a key")]
    assert anchors, "the quoted `govern audit verify` key problems should be in the facet"
    top = max(i for i in range(anchors[0]) if lines[i].startswith("```"))
    bottom = min(i for i in range(anchors[0], len(lines)) if lines[i].startswith("```"))
    quoted = lines[top + 1:bottom]

    reported = [_key_problem(tmp_path / name, shape)
                for name, shape in (("short", _short_file),
                                    ("denied", _denied_regular_file(monkeypatch)),
                                    ("dir", _directory))]
    assert quoted == reported

    # **The bullets under the block, and not only the block.** Each one opens with the
    # fragment that identifies its branch, and that is the whole of the reader's index from
    # a line on their screen to the paragraph telling them what to do. Tying the block and
    # leaving the bullets loose is how three statements about which branch means what got
    # into the prose in the first place: the block was right and the sentences under it
    # were not. `N` stands in for the byte count the facet cannot know, so it matches a
    # number; everything else in the fragment is literal.
    bullets = [ln for ln in lines[bottom + 1:bottom + 40] if ln.startswith("- `")]
    assert len(bullets) == 3, bullets
    for index, (bullet, line) in enumerate(zip(bullets, reported)):
        fragment = bullet.split("`")[1]
        pattern = "".join(r"\d+" if part == "N" else re.escape(part)
                          for part in re.split(r"\b(N)\b", fragment))
        assert re.search(pattern, line), (
            f"bullet {index} opens with {fragment!r}, which is not in the line it explains")
        others = [other for position, other in enumerate(reported) if position != index]
        assert not any(re.search(pattern, other) for other in others), (
            f"bullet {index}'s {fragment!r} also matches another branch, so it indexes nothing")

    # **The fragment indexes the bullet; it does not hold its body.** Demonstrated by a
    # reviewer, who rewrote a body to say the opposite of its own branch, left the leading
    # fragment alone, and this file stayed green. Arbitrary prose cannot be asserted from
    # here, but the claims that get an operator into an incident can be.
    #
    # Each of these belongs to exactly one branch, and putting it on another is the whole
    # of the attack: it is what "your records are gone" or "nothing was signed with it"
    # under the `the permissions` heading would be, said of a file whose contents are
    # unread. So each is required where it is earned **and forbidden everywhere else** —
    # the two halves are one table, because a guard written only as "present on these two"
    # leaves the third bullet free to say anything that is not those markers, which is the
    # gap the same reviewer walked through twice.
    # **Stems, not whole phrases, and only the forbidding half needs them.** A *required*
    # marker that somebody rewords fails loudly on the line below — the rewording is the
    # thing being caught, not a way past it. It is the forbidding half that a rewording
    # slips: `検証できない` to `検証できません` is one character, and the whole phrase went
    # on to a bullet that must not carry it while the assertion looked away. So each entry
    # is the part that cannot be inflected away.
    #
    # **Stems stop inflection; they do not stop synonymy.** A reviewer put a synonym for one
    # branch's verdict on another bullet and it went straight past this table, because no
    # substring of the original is in it. That is not closed here and no list closes it —
    # it is why the rules below are derived from what the code prints instead.
    owned = {
        0: ("signature does not verify", ".unusable-2", "検証できな", "accept` 側の警告"),
        1: ("測れていない",),
        2: ("signature does not verify", ".unusable-2", "復旧できる鍵",
            "署名されたエントリは無", "accept` 側の警告"),
    }
    for index, markers in owned.items():
        for marker in markers:
            assert marker in bullets[index], (index, marker, bullets[index])
    for index, bullet in enumerate(bullets):
        for other, markers in owned.items():
            if other == index:
                continue
            for marker in markers:
                if marker in set(owned[index]):
                    continue          # genuinely shared by two branches, e.g. the aftermath
                assert marker not in bullet, (
                    f"bullet {index} carries {marker!r}, which belongs to branch {other}")

    # **And a rule derived from the lines rather than listed here**, because an enumeration
    # only catches what somebody thought of. Three injections passed the list above: an
    # inflected form (the stems close that), the aftermath restated in Japanese with the
    # English marker dropped, and — the one that matters — a bullet telling the operator to
    # delete the key and run `accept`, discarding the signatures, on the branch whose file
    # may be an intact key. That is the instruction this whole series exists to prevent,
    # and it used none of the listed phrases.
    #
    # What separates the branches is already in the three strings: two of the printed lines
    # send the operator to `accept` and name the set-aside file, and one names neither,
    # because its remedy is to read the file rather than replace it. So a bullet may name
    # either only if its own line does. That shrinks the table instead of growing it, and
    # it cannot be got round by rewording, because the gate is the code's own output.
    for token in ("accept", "provenance.key.unusable"):
        for index, (bullet, line) in enumerate(zip(bullets, reported)):
            if token in line:
                continue
            assert token not in bullet, (
                f"bullet {index} names {token!r} while the line it explains does not — that "
                "branch's remedy is to read the file, not to replace it")

    # **And the same shape again, for the instruction itself rather than its object.** The
    # rule above keys on `accept` and on the filename, so "remove the key file and re-run"
    # walked past it — a second bullet reaching the same incident by naming neither. It is
    # the same class as the first injection and it got through in two separate rounds,
    # which is what an enumeration of phrases is always going to do.
    #
    # The derivation is the same and needs nothing new: **not one of the three lines this
    # code prints tells the operator to delete anything.** The length line says there is
    # nothing to repair, the permissions line says to fix permissions and re-run, the
    # directory line says to find out what put it there. So no bullet may say it either,
    # and the premise is asserted rather than assumed — if a remedy ever does say it, the
    # gate opens for that branch and for no other.
    deletions = ("delete", "remove", "消して", "消す", "削除", "unlink", "rm ")
    for token in deletions:
        assert not any(token in line for line in reported), (
            f"a printed line now says {token!r}; this rule has to be re-derived, not deleted")
        for index, bullet in enumerate(bullets):
            assert token not in bullet, (
                f"bullet {index} tells the operator to {token!r}, which no line this code "
                "prints ever does — on the permissions branch that file may be a whole key")

    # **The number in the prose is measured, not typed.** "the first 59 characters are the
    # same" is a second constant beside the one in the branch test, and a second constant
    # drifts silently. It is read back out of the sentence and compared with the lines.
    shared = os.path.commonprefix(reported)
    claimed = [int(n) for ln in lines[bottom + 1:bottom + 40] if "文字が同じ" in ln
               for n in re.findall(r"(\d+) 文字が同じ", ln)]
    assert claimed == [len(shared)], (claimed, len(shared), shared)

    # **And the same shape for the kind count, which rots in the direction the docstring's
    # list does not cover.** That list enrols the sentence against *reversal* — somebody
    # writing "only a directory" back into it — and reversal is not the only way it goes
    # wrong: the code gaining a fifth kind updates the fenced block, because the block is
    # tied byte for byte above, while the prose goes on saying four and nothing notices.
    # So the number is derived from the line it explains rather than typed beside it: the
    # reason string enumerates after `such as`, one item per comma, and the prose has to
    # agree. This takes that half of the sentence off the unheld list; what stays unheld is
    # the clause saying the enumeration is open, which no count can hold.
    #
    # **Every `N 種` in the passage, and not only the ones on a bullet.** The first form of
    # this read `bullets`, which filters on `- ` plus a backtick, and the paragraph under
    # them opens with the same digit — so an identical untethered number sat one line below
    # the derivation, where a reader fixing the bullet has no reason to look. Two reviewers
    # found it separately, which is what an enumerated window earns. The passage is bounded
    # by the next heading rather than by a line count, so a mention added anywhere in it is
    # enrolled by being written, and `(?!類)` keeps a `3 種類` elsewhere out of it.
    #
    # No `== 4` anywhere below: a literal here would be the second constant the paragraph
    # above warns about, one line later. The only check on the split is that it split.
    heading = next(i for i in range(bottom + 1, len(lines)) if lines[i].startswith("###"))
    passage = lines[bottom + 1:heading]
    kinds = reported[2].split("such as ", 1)[1].split("), so no signature")[0].split(", ")
    assert len(kinds) > 1 and all(kind.strip() for kind in kinds), kinds
    counted = [(offset, int(n)) for offset, ln in enumerate(passage)
               for n in re.findall(r"(\d+) 種(?!類)", ln)]
    assert {n for _, n in counted} == {len(kinds)}, (counted, kinds)
    # …and the **kind** bullet still has to carry one, or reversing *that* sentence would
    # leave the paragraph's digit agreeing with the code by itself and this check would
    # pass over the exact prose the run exists to kill. A widened window that stops failing
    # is worse than the literal it replaced.
    #
    # **`bullets[2]`, and the index is a name here rather than a position.** The first form
    # of this asked whether *any* bullet carried a digit, which a reviewer broke by
    # reversing the kind bullet and planting `4 種` on the permissions bullet: green, with
    # the sentence gone. Index 2 is safe because of the loop above, not because the order
    # happens to hold — that loop zips the bullets against `reported` and requires each
    # bullet's leading fragment to match its own line **and no other**, so `bullets[2]` is
    # "the bullet that identifies `reported[2]`", which is the line `kinds` is read out of
    # two statements up. The `len(bullets) == 3` beside it only makes the zip total; on its
    # own it would allow any ordering, and that is the assertion this one leans on.
    assert re.findall(r"(\d+) 種(?!類)", bullets[2]), (
        "the kind bullet no longer states how many kinds the line it explains names")


def test_the_conformance_report_says_the_approver_names_it_counted_are_claims(tmp_path):
    """The report is the number that gets quoted upward, so it has to say what it knows.

    An approver's name here is whatever the approving command was run under — nothing
    authenticates it — and a `✓ approvals` line that says only "satisfied" reads as "the
    right people approved". Counted and reported, not scored: every approval in every
    repository is on a self-asserted name, so failing on it would fail everyone.
    """
    repo = govern_repo(tmp_path, roles={"dev": ["accept"], "reviewer": ["approve"]},
                       members={"alice": ["dev"], "bob": ["reviewer"]},
                       approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}})
    run = add_task(repo, "t1")
    (run / "approvals.json").write_text(json.dumps(
        {"task_id": "t1", "decisions": [
            {"actor": "bob", "decision": "approve", "roles": ["reviewer"], "head": None,
             "branch_tip": None, "note": "read it",
             "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}]}),
        encoding="utf-8")

    result = check(evaluate_project(repo), "approvals")
    assert result.verdict == conf.PASS
    assert "1 accepted run(s) in the window satisfied it" in result.detail
    assert "all 1 counted approval(s) are on self-asserted names" in result.detail


def test_a_forged_assertion_does_not_talk_the_clause_out_of_the_conformance_report(tmp_path):
    """The second reader of the mark, and it has to refuse the same input the first does.

    The clause counts every counted approval; filtering it on what the decision claims about
    itself would let the file the report is scoring decide the report, and a mutant that put
    that filter back survived every suite because no fixture carried a forged value. This one
    does: `"actor_assertion": "authenticated"`, hand-written into `approvals.json`.
    """
    repo = govern_repo(tmp_path, roles={"dev": ["accept"], "reviewer": ["approve"]},
                       members={"alice": ["dev"], "bob": ["reviewer"]},
                       approvals={"feature": {"quorum": 1, "roles": ["reviewer"]}})
    run = add_task(repo, "t1")
    (run / "approvals.json").write_text(json.dumps(
        {"task_id": "t1", "decisions": [
            {"actor": "bob", "decision": "approve", "roles": ["reviewer"], "head": None,
             "branch_tip": None, "note": "read it", "actor_assertion": "authenticated",
             "actor_source": "corporate sso",
             "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}]}),
        encoding="utf-8")

    result = check(evaluate_project(repo), "approvals")
    assert result.verdict == conf.PASS
    assert "all 1 counted approval(s) are on self-asserted names" in result.detail


def test_the_count_in_that_clause_is_the_number_of_approvals_not_a_constant(tmp_path):
    """Every fixture above counts one approval, so "all N" never pinned N: a clause hard-
    coded to 1 would pass them all. Two counted approvals on one run, and the line says two.
    """
    repo = govern_repo(tmp_path, roles={"dev": ["accept"], "reviewer": ["approve"]},
                       members={"alice": ["dev"], "bob": ["reviewer"], "carol": ["reviewer"]},
                       approvals={"feature": {"quorum": 2, "roles": ["reviewer"]}})
    run = add_task(repo, "t1")
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    (run / "approvals.json").write_text(json.dumps(
        {"task_id": "t1", "decisions": [
            {"actor": who, "decision": "approve", "roles": ["reviewer"], "head": None,
             "branch_tip": None, "note": "read it", "ts": now}
            for who in ("bob", "carol")]}), encoding="utf-8")

    result = check(evaluate_project(repo), "approvals")
    assert result.verdict == conf.PASS
    assert "all 2 counted approval(s) are on self-asserted names" in result.detail


def test_a_failing_approvals_check_says_it_too(tmp_path):
    """The FAIL branch carries the same clause and needs its own case: a mutant that dropped
    it there survived every test, because every fixture that reached the clause passed.

    One counted approval against a quorum of two — an offender *and* a counted name, which is
    the only shape that reaches both halves of the line at once.
    """
    repo = govern_repo(tmp_path, roles={"dev": ["accept"], "reviewer": ["approve"]},
                       members={"alice": ["dev"], "bob": ["reviewer"], "carol": ["reviewer"]},
                       approvals={"feature": {"quorum": 2, "roles": ["reviewer"]}})
    run = add_task(repo, "t1")
    (run / "approvals.json").write_text(json.dumps(
        {"task_id": "t1", "decisions": [
            {"actor": "bob", "decision": "approve", "roles": ["reviewer"], "head": None,
             "branch_tip": None, "note": "read it",
             "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}]}),
        encoding="utf-8")

    result = check(evaluate_project(repo), "approvals")
    assert result.verdict == conf.FAIL
    assert "1 of 1 accepted run(s) were applied without their approvals" in result.detail
    assert "all 1 counted approval(s) are on self-asserted names" in result.detail
    assert "t1" in result.evidence[0] and "1/2" in result.evidence[0]
