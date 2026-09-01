# The document-review pack: what shipped, and what did not

The pack is at [`packs/domain/document-review`](../../../packs/domain/document-review). This
directory is what is left over: one persona that could not be measured, its draft case, and the
record of how the rest got there.

## Shipped

Two read-only reviewer personas and the output contract they answer to, with an approved
evaluation case each — subject and judge both `codex` under `--sandbox read-only`
(`os-enforced`), three repetitions, target and clean:

```
document-review-structure-buried-ask        target 3/3 REVISE   clean 3/3 APPROVE   judge 12/12
document-review-evidence-unsupported-claim  target 3/3 REVISE   clean 3/3 APPROVE   judge 12/12
```

Each case pairs a document carrying one known defect with a control that does not carry it, so
a persona answering `REVISE` to everything fails the control. The evidence is bound to the
composed prompt: `prompt_binding_sha256` is a digest of the persona and contract this pack
owns, not of the empty string that `eval run` alone would have recorded.

## Not shipped: `document-audience-reviewer`

The persona is [here](unmeasured-document-audience-reviewer.md) and its case is in
[`drafts/`](drafts/document-review-audience-undeclared-terms). It is not in the pack, because
the pack's claim is that its personas are measured and this one is not.

Its target arm never failed — the reviewer named p95 / SLO / エラーバジェット / HPA / PDB against
the declared 非エンジニアの経営層 in every run. The control is what would not hold:

| round | change | clean |
|---|---|---|
| 1 | first control | 0/3 |
| 2 | plain wording, concrete objects, named the recipient of the decision | 3/3 |
| 3 | *same document*, only the regex form of the checks changed | 2/3 |

Round 3 says it: with the document unchanged, the same control passed three times and then
twice. `green_thresholds.min_success_rate` is 1.0, so the case demands 6/6 across the pair and
it delivered 5/6. The run that failed reported that 目標 and 上限 are defined but their meaning
for the decision is not — a reading the persona's own checks license, and a fair one.

A fourth edit to the control was the obvious move and is the reason it did not happen. When the
same fixture has been broken three times, the thing to doubt is the instrument, not the next
hole in the document: the pair differs in vocabulary, but "can this reader act on it" is not a
question vocabulary alone settles, so the pair does not isolate the axis the case claims to
measure. Lowering the threshold or re-running until a green appeared would have produced a
number that means nothing.

To land it, the case needs a control that is unambiguous on the axis the reviewer keeps
probing, or the persona needs checks scoped tightly enough that the axis does not arise. Either
is a design change, not a rewrite of the document.

## What it took to get the other two through

Three blockers, each found by running something rather than by reading code, and each fixed
where it belonged rather than worked around here:

- **#552 / #553** — an entrypoint's `kind` had to be a `command` or a `recipe`, which a
  `reviewer` pack may not own, so it could not declare an entrypoint at all and
  `compose_case_prompt` refused every case.
- **#557** — `pack test --draft`, the only route to a pack's first evidence, had never
  completed with a real provider. It made every provider call, wrote the result, then raised
  `KeyError` while summarising, because the summary rebuilt its case lookup from the pack's
  *approved* cases. `--provider mock` returns before that lookup.
- **#556 / #561** — `compare_results` required the semantic rubric to pass on the baseline too.
  The baseline composes no prompt, so it is the bare model: measured here it rewrote the
  document instead of reviewing it, and `no_rewrite` passed 0 of 6. The rubric failed because
  the pack worked. The gate now derives from `prompt_binding_sha256` whether the two phases ran
  the same prompt.

And one still open: **#563** — `pack validate` refused these cases as containing an "absolute
path", which was the two backslashes JSON encoding produces for `\s` in a `regex:` check. A
shipped pack carries the same two backslashes and passes, because the character before them is
`^` rather than `"`. The checks here were rewritten to use ` *` and everything re-measured, so
nothing depends on that being fixed — but six signed evidence files were discarded to get past
it.

## Reproducing

```
rig-wb pack test packs/domain/document-review --draft <case-id> \
  --provider codex --model <model> --allow-paid-provider \
  --judge-provider codex --judge-model <judge-model> \
  --result-dir <outside the repo> --timeout 300
rig-wb eval run <case-id> --provider codex --model <model> --repeat 3 --phase baseline \
  --judge-provider codex --judge-model <judge-model> --timeout 300 --judge-timeout 300
rig-wb eval promote <case-id> --baseline <b> --current <c> --into packs/domain/document-review
```

`--timeout` matters: its default is 30 seconds, a codex sample here takes longer, and every
sample then times out into `provider_unavailable` — which reads like a missing provider rather
than an expired clock. Drafts must also be canonical JSON; `pack test` accepts one that is not
and `eval run` then refuses every draft in the repository.
