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

## Honest note

"Harder to fix correctly" is a property we can *construct* (the narrow variant
proves the incomplete fix is a real, test-passing failure). Whether real models
actually **diverge** on them — some shipping the silent defect, some not — is an
empirical question these tasks let `bench-invariance` answer, not one this README
asserts. If a panel run shows every model still converges, that is a finding
about the models, and the next move is harder tasks still — not a reworded goal.
