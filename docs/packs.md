# Rig packs

Rig packs are self-contained, versioned prompt extensions. The filenames remain
`pack.yaml` and `compatibility.yaml` for ecosystem compatibility, but their canonical
portable representation is the JSON-compatible YAML subset: UTF-8 JSON, sorted keys,
compact separators, and one trailing newline. Parsing therefore stays standard-library-only
and never evaluates YAML tags.

```console
rig-wb pack init my-domain --type skill --kind domain --root .rig/packs
rig-wb pack validate .rig/packs/my-domain
rig-wb pack validate --global
rig-wb pack doctor .rig/packs/my-domain --json
rig-wb pack sync .rig/packs/my-domain              # after adding or deleting an asset file
rig-wb pack bundle .rig/packs/my-domain               # -> dist/my-domain-0.1.0.zip
rig-wb pack install ./dist/my-domain-0.1.0.zip --scope project
rig-wb eval promote <case-id> --baseline ... --current ... --repo . --into .rig/packs/my-domain
rig-wb pack test my-domain                         # structural-only, not quality evidence
rig-wb pack test my-domain --provider codex --model gpt-5 --judge-provider codex --judge-model gpt-5
rig-wb pack remove my-domain --scope project       # dry-run
rig-wb pack remove my-domain --scope project --yes
```

The four sections below are one walkthrough in order — scaffold a pack, add an asset,
produce the evidence a prompt-bearing pack needs, hand someone the zip.

`pack init` refuses overwrites and creates every standard directory. Assets are declared and
hashed by `pack sync`, not by hand: `pack.yaml` is canonical and byte-compared, so it is
generated. Every prompt-bearing pack must still bind to at least one approved evaluation
case, which `eval promote` produces from evidence that passed. Validation rejects unknown
manifest fields, undeclared files, ownership crossover, path traversal, symlinks, broken
references, incompatible engine or dependency ranges, cycles, collisions, unsafe secrets,
invisible injection markers, and unambiguous destructive content.

## Starting a pack

`pack init` scaffolds the directory and then tells you the rest of the road:

```
$ rig-wb pack init my-domain --type skill --kind domain --root .rig/packs
initialized: .rig/packs/my-domain

next:
  1. write an asset          my-domain/facets/personas/<name>.md
  2. rig-wb pack sync .rig/packs/my-domain
  3. rig-wb pack validate .rig/packs/my-domain
```

The suggested asset directory depends on the pack's type, because `TYPE_ASSETS` refuses a
recipe inside a `knowledge` pack and proposing one would walk you into a refusal that is
correct and reads as arbitrary.

A scaffolded pack satisfies the schema while carrying nothing, so `validate` reports `valid`
— which is true and easy to misread as finished. `doctor` names that state:

```
$ rig-wb pack doctor .rig/packs/my-domain
pack doctor: warning
- empty_pack: .rig/packs/my-domain
```

That is a warning, not a failure, and `doctor` exits 0 for it. The exit code follows the
report's own distinction: `failed` means something is wrong, `warning` means something is
worth saying. Only the first is an error.

## Adding an asset

`pack.yaml` declares every asset by path and by sha256, and `pack validate` byte-compares the
file against its canonical form — sorted keys, no separators, one trailing newline. That form
is what makes a manifest hashable and signable, and it is deliberately the JSON subset so a
manifest cannot execute a YAML tag.

It is therefore not a file to edit by hand. Write the asset, then let the tool declare it:

```
$ vi .rig/packs/my-domain/facets/personas/reviewer.md
$ rig-wb pack sync .rig/packs/my-domain
  + facets/personas/reviewer.md
pack sync: 1 asset(s) declared and hashed
```

Sync mirrors the directory: a deleted file leaves the manifest too, so a stale declaration
never sends you looking for something you removed. It rewrites `assets` and `hashes` and
nothing else — version, description, capabilities and entrypoints are yours.

It refuses in two cases rather than proceeding quietly. A file sitting outside every asset
directory is named, because declaring nothing about it would leave a file inside the pack that
no hash covers. And a signed pack is refused outright, because rewriting the manifest
invalidates `pack.sig.json`; remove the signature, sync, then re-sign with your key.

A resource file needs a third derived field — `{media_type, size, sha256}` under
`resources` — and sync writes that too, deriving the media type from the extension rather
than asking for it. The pairing between the two is checked, so a hand-written declaration
could only ever agree with the derivation or be wrong. An extension with no supported media
type is named, and an executable extension is refused here exactly as `validate` refuses it,
because sync writes the declaration and would otherwise be the one place that rule could be
walked around.

