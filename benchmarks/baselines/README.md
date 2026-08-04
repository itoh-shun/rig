# Benchmark baseline schema v1

`rig-wb baseline capture` converts benchmark schema-v2 evidence into a
versioned scorecard. The baseline records the source SHA-256, normalized arm
evidence, provenance, thresholds, and an integrity SHA-256 over the baseline
schema version, source metadata, complete threshold object, scorecard, and
provenance. Editing any of those protected fields invalidates the baseline;
capture a new baseline instead.

Comparison thresholds are:

- `min_samples_per_identity`
- `max_task_success_rate_drop`
- `max_silent_defect_rate_increase`
- `max_safe_stop_rate_increase`
- `max_invalid_sample_rate_increase`
- `max_elapsed_p95_ratio`
- `max_calls_mean_ratio`
- `max_tokens_total_ratio`
- `max_cost_total_ratio`
- `freshness_days`

Calls, tokens, and cost are compared per valid sample. Invalid-sample rate has
its own threshold and is not coupled to the silent-defect threshold.
