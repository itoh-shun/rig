"""Rig's evidence, projected to OpenTelemetry (#501).

This is a projection and never a source of truth. `.rig/runs.jsonl`, the audit log and the
assurance receipts stay authoritative; nothing here re-judges anything, and a run that exports
nothing is a run that happened exactly as its local evidence says. Export failure is a warning
and never touches a gate, a verdict, or an exit code — a monitoring backend that is down must
not be able to change what rig decided.

**No SDK.** Rig has three runtime dependencies and the Issue asking for this says no monitoring
vendor belongs in its core, so this speaks OTLP/HTTP with JSON bodies over `urllib`, the same
way `run_http_provider` already talks to model endpoints. Any collector accepts it at
`/v1/traces` and `/v1/metrics`.

**The projection is an allowlist, and that is the whole redaction story.** Every field exported
is named in this file. Nothing is copied wholesale and then filtered, because a filter has to
be right about every field that will ever exist, and the first one somebody adds without
thinking about it ships by default. This matters concretely: `runs.jsonl` verdicts carry an
`anchor` — free text a model wrote, which routinely holds a file path and could hold anything
it was looking at. A denylist would have to know that; an allowlist simply never asks for it.
So no prompt, no response body, no diff, no path, and no verdict prose leaves this module,
and a new field in the record is absent from telemetry until somebody decides it is safe.

**Only measured timings are exported.** Phase spans come from intervals `perf` actually
recorded (`perf.record_span`); a phase with a duration but no interval becomes a metric rather
than a span, because laying aggregates end-to-end to draw a tree would invent an ordering
nobody observed. TTFT and generation rate are absent entirely: no provider path in rig measures
them today, and a zero would be a claim.

Identifiers are derived from the record's own content, so exporting the same log twice produces
the same trace rather than a second copy of the same run under a new id.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import urllib.error
import urllib.request

from . import perf

SCHEMA_URL = "https://opentelemetry.io/schemas/1.27.0"

#: Span names, from rig's phase vocabulary. Named here rather than derived so a phase renamed
#: in `perf` cannot silently rename a span somebody built a dashboard on.
PHASE_SPAN_NAMES = {
    "risk_assess": "rig.risk_assess",
    "auto_route": "rig.auto_route",
    "provider_generator": "rig.provider.generator",
    "provider_verifier": "rig.provider.verifier",
    "checks": "rig.test",
    "gate": "rig.acceptance_gate",
    "artifact": "rig.artifact",
}

#: Record fields that may become span attributes, and the attribute each becomes. The complete
#: list — anything absent here is not exported, whatever else the record grows.
RUN_ATTRIBUTES = {
    "recipe": "rig.recipe",
    "final": "rig.gate.status",
    "backend": "rig.backend",
    "invoker": "rig.invoker",
    "failure_mode": "rig.failure_mode",
}

_STATUS_OK, _STATUS_ERROR = 1, 2
#: Run outcomes that are an error on the trace. `ESCALATE` is deliberately among them: a run
#: that stopped for a human is not a failure of rig, but it is not a completed task either, and
#: a dashboard that showed it as success would be counting unfinished work as done.
_ERROR_FINALS = {"ESCALATE", "BLOCKED", "STOPPED"}


def _hex(*parts: object, width: int) -> str:
    digest = hashlib.sha256("\x00".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return digest[:width]


def _unix_nano(ts: str) -> int | None:
    """The record's own timestamp as OTLP wants it, or None if it cannot be read.

    None rather than "now": a record with an unreadable timestamp would otherwise be exported
    as if the run had just happened, which is worse than not exporting it.
    """
    try:
        return int(datetime.datetime.fromisoformat(ts).timestamp() * 1_000_000_000)
    except (TypeError, ValueError):
        return None


def _attrs(pairs: dict) -> list[dict]:
    out = []
    for key, value in pairs.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out.append({"key": key, "value": {"boolValue": value}})
        elif isinstance(value, int):
            out.append({"key": key, "value": {"intValue": str(value)}})
        elif isinstance(value, float):
            out.append({"key": key, "value": {"doubleValue": value}})
        else:
            out.append({"key": key, "value": {"stringValue": str(value)}})
    return out


def _resource(service_name: str) -> dict:
    return {"attributes": _attrs({"service.name": service_name, "telemetry.sdk.name": "rig",
                                  "telemetry.sdk.language": "python"})}


def project_traces(records: list[dict], *, service_name: str = "rig") -> dict:
    """Runs as traces: one root span each, with a child span per measured interval.

    A record with no readable timestamp is skipped rather than placed at the current time, and
    a record with no measured intervals still produces its root span — the run happened, and
    saying so with no children is honest about what was watched.
    """
    spans: list[dict] = []
    for record in records:
        start_nano = _unix_nano(record.get("ts", ""))
        if start_nano is None:
            continue
        measured = record.get("perf") or {}
        total_ms = measured.get("total_ms")
        trace_id = _hex(record.get("ts"), record.get("recipe"), record.get("invoker"),
                        record.get("final"), width=32)
        root_id = _hex(trace_id, "run", width=16)
        spans.append({
            "traceId": trace_id, "spanId": root_id, "name": "rig.run", "kind": 1,
            "startTimeUnixNano": str(start_nano),
            "endTimeUnixNano": str(start_nano + int((total_ms or 0.0) * 1_000_000)),
            "attributes": _attrs({
                **{attribute: record.get(field)
                   for field, attribute in RUN_ATTRIBUTES.items()},
                "rig.steps.total": record.get("steps_total"),
                "rig.steps.passed": record.get("steps_passed"),
                "rig.retries": record.get("retries"),
                "rig.escalated_at": record.get("escalated_at"),
                # Reported as an attribute, not as a failed span: a broken performance budget
                # never changed the run's outcome and must not look as though it did (#502).
                "rig.perf.budget_broken": bool(record.get("perf_budget_broken")) or None,
            }),
            "status": {"code": _STATUS_ERROR if record.get("final") in _ERROR_FINALS
                       else _STATUS_OK},
        })
        for index, interval in enumerate(measured.get("spans") or []):
            phase = interval.get("phase")
            if phase not in PHASE_SPAN_NAMES:
                continue
            child_start = start_nano + int(interval["start_ms"] * 1_000_000)
            spans.append({
                "traceId": trace_id, "spanId": _hex(trace_id, phase, index, width=16),
                "parentSpanId": root_id, "name": PHASE_SPAN_NAMES[phase], "kind": 1,
                "startTimeUnixNano": str(child_start),
                "endTimeUnixNano": str(start_nano + int(interval["end_ms"] * 1_000_000)),
                "attributes": _attrs({
                    "rig.phase": phase,
                    "rig.role": ("generator" if phase == "provider_generator" else
                                 "verifier" if phase == "provider_verifier" else None),
                }),
                "status": {"code": _STATUS_OK},
            })
    return {"resourceSpans": [{"resource": _resource(service_name),
                               "schemaUrl": SCHEMA_URL,
                               "scopeSpans": [{"scope": {"name": "rig.orchestrate"},
                                               "spans": spans}]}]}


def _sum(name: str, unit: str, points: list[dict]) -> dict:
    return {"name": name, "unit": unit,
            "sum": {"dataPoints": points, "aggregationTemporality": 1, "isMonotonic": True}}


def project_metrics(records: list[dict], *, service_name: str = "rig") -> dict:
    """Runs as metrics, with labels a dashboard can group by and nothing it cannot.

    Labels are the recipe, the outcome, and the provider — low cardinality on purpose. Task
    ids and step ids belong on traces; putting them here is how a metrics backend becomes
    unaffordable, and the Issue asks for them not to be.

    Three figures the Issue lists are deliberately absent. **Cached tokens**: the usage rollup
    records prompt and completion only, so there is nothing to report and a zero would read as
    "no cache hits" rather than "not measured". **Cost**: nothing in the record carries one —
    it would have to come from a price table this module was not given, and an estimate
    exported as a measurement is the failure the whole file is arranged against. **TTFT and
    output tokens/sec**: no provider path in rig observes them.
    """
    points: dict[str, list[dict]] = {}

    def add(metric: str, value, labels: dict, when: int) -> None:
        if value is None:
            return
        key = "asInt" if isinstance(value, int) and not isinstance(value, bool) else "asDouble"
        points.setdefault(metric, []).append({
            key: value if key == "asDouble" else int(value),
            "startTimeUnixNano": str(when), "timeUnixNano": str(when),
            "attributes": _attrs(labels)})

    for record in records:
        when = _unix_nano(record.get("ts", ""))
        if when is None:
            continue
        recipe, final = record.get("recipe"), record.get("final")
        base = {"rig.recipe": recipe, "rig.gate.status": final}
        add("rig.run.count", 1, base, when)
        failed = (record.get("steps_total") or 0) - (record.get("steps_passed") or 0)
        add("rig.gate.failure_count", failed, base, when)
        add("rig.run.retries", record.get("retries"), base, when)

        measured = record.get("perf") or {}
        add("rig.run.duration", measured.get("total_ms"), base, when)
        add("rig.provider.duration", measured.get("provider_ms"), base, when)
        # Only when the subtraction was allowed to happen: `perf` withholds this whenever a
        # provider call went untimed, and a metric that filled the gap with a plausible number
        # would undo that refusal one layer down.
        add("rig.overhead.duration", measured.get("rig_overhead_ms"), base, when)
        add("rig.context.bytes", measured.get("context_bytes_emitted"), base, when)

        partial = measured.get("token_usage_partial")
        for provider, usage in (record.get("token_usage") or {}).items():
            labels = {**base, "gen_ai.provider.name": provider,
                      # Says the totals cover part of the run: the CLI providers report no
                      # usage at all, and a reader summing these without knowing that would
                      # conclude rig got cheaper when it only got quieter.
                      "rig.tokens.partial": True if partial else None}
            add("rig.tokens.input", usage.get("prompt_tokens"), labels, when)
            add("rig.tokens.output", usage.get("completion_tokens"), labels, when)
            add("rig.provider.calls", usage.get("calls"), labels, when)

    metrics = [
        _sum("rig.run.count", "1", points.get("rig.run.count", [])),
        _sum("rig.run.retries", "1", points.get("rig.run.retries", [])),
        _sum("rig.gate.failure_count", "1", points.get("rig.gate.failure_count", [])),
        _sum("rig.run.duration", "ms", points.get("rig.run.duration", [])),
        _sum("rig.provider.duration", "ms", points.get("rig.provider.duration", [])),
        _sum("rig.overhead.duration", "ms", points.get("rig.overhead.duration", [])),
        _sum("rig.context.bytes", "By", points.get("rig.context.bytes", [])),
        _sum("rig.tokens.input", "1", points.get("rig.tokens.input", [])),
        _sum("rig.tokens.output", "1", points.get("rig.tokens.output", [])),
        _sum("rig.provider.calls", "1", points.get("rig.provider.calls", [])),
    ]
    return {"resourceMetrics": [{"resource": _resource(service_name),
                                 "schemaUrl": SCHEMA_URL,
                                 "scopeMetrics": [{"scope": {"name": "rig.orchestrate"},
                                                   "metrics": [m for m in metrics
                                                               if m["sum"]["dataPoints"]]}]}]}


def export(payload: dict, endpoint: str, signal: str, *, timeout: float = 10.0) -> str | None:
    """POST one OTLP payload. Returns an error description, or None on success.

    Returns rather than raises, everywhere, including on a malformed endpoint. Every caller of
    this is on a path where the run has already been decided, and an exporter that could raise
    would eventually be the reason a green run reported a failure.
    """
    url = (endpoint or "").rstrip("/") + f"/v1/{signal}"
    try:
        # Building the Request is inside the try, not before it: `Request(...)` raises
        # ValueError on a URL with no scheme, so a mistyped endpoint used to escape this
        # function despite the promise above — the one input most likely to be wrong.
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status >= 300:
                return f"{url}: HTTP {response.status}"
    except urllib.error.HTTPError as error:
        return f"{url}: HTTP {error.code}"
    except (urllib.error.URLError, OSError, ValueError) as error:
        return f"{url}: {type(error).__name__}: {error}"
    return None


def settings(manifest: dict) -> dict:
    """The `[observability]` block, with export off unless it was turned on.

    Off by default and off on anything malformed. Telemetry that started flowing because a
    config file was mistyped is a data-egress incident, so the ambiguous case does nothing.
    """
    block = manifest.get("observability")
    if not isinstance(block, dict) or not block.get("enabled"):
        return {"enabled": False}
    endpoint = block.get("otlp_endpoint")
    if not isinstance(endpoint, str) or not endpoint.startswith(("http://", "https://")):
        return {"enabled": False, "reason": "observability.otlp_endpoint is not an http(s) URL"}
    return {
        "enabled": True,
        "otlp_endpoint": endpoint,
        "service_name": block.get("service_name") or "rig",
        "export_traces": block.get("export_traces", True) is not False,
        "export_metrics": block.get("export_metrics", True) is not False,
    }


#: The phases a span can be drawn for, re-exported so a caller does not have to import two
#: modules to know what it will see.
KNOWN_PHASES = tuple(perf.PHASES)
