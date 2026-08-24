"""What an assurance target is allowed to decide elsewhere (#479).

Two paths, and both are mostly refusals: a target names outcomes, a workflow names steps, and
the claim that *this step reaches that outcome* is a policy this module is written not to make.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench import assurance_target, assurance_wiring
from rig_workbench.workbench.assurance_wiring import (ABSENT, INVALID, UNREADABLE_FILE,
                                                      floor_from, load_requires, projection,
                                                      unreachable)
from rig_workbench.workbench.synthesis import OPERATOR_REQUESTED, POLICY_REQUIRED, Required

CATALOG = frozenset({"acceptance", "implement", "review-diff", "sign"})


def _target(**axes) -> dict:
    return {"schema": assurance_target.SCHEMA, "axes": axes or {"gate": "passed"}}


def _step(step_id="acceptance", source=POLICY_REQUIRED, reason="the gate records it"):
    return {"id": step_id, "source": source, "reason": reason}


def _requires(payload=None):
    return load_requires(payload if payload is not None
                         else {"gate": {"passed": [_step()]}})


# ── the mapping is declared, not inferred ───────────────────────────────────────
def test_a_target_becomes_the_steps_somebody_declared_reach_it():
    built = floor_from(_target(gate="passed"), _requires(), CATALOG)
    assert built == (Required(id="acceptance", source=POLICY_REQUIRED,
                              reason="the gate records it"),)


def test_an_axis_nobody_mapped_is_refused_rather_than_skipped():
    """The whole failure this module could produce is a workflow that looks like it satisfies
    a target while nothing in it does. Silence about an axis means nobody wrote down which
    step reaches it — not that no step is needed."""
    with pytest.raises(ValueError) as raised:
        floor_from(_target(gate="passed", provenance="signed-and-verified"), _requires(),
                   CATALOG)
    assert "provenance: signed-and-verified" in str(raised.value)
    assert "below the floor" in str(raised.value)


def test_a_value_does_not_inherit_the_steps_declared_for_another():
    """`gate: skipped` is not a weaker `gate: passed`. A mapping keyed on the axis alone would
    hand a target asking for one the steps declared for the other."""
    with pytest.raises(ValueError) as raised:
        floor_from(_target(gate="skipped"), _requires(), CATALOG)
    assert "gate: skipped" in str(raised.value)


def test_an_empty_declaration_is_a_declaration_and_is_kept():
    """"Reaching this needs no step of its own" is a thing a policy can truthfully say, and
    somebody wrote it. Absence is the shape that means nobody has decided."""
    built = floor_from(_target(gate="passed"), _requires({"gate": {"passed": []}}), CATALOG)
    assert built == ()


def test_two_axes_disagreeing_about_one_step_are_refused():
    """`check_floor` refuses that rather than picking by order, and resolving it here would
    only move the same collision earlier."""
    requires = _requires({
        "gate": {"passed": [_step("acceptance", POLICY_REQUIRED, "policy says so")]},
        "isolation": {"git-worktree": [_step("acceptance", OPERATOR_REQUESTED, "I asked")]}})
    with pytest.raises(ValueError) as raised:
        floor_from(_target(gate="passed", isolation="git-worktree"), requires, CATALOG)
    assert "One authority per step" in str(raised.value)


def test_two_axes_agreeing_about_one_step_are_not():
    """The same step required identically by two axes is one floor entry, not a collision."""
    requires = _requires({
        "gate": {"passed": [_step("acceptance")]},
        "isolation": {"git-worktree": [_step("acceptance")]}})
    assert len(floor_from(_target(gate="passed", isolation="git-worktree"),
                          requires, CATALOG)) == 1


def test_a_step_nobody_registered_is_refused():
    """A floor may not add to the catalog either — `check_floor`'s rule, reached through here
    rather than reimplemented."""
    with pytest.raises(ValueError) as raised:
        floor_from(_target(gate="passed"),
                   _requires({"gate": {"passed": [_step("invent-a-step")]}}), CATALOG)
    assert "nobody registered" in str(raised.value)


def test_an_invalid_target_raises_rather_than_returning_a_floor():
    """A caller that got a floor back would put it under `synthesise` and read
    `floor_held: true`, so a refusal must not look like a floor."""
    with pytest.raises(ValueError) as raised:
        floor_from({"schema": "wrong", "axes": {"gate": "passed"}}, _requires(), CATALOG)
    assert "not an assurance target" in str(raised.value)


def test_a_target_asking_for_nothing_is_refused_by_the_target_schema():
    """`assurance_target.validate` refuses an empty axes map, and this does not route around
    it: a target requiring nothing is met by everything."""
    with pytest.raises(ValueError):
        floor_from({"schema": assurance_target.SCHEMA, "axes": {}}, _requires(), CATALOG)


# ── the mapping's own schema ────────────────────────────────────────────────────
@pytest.mark.parametrize("payload,expected", [
    ([], "expected an object"),
    ({"gate": []}, "expected an object mapping"),
    ({"gate": {"passed": {}}}, "expected a list of steps"),
    ({"gate": {"passed": ["acceptance"]}}, "expected an object"),
    ({"nowhere": {"x": []}}, "does not report that axis"),
    ({"gate": {"perfect": []}}, "not one of"),
    ({"gate": {"passed": [{"id": "acceptance", "source": POLICY_REQUIRED}]}}, "missing reason"),
    ({"gate": {"passed": [dict(_step(), waivable=True)]}}, "unknown key(s) waivable"),
])
def test_a_mapping_that_is_not_one_is_refused(payload, expected):
    with pytest.raises(ValueError) as raised:
        load_requires(payload)
    assert expected in str(raised.value)


def test_a_mapping_may_not_name_a_source_a_floor_cannot_carry():
    """`Required` refuses a source outside the two that mean somebody declared it — an
    inferred step on a floor is a conclusion promoting itself into a requirement."""
    with pytest.raises(ValueError) as raised:
        load_requires({"gate": {"passed": [_step(source="inferred")]}})
    assert "may only be required by" in str(raised.value)


def test_it_says_what_it_could_not_plan_for_before_being_asked():
    """`floor_from` refuses one target at a time, so an operator would learn about a gap only
    when they happened to ask for that value."""
    gaps = unreachable(_requires())
    assert "gate: passed" not in gaps
    assert "gate: skipped" in gaps and "provenance: signed-and-verified" in gaps
    assert set(gaps) | {"gate: passed"} == {
        f"{axis}: {value}" for axis, values in assurance_target.AXES.items()
        for value in values}


# ── the projection, and the one place evaluate is called ────────────────────────
def _receipt(**blocks) -> dict:
    base = {"isolation": {"observed": True, "mode": "git-worktree"},
            "verifier": {"observed": True,
                         "independence": {"verdict": "declared-separate"}},
            "provenance": {"observed": True, "verified": True},
            "approvals": {"observed": True},
            "gates": {"observed": True, "status": "passed"}}
    base.update(blocks)
    return base


def test_the_projection_copies_what_evaluate_answered():
    result = projection(_target(gate="passed"), _receipt())
    assert result["observed"] is True
    assert result["status"] == "assurance-complete"
    assert result["axes"]["gate"] == {"outcome": "met", "required": "passed",
                                      "achieved": "passed"}
    assert "schema" not in result, "the receipt's blocks carry the receipt's schema"


def test_unobservable_stays_its_own_outcome():
    """`unmet` says rig looked and what it found falls short; `unobservable` says it cannot
    look. A caller folding them together reads 'we do not measure that' as 'we measured it and
    it was insufficient', and acts on it."""
    result = projection(_target(approval="recorded"),
                        _receipt(approvals={"observed": False, "reason": "governance inactive"}))
    assert result["status"] == "assurance-unobservable"
    assert result["unmet"] == 0 and result["unobservable"] == 1
    assert result["axes"]["approval"]["outcome"] == "unobservable"
    assert result["axes"]["approval"]["reason"] == "governance inactive"


