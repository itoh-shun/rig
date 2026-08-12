# Evaluation case capture

Rig evaluation cases provide a standard-library-only capture, execution, comparison, and
promotion boundary.

Promoted cases live at `evals/cases/<id>/case.json`. New captures are always unapproved
drafts at `.rig/evals/drafts/<id>/case.json`; ad-hoc execution results are reserved under
`.rig/evals/results/`, while the signed evidence a gate consumes is committed to
`evals/evidence/<case-id>/current.json`. Capture records bounded summaries and SHA-256
provenance for source artifacts, never raw logs.

```console
rig-wb eval capture <task-id> [--repo <repository>]
rig-wb eval reproduce <draft-id> --provider <provider> --model <model> \
  [--repo <repository>]
rig-wb eval validate [<case.json-or-directory>]
rig-wb eval list [--repo <repository>]
rig-wb eval run <case-id-or-suite> --provider mock --model fixture --repeat 3 \
  --phase baseline --judge-provider mock --judge-model fixture \
  [--execution-base <git-ref>] [--repo <repository>]
rig-wb eval compare --baseline <result.json> --current <result.json> \
  [--repo <repository>]
rig-wb eval promote <draft-id> --baseline <result.json> --current <result.json> \
  [--repo <repository>]
rig-wb eval affected --base <git-ref> [--head <git-ref|working>] \
  [--require-cases | --ratchet] [--evidence-dir <directory>] [--json]
rig-wb eval gate --base <git-ref> [--head <git-ref|working>] \
  --evidence-dir <directory> [--ratchet] [--provider <provider>] [--model <model>]
rig-wb eval affected-run --base <git-ref> --head HEAD [--ratchet] \
  --provider <provider> --model <model> \
  --judge-provider <provider> --judge-model <model>
```

`capture` does not prove the failing (red) state. Every draft explicitly lists its missing
red/green evidence, deterministic checks, rubric, clean controls, and provider review.
Duplicate IDs and overwrites are rejected. Approved cases must have complete target inputs,
clean controls, deterministic checks, and a semantic rubric. `validate` rejects unknown
fields, unsupported versions and enum values, non-finite numbers, duplicate identifiers,
path traversal, file URIs or absolute paths, Unicode format controls, and secret-like fields
or values. HTTP(S) references remain valid. Files must use canonical JSON (sorted keys,
compact separators, UTF-8, and one trailing newline).

## Affected-surface coverage: threshold or ratchet

`affected` maps a git diff to the prompt surfaces it touches and to the cases covering
them. Two modes express the same requirement differently.

`--require-cases` is the threshold: every affected surface must already have a case, or
the change is `uncovered` (exit 1). It is the right destination and the wrong starting
point. With an empty `evals/cases/` it fails every change that touches a prompt surface —
including the change that would add the first case — and a check that fires on everything
distinguishes nothing. What it actually teaches is that this job is merged past, and that
habit does not stay confined to this job.

`--ratchet` states the requirement as a direction:

| affected surface | `--require-cases` | `--ratchet` |
|---|---|---|
| has a case | pass | pass |
| has no case yet | `uncovered`, exit 1 | **`coverage_debt`**, exit 0 |
| its coverage was removed by this change | not detected | **`coverage_regressions`**, exit 1 |
| kind not in the registry | `uncovered`, exit 1 | `uncovered`, exit 1 |
| the registry itself was **widened** | `uncovered`, exit 1 | reported, exit 0 |
| the registry itself was **narrowed** | `uncovered`, exit 1 | `registry_narrowings`, exit 1 |

