"""Where a run's time went, by phase (#502).

A run has always been timed as one number, and one number cannot answer the question worth
asking: did rig get slower, or did the provider? Those want opposite responses — the first is
a regression to fix, the second is weather. This records the phases separately so a report can
name which one moved.

Three rules shape everything here.

**An unmeasured phase is never zero.** A phase that did not run and a phase that ran in no time
are different facts, and a report that renders both as `0ms` invites the reader to conclude
that work vanished when it merely was not watched. Only phases that were actually timed appear
in `phases`; anything expected and absent is named in `unmeasured`, and any figure derived from
an absent phase is withheld rather than computed from a gap.

**Overhead is a subtraction, so it inherits every gap in what it subtracts.** `rig_overhead_ms`
is the total minus the time spent waiting on providers, which is only meaningful while every
provider call was timed. When one was not, the honest answer is that overhead is unknown, and
this module says so rather than reporting a number that quietly counts a provider's latency as
rig's own.

**A budget may only name figures rig itself controls.** Provider latency is deliberately absent
from the vocabulary: a budget that failed CI over somebody else's network would teach people to
delete the budget. What is left is the time rig spends on its own work, and the bytes of prompt
it chooses to emit — both of which a change to this repository can regress, and neither of
which depends on the weather.

The accumulator lives on `cfg`, exactly as `_token_usage` does, so a caller owns its lifetime
and one run's timings never blend into another's.
"""

from __future__ import annotations

import contextlib
import threading
import time

#: The phases of a run, as the orchestrator actually executes them. Listed so a report can say
#: what it did not see: without a roster there is no difference between "the gate took no time"
#: and "nobody timed the gate". Every name here has an instrumented call site — a phase nobody
#: measures would sit in `unmeasured` on every run and teach the reader to skip the field.
PHASES = (
    "risk_assess",
    "auto_route",
    "provider_generator",
    "provider_verifier",
    "checks",
    "gate",
    "artifact",
)

#: The phases that are somebody else's latency. What is left after these is rig's own overhead,
#: which is the only part of the clock a budget can fairly hold rig to.
PROVIDER_PHASES = ("provider_generator", "provider_verifier")

_LOCK = threading.Lock()


def accumulator() -> dict:
    """A fresh per-run accumulator, to be placed on `cfg` under `_perf`."""
    return {"phases": {}, "untimed_calls": 0, "context_bytes": 0, "context_calls": 0}


def record(cfg: dict, phase: str, seconds: float) -> None:
    """Add one observation of `phase`. Silently inert when the caller kept no accumulator.

    Inert rather than raising, because timing is telemetry: a run must not fail because
    nobody wanted its numbers. The same reason `telemetry_append` swallows a write failure.
    """
    acc = cfg.get("_perf") if isinstance(cfg, dict) else None
    if acc is None or phase not in PHASES:
        return
    with _LOCK:
        entry = acc["phases"].setdefault(phase, {"ms": 0.0, "calls": 0})
        entry["ms"] += seconds * 1000.0
        entry["calls"] += 1


def record_untimed(cfg: dict) -> None:
    """Note that a provider call happened whose duration was not observed.

    One of these is enough to make `rig_overhead_ms` a guess, and a guess presented as a
    measurement is the failure this module exists to avoid.
    """
    acc = cfg.get("_perf") if isinstance(cfg, dict) else None
    if acc is None:
        return
    with _LOCK:
        acc["untimed_calls"] += 1


def record_context_bytes(cfg: dict, prompt: str) -> None:
    """Add one prompt's UTF-8 size to what this run emitted to providers.

    Measured at the point of sending rather than estimated from the composed harness, because
    what a step actually put in front of a model is the number a context budget is about —
    a prompt assembled and then not sent has cost nobody anything.
    """
    acc = cfg.get("_perf") if isinstance(cfg, dict) else None
    if acc is None:
        return
    size = len((prompt or "").encode("utf-8"))
    with _LOCK:
        acc["context_bytes"] += size
        acc["context_calls"] += 1


@contextlib.contextmanager
def timed(cfg: dict, phase: str):
    """Time a block as `phase`, recording it even when the block raises.

    Recording on the way out of a failure matters: the slow path is usually the interesting
    one, and dropping its timing would leave a regression report describing only the runs
    that went well.
    """
    start = time.monotonic()
    try:
        yield
    finally:
        record(cfg, phase, time.monotonic() - start)