def test_an_unmet_axis_reports_what_was_recorded():
    result = projection(_target(gate="passed"),
                        _receipt(gates={"observed": True, "status": "failed"}))
    assert result["status"] == "assurance-incomplete"
    assert result["axes"]["gate"] == {"outcome": "unmet", "required": "passed",
                                      "achieved": "failed"}


@pytest.mark.parametrize("payload,state,says", [
    (None, ABSENT, "nothing was asked for in writing"),
    ("UNREADABLE", UNREADABLE_FILE, "cannot be read"),
    ({"schema": "wrong", "axes": {"gate": "passed"}}, INVALID, "is not a target"),
    ({"schema": assurance_target.SCHEMA, "axes": {"gate": "perfect"}}, INVALID, "not one of"),
])
def test_having_nothing_to_compare_says_which_kind_of_nothing(payload, state, says):
    """"Nobody asked" and "one is there and nothing can read it" are different situations with
    different next steps, and a reader that had to tell them apart by matching sentences would
    get it wrong the first time either sentence is edited."""
    from rig_workbench.workbench.assurance import UNREADABLE

    result = projection(UNREADABLE if payload == "UNREADABLE" else payload, _receipt())
    assert result["observed"] is False
    assert result["not_recorded"] == state
    assert says in result["reason"]


