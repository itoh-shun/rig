"""The PACKS on-disk contract, driven through the real `rig-wb` process.

`tests/test_packs.py` already covers this in process, by importing
`rig_workbench.packs.*` and calling `validate_pack` directly. That is the right place for
it while the internals stay where they are — and exactly the wrong place to leave it while
they are rewritten, because an in-process test moves with the code it imports. What a user
has is a pack directory on disk and a command that either accepts it or refuses it, and
nothing in the suite spoke for that pair until this file.

So everything here goes through `python -m rig_workbench.cli` (the `rig_cli` fixture) and
asserts on exit codes and printed lines. Three names are imported from the product anyway,
each lazily at its call site, and each for a stated reason:

  * `rig_workbench.packs.manifest.canonical` — T15 needs an oracle for "what bytes would the
    code write for this manifest", and reimplementing
    `json.dumps(..., sort_keys=True, separators=(",", ":"))` here would pin this file's
    opinion rather than the product's. That makes the product both the data under test and
    the oracle for it, so `canonical`'s own wire format is pinned separately, against a
    literal nothing can regenerate: see CANONICAL_FORMAT_LITERAL below.
  * `rig_workbench.__version__` — the tier fixture's packs must declare an `engine` range the
    running engine satisfies, and a hard-coded one would fail at the next version bump.
  * `rig_workbench.packs.model.ASSET_DIRS` — the same fixture must lay out exactly the asset
    directories the loader expects; a hard-coded list would go stale the next time a kind is
    added, and would be this file's opinion of the layout rather than the product's.

Four groups, selectable by keyword:

    pytest -k canonical T15  `canonical`'s wire format, pinned character for character
    pytest -k shipped   T15  the five shipped domain packs validate, and their manifests
                             are byte-identical to the canonical serialisation
    pytest -k refusal   T16  one broken rule per case, each refused by name
    pytest -k tier      T17  the five-tier search path resolves in the documented order
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import shutil

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SHIPPED_DOMAIN_PACKS = REPO_ROOT / "packs" / "domain"

#: Directory name → the pack id `pack validate` prints. Both halves are pinned on purpose:
#: the directory is what a user types and the id is what the product answers with, and a
#: rewrite that quietly derived one from the other would still have to keep them equal.
SHIPPED_PACK_IDS = {
    "sales": "sales",
    "decision-humor": "decision-humor",
    "document-review": "document-review",
    "pack-author": "pack-author",
    "video-storytelling": "video-storytelling",
}

#: The pack the refusal cases are broken copies of: the smallest shipped pack (two personas,
#: one output contract, two evaluation cases), so a case that breaks one rule is visibly
#: breaking one rule rather than getting lost in a hundred assets.
REFUSAL_BASE_PACK = "document-review"

#: `pack validate` exits 2 on a `PackError` and prints `[ERROR] <reason>` on stderr
#: (rig_workbench/packs/cli.py, `cmd_pack`'s except clause). Every refusal below is that
#: shape; the tests assert the code *and* the reason, because "exited non-zero" would pass
#: just as happily on a crash.
REFUSAL_EXIT_CODE = 2

# ── what this file cannot drive, and why ─────────────────────────────────────
# Anything that needs a production change or the network is named rather than silently
# skipped, per the stage-1 rule.

#: Tier precedence is pinned for `recipe` assets only. `rig-wb wb route --json` is the one
#: CLI surface that reports which tier an asset resolved from (`"tier"` / `"pack"` in the
#: route record), and it resolves recipes. For a persona, wiki, pattern, command or agent
#: the same five-tier order is applied by `packs.resolver.resolve_all`, but no command
#: prints the winner's tier, so there is nothing at the CLI boundary to assert on.
#: tests/test_packs.py::test_unified_tier_resolver_for_every_prompt_kind covers the other
#: kinds in process. Pinning them here would need a new read-only CLI surface.
NOT_PINNED_TIER_ORDER_FOR_NON_RECIPE_KINDS = (
    "no CLI command reports the resolved tier of a persona/wiki/pattern/command/agent; "
    "only `wb route` reports a tier, and only for recipes"
)

#: The refusals that only a remote source can produce — `source-unreachable`, `auth-failed`,
#: `revision-not-found`, `digest-mismatch` against a real remote, and `unverified-signature`
#: against a trust root fetched from one. `pack install` reaches the network for those, and
#: the suite must not.
NOT_PINNED_REMOTE_SOURCE_REFUSALS = (
    "`pack install` refusals for unreachable/unauthenticated/unsigned remote sources need "
    "the network; only on-disk refusals are pinned here"
)

#: The lowest tier is called `core` by the code (`packs.model.TIERS`, `pack_roots`) and
#: `shipped` by the prose in `skills/engine/`. This file pins the code's word, which is what
#: `wb route --json` actually prints. The divergence is recorded, not fixed.
NOT_PINNED_SHIPPED_VERSUS_CORE_NAMING = (
    "skills/engine/ prose calls the lowest tier `shipped`; the code and the route record "
    "call it `core`. Pinned as `core`; the prose is out of scope for this file"
)


def _canonical(value: object) -> str:
    """The canonical serialisation the product itself would write.

    Imported lazily and from one place, so that if V3 moves the function this file fails
    with an ImportError naming it rather than with a byte diff nobody can interpret.
    """
    from rig_workbench.packs.manifest import canonical

    return canonical(value)


def _copy_shipped_pack(tmp_path: pathlib.Path, name: str, tag: str) -> pathlib.Path:
    """A writable copy of a shipped pack, under tmp. Never the real thing: every refusal
    case mutates its pack, and `git status` must stay clean under `packs/`."""
    destination = tmp_path / tag / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SHIPPED_DOMAIN_PACKS / name, destination)
    return destination


def _read_manifest(pack: pathlib.Path) -> dict:
    return json.loads((pack / "pack.yaml").read_text(encoding="utf-8"))


def _write_manifest(pack: pathlib.Path, manifest: dict) -> None:
    (pack / "pack.yaml").write_text(_canonical(manifest), encoding="utf-8")


def _refusal(rig_cli, pack: pathlib.Path):
    """Run `pack validate <pack>` and assert it refused; return the stderr for matching."""
    result = rig_cli("pack", "validate", pack)
    assert result.returncode == REFUSAL_EXIT_CODE, (
        f"expected a refusal, got exit {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}")
    assert result.stdout == "", f"a refusal must print nothing on stdout: {result.stdout!r}"
    return result.stderr


# ── T15 · the canonical wire format, and the five shipped packs ──────────────

#: A document chosen to exercise every degree of freedom `canonical` has: keys that arrive
#: out of sorted order, a nested object, two empty containers (one list, one object), a
#: multi-element list and an integer (comma placement), and non-ASCII values in Latin-1,
#: CJK and punctuation ranges (`ensure_ascii`).
CANONICAL_FORMAT_FIXTURE = {
    "zeta": "ü",
    "id": "café-pack",
    "pack_schema_version": 2,
    "assets": {"recipe": [], "persona": ["facets/personas/レビュアー.md"]},
    "knowledge": {},
    "description": "日本語 — naïve",
    "dependencies": [],
    "surfaces": ["cli", "mcp"],
}

#: What `canonical(CANONICAL_FORMAT_FIXTURE)` must return, character for character.
#:
#: Why a literal, and not `json.dumps(...)` with the same arguments: every other assertion
#: in this file compares one product artefact against another, and `canonical` is the oracle
#: for the shipped manifests. That makes the pair circular — loosen `canonical`'s separators
#: and `test_shipped_pack_manifest_is_byte_identical_to_its_canonical_serialisation` fails,
#: but `rig-wb pack sync` (which its own failure message recommends) rewrites every shipped
#: `pack.yaml` in the new format and the file goes green again, having shipped a different
#: wire format. This literal is the one assertion in the file with no regenerable side:
#: `pack sync` cannot move it, so it fails and keeps failing until a human decides.
#:
#: What breaks if the format changes. These exact bytes are the signing payload, and
#: verification recomputes rather than replays them (rig_workbench/packs/publisher.py):
#: `_envelope` records `manifest_sha256` as the sha256 of `pack.yaml`'s bytes, and
#: `sign_pack`/`verify_publisher_signature` sign and verify `canonical(envelope)` with
#: Ed25519. Re-serialising a published pack therefore invalidates it twice over — the digest
#: no longer matches and the signature no longer verifies — and the repair path does not
#: exist: `packs/sync.py` refuses to rewrite a signed pack. So a change here is not a
#: reformat, it is a break of every signature already in the wild, and it must be made
#: deliberately (new schema version, re-signing) rather than absorbed by a regeneration.
CANONICAL_FORMAT_LITERAL = (
    '{"assets":{"persona":["facets/personas/レビュアー.md"],"recipe":[]},'
    '"dependencies":[],"description":"日本語 — naïve","id":"café-pack",'
    '"knowledge":{},"pack_schema_version":2,"surfaces":["cli","mcp"],"zeta":"ü"}\n'
)


def test_canonical_serialisation_format_is_pinned_to_a_literal():
    """`canonical`'s wire format, pinned where `pack sync` cannot regenerate it."""
    produced = _canonical(CANONICAL_FORMAT_FIXTURE)
    assert produced == CANONICAL_FORMAT_LITERAL

    # The same properties again, named, so a failure above says which one moved rather than
    # handing the reader two long strings to diff. None of these is weaker than the literal.
    assert list(CANONICAL_FORMAT_FIXTURE) != sorted(CANONICAL_FORMAT_FIXTURE), (
        "the fixture must arrive unsorted, or key ordering is not being exercised")
    assert produced.endswith("}\n") and produced.count("\n") == 1, (
        "exactly one trailing newline, and no pretty-printing")
    assert '", "' not in produced and '": "' not in produced, (
        "separators are (\",\", \":\") — no spaces after the comma or the colon")
    assert "\\u" not in produced and "レビュアー" in produced, (
        "ensure_ascii is False — non-ASCII is written through, not escaped")
    assert list(json.loads(produced)) == sorted(CANONICAL_FORMAT_FIXTURE), (
        "keys are emitted in sorted order")


