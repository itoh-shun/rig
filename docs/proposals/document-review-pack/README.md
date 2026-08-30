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
drafts/                    # three evaluation cases, still `status: draft`
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

## Two blockers that are now gone

This proposal was first written against a pack that could not be measured at all. Both reasons
have since been fixed in the tooling rather than worked around here:

- **The bootstrap circularity** — validating a prompt-bearing pack needed an approved case,
  approving a case needed evidence, and prompt-bound evidence came only from `pack test`,
  which validated first. `pack test --draft` (b4fd309) breaks it.
- **The type contradiction (#552, fixed in #553)** — an entrypoint's `kind` had to be a
  `command` or a `recipe`, which a `reviewer` pack may not own, so it could not declare an
  entrypoint at all and `compose_case_prompt` refused every case. An entrypoint's `kind` may
  now be any prompt kind.

The pack declares one entrypoint per persona, which is what a consumer of a reviewer pack
actually reaches for:

```yaml
entrypoints:
  - {id: structure-review, kind: persona, target: document-structure-reviewer}
  - {id: evidence-review,  kind: persona, target: document-evidence-reviewer}
  - {id: audience-review,  kind: persona, target: document-audience-reviewer}
```

## Measured

Each case composes its own persona plus the shared contract, and the evidence is bound to that
composition rather than to nothing:

```
$ rig-wb pack test docs/proposals/document-review-pack/pack \
    --draft document-review-structure-buried-ask --provider mock --model probe --result-dir <ext>
{"cases":["document-review-structure-buried-ask"],"failures":[],"pack":"document-review",
 "quality":false,"result_paths":["…-current-mock.json"],"status":"non_quality_mock"}
```

```
document-review-structure-buried-ask
  sections : persona:document-structure-reviewer, contract:document-review-verdict
  binding  : 08d5c28b8bf766fd…   (sha256 of the empty string? False)
document-review-evidence-unsupported-claim
  sections : persona:document-evidence-reviewer, contract:document-review-verdict
  binding  : 3887707ab38ab9b2…   (sha256 of the empty string? False)
document-review-audience-undeclared-terms
  sections : persona:document-audience-reviewer, contract:document-review-verdict
  binding  : c3a1beee25f22e68…   (sha256 of the empty string? False)
```

That empty-string check is not decoration. `rig-wb eval run` never passes `prompt_prefix`, so
an earlier measurement of this same case reported
`prompt_binding_sha256: e3b0c442…` — the digest of nothing — and `prompt_surface_digests:
null`. Evidence like that measures the bare model against the case input and says nothing
about whether these personas do their job. `pack test` is the only path that composes.

Each case pairs a document carrying exactly one known defect with a control that does not
carry it, so a persona that answers `REVISE` to everything fails the control:

| case | target | control |
|---|---|---|
| structure | the ask sits after three sections of background; headings are `はじめに` / `背景` / `まとめ` | same content, ask and options first |
| evidence | claims 障害対応が大幅に高速化した while the attached list records counts and no durations | same data, and says the duration is not recorded so the question cannot be settled |
| audience | declares 非エンジニアの経営層 and uses p95 / SLO / エラーバジェット / HPA / PDB unexplained | same decision, same numbers, terms explained |

## Why it is still here and not under `packs/`

`validate_pack` requires an **approved** evaluation case, and approving one requires evidence
from a real provider. `pack test` refuses the two adapters available in a development
container:

- `claude` — refused outright: *pack evaluation requires an OS-level read-only adapter*.
  Claude Code's isolation is agent-policy, which the eval harness records but packs do not
  accept for durable, redistributed evidence.
- `mock` — runs, and reports `non_quality_mock`. Mock evidence is forbidden as a basis for a
  quality claim, which is why it is a status of its own rather than a pass.

So the remaining step is a maintainer run with `codex` (`--sandbox read-only`, hence
`os-enforced`) plus `--allow-paid-provider`. Nothing about the pack blocks it; the container
lacks the binary.

Approving these cases on anything weaker would produce a pack that validates on evidence which
never exercised its prompt. That is the substitution this format exists to refuse, so the pack
waits here rather than shipping on a green that means something else.

## To land it

```
rig-wb pack test docs/proposals/document-review-pack/pack --draft <case-id> \
  --provider codex --model <model> --allow-paid-provider \
  --judge-provider codex --judge-model <judge-model> --result-dir <outside the repo>
```

once per case, then `eval promote --into` the three cases, `pack sync`, `pack validate`, and
move `pack/` to `packs/domain/document-review` with the cases under its own `evals/cases/`.
