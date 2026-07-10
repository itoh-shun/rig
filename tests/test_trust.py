"""Trust gate for project-local recipe overlays (<cwd>/.rig/recipes)."""

import pathlib

import pytest

from rig_workbench.orchestrate import config, recipes

RECIPE_BODY = """---
name: sneaky
steps:
  - id: s1
    instruction: do the thing
    checks:
      - "echo pwned"
---
body
"""


@pytest.fixture
def project_overlay(tmp_path, monkeypatch):
    """A scratch project overlay dir with one recipe, plus an isolated trust store."""
    overlay = tmp_path / "proj" / ".rig" / "recipes"
    overlay.mkdir(parents=True)
    recipe = overlay / "sneaky.md"
    recipe.write_text(RECIPE_BODY, encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_RECIPES", overlay)
    monkeypatch.setenv("RIG_TRUST_STORE", str(tmp_path / "trusted.json"))
    monkeypatch.delenv("RIG_ALLOW_PROJECT_RECIPES", raising=False)
    return recipe


def test_untrusted_project_recipe_refuses(project_overlay):
    with pytest.raises(SystemExit) as e:
        recipes.resolve_recipe("sneaky")
    assert e.value.code == 2


def test_explicit_path_into_overlay_also_refuses(project_overlay):
    with pytest.raises(SystemExit) as e:
        recipes.resolve_recipe(str(project_overlay))
    assert e.value.code == 2


def test_env_consent_allows_and_records(project_overlay, monkeypatch, tmp_path):
    monkeypatch.setenv("RIG_ALLOW_PROJECT_RECIPES", "1")
    assert recipes.resolve_recipe("sneaky") == project_overlay.parent / "sneaky.md"
    # consent is recorded: a later run without the env var passes silently
    monkeypatch.delenv("RIG_ALLOW_PROJECT_RECIPES")
    assert recipes.resolve_recipe("sneaky").name == "sneaky.md"
    assert (tmp_path / "trusted.json").exists()


def test_modified_file_requires_reconsent(project_overlay, monkeypatch):
    monkeypatch.setenv("RIG_ALLOW_PROJECT_RECIPES", "1")
    recipes.resolve_recipe("sneaky")
    monkeypatch.delenv("RIG_ALLOW_PROJECT_RECIPES")
    project_overlay.write_text(RECIPE_BODY + "\n# edited\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        recipes.resolve_recipe("sneaky")
    assert e.value.code == 2


def test_shipped_recipes_are_exempt(project_overlay):
    shipped = config.RECIPES / "review-only.md"
    assert recipes.resolve_recipe("review-only") == shipped


def test_extends_parent_in_overlay_is_gated(project_overlay, tmp_path, monkeypatch):
    child = tmp_path / "child.md"
    child.write_text("---\nname: child\nextends: sneaky\nsteps: []\n---\n", encoding="utf-8")
    fm = recipes.parse_frontmatter(child)
    with pytest.raises(SystemExit) as e:
        recipes.resolve_extends(fm, child)
    assert e.value.code == 2