def _fixture_repo(tmp_path, target=None) -> tuple[pathlib.Path, str]:
    """A repository with one task, optionally carrying an assurance target."""
    task_id = "rig-20260101-000000-example"
    run = tmp_path / ".rig" / "runs" / task_id
    run.mkdir(parents=True)
    # `input` is one of the fields every reader of the runs directory indexes (#488), so a
    # stub without it describes a record shape no real run has — and `read_all_tasks` is right
    # to name such a record rather than hand it on.
    (run / "task.json").write_text(json.dumps({
        "task_id": task_id, "task_type": "feature", "status": "accepted",
        "created_at": "2026-01-01T00:00:00+09:00", "input": "an example task",
        "worktree": {"runtime": "native", "path": str(tmp_path / "wt"), "branch": "b"}}),
        encoding="utf-8")
    (run / "acceptance.json").write_text(json.dumps({
        "task_id": task_id, "status": "passed", "presets": ["standard"],
        "checks": [{"name": "tests_pass_or_explained", "status": "passed", "detail": ""}]}),
        encoding="utf-8")
    if target is not None:
        (run / "assurance-target.json").write_text(json.dumps(target), encoding="utf-8")
    return tmp_path, task_id


def test_the_dashboard_gets_its_answer_through_the_receipt(tmp_path, monkeypatch):
    """Two readers of one record eventually disagree about it, and a dashboard disagreeing
    with the receipt about whether an assurance held is worse than either being wrong alone.
    Eight review rounds on #476 were this defect found one layer at a time.

    Counted at run time rather than grepped for: a text search matches this docstring, and
    approximating what the code does by reading its source is a mistake this repository has
    already paid for.
    """
    from rig_workbench import mission_control

    calls = []
    original = assurance_target.evaluate

    def counted(target, receipt):
        calls.append(target)
        return original(target, receipt)

    monkeypatch.setattr(assurance_target, "evaluate", counted)
    root, _ = _fixture_repo(tmp_path, _target(gate="passed"))
    section = mission_control._assurance_snapshot(root)
    assert section["counts"]["assurance-complete"] == 1
    assert len(calls) == 1, (
        "rendering the dashboard evaluated the target more than once — it is reading the "
        "files itself instead of copying the receipt")


def test_the_dashboard_counts_the_states_apart(tmp_path):
    from rig_workbench import mission_control

    root, _ = _fixture_repo(tmp_path)
    section = mission_control._assurance_snapshot(root)
    assert section["counts"][ABSENT] == 1
    assert section["counts"]["assurance-complete"] == 0
    assert section["tasks"] == [], "an absent target is the ordinary case, not a row"


def test_the_dashboard_names_a_target_it_cannot_read(tmp_path):
    """A row missing from a dashboard reads as a task with nothing to report, which is the one
    thing an unreadable target is not."""
    from rig_workbench import mission_control

    root, task_id = _fixture_repo(tmp_path, {"schema": "wrong", "axes": {"gate": "passed"}})
    section = mission_control._assurance_snapshot(root)
    assert section["counts"][INVALID] == 1
    assert [row["task_id"] for row in section["tasks"]] == [task_id]

    page = mission_control.render_html(mission_control.build_snapshot(root))
    assert "Assurance · asked for vs recorded" in page
    assert task_id in page


