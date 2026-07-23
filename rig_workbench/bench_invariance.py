"""Model-invariance scoring for rig: how much does the *accepted outcome*
depend on which model drives the harness?

rig's north star (beyond "quality gate") is that the accepted result's quality
is bounded by the **gate**, not by the **model** — so a run driven by a weaker
model should land on the same terminal outcome as a stronger one, or safely
stop, but never silently ship something worse. This module turns that claim
into a number instead of an assertion.

It runs the existing paired benchmark once per model in a panel (reusing
`bench.run_benchmark` unchanged), then measures, per arm (bare vs rig):

  - **agreement** — the fraction of (model x run) samples that reached the
    *same* terminal outcome for a task. 1.0 = every model agreed; low = the
    outcome swings with the model.
  - **panel silent-defect rate** — did any model, on any run, ship a
    passes-public-but-fails-hidden result. For a model-invariant *and safe*
    harness this must be 0.

The headline `model_invariance_score` is the rig arm's mean agreement. The
honest comparison is rig-vs-bare: rig is doing its job when its agreement is
higher (outcomes converge despite the model) and its silent-defect rate is
lower (ideally 0) than the bare arm's.

Infra/invalid samples (environment noise, not model variance) are excluded from
agreement and reported separately, so a flaky CI run does not masquerade as
model sensitivity.
"""

from __future__ import annotations

import math
from collections import Counter
from html import escape
from typing import Mapping

# Terminal classifications that represent a real measurement (not env noise).
_VALID_OUTCOMES = ("clean_pass", "silent_defect", "safe_stop", "stopped_wrong")
_NOISE_OUTCOMES = ("infra_error", "invalid")
_ARMS = ("bare", "rig")


def classify_arm_dict(arm: dict | None) -> str:
    """Classify a *serialized* arm dict, mirroring bench_score.classify_outcome.

    Kept in lock-step with the dataclass classifier so the invariance report and
    the per-provider report never disagree about what a run produced.
    """
    if not isinstance(arm, dict):
        return "invalid"
    completed = arm.get("completed")
    public = arm.get("public_test")
    hidden = arm.get("hidden_check")
    invocation_count = arm.get("invocation_count")
    if (
        not isinstance(completed, bool)
        or not isinstance(public, dict)
        or not isinstance(hidden, dict)
        or not isinstance(invocation_count, int)
    ):
        return "invalid"
    if any((attempt or {}).get("infra_error") for attempt in arm.get("attempts", []) or []):
        return "infra_error"
    if public.get("infra_error") or hidden.get("infra_error"):
        return "infra_error"
    done = completed and bool(public.get("passed"))
    if done and hidden.get("passed"):
        return "clean_pass"
    if done and not hidden.get("passed"):
        return "silent_defect"
    if hidden.get("passed"):
        return "safe_stop"
    return "stopped_wrong"


def _normalized_entropy(counts: list[int]) -> float:
    """Shannon entropy of a distribution, normalized to [0, 1] by log(k)."""
    total = sum(counts)
    if total <= 0:
        return 0.0
    nonzero = [c for c in counts if c > 0]
    if len(nonzero) <= 1:
        return 0.0
    entropy = -sum((c / total) * math.log(c / total) for c in nonzero)
    return entropy / math.log(len(nonzero))


def _score_arm(task_samples: dict[str, list[str]]) -> dict:
    """Given {task_id: [outcome per (model, run) sample]}, score one arm."""
    per_task = []
    agreements: list[float] = []
    valid_total = 0
    silent_total = 0
    clean_total = 0
    noise_total = 0
    for task_id, samples in sorted(task_samples.items()):
        valid = [s for s in samples if s in _VALID_OUTCOMES]
        noise = [s for s in samples if s in _NOISE_OUTCOMES]
        noise_total += len(noise)
        counts = Counter(valid)
        n = len(valid)
        if n == 0:
            per_task.append({
                "task_id": task_id, "samples": len(samples), "valid": 0,
                "agreement": None, "modal_outcome": None,
                "silent_defect_rate": None, "distribution": dict(Counter(samples)),
                "noise": len(noise),
            })
            continue
        modal_outcome, modal_count = counts.most_common(1)[0]
        agreement = modal_count / n
        silent = counts.get("silent_defect", 0)
        clean = counts.get("clean_pass", 0)
        agreements.append(agreement)
        valid_total += n
        silent_total += silent
        clean_total += clean
        per_task.append({
            "task_id": task_id,
            "samples": len(samples),
            "valid": n,
            "agreement": agreement,
            "modal_outcome": modal_outcome,
            "silent_defect_rate": silent / n,
            "success_rate": clean / n,
            "entropy": _normalized_entropy(list(counts.values())),
            "distribution": dict(counts),
            "noise": len(noise),
        })
    mean_agreement = sum(agreements) / len(agreements) if agreements else None
    return {
        "mean_agreement": mean_agreement,
        "panel_silent_defect_rate": (silent_total / valid_total) if valid_total else None,
        "success_rate": (clean_total / valid_total) if valid_total else None,
        "valid_samples": valid_total,
        "noise_samples": noise_total,
        "per_task": per_task,
    }


