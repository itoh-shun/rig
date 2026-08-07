"""The tamper-evident ledger, and the conformance report built on top of it.

v1's `.rig/audit.jsonl` could be edited with a text editor and nothing would
know. These tests are the difference: every way of quietly rewriting history —
editing an entry, deleting one, reordering them, appending an unsigned one — has
to show up in `verify`.
"""

import datetime
import json

from rig_workbench.govern import conformance as conf
from rig_workbench.govern import ledger


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
    d = tmp_path / ".rig" / "runs" / task_id
    d.mkdir(parents=True, exist_ok=True)
    task = {"task_id": task_id, "task_type": "feature", "status": "accepted",
            "actor": "alice", "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "updated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    task.update(fields)
    (d / "task.json").write_text(json.dumps(task), encoding="utf-8")
    return d


def check(report, check_id):
    return next(c for c in report.checks if c.id == check_id)


def test_an_unbound_repository_fails_the_binding_check(tmp_path):
    report = conf.evaluate_project(tmp_path)
    assert report.verdict == conf.FAIL
    assert check(report, "org_binding").verdict == conf.FAIL


def test_a_bound_repository_with_a_policy_passes_the_structural_checks(tmp_path):
    report = conf.evaluate_project(govern_repo(tmp_path))
    assert check(report, "org_binding").verdict == conf.PASS
    assert check(report, "policy_layers").verdict == conf.PASS
    assert check(report, "rbac_roles").verdict == conf.PASS


def test_a_project_local_policy_with_no_org_layer_is_flagged(tmp_path):
    repo = govern_repo(tmp_path, scope="project")
    report = conf.evaluate_project(repo)
    assert check(report, "policy_layers").verdict == conf.FAIL
    assert "no common bar" in check(report, "policy_layers").detail


def test_roles_with_no_members_fail(tmp_path):
    repo = govern_repo(tmp_path, members={})
    assert check(conf.evaluate_project(repo), "rbac_roles").verdict == conf.FAIL


def test_a_forced_accept_shows_up_in_the_force_rate(tmp_path):
    repo = govern_repo(tmp_path)
    add_task(repo, "t1")
    add_task(repo, "t2", forced=True)
    result = check(conf.evaluate_project(repo), "force_rate")
    assert result.verdict == conf.FAIL       # 50% — well past the 25% line
    assert "1/2" in result.detail


def test_a_clean_history_passes_the_force_rate(tmp_path):
    repo = govern_repo(tmp_path)
    add_task(repo, "t1")
    assert check(conf.evaluate_project(repo), "force_rate").verdict == conf.PASS


def test_an_accepted_run_that_skipped_a_required_criterion_is_caught(tmp_path):
    repo = govern_repo(tmp_path, require_criteria={"feature": ["threat_model_reviewed"]})
    d = add_task(repo, "t1")
    (d / "acceptance.json").write_text(json.dumps(
        {"task_id": "t1", "presets": ["standard", "feature"],
         "checks": [{"name": "no_secret_leak", "status": "passed"}]}), encoding="utf-8")
    result = check(conf.evaluate_project(repo), "required_criteria")
    assert result.verdict == conf.FAIL
    assert "threat_model_reviewed" in result.evidence[0]


def test_an_accepted_run_that_carried_the_criterion_passes(tmp_path):
    repo = govern_repo(tmp_path, require_criteria={"feature": ["threat_model_reviewed"]})
    d = add_task(repo, "t1")
    (d / "acceptance.json").write_text(json.dumps(
        {"task_id": "t1", "presets": ["standard", "feature"],
         "checks": [{"name": "threat_model_reviewed", "status": "passed"}]}), encoding="utf-8")
    assert check(conf.evaluate_project(repo), "required_criteria").verdict == conf.PASS


def test_an_accepted_run_without_its_required_approvals_is_caught(tmp_path):
    repo = govern_repo(tmp_path, approvals={"feature": {"quorum": 2}})
    add_task(repo, "t1")
    result = check(conf.evaluate_project(repo), "approvals")
    assert result.verdict == conf.FAIL and "0/2" in result.evidence[0]


def test_a_broken_ledger_fails_conformance(tmp_path):
    repo = govern_repo(tmp_path)
    seed(repo, 2)
    entries = ledger.read_ledger(repo)
    entries[0]["actor"] = "mallory"
    rewrite(repo, entries)
    assert check(conf.evaluate_project(repo), "audit_ledger").verdict == conf.FAIL


def test_a_live_waiver_is_surfaced_as_a_warning(tmp_path):
    from rig_workbench.govern import waiver
    from rig_workbench.govern.policy import effective_policy

    repo = govern_repo(tmp_path, waivers={"max_days": 30})
    eff = effective_policy(repo)
    waiver.grant(repo, eff, waiver_id="w1", actor="alice", criteria=["tests_pass_or_explained"],
                 reason="flaky runner",
                 expires=(datetime.date.today() + datetime.timedelta(days=5)).isoformat())
    result = check(conf.evaluate_project(repo), "waivers")
    assert result.verdict == conf.WARN and "w1" in result.evidence[0]


def test_legacy_access_json_is_flagged_as_a_second_source_of_truth(tmp_path):
    repo = govern_repo(tmp_path)
    (repo / ".rig" / "access.json").write_text(json.dumps({"default": ["alice"]}), encoding="utf-8")
    assert check(conf.evaluate_project(repo), "legacy_access").verdict == conf.WARN


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
    result = conf.rollup(roots)
    assert sorted(result.teams) == ["team-a", "team-b"]
    assert len(result.teams["team-a"]) == 2
    md = result.markdown()
    assert "| team-a | 2 |" in md and "svc-3" in md
    assert 0.0 <= result.score <= 1.0


def test_rollup_json_carries_per_team_scores(tmp_path):
    repo = tmp_path / "svc"
    repo.mkdir()
    govern_repo(repo)
    payload = conf.rollup([repo]).to_dict()
    assert payload["projects"] == 1
    assert "team-a" in payload["teams"]
    assert payload["teams"]["team-a"]["projects"] == 1


def test_a_project_whose_policy_does_not_load_scores_zero(tmp_path):
    """It stops after one or two checks, so scoring the fraction that ran would
    report a broken project as 100% — the most misleading number here."""
    repo = govern_repo(tmp_path)
    (repo / ".rig" / "policy" / "org.json").write_text("{ broken", encoding="utf-8")
    report = conf.evaluate_project(repo)
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
    result = conf.rollup([repo])
    assert result.to_dict()["teams"]["team-a"]["findings"] == ["policy_error"]
    assert "policy_error" in result.markdown()
    assert result.score == 0.0


def test_an_unbound_project_drags_the_rollup_down(tmp_path):
    good = tmp_path / "good"
    good.mkdir()
    govern_repo(good)
    bad = tmp_path / "bad"
    bad.mkdir()
    result = conf.rollup([good, bad])
    assert any(r.verdict == conf.FAIL for r in result.reports)
    assert result.score < 1.0
