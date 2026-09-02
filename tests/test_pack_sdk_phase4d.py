import copy
import hashlib
import json
import pathlib
import shutil
import zipfile

import pytest

from test_eval_cases import valid_case


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _resource_pack(root: pathlib.Path, pack_id: str = "resource-pack", *, kind="project"):
    from rig_workbench import __version__
    from rig_workbench.packs.manifest import canonical, digest
    from rig_workbench.packs.model import ASSET_DIRS

    pack = root / pack_id
    for directory in ASSET_DIRS.values():
        (pack / directory).mkdir(parents=True, exist_ok=True)
    resource = pack / "resources" / "guide.html"
    resource.write_text("<!doctype html><html><body>guide</body></html>\n", encoding="utf-8")
    relative = "resources/guide.html"
    assets = {asset_kind: [] for asset_kind in ASSET_DIRS}
    assets["resource"] = [relative]
    checksum = digest(resource)
    manifest = {
        "pack_schema_version": 2, "id": pack_id, "type": "skill", "version": "1.0.0",
        "kind": kind,
        "engine": f">={__version__}", "dependencies": [], "assets": assets,
        "hashes": {relative: checksum}, "display_name": "Resource Pack",
        "description": "Inert documentation resources.", "capabilities": ["resource"],
        "entrypoints": [], "references": [],
        "resources": {relative: {
            "media_type": "text/html", "size": resource.stat().st_size, "sha256": checksum,
        }},
        "provenance": {"source": "test", "created_at": "2026-08-05T06:00:00+09:00"},
    }
    compatibility = {
        "compatibility_schema_version": 1, "pack_id": pack_id, "pack_version": "1.0.0",
        "engine": f">={__version__}", "platforms": ["any"],
    }
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    (pack / "compatibility.yaml").write_text(canonical(compatibility), encoding="utf-8")
    return pack


def _typed_dependency_pack(root: pathlib.Path, pack_id: str, *, dependency=None,
                           external_owner=None):
    from rig_workbench import __version__
    from rig_workbench.eval.cases import canonical_json
    from rig_workbench.packs.manifest import canonical, digest
    from rig_workbench.packs.model import ASSET_DIRS

    pack = root / pack_id
    for directory in ASSET_DIRS.values():
        (pack / directory).mkdir(parents=True, exist_ok=True)
    flow_name = f"{pack_id}-flow"
    recipe = pack / f"recipes/{flow_name}.md"
    instruction = "shared"
    recipe.write_text(
        f"---\nname: {flow_name}\nsteps:\n  - id: work\n    instruction: " + instruction
        + f"\n    pattern: serial\n---\n# {flow_name}\n", encoding="utf-8",
    )
    assets = {kind: [] for kind in ASSET_DIRS}
    assets["recipe"] = [f"recipes/{flow_name}.md"]
    if not external_owner:
        instruction_file = pack / "facets/instructions/shared.md"
        instruction_file.write_text("# shared\n", encoding="utf-8")
        assets["instruction"] = ["facets/instructions/shared.md"]
    case = copy.deepcopy(valid_case())
    case.update(id=flow_name, prompt_surfaces=[f"recipe:{flow_name}"])
    case["provenance"]["source_task_id"] = f"{pack_id}-flow"
    case["provenance"]["source_hashes"] = {
        "task.json": hashlib.sha256(canonical_json(case["target_inputs"]).encode()).hexdigest()
    }
    case_path = pack / "evals/cases" / case["id"] / "case.json"
    case_path.parent.mkdir(parents=True)
    case_path.write_text(canonical(case), encoding="utf-8")
    assets["eval-case"] = [f"evals/cases/{case['id']}/case.json"]
    refs = [
        {"id": instruction, "kind": "instruction", "pack": external_owner or pack_id},
        {"id": "serial", "kind": "pattern", "pack": "rig-core"},
    ]
    refs.sort(key=lambda item: (item["pack"], item["kind"], item["id"]))
    hashes = {item: digest(pack / item) for paths in assets.values() for item in paths}
    manifest = {
        "pack_schema_version": 2, "id": pack_id, "type": "skill", "version": "1.0.0",
        "kind": "project",
        "engine": f">={__version__}", "dependencies": dependency or [], "assets": assets,
        "hashes": hashes, "display_name": pack_id, "description": "Dependency fixture",
        "capabilities": ["evaluation", "recipe"], "references": refs, "resources": {},
        "entrypoints": [{"id": flow_name, "kind": "recipe", "target": flow_name}],
        "provenance": {"source": "test", "created_at": "2026-08-05T06:00:00+09:00"},
    }
    compatibility = {
        "compatibility_schema_version": 1, "pack_id": pack_id, "pack_version": "1.0.0",
        "engine": f">={__version__}", "platforms": ["any"],
    }
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    (pack / "compatibility.yaml").write_text(canonical(compatibility), encoding="utf-8")
    return pack


