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
rig-wb pack install ./dist/my-domain.zip --scope project
rig-wb pack test my-domain                         # structural-only, not quality evidence
rig-wb pack test my-domain --provider codex --model gpt-5 --judge-provider codex --judge-model gpt-5
rig-wb pack remove my-domain --scope project       # dry-run
rig-wb pack remove my-domain --scope project --yes
```

`pack init` refuses overwrites and creates every standard directory. Add assets to the
manifest, record their SHA-256 hashes, and bind each prompt-bearing pack to at least one
approved evaluation case. Validation rejects unknown manifest fields, undeclared files,
ownership crossover, path traversal, symlinks, broken references, incompatible engine or
dependency ranges, cycles, collisions, unsafe secrets, invisible injection markers, and
unambiguous destructive content.

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