def test_the_page_never_adds_unobservable_to_unmet(tmp_path):
    """The confusion `assurance_target` names as the reason the outcome exists at all."""
    from rig_workbench import mission_control

    root, _ = _fixture_repo(tmp_path, _target(approval="recorded"))
    snapshot = mission_control.build_snapshot(root)
    assert snapshot["assurance"]["counts"]["assurance-unobservable"] == 1
    assert snapshot["assurance"]["counts"]["assurance-incomplete"] == 0
    page = mission_control.render_html(snapshot)
    assert "not observable" in page
    assert "not met" not in page, "an axis rig cannot answer was rendered as a shortfall"


def test_the_page_puts_no_score_of_its_own_on_the_numbers(tmp_path):
    """A rate computed by this page's own rule would be a second verdict on a page whose whole
    claim is that it holds none."""
    from rig_workbench import mission_control

    root, _ = _fixture_repo(tmp_path, _target(gate="passed"))
    section = mission_control.build_snapshot(root)["assurance"]
    assert set(section) == {"counts", "tasks", "unreadable_tasks", "unreadable_collection"}
    assert not any("pct" in key or "rate" in key or "score" in key for key in section["counts"])


# ── the command ─────────────────────────────────────────────────────────────────
def _run(tmp_path, target, requires, catalog=None):
    files = {}
    for name, payload in (("t.json", target), ("r.json", requires),
                          ("c.json", sorted(catalog if catalog is not None else CATALOG))):
        files[name] = tmp_path / name
        files[name].write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"
                             / "workbench.py"), "assurance-derive", str(files["t.json"]),
         "--requires", str(files["r.json"]), "--against", str(files["c.json"]), "--json"],
        capture_output=True, text=True)


def test_the_command_prints_the_floor_and_what_it_cannot_plan_for(tmp_path):
    result = _run(tmp_path, _target(gate="passed"), {"gate": {"passed": [_step()]}})
    assert result.returncode == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    assert payload["floor"] == {"acceptance": {"source": POLICY_REQUIRED,
                                               "reason": "the gate records it"}}
    assert "gate: skipped" in payload["unreachable"]


def test_the_command_exits_non_zero_when_it_cannot_derive(tmp_path):
    """Not `0` with an empty floor. The dispatcher discards what a subcommand returns, so a
    refusal that exited zero would leave the shell believing a floor had been derived."""
    result = _run(tmp_path, _target(provenance="none"), {"gate": {"passed": [_step()]}})
    assert result.returncode == 1
    assert "provenance: none" in result.stderr and result.stdout == ""


def test_the_command_reports_an_unreadable_file_as_its_own_status(tmp_path):
    """`2`, not `1`. "I could not read your files" and "what you asked for cannot be planned"
    are different answers to a caller deciding what to do next."""
    target = tmp_path / "t.json"
    target.write_text('{"schema": "rig.assurance-target/v1", "axes": {"gate": "passed",',
                      encoding="utf-8")
    requires = tmp_path / "r.json"
    requires.write_text(json.dumps({"gate": {"passed": [_step()]}}), encoding="utf-8")
    catalog = tmp_path / "c.json"
    catalog.write_text(json.dumps(sorted(CATALOG)), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"
                             / "workbench.py"), "assurance-derive", str(target),
         "--requires", str(requires), "--against", str(catalog)],
        capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "execution-error"


def test_a_target_naming_one_key_twice_is_refused_by_the_one_reader(tmp_path):
    """JSON allows a key twice and `json.loads` keeps the last one silently, so
    `"gate": "failed", "gate": "passed"` would be read as a request for a passing gate."""
    target = tmp_path / "t.json"
    target.write_text('{"schema": "rig.assurance-target/v1", "axes": {"gate": "failed", '
                      '"gate": "passed"}}', encoding="utf-8")
    with pytest.raises(ValueError) as raised:
        assurance_target.read(target)
    assert "gate" in str(raised.value)


# ── what the mutation sweep found: the page was asserted, not read ──────────────
def test_the_page_shows_the_unobservable_count_and_does_not_move_it(tmp_path):
    """Asserting the *word* is on the page says nothing about the number beside it: a tile
    reading zero while an axis is unobservable is the same silence with a label on it."""
    from rig_workbench import mission_control

    root, _ = _fixture_repo(tmp_path, _target(approval="recorded"))
    page = mission_control.render_html(mission_control.build_snapshot(root))
    tile = page[page.index("not observable"):]
    assert '<div class="metric-value">1</div>' in tile[:220]
    incomplete = page[page.index("assurance incomplete"):]
    assert '<div class="metric-value">0</div>' in incomplete[:240]