The last two rows are newer than the rest and were forced by the first change that
tried to use this table. Editing `evals/prompt-surfaces.json` was fatal outright, on
the reasoning that changing what the gate can see is not a coverage question — true,
and the consequence was that **the registry could never be extended without failing
the job**. That is #383's shape aimed at the one change class that widens the gate's
coverage, and no eval case can be written for a registry, so there was no way to pass
it. The registry is therefore monotonic like everything else here: adding a root, or
widening one, is the direction the gate is meant to move and passes with a notice.
Removing a root, renaming its kind (which orphans every case bound to the old ids
without deleting a single case, so `coverage_regressions` cannot see it), or dropping
its extensions or its recursion is coverage going down, and stays fatal. As with
`coverage_regressions`, a base tree that cannot be read yields no accusation.

Debt is reported, never swallowed: the report lists each uncovered path and the commits
that touched it, and CI raises a GitHub warning annotation with the count. What cannot
happen is coverage going *down* — deleting a promoted case, or narrowing the
`prompt_surfaces` of one, fails the job. Coverage is therefore monotonic, the same rule
the governance layer applies to policy layers, and the debt count is a number that moves
from the first day rather than a wall that never opens.

`coverage_regressions` is only claimed when it can be demonstrated. If the base tree
cannot be read — a shallow clone, an unborn ref — the comparison reports no regression
rather than accusing the change of deleting everything.

The paid quality steps key off `affected_cases`, not off the status: with no case covering
the change there is nothing to measure, and demanding a provider run anyway is what made
the gate unpassable.

## Execution and promotion

Each run executes both target inputs and clean controls for exactly the repeat count declared
by the case; fewer than three repeats are rejected. Mock runs are deterministic: baseline produces a two-of-three target
failure fixture while current produces three-of-three target and clean passes. Command runs
require an explicit allowlisted argv string, use `shell=False`, and apply a timeout, output
cap, hashing, and secret redaction. Claude and Codex runs reuse the existing benchmark argv
adapter. Their unavailable, timeout, and nonzero exits are infrastructure failures rather
than evidence that product quality is red.

Semantic judging supports `mock`, `command`, `claude`, and `codex` through
`--judge-provider` and `--judge-model`; command judges additionally require
`--judge-command`. Judge commands use bounded output, secret rejection, a timeout, and
`shell=False`. A required judge that errors, omits a rubric ID, duplicates one, or is not
measured fails comparison.

Results are canonical, versioned JSON under
`.rig/evals/results/<case-id>/<run-id>.json`. Comparison requires matching case hash, source
commit, provider, model, integrity hash, fresh timestamps, and a Git execution commit/base
identity. Non-Git evidence cannot be compared or promoted. A target improvement never hides
a clean-control regression.

Use `--execution-base <git-ref>` for PR evidence. Rig resolves it with shell-free Git,
requires it to be an ancestor of HEAD, and binds the resolved commit into the signed result.
The signed `execution_diff_sha256` also hashes the base diff, including tracked/staged
binary diff data and framed untracked path/content. A bare `eval run` hashes the working
tree, so evidence signed before a later uncommitted prompt edit cannot be reused;
`affected-run` instead pins the resolved head, because its evidence is meant to be committed
and re-checked from history later. When omitted, the historical repository-root commit
remains the compatibility default.

## Who measures, and what CI checks

CI does not run providers. `codex` and `claude` are external binaries; a GitHub runner that
installs the package and nothing else has neither, and no credentials for them either. The
step that tried anyway failed before it measured anything and was merged past, which is the
same lesson as `--require-cases` on an empty corpus: a check nobody can pass teaches that
this job is ignored.

So the measurement is a maintainer's:

```console
rig-wb eval affected-run --base <pr-base> --head HEAD --ratchet \
  --provider <provider> --model <model> \
  --judge-provider <judge> --judge-model <judge-model>
git add evals/evidence && git commit -m 'signed evaluation evidence'
```

`--ratchet` on both ends, matching CI. Two prompt surfaces in this repository have a case
and around 198 do not, so a change that touches a covered surface next to any other one is
ordinary. Strict, `affected-run` refuses to measure such a change at all and the gate
reports `uncovered:<path>` — a red that no amount of signed evidence answers, which is the
same defect as `--require-cases` on an empty corpus. Ratcheting, the covered surfaces are
measured and verified while the rest is reported as debt.

