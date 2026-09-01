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

import copy
import hashlib
import json
import pathlib

import pytest

from rig_workbench.packs.inventory import knowledge_rows
from rig_workbench.packs.manifest import (KNOWLEDGE_FIELDS, PACK_SCHEMA_VERSION, canonical,
                                          validate_manifest_shape)
from rig_workbench.packs.model import ASSET_DIRS, PackError
from test_eval_cases import valid_case

COMPANY = {
    "scope": ["company"],
    "topics": ["access-control", "backup", "encryption"],
    "owner": "Corp IT",
    "evidence": ["情報セキュリティ規程", "運用設計書"],
    "reviewed_at": "2026-08-01T00:00:00+00:00",
}
PRODUCT = {
    "scope": ["product:northwind-one"],
    "topics": ["backup", "sla"],
    "owner": "Northwind One Team",
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


def _scope(tmp_path: pathlib.Path, packs: dict[str, dict | None],
           wikis: dict[str, list[str]] | None = None,
           scope: str = "project") -> pathlib.Path:
    """A scope root whose lock owns `packs`, each written to disk as a valid manifest.

    Built at `<project>/.rig`, which is where a real project scope lives, rather than at some
    convenient scratch path. That matters for the document rows and not only for tidiness:
    whether a wiki is shadowed is decided by the tier resolver reading `.rig/packs`, so a
    fixture placed anywhere else would report every document as shadowed by nothing and the
    shadowing tests below would pass without exercising the thing they name.

    `knowledge_rows` reads each manifest from disk rather than from the lock, exactly as
    `pack list` reads `type` from disk, so the pack directory is the part that has to be real.
    """
    wikis = wikis or {}
    root = tmp_path / ".rig"
    entries = []
    # Sorted, because the lock refuses unsorted entries. Building the fixture through that
    # rule rather than around it keeps these tests reading a lock a real install could write.
    for pack_id in sorted(packs):
        knowledge = packs[pack_id]
        directory = root / "packs" / pack_id
        directory.mkdir(parents=True)
        manifest = _manifest(pack_id, knowledge)
        for name in wikis.get(pack_id, []):
            relative = f"facets/knowledge/{name}.md"
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# {name}\n", encoding="utf-8")
            manifest["assets"]["wiki"].append(relative)
            manifest["hashes"][relative] = hashlib.sha256(
                target.read_bytes()).hexdigest()
        if manifest["assets"]["wiki"]:
            # A wiki is prompt material, so two older rules apply to a knowledge pack exactly
            # as they do to any other: it must ship an evaluation case, and that case must be
            # bound by `prompt_surfaces` to the pack's own prompt assets. Built through those
            # rules rather than around them — a fixture that dodged them would be testing a
            # pack no install would accept, and the coupling is worth knowing about, since it
            # means a company's knowledge documents sit under the prompt-evaluation ratchet.
            case = copy.deepcopy(valid_case())
            case["id"] = f"{pack_id}-case"
            case["prompt_surfaces"] = sorted(f"wiki:{name}" for name in wikis[pack_id])
            relative = f"evals/cases/{pack_id}-case/case.json"
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(canonical(case), encoding="utf-8")
            manifest["assets"]["eval-case"] = [relative]
            manifest["hashes"][relative] = hashlib.sha256(target.read_bytes()).hexdigest()
        (directory / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
        (directory / "compatibility.yaml").write_text(canonical({
            "compatibility_schema_version": 1, "pack_id": pack_id, "pack_version": "1.0.0",
            "engine": "*", "platforms": ["any"],
        }), encoding="utf-8")
        entries.append({
            "id": pack_id, "version": "1.0.0", "kind": "project", "scope": scope,
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


@pytest.mark.parametrize("scope", ["Company", "company:", ":x", "company:Northwind", "a b"])
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

    report = knowledge_rows(tmp_path, root, topics=("backup",))

    assert [row["id"] for row in report["candidates"]] == [
        "company-security", "product-security"]
    assert report["ambiguous"]
    assert report["scopes"] == ["company", "product:northwind-one"]


def test_naming_the_scope_settles_it(tmp_path):
    root = _scope(tmp_path, {"company-security": COMPANY, "product-security": PRODUCT})

    report = knowledge_rows(tmp_path, root, topics=("backup",), scopes=("company",))

    assert [row["id"] for row in report["candidates"]] == ["company-security"]
    assert not report["ambiguous"]


def test_a_bare_dimension_matches_every_value_under_it(tmp_path):
    """Somebody narrowing to "the product, whichever one" should not have to know its slug."""
    root = _scope(tmp_path, {"company-security": COMPANY, "product-security": PRODUCT})

    report = knowledge_rows(tmp_path, root, scopes=("product",))

    assert [row["id"] for row in report["candidates"]] == ["product-security"]


def test_a_valued_scope_does_not_match_a_pack_claiming_only_the_dimension(tmp_path):
    """The reverse of the rule above, and the half that matters: an answer about one product
    must not be sourced from a pack that never claimed to be about that product."""
    generic = {**PRODUCT, "scope": ["product"]}
    root = _scope(tmp_path, {"product-security": generic})

    assert knowledge_rows(tmp_path, root, scopes=("product:northwind-one",))["candidates"] == []


def test_two_packs_at_one_scope_are_not_an_ambiguity(tmp_path):
    """Ambiguity here is about scopes, not counts. Two company packs are two sources for one
    scope and an answer should rest on both; treating that as a question to ask would send
    people back a prompt with nothing to choose between."""
    root = _scope(tmp_path, {"corp-a": COMPANY, "corp-b": {**COMPANY, "owner": "Legal"}})

    report = knowledge_rows(tmp_path, root, topics=("backup",))

    assert len(report["candidates"]) == 2
    assert not report["ambiguous"]


def test_a_pack_with_no_declaration_is_never_a_candidate(tmp_path):
    """Silence is not a claim to every scope. A pack that has not said what it is about must
    not be read into an answer."""
    root = _scope(tmp_path, {"company-security": COMPANY, "silent": None})

    assert [row["id"] for row in knowledge_rows(tmp_path, root)["candidates"]] == ["company-security"]


def test_a_topic_nobody_claims_returns_nothing_rather_than_everything(tmp_path):
    """The failure mode of a selector that falls back to "all of them" is worse than empty:
    it hands the answering side material about something else and calls it evidence."""
    root = _scope(tmp_path, {"company-security": COMPANY, "product-security": PRODUCT})

    report = knowledge_rows(tmp_path, root, topics=("payroll",))

    assert report["candidates"] == []
    assert not report["ambiguous"]


def test_every_candidate_carries_its_evidence_and_owner(tmp_path):
    """What the answering side cites. Without this the selection is only a pack list, and the
    answer would have to name its sources from somewhere other than the pack that supplied
    them."""
    root = _scope(tmp_path, {"company-security": COMPANY})

    row = knowledge_rows(tmp_path, root, topics=("backup",))["candidates"][0]

    assert row["evidence"] == COMPANY["evidence"]
    assert (row["owner"], row["reviewed_at"]) == (COMPANY["owner"], COMPANY["reviewed_at"])


def test_an_unreadable_pack_is_skipped_rather_than_half_read(tmp_path):
    """This feeds a citation. A pack whose contents failed validation must not appear behind
    one, so the selector drops it instead of reporting what its manifest claimed."""
    root = _scope(tmp_path, {"company-security": COMPANY, "broken": PRODUCT})
    (root / "packs" / "broken" / "pack.yaml").write_text("{not json", encoding="utf-8")

    assert [row["id"] for row in knowledge_rows(tmp_path, root)["candidates"]] == ["company-security"]


# ── handing the documents over ────────────────────────────────────────────────


def test_a_candidate_carries_the_documents_and_not_just_the_pack_name(tmp_path):
    """Without this, the answering side is told which pack to read and left to find the files
    itself — which means reimplementing tier resolution, or citing the wrong copy."""
    root = _scope(tmp_path, {"company-security": COMPANY},
                  wikis={"company-security": ["backup-policy"]})

    documents = knowledge_rows(tmp_path, root, topics=("backup",))["candidates"][0]["documents"]

    assert [(d["kind"], d["name"]) for d in documents] == [("wiki", "backup-policy")]


def test_a_document_is_addressed_by_uri_and_never_by_filesystem_path(tmp_path):
    """The pack model's own rule: `path` is an internal handle, and anything anybody else
    consumes gets the stable `pack://` form. A projection that leaked absolute paths would
    also leak the machine's directory layout into whatever quotes it."""
    root = _scope(tmp_path, {"company-security": COMPANY},
                  wikis={"company-security": ["backup-policy"]})

    document = knowledge_rows(tmp_path, root)["candidates"][0]["documents"][0]

    assert document["uri"] == (
        "pack://project/company-security/facets/knowledge/backup-policy.md")
    assert str(tmp_path) not in document["uri"]


def test_a_shadowed_document_says_so_and_names_the_winner(tmp_path, monkeypatch):
    """Shadowing is across tiers, never within one: two packs in the same scope may not both
    carry `wiki:backup-policy` — the collection validator calls that a same-tier collision and
    refuses the install. So the case that matters is a user-scope pack whose document a
    project pack overrides. Only the winner's text reaches a prompt, and reporting the loser
    as effective would put an answer behind a document nobody reads — a citation wrong in the
    one way a citation must never be."""
    user_home = tmp_path / "home"
    monkeypatch.setenv("RIG_USER_HOME", str(user_home))
    _scope(user_home, {"company-security": COMPANY},
           wikis={"company-security": ["backup-policy"]}, scope="user")
    _scope(tmp_path, {"product-security": PRODUCT},
           wikis={"product-security": ["backup-policy"]})

    report = knowledge_rows(tmp_path, user_home / ".rig", topics=("backup",))
    document = report["candidates"][0]["documents"][0]

    assert not document["effective"]
    assert document["provided_by"] == "product-security"


def test_a_shadowed_document_is_still_listed_rather_than_dropped(tmp_path, monkeypatch):
    """Hiding it would leave somebody asking where their file went; citing it silently would
    put an answer behind text nobody reads. The two failures are opposite, and this reports
    its way between them: listed, and labelled."""
    user_home = tmp_path / "home"
    monkeypatch.setenv("RIG_USER_HOME", str(user_home))
    _scope(user_home, {"company-security": COMPANY},
           wikis={"company-security": ["backup-policy"]}, scope="user")
    _scope(tmp_path, {"product-security": PRODUCT},
           wikis={"product-security": ["backup-policy"]})

    report = knowledge_rows(tmp_path, user_home / ".rig", topics=("backup",))

    assert len(report["candidates"][0]["documents"]) == 1


def test_a_document_nothing_else_claims_is_effective(tmp_path):
    """The other half of the flag. Without this, `effective` could be hard-wired False and
    the shadowing test above would still pass."""
    root = _scope(tmp_path, {"company-security": COMPANY},
                  wikis={"company-security": ["backup-policy"]})

    document = knowledge_rows(tmp_path, root, topics=("backup",))["candidates"][0]["documents"][0]

    assert document["effective"]
    assert document["provided_by"] == "company-security"


def test_a_pack_carrying_no_documents_reports_an_empty_list(tmp_path):
    """A declaration without material is a real state — a pack whose knowledge lives in an
    external source it has not wired up yet — and it should read as "nothing here", not as a
    missing key the caller has to guess at."""
    root = _scope(tmp_path, {"company-security": COMPANY})

    assert knowledge_rows(tmp_path, root)["candidates"][0]["documents"] == []


def test_the_uri_names_the_scope_the_pack_is_actually_installed_in(tmp_path, monkeypatch):
    """The tier segment comes from the lock, which is the record of where the pack was
    installed. A user-scope pack that announced itself as `pack://project/...` would be a
    public identifier pointing at a tier it is not in, and anything resolving it would look
    in the wrong place."""
    user_home = tmp_path / "home"
    monkeypatch.setenv("RIG_USER_HOME", str(user_home))
    _scope(user_home, {"company-security": COMPANY},
           wikis={"company-security": ["backup-policy"]}, scope="user")

    document = knowledge_rows(tmp_path, user_home / ".rig")["candidates"][0]["documents"][0]

    assert document["uri"].startswith("pack://user/company-security/")