def summary(cfg: dict, total_ms: float | None = None,
            token_usage: dict | None = None) -> dict | None:
    """What was measured, what was not, and rig's own share — or None if nothing was timed.

    `total_ms` is the wall clock around the whole run, which only its caller can know.
    Without it there is no total to subtract from, so `rig_overhead_ms` is withheld rather
    than invented from the sum of the phases (that sum is not the total: it omits everything
    between phases, and reporting it as the total would understate rig's own overhead —
    flattering, and wrong).
    """
    acc = cfg.get("_perf") if isinstance(cfg, dict) else None
    if acc is None or not acc["phases"]:
        return None
    with _LOCK:
        phases = {name: {"ms": round(entry["ms"], 3), "calls": entry["calls"]}
                  for name, entry in sorted(acc["phases"].items())}
        untimed = acc["untimed_calls"]
        context_bytes, context_calls = acc["context_bytes"], acc["context_calls"]
    out: dict = {
        "phases": phases,
        "unmeasured": [name for name in PHASES if name not in phases],
    }
    if total_ms is not None:
        out["total_ms"] = round(total_ms, 3)
    if context_calls:
        # Only reported once something was actually sent. A run that called no provider emitted
        # no context, and saying "0 bytes" would read as a measurement of frugality rather than
        # of absence.
        out["context_bytes_emitted"] = context_bytes
        out["context_calls"] = context_calls
    provider_calls = sum(phases[name]["calls"] for name in PROVIDER_PHASES if name in phases)
    out.update(_token_totals(token_usage or {}, provider_calls + untimed))
    provider_ms = sum(phases[name]["ms"] for name in PROVIDER_PHASES if name in phases)
    if untimed:
        # Overhead is total minus provider time. An untimed provider call is time that would
        # land in overhead without belonging to it, so the subtraction is refused and the
        # reason is carried rather than left for the reader to infer from a missing key.
        out["rig_overhead_unmeasured"] = f"{untimed} provider call(s) were not timed"
    elif total_ms is not None:
        out["provider_ms"] = round(provider_ms, 3)
        out["rig_overhead_ms"] = round(max(total_ms - provider_ms, 0.0), 3)
    return out


def _token_totals(token_usage: dict, provider_calls: int) -> dict:
    """Run totals from the per-provider usage rollup, and whether they cover the whole run.

    Only HTTP providers report structured usage; the CLI-based ones (claude, codex) expose
    none, and rig deliberately does not estimate it (#271/#296). So on a mixed run the totals
    are real but partial, and a token budget checked against them would be checking a fraction
    of the work while reading as a full pass. The counts are reported either way and
    `token_usage_partial` says which case this is — the same rule `rig_overhead_unmeasured`
    follows, applied to the other axis.
    """
    covered = sum(entry.get("calls", 0) for entry in token_usage.values())
    if not covered:
        return {}
    totals = {
        "input_tokens": sum(entry.get("prompt_tokens", 0) for entry in token_usage.values()),
        "output_tokens": sum(entry.get("completion_tokens", 0) for entry in token_usage.values()),
        "token_calls": covered,
    }
    if covered < provider_calls:
        totals["token_usage_partial"] = (
            f"{covered} of {provider_calls} provider call(s) reported usage")
    return totals


#: The summary fields a comparison reports on their own, beside the per-phase deltas. Both are
#: rig's own to answer for; neither moves because a provider had a slow day.
SCALARS = ("rig_overhead_ms", "context_bytes_emitted", "input_tokens", "output_tokens")


