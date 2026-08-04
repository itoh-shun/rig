# Evaluation case capture

Phase 1A adds a standard-library-only case format and capture boundary. It does not run,
compare, promote, select affected cases, or gate a change.

Promoted cases live at `evals/cases/<id>/case.json`. New captures are always unapproved
drafts at `.rig/evals/drafts/<id>/case.json`; execution results are reserved under
`.rig/evals/results/`. Capture records bounded summaries and SHA-256 provenance for source
artifacts, never raw logs.

```console
rig-wb eval capture <task-id> [--repo <repository>]
rig-wb eval validate [<case.json-or-directory>]
rig-wb eval list [--repo <repository>]
```

`capture` does not prove the failing (red) state. Every draft explicitly lists its missing
red/green evidence, deterministic checks, rubric, clean controls, and provider review.
Duplicate IDs and overwrites are rejected. Approved cases must have complete target inputs,
clean controls, deterministic checks, and a semantic rubric. `validate` rejects unknown
fields, unsupported versions and enum values, non-finite numbers, duplicate identifiers,
path traversal, file URIs or absolute paths, Unicode format controls, and secret-like fields
or values. HTTP(S) references remain valid. Files must use canonical JSON (sorted keys,
compact separators, UTF-8, and one trailing newline).