`affected-run` refuses a dirty working tree — anything uncommitted would be measured but
not described — and writes one signed result per case to
`evals/evidence/<case-id>/current.json`. One file per case, overwritten: the gate collects
every result under the tree whose `case_id` matches, and a second `current` for the same
case is `current_evidence_count`.

CI then runs `eval gate --evidence-dir evals/evidence`, which needs git and the signing key
and nothing else. Committing evidence changes what the gate can bind to, because
`execution_commit == HEAD` is false the instant the file is tracked — committing it makes a
new HEAD. The binding is the measured **content**, not the measured commit:

- every prompt surface **this change is accountable for** must still hold the object id the
  measurement signed for it, taken from the `prompt_surface_digests` map. The map covers the
  whole surface set at the measured commit, so a path the gate holds accountable and the
  measurement never saw is a file created afterwards, and fails;
- evidence may only move forward. Its `started_at` is compared against the evidence for the
  same case on the **base branch's tip**, and older evidence is
  `evidence_regression:<case-id>`. The comparison reads `evals/evidence/` as a literal path
  at the base commit CI supplies — not the `--evidence-dir` argument and not the fork point,
  because both of those are things the branch under review chooses for itself. A symlink at
  or under the evidence directory is `evidence_symlink:<path>`, refused rather than
  followed, and a comparison that cannot be made at all — a git that will not answer, a
  clone whose blobs were never fetched — is `evidence_ratchet_unavailable:<case-id>` rather
  than a pass;
- `execution_diff_sha256` is recomputed from the *recorded* base to the *recorded* commit and
  must match, whenever history still holds both — a provenance check on the evidence's own
  account of itself. CI never supplies the base, so a base branch moving under a long-lived
  PR does not invalidate a measurement.

Content rather than ancestry because ancestry does not survive this repository's own merge
buttons. Squash and rebase are both enabled, and each rewrites the branch so the measured
commit is gone or is nobody's ancestor: the PR check is green, and the push to the default
branch immediately after the merge is red, recoverable only by measuring on the default
branch and pushing straight to it. A squash reproduces the branch's files exactly, so the
content survives it.

Intersecting with the affected set, rather than comparing the whole map, is what keeps a
merge legal: everything the base branch did since the fork is not this change's to answer
for and was gated on its own PR. An edit the author makes after measuring is in the
intersection and fails.

The evidence ratchet is what makes any of this worth signing. Without it, someone holding
no key can open a PR that re-applies a prompt humans reverted and restores, byte for byte,
the signed evidence that measured it — both are public in the history, and every other
check passes by construction. The price is stated: a branch whose measurement predates
another measurement of the same case on the base branch is told to measure again. That is a
tightening of the intersection rule, and it is the demand the 30-day expiry already makes.

That price is one those two branches already owed each other, and git rather than this gate
is what collects it: both write `evals/evidence/<case-id>/current.json`, and the file is one
canonical-JSON line whose `started_at`, `result_sha256`, and `attestation` cannot coincide,
so the second branch conflicts and no merge button will land it. Comparing against the base
tip only changes *when* the demand is made — on the PR, before the merge, rather than on the
push after it. The structural half of that guarantee is a property of the current
configuration, not a law: a `*.json` merge driver in `.gitattributes` (`union`, say) would
auto-merge that line and dissolve it. There is no `.gitattributes` in this repository today,
and adding one that covers `evals/evidence/` should be treated as changing this gate.

The ratchet also has a start date. It protects a case from the moment a *second* measurement
of it exists on the base branch: with no committed evidence for a case, there is nothing to
move backwards from, and the check correctly passes. This repository currently has none at
all, so today the ratchet is inert everywhere and only the content binding is load bearing.
"Replay is refused" becomes true for a case one measurement after its first one lands.

