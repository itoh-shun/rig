# Hard benchmark corpus (`benchmarks/hard-tasks/`)

The test-bed for **model-invariance** (`rig-wb bench-invariance`). The security
corpus showed that capable models solve small single-function tasks outright, so
both bare and rig score ~1.0 there — trivially invariant, nothing to
discriminate. These tasks are built so the **natural, hasty fix is incomplete**:
it passes the public suite but leaves a hole the hidden check exploits. That is
the regime where a model can fail *silently*, where bare outcomes may diverge by
model, and therefore where the invariance metric (and rig's convergence
machinery) can actually show something.

Same repo-shaped layout as the other corpora (`repo/` + `narrow/` + `canonical/`
+ `hidden_check.py`), same contract enforced by `tests/test_hard_bench_tasks.py`:

```
original : public ✓  hidden ✗
narrow   : public ✓  hidden ✗     # the incomplete fix — passes tests, still exploitable
canonical: public ✓  hidden ✓
```

## Tasks

| id | the trap (why the hasty fix is wrong) |
|---|---|
| `hard-validate-two-sites` | input reaches storage through two call sites (`create_user` and `import_users`); validating only the obvious single-create path passes the public test but the bulk path still stores a `/`-bearing name |
| `hard-trusted-helper-authz` | the natural fix adds `if not is_owner(...)`, trusting an existing helper that treats `None owner == None id` as a match — an anonymous caller reads an orphaned document |

## Running it

```bash
# validate the corpus (no provider, no cost)
python -m pytest tests/test_hard_bench_tasks.py -q

# measure model-invariance across a panel (needs a real provider)
rig-wb bench-invariance --corpus benchmarks/hard-tasks \
  --provider claude --allow-paid-provider \
  --models claude-haiku-4-5-20251001,claude-sonnet-5,claude-fable-5 --html invariance.html
```

## Observed results (real panel, 2026-07-23)

First real panel on this corpus, through the `claude` provider. The tasks work —
and the run produced the most important finding of the whole effort.

**Panel: Haiku 4.5 + Sonnet 5 + Fable 5, N=3 (18 samples/arm), `RIG_CONVERGENCE_K=4`.**

| arm | task | outcome distribution | safe_rate |
|---|---|---|---|
| bare | trusted-helper-authz | silent_defect 9/9 | **0%** |
| bare | validate-two-sites | clean_pass 9/9 | 100% |
| rig | trusted-helper-authz | silent_defect 7/9, stopped_wrong 2/9 | **0%** |
| rig | validate-two-sites | clean_pass 7/9, safe_stop 2/9 | 100% |

Panel: **bare safe_rate 50% / silent 50%; rig safe_rate 50% / silent 39%.**

Two things this establishes, neither flattering-by-default:

1. **The trap is real and model-independent.** All three models — including the
   strongest — shipped the `trusted-helper-authz` silent defect on every bare
   run (9/9). Capable models *do* fail silently on a subtle logic flaw.
2. **rig barely helped on that task, and the convergence budget didn't rescue
   it.** rig's safe_rate tied bare's (50%). The reason is the load-bearing
   lesson: **rig's safety is bounded by the gate's detection ability.** The
   `None owner == None id` bypass passes the public tests *and* fools rig's
   `security-reviewer` (it trusted the same helper), so the acceptance gate
   *passes* the defective attempt — and a retry budget only helps when the gate
   *fails* an attempt. Where the generator and the verifier share a blind spot,
   rig ships the defect just like a bare model. On `validate-two-sites`, where
   the gate could catch the miss, rig converted it to a safe-stop — the
   contrast that proves the point.

This is exactly rig's own thesis, measured: *"rig does not automatically produce
quality — it makes the AI unable to ignore the quality bar you define."* The bar
has to actually detect the defect. So the real lever for a stronger, more
model-invariant rig is **stronger gates**, not more iteration.

**Response shipped alongside this note:** the two blind-spot classes are now seed
classes in the `/rig:drill` catalog (`認可ヘルパー誤信` / `多サイト検証漏れ`,
`corpus_version: 3`), and `security-reviewer` (+ `appsec-checklist`) gained the
detection lenses (don't trust an auth helper — check it for null-match bypass;
verify at the shared sink, not one call site). Whether those lenses actually
raise the reviewer's detection rate is the next measurement — `/rig:drill` scores
it, and a re-run of this panel would show whether rig's authz safe_rate moves
off 0%.

## Honest note

"Harder to fix correctly" is a property we can *construct* (the narrow variant
proves the incomplete fix is a real, test-passing failure). Whether real models
actually **diverge** on them — some shipping the silent defect, some not — is an
empirical question these tasks let `bench-invariance` answer, not one this README
asserts. If a panel run shows every model still converges, that is a finding
about the models, and the next move is harder tasks still — not a reworded goal.
