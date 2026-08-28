"""Phase timing, budgets, and the regression gate (#502).

The unit before this one was a single elapsed number per run, which cannot answer the only
question a performance report is asked: did rig get slower, or did the provider? These tests
pin the separation and — more importantly — the three ways a performance gate usually rots:

* an unmeasured figure reading as zero, so work that stopped being watched looks like work
  that stopped costing anything;
* a budget naming something the run could not measure reading as a pass, so the gate quietly
  enforces nothing;
* provider latency being gateable, so the gate fails on somebody else's network and gets
  deleted, taking the parts rig can answer for with it.
"""

import json

import pytest

from rig_workbench.orchestrate import commands, config, perf, providers
from rig_workbench.orchestrate.recipes import (load_steps, parse_frontmatter,
                                               resolve_recipe)
from rig_workbench.orchestrate.runstate import new_state


@pytest.fixture
def runs_log(tmp_path, monkeypatch):
    """A per-test `.rig/runs.jsonl`. The suite leaves `RIG_RUNS_PATH` alone on purpose (see
    tests/test_suite_isolation.py), so a test that reads it back has to pin its own."""
    path = tmp_path / "runs.jsonl"
    monkeypatch.setenv("RIG_RUNS_PATH", str(path))
    return path


def _run(tmp_path, cfg=None, quiet=True):
    steps = load_steps(parse_frontmatter(resolve_recipe("bugfix")))
    state = new_state("bugfix", steps, "fix")
    final = providers.run_loop(state, None, "mock", "mock",
                               {"cwd": str(tmp_path), **(cfg or {})}, 40, quiet=quiet)
    return state, final


# ── what a real run records ──────────────────────────────────────────────────
def test_a_run_records_where_its_time_went(tmp_path, runs_log):
    """End-to-end through the shipped `bugfix` recipe: the phases reach telemetry, and rig's
    own share is separated from the providers'."""
    state, final = _run(tmp_path)
    assert final == "DONE"

    measured = state["perf"]
    assert measured["phases"]["provider_generator"]["calls"] >= 1
    assert measured["phases"]["gate"]["calls"] >= 1
    # The subtraction actually happened, and it left something for rig.
    assert measured["total_ms"] >= measured["provider_ms"]
    assert measured["rig_overhead_ms"] == pytest.approx(
        measured["total_ms"] - measured["provider_ms"], abs=0.01)

    written = json.loads(runs_log.read_text(encoding="utf-8").splitlines()[-1])
    assert written["perf"]["phases"] == measured["phases"]


def test_a_phase_that_did_not_run_is_named_rather_than_zeroed(tmp_path, runs_log):
    """`bugfix` declares no `checks:` and does no risk assessment. Those phases have to be
    reported as unmeasured — rendering them as `0ms` would say rig runs its checks for free."""
    state, _ = _run(tmp_path)
    assert "checks" in state["perf"]["unmeasured"]
    assert "checks" not in state["perf"]["phases"]


def test_two_runs_do_not_blend(tmp_path, runs_log):
    """`run_loop` owns the accumulator, so a caller reusing one cfg cannot accumulate one
    run's timings into the next — the failure `_token_usage` already had to be built against."""
    shared = {"cwd": str(tmp_path)}
    steps = load_steps(parse_frontmatter(resolve_recipe("bugfix")))
    first = new_state("bugfix", steps, "fix")
    providers.run_loop(first, None, "mock", "mock", shared, 40, quiet=True)
    second = new_state("bugfix", steps, "fix")
    providers.run_loop(second, None, "mock", "mock", shared, 40, quiet=True)

    assert (second["perf"]["phases"]["provider_generator"]["calls"]
            == first["perf"]["phases"]["provider_generator"]["calls"])
    assert "_perf" not in shared


# ── the honesty rules ────────────────────────────────────────────────────────
def test_an_untimed_provider_call_withholds_overhead_rather_than_guessing_it():
    """Overhead is total minus provider time. One unobserved provider call is time that would
    land in overhead without belonging there, so the number is refused, with its reason."""
    cfg = {"_perf": perf.accumulator()}
    perf.record(cfg, "gate", 0.010)
    perf.record(cfg, "provider_generator", 1.0)
    perf.record_untimed(cfg)

    measured = perf.summary(cfg, total_ms=5000.0)
    assert "rig_overhead_ms" not in measured
    assert "1 provider call" in measured["rig_overhead_unmeasured"]


