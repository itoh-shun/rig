import copy
import hashlib
import io
import json
import pathlib
import shutil
import subprocess
import tarfile
import zipfile

import pytest

from test_eval_cases import valid_case
from test_packs import _write_pack


def _quality_pack(root: pathlib.Path, monkeypatch) -> pathlib.Path:
    from rig_workbench import __version__
    from rig_workbench.eval.runner import run_case
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml

    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", "pack-quality-fixture-key-is-at-least-32-bytes")
    pack = _write_pack(root, "quality-pack", recipe=True)
    case_path = pack / "evals/cases/hello-case/case.json"
    case = copy.deepcopy(valid_case())
    case.update(id="hello-case", prompt_surfaces=["recipe:hello"],
                deterministic_checks=["exit:0"])
    case["provider_policy"] = {
        "mode": "allowlist", "allowed": ["command"], "models": ["fixture"],
        "judge_providers": ["command"], "judge_models": ["fixture"],
    }
    case_path.write_text(canonical(case), encoding="utf-8")
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    manifest["hashes"]["evals/cases/hello-case/case.json"] = digest(case_path)
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Rig", "-c", "user.email=rig@example.invalid",
         "commit", "-qm", "quality fixture"], cwd=root, check=True,
    )
    def judge(_case, _payload, _output):
        return {"status": "measured", "criteria": [
            {"id": "correct", "status": "pass", "score": 1.0},
        ]}
    judge.judge_provider = "command"
    judge.judge_model = "fixture"
    judge.judge_executor_version = __version__
    result_root = root / "generated-results"
    result_path, _result = run_case(
        case, repo=root, provider="command", model="fixture", repeat=3,
        phase="current", command="true", result_root=result_root, judge_adapter=judge,
    )
    bundled = pack / "evals/results/hello-case/current-command.json"
    bundled.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(result_path, bundled)
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    manifest["assets"]["eval-result"] = ["evals/results/hello-case/current-command.json"]
    manifest["hashes"]["evals/cases/hello-case/case.json"] = digest(case_path)
    manifest["hashes"]["evals/results/hello-case/current-command.json"] = digest(bundled)
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    return pack


def test_install_local_is_atomic_canonical_and_does_not_modify_source(tmp_path):
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.lock import read_lock, tree_hash
    from rig_workbench.packs.model import PackError

    source = _write_pack(tmp_path / "sources", "local-pack", recipe=False)
    before = tree_hash(source)
    root = tmp_path / "installed"
    result = install_pack(source, scope="project", project=tmp_path, root=root)

    assert result.path == root / "local-pack" and result.verification_status == "verified"
    assert tree_hash(source) == before
    lock = read_lock(root)
    assert lock["packs"][0]["id"] == "local-pack"
    assert (root / "pack.lock.json").read_text() == json.dumps(
        lock, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ) + "\n"
    with pytest.raises(PackError, match="already exists"):
        install_pack(source, scope="project", project=tmp_path, root=root)


@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_install_safe_zip_and_tar(tmp_path, kind):
    from rig_workbench.packs.installer import install_pack

    source = _write_pack(tmp_path / f"source-{kind}", f"archive-{kind}", recipe=False)
    if kind == "zip":
        archive = pathlib.Path(shutil.make_archive(str(tmp_path / "pack"), "zip",
                                                   root_dir=source.parent, base_dir=source.name))
    else:
        archive = pathlib.Path(shutil.make_archive(str(tmp_path / "pack"), "gztar",
                                                   root_dir=source.parent, base_dir=source.name))
    result = install_pack(archive, scope="project", project=tmp_path,
                          root=tmp_path / f"installed-{kind}")
    assert result.manifest["id"] == f"archive-{kind}"


def test_archive_rejects_traversal_symlink_and_compression_bomb_without_partial(tmp_path):
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.model import PackError

    root = tmp_path / "installed"
    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../outside", "bad")
    symlink = tmp_path / "symlink.tar"
    with tarfile.open(symlink, "w") as archive:
        member = tarfile.TarInfo("pack/link")
        member.type = tarfile.SYMTYPE
        member.linkname = "../outside"
        archive.addfile(member)
    bomb = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("pack/bomb", b"0" * (1024 * 1024))
    for source in (traversal, symlink, bomb):
        with pytest.raises(PackError, match="traversal|symlink|compression ratio"):
            install_pack(source, scope="project", project=tmp_path, root=root)
    assert not (tmp_path / "outside").exists()
    assert not [item for item in root.iterdir() if not item.name.startswith(".pack")]


