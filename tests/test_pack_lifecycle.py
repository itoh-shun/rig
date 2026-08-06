import copy
import hashlib
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
    from rig_workbench.packs.lock import tree_hash

    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", "pack-quality-fixture-key-is-at-least-32-bytes")
    pack = _write_pack(root, "quality-pack", recipe=True)
    case_path = pack / "evals/cases/hello-case/case.json"
    case = copy.deepcopy(valid_case())
    case.update(id="hello-case", prompt_surfaces=["recipe:hello"],
                deterministic_checks=["exit:0"], prompt_entrypoint="hello",
                prompt_composition=["recipe:hello"],
                target_expectations=["exit:0"],
                clean_expectations=["not_contains:impossible-clean-output"])
    case["provider_policy"] = {
        "mode": "allowlist", "allowed": ["codex"], "models": ["fixture"],
        "judge_providers": ["codex"], "judge_models": ["fixture"],
    }
    case_path.write_text(canonical(case), encoding="utf-8")
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    manifest["hashes"]["evals/cases/hello-case/case.json"] = digest(case_path)
    manifest.update(
        display_name="Quality pack", description="Publisher quality fixture",
        capabilities=["evaluation", "recipe"],
        entrypoints=[{"id": "hello", "kind": "recipe", "target": "hello"}],
        references=[], resources={},
    )
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
    judge.judge_provider = "codex"
    judge.judge_model = "fixture"
    judge.judge_executor_version = __version__
    result_root = root / "generated-results"
    monkeypatch.setattr(
        "rig_workbench.eval.runner._execute",
        lambda **_kwargs: (0, "ok", "", None),
    )
    from rig_workbench.packs.tester import compose_case_prompt, prompt_binding_sha256
    prompt = compose_case_prompt(pack, manifest, case, project=root)
    result_path, _result = run_case(
        case, repo=root, provider="codex", model="fixture", repeat=3,
        phase="current", result_root=result_root, judge_adapter=judge,
        prompt_prefix=prompt,
        prompt_binding_sha256=prompt_binding_sha256(manifest, case, prompt),
        pack_tree_sha256=tree_hash(pack),
    )
    bundled = pack / "evals/results/hello-case/current-codex.json"
    bundled.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(result_path, bundled)
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    manifest["assets"]["eval-result"] = ["evals/results/hello-case/current-codex.json"]
    manifest["hashes"]["evals/cases/hello-case/case.json"] = digest(case_path)
    manifest["hashes"]["evals/results/hello-case/current-codex.json"] = digest(bundled)
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    return pack


def test_install_local_is_atomic_canonical_and_does_not_modify_source(tmp_path):
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.lock import read_lock, tree_hash
    from rig_workbench.packs.model import PackError

    source = _write_pack(tmp_path / "sources", "local-pack", recipe=False)
    before = tree_hash(source)
    root = tmp_path / "installed"
    result = install_pack(
        source, scope="project", project=tmp_path, root=root, allow_unverified=True,
    )

    assert result.path == root / "local-pack" and result.verification_status == "verified-local"
    assert tree_hash(source) == before
    lock = read_lock(root)
    assert lock["packs"][0]["id"] == "local-pack"
    assert (root / "pack.lock.json").read_text() == json.dumps(
        lock, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ) + "\n"
    with pytest.raises(PackError, match="already exists"):
        install_pack(source, scope="project", project=tmp_path, root=root)


def test_unsigned_project_escape_hatch_cannot_target_other_or_external_roots(
    tmp_path, monkeypatch,
):
    from rig_workbench.packs.cli import cmd_pack
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.model import PackError

    project = tmp_path / "project"
    project.mkdir()
    source = _write_pack(tmp_path / "source", "placement-pack", recipe=False)
    user_home = tmp_path / "user-home"
    org_home = tmp_path / "org-home"
    monkeypatch.setenv("RIG_USER_HOME", str(user_home))
    monkeypatch.setenv("RIG_ORG_HOME", str(org_home))
    outside = tmp_path / "external-root"
    forbidden = [user_home / ".rig/packs", org_home / "packs", outside]
    for root in forbidden:
        with pytest.raises(PackError, match="inside the project"):
            install_pack(
                source, scope="project", project=project, root=root,
                allow_unverified=True,
            )
        assert not root.exists()

    org_target = org_home / "packs"
    org_home.mkdir()
    (project / "linked-packs").symlink_to(org_target, target_is_directory=True)
    with pytest.raises(PackError, match="symlink"):
        install_pack(
            source, scope="project", project=project, root=project / "linked-packs",
            allow_unverified=True,
        )
    assert not org_target.exists()

    monkeypatch.chdir(project)
    assert cmd_pack([
        "install", str(source), "--scope", "project", "--root", str(org_target),
        "--allow-unverified",
    ]) == 2
    assert not org_target.exists()