def test_the_page_prints_the_reason_rather_than_a_verdict_for_an_unobservable_axis(tmp_path):
    """`achieved` is `None` on that path by construction, so rendering it would put an empty
    cell where the receipt's own reason for not having looked belongs."""
    from rig_workbench import mission_control

    root, _ = _fixture_repo(tmp_path, _target(approval="recorded"))
    page = mission_control.render_html(mission_control.build_snapshot(root))
    row = page[page.index("approval: asked for"):]
    row = row[:row.index("</tr>")]
    assert "governance is inactive" in row or "no human gate" in row
    assert "not observable" in row
    # `bad` is the page's word for a shortfall. An axis rig cannot answer is not one.
    assert 'class="bad"' not in row


def test_the_page_marks_a_shortfall_and_only_a_shortfall(tmp_path):
    from rig_workbench import mission_control

    root, _ = _fixture_repo(tmp_path, _target(gate="failed"))
    page = mission_control.render_html(mission_control.build_snapshot(root))
    row = page[page.index("gate: asked for"):]
    row = row[:row.index("</tr>")]
    assert 'class="bad"' in row and "not met" in row


def test_a_task_whose_state_cannot_be_read_is_named_not_dropped(tmp_path, monkeypatch):
    """One unreadable task must not take the dashboard down, and must not vanish from it
    either: a row missing from a table reads as a task with nothing to report."""
    from rig_workbench import mission_control

    root, task_id = _fixture_repo(tmp_path, _target(gate="passed"))

    def broken(_root, _task_id):
        raise OSError("state is unreadable")

    monkeypatch.setattr(mission_control, "build_receipt", broken)
    section = mission_control._assurance_snapshot(root)
    assert section["unreadable_tasks"] == [task_id]
    assert section["counts"]["assurance-complete"] == 0

    snapshot = mission_control.build_snapshot(root)
    page = mission_control.render_html(snapshot)
    assert "State could not be read for 1 task(s)" in page and task_id in page


def test_the_assurance_target_command_reads_through_the_one_parser(tmp_path):
    """`assurance-derive` and the receipt refuse a duplicated key; the command that compares a
    target against a receipt has to refuse it too, or the same document means two things
    depending on which entry point read it."""
    root, task_id = _fixture_repo(tmp_path)
    target = tmp_path / "t.json"
    target.write_text('{"schema": "rig.assurance-target/v1", "axes": '
                      '{"gate": "failed", "gate": "passed"}}', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"
                             / "workbench.py"), "assurance-target", task_id, str(target)],
        capture_output=True, text=True, cwd=root)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["status"] == "execution-error"
    assert "gate" in json.loads(result.stdout)["error"]


# ── what round 1 found ──────────────────────────────────────────────────────────
def test_a_mapping_naming_one_key_twice_is_refused(tmp_path):
    """`{"gate": {"passed": [...], "passed": []}}` would be read as the *empty* declaration,
    which means "this needs no step of its own" — the one answer this module refuses to reach
    by accident. The distinction between an absent pair and an empty one is its whole safety
    rule, and a parser that turns one into the other hands it away."""
    doc = tmp_path / "r.json"
    doc.write_text('{"gate": {"passed": [{"id": "acceptance", "source": "policy-required", '
                   '"reason": "the gate records it"}], "passed": []}}', encoding="utf-8")
    with pytest.raises(ValueError) as raised:
        assurance_wiring.read_requires(doc)
    assert "passed" in str(raised.value)


def test_a_mapping_naming_an_axis_twice_is_refused(tmp_path):
    doc = tmp_path / "r.json"
    doc.write_text('{"gate": {"passed": []}, "gate": {"failed": []}}', encoding="utf-8")
    with pytest.raises(ValueError) as raised:
        assurance_wiring.read_requires(doc)
    assert "gate" in str(raised.value)


