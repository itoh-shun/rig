"""Publisher trust on the *resolve* path, not only on install/remove/doctor.

Every tamper test this repo had called `lock.validate_lock_root` directly and handed it
`verify_publisher_signature` itself, so all six of them kept passing when `packs.resolver`
started handing that same function `None` — the resolve path was the one caller nobody
drove through a tampered pack. `pack.sig.json` is not in `manifest["hashes"]`
(`validation.py` classes it a non-asset), so no other drift check in `validate_lock_root`
looks at it: with the verifier disarmed, replacing the signature bytes, presenting a
revoked key, or claiming `verified-publisher` in `pack.lock.json` with no signature file at
all were all resolved cleanly and reported as `verification_status='verified-publisher'`.

These tests drive `resolve_all` and `resolved_collection` — the functions every recipe,
persona and command actually goes through — rather than the lock validator underneath them.

`rig_workbench.packs.signature` is the patch point throughout: `packs.publisher` re-exports
the verify half for importers, but a re-export is a second binding, and the moved functions
resolve their own globals in `packs.signature`.
"""

import ast
import base64
import copy
import datetime as dt
import json
import pathlib

import pytest

# No `importorskip("cryptography")`, for the reason test_pack_publisher.py gives at length:
# cryptography is a declared dependency, so its absence is a broken install, and a skip is
# the one result nobody scans a log for.

from test_pack_publisher import _key_material, _write_signature
from test_pack_sdk_phase4d import _resource_pack
from test_packs import _write_pack

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
RESOLVER_SOURCE = REPO_ROOT / "rig_workbench" / "packs" / "resolver.py"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _signed_install(tmp_path, monkeypatch, pack_id="resolve-signed"):
    """Install a genuinely signed pack into a fresh project, and return the pieces."""
    from rig_workbench.packs import signature
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.manifest import read_json_yaml

    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user-home"))
    monkeypatch.delenv("RIG_ORG_HOME", raising=False)
    source = _resource_pack(tmp_path / "source", pack_id)
    private, _key_path, roots = _key_material(tmp_path)
    _raw, manifest = read_json_yaml(source / "pack.yaml")
    _write_signature(source, manifest, private, issued_at=_now())
    monkeypatch.setattr(signature, "load_trust_roots", lambda: roots)

    project = tmp_path / "project"
    result = install_pack(source, scope="project", project=project)
    # The precondition the rest of each test leans on: without this, a later refusal could
    # come from the pack never having been publisher-verified in the first place.
    assert result.verification_status == "verified-publisher"
    return project, result.path, roots


def test_a_tampered_signature_is_refused_by_resolve_all_not_only_by_the_lock_validator(
    tmp_path, monkeypatch,
):
    """Swap the Ed25519 signature bytes of an installed, signed pack; resolve must refuse.

    This is the reproduction from the review: before the resolver was handed `None` this
    raised `pack publisher signature is invalid`, and afterwards `resolve_all` returned the
    path and `resolved_collection` reported `verification_status='verified-publisher'`.
    """
    from rig_workbench.packs.manifest import canonical
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.resolver import resolve_all, resolved_collection

    project, installed, _roots = _signed_install(tmp_path, monkeypatch, "tampered-sig")

    # Resolution is clean while the signature is intact — so the refusal below is the
    # tampering being caught, not the fixture being broken.
    assert [record.verification_status for record in resolved_collection(project=project)
            if record.manifest["id"] == "tampered-sig"] == ["verified-publisher"]

    signature_path = installed / "pack.sig.json"
    document = json.loads(signature_path.read_text(encoding="utf-8"))
    document["signature"] = base64.b64encode(b"x" * 64).decode("ascii")
    signature_path.write_text(canonical(document), encoding="utf-8")

    with pytest.raises(PackError, match="pack publisher signature is invalid"):
        resolve_all("recipe", "anything", project=project)
    with pytest.raises(PackError, match="pack publisher signature is invalid"):
        resolved_collection(project=project)


def test_a_revoked_publisher_key_is_refused_by_resolve_all(tmp_path, monkeypatch):
    """Revocation and the validity window only bite where the verifier is actually run."""
    from rig_workbench.packs import signature
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.resolver import resolve_all, resolved_collection

    project, _installed, roots = _signed_install(tmp_path, monkeypatch, "revoked-key")

    revoked = copy.deepcopy(roots)
    revoked["keys"][0]["revoked_at"] = _now()
    monkeypatch.setattr(signature, "load_trust_roots", lambda: revoked)

    with pytest.raises(PackError, match="pack publisher key is revoked"):
        resolve_all("recipe", "anything", project=project)
    with pytest.raises(PackError, match="pack publisher key is revoked"):
        resolved_collection(project=project)


