# Security benchmark corpus (`benchmarks/security-tasks/`)

The evidence behind the security pack's "with vs without rig" claim. It runs on
the **same paired benchmark runner** as the default corpus (`rig_workbench/bench.py`),
so the metric is the one rig already reports for bugfixes: the **silent-defect
rate** — a diff that passes the visible tests but fails a hidden check.

Security is where that gap is widest. Asked to "fix this vulnerability," a bare
agent tends to write the *plausible* fix — a denylist, a specific-payload block,
an authentication check that forgets authorization. It passes the public suite
and looks done. A slightly different attack still lands. That is a **silent
security defect**, and shipping it is worse than shipping an obvious bug.

## Task shape

Each task mirrors the default corpus layout:

| Path | Role |
|---|---|
| `repo/` | vulnerable starting code + a **public test** that only exercises benign behavior (so it passes on *every* variant) |
| `narrow/` | the plausible-but-still-vulnerable fix a bare agent tends to write — public test passes, exploit still lands |
| `canonical/` | the real, root-cause fix — public test passes **and** the exploit fails |
| `hidden_check.py` | the exploit: fails on `repo` and `narrow`, passes on `canonical` |

The invariant every task holds (enforced by `tests/test_security_bench_tasks.py`):

```
original : public ✓  hidden ✗      # vulnerable
narrow   : public ✓  hidden ✗      # looks fixed, still exploitable  → silent defect
canonical: public ✓  hidden ✓      # actually fixed
```

## Tasks

| id | class | the narrow trap |
|---|---|---|
| `sec-path-traversal-absolute` | CWE-22 path traversal | blocks `..` but an absolute path bypasses `os.path.join` |
| `sec-command-injection-shell` | CWE-78 command injection | denylists `; & \| \` \n` but `$(...)` substitution slips through |
| `sec-ssrf-private-ip` | CWE-918 SSRF | blocks `localhost`/`127.0.0.1` literals but not `169.254.169.254`, `[::1]`, private ranges |
| `sec-unsalted-password-hash` | CWE-916 weak hashing | adds one app-wide salt — identical passwords still collide |
| `sec-weak-reset-token` | CWE-338 weak RNG | swaps to `random.getrandbits` — still a seedable PRNG |
| `sec-idor-doc-access` | CWE-639 IDOR | requires a logged-in user but never checks ownership |
| `sec-tenant-isolation-read` | CWE-1230 multi-tenant read | requires a tenant context but scopes the lookup by id only, not tenant |
| `sec-tenant-isolation-write` | CWE-1230 multi-tenant write | same missing-scope trap on an update — one tenant closes another's record |

## Running it

```bash
# bare agent vs rig, on the security corpus (needs a real provider)
rig-wb bench --corpus benchmarks/security-tasks --provider claude --html sec-report.html

# validate the corpus itself (no provider, no cost)
python -m pytest tests/test_security_bench_tasks.py -q
```

The HTML report's headline cards are **bare silent-defect rate**, **rig
silent-defect rate**, and the **relative reduction** between them, plus rig's
**safe-stop rate** — the fraction of runs where rig recognized it could not
prove the fix was safe and *discarded* rather than shipping a silent defect.
That last number is the point: rig's win here is not "writes better exploits,"
it is **refusing to ship a vulnerability it cannot prove is closed.**

## Observed results so far (honest pilot, 2026-07-23)

rig's own claim is that gate efficacy is **measured, not asserted** — so the
measured result belongs here even when it is unflattering. Pilot runs on this
corpus through the real `claude` provider, across models, goal phrasings, and
repeat counts:

| Run | Model(s) (bare / rig) | Silent defects (bare / rig) |
|---|---|---|
| 6 base tasks × 1 | Haiku 4.5 / Haiku 4.5 | 0 / 0 |
| 6 base tasks × 3 | Haiku 4.5 / Haiku 4.5 | 0 / 0 |
| 2 tenant tasks × 3 | Haiku 4.5 / Haiku 4.5 | 0 / 0 |
| 2 tenant tasks × 3 | Fable 5 / Sonnet 5 | 0 / 0 |
| 2 tenant tasks × 3 (bug-report goals) | Fable 5 / Sonnet 5 | 0 / 0 |

**No silent defect occurred in any arm, so no bare-vs-rig differential was
observed.** The models solved these small, single-function tasks correctly —
even from a bug-report-style goal that names only the symptom. When a model did
get one wrong (the unsalted-hash task, where the bare arm produced code whose
round-trip broke), it failed the **public** test — a visible failure, not a
silent one. The only place rig's gate demonstrably acted was that same task,
where the rig arm *safe-stopped* its own broken attempt (escalated instead of
accepting) while the bare arm shipped the broken code.

Why the gap stays at zero here: a silent defect requires the generator to write
a **plausible-but-wrong** fix that passes the visible tests. On tasks this small
and self-contained, capable models either get it fully right or fail visibly —
they rarely fail *silently*. The discriminators are real (the `narrow` variants
prove a partial fix would be caught — see the contract test), but the models
under test did not produce one.

What this does and doesn't establish: the harness, the hidden-check
discriminators, the acceptance gate, and rig's safe-stop all work end-to-end,
and the benchmark does not manufacture a difference. It does **not** yet
demonstrate a bare-vs-rig quality gap on this corpus. Observing that gap
honestly needs the regime where even strong models fail silently — multi-file
tasks, existing confusing authorization code, non-obvious fix sites — not
reworded goals on single-function snippets. Treat the differential as a
hypothesis this corpus is built to test, not a result it has yet shown.

## Model-invariance (`rig-wb bench-invariance`)

A stronger question than "does rig beat a bare agent" is **"is rig's result the
same no matter which model drives it?"** — rig's aim that the accepted outcome
is bounded by the gate, not the model. Measure it across a model panel:

```bash
rig-wb bench-invariance --corpus benchmarks/security-tasks \
  --provider claude --allow-paid-provider \
  --models claude-haiku-4-5-20251001,claude-sonnet-5,claude-fable-5 --html invariance.html
```

It runs the paired benchmark once per model and reports, per arm, the
**agreement** (fraction of model×run samples that reached the same outcome) and
the **panel silent-defect rate**. rig is doing its job when its agreement is
higher than bare's (outcomes converge despite the model) and its silent-defect
rate is 0. Caveat: on this easy corpus every model already succeeds bare, so
both arms score ~1.0 — the metric only discriminates once tasks are hard enough
that bare outcomes diverge by model (the follow-up harder-corpus work).

## What these tasks are not

They are self-contained, stdlib-only fixtures for measuring the generate/verify
loop. They are not a substitute for a real audit of a real system, and nothing
here sends traffic to any external target. The pack's ethical boundary —
own-product / authorized-environment, static + local verification only — applies
to the tooling built on top of this corpus, not to the corpus itself.