That makes one authoring path complete today: a `knowledge` pack of pure `resource` files
carries no prompt material, so the evaluation gate does not apply and
`init` → add a file → `sync` → `validate` finishes.

Note what `sync` does not do. A pack carrying prompt material — a persona, an instruction, a
recipe, a wiki page — still needs at least one **approved** evaluation case before `validate`
will pass it. Sync clears the bookkeeping; it does not clear the evidence gate, and it is not
meant to. Producing that evidence is the next section.

## Evidence for a prompt-bearing pack

A pack that carries prompt material needs an **approved** evaluation case before `validate`
passes it. That gate is the point of the design: it makes an installed pack's quality a
measurement rather than a claim. Approval is not a flag you set — `eval promote` refuses a
draft whose evidence does not pass its red/green/clean thresholds, and refuses one whose
semantic rubric was never judged. Results are attested, so an edited result fails at the
signature before the thresholds are consulted.

The draft lives in the **project**, not in the pack. A pack may hold nothing it has not
declared, so a draft staged inside one is refused by `pack validate` and `pack sync` alike.
`--into` moves only the destination of the approved case:

```
$ vi .rig/packs/my-domain/facets/personas/hello.md
$ rig-wb pack sync .rig/packs/my-domain
$ rig-wb pack validate .rig/packs/my-domain
[ERROR] prompt-bearing pack requires at least one evaluation case

# write .rig/evals/drafts/<case-id>/case.json, naming the pack's surfaces in
# prompt_surfaces (e.g. ["persona:hello"]), then measure it:
$ rig-wb eval run <case-id> --repo . --phase baseline --provider ... --model ...
$ rig-wb eval run <case-id> --repo . --phase current  --provider ... --model ...
$ rig-wb eval compare --baseline <baseline.json> --current <current.json> --repo .

$ rig-wb eval promote <case-id> --baseline <baseline.json> --current <current.json> \
      --repo . --into .rig/packs/my-domain
.rig/packs/my-domain/evals/cases/<case-id>/case.json
next: rig-wb pack sync .rig/packs/my-domain   # declare the new case

$ rig-wb pack sync .rig/packs/my-domain
$ rig-wb pack validate .rig/packs/my-domain
valid: my-domain@0.1.0
```

`--into` refuses a directory with no `pack.yaml`. A mistyped path would otherwise put an
approved case somewhere nothing reads it, and the next `pack validate` would report the case
as missing rather than misplaced.

Every gate stays where it was. `--into` changes the destination and nothing else, so a pack's
evidence is held to exactly the standard the repository's own evidence is.

## Handing someone a zip

`pack install` takes a directory, a zip, or a tar. `pack bundle` writes the zip:

```
$ rig-wb pack bundle .rig/packs/my-domain
bundled: my-domain@0.1.0 (3 file(s)) -> /home/you/dist/my-domain-0.1.0.zip
  sha256: 7ef9b1a3...
next:
  rig-wb pack install /home/you/dist/my-domain-0.1.0.zip --scope project
```

The pack is validated first — an archive built from a pack that does not validate can only
produce the same failure, one machine away from whoever could fix it. A signature travels
with the pack it signs.

The bytes are reproducible: entries are sorted, dated to the zip epoch, and given fixed
permissions. That matters because `install` records the archive's sha256 and `pack.lock.json`
pins it, so a bundle that differed on every rebuild would report a change every time and
therefore report nothing. Two bundles of an unchanged pack are byte-identical even from a
fresh clone, where every file has a new mtime.

An existing output file is never overwritten. A released artifact's published digest should
not change with nothing said.

Use `export` instead when the destination is a git repository of its own — that writes a
repository tree with the pack one level down, not an archive.

## Named sources — installing from a private repository

A project declares where packs come from in `.rig/sources.json`, and a spec names the source
rather than an address:

```console
rig-wb pack source add product --scheme git+ssh --url git@github.com:acme/rig-pack-{pack}.git
rig-wb pack source list
rig-wb pack install product:joypla@1.4.0 --scope project
rig-wb pack verify-sources --scope project
rig-wb pack source remove product
```

## Taking a pack out to its own repository

```console
rig-wb pack export packs/domain/japanese-writing --to ../rig-pack-japanese-writing
```

The pack lands one level down, with the repository's own files at the root:

```text
rig-pack-japanese-writing/
├─ README.md
└─ japanese-writing/pack.yaml, recipes/, facets/, ...
```