def test_the_command_reads_the_mapping_through_that_reader(tmp_path):
    """Not a second parser. A rule each caller has to remember is a rule one of them will not,
    and this is the shape that cost #476 eight review rounds."""
    target = tmp_path / "t.json"
    target.write_text(json.dumps(_target(gate="passed")), encoding="utf-8")
    requires = tmp_path / "r.json"
    requires.write_text('{"gate": {"passed": [{"id": "acceptance", "source": '
                        '"policy-required", "reason": "x"}], "passed": []}}', encoding="utf-8")
    catalog = tmp_path / "c.json"
    catalog.write_text(json.dumps(sorted(CATALOG)), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"
                             / "workbench.py"), "assurance-derive", str(target),
         "--requires", str(requires), "--against", str(catalog)],
        capture_output=True, text=True)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert "passed" in json.loads(result.stdout)["error"]


def test_a_task_whose_own_record_cannot_be_parsed_is_named(tmp_path):
    """A task this section cannot read is named rather than skipped: a row missing from a
    dashboard reads as a task that has nothing to report. `read_all_tasks` now carries such
    directories alongside the records it did read (#488), and this section reports the ones it
    hands over together with the tasks whose receipt it could not build."""
    from rig_workbench import mission_control

    root, _ = _fixture_repo(tmp_path, _target(gate="passed"))
    broken = root / ".rig" / "runs" / "rig-20260101-000001-broken"
    broken.mkdir()
    (broken / "task.json").write_text("{not json", encoding="utf-8")

    section = mission_control._assurance_snapshot(root)
    assert section["unreadable_tasks"] == ["rig-20260101-000001-broken"]
    assert section["counts"]["assurance-complete"] == 1, "the readable task still reported"


# ── what round 2 found ──────────────────────────────────────────────────────────
def test_an_unreadable_runs_directory_is_not_zero_tasks(tmp_path, monkeypatch):
    """Reporting it as zero would print "no task has recorded an assurance target yet", which
    is a verdict this page did not establish and cannot: it never looked."""
    from rig_workbench import mission_control

    root, _ = _fixture_repo(tmp_path, _target(gate="passed"))
    real = pathlib.Path.iterdir

    def refused(self):
        if self.name == "runs":
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(pathlib.Path, "iterdir", refused)
    section = mission_control._assurance_snapshot(root)
    assert section["unreadable_collection"] is not None
    assert "PermissionError" in section["unreadable_collection"]
    assert section["counts"]["assurance-complete"] == 0
    monkeypatch.undo()

    snapshot = mission_control.build_snapshot(root)
    snapshot["assurance"]["unreadable_collection"] = "PermissionError: Permission denied"
    page = mission_control.render_html(snapshot)
    assert "The run records could not be read" in page
    assert "No task has recorded an assurance target yet" not in page


def test_a_missing_runs_directory_is_a_cold_start_not_a_failure(tmp_path):
    """Nothing recorded yet is a fact about the repository; failing to look is a fact about
    rig, and a page that reported one as the other would be wrong either way round."""
    from rig_workbench import mission_control

    section = mission_control._assurance_snapshot(tmp_path)
    assert section["unreadable_collection"] is None
    assert section["counts"][ABSENT] == 0


# The invariant is that *one implementation* compares a target against a receipt, so two views
# cannot come to different answers about the same question. It is not that one comparison
# happens — `assurance-target` answers about the file on the command line while the receipt has
# answered about the file in the run, and both are printed.
#
# A static guard over the source used to sit here, refusing a second caller of
# `assurance_target.evaluate`. It was broken four times running — it matched the spelling, then
# missed an alias, then the module that defines the function, then `getattr` — and the next
# hole was `globals()[...]`, and after that `operator.attrgetter`. Approximating Python's name
# binding with `ast` is a thing this repository has paid for once already. What it was
# protecting is covered exactly and at run time by
# `test_the_dashboard_gets_its_answer_through_the_receipt`, which counts the calls made while
# the dashboard renders. A check that cannot be made true is worth less than the one that
# already is.


# ── what round 3 found ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("extra", [{"waive": True}, {"axis": "isolation"}, {"note": "x"}])
def test_a_target_carrying_a_key_nothing_reads_is_refused(extra):
    """A key accepted and read by nothing would let a target carry `waive: true` all the way
    to the floor, the receipt and the dashboard while the field it asserted was discarded —
    leaving the author believing the target said something no part of rig ever read."""
    problems = assurance_target.validate({**_target(gate="passed"), **extra})
    assert any("unknown key" in p for p in problems)