def test_lock_scope_mismatch_fails_closed(tmp_path):
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.lock import read_lock, validate_lock_root, write_lock
    from rig_workbench.packs.model import PackError

    project = tmp_path / "project"
    source = _write_pack(tmp_path / "source", "scope-lock", recipe=False)
    install_pack(source, scope="project", project=project, allow_unverified=True)
    root = project / ".rig/packs"
    lock = read_lock(root)
    lock["packs"][0]["scope"] = "org"
    write_lock(root, lock)
    with pytest.raises(PackError, match="scope mismatch"):
        validate_lock_root(root, expected_scope="project")


@pytest.mark.parametrize("link_component", [".rig", ".rig/packs", "broken/intermediate"])
def test_default_and_intermediate_project_root_symlinks_fail_before_write(
    tmp_path, monkeypatch, link_component,
):
    from rig_workbench.packs.cli import cmd_pack
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.model import PackError

    project = tmp_path / "project"
    project.mkdir()
    source = _write_pack(tmp_path / "source", "default-link-pack", recipe=False)
    external = tmp_path / "external"
    link = project / link_component
    link.parent.mkdir(parents=True, exist_ok=True)
    target = external if link_component != "broken/intermediate" else tmp_path / "absent"
    link.symlink_to(target, target_is_directory=True)
    root = None if link_component in {".rig", ".rig/packs"} else project / "broken/intermediate/packs"
    with pytest.raises(PackError, match="symlink"):
        install_pack(
            source, scope="project", project=project, root=root,
            allow_unverified=True,
        )
    assert not external.exists() and not target.exists()

    if root is None:
        monkeypatch.chdir(project)
        assert cmd_pack([
            "install", str(source), "--scope", "project", "--allow-unverified",
        ]) == 2
        assert not external.exists()


@pytest.mark.parametrize("scope", ["user", "org"])
def test_default_nonproject_tier_symlink_fails_before_write(tmp_path, monkeypatch, scope):
    from rig_workbench.packs.cli import cmd_pack
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.model import PackError

    project = tmp_path / "project"
    project.mkdir()
    source = _write_pack(tmp_path / "source", f"{scope}-link-pack", recipe=False)
    external = tmp_path / f"{scope}-external"
    if scope == "user":
        home = tmp_path / "user-home"
        home.mkdir()
        (home / ".rig").symlink_to(external, target_is_directory=True)
        monkeypatch.setenv("RIG_USER_HOME", str(home))
    else:
        home = tmp_path / "org-home"
        home.mkdir()
        (home / "packs").symlink_to(external, target_is_directory=True)
        monkeypatch.setenv("RIG_ORG_HOME", str(home))
    with pytest.raises(PackError, match="symlink"):
        install_pack(source, scope=scope, project=project)
    assert not external.exists()
    monkeypatch.chdir(project)
    assert cmd_pack(["install", str(source), "--scope", scope]) == 2
    assert not external.exists()


def test_default_absent_project_pack_root_is_created_normally(tmp_path):
    from rig_workbench.packs.installer import install_pack

    project = tmp_path / "project"
    project.mkdir()
    source = _write_pack(tmp_path / "source", "normal-default", recipe=False)
    result = install_pack(
        source, scope="project", project=project, allow_unverified=True,
    )
    assert result.path == project / ".rig/packs/normal-default"


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
                          root=tmp_path / f"installed-{kind}", allow_unverified=True)
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
    with pytest.raises(PackError, match="unsigned packs require"):
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
                            root=tmp_path / "quality-installed", allow_unverified=True)
    assert verified.verification_status == "verified-local"


def test_lock_write_failure_rolls_back_install(tmp_path, monkeypatch):
    from rig_workbench.packs import installer
    from rig_workbench.packs.model import PackError

    source = _write_pack(tmp_path / "source", "rollback-pack", recipe=False)
    root = tmp_path / "installed"
    monkeypatch.setattr(installer, "write_lock", lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(PackError("lock failure"))
    ))
    with pytest.raises(PackError, match="lock failure"):
        installer.install_pack(
            source, scope="project", project=tmp_path, root=root, allow_unverified=True,
        )
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
    install_pack(base, scope="project", project=project, allow_unverified=True)
    target, removed = remove_pack("base-pack", scope="project", project=project)
    assert not removed and target.exists()
    dependent = _write_pack(tmp_path / "dependent", "dependent-pack", recipe=False,
                            dependency=[{"id": "base-pack", "range": "*"}])
    install_pack(dependent, scope="project", project=project, allow_unverified=True)
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
    installed = install_pack(source, scope="project", project=project, allow_unverified=True)
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
    installed = install_pack(source, scope="project", project=project, allow_unverified=True)
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
    install_pack(source, scope="project", project=project, allow_unverified=True)
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
    _raw, result = read_json_yaml(pack / "evals/results/hello-case/current-codex.json")
    mutation(result)
    result.pop("attestation")
    result.pop("result_sha256")
    result["result_sha256"] = hashlib.sha256(
        canonical_json(result).encode("utf-8")
    ).hexdigest()
    result["attestation"] = sign_result_attestation(result)
    assert any(item.startswith(expected) for item in quality_result_failures(result, case))