@pytest.mark.parametrize("name", sorted(SHIPPED_PACK_IDS))
def test_shipped_domain_pack_validates_through_the_cli_and_prints_its_identity(
        rig_cli, name):
    pack = SHIPPED_DOMAIN_PACKS / name
    version = _read_manifest(pack)["version"]
    result = rig_cli("pack", "validate", pack)
    assert result.returncode == 0, (
        f"`pack validate {name}` failed\n--- stdout ---\n{result.stdout}"
        f"\n--- stderr ---\n{result.stderr}")
    # The identifying line, exactly: `valid: <id>@<version>` and nothing else. The id is
    # pinned against the table above rather than read back out of the manifest, so a
    # rewrite that printed the directory name instead of the declared id would fail here.
    assert result.stdout == f"valid: {SHIPPED_PACK_IDS[name]}@{version}\n"


@pytest.mark.parametrize("name", sorted(SHIPPED_PACK_IDS))
def test_shipped_pack_manifest_is_byte_identical_to_its_canonical_serialisation(name):
    raw = (SHIPPED_DOMAIN_PACKS / name / "pack.yaml").read_text(encoding="utf-8")
    assert raw == _canonical(json.loads(raw)), (
        f"packs/domain/{name}/pack.yaml is not the canonical serialisation of its own "
        f"content; regenerate it with `rig-wb pack sync`")


