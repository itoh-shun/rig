# Evaluation case capture

Rig evaluation cases provide a standard-library-only capture, execution, comparison, and
promotion boundary.

Promoted cases live at `evals/cases/<id>/case.json`. New captures are always unapproved
drafts at `.rig/evals/drafts/<id>/case.json`; execution results are reserved under
`.rig/evals/results/`. Capture records bounded summaries and SHA-256 provenance for source
artifacts, never raw logs.

```console
rig-wb eval capture <task-id> [--repo <repository>]
rig-wb eval validate [<case.json-or-directory>]
rig-wb eval list [--repo <repository>]
rig-wb eval run <case-id-or-suite> --provider mock --model fixture --repeat 3 \
  --phase baseline --judge-provider mock --judge-model fixture \
  [--repo <repository>]
rig-wb eval compare --baseline <result.json> --current <result.json> \
  [--repo <repository>]
rig-wb eval promote <draft-id> --baseline <result.json> --current <result.json> \
  [--repo <repository>]
```

`capture` does not prove the failing (red) state. Every draft explicitly lists its missing
red/green evidence, deterministic checks, rubric, clean controls, and provider review.
Duplicate IDs and overwrites are rejected. Approved cases must have complete target inputs,
clean controls, deterministic checks, and a semantic rubric. `validate` rejects unknown
fields, unsupported versions and enum values, non-finite numbers, duplicate identifiers,
path traversal, file URIs or absolute paths, Unicode format controls, and secret-like fields
or values. HTTP(S) references remain valid. Files must use canonical JSON (sorted keys,
compact separators, UTF-8, and one trailing newline).

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

Every result is signed with HMAC-SHA256. Set `RIG_EVAL_ATTESTATION_KEY` to a secret of at
least 32 bytes in CI. Without it, Rig atomically creates a private `0600` key at
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