That nesting is required, not stylistic. A pack directory may contain nothing it has not
declared — that is what makes the type rules enforceable rather than advisory — so a README,
a licence, or a CI workflow cannot sit beside `pack.yaml`. Installing takes the pack directory
only, so a repository's own files never reach a consumer. A repository holding two pack roots
is refused rather than guessed at.

`export` stops at the tree and prints the commands that finish the job. Which forge, public or
private, and who may read it are the owner's decisions. The full walkthrough for both sides of
a migration is in [pack-migration.md](pack-migration.md).

Why the pack model is shaped this way — the type/permission split, the source contract, and
which slices were taken in what order — is recorded in
[pack-vnext-design-brief.ja.md](pack-vnext-design-brief.ja.md) (Japanese).

## Seeing what is installed

```console
rig-wb pack list                       # id@version, type, kind, origin, verification
rig-wb pack info joypla                # source, revision, digests, engine, dependencies
rig-wb pack info joypla --json
rig-wb pack explain joypla             # which of its assets actually reach a prompt
rig-wb pack outdated                   # asks each source what it publishes (network)
rig-wb pack update joypla --to 1.5.0
```

Schemes are `git+ssh`, `git+https`, and `git+file` (a local or mounted repository — for
development and for the offline case where nothing can reach a forge). The URL is a template
containing `{pack}`.

**Rig never holds a credential.** It runs `git`, and git answers for authentication out of
whatever is already configured — an SSH agent, a credential helper, `gh auth`, the OS
keychain, a CI secret. Rig does not read tokens, does not prompt for them, and a source URL
that embeds one is refused. The lock records the source's *name* and the commit, never the
URL, so nothing rig writes can carry a credential and moving a pack between forges does not
rewrite every lock that installed it. That rule is enforced where the bytes are written: the
lock writer runs the same sensor as `rig-wb wb scan-secrets` and refuses a credential-shaped
payload rather than asking each caller to be careful.

`@1.4.0` resolves tag → commit → tree digest, and all three go in the lock. The fetch
re-checks the commit it was given, so a tag moved in between is a refusal rather than a
different pack installed under the version somebody pinned. `verify-sources` re-checks every
locked git pack later and exits non-zero if any pin no longer holds.

Failures arrive apart, because they want opposite responses:

| reason | meaning |
|---|---|
| `source-unreachable` | the source could not be read at all (also what a private repo says to someone who cannot see it) |
| `auth-failed` | the source answered and refused the credentials on this machine |
| `revision-not-found` | the source was read but has no such tag, or the tag moved mid-install |
| `digest-mismatch` | the pin no longer resolves to the recorded commit |
| `capability-refused` | the pack declares something its type may not carry or run |
| `engine-incompatible` | the pack's engine range excludes this engine |
| `unverified-signature` | no publisher signature verifies against a trust root |

`list`, `info`, and `explain` answer from the lock and the installed manifest, so they are
always cheap. `outdated` makes one network round trip per pinned pack, and reports a source
it cannot read on that pack's row rather than failing the whole listing — one unreachable
remote should not hide the rest of the inventory. It exits non-zero when any row is not `ok`,
so it works as a check and not only as a listing.

`info` and `explain` answer different questions and neither substitutes for the other: `info`
is identity and provenance, which the lock knows; `explain` is whether any of the pack's
assets actually win at their tier, which only the resolver knows. A pack can be installed,
valid, and entirely shadowed — that is the state somebody is looking for when an override did
nothing.

`update` moves a git-pinned pack to another version in place. It stages and validates the new
content first and swaps the directory and the lock last, so a failure leaves the old version
installed; remove-then-install would strand the project with neither. It refuses a pack
installed from a directory or an archive, which has no source to ask for a version, and
refuses a tag whose manifest declares a different version than the tag names.

The lock also records what satisfied each declared dependency — the version and tier that
answered the range, not just the range — because a range stays satisfied after the pack that
answered it is swapped underneath. `pack info` reports it.

A digest pins content, not provenance: it says the bytes are the same ones, never who put
them there. Signatures and trust roots are what answer that, and `private` is not a substitute
for either — a pack from a private repository goes through the same manifest validation,
digest check, type check, and secret scan as a public one.

## Pack type — what a pack may carry and run

`type` is required and has no default: it decides the pack's permissions, and a default
would hand that decision to whoever did not make it. It is separate from `kind`
(`core`/`official`/`domain`/`project`), which decides only where the pack resolves in the
tier order — a tier is not a permission.

