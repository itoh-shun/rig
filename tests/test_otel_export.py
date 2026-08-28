"""Rig's evidence projected to OpenTelemetry (#501).

The projection is the security boundary, so most of this file is about what does *not* come
out. `runs.jsonl` carries text a model wrote — a verdict's `anchor` routinely holds a file
path — so a projection built as copy-then-filter would leak by default and would leak again
the first time somebody added a field without thinking about telemetry. These tests pin the
allowlist, and pin that the export cannot affect a run.
"""

import json
import pathlib
import tempfile
import threading
import http.server

import pytest

from rig_workbench.orchestrate import commands, config, otel, providers
from rig_workbench.orchestrate.recipes import (load_steps, parse_frontmatter,
                                               resolve_recipe)
from rig_workbench.orchestrate.runstate import new_state

#: A record shaped like a real one, with something sensitive in every field a careless
#: projection would copy: an absolute path inside a model-written anchor, the goal, a prompt,
#: a diff, the verifier's identity, the step id, the model name.
POISONED = {
    "ts": "2026-08-28T02:00:00+00:00", "recipe": "bugfix", "backend": "orchestrate",
    "invoker": "direct", "final": "DONE", "steps_total": 1, "steps_passed": 1, "retries": 0,
    "token_usage": {"ollama": {"prompt_tokens": 10, "completion_tokens": 5, "calls": 1}},
    "perf": {"phases": {"gate": {"ms": 1.0, "calls": 1}}, "total_ms": 10.0,
             "rig_overhead_ms": 9.0,
             "spans": [{"phase": "gate", "start_ms": 0.0, "end_ms": 1.0}]},
    "steps": [{"id": "sekrit-step-id", "status": "passed", "model": "sekrit-model",
               "verdicts": [{"by": "sekrit-verifier", "ok": True,
                             "criteria": [{"n": 1, "verdict": "PASS",
                                           "anchor": "/home/sekrit-user/private/apikey.py:42"}]}]}],
    "goal": "SEKRIT-GOAL", "prompt": "SEKRIT-PROMPT", "diff": "SEKRIT-DIFF",
}


def _both(record=POISONED):
    return json.dumps(otel.project_traces([record])) + json.dumps(otel.project_metrics([record]))


# ── what must never come out ─────────────────────────────────────────────────
@pytest.mark.parametrize("secret", [
    "/home/sekrit-user", "apikey.py", "SEKRIT-GOAL", "SEKRIT-PROMPT", "SEKRIT-DIFF",
    "sekrit-verifier", "sekrit-step-id", "sekrit-model",
])
def test_nothing_sensitive_reaches_the_payload(secret):
    assert secret not in _both()


def test_a_field_nobody_allowlisted_is_simply_absent():
    """The allowlist's whole point: a record that grows a field does not start exporting it.
    A denylist would have to be updated for this record and would ship it until somebody was."""
    grown = {**POISONED, "operator_email": "someone@example.invalid",
             "workspace_path": "/home/sekrit-user/rig"}
    blob = _both(grown)
    assert "someone@example.invalid" not in blob
    assert "/home/sekrit-user/rig" not in blob


def test_the_exported_attribute_names_are_the_declared_ones():
    spans = otel.project_traces([POISONED])["resourceSpans"][0]["scopeSpans"][0]["spans"]
    keys = {attribute["key"] for span in spans for attribute in span["attributes"]}
    assert keys <= (set(otel.RUN_ATTRIBUTES.values()) |
                    {"rig.steps.total", "rig.steps.passed", "rig.retries", "rig.escalated_at",
                     "rig.perf.budget_broken", "rig.phase", "rig.role"})


