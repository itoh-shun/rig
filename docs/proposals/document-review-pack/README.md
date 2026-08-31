# Proposal: a `document-review` pack

Three read-only reviewer personas and the output contract they answer to, for reviewing a
document rather than writing one: does its **structure** do the document's job, do its
**claims** return to the supplied material, does it reach the **reader** it declares.

```
pack/                      # the pack itself
  facets/personas/document-structure-reviewer.md
  facets/personas/document-evidence-reviewer.md
  facets/personas/document-audience-reviewer.md
  facets/output-contracts/document-review-verdict.md
  pack.yaml, compatibility.yaml
drafts/                    # three evaluation cases, measured green, still `status: draft`
  document-review-structure-buried-ask/case.json
  document-review-evidence-unsupported-claim/case.json
  document-review-audience-undeclared-terms/case.json
```

`reviewer` is the right type and the constraint is load-bearing: `TYPE_ASSETS` lets that type
carry personas, output contracts, wiki pages and inert data — **not** recipes, instructions or
commands. A pack that only judges cannot be made to generate.

Each persona names what it does **not** accept as grounds for passing, which is the part worth
keeping whatever happens to the rest: structure is not heading tidiness, evidence is not
confident phrasing, audience fit is not plainness.

## Three blockers found by measuring, two of them gone

This proposal was first written against a pack that could not be measured at all. Each blocker
below was found by running something, not by reading the code.

- **The bootstrap circularity** — validating a prompt-bearing pack needed an approved case,
  approving a case needed evidence, and prompt-bound evidence came only from `pack test`,
  which validated first. `pack test --draft` (b4fd309) breaks it.
- **The type contradiction (#552, fixed in #553)** — an entrypoint's `kind` had to be a
  `command` or a `recipe`, which a `reviewer` pack may not own, so it could not declare an
  entrypoint at all and `compose_case_prompt` refused every case. An entrypoint's `kind` may
  now be any prompt kind.
- **`pack test --draft` crashed on every real provider (fixed in 1ffcba8)** — it made all
  twelve provider calls, wrote the result file, then raised `KeyError` while summarising,
  because the summary rebuilt its case lookup from the pack's *approved* cases and a draft is
  never one of them. `--provider mock` returns before that lookup, so every earlier run of
  this proposal took the one path that skips it. The only route to a pack's first evidence had
  never completed.

The pack declares one entrypoint per persona, which is what a consumer of a reviewer pack
actually reaches for:

```yaml
entrypoints:
  - {id: structure-review, kind: persona, target: document-structure-reviewer}
  - {id: evidence-review,  kind: persona, target: document-evidence-reviewer}
  - {id: audience-review,  kind: persona, target: document-audience-reviewer}
```

## Measured against codex, all three cases green

`repeat: 3`, `green_thresholds.min_success_rate: 1.0`, `red_thresholds.max_success_rate: 0.0`,
subject and judge both `codex` under `--sandbox read-only` (`os-enforced`):

```
structure : target 3/3 REVISE   clean 3/3 APPROVE   judge 12/12   status: pass
evidence  : target 3/3 REVISE   clean 3/3 APPROVE   judge 12/12   status: pass
audience  : target 3/3 REVISE   clean 3/3 APPROVE   judge 12/12   status: pass
```

Each case composes its own persona plus the shared contract, and the evidence is bound to that
composition rather than to nothing — the binding is not the digest of the empty string, which
is what `eval run` alone would have recorded.

Each case pairs a document carrying one known defect with a control that does not carry it, so
a persona that answers `REVISE` to everything fails the control. Getting there took three
rounds, and every round found a fault in the **control**, not in the persona:

| case | the defect under test | what the control still carried |
|---|---|---|
| structure | the ask sits after three sections of background; headings do not preview | it asked the reader to choose between A and B and gave nothing to choose on |
| evidence | claims 障害対応が大幅に高速化した while the attached list records counts and no durations | it promised 下期は所要時間の記録を開始します — a commitment the attached material cannot support, which `document-evidence-reviewer` says in as many words that it looks for |
| audience | declares 非エンジニアの経営層 and uses p95 / SLO / エラーバジェット / HPA / PDB unexplained | 一部のデータを持つ処理 and 基盤 are not plain, they are vague; and the document never said **to whom** the decision goes |

All three reviewers were right all three times, and consistently: on each round every one of
the three repetitions reported the same fault in the same control. The pairs now differ only
in the defect under test — the structure pair carries the same comparison of A and B in both
arms, the evidence pair differs only in whether the speed claim is asserted or refused, and
the audience pair differs only in vocabulary.

The rubrics were rewritten in the same pass. A criterion phrased as *finds the buried ask*
can only hold on the arm that has one, so it fails the control by construction and the gate
counts that as a quality failure. The shipped `japanese-writing` cases show the convention:
rubric criteria describe the quality of the **review**, not which verdict it reached. Rephrased
arm-neutrally — *states where the ask sits, and that stated position matches the document* —
they keep their teeth: a review that puts the ask in the wrong place still fails.

## Why it is still here and not under `packs/`

The pack is green. The promotion gate is not reachable (#556).

`eval promote` requires a **baseline** as well as the green current run, and `compare_results`
requires the semantic rubric to pass on every baseline sample too. The baseline is
`eval run --phase baseline`, which composes no prompt: the bare model, without these personas.
Measured on the structure case, same provider and judge:

```
target_success_rate 0.0     (the red threshold is satisfied)
locates_the_ask   pass 1/6
no_rewrite        pass 0/6
```

The bare model does not review the document. It rewrites it:

```
以下のように、判断事項と比較材料を先に示す構成へ直すと、部門長が読みやすくなります。
---
# 受付ツール刷新方針のご判断依頼
```

`eval compare` then refuses with `semantic judge rubric criterion failed`.

`no_rewrite` is the property that separates a reviewer from a writer, and the baseline fails it
because the pack works. Writing a rubric the bare model could pass means asserting nothing the
prompt is responsible for. The rule is satisfiable in proportion to how little the prompt
changes, so the pack waits rather than trading its rubric for a green.

## To land it

Once #556 is decided, per case:

```
rig-wb pack test docs/proposals/document-review-pack/pack --draft <case-id> \
  --provider codex --model <model> --allow-paid-provider \
  --judge-provider codex --judge-model <judge-model> --result-dir <outside the repo>
rig-wb eval run <case-id> --provider codex --model <model> --repeat 3 --phase baseline \
  --judge-provider codex --judge-model <judge-model>
```

then `eval promote --into` the three cases, `pack sync`, `pack validate`, and move `pack/` to
`packs/domain/document-review` with the cases under its own `evals/cases/`.

Drafts must be canonical JSON (`sort_keys`, `separators=(",", ":")`, trailing newline);
`pack test` accepts a non-canonical one and `eval run` refuses every draft in the repository
until it is fixed.