def test_pack_test_structural_mock_and_provider_unavailable(tmp_path, monkeypatch):
    from rig_workbench.packs.lock import tree_hash
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.tester import test_pack

    pack = _write_pack(tmp_path / "source", "test-pack", recipe=True)
    case_path = pack / "evals/cases/hello-case/case.json"
    _raw, case = read_json_yaml(case_path)
    case["provider_policy"] = {"mode": "any", "allowed": []}
    case.update(
        prompt_entrypoint="hello",
        prompt_composition=["recipe:hello"],
        target_expectations=["contains:target"],
        clean_expectations=["contains:clean"],
    )
    case_path.write_text(canonical(case), encoding="utf-8")
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    manifest["hashes"]["evals/cases/hello-case/case.json"] = digest(case_path)
    manifest.update(
        display_name="Test pack", description="Evaluation composition fixture",
        capabilities=["evaluation", "recipe"],
        entrypoints=[{"id": "hello", "kind": "recipe", "target": "hello"}],
        references=[], resources={},
    )
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    monkeypatch.setenv("RIG_EVAL_ATTESTATION_KEY", "pack-test-fixture-key-is-at-least-32-bytes")
    before = tree_hash(pack)
    structural, code = test_pack(pack, project=tmp_path)
    assert code == 0 and structural["status"] == "structural_only"
    marker = tmp_path / "COMMAND_MUTATION"
    rejected_results = tmp_path.parent / f"{tmp_path.name}-rejected-results"
    with pytest.raises(PackError, match="forbids command"):
        test_pack(
            pack, project=tmp_path, provider="command", model="fixture",
            command=f"python -c 'open({str(marker)!r}, \"w\").write(\"bad\")'",
            result_dir=rejected_results,
        )
    with pytest.raises(PackError, match="forbids command"):
        test_pack(
            pack, project=tmp_path, provider="mock", model="fixture",
            judge_provider="command", judge_model="fixture",
            judge_command=f"python -c 'open({str(marker)!r}, \"w\").write(\"bad\")'",
            result_dir=rejected_results,
        )
    assert not marker.exists() and not rejected_results.exists()
    result_dir = tmp_path.parent / f"{tmp_path.name}-durable-results"
    mock, code = test_pack(pack, project=tmp_path, provider="mock", model="fixture",
                           judge_provider="mock", judge_model="fixture",
                           result_dir=result_dir)
    assert code == 0 and mock["status"] == "non_quality_mock"
    assert mock["result_paths"] and all(pathlib.Path(item).is_file()
                                        for item in mock["result_paths"])
    persisted = json.loads(pathlib.Path(mock["result_paths"][0]).read_text(encoding="utf-8"))
    assert persisted["target"][0]["checks"][0]["spec"] == "contains:target"
    assert persisted["clean"][0]["checks"][0]["spec"] == "contains:clean"
    assert tree_hash(pack) == before
    from rig_workbench.packs.evidence import import_results
    with pytest.raises(PackError, match="dev-only"):
        import_results(pack, staged=result_dir, project=tmp_path)
    assert tree_hash(pack) == before
    monkeypatch.setattr("rig_workbench.eval.runner.shutil.which", lambda _name: None)
    with pytest.raises(PackError, match="allow-paid-provider"):
        test_pack(pack, project=tmp_path, provider="codex", model="fixture",
                  judge_provider="codex", judge_model="fixture",
                  result_dir=result_dir / "blocked")
    unavailable, code = test_pack(pack, project=tmp_path, provider="codex", model="fixture",
                                  judge_provider="codex", judge_model="fixture",
                                  result_dir=result_dir / "unavailable",
                                  allow_paid_provider=True)
    assert code == 2 and unavailable["status"] == "provider_unavailable"


