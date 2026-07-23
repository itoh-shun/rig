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

## What these tasks are not

They are self-contained, stdlib-only fixtures for measuring the generate/verify
loop. They are not a substitute for a real audit of a real system, and nothing
here sends traffic to any external target. The pack's ethical boundary —
own-product / authorized-environment, static + local verification only — applies
to the tooling built on top of this corpus, not to the corpus itself.