def test_shipped_pack_manifest_canonicality_is_broken_by_a_single_added_space(
        rig_cli, tmp_path):
    """The negative control for the assertion above.

    A byte comparison that nothing can fail is not a check, so this proves the oracle bites:
    one space inserted after the opening brace leaves the manifest valid JSON with identical
    content, and both the in-test comparison and the CLI must still reject it.
    """
    pack = _copy_shipped_pack(tmp_path, REFUSAL_BASE_PACK, "one-space")
    manifest_path = pack / "pack.yaml"
    original = manifest_path.read_text(encoding="utf-8")
    assert original == _canonical(json.loads(original))

    spaced = original[:1] + " " + original[1:]
    assert len(spaced) == len(original) + 1
    manifest_path.write_text(spaced, encoding="utf-8")

    # Same content, one more byte: still parses, no longer canonical.
    assert json.loads(spaced) == json.loads(original)
    assert spaced != _canonical(json.loads(spaced))
    assert "pack.yaml is not canonical" in _refusal(rig_cli, pack)


# ── T16 · one refusal rule, one case ─────────────────────────────────────────


def test_an_unmodified_copy_of_a_shipped_pack_validates_so_each_refusal_breaks_one_thing(
        rig_cli, tmp_path):
    """The control every case below rests on: copying the pack changes nothing."""
    pack = _copy_shipped_pack(tmp_path, REFUSAL_BASE_PACK, "control")
    result = rig_cli("pack", "validate", pack)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"valid: {REFUSAL_BASE_PACK}@{_read_manifest(pack)['version']}\n"