| `type` | may declare | may run host commands |
|---|---|---|
| `knowledge` | wiki, resource, evaluation cases and results | no |
| `policy` | the above ＋ policies | no |
| `reviewer` | the above ＋ personas, output contracts | no |
| `skill` | every prompt kind (instructions, recipes, patterns, commands, agents) | no |
| `workflow` | the same as `skill` | no |
| `tool` | every asset kind | **yes** |

`skill` and `workflow` carry the same kinds; the difference between them is declared intent,
not permission. What separates `tool` from both is that only a `tool` pack may ship a recipe
declaring `checks:` — shell commands the orchestrator runs on the host. Everything else in a
pack is text a provider reads. That is the line the type model exists to draw: adding a
team's domain knowledge must not also hand them command execution.

The declared asset kinds are checked against the type, and the recipe files are read for
`checks:` because a manifest cannot declare them. Editing the manifest to hide an asset does
not get it installed — validation separately refuses any file the manifest does not declare
and hashes every file it does, so the declaration is the pack's whole contents.

All prompt assets use one precedence order:

1. project `.rig/packs` and legacy `.rig` / `.claude/rig` overlays;
2. user `~/.rig/packs`;
3. organization `$RIG_ORG_HOME/packs`;
4. shipped official packs;
5. Rig core assets.

Resolution reports the selected tier/source and every shadowed candidate. Runtime recipe
and verifier-persona loading use this resolver. Project recipes, including recipes inside a
project pack, retain the existing explicit content-hash trust gate.

`pack doctor` is deterministic and read-only. It reports missing dependencies,
incompatibility, shadows, invalid/evaluation-drifted packs, and coexistence with legacy
overlays without modifying either representation.

## Knowledge — what a pack is *about*

`type` says what a pack may carry. The optional `knowledge:` block says what its contents are
about, so that a question can find the packs that could answer it.

```yaml
knowledge:
  scope: ["company"]
  topics: ["access-control", "backup", "encryption"]
  owner: "Corp IT"
  evidence: ["情報セキュリティ規程", "運用設計書"]
  reviewed_at: "2026-08-01T00:00:00+00:00"
```

The block is optional; a half-filled one is not. Once it is present all five keys are
required, and `reviewed_at` is the reason: a knowledge declaration with no review date is
exactly the one that goes stale without anybody noticing, and it is the field that would be
dropped first if dropping were allowed.

Any `type` may declare it. This is description, not permission — a `reviewer` pack whose
personas encode a product's domain has the same thing to say as a `knowledge` one, and
refusing it there would only teach people to mislabel `type`, which *is* a permission.

A `scope` is a bare dimension (`company`) or a dimension with a value (`product:joypla-one`).
`topics` are slugs. Both are sorted and unique, because order carries nothing there and
leaving it free would let two manifests describing the same pack differ.

`evidence` is the exception, and deliberately: it is **not** required to be sorted. These are
the titles of documents a person wrote, in the language they wrote them, and codepoint order
over prose is not an order any author can predict — `運用設計書` sorts after
`情報セキュリティ規程` for a reason nobody reading either would guess. The order also carries
meaning: a citation list leads with the document the answer chiefly rests on. Duplicates are
still refused; the same document cited twice inflates how well-sourced an answer looks.

It is spelled `evidence` rather than `sources` because `sources` already means *where a pack
is installed from* — `pack source add`, `verify-sources`, and the lock's `source` entries. One
word with two meanings in one CLI is a defect worth not introducing.

### Finding the packs that could answer

```console
rig-wb pack knowledge                              # every declaration in this scope
rig-wb pack knowledge --topic backup
rig-wb pack knowledge --topic backup --scope product
rig-wb pack knowledge --topic backup --json
```

`--topic` and `--scope` are repeatable. A bare dimension matches every value under it, so
`--scope product` finds `product:joypla-one` without your having to know the slug. A valued
scope is exact, and that half is the one that matters: an answer about one product must not
be sourced from a pack that only ever claimed to be about products in general.

**This selects; it does not choose.** The issue behind it gives the case — a security
questionnaire asking "do you take backups?", which has a different correct answer for the
company, for one product, and for the infrastructure underneath both, and an asker who often
has not decided which they meant. When the candidates span more than one scope, the command
says so and names them:

```console
$ rig-wb pack knowledge --topic backup
company-security@0.1.0	company	Corp IT	reviewed 2026-08-01T00:00:00+00:00
  topics: access-control, backup, encryption
  evidence: 情報セキュリティ規程, 運用設計書
product-security@0.1.0	product:joypla-one	JoyPla ONE Team	reviewed 2026-07-15T00:00:00+00:00
  topics: backup, sla
  evidence: サービス仕様書
scope is ambiguous: company, product:joypla-one — narrow with --scope before treating any of these as the answer
```