Known limit: comparison is per-file content, so a surface edited after the measurement and
restored byte-for-byte passes — which is correct, since the tree being gated is then the
tree that was measured. What genuinely escapes is everything outside the surface registry:
prompt-composition code under `rig_workbench/`, `scripts/`, or `skills/engine/corpora/` can
change after a measurement without invalidating it. The registry is this gate's declared
field of view; the older whole-tree diff bound more only as a side effect.

Committed evidence still expires: `MAX_RESULT_AGE` is 30 days, so a branch left open past
that reports `invalid_evidence:<case-id>:evaluation result is stale` and has to be measured
again. Freshness is the one property no signature can carry.

Thirty days stays thirty days now that the evidence is a committed artifact rather than a
file under `.rig/`, and the decision is deliberate rather than inherited. The consequence is
real — checking out an old commit and running the gate reports every result stale, so past
commits cannot be re-verified — and it is the right trade: what the gate certifies is that
a provider measured this prompt recently, and a provider's behaviour is the one input here
that changes without any commit recording it.

The gate fails closed on a missing key: without one no signature can be checked, and a gate
that shrugs when it cannot verify is not a gate. `RIG_EVAL_PROVIDER`, `RIG_EVAL_MODEL`,
`RIG_EVAL_JUDGE_PROVIDER`, and `RIG_EVAL_JUDGE_MODEL` are optional pins — with none set, the
provider constraint is whatever the case's own `provider_policy` declares, plus the standing
refusal of `mock` evidence. A case with `{"mode": "any", "allowed": []}` therefore accepts
any non-mock provider; tightening it is a one-line edit that changes `case_hash` and costs
one re-measurement.

Every result is signed with HMAC-SHA256. `RIG_EVAL_ATTESTATION_KEY` must be **64 hex
characters**, exactly what `openssl rand -hex 32` emits; anything else is refused with
`configured attestation key is invalid`, and the CI job checks the same shape before it
writes the secret to a key file. Randomness cannot be verified, so the form is the
enforceable proxy for it, and prose alone was not enough: committed evidence publishes both
the signature and `key_id` (`sha256(key)[:16]`) on a public repository, which is harmless
against generated material and a complete offline guessing oracle against a memorable
passphrase — one that ends in forgery by someone who never had the key.
Without an explicit key, Rig atomically creates a private `0600` key at
`${XDG_STATE_HOME:-~/.local/state}/rig/eval-attestation.key`. Verification rejects missing,
weakly permissioned, non-regular, or symlinked keys. Keep this key outside the repository;
never commit or print it. Rig strips attestation environment variables from evaluator and
judge child processes. Recomputing the public SHA-256 fields cannot forge the attestation.

Threat model: attestation detects repository or result-file modification by a process that
does not possess the trusted key. It is not an isolation sandbox against a malicious
executor running as the same operating-system user: that process may be able to read the
default XDG key file directly. Run untrusted providers under a separate user or external
sandbox and inject `RIG_EVAL_ATTESTATION_KEY` only into the trusted Rig parent process.

Promotion requires a proved baseline red state, current target and clean green states, and
measured passing semantic evidence for every target and clean repetition in both baseline
and current whenever the case has a rubric. Promotion atomically
creates `evals/cases/<id>/case.json`, refuses an existing destination, and retains the draft.
Capture alone never provides promotion evidence.

## Incident reproduction and affected prompts

Capture prioritizes a production incident over gate and reviewer failures, then records a
bounded failure family, expected fail condition, clean control, and SHA-256 hashes of the
available task/run artifacts. These are safe draft fixtures, not claims of correctness;
remaining inferred requirements stay explicit. Explicitly successful tasks are rejected
unless `--allow-nonincident` is supplied. `reproduce` runs the draft as a baseline and
returns nonzero when the declared RED threshold is not reproduced.
Infrastructure failures are never RED evidence: unavailable, timeout, and provider-error
samples return exit 2. A quality RED additionally requires clean controls to pass and every
required judge sample to be measured. Mock reproduction requires `--allow-mock`, remains a
development probe, and deliberately returns nonzero even when it exhibits the fixture.
The same rule applies to a mock judge paired with a real subject: without the flag it is
rejected before execution, and with the flag it still cannot produce quality RED success.

