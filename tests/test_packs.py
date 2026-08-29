import copy
import pathlib

import pytest

from test_eval_cases import valid_case


def _write_pack(root: pathlib.Path, pack_id: str = "demo-pack", *, recipe: bool = True,
                dependency: list[dict] | None = None) -> pathlib.Path:
    from rig_workbench import __version__
    from rig_workbench.packs.manifest import canonical, digest
    from rig_workbench.packs.model import ASSET_DIRS

    pack = root / pack_id
    for directory in ASSET_DIRS.values():
        (pack / directory).mkdir(parents=True, exist_ok=True)
    assets = {kind: [] for kind in ASSET_DIRS}
    if recipe:
        recipe_path = pack / "recipes" / "hello.md"
        recipe_path.write_text("---\nname: hello\nsteps: []\n---\n", encoding="utf-8")
        assets["recipe"] = ["recipes/hello.md"]
        case = copy.deepcopy(valid_case())
        case["id"] = "hello-case"
        case["prompt_surfaces"] = ["recipe:hello"]
        case_path = pack / "evals" / "cases" / "hello-case" / "case.json"
        case_path.parent.mkdir(parents=True, exist_ok=True)
        case_path.write_text(canonical(case), encoding="utf-8")
        assets["eval-case"] = ["evals/cases/hello-case/case.json"]
    hashes = {item: digest(pack / item) for paths in assets.values() for item in paths}
    manifest = {
        "pack_schema_version": 2, "id": pack_id, "type": "skill", "version": "1.0.0",
        "kind": "domain",
        "engine": f">={__version__}", "dependencies": dependency or [],
        "assets": assets, "hashes": hashes,
        "provenance": {"source": "test", "created_at": "2026-08-05T00:00:00+00:00"},
    }
    compatibility = {
        "compatibility_schema_version": 1, "pack_id": pack_id, "pack_version": "1.0.0",
        "engine": f">={__version__}", "platforms": ["any"],
    }
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    (pack / "compatibility.yaml").write_text(canonical(compatibility), encoding="utf-8")
    return pack


def test_pack_init_is_canonical_non_overwriting_and_valid(tmp_path):
    from rig_workbench.packs.cli import init_pack
    from rig_workbench.packs.manifest import read_json_yaml, canonical
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.validation import validate_pack

    pack = init_pack("my-pack", kind="project", type_="skill", root=tmp_path)
    raw, value = read_json_yaml(pack / "pack.yaml")
    assert raw == canonical(value)
    assert validate_pack(pack)["id"] == "my-pack"
    with pytest.raises(PackError, match="already exists"):
        init_pack("my-pack", kind="project", type_="skill", root=tmp_path)


def test_pack_cli_init_validate_and_doctor_json(tmp_path, monkeypatch, capsys):
    from rig_workbench.packs.cli import cmd_pack

    root = tmp_path / "packs"
    assert cmd_pack(["init", "cli-pack", "--type", "skill", "--root", str(root)]) == 0
    assert cmd_pack(["validate", str(root / "cli-pack")]) == 0
    monkeypatch.chdir(tmp_path)
    # Exit 0 on a warning: a scaffolded pack carries nothing, which `doctor` now says out
    # loud rather than reporting `ok`, but it is the expected state after `init` and not a
    # failure. `empty_pack` is the finding; the exit code stays clear for `failed`.
    assert cmd_pack(["doctor", str(root / "cli-pack"), "--json"]) == 0
    output = capsys.readouterr().out
    assert '"pack_doctor_schema_version":1' in output
    assert '"empty_pack"' in output