def test_builtin_catalog_is_manifest_discovered_and_tamper_fails_closed(tmp_path):
    from rig_workbench.packs.catalog import catalog_records, discover_builtin_packs
    from rig_workbench.packs.model import PackError

    records = catalog_records()
    assert {item["alias"] for item in records} == {
        "domain:decision-humor", "domain:document-review", "domain:japanese-writing",
        "domain:layout-gate", "domain:pack-author",
        "domain:sales", "domain:video-storytelling",
    }
    assert all(set(item) == {
        "id", "kind", "version", "display_name", "description", "capabilities",
        "entrypoints", "manifest_sha256", "alias",
    } for item in records)

    copied = tmp_path / "dist/packs/domain/not-the-id"
    shutil.copytree(REPO_ROOT / "packs/domain/video-storytelling", copied)
    assert ("domain", "video-storytelling") in discover_builtin_packs(tmp_path / "dist")
    (copied / "recipes/movie.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(PackError, match="hash mismatch"):
        discover_builtin_packs(tmp_path / "dist")


def test_builtin_catalog_rejects_duplicate_id_and_kind_mismatch(tmp_path):
    from rig_workbench.packs.catalog import discover_builtin_packs
    from rig_workbench.packs.manifest import canonical, read_json_yaml
    from rig_workbench.packs.model import PackError

    domain = tmp_path / "dist/packs/domain"
    shutil.copytree(REPO_ROOT / "packs/domain/video-storytelling", domain / "one")
    shutil.copytree(REPO_ROOT / "packs/domain/video-storytelling", domain / "two")
    with pytest.raises(PackError, match="duplicate builtin pack id"):
        discover_builtin_packs(tmp_path / "dist")
    shutil.rmtree(domain / "two")
    _raw, manifest = read_json_yaml(domain / "one/pack.yaml")
    manifest["kind"] = "official"
    (domain / "one/pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    with pytest.raises(PackError, match="kind mismatch"):
        discover_builtin_packs(tmp_path / "dist")


def test_catalog_schema_is_additive_for_legacy_non_resource_manifests(tmp_path):
    from rig_workbench.packs.manifest import PACK_CATALOG_FIELDS, canonical, read_json_yaml
    from rig_workbench.packs.validation import validate_pack

    copied = tmp_path / "legacy-sales"
    shutil.copytree(REPO_ROOT / "packs/domain/sales", copied)
    _raw, manifest = read_json_yaml(copied / "pack.yaml")
    for field in PACK_CATALOG_FIELDS:
        manifest.pop(field)
    manifest["assets"].pop("resource")
    (copied / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    validated = validate_pack(copied)
    assert validated["id"] == "sales"
    assert "resource" not in validated["assets"]


def test_rig_core_pack_id_is_reserved_in_manifest_and_init(tmp_path):
    from rig_workbench.packs.cli import init_pack
    from rig_workbench.packs.manifest import canonical, read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.validation import validate_pack

    pack = _resource_pack(tmp_path / "source")
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    manifest["id"] = "rig-core"
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    with pytest.raises(PackError, match="pack id is reserved: rig-core"):
        validate_pack(pack)
    with pytest.raises(PackError, match="pack id is reserved: rig-core"):
        init_pack("rig-core", kind="project", type_="skill", root=tmp_path / "initialized")
    assert not (tmp_path / "initialized/rig-core").exists()


def test_dependency_collection_is_topological_and_refs_stay_in_closure(tmp_path):
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.validation import validate_tiered_collection

    dep = _typed_dependency_pack(tmp_path, "dep")
    child = _typed_dependency_pack(
        tmp_path, "child", dependency=[{"id": "dep", "range": ">=1.0.0"}],
        external_owner="dep",
    )
    records = validate_tiered_collection([("project", child), ("project", dep)])
    assert [manifest["id"] for _tier, _path, manifest in records] == ["dep", "child"]
    with pytest.raises(PackError, match="missing dependency"):
        validate_tiered_collection([("project", child)])

    sibling = _typed_dependency_pack(tmp_path, "sibling")
    from rig_workbench.packs.manifest import canonical, read_json_yaml
    _raw, manifest = read_json_yaml(child / "pack.yaml")
    for reference in manifest["references"]:
        if reference["kind"] == "instruction":
            reference["pack"] = "sibling"
    manifest["references"].sort(key=lambda item: (item["pack"], item["kind"], item["id"]))
    (child / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    with pytest.raises(PackError, match="escapes dependency closure"):
        validate_tiered_collection([
            ("project", child), ("project", dep), ("user", sibling),
        ])


def test_typed_recipe_owner_cannot_be_shadowed_at_runtime(tmp_path, monkeypatch):
    from rig_workbench.orchestrate import commands, config, recipes
    from rig_workbench.packs.cli import invoke_pack
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.resolver import (
        resolve_asset, resolve_bound_asset, resolve_owned_asset,
    )

    project = tmp_path / "project"
    user_home = tmp_path / "user"
    dep = _typed_dependency_pack(user_home / ".rig/packs", "dep")
    child = _typed_dependency_pack(
        project / ".rig/packs", "child",
        dependency=[{"id": "dep", "range": ">=1.0.0"}], external_owner="dep",
    )
    sibling = _typed_dependency_pack(project / ".rig/packs", "sibling")

    child_recipe = child / "recipes/child-flow.md"
    child_recipe.write_text(
        "---\nname: child-flow\nextends: dep-flow\nsteps:\n"
        "  - id: child\n    instruction: shared\n    pattern: serial\n---\n# child\n",
        encoding="utf-8",
    )
    _raw, child_manifest = read_json_yaml(child / "pack.yaml")
    child_manifest["hashes"]["recipes/child-flow.md"] = digest(child_recipe)
    child_manifest["references"].append(
        {"id": "dep-flow", "kind": "recipe", "pack": "dep"}
    )
    child_manifest["references"].sort(
        key=lambda item: (item["pack"], item["kind"], item["id"])
    )
    (child / "pack.yaml").write_text(canonical(child_manifest), encoding="utf-8")

    shadow = sibling / "recipes/dep-flow.md"
    shadow.write_text(
        "---\nname: dep-flow\nsteps:\n"
        "  - id: shadowed\n    instruction: shared\n    pattern: serial\n---\n# shadow\n",
        encoding="utf-8",
    )
    _raw, sibling_manifest = read_json_yaml(sibling / "pack.yaml")
    sibling_manifest["assets"]["recipe"].append("recipes/dep-flow.md")
    sibling_manifest["assets"]["recipe"].sort()
    sibling_manifest["hashes"]["recipes/dep-flow.md"] = digest(shadow)
    (sibling / "pack.yaml").write_text(canonical(sibling_manifest), encoding="utf-8")

    monkeypatch.setenv("RIG_USER_HOME", str(user_home))
    monkeypatch.setenv("RIG_ALLOW_PROJECT_PACKS", "1")
    monkeypatch.setenv("RIG_TRUST_STORE", str(tmp_path / "trust.json"))
    monkeypatch.setattr(config, "INVOCATION_CWD", project)

    assert resolve_asset("recipe", "dep-flow", project=project).pack_id == "sibling"
    assert resolve_owned_asset("recipe", "dep-flow", "dep", project=project).path == (
        dep / "recipes/dep-flow.md"
    )
    assert resolve_bound_asset(
        "recipe", "dep-flow", child_recipe, project=project
    ).pack_id == "dep"
    plan = recipes.resolve_plan_json(child_recipe)
    assert [step["id"] for step in plan["steps"]] == ["work", "child"]

    invoked = []
    monkeypatch.setattr(
        commands, "cmd_run",
        lambda args: invoked.append(recipes.resolve_plan_json(commands.resolve_recipe(args[0]))),
    )
    assert invoke_pack("child:child-flow", [], project=project) == 0
    assert [step["id"] for step in invoked[0]["steps"]] == ["work", "child"]


def test_rig_core_typed_owner_never_selects_pack_asset(tmp_path, monkeypatch):
    from rig_workbench.packs import resolver
    from rig_workbench.packs.model import ResolvedAsset

    attacker = ResolvedAsset(
        "recipe", "adaptive-bugfix", tmp_path / "attacker.md", "project",
        str(tmp_path / "attacker"), "rig-core",
    )
    trusted = ResolvedAsset(
        "recipe", "adaptive-bugfix", tmp_path / "trusted.md", "core",
        "core:test", "rig-core",
    )
    monkeypatch.setattr(resolver, "resolve_all", lambda *_args, **_kwargs: [attacker])
    monkeypatch.setattr(resolver, "_core_assets", lambda: iter([trusted]))
    assert resolver.resolve_asset("recipe", "adaptive-bugfix") == attacker
    assert resolver.resolve_owned_asset(
        "recipe", "adaptive-bugfix", "rig-core"
    ) == trusted


def test_spoofed_rig_core_blocks_bound_plan_and_invoke(tmp_path, monkeypatch):
    from rig_workbench.orchestrate import config, recipes
    from rig_workbench.packs.cli import invoke_pack
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.resolver import resolve_bound_asset
    from rig_workbench.packs.validation import validate_tiered_collection

    project = tmp_path / "project"
    root = project / ".rig/packs"
    child = _typed_dependency_pack(root, "core-child")
    spoof = _typed_dependency_pack(root, "spoof")

    child_recipe = child / "recipes/core-child-flow.md"
    child_recipe.write_text(
        "---\nname: core-child-flow\nextends: adaptive-bugfix\nsteps:\n"
        "  - id: child\n    instruction: shared\n    pattern: serial\n---\n# child\n",
        encoding="utf-8",
    )
    _raw, child_manifest = read_json_yaml(child / "pack.yaml")
    child_manifest["hashes"]["recipes/core-child-flow.md"] = digest(child_recipe)
    child_manifest["references"].append(
        {"id": "adaptive-bugfix", "kind": "recipe", "pack": "rig-core"}
    )
    child_manifest["references"].sort(
        key=lambda item: (item["pack"], item["kind"], item["id"])
    )
    (child / "pack.yaml").write_text(canonical(child_manifest), encoding="utf-8")

    fake_core_recipe = spoof / "recipes/adaptive-bugfix.md"
    fake_core_recipe.write_text(
        "---\nname: adaptive-bugfix\nsteps:\n"
        "  - id: shadow-core\n    instruction: shared\n    pattern: serial\n---\n",
        encoding="utf-8",
    )
    _raw, spoof_manifest = read_json_yaml(spoof / "pack.yaml")
    spoof_manifest["id"] = "rig-core"
    spoof_manifest["assets"]["recipe"].append("recipes/adaptive-bugfix.md")
    spoof_manifest["assets"]["recipe"].sort()
    spoof_manifest["hashes"]["recipes/adaptive-bugfix.md"] = digest(fake_core_recipe)
    for reference in spoof_manifest["references"]:
        if reference["pack"] == "spoof":
            reference["pack"] = "rig-core"
    spoof_manifest["references"].sort(
        key=lambda item: (item["pack"], item["kind"], item["id"])
    )
    (spoof / "pack.yaml").write_text(canonical(spoof_manifest), encoding="utf-8")

    monkeypatch.setattr(config, "INVOCATION_CWD", project)
    entries = [("project", child), ("project", spoof)]
    with pytest.raises(PackError, match="pack id is reserved: rig-core"):
        validate_tiered_collection(entries)
    with pytest.raises(PackError, match="pack id is reserved: rig-core"):
        resolve_bound_asset("recipe", "adaptive-bugfix", child_recipe, project=project)
    with pytest.raises(PackError, match="pack id is reserved: rig-core"):
        recipes.resolve_plan_json(child_recipe)
    with pytest.raises(PackError, match="pack id is reserved: rig-core"):
        invoke_pack("core-child:core-child-flow", [], project=project)


def test_entrypoint_requires_owned_target_and_eval_coverage(tmp_path):
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.validation import validate_pack

    copied = tmp_path / "sales"
    shutil.copytree(REPO_ROOT / "packs/domain/sales", copied)
    case_path = copied / "evals/cases/deal-review-structure/case.json"
    _raw, case = read_json_yaml(case_path)
    case["prompt_surfaces"] = ["command:sales"]
    case_path.write_text(canonical(case), encoding="utf-8")
    _raw, manifest = read_json_yaml(copied / "pack.yaml")
    manifest["hashes"]["evals/cases/deal-review-structure/case.json"] = digest(case_path)
    (copied / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    with pytest.raises(PackError, match="entrypoint lacks evaluation coverage"):
        validate_pack(copied)


def test_pack_invoke_routes_recipe_command_and_refuses_manual_only(monkeypatch, tmp_path, capsys):
    from rig_workbench.orchestrate import commands
    from rig_workbench.packs.cli import invoke_pack
    from rig_workbench.packs.model import PackError

    seen = []
    monkeypatch.setattr(commands, "cmd_run", lambda args: seen.append(args))
    assert invoke_pack(
        "sales:deal-review", ["--", "--provider", "mock"], project=tmp_path
    ) == 0
    assert seen == [["deal-review", "--provider", "mock"]]
    assert invoke_pack("sales:sales", ["--", "--account", "example"], project=tmp_path) == 0
    descriptor = json.loads(capsys.readouterr().out)
    assert descriptor["mode"] == "manual-command"
    assert descriptor["entrypoint"] == "sales:sales"
    assert descriptor["args"] == ["--account", "example"]
    with pytest.raises(PackError, match="manual-only"):
        invoke_pack("decision-humor:magi", [], project=tmp_path)


def test_resource_metadata_runtime_lookup_and_mime_spoof_fail_closed(tmp_path, monkeypatch):
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.manifest import canonical, read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.resolver import resolve_resource
    from rig_workbench.packs.validation import validate_pack

    pack = _resource_pack(tmp_path / "source")
    assert validate_pack(pack)["assets"]["resource"] == ["resources/guide.html"]
    project = tmp_path / "project"
    install_pack(pack, scope="project", project=project, allow_unverified=True)
    resolved = resolve_resource("resource-pack", "guide", project=project)
    assert resolved and resolved["media_type"] == "text/html"
    assert resolved["executable"] is False and resolved["path"].is_file()

    install_pack(
        "domain:video-storytelling", scope="project", project=project,
        allow_unverified=True,
    )
    historical = resolve_resource("video-storytelling", "launch-film", project=project)
    assert historical and historical["media_type"] == "text/html"
    assert historical["size"] == 16561 and historical["executable"] is False

    spoof = _resource_pack(tmp_path / "spoof", "spoof")
    _raw, manifest = read_json_yaml(spoof / "pack.yaml")
    manifest["resources"]["resources/guide.html"]["media_type"] = "image/png"
    (spoof / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    with pytest.raises(PackError, match="MIME/extension|signature"):
        validate_pack(spoof)


def test_resource_archive_traversal_and_executable_extension_are_rejected(tmp_path):
    from rig_workbench.packs.installer import install_pack
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.validation import validate_pack

    archive = tmp_path / "traversal.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape", "bad")
    with pytest.raises(PackError, match="traversal"):
        install_pack(archive, scope="project", project=tmp_path / "project")

    pack = _resource_pack(tmp_path / "executable", "executable")
    source = pack / "resources/guide.html"
    target = pack / "resources/guide.sh"
    source.rename(target)
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    new = "resources/guide.sh"
    checksum = digest(target)
    manifest["assets"]["resource"] = [new]
    manifest["hashes"] = {new: checksum}
    manifest["resources"] = {new: {
        "media_type": "text/plain", "size": target.stat().st_size, "sha256": checksum,
    }}
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    with pytest.raises(PackError, match="executable resource extension"):
        validate_pack(pack)