def test_overhead_is_withheld_without_a_total_it_could_be_subtracted_from():
    """The sum of the phases is not the total — it omits everything between them — so using
    it would understate rig's own overhead. Flattering, and wrong."""
    cfg = {"_perf": perf.accumulator()}
    perf.record(cfg, "gate", 0.010)
    assert "rig_overhead_ms" not in perf.summary(cfg)


def test_timing_a_block_that_raises_still_records_it():
    """The slow path is usually the interesting one; dropping it would leave a report
    describing only the runs that went well."""
    cfg = {"_perf": perf.accumulator()}
    with pytest.raises(ValueError):
        with perf.timed(cfg, "checks"):
            raise ValueError("boom")
    assert perf.summary(cfg)["phases"]["checks"]["calls"] == 1


def test_timing_is_inert_without_an_accumulator():
    """A run must not fail because nobody wanted its numbers — the same reason
    `telemetry_append` swallows a write failure."""
    perf.record({}, "gate", 1.0)
    perf.record_untimed({})
    perf.record_context_bytes({}, "x")
    assert perf.summary({}) is None


# ── comparison ───────────────────────────────────────────────────────────────
def test_a_phase_that_stopped_being_measured_is_not_an_improvement():
    before = {"phases": {"gate": {"ms": 100.0}, "checks": {"ms": 50.0}}}
    after = {"phases": {"gate": {"ms": 100.0}}}
    comparison = perf.compare(before, after)

    assert comparison["stopped_being_measured"] == ["checks"]
    assert not any(item["phase"] == "checks" for item in comparison["phases"])
    assert "did not get faster" in "\n".join(perf.render(comparison))


def test_provider_latency_is_reported_but_never_gated():
    """A gate that failed on somebody else's network would be switched off within a month,
    taking the phases rig can actually answer for with it."""
    before = {"phases": {"gate": {"ms": 10.0}, "provider_generator": {"ms": 1.0}}}
    after = {"phases": {"gate": {"ms": 10.5}, "provider_generator": {"ms": 900.0}}}
    comparison = perf.compare(before, after, tolerance_pct=20.0)

    assert comparison["regressed"] == []
    assert [item["phase"] for item in comparison["provider_drift"]] == ["provider_generator"]
    assert "[not gated: provider]" in "\n".join(perf.render(comparison))


def test_rig_overhead_growing_past_the_tolerance_is_a_regression():
    before = {"phases": {"gate": {"ms": 100.0}}, "rig_overhead_ms": 100.0}
    after = {"phases": {"gate": {"ms": 200.0}}, "rig_overhead_ms": 200.0}
    comparison = perf.compare(before, after, tolerance_pct=20.0)

    assert [item["phase"] for item in comparison["regressed"]] == ["gate"]
    assert comparison["rig_overhead_ms"]["delta_pct"] == 100.0


def test_a_figure_one_side_never_took_is_not_comparable():
    comparison = perf.compare({"phases": {"gate": {"ms": 1.0}}, "rig_overhead_ms": 5.0},
                              {"phases": {"gate": {"ms": 1.0}}})
    assert comparison["rig_overhead_ms_comparable"] is False
    assert "not comparable" in "\n".join(perf.render(comparison))


# ── budgets ──────────────────────────────────────────────────────────────────
def test_a_budget_naming_something_unmeasured_is_unenforced_not_passed():
    """The failure this whole module is built against: a limit nobody could test reading as a
    limit that held."""
    broken = perf.check_budget({"phases": {}}, {"max_rig_overhead_ms": 10})
    assert broken and "was not enforced" in broken[0]


def test_a_budget_key_with_a_typo_in_it_is_reported():
    broken = perf.check_budget({"rig_overhead_ms": 1.0}, {"max_rig_overhad_ms": 10})
    assert broken and "unknown budget key" in broken[0]


def test_a_budget_that_holds_says_nothing():
    assert perf.check_budget({"rig_overhead_ms": 5.0, "context_bytes_emitted": 10},
                             {"max_rig_overhead_ms": 10, "max_context_bytes": 100}) == []


def test_breaking_a_budget_warns_but_does_not_change_the_verdict(tmp_path, runs_log, capsys):
    """A perf budget failing a bugfix would make people stop declaring budgets.
    `rig-wb perf --check` is where it costs something."""
    state, final = _run(tmp_path, {"perf_budget": {"max_rig_overhead_ms": 0}}, quiet=False)
    assert final == "DONE"
    assert state["perf_budget_broken"]
    # Through `log`, so `--artifact-stdout` (which runs quiet) keeps its stdout contract: the
    # warning is for a person watching a run, and that mode has no person watching stdout.
    assert "perf budget" in capsys.readouterr().out
    written = json.loads(runs_log.read_text(encoding="utf-8").splitlines()[-1])
    assert written["perf_budget_broken"] == state["perf_budget_broken"]


