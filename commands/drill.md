---
description: "rig/drill — measure what reviewers actually catch, by mutation drill. Seeds known bugs into a worktree, runs the review fan-out, and scores which reviewer caught what. --replay re-runs an edited persona over archived diffs and diffs the verdicts. Turns persona quality from an opinion into a number."
argument-hint: "[--seeds <n>] [--clean] [--personas <a,b,…>] [--verify-findings] [--replay [<persona>]] [--ablate]"
---

# rig/drill — measuring reviewer detection 🎯

**Start the `rig:engine` skill with the Skill tool first and follow its SKILL.md** (PARSE → RESOLVE → COMPOSE → RUN, context-minimal). This command is only the entry point; the procedure lives in `facets/instructions/drill` and is not repeated here.

Then follow `facets/instructions/drill` to run the drill:

```
$ARGUMENTS
```

## What it does

- **Measurement**: seed **known bugs** — a catalogue tied to CWE and ODC covering missing authorization, XSS, path traversal, N+1, TOCTOU, breaking changes, one-way migrations, absent tests — into a synthetic diff in a temporary worktree; put each seed through a **validity gate** (a seed the `finding-verifier` refutes as harmless in context is dropped from the denominator, which is the equivalent-mutant problem); run the review fan-out (with `output-contracts/review-findings` forcing severity, `file:line`, and blocking or not); and score against the answer key across **seven measures — caught, missed, false positives, the clean false-positive rate, severity accuracy, blocking accuracy, and explanation quality** (a detection rate under n=10 carries a Wilson 95% interval). It promotes `runs --personas`, an indirect signal, into direct measurement. It also prints a per-persona `Drill Result`: score, missed issues, false positives, and recommended persona updates in four fixed categories, triggered by the running history in `.rig/drill-results.jsonl`.
- **`--clean`**: the clean-control mode. Runs the fan-out over no-bug diffs only — refactors and renames — and counts every REJECT and every finding against them as a false positive, measuring each persona's `clean_fp_rate`. Without it, the default is a mix: one clean diff among the seeded ones.
- **`--verify-findings`**: score the refuter too — refuting a genuine seed loses points.
- **`--replay <persona>`**: after editing a persona, re-run it over archived diffs and produce a table of old versus new verdicts. A snapshot test for persona development.
- **`--ablate`**: **a causality test for findings.** Remove only the defect a reviewer named, re-judge, and see whether the verdict flips. A finding that does not flip it was not moving the decision — it was decoration. Take the null control, a re-judgement with nothing changed, first.
- Real code is never touched: a worktree, discarded at the end. Results accumulate in `.rig/drill-results.jsonl`.

## Examples

```
/rig:drill                                  # five seeds plus one clean diff, default reviewer set
/rig:drill --seeds 10 --verify-findings     # serious calibration, refuter included
/rig:drill --clean                          # calibrate clean_fp_rate on no-bug diffs alone
/rig:drill --replay security-reviewer       # regression check after sharpening a lens
/rig:drill --ablate                         # separate findings that caused the verdict from decoration
```

## run-continuity (SKILL.md §6)

While a RUN is active, restate this run-status header as a single line at the top of every turn. Do not drop it right after an interruption, a question, or tool output — the visibility is the evidence that the harness is driving:

```
▸ rig | recipe: <name[tier]|ad-hoc> | step: <id> (<n>/<N>) | gate: <none|pending|passed|REJECT> | backend: <manual|workflow> | mode: <gated|autonomous>
```