def score_invariance(summaries: list[dict], *, agreement_threshold: float = 0.8) -> dict:
    """Aggregate per-model bench summaries into a model-invariance report.

    Each element of `summaries` is a `bench.run_benchmark` result for one model
    of the panel (both arms driven by that model). Pure function.
    """
    models = [
        s.get("rig_model") or s.get("model") or f"model-{i}"
        for i, s in enumerate(summaries)
    ]
    arms: dict[str, dict] = {}
    for arm in _ARMS:
        task_samples: dict[str, list[str]] = {}
        for summary in summaries:
            for task in summary.get("tasks", []) or []:
                task_id = task.get("task_id", "?")
                bucket = task_samples.setdefault(task_id, [])
                for run in task.get("runs", []) or []:
                    bucket.append(classify_arm_dict((run.get("arms") or {}).get(arm)))
        arms[arm] = _score_arm(task_samples)

    rig = arms["rig"]
    score = rig["mean_agreement"]
    silent = rig["panel_silent_defect_rate"]
    reasons = []
    if score is None:
        verdict = "inconclusive"
        reasons.append("no valid rig samples across the panel")
    elif silent and silent > 0:
        verdict = "unsafe"
        reasons.append(
            f"rig shipped a silent defect on {silent:.0%} of panel samples — "
            "invariance is moot until that is zero"
        )
    elif score >= agreement_threshold:
        verdict = "model_invariant"
        reasons.append(
            f"rig outcomes agreed on {score:.0%} of panel samples "
            f"(>= {agreement_threshold:.0%}) with zero silent defects"
        )
    else:
        verdict = "model_sensitive"
        reasons.append(
            f"rig outcomes agreed on only {score:.0%} of panel samples "
            f"(< {agreement_threshold:.0%}); the harness does not yet neutralize the model"
        )

    bare = arms["bare"]
    if score is not None and bare["mean_agreement"] is not None:
        delta = score - bare["mean_agreement"]
        reasons.append(
            f"rig agreement {score:.0%} vs bare {bare['mean_agreement']:.0%} "
            f"(rig {'+' if delta >= 0 else ''}{delta:.0%})"
        )

    return {
        "schema_version": 1,
        "kind": "model-invariance",
        "agreement_threshold": agreement_threshold,
        "models": models,
        "panel_size": len(models),
        "verdict": verdict,
        "reasons": reasons,
        "model_invariance_score": score,
        "arms": arms,
    }


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.0%}"
    return str(value)


