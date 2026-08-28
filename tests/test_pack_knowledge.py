"""A pack declares what its contents are *about*, and rig names candidates without choosing (#533).

The issue's example is a security questionnaire asking "do you take backups?". That question
has three different correct answers depending on whether it is about the company, one product,
or the infrastructure underneath both — and the person asking often has not decided which they
meant. So the requirement is not a better guess. It is that rig can (a) find every pack that
could answer and (b) say plainly when the question is still open, leaving the choosing to a
layer that can hold a conversation.

Both halves are checked here, and the second is the one worth being strict about: a selector
that quietly returns the first match would pass any test that only asserted "something came
back". Every ambiguity case below therefore asserts on what the selector *refused* to do.
"""

import json
import pathlib

import pytest

from rig_workbench.packs.inventory import knowledge_rows
from rig_workbench.packs.manifest import (KNOWLEDGE_FIELDS, PACK_SCHEMA_VERSION, canonical,
                                          validate_manifest_shape)
from rig_workbench.packs.model import ASSET_DIRS, PackError

COMPANY = {
    "scope": ["company"],
    "topics": ["access-control", "backup", "encryption"],
    "owner": "Corp IT",
    "evidence": ["情報セキュリティ規程", "運用設計書"],
    "reviewed_at": "2026-08-01T00:00:00+00:00",
}
PRODUCT = {
    "scope": ["product:joypla-one"],
    "topics": ["backup", "sla"],
    "owner": "JoyPla ONE Team",
    "evidence": ["サービス仕様書"],
    "reviewed_at": "2026-07-15T00:00:00+00:00",
}


def _manifest(pack_id: str, knowledge: dict | None = None) -> dict:
    manifest = {
        "pack_schema_version": PACK_SCHEMA_VERSION, "id": pack_id, "type": "knowledge",
        "version": "1.0.0", "kind": "project", "engine": "*", "dependencies": [],
        "assets": {kind: [] for kind in ASSET_DIRS}, "hashes": {},
        "provenance": {"source": "test", "created_at": "2026-08-27T00:00:00+00:00"},
    }
    if knowledge is not None:
        manifest["knowledge"] = knowledge
    return manifest