# ── aggregation ──────────────────────────────────────────────────────────────
def test_one_slow_run_does_not_set_the_baseline():
    """A laptop that slept, a cold cache, a noisy neighbour. A mean would let any of them
    decide what every later run is judged against; the median moves only when most runs do."""
    runs = [{"phases": {"gate": {"ms": 10.0}}} for _ in range(4)]
    runs.append({"phases": {"gate": {"ms": 10_000.0}}})
    assert perf.aggregate(runs)["phases"]["gate"]["ms"] == 10.0


def test_a_phase_only_some_runs_measured_carries_how_many():
    aggregated = perf.aggregate([{"phases": {"gate": {"ms": 1.0}}},
                                 {"phases": {"gate": {"ms": 3.0}, "checks": {"ms": 9.0}}}])
    assert aggregated["phases"]["checks"]["runs"] == 1
    assert aggregated["runs"] == 2
    assert aggregated["phases"]["gate"]["ms"] == 2.0


def test_aggregating_runs_that_measured_nothing_yields_nothing():
    assert perf.aggregate([{"phases": {}}, {}]) is None


# ── the gate command ─────────────────────────────────────────────────────────
def test_check_with_nothing_to_judge_fails_rather_than_passes(tmp_path, runs_log, capsys):
    """A gate with no measurements has not judged anything, and a green light for that is how
    a gate stops gating without anyone noticing."""
    with pytest.raises(SystemExit) as exit_:
        commands.cmd_perf(["--check", "--recipe", "bugfix"])
    assert exit_.value.code == 1
    assert "no timed runs" in capsys.readouterr().out


def test_check_with_neither_budget_nor_baseline_fails(tmp_path, runs_log, monkeypatch):
    _run(tmp_path)
    monkeypatch.setattr(commands, "load_manifest", lambda *a, **k: {})
    with pytest.raises(SystemExit) as exit_:
        commands.cmd_perf(["--check", "--recipe", "bugfix"])
    assert exit_.value.code == 1


def test_baseline_round_trip_passes_against_itself(tmp_path, runs_log, monkeypatch, capsys):
    _run(tmp_path)
    monkeypatch.setattr(commands, "load_manifest", lambda *a, **k: {})
    baseline = tmp_path / "baseline.json"
    commands.cmd_perf(["--recipe", "bugfix", "--save-baseline", str(baseline)])
    assert json.loads(baseline.read_text(encoding="utf-8"))["recipe"] == "bugfix"

    commands.cmd_perf(["--recipe", "bugfix", "--check", "--baseline", str(baseline)])
    assert "within budget" in capsys.readouterr().out


def test_the_gate_fails_on_a_regressed_baseline(tmp_path, runs_log, monkeypatch, capsys):
    _run(tmp_path)
    monkeypatch.setattr(commands, "load_manifest", lambda *a, **k: {})
    baseline = tmp_path / "baseline.json"
    commands.cmd_perf(["--recipe", "bugfix", "--save-baseline", str(baseline)])
    shrunk = json.loads(baseline.read_text(encoding="utf-8"))
    for entry in shrunk["phases"].values():
        entry["ms"] = max(entry["ms"] / 100.0, 0.0001)
    baseline.write_text(json.dumps(shrunk), encoding="utf-8")

    with pytest.raises(SystemExit) as exit_:
        commands.cmd_perf(["--recipe", "bugfix", "--check", "--baseline", str(baseline)])
    assert exit_.value.code == 1
    out = capsys.readouterr().out
    assert "[perf] FAIL" in out
    # ...and the failures named are rig's own phases, never the providers'.
    assert not any("provider_" in line for line in out.splitlines() if "[perf] FAIL" in line)


def test_the_budget_comes_from_the_manifest_when_no_file_is_given(tmp_path, runs_log,
                                                                 monkeypatch):
    """A budget has to be committed to be a gate, and `.rig/` is gitignored — so its home is
    the manifest, not a generated file."""
    _run(tmp_path)
    monkeypatch.setattr(commands, "load_manifest",
                        lambda *a, **k: {"perf_budget": {"max_rig_overhead_ms": 0}})
    with pytest.raises(SystemExit) as exit_:
        commands.cmd_perf(["--recipe", "bugfix", "--check"])
    assert exit_.value.code == 1


