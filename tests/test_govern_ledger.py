"""The tamper-evident ledger, and the conformance report built on top of it.

v1's `.rig/audit.jsonl` could be edited with a text editor and nothing would
know. These tests are the difference: every way of quietly rewriting history —
editing an entry, deleting one, reordering them, appending an unsigned one — has
to show up in `verify`.
"""

import datetime
import json
from collections import Counter

import pytest

from rig_workbench.govern import conformance as conf
from rig_workbench.govern import ledger
from rig_workbench.workbench.reporting import read_all_tasks


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
