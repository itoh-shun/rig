"""Plugin/marketplace branding is machine-checked so a rename cannot half-land.

The marketplace name is not cosmetic: Claude Code derives a plugin's data directory as
`<plugin>-<marketplace>`, so renaming it moves `~/.claude/plugins/data/…` and can orphan
an existing install's state. These tests pin the current brand, keep every pre-rename
data directory reachable, and assert the README still documents install lines that
resolve.

Two marketplace names are in play, and they are not the same thing:
- SHARED_MARKETPLACE (`sito-plugins`) is the umbrella marketplace, now hosted by a
  dedicated `sito-plugins` repo rather than this one, and stays the recommended install
  path.
- OWN_MARKETPLACE (`rig`) is this repo's own single-plugin marketplace, for a direct
  install without the shared one. It used to also be named `sito-plugins`, but that
  collided with the new dedicated repo — both declared the same marketplace name, and
  Claude Code keys `known_marketplaces.json` by that name, so whichever was added last
  on the CLI silently overwrote the other's registration — so this repo gave the shared
  name back up. (Separately, Cowork also failed to list this plugin at all, in any
  marketplace — see CHANGELOG 1.28.2. That turned out to be caused by the top-level
  `bin/` directory, unrelated to the marketplace name or self-reference; the directory
  was removed in 1.35.0 once Claude Desktop showed the same symptom.)
"""

import json
import pathlib

import pytest

from rig_workbench.orchestrate import config

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SHARED_MARKETPLACE = "sito-plugins"
OWN_MARKETPLACE = "rig"
LEGACY_MARKETPLACE = "itoshun-local-plugins"
AUTHOR_BRAND = "sito-plugins"
PLUGIN = "rig"
# Claude Code derives a plugin skill's invocation id from its directory name under
# `skills/` (not SKILL.md's frontmatter `name:`), so this directory is what makes
# `rig:engine` invocable. LEGACY_SKILL_DIR is the pre-rename name.
ENGINE_SKILL_DIR = "engine"
LEGACY_SKILL_DIR = "rig"


def _load(relative: str) -> dict:
    return json.loads((REPO_ROOT / relative).read_text(encoding="utf-8"))


def test_marketplace_and_owner_carry_the_brand():
    data = _load(".claude-plugin/marketplace.json")
    assert data["name"] == OWN_MARKETPLACE
    assert data["owner"]["name"] == AUTHOR_BRAND


def test_plugin_keeps_its_own_name_and_brands_the_author():
    data = _load(".claude-plugin/plugin.json")
    # The plugin name drives every `/rig:*` command id — the brand rename must not touch it.
    assert data["name"] == PLUGIN
    assert data["author"]["name"] == AUTHOR_BRAND


def test_plugin_data_dir_prefers_the_shared_name_but_still_finds_older_ones():
    assert config.PLUGIN_DATA_DIRS == (f"{PLUGIN}-{SHARED_MARKETPLACE}",
                                       f"{PLUGIN}-{OWN_MARKETPLACE}",
                                       f"{PLUGIN}-{LEGACY_MARKETPLACE}")


def _fake_install(root: pathlib.Path, marketplace: str, skill_dir: str) -> pathlib.Path:
    installed = root / ".claude" / "plugins" / "data" / f"{PLUGIN}-{marketplace}"
    (installed / "skills" / skill_dir).mkdir(parents=True)
    (installed / "skills" / skill_dir / "SKILL.md").write_text(
        "---\nname: engine\n---\n", encoding="utf-8")
    return installed


@pytest.mark.parametrize("marketplace", [OWN_MARKETPLACE, LEGACY_MARKETPLACE])
def test_find_rig_home_resolves_an_older_install(tmp_path, monkeypatch, marketplace):
    """An install made under a name this repo no longer leads with still works."""
    older = _fake_install(tmp_path, marketplace, ENGINE_SKILL_DIR)
    monkeypatch.delenv("RIG_HOME", raising=False)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    assert config.find_rig_home() == older


def test_find_rig_home_resolves_a_pre_rename_skill_dir(tmp_path, monkeypatch):
    """`skills/rig/` predates the `skills/engine/` rename; such an install still resolves.

    The pip package and the plugin directory can sit at different versions, so a newer
    rig-wb must not lose track of an older plugin install and silently fall back to the
    dev path.
    """
    older = _fake_install(tmp_path, SHARED_MARKETPLACE, LEGACY_SKILL_DIR)
    monkeypatch.delenv("RIG_HOME", raising=False)
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    assert config.find_rig_home() == older
    assert config._skill_root(older) == older / "skills" / LEGACY_SKILL_DIR


@pytest.mark.parametrize("readme", ["README.md", "README.ja.md"])
def test_readme_install_lines_match_the_shipped_marketplaces(readme):
    text = (REPO_ROOT / readme).read_text(encoding="utf-8")
    assert f"/plugin install {PLUGIN}@{SHARED_MARKETPLACE}" in text
    assert f"/plugin install {PLUGIN}@{OWN_MARKETPLACE}" in text
    # The only surviving legacy mention is the migration note, never an install command.
    assert f"/plugin install {PLUGIN}@{LEGACY_MARKETPLACE}" not in text


@pytest.mark.parametrize("path,needle", [
    ("action.yml", "author: 'sito-plugins'"),
    ("pyproject.toml", '{ name = "sito-plugins" }'),
])
def test_packaging_metadata_carries_the_brand(path, needle):
    assert needle in (REPO_ROOT / path).read_text(encoding="utf-8")


def test_no_top_level_bin_directory():
    """A top-level `bin/` makes Cowork and Claude Desktop drop this plugin entirely.

    The directory name alone is the trigger — contents, file count, and the executable
    bit make no difference (CHANGELOG 1.28.2 proved it by renaming a byte-identical
    copy). It was kept until 1.35.0 because it backed Claude Code's plugin `bin/`-on-PATH
    feature, but two dead surfaces outweighed one convenience: `.claude-plugin/bin/rig`
    plus `orchestrate install-shim` already cover the same ground. Re-adding `bin/` would
    silently un-list the plugin again, which no other check would notice.
    """
    assert not (REPO_ROOT / "bin").exists(), (
        "a top-level bin/ directory makes Cowork and Claude Desktop fail to list this "
        "plugin — put executables under .claude-plugin/bin/ instead (see CHANGELOG 1.35.0)"
    )