def test_pack_validate_prompt_eval_hash_ref_compat_and_malicious(tmp_path):
    from rig_workbench.packs.manifest import canonical, read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.validation import validate_pack

    valid = _write_pack(tmp_path / "valid")
    assert validate_pack(valid)["id"] == "demo-pack"

    no_eval = _write_pack(tmp_path / "no-eval")
    raw, manifest = read_json_yaml(no_eval / "pack.yaml")
    manifest["assets"]["eval-case"] = []
    manifest["hashes"].pop("evals/cases/hello-case/case.json")
    (no_eval / "evals/cases/hello-case/case.json").unlink()
    (no_eval / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    with pytest.raises(PackError, match="requires at least one"):
        validate_pack(no_eval)

    malicious = _write_pack(tmp_path / "malicious")
    recipe = malicious / "recipes/hello.md"
    recipe.write_text(recipe.read_text() + "\u202e hidden\n", encoding="utf-8")
    _raw, manifest = read_json_yaml(malicious / "pack.yaml")
    import hashlib
    manifest["hashes"]["recipes/hello.md"] = hashlib.sha256(recipe.read_bytes()).hexdigest()
    (malicious / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    with pytest.raises(PackError, match="unsafe|injection"):
        validate_pack(malicious)


def test_pack_rejects_symlink_dependency_cycle_and_collision(tmp_path):
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.validation import validate_collection, validate_pack

    symlinked = _write_pack(tmp_path / "links")
    (symlinked / "commands/link.md").symlink_to(symlinked / "recipes/hello.md")
    with pytest.raises(PackError, match="symlink"):
        validate_pack(symlinked)

    a = _write_pack(tmp_path / "deps", "pack-a", recipe=False,
                    dependency=[{"id": "pack-b", "range": "*"}])
    b = _write_pack(tmp_path / "deps", "pack-b", recipe=False,
                    dependency=[{"id": "pack-a", "range": "*"}])
    with pytest.raises(PackError, match="cycle"):
        validate_collection([a, b])

    one = _write_pack(tmp_path / "collision-a", "one")
    two = _write_pack(tmp_path / "collision-b", "two")
    with pytest.raises(PackError, match="collision"):
        validate_collection([one, two])


@pytest.mark.parametrize("kind", [
    "recipe", "persona", "instruction", "pattern", "wiki", "policy", "output-contract",
    "command", "agent",
])
def test_unified_tier_resolver_for_every_prompt_kind(tmp_path, monkeypatch, kind):
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.model import ASSET_DIRS
    from rig_workbench.packs.resolver import resolve_asset

    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user"))
    project = tmp_path / "project"
    user_pack = _write_pack(tmp_path / "user/.rig/packs", "user-pack", recipe=True)
    project_pack = _write_pack(project / ".rig/packs", "project-pack", recipe=True)
    for pack in (user_pack, project_pack):
        directory = pack / ASSET_DIRS[kind]
        suffix = ".yaml" if kind == "agent" else ".md"
        asset = directory / f"shared{suffix}"
        content = "---\nname: shared\nsteps: []\n---\nprompt body\n" if kind == "recipe" else "prompt body\n"
        asset.write_text(content, encoding="utf-8")
        _raw, manifest = read_json_yaml(pack / "pack.yaml")
        rel = asset.relative_to(pack).as_posix()
        manifest["assets"][kind] = sorted([*manifest["assets"][kind], rel])
        manifest["hashes"][rel] = digest(asset)
        (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    resolved = resolve_asset(kind, "shared", project=project)
    assert resolved is not None and resolved.tier == "project"
    assert resolved.shadowed and "user-pack" in resolved.shadowed[0]


def test_doctor_is_deterministic_and_runtime_recipe_persona_use_resolver(tmp_path, monkeypatch):
    from rig_workbench.packs.doctor import diagnose
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.orchestrate import config, providers, recipes

    project = tmp_path / "project"
    pack = _write_pack(project / ".rig/packs", "runtime-pack", recipe=True)
    recipe = pack / "recipes/runtime.md"
    persona = pack / "facets/personas/runtime.md"
    recipe.write_text("---\nname: runtime\nsteps: []\n---\n", encoding="utf-8")
    persona.write_text("---\nname: runtime\n---\nRuntime lens\n", encoding="utf-8")
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    for kind, asset in (("recipe", recipe), ("persona", persona)):
        rel = asset.relative_to(pack).as_posix()
        manifest["assets"][kind] = sorted([*manifest["assets"][kind], rel])
        manifest["hashes"][rel] = digest(asset)
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    monkeypatch.setattr(config, "INVOCATION_CWD", project)
    monkeypatch.setenv("RIG_ALLOW_PROJECT_RECIPES", "1")
    monkeypatch.setenv("RIG_ALLOW_PROJECT_PERSONAS", "1")
    monkeypatch.setenv("RIG_TRUST_STORE", str(tmp_path / "trust.json"))
    assert recipes.resolve_recipe("runtime") == recipe
    assert providers._load_persona_brief("runtime") == "Runtime lens"
    assert diagnose(project=project) == diagnose(project=project)


def test_runtime_persona_trust_binds_kind_content_pack_and_tier(tmp_path, monkeypatch):
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.orchestrate import config, providers

    project = tmp_path / "project"
    pack = _write_pack(project / ".rig/packs", "persona-pack", recipe=True)
    persona = pack / "facets/personas/reviewer.md"
    persona.write_text("---\nname: reviewer\n---\nFirst lens\n", encoding="utf-8")
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    rel = persona.relative_to(pack).as_posix()
    manifest["assets"]["persona"] = [rel]
    manifest["hashes"][rel] = digest(persona)
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    monkeypatch.setattr(config, "INVOCATION_CWD", project)
    monkeypatch.setenv("RIG_PACK_TRUST_STORE", str(tmp_path / "pack-trust.json"))
    with pytest.raises(PackError, match="untrusted.*persona"):
        providers._load_persona_brief("reviewer")
    monkeypatch.setenv("RIG_ALLOW_PROJECT_PERSONAS", "1")
    assert providers._load_persona_brief("reviewer") == "First lens"
    monkeypatch.delenv("RIG_ALLOW_PROJECT_PERSONAS")
    assert providers._load_persona_brief("reviewer") == "First lens"
    persona.write_text("---\nname: reviewer\n---\nChanged lens\n", encoding="utf-8")
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    manifest["hashes"][rel] = digest(persona)
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    with pytest.raises(PackError, match="untrusted.*persona"):
        providers._load_persona_brief("reviewer")


def test_multiline_frontmatter_refs_all_kinds_and_broken_ref(tmp_path):
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.model import ASSET_DIRS, PackError
    from rig_workbench.packs.validation import validate_pack

    pack = _write_pack(tmp_path / "refs", "refs-pack", recipe=True)
    values = {
        "instruction": "check", "pattern": "serial", "output-contract": "report",
        "persona": "reviewer", "policy": "safe", "wiki": "guide",
    }
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    for kind, name in values.items():
        asset = pack / ASSET_DIRS[kind] / f"{name}.md"
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
        rel = asset.relative_to(pack).as_posix()
        manifest["assets"][kind] = [rel]
        manifest["hashes"][rel] = digest(asset)
    recipe = pack / "recipes/hello.md"
    recipe.write_text(
        "---\nname: hello\nsteps:\n  - id: verify\n    instruction: check\n"
        "    pattern: serial\n    output_contract: report\n    personas:\n"
        "      - reviewer\n    policies:\n      - safe\n---\n[[guide]]\n",
        encoding="utf-8",
    )
    manifest["hashes"]["recipes/hello.md"] = digest(recipe)
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    assert validate_pack(pack)["id"] == "refs-pack"
    recipe.write_text(recipe.read_text().replace("- safe", "- absent"), encoding="utf-8")
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    manifest["hashes"]["recipes/hello.md"] = digest(recipe)
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    with pytest.raises(PackError, match="broken pack reference: policy:absent"):
        validate_pack(pack)


@pytest.mark.parametrize("frontmatter", [
    "name: hello\nsteps:\n  - personas: [absent]",
    '{"name":"hello","steps":[{"personas":["absent"]}]}',
])
def test_nested_frontmatter_references_are_not_flattened_or_missed(tmp_path, frontmatter):
    from rig_workbench.packs.manifest import canonical, digest, read_json_yaml
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.validation import validate_pack

    pack = _write_pack(tmp_path / "nested")
    recipe = pack / "recipes/hello.md"
    recipe.write_text(f"---\n{frontmatter}\n---\n", encoding="utf-8")
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    manifest["hashes"]["recipes/hello.md"] = digest(recipe)
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    with pytest.raises(PackError, match="broken pack reference: persona:absent"):
        validate_pack(pack)


def test_frontmatter_subset_rejects_unsupported_yaml_and_parses_all_shipped_recipes(tmp_path):
    from rig_workbench.packs.manifest import parse_frontmatter_subset
    from rig_workbench.packs.model import PackError

    unsupported = tmp_path / "unsupported.md"
    unsupported.write_text("---\ndescription: |\n  folded text\n---\n", encoding="utf-8")
    with pytest.raises(PackError, match="unsupported frontmatter scalar"):
        parse_frontmatter_subset(unsupported)

    recipes = pathlib.Path(__file__).parents[1] / "skills" / "engine" / "recipes"
    shipped = sorted(recipes.glob("*.md"))
    assert shipped
    for recipe in shipped:
        parsed = parse_frontmatter_subset(recipe)
        assert isinstance(parsed, dict) and parsed.get("name"), recipe


@pytest.mark.parametrize("source", [
    "description: rm -rf",
    "documentation: git reset --hard",
])
def test_manifest_description_prefix_cannot_bypass_command_scanner(tmp_path, source):
    from rig_workbench.packs.manifest import read_json_yaml, validate_manifest_shape
    from rig_workbench.packs.model import PackError

    pack = _write_pack(tmp_path / "manifest")
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    manifest["provenance"]["source"] = source
    with pytest.raises(PackError, match="unsafe manifest instruction"):
        validate_manifest_shape(manifest)


def test_manifest_allows_non_command_documentation_text(tmp_path):
    from rig_workbench.packs.manifest import read_json_yaml, validate_manifest_shape

    pack = _write_pack(tmp_path / "safe-manifest")
    _raw, manifest = read_json_yaml(pack / "pack.yaml")
    manifest["provenance"]["source"] = "Documentation for reviewed recovery guidance"
    validate_manifest_shape(manifest)


def test_runtime_collection_fails_closed_and_doctor_reports_dependency_errors(tmp_path, monkeypatch):
    from rig_workbench.packs.doctor import diagnose
    from rig_workbench.packs.model import PackError
    from rig_workbench.packs.resolver import resolve_asset

    project = tmp_path / "collision"
    _write_pack(project / ".rig/packs", "one", recipe=True)
    _write_pack(project / ".rig/packs", "two", recipe=True)
    with pytest.raises(PackError, match="same-tier.*collision"):
        resolve_asset("recipe", "hello", project=project)

    missing = tmp_path / "missing"
    _write_pack(missing / ".rig/packs", "dependent", recipe=False,
                dependency=[{"id": "absent", "range": "*"}])
    with pytest.raises(PackError, match="missing dependency"):
        resolve_asset("recipe", "anything", project=missing)
    report = diagnose(project=missing)
    assert report["status"] == "failed"
    assert any(item["code"] == "missing_dependency" for item in report["findings"])

    cycle = tmp_path / "cycle"
    _write_pack(cycle / ".rig/packs", "a-pack", recipe=False,
                dependency=[{"id": "b-pack", "range": "*"}])
    _write_pack(cycle / ".rig/packs", "b-pack", recipe=False,
                dependency=[{"id": "a-pack", "range": "*"}])
    cycle_report = diagnose(project=cycle)
    assert cycle_report["status"] == "failed"
    assert any(item["code"] == "dependency_cycle" for item in cycle_report["findings"])

    ranged = tmp_path / "range"
    _write_pack(ranged / ".rig/packs", "base-pack", recipe=False)
    _write_pack(ranged / ".rig/packs", "range-pack", recipe=False,
                dependency=[{"id": "base-pack", "range": ">=2.0.0"}])
    range_report = diagnose(project=ranged)
    assert range_report["status"] == "failed"
    assert any(item["code"] == "incompatible_dependency"
               for item in range_report["findings"])