def test_the_closed_schema_reaches_every_entry_point(tmp_path):
    """One refusal, wherever a target arrives: `floor_from`, the receipt's projection and the
    command all go through `validate`."""
    bad = {**_target(gate="passed"), "waive": True}
    with pytest.raises(ValueError) as raised:
        floor_from(bad, _requires(), CATALOG)
    assert "unknown key" in str(raised.value)

    projected = projection(bad, _receipt())
    assert projected["observed"] is False and projected["not_recorded"] == INVALID

    result = _run(tmp_path, bad, {"gate": {"passed": [_step()]}})
    assert result.returncode == 1 and "unknown key" in result.stderr


def test_the_page_does_not_say_nothing_was_asked_for_when_it_could_not_look(tmp_path,
                                                                            monkeypatch):
    """Every task that could have had a target was one the page could not read. Saying "no
    task has recorded an assurance target yet" would be a verdict about the targets, reached
    without looking at a single one."""
    from rig_workbench import mission_control

    root, _ = _fixture_repo(tmp_path, _target(gate="passed"))

    def broken(_root, _task_id):
        raise OSError("state is unreadable")

    monkeypatch.setattr(mission_control, "build_receipt", broken)
    snapshot = mission_control.build_snapshot(root)
    page = mission_control.render_html(snapshot)
    assert "No task has recorded an assurance target yet" not in page
    assert "Nothing could be read about what was asked for" in page


def test_the_command_shows_the_runs_own_recorded_target_too(tmp_path):
    """This command answers about the file named on the command line; the receipt has already
    answered about the file in the run. Both are real questions with legitimately different
    answers, and a command showing one while the other existed would let a reader take the
    answer to the question they did not ask."""
    root, task_id = _fixture_repo(tmp_path, _target(gate="failed"))
    # The command resolves the repository root, so the fixture has to be one.
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    target = tmp_path / "asked.json"
    target.write_text(json.dumps(_target(gate="passed")), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"
                             / "workbench.py"), "assurance-target", task_id, str(target),
         "--json"], capture_output=True, text=True, cwd=root)
    payload = json.loads(result.stdout)
    assert payload["asked"]["status"] == "assurance-complete", "the file on the command line"
    assert payload["recorded"]["status"] == "assurance-incomplete", "the file in the run"

    # And on the page a person actually reads, not only in the JSON.
    plain = subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"
                             / "workbench.py"), "assurance-target", task_id, str(target)],
        capture_output=True, text=True, cwd=root)
    assert "the run's own recorded target: assurance-incomplete" in plain.stdout


def test_the_command_says_when_the_run_recorded_no_target_of_its_own(tmp_path):
    """"The run asked for nothing" and "the run asked for something else" are different, and a
    line that appeared only in one case would leave the reader to guess which."""
    root, task_id = _fixture_repo(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    target = tmp_path / "asked.json"
    target.write_text(json.dumps(_target(gate="passed")), encoding="utf-8")
    plain = subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"
                             / "workbench.py"), "assurance-target", task_id, str(target)],
        capture_output=True, text=True, cwd=root)
    assert "the run's own recorded target: none — no assurance-target.json" in plain.stdout


def test_a_receipt_without_the_block_says_so_rather_than_raising(tmp_path, monkeypatch):
    """Every receipt this repository builds carries it; one that does not came from somewhere
    else. Printing nothing about the run's own target would read as the run having recorded
    none, and a `KeyError` reported as an execution error says less than the sentence."""
    from rig_workbench.workbench import assurance_target as module

    root, task_id = _fixture_repo(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    target = tmp_path / "asked.json"
    target.write_text(json.dumps(_target(gate="passed")), encoding="utf-8")

    monkeypatch.setattr(module, "repo_root", lambda: root, raising=False)
    monkeypatch.setattr("rig_workbench.workbench.assurance.build_receipt",
                        lambda _root, _task: {"gates": {"observed": True, "status": "passed"}})

    class Args:
        json = False

    Args.task_id, Args.target = task_id, str(target)
    with pytest.raises(SystemExit) as exited:
        module.cmd_assurance_target(Args())
    assert exited.value.code == 0, "the comparison it was asked for still happened"