def test_the_run_log_path_is_the_one_the_command_reads(tmp_path, runs_log):
    """Guards the fixture itself: if `RIG_RUNS_PATH` stopped being honoured, every test above
    would read some other repository's history and pass for the wrong reason."""
    _run(tmp_path)
    assert config.RUNS_PATH == runs_log
    assert runs_log.exists()


# ── tokens ───────────────────────────────────────────────────────────────────
def test_token_totals_come_from_the_usage_rollup():
    cfg = {"_perf": perf.accumulator()}
    perf.record(cfg, "provider_generator", 1.0)
    measured = perf.summary(cfg, total_ms=2000.0, token_usage={
        "ollama": {"prompt_tokens": 100, "completion_tokens": 40, "calls": 1}})
    assert (measured["input_tokens"], measured["output_tokens"]) == (100, 40)
    assert "token_usage_partial" not in measured


def test_a_run_only_some_providers_reported_usage_for_says_so():
    """claude and codex expose no structured usage and rig will not estimate it (#271/#296),
    so on a mixed run the totals are real but cover part of the work."""
    cfg = {"_perf": perf.accumulator()}
    perf.record(cfg, "provider_generator", 1.0)
    perf.record(cfg, "provider_verifier", 1.0)
    measured = perf.summary(cfg, total_ms=2000.0, token_usage={
        "ollama": {"prompt_tokens": 100, "completion_tokens": 40, "calls": 1}})
    assert "1 of 2 provider call(s)" in measured["token_usage_partial"]


def test_a_token_budget_is_unenforced_against_partial_coverage():
    """Otherwise the limit passes for a reason that has nothing to do with the work staying
    inside it: half the calls were simply never counted."""
    broken = perf.check_budget(
        {"output_tokens": 10, "token_usage_partial": "1 of 2 provider call(s) reported usage"},
        {"max_output_tokens": 1000})
    assert broken and "was not enforced" in broken[0]


def test_a_run_with_no_usage_at_all_reports_no_token_figures():
    cfg = {"_perf": perf.accumulator()}
    perf.record(cfg, "provider_generator", 1.0)
    measured = perf.summary(cfg, total_ms=2000.0, token_usage={})
    assert "output_tokens" not in measured and "input_tokens" not in measured


# ── percentage limits ────────────────────────────────────────────────────────
def test_a_token_regression_is_caught_alongside_a_wall_clock_one():
    comparison = perf.compare(
        {"phases": {"gate": {"ms": 1.0}}, "output_tokens": 1000},
        {"phases": {"gate": {"ms": 1.0}}, "output_tokens": 1500})
    broken = perf.check_regression(comparison, {"max_token_regression_pct": 20})
    assert broken and "+50.0%" in broken[0]


def test_a_percentage_limit_within_tolerance_says_nothing():
    comparison = perf.compare({"phases": {"gate": {"ms": 1.0}}, "rig_overhead_ms": 1000.0},
                              {"phases": {"gate": {"ms": 1.0}}, "rig_overhead_ms": 1050.0})
    assert perf.check_regression(comparison, {"max_overhead_regression_pct": 20}) == []


def test_a_percentage_limit_one_side_could_not_measure_is_unenforced():
    comparison = perf.compare({"phases": {"gate": {"ms": 1.0}}},
                              {"phases": {"gate": {"ms": 1.0}}, "output_tokens": 900})
    broken = perf.check_regression(comparison, {"max_token_regression_pct": 20})
    assert broken and "was not enforced" in broken[0]


def test_a_percentage_limit_declared_without_a_baseline_is_reported(tmp_path, runs_log,
                                                                    monkeypatch):
    """A percentage is a statement about a change, and a run compared against nothing has not
    changed. Letting the declaration sit unchecked is the same silent non-gate."""
    _run(tmp_path)
    monkeypatch.setattr(commands, "load_manifest",
                        lambda *a, **k: {"perf_budget": {"max_overhead_regression_pct": 20}})
    with pytest.raises(SystemExit) as exit_:
        commands.cmd_perf(["--recipe", "bugfix", "--check"])
    assert exit_.value.code == 1


def test_a_percentage_limit_is_not_mistaken_for_an_unknown_key():
    assert perf.check_budget({"rig_overhead_ms": 1.0}, {"max_token_regression_pct": 20}) == []