def compare(baseline: dict, current: dict, *, tolerance_pct: float = 20.0) -> dict:
    """Which phases moved between two summaries, worst first.

    Reports a phase present in one side and absent in the other as a change in what was
    measured rather than as a delta: a phase that stopped being timed did not become
    infinitely fast, and a comparison that renders it as one is worse than no comparison.
    """
    before, after = baseline.get("phases") or {}, current.get("phases") or {}
    deltas, appeared, disappeared = [], [], []
    for name in sorted(set(before) | set(after)):
        if name not in after:
            disappeared.append(name)
            continue
        if name not in before:
            appeared.append(name)
            continue
        was, now = before[name]["ms"], after[name]["ms"]
        change = now - was
        pct = (change / was * 100.0) if was else None
        deltas.append({"phase": name, "baseline_ms": round(was, 3), "current_ms": round(now, 3),
                       "delta_ms": round(change, 3),
                       "delta_pct": None if pct is None else round(pct, 1)})
    deltas.sort(key=lambda item: item["delta_ms"], reverse=True)

    result: dict = {"phases": deltas}
    if appeared:
        result["newly_measured"] = appeared
    if disappeared:
        # Louder than a delta on purpose: losing a measurement can look like an improvement
        # in every total it used to be part of.
        result["stopped_being_measured"] = disappeared

    # What a gate may fail on, and what it may only report. A provider phase moving is the
    # weather — a busier API, a slower link, a different model behind the same name — and a
    # gate that failed on it would be switched off within a month, taking the phases rig can
    # actually answer for with it. They are still compared, under their own key, because
    # "which half got slower" is the question this module exists to answer.
    result["regressed"] = [item for item in deltas
                           if item["phase"] not in PROVIDER_PHASES
                           and item["delta_pct"] is not None and item["delta_pct"] > tolerance_pct]
    result["provider_drift"] = [item for item in deltas if item["phase"] in PROVIDER_PHASES]
    for key in SCALARS:
        was, now = baseline.get(key), current.get(key)
        if was is None or now is None:
            # One side could not measure it. "Unknown" is the answer; a comparison against a
            # number that was never taken would be a comparison with nothing.
            result[f"{key}_comparable"] = False
            continue
        result[f"{key}_comparable"] = True
        result[key] = {"baseline": was, "current": now, "delta": round(now - was, 3),
                       "delta_pct": round((now - was) / was * 100.0, 1) if was else None}
    return result


def render(comparison: dict) -> list[str]:
    """The comparison as lines, leading with the phase that moved most."""
    lines: list[str] = []
    for key, label, unit in (("rig_overhead_ms", "rig overhead", "ms"),
                             ("context_bytes_emitted", "context emitted", " bytes")):
        moved = comparison.get(key)
        if comparison.get(f"{key}_comparable") and moved:
            sign = "+" if moved["delta"] >= 0 else ""
            pct = "" if moved["delta_pct"] is None else f" ({sign}{moved['delta_pct']}%)"
            lines.append(f"{label}: {moved['baseline']}{unit} → {moved['current']}{unit}{pct}")
        elif f"{key}_comparable" in comparison:
            lines.append(f"{label}: not comparable (one side was not measured)")
    if comparison.get("stopped_being_measured"):
        lines.append("no longer measured: "
                     + ", ".join(comparison["stopped_being_measured"])
                     + "  — a phase that stopped being timed did not get faster")
    if comparison.get("newly_measured"):
        lines.append("newly measured: " + ", ".join(comparison["newly_measured"]))
    for item in comparison.get("phases", [])[:5]:
        sign = "+" if item["delta_ms"] >= 0 else ""
        pct = "" if item["delta_pct"] is None else f" ({sign}{item['delta_pct']}%)"
        # Naming the provider rows as ungated in the output itself, so nobody reads a large
        # number here as something the gate let through.
        note = "  [not gated: provider]" if item["phase"] in PROVIDER_PHASES else ""
        lines.append(f"  {item['phase']:<20} {sign}{item['delta_ms']}ms{pct}{note}")
    return lines


#: budget key -> (summary field, human label). Deliberately short, and deliberately without a
#: provider-latency entry: see this module's docstring.
BUDGET_LIMITS = {
    "max_rig_overhead_ms": ("rig_overhead_ms", "rig overhead"),
    "max_context_bytes": ("context_bytes_emitted", "context emitted"),
    "max_output_tokens": ("output_tokens", "output tokens"),
}

#: budget key -> (summary field, human label). Percentage limits, checked against a baseline
#: rather than against a run on its own. Same vocabulary rule: everything here is a figure rig
#: decides — how much it asks for and how much work it does around the call.
REGRESSION_LIMITS = {
    "max_overhead_regression_pct": ("rig_overhead_ms", "rig overhead"),
    "max_context_regression_pct": ("context_bytes_emitted", "context emitted"),
    "max_token_regression_pct": ("output_tokens", "output tokens"),
}