The versioned prompt registry covers Rig facets (including output contracts), patterns,
recipes, shipped/native agents, commands, and — since registry v2 — the engine's own prose:
the Markdown sitting directly in `skills/engine/`, which is `SKILL.md` and `PACKS.md`.

That last root closed a hole worth naming, because it is the ratchet's own defect pointing
the other way. Every root the registry knew about was a *subdirectory* of `skills/engine/`,
so the two documents governing all of them were the only prompt surfaces in the repository
the analysis could not see: touching one line of a persona registered as affected, while
rewriting §6 of `SKILL.md` — the section that decides PARSE/RESOLVE/COMPOSE/RUN for every
run — reported `noop`. `--require-cases` fired on everything and distinguished nothing;
this fired on everything except the file that matters most.

The root is stated as a rule about the directory rather than as a list of two filenames, so
adding a third engine document does not silently reopen it. It is deliberately **not**
recursive: its subdirectories are either registered above already, or are not prose at all
(`corpora/` is drill fixture data — evidence the gate consumes, not text the model reads).
A registered subdirectory always wins, so a recipe stays `recipe:bugfix` rather than
becoming `engine:recipes/bugfix`.

Note what this depends on. Under `--require-cases` this root could not have shipped: the
first change to `SKILL.md` would have failed the job with no way to pass it, since the case
that would cover it is itself a change to a prompt surface. As debt it is counted, named,
and exit 0 — the ratchet is what makes the registry extensible at all.

`affected` uses a shell-free Git name diff and the existing typed brick graph to
reverse-map direct changes and instruction/persona/policy/wiki dependencies to recipes and
approved canonical cases under `evals/cases/`. Drafts never satisfy coverage or quality
evidence. Cases bind coverage explicitly through unique `prompt_surfaces` registry IDs such
as `instruction:security-audit` and `recipe:bugfix`. Task prose, suites, tags, target inputs,
and clean controls are never substring-matched as coverage evidence. Captured drafts start
with an empty binding and an explicit missing requirement. Unknown prompt surfaces are
reported as uncovered; ordinary source changes are a
deterministic no-op. `--require-cases` makes a known prompt without a bound case fail.

`eval gate` accepts only fresh, HMAC-attested, non-mock current evidence with matching case
hash, Git HEAD/base identity, provider policy, optional provider/model pin, repeat count,
and green target, clean-control, and semantic-judge samples. Result attestations bind the
actual judge adapter's provider, model, and executor version; mock judges are never quality
evidence. Optional `provider_policy.models`, `judge_providers`, and `judge_models` pin these
identities in the case hash. Exit codes are 0 for pass/no-op,
1 for quality or coverage failure, and 2 for malformed/configuration/infrastructure evidence.
The workbench adds `prompt_regression_passed` only when its task diff touches a registered
prompt surface. That criterion is machine-owned: `workbench.py gate --set
prompt_regression_passed=passed` is rejected.

CI always runs the free structural affected-case check. Prompt quality runs only with the
trusted attestation key and pinned provider/model; forks without those credentials fail with
an instruction to request a maintainer run. Missing evidence and mock-only evidence cannot
turn a prompt-changing workflow green.
For trusted same-repository changes, `affected-run` executes approved cases in sorted order
with real subject and judge providers, writes only to a temporary directory, runs the final
gate, and atomically renames the complete evidence directory. Failure or provider
unavailability leaves no partial current-evidence directory.
Every affected report includes `resolved_head`, the verified 40-character commit SHA for a
named revision or the working-tree HEAD. Atomic directories use
`affected-<resolved_head>`, so different commits do not collide. Re-running the same commit
is rejected before provider or judge execution, avoiding duplicate cost and evidence.