Which scope was meant is a fact about the asker that no pack contains, so no amount of reading
them recovers it. What this produces instead is the fact that the question is open and the
exact set of alternatives — the material a layer that can hold a conversation needs in order
to ask, rather than guess.

Ambiguity is about scopes, not counts. Two company packs both matching is not ambiguous: they
are two sources for one scope and an answer should rest on both.

Every candidate carries its `evidence`, `owner`, and `reviewed_at`, so the answering side
cites the pack that supplied the material rather than reconstructing it. A pack that declares
no `knowledge` block is never a candidate — silence is not a claim to every scope — and a pack
whose contents fail validation is skipped rather than half-read, because this feeds a citation.

### The documents, not just the pack name

Each candidate also carries the knowledge material the pack ships, addressed as
`pack://<scope>/<id>/<relative>`:

```console
company-security@0.2.0	company	Corp IT	reviewed 2026-08-01T00:00:00+00:00
  topics: access-control, backup, encryption
  evidence: 情報セキュリティ規程, 運用設計書
  wiki: pack://project/company-security/facets/knowledge/backup-policy.md
```

Without this the answering side is told which pack to read and left to find the files itself,
which means either reimplementing tier resolution or citing the wrong copy of a document.

A `wiki` is resolved by name across the tier order, so a project pack's `backup-policy`
overrides a user pack's and only the winner's text ever reaches a prompt. Documents therefore
carry `effective` and `provided_by`, and a shadowed one is **listed and labelled** rather than
either hidden or quietly cited:

```console
  wiki: pack://user/company-security/facets/knowledge/backup-policy.md  [shadowed by product-security]
```

Hiding it leaves somebody wondering where their file went; citing it silently puts an answer
behind text nobody reads. Two packs in one scope cannot collide this way at all — the
collection validator refuses a same-tier `wiki:backup-policy` — so shadowing is always across
tiers. A `resource` is addressed inside its own pack and nothing can shadow it.

Paths are never serialised here. `pack://` is the stable form the pack model requires for any
projection somebody else consumes; the filesystem path is an internal handle.

### Wikis put a knowledge pack under the evaluation ratchet

A `wiki` is prompt material — text a provider is shown — so a pack carrying one is subject to
two rules that predate this block: it must ship at least one evaluation case, and that case
must bind to the pack's own prompt surfaces (`wiki:<name>`). A knowledge pack whose documents
are pure `resource` files is not affected.

This is worth knowing before writing a company knowledge pack: the documents are governed like
any other prompt surface, which is the intended behaviour rather than an accident of packaging.

## Install, lock, test, and remove

`pack install` accepts a local directory, ZIP, or tar archive. URL installation is not
supported. Archives are extracted into a same-filesystem staging directory and rejected
before activation if they contain traversal paths, links, devices, excessive entries,
oversized members, or suspicious compression ratios. The staged pack then passes the same
secret, injection, destructive-command, compatibility, dependency, collision, reference,
and evaluation validation as an installed pack. The source is never modified, an existing
target is never overwritten, and failures leave no active partial pack.

Each managed scope has a canonical `pack.lock.json`. It binds the installed id, version,
kind, scope and relative path to source/manifest/asset/evaluation hashes, engine version,
dependencies, install time, and verification status. Resolver and doctor fail closed on
lock drift. Lock replacement is atomic; install rolls the pack directory back when the lock
cannot be committed.

Prompt-bearing packs require approved evaluation cases. A normal install additionally
requires fresh HMAC-attested, non-mock, current green results owned by the pack. Only a
project-scope install may use `--allow-unverified`; it prints a warning and records
`verification_status: unverified` in the lock. User and organization scopes cannot bypass
quality verification, and mock results never count as quality evidence.

`pack test` without a provider performs structural validation and reports
`structural_only` (successful validation, but not quality evidence). Provider runs reuse the
bounded, shell-free evaluation runner and isolate temporary results. Quality failures exit
1; unavailable providers or evaluation infrastructure exit 2. Mock runs are explicitly
reported as `non_quality_mock`.

`pack remove` is a dry-run unless `--yes` is supplied. It removes only a directory owned by
the selected scope lock, refuses drift and installed dependents, atomically moves the pack
out of service, updates the lock, and deletes the moved directory only after the lock update
succeeds.
