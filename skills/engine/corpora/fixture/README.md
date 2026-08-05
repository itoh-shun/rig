# fixture corpus — pre-built planted-defect cases for `/rig:drill`

The standard corpus (`facets/instructions/drill.md`) is a table of **seed classes**: a
subagent synthesizes each class into a fresh diff at run time. This corpus is the other
shape — **the diff is already written**. Every case ships a `base/` tree (the committed
state) and a `head/` tree (the uncommitted working-tree change), and the answer key
travels with the case.

Two things follow from that, and they are the reason this corpus exists alongside the
standard one:

- **The measurement is repeatable byte-for-byte.** Nothing about the diff varies between
  runs, so a detection-rate change is a change in the reviewer, not in the seed the
  synthesizer happened to write that day.
- **The cases were written for this measurement and are taken from no reviewer's own
  fixture set.** A reviewer that was tuned against the very fixtures it is graded on has
  an unfair edge, and the number stops meaning anything.

## Layout

```
corpus.json                  corpus id + corpus_version (bump on any case change)
cases/<case-id>/case.json    the answer key
cases/<case-id>/base/        committed state
cases/<case-id>/head/        uncommitted working-tree state (the diff under review)
```

`case.json`:

| field | meaning |
|---|---|
| `id` | case id (matches the directory name) |
| `language` | primary language of the case |
| `clean` | `true` = no defect planted; every finding on this case is a false positive |
| `description` | what the change is, and for a clean case why it is genuinely clean |
| `violations[]` | the planted defects (empty for a clean case) |

Each violation carries:

| field | meaning |
|---|---|
| `id` | stable id of the planted defect |
| `category` | defect class (`security` / `performance` / `correctness` / `test` / `compatibility` / `type-safety`) |
| `severity` | expected severity (`critical` / `high` / `medium`) |
| `perspectives` | which reviewer perspectives should catch it — the vocabulary of the "検出すべき観点" column of the standard seed catalog |
| `summary` | prose statement of the defect (for the human reading the scoreboard, never shown to a reviewer) |
| `location` | regex for the symbol/file signal |
| `concept` | regex for the defect-class signal |

## How a violation is scored

A violation counts as detected only when the review carries **both** a location signal and
a concept signal, **and** they appear near each other (within `PROXIMITY_WINDOW`
characters — see `rig_workbench/workbench/detection_corpus.py`).

Requiring both is what stops "I reviewed the code and it looks risky" from scoring.
Requiring proximity is what stops a long review that names `mergeMetadata` in one
paragraph and the word "any" in an unrelated sentence from scoring as a detection.

`location_hit` and `concept_hit` are also reported separately, because "named the symbol
but never said what was wrong with it" is a different failure from "never looked at it".

On the **clean** case the direction inverts: any blocking language at all is a false
positive. Plain "looks fine" prose, or a suggestion phrased as optional, is not.

## What this corpus does and does not measure mechanically

Mechanical, no judge needed: **detection rate** (per reviewer, per perspective) and
**clean false-positive rate**.

Not mechanical: `severity_accuracy`, `blocking_accuracy`, and `explanation_quality`.
Those need the judge step described in `facets/instructions/drill.md` ③-b — the scorer
does not guess them, and leaves them absent rather than filling in a number nobody
measured. Likewise, false positives *on the violation cases* are not counted here: telling
an invented finding from a real bug the reviewer happened to notice is a judgement call,
and the clean case is the controlled way to measure the same thing.

## Adding a case

1. Write `base/` and `head/` so that the diff between them is a change a reviewer would
   plausibly receive — not a synthetic marker.
2. Write `case.json`. Keep `location` tight enough that an unrelated paragraph cannot
   match it, and `concept` broad enough to accept the words a reviewer would actually use
   (including Japanese — the shipped cases accept both).
3. Bump `corpus_version` in `corpus.json`.
4. Run `python3 -m pytest tests/test_drill_detection_corpus.py -q`. The scorer's own
   fixtures (ideal / vague / decoy reviews) must still score 100% / 0% / 0%; a new case
   that breaks them means the regexes credit prose that names symbols without describing
   any defect.
