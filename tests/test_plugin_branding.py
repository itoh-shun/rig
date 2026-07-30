"""Plugin/marketplace branding is machine-checked so a rename cannot half-land.

The marketplace name is not cosmetic: Claude Code derives a plugin's data directory as
`<plugin>-<marketplace>`, so renaming it moves `~/.claude/plugins/data/…` and can orphan
an existing install's state. These tests pin the current brand, keep the pre-rename data
directory reachable, and assert the README still documents an install line that resolves.
"""

import json
import pathlib

import pytest

from rig_workbench.orchestrate import config

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MARKETPLACE = "sito-plugins"
LEGACY_MARKETPLACE = "itoshun-local-plugins"
PLUGIN = "rig"


def _load(relative: str) -> dict:
    return json.loads((REPO_ROOT / relative).read_text(encoding="utf-8"))


def test_marketplace_and_owner_carry_the_brand():
    data = _load(".claude-plugin/marketplace.json")
    assert data["name"] == MARKETPLACE
    assert data["owner"]["name"] == MARKETPLACE


def test_plugin_keeps_its_own_name_and_brands_the_author():
    data = _load(".claude-plugin/plugin.json")
    # The plugin name drives every `/rig:*` command id — the brand rename must not touch it.
    assert data["name"] == PLUGIN
    assert data["author"]["name"] == MARKETPLACE


def test_plugin_data_dir_prefers_the_new_name_but_still_finds_the_old_one():
    assert config.PLUGIN_DATA_DIRS == (f"{PLUGIN}-{MARKETPLACE}",
                                       f"{PLUGIN}-{LEGACY_MARKETPLACE}")


def test_find_rig_home_resolves_a_legacy_install(tmp_path, monkeypatch):
    """An install made before the rename keeps working (its state is not orphaned)."""
    legacy = tmp_path / ".claude" / "plugins" / "data" / f"{PLUGIN}-{LEGACY_MARKETPLACE}"
    (legacy / "skills" / "rig").mkdir(parents=True)
    (legacy / "skills" / "rig" / "SKILL.md").write_text("---\nname: engine\n---\n", encoding="utf-8")
    monkeypatch.delenv("RIG_HOME", raising=False)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    assert config.find_rig_home() == legacy


@pytest.mark.parametrize("readme", ["README.md", "README.ja.md"])
def test_readme_install_line_matches_the_shipped_marketplace(readme):
    text = (REPO_ROOT / readme).read_text(encoding="utf-8")
    assert f"/plugin install {PLUGIN}@{MARKETPLACE}" in text
    # The only surviving legacy mentions are the migration note, never an install command.
    assert f"/plugin install {PLUGIN}@{LEGACY_MARKETPLACE}" not in text


@pytest.mark.parametrize("path,needle", [
    ("action.yml", "author: 'sito-plugins'"),
    ("pyproject.toml", '{ name = "sito-plugins" }'),
])
def test_packaging_metadata_carries_the_brand(path, needle):
    assert needle in (REPO_ROOT / path).read_text(encoding="utf-8")