def test_refusal_when_pack_yaml_is_no_longer_canonical_json(rig_cli, tmp_path):
    pack = _copy_shipped_pack(tmp_path, REFUSAL_BASE_PACK, "noncanonical")
    manifest_path = pack / "pack.yaml"
    # Re-serialised with indentation: identical content, different bytes. This is the shape
    # a person produces by opening the file in an editor that pretty-prints JSON.
    manifest_path.write_text(
        json.dumps(_read_manifest(pack), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    assert "pack.yaml is not canonical" in _refusal(rig_cli, pack)


def test_refusal_when_the_manifest_key_set_is_missing_a_key_of_every_pack_shape(
        rig_cli, tmp_path):
    pack = _copy_shipped_pack(tmp_path, REFUSAL_BASE_PACK, "missing-key")
    manifest = _read_manifest(pack)
    assert manifest.pop("description", None) is not None
    _write_manifest(pack, manifest)
    assert "pack manifest schema fields/version are invalid" in _refusal(rig_cli, pack)


def test_refusal_when_the_manifest_key_set_carries_a_key_no_pack_shape_declares(
        rig_cli, tmp_path):
    pack = _copy_shipped_pack(tmp_path, REFUSAL_BASE_PACK, "extra-key")
    manifest = _read_manifest(pack)
    # The field set is exact, not a minimum — which is what makes a typo'd key a refusal
    # instead of a line the loader silently ignores.
    manifest["unexpected_field"] = "surplus"
    _write_manifest(pack, manifest)
    assert "pack manifest schema fields/version are invalid" in _refusal(rig_cli, pack)


def test_refusal_when_assets_declares_a_file_that_is_not_on_disk(rig_cli, tmp_path):
    pack = _copy_shipped_pack(tmp_path, REFUSAL_BASE_PACK, "declared-absent")
    declared = "facets/personas/document-evidence-reviewer.md"
    assert declared in _read_manifest(pack)["assets"]["persona"]
    (pack / declared).unlink()
    stderr = _refusal(rig_cli, pack)
    assert f"asset declaration drift (missing=['{declared}'], undeclared=[])" in stderr


def test_refusal_when_a_file_on_disk_is_not_declared_in_assets(rig_cli, tmp_path):
    pack = _copy_shipped_pack(tmp_path, REFUSAL_BASE_PACK, "undeclared-present")
    stray = "facets/personas/stray.md"
    (pack / stray).write_text("an asset nobody declared\n", encoding="utf-8")
    stderr = _refusal(rig_cli, pack)
    assert f"asset declaration drift (missing=[], undeclared=['{stray}'])" in stderr


def test_refusal_when_a_hashes_entry_does_not_match_the_file_it_names(rig_cli, tmp_path):
    pack = _copy_shipped_pack(tmp_path, REFUSAL_BASE_PACK, "hash-drift")
    declared = "facets/personas/document-evidence-reviewer.md"
    asset = pack / declared
    # The file still exists and is still declared, so declaration drift stays silent; the
    # only thing wrong is that its bytes no longer hash to what the manifest recorded.
    asset.write_text(asset.read_text(encoding="utf-8") + "\nan added line\n", encoding="utf-8")
    assert f"asset hash mismatch: {declared}" in _refusal(rig_cli, pack)


def test_refusal_when_a_knowledge_type_pack_declares_a_recipe_asset(rig_cli, tmp_path):
    """TYPE_ASSETS: a `knowledge` pack carries inert data, never a recipe.

    The copy is rebuilt into a structurally sound knowledge pack — the personas and the
    output contract are removed, a recipe is added, and the hashes are recomputed — so the
    only rule left broken is the one about what this type may carry.
    """
    pack = _copy_shipped_pack(tmp_path, REFUSAL_BASE_PACK, "type-assets")
    manifest = _read_manifest(pack)
    shutil.rmtree(pack / "facets")
    (pack / "recipes").mkdir(parents=True, exist_ok=True)
    (pack / "recipes" / "example.md").write_text(
        "---\nname: example\nsteps: []\n---\nbody\n", encoding="utf-8")
    manifest["type"] = "knowledge"
    manifest["assets"]["persona"] = []
    manifest["assets"]["output-contract"] = []
    manifest["assets"]["recipe"] = ["recipes/example.md"]
    manifest["entrypoints"] = []
    manifest["hashes"] = {
        item: hashlib.sha256((pack / item).read_bytes()).hexdigest()
        for paths in manifest["assets"].values() for item in paths
    }
    _write_manifest(pack, manifest)
    stderr = _refusal(rig_cli, pack)
    assert "a knowledge pack may not carry recipe assets" in stderr
    assert "permitted: eval-case, eval-result, resource, wiki" in stderr


def test_refusal_when_the_knowledge_block_is_present_but_only_half_filled(rig_cli, tmp_path):
    pack = _copy_shipped_pack(tmp_path, REFUSAL_BASE_PACK, "half-knowledge")
    manifest = _read_manifest(pack)
    # The block is optional; a partial one is not. `scope` alone is the shape a person
    # writes when they start the declaration and stop.
    manifest["knowledge"] = {"scope": ["company"]}
    _write_manifest(pack, manifest)
    stderr = _refusal(rig_cli, pack)
    assert "pack knowledge must declare exactly" in stderr
    assert "evidence, owner, reviewed_at, scope, topics" in stderr


# ── T17 · the five-tier search path ──────────────────────────────────────────

#: `packs.model.TIERS`, highest precedence first. `wb route --json` prints the winner's tier
#: under this vocabulary; see NOT_PINNED_SHIPPED_VERSUS_CORE_NAMING for the prose divergence.
TIER_ORDER = ("project", "user", "org", "official", "core")

#: The tiers a user can write to, where an asset must be explicitly trusted before it is
#: allowed to route work. `wb route` reports the winner and then refuses to proceed, which
#: is the behaviour being pinned: precedence is decided first, trust second.
WRITABLE_TIERS = frozenset({"project", "user", "org"})

#: `wb route` exits 2 when it has an answer it declines to act on: `route_cli.cmd_route`
#: ends with `raise SystemExit(2)` for `status in {"stopped", "trust_required"}`. That is a
#: judgement, and 2 is the code it is reported under.
ROUTE_TRUST_REQUIRED_EXIT_CODE = 2

#: And it exits 1 when resolution raised a `PackError` — an unresolvable recipe, i.e. a plain
#: failure (`route_cli.cmd_route`'s `except PackError` clause). So the harder outcome is
#: reported under the *lower* code than the softer one: `trust_required` (a decision, with a
#: route record to show for it) is 2, while "no such recipe anywhere" (nothing worked) is 1.
#:
#: That inversion is not a designed contract. The 1 is inherited from `workbench/state.py`'s
#: `die()`, which prints `[ERROR] …` and calls `sys.exit(1)` for every failure in the repo;
#: `route_cli` follows that house style, and the 2 was chosen independently for the trust
#: gate. The value is pinned below because it is what the product does today and a silent
#: change would still be a break for anyone scripting `wb route` — but pinned as a record of
#: the current behaviour, not as an argument that it is right. Fixing it is a production
#: change (it would move every `die()` call site), and out of scope for this file.
UNRESOLVABLE_RECIPE_EXIT_CODE_IS_A_SYMPTOM_NOT_A_CONTRACT = 1

#: The recipe every tier's pack ships under the same name — the whole point of the exercise.
TIER_PROBE_RECIPE = "tier-probe"

#: An approved evaluation case bound to the probe recipe. `validate_pack` refuses a
#: prompt-bearing pack that has none, so a tier fixture cannot be built without one; it is
#: spelled out here rather than imported from another test module to keep this file
#: standalone through the rewrite.
_APPROVED_CASE = {
    "case_schema_version": 1,
    "id": "tier-probe-case",
    "version": 1,
    "title": "Tier probe",
    "status": "approved",
    "incident": True,
    "provenance": {
        "source_task_id": "rig-20260805-tier-probe",
        "source_commit": "a" * 40,
        "source_hashes": {"task.json": "b" * 64},
        "captured_at": "2026-08-05T01:00:00+00:00",
    },
    "surfaces": ["cli"],
    "suite": "regression",
    "tags": ["packs"],
    "provider_policy": {"allowed": ["mock"], "mode": "allowlist"},
    "repeat": 3,
    "red_thresholds": {"max_success_rate": 0.0},
    "green_thresholds": {"min_success_rate": 1.0},
    "deterministic_checks": ["pytest -q tests/test_pack_disk_contract.py"],
    "semantic_rubric": [{"id": "correct", "description": "resolves", "weight": 1.0}],
    "target_inputs": {"scenario": "tier precedence"},
    "clean_controls": {"scenario": "unrelated"},
    "missing_requirements": [],
    "created_at": "2026-08-05T01:00:00+00:00",
    "updated_at": "2026-08-05T01:00:00+00:00",
    "prompt_surfaces": [f"recipe:{TIER_PROBE_RECIPE}"],
}


def _write_tier_pack(root: pathlib.Path, pack_id: str) -> pathlib.Path:
    """A minimal valid pack shipping one recipe named TIER_PROBE_RECIPE."""
    from rig_workbench import __version__
    from rig_workbench.packs.model import ASSET_DIRS

    pack = root / pack_id
    for directory in ASSET_DIRS.values():
        (pack / directory).mkdir(parents=True, exist_ok=True)
    recipe = f"recipes/{TIER_PROBE_RECIPE}.md"
    (pack / recipe).write_text(
        f"---\nname: {TIER_PROBE_RECIPE}\nsteps: []\n---\nowned by {pack_id}\n",
        encoding="utf-8")
    case = dict(_APPROVED_CASE, id=f"{pack_id}-case")
    case_rel = f"evals/cases/{pack_id}-case/case.json"
    (pack / case_rel).parent.mkdir(parents=True, exist_ok=True)
    (pack / case_rel).write_text(_canonical(case), encoding="utf-8")

    assets = {kind: [] for kind in ASSET_DIRS}
    assets["recipe"] = [recipe]
    assets["eval-case"] = [case_rel]
    manifest = {
        "pack_schema_version": 2,
        "id": pack_id,
        "type": "skill",
        "version": "1.0.0",
        "kind": "domain",
        "engine": f">={__version__}",
        "dependencies": [],
        "assets": assets,
        "hashes": {
            item: hashlib.sha256((pack / item).read_bytes()).hexdigest()
            for paths in assets.values() for item in paths
        },
        "provenance": {
            "source": "tests/test_pack_disk_contract.py",
            "created_at": "2026-08-05T00:00:00+00:00",
        },
    }
    compatibility = {
        "compatibility_schema_version": 1,
        "pack_id": pack_id,
        "pack_version": "1.0.0",
        "engine": f">={__version__}",
        "platforms": ["any"],
    }
    (pack / "pack.yaml").write_text(_canonical(manifest), encoding="utf-8")
    (pack / "compatibility.yaml").write_text(_canonical(compatibility), encoding="utf-8")
    return pack


def test_tier_precedence_resolves_project_then_user_then_org_then_official_then_core(
        rig_cli, rig_git_repo, tmp_path):
    """The documented search path, one tier removed at a time, through the CLI.

    `rig-wb wb route --type feature --recipe <name> --json` is the read-only surface that
    reports which tier an asset resolved from. Each tier gets its own pack shipping the same
    recipe name; the highest one present must win, and removing the winner must fall through
    to the next — down to `core`, and then to "not resolvable" with nothing left.
    """
    user_home = tmp_path / "user-home"
    org_home = tmp_path / "org-home"
    rig_home = tmp_path / "rig-home"
    rig_home.mkdir()
    # RIG_HOME redirects the official and core tiers (`<RIG_HOME>/packs/{official,core}`),
    # which is the only way to populate them without writing into the repository's own
    # `packs/`. It also moves the engine's shipped prompt assets, and the packaged domain
    # packs — validated by `wb route`'s canonical catalog, which reads them from the
    # distribution rather than from RIG_HOME — declare typed references into them. So the
    # engine skill tree is brought in from the checkout; everything else under this RIG_HOME
    # is the fixture's own.
    #
    # Copied, not symlinked. Every use below is read-only today, but a symlink makes that a
    # property of the commands rather than of the fixture: one `rig-wb` subcommand that ever
    # writes under RIG_HOME — a cache, a lockfile, a rewritten skill — and the test edits the
    # developer's real `skills/` through the link, in a directory `git status` is expected to
    # keep clean. A copy cannot do that whatever the command does. It is also cheap: 1.8 MB
    # over 226 files, measured at ~0.02s on this checkout, against ~5s for the file's own
    # subprocess runs.
    shutil.copytree(REPO_ROOT / "skills", rig_home / "skills", symlinks=True)

    packs_by_tier = {
        "project": rig_git_repo / ".rig" / "packs",
        "user": user_home / ".rig" / "packs",
        "org": org_home / "packs",
        "official": rig_home / "packs" / "official",
        "core": rig_home / "packs" / "core",
    }
    directories = {tier: _write_tier_pack(root, f"{tier}-tier-pack")
                   for tier, root in packs_by_tier.items()}

    env = {
        "RIG_USER_HOME": user_home,
        "RIG_ORG_HOME": org_home,
        "RIG_HOME": rig_home,
        # Trust is asked for through the environment rather than globally, per the fixture's
        # env overlay; the store it would write to is redirected into tmp so the developer's
        # own ~/.rig/trusted-pack-assets.json is never touched.
        "RIG_ALLOW_PROJECT_PACKS": "1",
        "RIG_PACK_TRUST_STORE": tmp_path / "trusted-pack-assets.json",
    }

    def route():
        result = rig_cli("wb", "route", "--type", "feature",
                         "--recipe", TIER_PROBE_RECIPE, "--json",
                         cwd=rig_git_repo, env=env)
        assert result.stdout, f"wb route printed nothing\n--- stderr ---\n{result.stderr}"
        return json.loads(result.stdout), result.returncode

    for index, tier in enumerate(TIER_ORDER):
        record, returncode = route()
        assert record["tier"] == tier, (
            f"with tiers {TIER_ORDER[index:]} present, {tier} must win; got {record['tier']}")
        assert record["pack"] == f"{tier}-tier-pack"
        assert record["recipe"] == TIER_PROBE_RECIPE
        assert record["provenance"]["resolver"] == "validated-tiered-recipe-resolver"
        # Precedence is settled before trust is: the three writable tiers name their winner
        # and then decline to route it, rather than skipping it and picking a lower tier.
        if tier in WRITABLE_TIERS:
            assert (record["status"] == "trust_required"
                    and returncode == ROUTE_TRUST_REQUIRED_EXIT_CODE)
        else:
            assert record["status"] == "ready" and returncode == 0
        shutil.rmtree(directories[tier])

    exhausted, returncode = route()
    # Exit 1 here, against exit 2 for `trust_required` above: see
    # UNRESOLVABLE_RECIPE_EXIT_CODE_IS_A_SYMPTOM_NOT_A_CONTRACT for why the two are the wrong
    # way round, and why this file pins the inversion rather than asserting it is correct.
    assert (exhausted["status"] == "error"
            and returncode == UNRESOLVABLE_RECIPE_EXIT_CODE_IS_A_SYMPTOM_NOT_A_CONTRACT)
    assert f"explicit recipe `{TIER_PROBE_RECIPE}` is not resolvable" in exhausted["error"]