def render_invariance_html(report: dict) -> str:
    models = ", ".join(escape(str(m)) for m in report.get("models", []))
    verdict = escape(str(report.get("verdict")))
    score = report.get("model_invariance_score")
    reasons = "".join(f"<li>{escape(r)}</li>" for r in report.get("reasons", []))
    rig = report["arms"]["rig"]
    bare = report["arms"]["bare"]

    rows = []
    rig_tasks = {t["task_id"]: t for t in rig["per_task"]}
    for bt in bare["per_task"]:
        tid = bt["task_id"]
        rt = rig_tasks.get(tid, {})
        rows.append(
            "<tr>"
            f"<td>{escape(tid)}</td>"
            f"<td>{_fmt(bt.get('agreement'))}</td>"
            f"<td>{escape(str(bt.get('modal_outcome')))}</td>"
            f"<td>{_fmt(bt.get('silent_defect_rate'))}</td>"
            f"<td>{_fmt(rt.get('agreement'))}</td>"
            f"<td>{escape(str(rt.get('modal_outcome')))}</td>"
            f"<td>{_fmt(rt.get('silent_defect_rate'))}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>rig model-invariance</title>
<style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:2rem;max-width:60rem}}
 .cards{{display:flex;gap:1rem;flex-wrap:wrap;margin:1rem 0}}
 .card{{border:1px solid #ccc;border-radius:8px;padding:.75rem 1rem;min-width:12rem}}
 .label{{font-size:.75rem;color:#666;text-transform:uppercase}}
 .value{{font-size:1.5rem;font-weight:600}}
 table{{border-collapse:collapse;width:100%;margin-top:1rem}}
 th,td{{border:1px solid #ddd;padding:.4rem .6rem;text-align:left}}
 th{{background:#f4f4f4}}
 @media(prefers-color-scheme:dark){{body{{background:#111;color:#eee}}th{{background:#222}}
   .card{{border-color:#444}}th,td{{border-color:#333}}}}
</style></head><body>
<h1>rig model-invariance report</h1>
<p>Panel: {models}</p>
<div class="cards">
 <div class="card"><div class="label">Verdict</div><div class="value">{verdict}</div></div>
 <div class="card"><div class="label">Model-invariance score (rig)</div><div class="value">{_fmt(score)}</div></div>
 <div class="card"><div class="label">rig silent-defect (panel)</div><div class="value">{_fmt(rig.get('panel_silent_defect_rate'))}</div></div>
 <div class="card"><div class="label">bare agreement (panel)</div><div class="value">{_fmt(bare.get('mean_agreement'))}</div></div>
</div>
<ul>{reasons}</ul>
<h2>Per-task agreement (bare vs rig)</h2>
<table><thead><tr>
 <th>Task</th><th>bare agree</th><th>bare modal</th><th>bare silent</th>
 <th>rig agree</th><th>rig modal</th><th>rig silent</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table>
</body></html>
"""


def run_invariance(
    tasks,
    provider: str,
    models: list[str],
    runs: int,
    options: Mapping[str, object] | None = None,
) -> list[dict]:
    """Run the paired benchmark once per panel model (both arms = that model)."""
    from .bench import run_benchmark

    summaries = []
    for model in models:
        summaries.append(run_benchmark(list(tasks), provider, model, runs, options))
    return summaries


def cmd_invariance(argv: list[str]) -> None:
    import argparse
    import json
    import pathlib

    from .bench_tasks import load_tasks

    parser = argparse.ArgumentParser(
        prog="rig-wb bench-invariance",
        description="Measure how model-invariant rig's accepted outcomes are across a model panel.",
    )
    parser.add_argument("--corpus", type=pathlib.Path, help="benchmark task corpus root")
    parser.add_argument("--tasks", nargs="+", default=["all"], help="task ids or all")
    parser.add_argument(
        "--provider",
        choices=["claude", "codex", "ollama", "lmstudio", "mock"],
        default="mock",
    )
    parser.add_argument(
        "--models",
        required=True,
        help="comma-separated model panel, e.g. claude-haiku-4-5-20251001,claude-sonnet-5,claude-fable-5",
    )
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--agreement-threshold", type=float, default=0.8)
    parser.add_argument("--max-steps", type=int, default=14)
    parser.add_argument("--provider-timeout", type=float, default=600)
    parser.add_argument("--rig-timeout", type=float, default=1800)
    parser.add_argument("--check-timeout", type=float, default=60)
    parser.add_argument("--base-url")
    parser.add_argument("--allow-headless-in-cc", action="store_true")
    parser.add_argument("--mock-scenario", choices=["success", "timeout", "malformed", "partial"], default="success")
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--html", type=pathlib.Path)
    args = parser.parse_args(argv)

    if args.runs < 1:
        parser.error("--runs must be at least 1")
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if len(models) < 2:
        parser.error("--models needs at least two models to measure invariance across a panel")

    available = load_tasks(args.corpus)
    requested = list(available) if "all" in args.tasks else args.tasks
    unknown = sorted(set(requested) - set(available))
    if unknown:
        parser.error(f"unknown task id(s): {', '.join(unknown)}")
    selected = [available[task_id] for task_id in requested]
    options = {
        "max_steps": args.max_steps,
        "timeout_s": args.provider_timeout,
        "rig_timeout_s": args.rig_timeout,
        "check_timeout_s": args.check_timeout,
        "base_url": args.base_url,
        "allow_headless_in_cc": args.allow_headless_in_cc,
        "mock_scenario": args.mock_scenario,
    }
    summaries = run_invariance(selected, args.provider, models, args.runs, options)
    report = score_invariance(summaries, agreement_threshold=args.agreement_threshold)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(output, encoding="utf-8")
        print(f"Wrote: {args.out}")
    else:
        print(output)
    if args.html:
        args.html.write_text(render_invariance_html(report), encoding="utf-8")
        print(f"HTML: {args.html}")