def test_import_results_validates_every_staged_file_and_is_atomic(
    tmp_path, monkeypatch, capsys,
):
    from rig_workbench.packs import evidence
    from rig_workbench.packs.evidence import import_results
    from rig_workbench.packs.lock import tree_hash
    from rig_workbench.packs.manifest import canonical, read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.validation import validate_pack

    repository = tmp_path / "repository"
    pack = _quality_pack(repository, monkeypatch)
    bundled = pack / "evals/results/hello-case/current-codex.json"
    staged = tmp_path / "staged-results"
    staged.mkdir()
    shutil.copyfile(bundled, staged / "result.json")
    shutil.rmtree(repository / "generated-results")
    bundled.unlink()
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    relative = "evals/results/hello-case/current-codex.json"
    manifest["assets"]["eval-result"] = []
    manifest["hashes"].pop(relative)
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    before = tree_hash(pack)

    (staged / "malformed.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(PackError, match="invalid staged"):
        import_results(pack, staged=staged, project=repository)
    assert tree_hash(pack) == before
    (staged / "malformed.json").unlink()

    real_replace = evidence.os.replace
    calls = 0

    def fail_commit_once(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected evidence commit failure")
        return real_replace(source, destination)

    monkeypatch.setattr(evidence.os, "replace", fail_commit_once)
    with pytest.raises(PackError, match="transaction"):
        import_results(pack, staged=staged, project=repository)
    assert tree_hash(pack) == before
    monkeypatch.setattr(evidence.os, "replace", real_replace)

    original_recipe = (pack / "recipes/hello.md").read_text(encoding="utf-8")
    real_tree_hash = evidence.tree_hash

    def race_at_commit(path):
        candidate = pathlib.Path(path)
        if candidate.name.startswith(f".{pack.name}.evidence-backup-"):
            recipe = candidate / "recipes/hello.md"
            recipe.write_text(original_recipe + "\nracing writer\n", encoding="utf-8")
        return real_tree_hash(candidate)

    monkeypatch.setattr(evidence, "tree_hash", race_at_commit)
    with pytest.raises(PackError, match="changed at.*commit"):
        import_results(pack, staged=staged, project=repository)
    assert "racing writer" in (pack / "recipes/hello.md").read_text(encoding="utf-8")
    (pack / "recipes/hello.md").write_text(original_recipe, encoding="utf-8")
    assert tree_hash(pack) == before
    monkeypatch.setattr(evidence, "tree_hash", real_tree_hash)

    from rig_workbench.packs.cli import cmd_pack
    monkeypatch.chdir(repository)
    assert cmd_pack([
        "import-results", str(pack), "--result-dir", str(staged),
    ]) == 0
    assert capsys.readouterr().out == f"imported: {relative}\n"
    validated = validate_pack(pack)
    assert validated["assets"]["eval-result"] == [relative]
    assert (pack / relative).is_file()


def test_import_results_rejects_stale_prompt_binding_and_unsafe_stage_paths(
    tmp_path, monkeypatch,
):
    from rig_workbench.packs.evidence import import_results
    from rig_workbench.packs.lock import tree_hash
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.model import PackError

    repository = tmp_path / "repository"
    pack = _quality_pack(repository, monkeypatch)
    bundled = pack / "evals/results/hello-case/current-codex.json"
    staged = tmp_path / "staged-results"
    staged.mkdir()
    shutil.copyfile(bundled, staged / "result.json")
    result = json.loads((staged / "result.json").read_text(encoding="utf-8"))
    shutil.rmtree(repository / "generated-results")
    bundled.unlink()
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    relative = "evals/results/hello-case/current-codex.json"
    manifest["assets"]["eval-result"] = []
    manifest["hashes"].pop(relative)
    recipe = pack / "recipes/hello.md"
    recipe.write_text(recipe.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    manifest["hashes"]["recipes/hello.md"] = digest(recipe)
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    before = tree_hash(pack)

    monkeypatch.setattr(
        "rig_workbench.packs.evidence._git_identity",
        lambda _root: (result["execution_commit"], result["execution_base_commit"], "available"),
    )
    monkeypatch.setattr(
        "rig_workbench.packs.evidence.execution_diff_sha256",
        lambda *_args, **_kwargs: result["execution_diff_sha256"],
    )
    with pytest.raises(PackError, match="prompt/asset binding is stale"):
        import_results(pack, staged=staged, project=repository)
    assert tree_hash(pack) == before

    inside = repository / "staged"
    shutil.copytree(staged, inside)
    with pytest.raises(PackError, match="outside the project"):
        import_results(pack, staged=inside, project=repository)
    linked = tmp_path / "linked-stage"
    linked.symlink_to(staged, target_is_directory=True)
    with pytest.raises(PackError, match="symlink"):
        import_results(pack, staged=linked, project=repository)


def test_pack_cli_requires_paid_opt_in_before_codex_execution(tmp_path, monkeypatch, capsys):
    from rig_workbench.packs.cli import cmd_pack

    pack = _write_pack(tmp_path / "source", "paid-opt-in", recipe=True)
    monkeypatch.chdir(tmp_path)
    code = cmd_pack([
        "test", str(pack), "--provider", "codex", "--model", "fixture",
        "--result-dir", str(tmp_path.parent / f"{tmp_path.name}-results"), "--json",
    ])
    assert code == 2
    assert "--allow-paid-provider" in capsys.readouterr().err