def check_budget(summary_: dict, budget: dict) -> list[str]:
    """Which budget limits this run broke, as sentences. Empty means it stayed inside.

    Only limits that can be checked are checked: a budget naming a figure this run could not
    measure is reported as unenforced rather than passed, because a limit nobody could test is
    not a limit that held. An unknown key is reported too — a budget with a typo in it silently
    enforcing nothing is the failure mode this whole module is built against.
    """
    broken: list[str] = []
    for key in sorted(budget):
        if key in REGRESSION_LIMITS:
            continue  # checked by check_regression, which has a baseline to compare against
        if key not in BUDGET_LIMITS:
            broken.append(f"unknown budget key {key!r} (known: "
                          + ", ".join(sorted(BUDGET_LIMITS) + sorted(REGRESSION_LIMITS)) + ")")
            continue
        field, label = BUDGET_LIMITS[key]
        if field in ("input_tokens", "output_tokens") and summary_.get("token_usage_partial"):
            # Real, but covering only part of the run: the CLI providers report no usage at
            # all. A limit checked against a fraction of the work would pass for a reason that
            # has nothing to do with the work staying inside it.
            broken.append(f"{label}: budget {key}={budget[key]} was not enforced "
                          f"({summary_['token_usage_partial']})")
            continue
        observed = summary_.get(field)
        if observed is None:
            broken.append(f"{label}: budget {key}={budget[key]} was not enforced "
                          f"(this run did not measure {field})")
            continue
        if observed > budget[key]:
            broken.append(f"{label}: {observed} exceeds {key}={budget[key]}")
    return broken


def aggregate(summaries: list[dict]) -> dict | None:
    """One summary standing for several runs, per phase, or None if none of them measured.

    The middle value, not the mean. A laptop that slept, a cold page cache, a CI runner with a
    noisy neighbour — each produces one run several times slower than the rest, and a mean lets
    that single run set the baseline everything afterwards is judged against. The median moves
    only when most runs move, which is the definition of a regression worth gating on.

    A phase is aggregated over the runs that measured it, and `runs` records how many those
    were: a phase seen once in fifty is a thin basis for a threshold, and the reader is owed
    that fact rather than a confident number. `unmeasured` names the phases no run measured at
    all — the same rule as a single summary, applied to a set.
    """
    per_phase: dict[str, list[float]] = {}
    scalars: dict[str, list[float]] = {}
    for item in summaries:
        for name, entry in (item.get("phases") or {}).items():
            per_phase.setdefault(name, []).append(entry["ms"])
        for key in SCALARS:
            if item.get(key) is not None:
                scalars.setdefault(key, []).append(item[key])
    if not per_phase:
        return None
    out: dict = {
        "phases": {name: {"ms": round(_median(values), 3), "runs": len(values)}
                   for name, values in sorted(per_phase.items())},
        "unmeasured": [name for name in PHASES if name not in per_phase],
        "runs": len(summaries),
    }
    for key, values in scalars.items():
        out[key] = round(_median(values), 3)
        # How many runs stand behind each scalar, since a run that could not measure its
        # overhead contributes to `runs` without contributing to this.
        out[f"{key}_runs"] = len(values)
    return out


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def check_regression(comparison: dict, budget: dict,
                     *, default_tolerance_pct: float = 20.0) -> list[str]:
    """Which declared percentage limits this comparison broke, as sentences.

    Separate from `check_budget` because these need two runs, not one: a percentage is a
    statement about a change, and a run compared against nothing has not changed. A limit whose
    figure one side could not measure is reported as unenforced, for the same reason an
    absolute one is — the comparison was against nothing, and nothing always fits.
    """
    broken: list[str] = []
    for key, (field, label) in REGRESSION_LIMITS.items():
        if key not in budget:
            continue
        if not comparison.get(f"{field}_comparable"):
            broken.append(f"{label}: budget {key}={budget[key]} was not enforced "
                          f"(one side did not measure {field})")
            continue
        moved = comparison[field]
        if moved["delta_pct"] is not None and moved["delta_pct"] > budget[key]:
            broken.append(f"{label}: +{moved['delta_pct']}% "
                          f"({moved['baseline']} → {moved['current']}) "
                          f"exceeds {key}={budget[key]}")
    return broken
