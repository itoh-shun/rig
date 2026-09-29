"""`~/.claude/rig/recipes` and `~/.claude/rig/personas` are the user tier the prose promises.

`rig-wb wb route --recipe feature-roles` answered "explicit recipe is not resolvable" for a
recipe sitting at `~/.claude/rig/recipes/feature-roles.md`, because `_legacy_assets` only
read the project tier. These pin the fix end to end: the recipe resolves at the user tier,
it is gated exactly as a user pack is (one consent, never exempt), and one approval satisfies
both readers of trust — the route's `_trusted` and `resolve_recipe`'s `ensure_asset_trusted`.
"""

from __future__ import annotations

import pathlib

import pytest

RECIPE = """---
name: feature-roles
description: user-tier recipe
scope: user
extends: feature
steps:
  - id: design
    model: claude-sonnet-5-5
---

body
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    user = tmp_path / "home"
    (user / ".claude" / "rig" / "recipes").mkdir(parents=True)
    (user / ".claude" / "rig" / "recipes" / "feature-roles.md").write_text(RECIPE, encoding="utf-8")
    (user / ".claude" / "rig" / "personas").mkdir(parents=True)
    (user / ".claude" / "rig" / "personas" / "my-lens.md").write_text("lens\n", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("RIG_USER_HOME", str(user))
    monkeypatch.setenv("HOME", str(tmp_path / "unrelated-home"))
    monkeypatch.setenv("RIG_PACK_TRUST_STORE", str(tmp_path / "pack-trust.json"))
    monkeypatch.setenv("RIG_TRUST_STORE", str(tmp_path / "legacy-trust.json"))
    for name in ("RIG_ALLOW_PROJECT_RECIPES", "RIG_ALLOW_PROJECT_PERSONAS",
                 "RIG_ALLOW_PROJECT_PACKS", "RIG_ORG_HOME"):
        monkeypatch.delenv(name, raising=False)
    return user, project


def test_user_tier_recipe_and_persona_resolve(home):
    from rig_workbench.packs.resolver import catalog, resolve_asset

    _user, project = home
    recipe = resolve_asset("recipe", "feature-roles", project=project)
    persona = resolve_asset("persona", "my-lens", project=project)
    for asset in (recipe, persona):
        assert asset is not None
        assert (asset.tier, asset.pack_id) == ("user", None)
        assert asset.source.startswith("legacy:")
    assert {(a.kind, a.name) for a in catalog(project=project) if a.tier == "user"} >= {
        ("recipe", "feature-roles"), ("persona", "my-lens")}


def test_user_home_is_the_same_answer_for_packs_and_loose_files(home):
    from rig_workbench.packs.resolver import _user_home, pack_roots

    user, project = home
    assert _user_home() == user
    assert dict(pack_roots(project))["user"] == user / ".rig" / "packs"


def test_route_refuses_the_untrusted_user_recipe_then_routes_after_one_approval(home, monkeypatch):
    from rig_workbench.orchestrate import config, recipes
    from rig_workbench.packs.resolver import resolve_asset
    from rig_workbench.packs.model import PackError
    from rig_workbench.workbench.capabilities import resolve_task_route

    _user, project = home
    monkeypatch.setattr(config, "INVOCATION_CWD", project)

    route = resolve_task_route("feature", {"recipe": "feature-roles"}, project)
    assert route["status"] == "trust_required"
    assert (route["tier"], route["pack"]) == ("user", None)
    assert "RIG_ALLOW_PROJECT_RECIPES=1" in route["hint"]

    # resolve_recipe is the other reader, and it refuses too — with the same instruction.
    with pytest.raises(PackError, match="RIG_ALLOW_PROJECT"):
        recipes.resolve_recipe("feature-roles")

    # One consent, given where the recipe is actually resolved for a run ...
    monkeypatch.setenv("RIG_ALLOW_PROJECT_RECIPES", "1")
    path = recipes.resolve_recipe("feature-roles")
    assert path == resolve_asset("recipe", "feature-roles", project=project).path
    monkeypatch.delenv("RIG_ALLOW_PROJECT_RECIPES")

    # ... is remembered, and the route reads it without being asked again.
    route = resolve_task_route("feature", {"recipe": "feature-roles"}, project)
    assert route["status"] == "ready"
    assert route["recipe"] == "feature-roles"
    assert recipes.resolve_recipe("feature-roles") == path

    # An edit re-requires consent in both readers.
    path.write_text(RECIPE + "\nedited\n", encoding="utf-8")
    assert resolve_task_route("feature", {"recipe": "feature-roles"}, project)[
        "status"] == "trust_required"
    with pytest.raises(PackError):
        recipes.resolve_recipe("feature-roles")


def test_a_project_copy_still_beats_the_user_copy(home):
    from rig_workbench.packs.resolver import resolve_all

    _user, project = home
    overlay = project / ".claude" / "rig" / "recipes"
    overlay.mkdir(parents=True)
    (overlay / "feature-roles.md").write_text(RECIPE, encoding="utf-8")
    tiers = [item.tier for item in resolve_all("recipe", "feature-roles", project=project)]
    assert tiers == ["project", "user"]


def test_relative_user_home_keeps_the_pack_before_loose_order(tmp_path, monkeypatch):
    from rig_workbench.packs.resolver import _user_home, resolve_all

    monkeypatch.chdir(tmp_path)
    rel = pathlib.Path("z-home")
    (rel / ".claude" / "rig" / "recipes").mkdir(parents=True)
    (rel / ".claude" / "rig" / "recipes" / "x.md").write_text("x\n", encoding="utf-8")
    monkeypatch.setenv("RIG_USER_HOME", "z-home")
    assert _user_home() == (tmp_path / "z-home").resolve()
    (tmp_path / "proj").mkdir()
    found = resolve_all("recipe", "x", project=tmp_path / "proj")
    assert [(a.tier, a.pack_id) for a in found] == [("user", None)]
    assert found[0].path.is_absolute() and found[0].source == f"legacy:{found[0].path.parent}"


def test_route_does_not_trust_a_user_recipe_on_the_legacy_record_alone(home, monkeypatch):
    import hashlib
    import json

    from rig_workbench.packs.resolver import resolve_asset
    from rig_workbench.workbench.capabilities import _trusted

    _user, project = home
    asset = resolve_asset("recipe", "feature-roles", project=project)
    digest = hashlib.sha256(asset.path.resolve().read_bytes()).hexdigest()
    pathlib.Path(__import__("os").environ["RIG_TRUST_STORE"]).write_text(
        json.dumps({str(asset.path.resolve()): digest}), encoding="utf-8")
    assert _trusted(asset) is False


def test_a_shadowed_user_sibling_extends_parent_is_gated(home, monkeypatch):
    """The parent that is LOADED is gated, not the one `resolve` ranks first."""
    from rig_workbench.orchestrate import config, recipes
    from rig_workbench.packs.model import PackError

    user, project = home
    user_dir = user / ".claude" / "rig" / "recipes"
    (user_dir / "child.md").write_text(
        "---\nname: child\nextends: base\nsteps: []\n---\n", encoding="utf-8")
    (user_dir / "base.md").write_text(
        "---\nname: base\nsteps:\n  - id: a\n    instruction: user\n---\n", encoding="utf-8")
    overlay = project / ".claude" / "rig" / "recipes"
    overlay.mkdir(parents=True)
    (overlay / "base.md").write_text(
        "---\nname: base\nsteps:\n  - id: a\n    instruction: project\n---\n", encoding="utf-8")
    monkeypatch.setattr(config, "INVOCATION_CWD", project)

    # Approve the child (and only the child), as a person would by running it by name.
    monkeypatch.setenv("RIG_ALLOW_PROJECT_RECIPES", "1")
    recipes.resolve_recipe("child")
    monkeypatch.delenv("RIG_ALLOW_PROJECT_RECIPES")

    child = user_dir / "child.md"
    with pytest.raises(PackError, match="untrusted user recipe"):
        recipes._resolve_extends_chain(recipes.parse_frontmatter(child), child, [])


def test_an_unapproved_user_persona_shadowing_a_core_one_is_a_clean_refusal(
        home, tmp_path, monkeypatch, capsys):
    from rig_workbench.orchestrate import commands, composition, config

    user, project = home
    (user / ".claude" / "rig" / "personas" / "security-reviewer.md").write_text(
        "hostile\n", encoding="utf-8")
    monkeypatch.setattr(config, "INVOCATION_CWD", project)
    monkeypatch.setattr(commands, "run_loop",
                        lambda *a, **k: composition._load_persona_brief("security-reviewer"))
    recipe = tmp_path / "r.md"
    recipe.write_text("---\nname: r\nexecutable: true\nsteps:\n  - id: s\n    instruction: do\n---\n",
                      encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        commands.cmd_run([str(recipe), "--provider", "mock", "--goal", "x",
                          "--out", str(tmp_path / "state.json")])
    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "RIG_ALLOW_PROJECT_PERSONAS=1" in text and "Traceback" not in text


def test_a_symlinked_user_extends_parent_pointing_outside_is_gated(home, tmp_path, monkeypatch):
    from rig_workbench.orchestrate import config, recipes
    from rig_workbench.packs.model import PackError

    user, project = home
    user_dir = user / ".claude" / "rig" / "recipes"
    outside = tmp_path / "elsewhere" / "base.md"
    outside.parent.mkdir()
    outside.write_text("---\nname: base\nsteps:\n  - id: a\n    instruction: x\n---\n",
                       encoding="utf-8")
    (user_dir / "base.md").symlink_to(outside)
    (user_dir / "child.md").write_text(
        "---\nname: child\nextends: base\nsteps: []\n---\n", encoding="utf-8")
    monkeypatch.setattr(config, "INVOCATION_CWD", project)
    monkeypatch.setenv("RIG_ALLOW_PROJECT_RECIPES", "1")
    recipes.resolve_recipe("child")          # approves the child only
    monkeypatch.delenv("RIG_ALLOW_PROJECT_RECIPES")

    child = user_dir / "child.md"
    with pytest.raises(PackError, match="untrusted user recipe"):
        recipes._resolve_extends_chain(recipes.parse_frontmatter(child), child, [])

    monkeypatch.setenv("RIG_ALLOW_PROJECT_RECIPES", "1")
    chain = recipes._resolve_extends_chain(recipes.parse_frontmatter(child), child, [])
    assert [name for name, _fm in chain] == [None, "base"]


def test_ab_reports_an_unapproved_user_persona_as_a_clean_refusal(
        home, tmp_path, monkeypatch, capsys):
    from rig_workbench.orchestrate import commands, composition, config

    user, project = home
    monkeypatch.chdir(tmp_path)              # `ab` writes its state files to the cwd
    (user / ".claude" / "rig" / "personas" / "security-reviewer.md").write_text(
        "hostile\n", encoding="utf-8")
    monkeypatch.setattr(config, "INVOCATION_CWD", project)
    monkeypatch.setattr(commands, "run_loop",
                        lambda *a, **k: composition._load_persona_brief("security-reviewer"))
    monkeypatch.setattr(commands, "setup_isolation",
                        lambda name: {"dir": str(tmp_path), "branch": "b"})
    monkeypatch.setattr(commands, "teardown_isolation", lambda iso, final: "clean-removed")
    recipe = tmp_path / "r.md"
    recipe.write_text("---\nname: r\nexecutable: true\nsteps:\n  - id: s\n    instruction: do\n---\n",
                      encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        commands.cmd_ab([str(recipe), str(recipe), "--provider", "mock", "--goal", "x"])
    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert "RIG_ALLOW_PROJECT_PERSONAS=1" in text and "[BLOCKED]" in text
    assert "Traceback" not in text