def test_install_rejects_scan_findings_missing_dependency_and_incompatible_engine(tmp_path):
    from rig_workbench import __version__
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.model import PackError

    dangerous = _write_pack(tmp_path / "danger", "danger-pack", recipe=True)
    recipe = dangerous / "recipes/hello.md"
    recipe.write_text("---\nname: hello\nsteps: []\n---\ngit reset --hard\n", encoding="utf-8")
    _raw, manifest = read_json_yaml(dangerous / "pack.yaml")
    manifest["hashes"]["recipes/hello.md"] = digest(recipe)
    (dangerous / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    with pytest.raises(PackError, match="destructive"):
        install_pack(dangerous, scope="project", project=tmp_path,
                     root=tmp_path / "danger-installed", allow_unverified=True)

    missing = _write_pack(tmp_path / "missing", "dependent", recipe=False,
                          dependency=[{"id": "absent", "range": "*"}])
    with pytest.raises(PackError, match="missing dependency"):
        install_pack(missing, scope="project", project=tmp_path, root=tmp_path / "deps")

    incompatible = _write_pack(tmp_path / "incompat", "future-pack", recipe=False)
    _raw, manifest = read_json_yaml(incompatible / "pack.yaml")
    manifest["engine"] = ">=999.0.0"
    (incompatible / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    compat_path = incompatible / "compatibility.yaml"
    _raw, compat = read_json_yaml(compat_path)
    compat["engine"] = ">=999.0.0"
    compat_path.write_text(canonical(compat), encoding="utf-8")
    assert __version__ != "999.0.0"
    with pytest.raises(PackError, match="incompatible"):
        install_pack(incompatible, scope="project", project=tmp_path,
                     root=tmp_path / "future")


def test_unverified_prompt_pack_is_project_only_and_quality_fixture_installs(tmp_path, monkeypatch):
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.lock import read_lock
    from rig_workbench.packs.model import PackError

    unverified = _write_pack(tmp_path / "unverified", "unverified-pack", recipe=True)
    with pytest.raises(PackError, match="attested non-mock"):
        install_pack(unverified, scope="project", project=tmp_path,
                     root=tmp_path / "verified-required")
    with pytest.raises(PackError, match="restricted to project"):
        install_pack(unverified, scope="user", project=tmp_path,
                     root=tmp_path / "user", allow_unverified=True)
    installed = install_pack(unverified, scope="project", project=tmp_path,
                             root=tmp_path / "project", allow_unverified=True)
    assert installed.verification_status == "unverified"
    assert read_lock(tmp_path / "project")["packs"][0]["verification_status"] == "unverified"

    quality = _quality_pack(tmp_path / "quality", monkeypatch)
    verified = install_pack(quality, scope="project", project=tmp_path,
                            root=tmp_path / "quality-installed")
    assert verified.verification_status == "verified"


def test_lock_write_failure_rolls_back_install(tmp_path, monkeypatch):
    from rig_workbench.packs import installer
    from rig_workbench.packs.model import PackError

    source = _write_pack(tmp_path / "source", "rollback-pack", recipe=False)
    root = tmp_path / "installed"
    monkeypatch.setattr(installer, "write_lock", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(PackError("lock failure"))
    ))
    with pytest.raises(PackError, match="lock failure"):
        installer.install_pack(source, scope="project", project=tmp_path, root=root)
    assert not (root / "rollback-pack").exists()


def test_lock_drift_blocks_resolve_doctor_and_remove(tmp_path, monkeypatch):
    from rig_workbench.packs.doctor import diagnose
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.remover import remove_pack
    from rig_workbench.packs.resolver import resolve_asset

    project = tmp_path / "project"
    source = _write_pack(tmp_path / "source", "tampered-pack", recipe=True)
    installed = install_pack(source, scope="project", project=project,
                             allow_unverified=True)
    recipe = installed.path / "recipes/hello.md"
    recipe.write_text(recipe.read_text() + "tampered\n", encoding="utf-8")
    with pytest.raises(PackError, match="hash mismatch|lock drift"):
        resolve_asset("recipe", "hello", project=project)
    report = diagnose(project=project)
    assert report["status"] == "failed"
    assert any(item["code"] == "lock_drift" for item in report["findings"])
    with pytest.raises(PackError, match="hash mismatch|lock drift"):
        remove_pack("tampered-pack", scope="project", project=project, yes=True)


def test_remove_is_dry_run_then_yes_and_refuses_dependents(tmp_path):
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.remover import remove_pack

    project = tmp_path / "project"
    base = _write_pack(tmp_path / "base", "base-pack", recipe=False)
    install_pack(base, scope="project", project=project)
    target, removed = remove_pack("base-pack", scope="project", project=project)
    assert not removed and target.exists()
    dependent = _write_pack(tmp_path / "dependent", "dependent-pack", recipe=False,
                            dependency=[{"id": "base-pack", "range": "*"}])
    install_pack(dependent, scope="project", project=project)
    with pytest.raises(PackError, match="dependents"):
        remove_pack("base-pack", scope="project", project=project, yes=True)
    _target, removed = remove_pack("dependent-pack", scope="project", project=project, yes=True)
    assert removed
    _target, removed = remove_pack("base-pack", scope="project", project=project, yes=True)
    assert removed


def test_remove_delete_failure_restores_exact_lock_and_target(tmp_path, monkeypatch):
    from rig_workbench.packs import remover
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.model import PackError

    project = tmp_path / "project"
    source = _write_pack(tmp_path / "source-rollback", "remove-rollback", recipe=False)
    installed = install_pack(source, scope="project", project=project)
    lock_path = project / ".rig/packs/pack.lock.json"
    original = lock_path.read_bytes()
    monkeypatch.setattr(remover.shutil, "rmtree", lambda _path: (
        (_ for _ in ()).throw(OSError("delete denied"))
    ))
    with pytest.raises(PackError, match="trash delete failed.*rollback completed"):
        remover.remove_pack("remove-rollback", scope="project", project=project, yes=True)
    assert installed.path.is_dir()
    assert lock_path.read_bytes() == original


def test_remove_lock_failure_restores_target_without_changing_lock(tmp_path, monkeypatch):
    from rig_workbench.packs import remover
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.model import PackError

    project = tmp_path / "project-lock-rollback"
    source = _write_pack(tmp_path / "source-lock-rollback", "lock-rollback", recipe=False)
    installed = install_pack(source, scope="project", project=project)
    lock_path = project / ".rig/packs/pack.lock.json"
    original = lock_path.read_bytes()
    monkeypatch.setattr(remover, "write_lock", lambda *_args: (
        (_ for _ in ()).throw(PackError("lock denied"))
    ))
    with pytest.raises(PackError, match="lock denied"):
        remover.remove_pack("lock-rollback", scope="project", project=project, yes=True)
    assert installed.path.is_dir() and lock_path.read_bytes() == original


def test_lock_ownership_is_bidirectional_and_lockless_root_is_diagnosed(tmp_path):
    from rig_workbench.packs.doctor import diagnose
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.lock import validate_lock_root
    from rig_workbench.packs.model import PackError

    project = tmp_path / "project-owned"
    root = project / ".rig/packs"
    source = _write_pack(tmp_path / "owned-source", "owned-pack", recipe=False)
    install_pack(source, scope="project", project=project)
    _write_pack(root, "unowned-pack", recipe=False)
    with pytest.raises(PackError, match="directory ownership mismatch.*unowned-pack"):
        validate_lock_root(root)
    assert any(item["code"] == "lock_drift" for item in diagnose(project=project)["findings"])

    legacy_project = tmp_path / "legacy-project"
    _write_pack(legacy_project / ".rig/packs", "legacy-pack", recipe=False)
    report = diagnose(project=legacy_project)
    assert report["status"] == "warning"
    assert any(item["code"] == "unmanaged_pack_root" for item in report["findings"])


@pytest.mark.parametrize("mutation, expected", [
    (lambda result: result.update(execution_status="unavailable"), "execution_identity_unavailable"),
    (lambda result: result.update(executor_version="old"), "executor_version_mismatch"),
    (lambda result: result.update(model="unpinned"), "model_policy"),
    (lambda result: result["target"][0]["judge"]["criteria"][0].update(status="fail"),
     "semantic_criteria_failed"),
    (lambda result: result["target"][0]["judge"]["criteria"].append(
        copy.deepcopy(result["target"][0]["judge"]["criteria"][0])),
     "semantic_criteria_failed"),
])
def test_pack_quality_uses_canonical_eval_gate_policy(tmp_path, monkeypatch, mutation, expected):
    from rig_workbench.eval.attestation import sign_result_attestation
    from rig_workbench.eval.cases import canonical_json
    from rig_workbench.eval.gate import quality_result_failures
    from rig_workbench.packs.manifest import read_json_yaml

    pack = _quality_pack(tmp_path / "canonical-quality", monkeypatch)
    _raw, case = read_json_yaml(pack / "evals/cases/hello-case/case.json")
    _raw, result = read_json_yaml(pack / "evals/results/hello-case/current-command.json")
    mutation(result)
    result.pop("attestation")
    result.pop("result_sha256")
    result["result_sha256"] = hashlib.sha256(
        canonical_json(result).encode("utf-8")
    ).hexdigest()
    result["attestation"] = sign_result_attestation(result)
    assert any(item.startswith(expected) for item in quality_result_failures(result, case))


def test_pack_test_structural_mock_and_provider_unavailable(tmp_path, monkeypatch):
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.tester import test_pack

    pack = _write_pack(tmp_path / "source", "test-pack", recipe=True)
    case_path = pack / "evals/cases/hello-case/case.json"
    _raw, case = read_json_yaml(case_path)
    case["provider_policy"] = {"mode": "any", "allowed": []}
    case_path.write_text(canonical(case), encoding="utf-8")
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    manifest["hashes"]["evals/cases/hello-case/case.json"] = digest(case_path)
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", "pack-test-fixture-key-is-at-least-32-bytes")
    structural, code = test_pack(pack, project=tmp_path)
    assert code == 0 and structural["status"] == "structural_only"
    mock, code = test_pack(pack, project=tmp_path, provider="mock", model="fixture",
                           judge_provider="mock", judge_model="fixture")
    assert code == 0 and mock["status"] == "non_quality_mock"
    monkeypatch.setattr("rig_workbench.eval.runner.shutil.which", lambda _name: None)
    unavailable, code = test_pack(pack, project=tmp_path, provider="claude", model="fixture",
                                  judge_provider="claude", judge_model="fixture")
    assert code == 2 and unavailable["status"] == "provider_unavailable"
