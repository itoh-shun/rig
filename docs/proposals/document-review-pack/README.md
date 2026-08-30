# Proposal: a `document-review` pack

Three read-only reviewer personas and the output contract they answer to, for reviewing a
document rather than writing one: does its **structure** do the document's job, do its
**claims** return to the supplied material, does it reach the **reader** it declares.

**Not shipped, and deliberately not under `packs/`.** Placing it in the shipped tree makes
`pack validate --global` and nine tests fail, because a prompt-bearing pack must carry an
approved evaluation case and this one cannot honestly get there yet. The assets are complete
and reviewable; what is missing is evidence, and the reason it is missing is a circular
dependency in the pack tooling rather than anything about these files.

## Why it cannot be approved yet

- Validating a prompt-bearing pack requires an **approved** evaluation case.
- Approving a case requires evidence that passes `eval promote`.
- Evidence bound to *the pack's own prompt* comes only from `pack test`, which is the one
  path that calls `compose_case_prompt(...)` and passes it as `prompt_prefix`.
- `pack test` calls `validate_pack` first (`packs/tester.py:103`).

So a new pack cannot be measured with its own prompt before it is approved, and cannot be
approved without being measured.

The other path, `rig-wb eval run`, never passes `prompt_prefix`. Measured on a real run of
this case with `--provider claude --model sonnet`:

```
prompt_binding_sha256: e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
                       (= sha256 of the empty string)
prompt_surface_digests: null
```

That evidence measures the bare model against the case input. It says nothing about whether
these personas do their job, and `validate_pack` does not check the binding — so approving on
it would produce a pack that validates on evidence which never exercised its prompt.

## What the real run did show

Given the case's target input — a document claiming "障害対応は大幅に高速化しました" with
attached material containing only incident counts and no response-time records — the model
identified the defect unprompted:

> 本文の主張:「障害対応が大幅に高速化した」／添付データ:12件の一覧のみ、**対応時間の記録なし**
> つまり「高速化した」という結論を裏付けるデータが添付資料に存在しません

It answered in prose rather than the JSON contract, which is expected: the contract was never
in the prompt. The case is a sound test of the property; the harness did not put the pack in
front of it.

## Contents

```
facets/personas/document-structure-reviewer.md
facets/personas/document-evidence-reviewer.md
facets/personas/document-audience-reviewer.md
facets/output-contracts/document-review-verdict.md
pack.yaml, compatibility.yaml          # from `pack init --type reviewer`
```

`reviewer` is the right type and the constraint is load-bearing: `TYPE_ASSETS` lets that type
carry personas, output contracts, wiki pages and inert data — **not** recipes, instructions or
commands. A pack that only judges cannot be made to generate.

Each persona names what it does **not** accept as grounds for passing, which is the part worth
keeping whatever happens to the rest: structure is not heading tidiness, evidence is not
confident phrasing, audience fit is not plainness.

## To land it

Fix the circular dependency first — see the issue this proposal is filed against. Then the
sequence is `pack test` for prompt-bound evidence → `eval promote --into` → `pack sync` →
`pack validate`, and the directory moves to `packs/domain/document-review`.
