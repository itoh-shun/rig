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


# ── what the Issue asked to compare, and could not (#501, the three unmet criteria) ──
PROVIDERS = {**POISONED, "providers": {"generator": "codex", "verifier": ["claude", "ollama"],
                                       "model": "gpt-5-codex"}}


def test_provider_verifier_and_model_are_on_the_root_span():
    """"Provider/model/role can be compared without reading Rig-specific JSON files": the
    record used to carry none of them at the top level, so nothing here could label a run by
    who generated it. Names of configuration, not text a model wrote."""
    span = otel.project_traces([PROVIDERS])["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    attrs = {a["key"]: a["value"] for a in span["attributes"]}
    assert attrs["gen_ai.provider.name"] == {"stringValue": "codex"}
    assert attrs["gen_ai.request.model"] == {"stringValue": "gpt-5-codex"}
    assert attrs["rig.verifier.provider"] == {"stringValue": "claude,ollama"}


def test_the_generator_labels_every_run_level_metric_point():
    """The token metrics keep the provider that *reported* the usage — an HTTP verifier's
    tokens are that verifier's, not the generator's — and every other point is labelled with
    who generated the run, so a dashboard can group by it."""
    payload = otel.project_metrics([PROVIDERS])
    for metric in payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]:
        if metric["name"].startswith("rig.tokens.") or metric["name"] == "rig.provider.calls":
            continue
        for point in metric["sum"]["dataPoints"]:
            labels = {a["key"]: a["value"] for a in point["attributes"]}
            assert labels["gen_ai.provider.name"] == {"stringValue": "codex"}, metric["name"]


def test_a_record_without_providers_exports_no_provider_attributes():
    """Older records have none, and an absent provider is not "unknown provider"."""
    span = otel.project_traces([POISONED])["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    keys = {a["key"] for a in span["attributes"]}
    assert not keys & {"gen_ai.provider.name", "gen_ai.request.model", "rig.verifier.provider"}


def _metric_values(payload: dict, name: str) -> list:
    for metric in payload["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]:
        if metric["name"] == name:
            return [p.get("asInt", p.get("asDouble")) for p in metric["sum"]["dataPoints"]]
    return []


def test_a_forced_accept_is_visible_and_an_ordinary_one_is_silent():
    """"Gate results and force overrides appear in telemetry": the gate status was there and
    the override was not, so a dashboard could not tell an accept the gate allowed from one a
    person pushed through. An attribute and a counter, never a failed span — the run's
    outcome did not change, and the trace must not say it did."""
    forced = {**POISONED, "forced": True}
    span = otel.project_traces([forced])["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert {"key": "rig.accept.force", "value": {"boolValue": True}} in span["attributes"]
    assert span["status"] == {"code": otel._STATUS_OK}
    assert _metric_values(otel.project_metrics([forced]), "rig.force.count") == [1]

    plain_span = otel.project_traces([POISONED])["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert "rig.accept.force" not in {a["key"] for a in plain_span["attributes"]}
    assert _metric_values(otel.project_metrics([POISONED]), "rig.force.count") == []


def test_sensor_findings_are_counted_and_their_excerpts_are_not_exported():
    """"Prompt-injection, secret, and destructive-operation findings can be counted without
    exporting sensitive excerpts": the count is what leaves, the masked excerpt in
    acceptance.json is not part of the record, and a sensor that recorded nothing yields no
    point rather than a zero."""
    record = {**POISONED, "findings": {"secret": 2, "destructive": 1},
              "secret_findings": ["src/config.py:3 [aws] AKIA****"]}
    payload = otel.project_metrics([record])
    assert _metric_values(payload, "rig.secret.detection_count") == [2]
    assert _metric_values(payload, "rig.destructive.detection_count") == [1]
    assert _metric_values(payload, "rig.injection.detection_count") == []
    assert "AKIA" not in json.dumps(payload) and "config.py" not in json.dumps(payload)
