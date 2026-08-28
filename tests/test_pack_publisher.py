import base64
import copy
import datetime as dt
import importlib
import json
import pathlib
import subprocess
import sys
import zipfile

import pytest

# There is deliberately no `importorskip("cryptography")` in this file. It signs and verifies
# publisher material, so cryptography is not optional to it — and cryptography is a *declared*
# dependency of the package, meaning its absence is a broken install rather than an absent
# option. The guard used to be on every test here, and it cost: an install whose cryptography
# imported but whose `_cffi_backend` did not turned all eleven of these into skips, and a skip
# is the one result nobody scans a log for. A broken install must be loud.

from test_pack_sdk_phase4d import _resource_pack
from test_pack_lifecycle import _quality_pack
from test_packs import _write_pack


def _key_material(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    key_path = tmp_path / "publisher.pem"
    key_path.write_bytes(private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    root = {
        "key_id": "test-2026", "signer": "Rig Test Publisher",
        "public_key": base64.b64encode(public).decode("ascii"),
        "valid_from": "2020-01-01T00:00:00+00:00", "valid_until": None,
        "revoked_at": None,
    }
    return private, key_path, {
        "publisher_trust_roots_schema_version": 1, "keys": [root],
    }


@pytest.fixture
def without_cryptography(monkeypatch):
    """Make `cryptography` unimportable, as in an install that omitted it."""

    class _Absent:
        def find_spec(self, name, path=None, target=None):
            if name == "cryptography" or name.startswith("cryptography."):
                raise ModuleNotFoundError(f"No module named {name!r}", name=name)
            return None

    for name in [item for item in sys.modules
                 if item == "cryptography" or item.startswith("cryptography.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [_Absent(), *sys.meta_path])
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("cryptography")


def _forged_signature(pack, manifest):
    """Write a signature whose envelope is genuine and whose Ed25519 signature is not.

    Everything a signature check can verify without cryptography already matches
    the pack, so only the Ed25519 check separates this forgery from real trust.
    """
    from rig_workbench.packs.manifest import canonical
    from rig_workbench.packs.publisher import signed_envelope

    envelope = signed_envelope(
        pack, manifest, signer="Rig Test Publisher", key_id="test-2026",
        issued_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )
    (pack / "pack.sig.json").write_text(canonical({
        "pack_signature_schema_version": 1, "signed": envelope,
        "signature": base64.b64encode(bytes(64)).decode("ascii"),
    }), encoding="utf-8")
    return envelope


def test_verification_without_cryptography_never_reports_publisher_trust(
    tmp_path, without_cryptography,
):
    from rig_workbench.packs.installer import verification_status
    from rig_workbench.packs.manifest import digest, read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.publisher import verify_publisher_signature

    unsigned = _resource_pack(tmp_path / "unsigned", "unsigned-pack")
    _raw, manifest = read_json_yaml(unsigned / "pack.yaml")
    assert verify_publisher_signature(unsigned, manifest) is None
    assert verification_status(unsigned, manifest) == ("verified-local", None)

    forged = _resource_pack(tmp_path / "forged", "forged-pack")
    _raw, forged_manifest = read_json_yaml(forged / "pack.yaml")
    envelope = _forged_signature(forged, forged_manifest)
    assert envelope["manifest_sha256"] == digest(forged / "pack.yaml")
    with pytest.raises(PackError, match="requires cryptography"):
        verify_publisher_signature(forged, forged_manifest)
    with pytest.raises(PackError, match="requires cryptography"):
        verification_status(forged, forged_manifest)


def test_install_without_cryptography_refuses_publisher_trust(
    tmp_path, without_cryptography,
):
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.lock import validate_lock_root
    from rig_workbench.packs.manifest import read_json_yaml
    from rig_workbench.packs.model import PackError

    project = tmp_path / "project"
    root = project / ".rig/packs"
    forged = _resource_pack(tmp_path / "forged", "forged-pack")
    _raw, forged_manifest = read_json_yaml(forged / "pack.yaml")
    _forged_signature(forged, forged_manifest)
    with pytest.raises(PackError, match="requires cryptography"):
        install_pack(forged, scope="project", project=project, allow_unverified=True)
    assert not (root / "forged-pack").exists()

    unsigned = _resource_pack(tmp_path / "unsigned", "unsigned-pack")
    with pytest.raises(PackError, match="allow-unverified"):
        install_pack(unsigned, scope="project", project=project)

    result = install_pack(unsigned, scope="project", project=project, allow_unverified=True)
    assert result.verification_status == "verified-local"
    assert [(item["id"], item["verification_status"],
             item["publisher_key_id"], item["signed_digest"])
            for item in validate_lock_root(root)] == [
        ("unsigned-pack", "verified-local", None, None)]


def test_lock_and_doctor_without_cryptography_reject_a_publisher_claim(
    tmp_path, without_cryptography,
):
    from rig_workbench.packs.doctor import diagnose
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.lock import read_lock, validate_lock_root, write_lock
    from rig_workbench.packs.manifest import read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.publisher import signed_digest

    project = tmp_path / "project"
    root = project / ".rig/packs"
    result = install_pack(
        _resource_pack(tmp_path / "source", "claimed-pack"),
        scope="project", project=project, allow_unverified=True,
    )
    assert diagnose(project=project)["status"] == "ok"

    _raw, manifest = read_json_yaml(result.path / "pack.yaml")
    envelope = _forged_signature(result.path, manifest)
    lock = read_lock(root)
    lock["packs"][0].update({
        "verification_status": "verified-publisher",
        "publisher_key_id": envelope["key_id"],
        "signed_digest": signed_digest(envelope),
    })
    write_lock(root, lock)

    with pytest.raises(PackError, match="requires cryptography"):
        validate_lock_root(root)
    assert diagnose(project=project)["status"] == "failed"


def test_signing_and_keygen_without_cryptography_fail_closed(
    tmp_path, without_cryptography,
):
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.publisher import generate_publisher_key, sign_pack

    repository = tmp_path / "repository"
    pack = _resource_pack(repository, "unsignable-pack")
    _commit_pack(repository, pack)
    secure = tmp_path / "secure"
    secure.mkdir(mode=0o700)
    with pytest.raises(PackError, match="pack signing requires cryptography"):
        sign_pack(pack, private_key_path=secure / "publisher.pem",
                  key_id="test-2026", signer="Rig Test Publisher")
    with pytest.raises(PackError, match="publisher key generation requires cryptography"):
        generate_publisher_key(
            private_key_path=secure / "publisher.pem",
            trust_roots_path=repository / "trust-roots.json",
            key_id="test-2026", signer="Rig Test Publisher",
            source_repository=repository,
        )
    assert not (secure / "publisher.pem").exists()


def test_keygen_keeps_private_external_and_registers_only_public_material(
    tmp_path, monkeypatch, capsys,
):
    from rig_workbench.packs.cli import cmd_pack
    from rig_workbench.packs.manifest import canonical
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.publisher import generate_publisher_key

    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    roots = repository / "trust-roots.json"
    roots.write_text(canonical({
        "publisher_trust_roots_schema_version": 1, "keys": [],
    }), encoding="utf-8")
    roots.chmod(0o644)
    secure = tmp_path / "secure"
    secure.mkdir(mode=0o700)
    private = secure / "publisher.pem"

    monkeypatch.chdir(repository)
    assert cmd_pack([
        "keygen", "--private-key", str(private), "--trust-roots", str(roots),
        "--key-id", "release-2026", "--signer", "Rig Release",
    ]) == 0
    assert "publisher key registered: release-2026" in capsys.readouterr().out
    assert private.is_file() and (private.stat().st_mode & 0o777) == 0o600
    assert b"PRIVATE KEY" in private.read_bytes()
    document = json.loads(roots.read_text(encoding="utf-8"))
    assert [item["key_id"] for item in document["keys"]] == ["release-2026"]
    assert "PRIVATE" not in roots.read_text(encoding="utf-8")
    assert not any(path.suffix == ".pem" for path in repository.rglob("*"))

    with pytest.raises(PackError, match="outside"):
        generate_publisher_key(
            private_key_path=repository / "forbidden.pem", trust_roots_path=roots,
            key_id="forbidden", signer="Rig Release", source_repository=repository,
        )
    subdirectory = repository / "nested"
    subdirectory.mkdir()
    with pytest.raises(PackError, match="outside"):
        generate_publisher_key(
            private_key_path=repository / "nested-private.pem", trust_roots_path=roots,
            key_id="nested", signer="Rig Release", source_repository=subdirectory,
        )
    outside_roots = secure / "trust-roots.json"
    outside_roots.write_text(canonical({
        "publisher_trust_roots_schema_version": 1, "keys": [],
    }), encoding="utf-8")
    with pytest.raises(PackError, match="inside"):
        generate_publisher_key(
            private_key_path=secure / "outside-roots.pem", trust_roots_path=outside_roots,
            key_id="outside-roots", signer="Rig Release", source_repository=repository,
        )
    linked = secure / "linked.pem"
    linked.symlink_to(secure / "target.pem")
    with pytest.raises(PackError, match="symlink"):
        generate_publisher_key(
            private_key_path=linked, trust_roots_path=roots,
            key_id="linked", signer="Rig Release", source_repository=repository,
        )


def test_keygen_detects_trust_root_replacement_and_rolls_back_private_key(
    tmp_path, monkeypatch,
):
    from rig_workbench.packs.manifest import canonical
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs import publisher

    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    roots = repository / "trust-roots.json"
    roots.write_text(canonical({
        "publisher_trust_roots_schema_version": 1, "keys": [],
    }), encoding="utf-8")
    secure = tmp_path / "secure"
    secure.mkdir(mode=0o700)
    private = secure / "publisher.pem"
    real_stat = publisher.os.stat
    roots_checks = 0

    def replaced_stat(path, *args, **kwargs):
        nonlocal roots_checks
        value = real_stat(path, *args, **kwargs)
        if path == roots.name and kwargs.get("dir_fd") is not None:
            roots_checks += 1
            if roots_checks == 1:
                changed = list(value)
                changed[1] += 1
                return publisher.os.stat_result(changed)
        return value

    monkeypatch.setattr(publisher.os, "stat", replaced_stat)
    with pytest.raises(PackError, match="changed during"):
        publisher.generate_publisher_key(
            private_key_path=private, trust_roots_path=roots,
            key_id="raced", signer="Rig Release", source_repository=repository,
        )
    assert not private.exists()
    assert json.loads(roots.read_text(encoding="utf-8"))["keys"] == []


def test_keygen_detects_transaction_file_substitution(tmp_path, monkeypatch):
    from rig_workbench.packs.manifest import canonical
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs import publisher

    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    roots = repository / "trust-roots.json"
    roots.write_text(canonical({
        "publisher_trust_roots_schema_version": 1, "keys": [],
    }), encoding="utf-8")
    secure = tmp_path / "secure"
    secure.mkdir(mode=0o700)
    private = secure / "publisher.pem"
    real_stat = publisher.os.stat

    def substituted_stat(path, *args, **kwargs):
        value = real_stat(path, *args, **kwargs)
        if isinstance(path, str) and path.startswith(".trust-roots."):
            changed = list(value)
            changed[1] += 1
            return publisher.os.stat_result(changed)
        return value

    monkeypatch.setattr(publisher.os, "stat", substituted_stat)
    with pytest.raises(PackError, match="transaction file changed"):
        publisher.generate_publisher_key(
            private_key_path=private, trust_roots_path=roots,
            key_id="temp-raced", signer="Rig Release", source_repository=repository,
        )
    assert not private.exists()
    assert json.loads(roots.read_text(encoding="utf-8"))["keys"] == []


def test_keygen_descriptor_walk_rejects_ancestor_substitution(tmp_path, monkeypatch):
    from rig_workbench.packs.manifest import canonical
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs import publisher

    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    roots = repository / "trust-roots.json"
    roots.write_text(canonical({
        "publisher_trust_roots_schema_version": 1, "keys": [],
    }), encoding="utf-8")
    secure_ancestor = tmp_path / "secure-ancestor"
    secure_parent = secure_ancestor / "keys"
    secure_parent.mkdir(parents=True, mode=0o700)
    secure_ancestor.chmod(0o700)
    attacker = tmp_path / "attacker"
    (attacker / "keys").mkdir(parents=True, mode=0o700)
    attacker.chmod(0o700)
    private = secure_parent / "publisher.pem"
    original_reject = publisher._reject_symlink_path
    checks = 0

    def substitute_after_lexical_check(path, *, include_target):
        nonlocal checks
        original_reject(path, include_target=include_target)
        checks += 1
        if checks == 2:
            secure_ancestor.rename(tmp_path / "secure-original")
            secure_ancestor.symlink_to(attacker, target_is_directory=True)

    monkeypatch.setattr(publisher, "_reject_symlink_path", substitute_after_lexical_check)
    with pytest.raises(PackError, match="path changed"):
        publisher.generate_publisher_key(
            private_key_path=private, trust_roots_path=roots,
            key_id="ancestor-raced", signer="Rig Release", source_repository=repository,
        )
    assert not (attacker / "keys/publisher.pem").exists()
    assert json.loads(roots.read_text(encoding="utf-8"))["keys"] == []


def _commit_pack(repository: pathlib.Path, pack: pathlib.Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "publisher@example.test"],
                   cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Publisher Test"],
                   cwd=repository, check=True)
    subprocess.run(["git", "add", pack.relative_to(repository)], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "pack release"], cwd=repository, check=True)


def _write_signature(pack, manifest, private, *, issued_at, engine_release=None):
    from rig_workbench import __version__
    from rig_workbench.packs.manifest import canonical
    from rig_workbench.packs.publisher import signed_envelope

    envelope = signed_envelope(
        pack, manifest, signer="Rig Test Publisher", key_id="test-2026",
        issued_at=issued_at, engine_release=engine_release or __version__,
    )
    document = {
        "pack_signature_schema_version": 1, "signed": envelope,
        "signature": base64.b64encode(
            private.sign(canonical(envelope).encode("utf-8"))
        ).decode("ascii"),
    }
    (pack / "pack.sig.json").write_text(canonical(document), encoding="utf-8")
    return document


def test_ed25519_sign_install_lock_and_doctor_end_to_end(tmp_path, monkeypatch):
    from rig_workbench.packs import publisher
    from rig_workbench.packs.doctor import diagnose
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.lock import read_lock, validate_lock_root
    from rig_workbench.packs.manifest import read_json_yaml

    repository = tmp_path / "repository"
    pack = _resource_pack(repository, "publisher-pack")
    _commit_pack(repository, pack)
    _private, key_path, roots = _key_material(tmp_path)
    publisher.sign_pack(
        pack, private_key_path=key_path, key_id="test-2026",
        signer="Rig Test Publisher",
    )
    monkeypatch.setattr(publisher, "load_trust_roots", lambda: roots)
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    verified = publisher.verify_publisher_signature(pack, manifest)
    assert verified["key_id"] == "test-2026"
    assert len(verified["signed_digest"]) == 64

    project = tmp_path / "project"
    result = install_pack(pack, scope="project", project=project)
    assert result.verification_status == "verified-publisher"
    entries = validate_lock_root(project / ".rig/packs")
    assert entries[0]["publisher_key_id"] == "test-2026"
    assert entries[0]["signed_digest"] == verified["signed_digest"]
    # A literal, not the constant: a lock format change is a migration question for every
    # installed project, and this assertion is the canary that makes somebody answer it. The
    # version moved 2 -> 3 when a git source gained `source_id` and `revision` (#523 S2), and
    # 3 -> 4 when entries began recording which version satisfied each dependency (S4).
    assert read_lock(project / ".rig/packs")["pack_lock_schema_version"] == 4
    assert diagnose(project=project)["status"] == "ok"
    installed_signature = result.path / "pack.sig.json"
    tampered = json.loads(installed_signature.read_text(encoding="utf-8"))
    tampered["signature"] = base64.b64encode(b"x" * 64).decode("ascii")
    from rig_workbench.packs.manifest import canonical
    installed_signature.write_text(canonical(tampered), encoding="utf-8")
    from rig_workbench.packs.model import PackError
    with pytest.raises(PackError, match="signature is invalid"):
        validate_lock_root(project / ".rig/packs")
    assert diagnose(project=project)["status"] == "failed"
    assert diagnose(result.path, project=project)["status"] == "failed"


def test_signature_rejects_unknown_revoked_future_invalid_and_replay(
    tmp_path, monkeypatch,
):
    from rig_workbench import __version__
    from rig_workbench.packs import publisher
    from rig_workbench.packs.manifest import canonical, read_json_yaml
    from rig_workbench.packs.model import PackError

    pack = _resource_pack(tmp_path / "source", "signed-one")
    private, _key_path, roots = _key_material(tmp_path)
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    now = dt.datetime.now(dt.timezone.utc)
    issued = now.isoformat(timespec="seconds")
    document = _write_signature(pack, manifest, private, issued_at=issued)

    with pytest.raises(PackError, match="unknown"):
        publisher.verify_publisher_signature(
            pack, manifest,
            trust_roots={"publisher_trust_roots_schema_version": 1, "keys": []},
        )
    revoked = copy.deepcopy(roots)
    revoked["keys"][0]["revoked_at"] = issued
    with pytest.raises(PackError, match="revoked"):
        publisher.verify_publisher_signature(pack, manifest, trust_roots=revoked)
    with pytest.raises(PackError, match="not yet valid"):
        publisher.verify_publisher_signature(
            pack, manifest, trust_roots=roots, now=now - dt.timedelta(days=1),
        )

    invalid = copy.deepcopy(document)
    invalid["signature"] = base64.b64encode(b"x" * 64).decode("ascii")
    (pack / "pack.sig.json").write_text(canonical(invalid), encoding="utf-8")
    with pytest.raises(PackError, match="invalid"):
        publisher.verify_publisher_signature(pack, manifest, trust_roots=roots)

    (pack / "pack.sig.json").write_text(canonical(document), encoding="utf-8")
    replay = _resource_pack(tmp_path / "replay", "signed-two")
    (replay / "pack.sig.json").write_text(canonical(document), encoding="utf-8")
    _raw, replay_manifest = read_json_yaml(replay / "pack.yaml")
    with pytest.raises(PackError, match="binding mismatch"):
        publisher.verify_publisher_signature(replay, replay_manifest, trust_roots=roots)

    old_engine = _write_signature(
        pack, manifest, private, issued_at=issued, engine_release="0.0.1",
    )
    assert old_engine["signed"]["engine_release"] != __version__
    with pytest.raises(PackError, match="engine release mismatch"):
        publisher.verify_publisher_signature(pack, manifest, trust_roots=roots)


def test_manifest_asset_version_and_compatibility_tamper_are_bound(tmp_path):
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.publisher import verify_publisher_signature

    private, _key_path, roots = _key_material(tmp_path)
    mutations = ("asset", "version", "compatibility")
    for mutation in mutations:
        pack = _resource_pack(tmp_path / mutation, f"tamper-{mutation}")
        _raw, manifest = read_json_yaml(pack / "pack.yaml")
        _write_signature(
            pack, manifest, private,
            issued_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        )
        if mutation == "asset":
            asset = pack / "resources/guide.html"
            asset.write_text("<!doctype html><p>changed</p>\n", encoding="utf-8")
            manifest["hashes"]["resources/guide.html"] = digest(asset)
            manifest["resources"]["resources/guide.html"]["sha256"] = digest(asset)
            manifest["resources"]["resources/guide.html"]["size"] = asset.stat().st_size
            (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
        elif mutation == "version":
            manifest["version"] = "1.0.1"
            (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
        else:
            raw, compat = read_json_yaml(pack / "compatibility.yaml")
            compat["platforms"] = ["linux"]
            (pack / "compatibility.yaml").write_text(canonical(compat), encoding="utf-8")
        with pytest.raises(PackError, match="binding mismatch"):
            verify_publisher_signature(pack, manifest, trust_roots=roots)


def test_signing_refuses_dirty_source_and_non_green_quality(tmp_path, monkeypatch):
    from rig_workbench.packs import installer
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.publisher import sign_pack

    repository = tmp_path / "repository"
    pack = _resource_pack(repository, "dirty-pack")
    _commit_pack(repository, pack)
    _private, key_path, _roots = _key_material(tmp_path)
    (pack / "UNCOMMITTED").write_text("dirty", encoding="utf-8")
    with pytest.raises(PackError, match="dirty or uncommitted"):
        sign_pack(
            pack, private_key_path=key_path, key_id="test-2026",
            signer="Rig Test Publisher",
        )
    (pack / "UNCOMMITTED").unlink()
    monkeypatch.setattr(
        installer, "local_quality_status", lambda *_args, **_kwargs: "unverified",
    )
    with pytest.raises(PackError, match="non-mock current green"):
        sign_pack(
            pack, private_key_path=key_path, key_id="test-2026",
            signer="Rig Test Publisher",
        )


def test_valid_publisher_signature_cannot_upgrade_mock_or_non_green_quality(
    tmp_path, monkeypatch,
):
    from rig_workbench.packs import installer, publisher
    from rig_workbench.packs.manifest import read_json_yaml
    from rig_workbench.packs.model import PackError

    pack = _resource_pack(tmp_path / "quality-bound", "quality-bound")
    private, _key_path, roots = _key_material(tmp_path)
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    _write_signature(
        pack, manifest, private,
        issued_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    )
    monkeypatch.setattr(publisher, "load_trust_roots", lambda: roots)
    monkeypatch.setattr(
        installer, "local_quality_status", lambda *_args, **_kwargs: "unverified",
    )
    with pytest.raises(PackError, match="mock, mismatched, or non-green"):
        installer.verification_status(pack, manifest)


def test_signing_refuses_legacy_prompt_case_without_composition_or_durable_evidence(tmp_path):
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.publisher import sign_pack

    repository = tmp_path / "legacy-repository"
    pack = _write_pack(repository, "legacy-prompt", recipe=True)
    _commit_pack(repository, pack)
    _private, key_path, _roots = _key_material(tmp_path)
    with pytest.raises(PackError, match="composition and distinct expectations"):
        sign_pack(
            pack, private_key_path=key_path, key_id="test-2026",
            signer="Rig Test Publisher",
        )


def test_prompt_pack_with_composition_distinct_expectations_and_green_evidence_signs(
    tmp_path, monkeypatch,
):
    from rig_workbench.packs import installer, publisher
    from rig_workbench.packs.manifest import read_json_yaml

    repository = tmp_path / "quality-repository"
    pack = _quality_pack(repository, monkeypatch)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Rig", "-c", "user.email=rig@example.invalid",
         "commit", "-qm", "durable green evidence"],
        cwd=repository, check=True,
    )
    _private, key_path, roots = _key_material(tmp_path)
    document = publisher.sign_pack(
        pack, private_key_path=key_path, key_id="test-2026",
        signer="Rig Test Publisher",
    )
    assert document["signed"]["eval_case_tree_sha256"] != document["signed"][
        "eval_result_tree_sha256"
    ]
    monkeypatch.delenv("RIG_EVAL_ATTESTATION_KEY", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "clean-state"))
    monkeypatch.setattr(publisher, "load_trust_roots", lambda: roots)
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    status, verified = installer.verification_status(pack, manifest)
    assert status == "verified-publisher" and verified["key_id"] == "test-2026"


def test_packaged_trust_roots_are_immutable_resource_without_private_material():
    """The shipped roots carry publisher *public* keys and nothing else.

    This asserted `keys == []` until `dca3e17` added the 1.29.0 release key —
    verification has to bootstrap from a key that ships with the package, so an
    empty list stopped being the right shape. What must not change is that only
    public material ships: every entry is a bare Ed25519 public key (32 bytes),
    and no field carries private material.
    """
    from rig_workbench.packs.publisher import load_trust_roots

    roots = load_trust_roots()
    assert roots["publisher_trust_roots_schema_version"] == 1
    assert roots["keys"], "at least one publisher key must ship, or nothing verifies"

    for key in roots["keys"]:
        assert set(key) == {"key_id", "public_key", "signer",
                            "valid_from", "valid_until", "revoked_at"}, \
            f"unexpected field in a shipped trust root: {sorted(key)}"
        assert len(base64.b64decode(key["public_key"], validate=True)) == 32

    text = json.dumps(roots).lower()
    assert "private" not in text and "secret" not in text
    assert "-----begin" not in text


def test_wheel_contains_trust_roots_and_declares_crypto_runtime(tmp_path):
    from test_cli_smoke import _build_wheel_offline

    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    wheel = _build_wheel_offline(wheel_dir)
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "rig_workbench/packs/trust-roots.json" in names
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8").lower()
    assert "requires-dist: cryptography>=41" in metadata