def _scope(tmp_path: pathlib.Path, packs: dict[str, dict | None]) -> pathlib.Path:
    """A scope root whose lock owns `packs`, each written to disk as a valid manifest.

    `knowledge_rows` reads the manifest on disk rather than the lock, exactly as `pack list`
    reads `type` from disk, so the pack directory is the part that has to be real.
    """
    root = tmp_path / "scope"
    entries = []
    # Sorted, because the lock refuses unsorted entries. Building the fixture through that
    # rule rather than around it keeps these tests reading a lock a real install could write.
    for pack_id in sorted(packs):
        knowledge = packs[pack_id]
        directory = root / "packs" / pack_id
        directory.mkdir(parents=True)
        (directory / "pack.yaml").write_text(canonical(_manifest(pack_id, knowledge)),
                                             encoding="utf-8")
        (directory / "compatibility.yaml").write_text(canonical({
            "compatibility_schema_version": 1, "pack_id": pack_id, "pack_version": "1.0.0",
            "engine": "*", "platforms": ["any"],
        }), encoding="utf-8")
        entries.append({
            "id": pack_id, "version": "1.0.0", "kind": "project", "scope": "project",
            "path": f"packs/{pack_id}", "installed_at": "2026-08-27T00:00:00+00:00",
            "engine_version": "2.8.0", "verification_status": "verified-local",
            "publisher_key_id": None, "manifest_sha256": "0" * 64,
            "dependencies": [], "dependency_resolution": [], "eval_case_hashes": {},
            "source": {"type": "path", "path": str(directory), "sha256": "0" * 64},
        })
    (root / "pack.lock.json").write_text(
        json.dumps({"pack_lock_schema_version": 4, "packs": entries},
                   ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8")
    return root


# ── the declaration ───────────────────────────────────────────────────────────


def test_the_block_is_optional_and_a_pack_without_one_still_validates():
    """Every pack that exists predates this field. Requiring it would have made the feature
    a breaking change to packs that have nothing to do with knowledge."""
    validate_manifest_shape(_manifest("demo"))


def test_a_pack_of_any_type_may_declare_knowledge():
    """This is description, not permission. A `reviewer` pack whose personas encode a
    product's domain has the same thing to say as a `knowledge` one, and refusing it there
    would only teach people to mislabel `type` — which *is* a permission — to get the field."""
    manifest = _manifest("demo", COMPANY)
    manifest["type"] = "reviewer"
    validate_manifest_shape(manifest)


@pytest.mark.parametrize("missing", sorted(KNOWLEDGE_FIELDS))
def test_a_half_filled_block_is_refused(missing):
    """The block is what is optional; a partial one is not. `reviewed_at` is why: a knowledge
    declaration with no review date is precisely the one that goes stale unnoticed, and it is
    the field that would be dropped first if dropping were allowed at all."""
    knowledge = {k: v for k, v in COMPANY.items() if k != missing}
    with pytest.raises(PackError, match="knowledge must declare"):
        validate_manifest_shape(_manifest("demo", knowledge))


def test_an_unknown_key_in_the_block_is_refused_rather_than_ignored():
    """The manifest's field sets are exact everywhere else for this reason: a typo'd key that
    is silently dropped leaves a pack claiming a scope it does not have."""
    with pytest.raises(PackError, match="knowledge must declare"):
        validate_manifest_shape(_manifest("demo", {**COMPANY, "scopes": ["company"]}))


@pytest.mark.parametrize("scope", ["Company", "company:", ":x", "company:JoyPla", "a b"])
def test_a_malformed_scope_is_refused(scope):
    with pytest.raises(PackError, match="knowledge scope"):
        validate_manifest_shape(_manifest("demo", {**COMPANY, "scope": [scope]}))


def test_topics_must_be_sorted_and_unique():
    """Slugs, where declaration order carries nothing — so leaving it free would admit
    differences between two manifests that describe the same pack."""
    with pytest.raises(PackError, match="knowledge topics"):
        validate_manifest_shape(_manifest("demo", {**COMPANY, "topics": ["backup", "access"]}))


def test_evidence_is_not_required_to_be_sorted():
    """Unlike every other list here, and deliberately. These are document titles a person
    wrote, in the language they wrote them: codepoint order over prose is not an order any
    author can predict — "運用設計書" sorts after "情報セキュリティ規程" for a reason nobody
    reading either would guess — so the rule could only be obeyed by trial and error. And the
    order carries meaning a sort would destroy: a citation list leads with the document the
    answer chiefly rests on."""
    validate_manifest_shape(_manifest("demo", {**COMPANY, "evidence": ["運用設計書", "規程"]}))


def test_a_repeated_evidence_label_is_still_refused():
    """Order is free; duplication is not. The same document cited twice is a mistake, and it
    would inflate how well-sourced an answer looks."""
    with pytest.raises(PackError, match="knowledge evidence"):
        validate_manifest_shape(_manifest("demo", {**COMPANY, "evidence": ["規程", "規程"]}))


def test_reviewed_at_requires_a_timezone():
    """A bare local timestamp is unreadable to everyone but the machine that wrote it, and
    staleness is the whole reason the field exists."""
    with pytest.raises(PackError, match="reviewed_at requires timezone"):
        validate_manifest_shape(_manifest("demo", {**COMPANY, "reviewed_at": "2026-08-01"}))


def test_a_secret_in_the_block_is_refused_like_anywhere_else_in_the_manifest():
    """The block is nested, and the manifest's secret scan recurses. Checked because a new
    nesting level is exactly where such a scan silently stops applying."""
    with pytest.raises(PackError):
        validate_manifest_shape(_manifest("demo", {
            **COMPANY, "owner": "aws_secret_access_key=AKIAIOSFODNN7EXAMPLE"}))


# ── selection ─────────────────────────────────────────────────────────────────


def test_the_issue_example_returns_both_candidates_and_refuses_to_pick(tmp_path):
    """"Do you take backups?" — the company and the product both claim the topic, and the
    right answer differs. The selector returns both and says the scope is unsettled; it does
    not rank them, and it does not return the first."""
    root = _scope(tmp_path, {"company-security": COMPANY, "product-security": PRODUCT})

    report = knowledge_rows(root, topics=("backup",))

    assert [row["id"] for row in report["candidates"]] == [
        "company-security", "product-security"]
    assert report["ambiguous"]
    assert report["scopes"] == ["company", "product:joypla-one"]


def test_naming_the_scope_settles_it(tmp_path):
    root = _scope(tmp_path, {"company-security": COMPANY, "product-security": PRODUCT})

    report = knowledge_rows(root, topics=("backup",), scopes=("company",))

    assert [row["id"] for row in report["candidates"]] == ["company-security"]
    assert not report["ambiguous"]


def test_a_bare_dimension_matches_every_value_under_it(tmp_path):
    """Somebody narrowing to "the product, whichever one" should not have to know its slug."""
    root = _scope(tmp_path, {"company-security": COMPANY, "product-security": PRODUCT})

    report = knowledge_rows(root, scopes=("product",))

    assert [row["id"] for row in report["candidates"]] == ["product-security"]


def test_a_valued_scope_does_not_match_a_pack_claiming_only_the_dimension(tmp_path):
    """The reverse of the rule above, and the half that matters: an answer about one product
    must not be sourced from a pack that never claimed to be about that product."""
    generic = {**PRODUCT, "scope": ["product"]}
    root = _scope(tmp_path, {"product-security": generic})

    assert knowledge_rows(root, scopes=("product:joypla-one",))["candidates"] == []


def test_two_packs_at_one_scope_are_not_an_ambiguity(tmp_path):
    """Ambiguity here is about scopes, not counts. Two company packs are two sources for one
    scope and an answer should rest on both; treating that as a question to ask would send
    people back a prompt with nothing to choose between."""
    root = _scope(tmp_path, {"corp-a": COMPANY, "corp-b": {**COMPANY, "owner": "Legal"}})

    report = knowledge_rows(root, topics=("backup",))

    assert len(report["candidates"]) == 2
    assert not report["ambiguous"]


def test_a_pack_with_no_declaration_is_never_a_candidate(tmp_path):
    """Silence is not a claim to every scope. A pack that has not said what it is about must
    not be read into an answer."""
    root = _scope(tmp_path, {"company-security": COMPANY, "silent": None})

    assert [row["id"] for row in knowledge_rows(root)["candidates"]] == ["company-security"]


def test_a_topic_nobody_claims_returns_nothing_rather_than_everything(tmp_path):
    """The failure mode of a selector that falls back to "all of them" is worse than empty:
    it hands the answering side material about something else and calls it evidence."""
    root = _scope(tmp_path, {"company-security": COMPANY, "product-security": PRODUCT})

    report = knowledge_rows(root, topics=("payroll",))

    assert report["candidates"] == []
    assert not report["ambiguous"]


def test_every_candidate_carries_its_evidence_and_owner(tmp_path):
    """What the answering side cites. Without this the selection is only a pack list, and the
    answer would have to name its sources from somewhere other than the pack that supplied
    them."""
    root = _scope(tmp_path, {"company-security": COMPANY})

    row = knowledge_rows(root, topics=("backup",))["candidates"][0]

    assert row["evidence"] == COMPANY["evidence"]
    assert (row["owner"], row["reviewed_at"]) == (COMPANY["owner"], COMPANY["reviewed_at"])


def test_an_unreadable_pack_is_skipped_rather_than_half_read(tmp_path):
    """This feeds a citation. A pack whose contents failed validation must not appear behind
    one, so the selector drops it instead of reporting what its manifest claimed."""
    root = _scope(tmp_path, {"company-security": COMPANY, "broken": PRODUCT})
    (root / "packs" / "broken" / "pack.yaml").write_text("{not json", encoding="utf-8")

    assert [row["id"] for row in knowledge_rows(root)["candidates"]] == ["company-security"]