# ── only measured timings ────────────────────────────────────────────────────
def test_a_phase_with_no_recorded_interval_gets_no_span():
    """Laying aggregates end-to-end to draw a tree would invent an ordering nobody observed."""
    record = {**POISONED, "perf": {"phases": {"gate": {"ms": 5.0, "calls": 2},
                                              "checks": {"ms": 90.0, "calls": 1}},
                                   "total_ms": 100.0, "spans": []}}
    spans = otel.project_traces([record])["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert [span["name"] for span in spans] == ["rig.run"]


def test_children_sit_inside_their_parent():
    spans = otel.project_traces([POISONED])["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root = next(span for span in spans if "parentSpanId" not in span)
    for child in (span for span in spans if span.get("parentSpanId")):
        assert child["parentSpanId"] == root["spanId"]
        assert int(root["startTimeUnixNano"]) <= int(child["startTimeUnixNano"])
        assert int(child["endTimeUnixNano"]) <= int(root["endTimeUnixNano"])


def test_a_record_with_an_unreadable_timestamp_is_skipped_not_dated_now():
    """Exporting it as if the run had just happened would put a fabricated time on a real run,
    which is worse than not exporting it."""
    spans = otel.project_traces([{**POISONED, "ts": "not a timestamp"}])
    assert spans["resourceSpans"][0]["scopeSpans"][0]["spans"] == []


def test_figures_nothing_measures_are_absent_rather_than_zero():
    """Cached tokens, cost, TTFT and tokens/sec: no path in rig observes any of them, and a
    zero would read as a measurement of frugality rather than of absence."""
    blob = _both()
    for absent in ("cache_read", "rig.cost", "ttft", "tokens_per_sec"):
        assert absent not in blob


def test_overhead_that_perf_withheld_is_not_reinvented_downstream():
    """`perf` withholds `rig_overhead_ms` when a provider call went untimed. A metric filling
    that gap with a plausible number would undo the refusal one layer down."""
    record = {**POISONED, "perf": {"phases": {"gate": {"ms": 1.0, "calls": 1}},
                                   "total_ms": 10.0, "spans": [],
                                   "rig_overhead_unmeasured": "1 provider call(s) were not timed"}}
    names = [metric["name"] for metric in otel.project_metrics([record])["resourceMetrics"][0]
             ["scopeMetrics"][0]["metrics"]]
    assert "rig.overhead.duration" not in names


def test_partial_token_coverage_is_labelled():
    """A reader summing these without knowing would conclude rig got cheaper when it only got
    quieter — claude and codex report no usage at all."""
    record = {**POISONED, "perf": {**POISONED["perf"],
                                   "token_usage_partial": "1 of 2 provider call(s) reported usage"}}
    blob = json.dumps(otel.project_metrics([record]))
    assert "rig.tokens.partial" in blob


# ── identity ─────────────────────────────────────────────────────────────────
def test_exporting_the_same_log_twice_is_the_same_trace():
    """Ids come from the record's content, so re-running the exporter does not turn one run
    into two."""
    first = otel.project_traces([POISONED])["resourceSpans"][0]["scopeSpans"][0]["spans"]
    second = otel.project_traces([POISONED])["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert [span["spanId"] for span in first] == [span["spanId"] for span in second]
    assert len({span["traceId"] for span in first}) == 1


def test_an_escalated_run_is_an_error_on_the_trace():
    """A run that stopped for a human is not rig failing, but it is not a finished task
    either, and a dashboard showing it green counts unfinished work as done."""
    spans = otel.project_traces([{**POISONED, "final": "ESCALATE"}])
    root = spans["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert root["status"]["code"] == 2


def test_a_broken_perf_budget_is_an_attribute_not_a_failed_span():
    """It never changed the run's outcome (#502) and must not look as though it did."""
    spans = otel.project_traces([{**POISONED, "perf_budget_broken": ["context emitted: too big"]}])
    root = spans["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert root["status"]["code"] == 1
    assert any(a["key"] == "rig.perf.budget_broken" for a in root["attributes"])
    assert "too big" not in json.dumps(spans)


# ── configuration: off unless asked ──────────────────────────────────────────
@pytest.mark.parametrize("manifest", [
    {},
    {"observability": {}},
    {"observability": {"enabled": False, "otlp_endpoint": "http://localhost:4318"}},
    {"observability": {"enabled": True}},
    {"observability": {"enabled": True, "otlp_endpoint": "localhost:4318"}},
    {"observability": {"enabled": True, "otlp_endpoint": "file:///etc/passwd"}},
    {"observability": "yes please"},
])
def test_anything_ambiguous_sends_nothing(manifest):
    """Telemetry that started flowing because a config file was mistyped is a data-egress
    incident, so the ambiguous case does nothing."""
    assert otel.settings(manifest)["enabled"] is False


def test_a_complete_declaration_turns_it_on():
    resolved = otel.settings({"observability": {
        "enabled": True, "otlp_endpoint": "http://localhost:4318", "service_name": "rig-ci"}})
    assert resolved == {"enabled": True, "otlp_endpoint": "http://localhost:4318",
                        "service_name": "rig-ci", "export_traces": True,
                        "export_metrics": True}


# ── export never raises ──────────────────────────────────────────────────────
@pytest.mark.parametrize("endpoint", [
    "http://127.0.0.1:1", "not-a-url", "http://[::1]:1", "",
])
def test_a_failing_export_returns_its_error_instead_of_raising(endpoint):
    """Every caller is on a path where the run is already decided. An exporter that could
    raise would eventually be why a green run reported a failure."""
    assert otel.export({"resourceSpans": []}, endpoint, "traces", timeout=0.3) is not None


def test_a_failing_export_does_not_change_the_command_s_exit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / "runs.jsonl")
    (tmp_path / "runs.jsonl").write_text(json.dumps(POISONED) + "\n", encoding="utf-8")
    monkeypatch.setattr(commands, "load_manifest", lambda *a, **k: {})
    commands.cmd_otel(["--endpoint", "http://127.0.0.1:1", "--recipe", "bugfix"])
    assert "WARN export failed" in capsys.readouterr().out


def test_without_configuration_the_command_sends_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / "runs.jsonl")
    (tmp_path / "runs.jsonl").write_text(json.dumps(POISONED) + "\n", encoding="utf-8")
    monkeypatch.setattr(commands, "load_manifest", lambda *a, **k: {})
    with pytest.raises(SystemExit) as exit_:
        commands.cmd_otel([])
    assert exit_.value.code == 0
    assert "nothing sent" in capsys.readouterr().out


# ── end to end ───────────────────────────────────────────────────────────────
class _Collector(http.server.BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).received.append((self.path, json.loads(body)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


def test_a_real_run_reaches_a_collector_as_a_trace_and_metrics(tmp_path, monkeypatch):
    """The whole path, on a run the orchestrator actually performed: phases measured by #502
    become child spans with the times that were measured, not times laid out to look tidy."""
    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(commands, "load_manifest", lambda *a, **k: {})
    steps = load_steps(parse_frontmatter(resolve_recipe("bugfix")))
    workspace = pathlib.Path(tempfile.mkdtemp())
    providers.run_loop(new_state("bugfix", steps, "fix"), None, "mock", "mock",
                       {"cwd": str(workspace)}, 40, quiet=True)

    _Collector.received = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        commands.cmd_otel([
            "--endpoint", f"http://127.0.0.1:{server.server_address[1]}", "--recipe", "bugfix"])
    finally:
        server.shutdown()

    assert sorted(path for path, _ in _Collector.received) == ["/v1/metrics", "/v1/traces"]
    traces = next(body for path, body in _Collector.received if path.endswith("traces"))
    spans = traces["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert sum(1 for span in spans if "parentSpanId" not in span) == 1
    assert "rig.provider.generator" in {span["name"] for span in spans}
    metrics = next(body for path, body in _Collector.received if path.endswith("metrics"))
    names = {m["name"] for m in metrics["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]}
    assert {"rig.run.count", "rig.run.duration", "rig.provider.duration"} <= names