def test_forged_publisher_provenance_with_no_signature_file_is_refused_by_resolve_all(
    tmp_path, monkeypatch,
):
    """The security review's case: the claim lives in a file the attacker can also write.

    `pack.lock.json` sits beside the pack in `.rig/packs`; anyone who can edit one can edit
    the other. So `verification_status: "verified-publisher"` with a fabricated
    `publisher_key_id` and any well-formed `signed_digest` passes every *structural* check
    in `validate_lock_root`, and the pack ships no `pack.sig.json` at all. Only running the
    verifier — which returns `None` when there is no signature to read — catches it.
    """
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.lock import read_lock, write_lock
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.resolver import resolve_all, resolved_collection

    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user-home"))
    monkeypatch.delenv("RIG_ORG_HOME", raising=False)
    project = tmp_path / "project"
    source = _write_pack(tmp_path / "source", "forged-trust", recipe=False)
    result = install_pack(source, scope="project", project=project, allow_unverified=True)
    assert result.verification_status != "verified-publisher"
    assert not (result.path / "pack.sig.json").exists()

    root = project / ".rig/packs"
    lock = read_lock(root)
    entry = next(item for item in lock["packs"] if item["id"] == "forged-trust")
    entry["verification_status"] = "verified-publisher"
    entry["publisher_key_id"] = "rig-release-2026"
    entry["signed_digest"] = "b" * 64
    write_lock(root, lock)

    with pytest.raises(PackError, match="publisher signature changed"):
        resolve_all("recipe", "anything", project=project)
    with pytest.raises(PackError, match="publisher signature changed"):
        resolved_collection(project=project)


def test_the_publisher_signature_changed_branch_fires_on_install_and_on_doctor(
    tmp_path, monkeypatch,
):
    """`lock.py`'s last drift branch, which no test in the repo reached before.

    No test anywhere in the repo asserted on `pack lock drift: publisher signature changed`
    before this one: the branch that compares the re-verified `key_id`/`signed_digest`
    against the locked pair had never been exercised, in any caller. Both of its arms are covered here — a signature that disappeared
    (`verified is None`) on the install path, and a locked digest that no longer matches
    what the file verifies to, on the doctor path.
    """
    from rig_workbench.packs.doctor import diagnose
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.lock import read_lock, write_lock
    from rig_workbench.packs.model import PackError

    project, installed, _roots = _signed_install(tmp_path, monkeypatch, "drift-sig")
    root = project / ".rig/packs"
    assert diagnose(project=project)["status"] == "ok"

    # Arm one: the signature file is gone, so the verifier returns None while the lock
    # still claims a publisher. Reached through `install`, which re-validates the whole
    # destination root before it writes anything into it.
    (installed / "pack.sig.json").unlink()
    second = _write_pack(tmp_path / "second-source", "second-pack", recipe=False)
    with pytest.raises(PackError, match="publisher signature changed"):
        install_pack(second, scope="project", project=project, allow_unverified=True)
    assert not (root / "second-pack").exists()

    drift = [item for item in diagnose(project=project)["findings"]
             if item["code"] == "lock_drift"]
    assert drift and any("publisher signature changed" in item["detail"] for item in drift)

    # Arm two: the signature verifies, but to a digest the lock does not record.
    from rig_workbench.packs.manifest import read_json_yaml

    private, _key_path, roots = _key_material(tmp_path)
    _raw, manifest = read_json_yaml(installed / "pack.yaml")
    _write_signature(installed, manifest, private, issued_at=_now())
    lock = read_lock(root)
    entry = next(item for item in lock["packs"] if item["id"] == "drift-sig")
    entry["signed_digest"] = "c" * 64
    write_lock(root, lock)
    from rig_workbench.packs import signature as signature_module
    monkeypatch.setattr(signature_module, "load_trust_roots", lambda: roots)
    report = diagnose(project=project)
    assert report["status"] == "failed"
    assert any("publisher signature changed" in item["detail"]
               for item in report["findings"] if item["code"] == "lock_drift")


def test_the_resolve_path_hands_the_lock_a_real_verifier_and_never_none(
    tmp_path, monkeypatch,
):
    """A mechanical guard on the one argument the whole failure turned on.

    Two independent checks, because either alone can rot. The source-level one pins the
    call shape so that re-introducing `verify_publisher=None` in `resolver` fails here even
    if some future refactor stops these behavioural tests from reaching the lock at all.
    The behavioural one proves the call is really made at run time, which no AST walk can.
    Each asserts first that it found something to check: a guard that scans nothing passes.
    """
    from rig_workbench.packs import signature
    from rig_workbench.packs.resolver import resolve_all

    tree = ast.parse(RESOLVER_SOURCE.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and (getattr(node.func, "id", None) == "validate_lock_root"
                  or getattr(node.func, "attr", None) == "validate_lock_root")]
    assert calls, "no validate_lock_root call found in resolver.py — this guard scanned nothing"
    for call in calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        assert "verify_publisher" in keywords, (
            "resolver must pass verify_publisher explicitly (lock.PublisherVerifier)"
        )
        value = keywords["verify_publisher"]
        assert not (isinstance(value, ast.Constant) and value.value is None), (
            "resolver passes verify_publisher=None: the resolve path is fail-open and a "
            "tampered pack.sig.json reaches resolve_all unnoticed"
        )

    project, _installed, _roots = _signed_install(tmp_path, monkeypatch, "guard-sig")
    seen: list[str] = []

    def _disarmed(pack, manifest):
        seen.append(manifest["id"])
        raise AssertionError(f"verifier reached for {manifest['id']}")

    monkeypatch.setattr(signature, "verify_publisher_signature", _disarmed)
    with pytest.raises(AssertionError, match="verifier reached for guard-sig"):
        resolve_all("recipe", "anything", project=project)
    assert seen == ["guard-sig"], (
        "resolve_all did not call the publisher verifier for the installed signed pack"
    )
