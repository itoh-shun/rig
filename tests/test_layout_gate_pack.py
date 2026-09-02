"""The layout-gate pack, and the one property that makes shipping it honest.

A pack may not carry runnable code — `.sh` and `.py` are refused by extension,
`.js` and `.mjs` by MIME — so the sensors live in `scripts/layout/` and the pack
carries them as Markdown reference implementations. Two copies drift the moment
nobody is comparing them, which is exactly the failure mode the pack exists to
argue against, so the comparison is a test rather than a convention.
"""

import pathlib
import re
import subprocess


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PACK = REPO_ROOT / "packs" / "domain" / "layout-gate"
SCRIPTS = REPO_ROOT / "scripts" / "layout"

REFERENCES = {
    "layout-fit.js": "layout-fit.reference.md",
    "check-html-layout.mjs": "check-html-layout.reference.md",
}


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("RIG_HOME", str(REPO_ROOT))
    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("RIG_ORG_HOME", raising=False)


def _fenced_code(markdown: str) -> str:
    blocks = re.findall(r"^```[a-z]*\n(.*?)^```$", markdown, re.MULTILINE | re.DOTALL)
    assert len(blocks) == 1, f"expected exactly one fenced block, found {len(blocks)}"
    return blocks[0]


def test_pack_is_opt_in_valid_and_typed_tool(monkeypatch, tmp_path):
    from rig_workbench.packs.manifest import read_json_yaml
    from rig_workbench.packs.resolver import resolve_asset
    from rig_workbench.packs.validation import validate_pack

    _isolated(monkeypatch, tmp_path)
    assert not (REPO_ROOT / "skills/engine/recipes/layout-gate.md").exists()
    assert not (REPO_ROOT / "commands/layout-gate.md").exists()
    assert resolve_asset("recipe", "layout-gate", project=tmp_path) is None

    manifest = validate_pack(PACK)
    assert manifest["id"] == "layout-gate"
    assert manifest["version"] == "0.1.0"
    # Only a `tool` pack may ship a recipe that declares `checks:`, and this recipe
    # does. Demoting the type would have to drop the executing step, not just relabel.
    assert manifest["type"] == "tool"
    assert manifest["dependencies"] == []
    _raw, compatibility = read_json_yaml(PACK / "compatibility.yaml")
    assert compatibility["pack_version"] == manifest["version"]
    assert compatibility["engine"] == manifest["engine"]
    assert {"id": "layout-gate", "kind": "recipe", "target": "layout-gate"} in (
        manifest["entrypoints"])
    assert {"id": "layout-gate-command", "kind": "command", "target": "layout-gate"} in (
        manifest["entrypoints"])


def test_the_only_host_commands_are_the_declared_project_gate():
    from rig_workbench.packs.manifest import parse_frontmatter_subset

    recipe = parse_frontmatter_subset(PACK / "recipes" / "layout-gate.md")
    checks = [check for step in recipe["steps"] for check in step.get("checks", [])]
    # Two lines, both naming the same project-owned path: the pack never runs code
    # it shipped, because it cannot ship any.
    assert checks == ["test -x ./scripts/layout-gate.sh", "./scripts/layout-gate.sh"]
    assert (REPO_ROOT / "scripts" / "layout-gate.sh").is_file()


def test_pack_carries_no_runnable_resource():
    from rig_workbench.packs.resources import media_type_of

    for relative in (m := __import__("json").loads(
            (PACK / "pack.yaml").read_text()))["assets"]["resource"]:
        assert media_type_of(PACK / relative) == "text/markdown"
    assert m["assets"]["resource"] == [
        "resources/check-html-layout.reference.md",
        "resources/layout-fit.reference.md",
    ]


def test_reference_implementations_match_the_scripts_that_run():
    for script, reference in REFERENCES.items():
        shipped = _fenced_code((PACK / "resources" / reference).read_text())
        assert shipped == (SCRIPTS / script).read_text(), (
            f"{reference} has drifted from scripts/layout/{script}; regenerate it")


def test_the_sensor_catches_an_overflow_and_a_collision():
    probe = """
      const { LayoutGate } = require(process.argv[1]);
      const over = new LayoutGate();
      over.text(1, "body", { x: 0, y: 0, w: 3, h: 0.4, fontPt: 14,
                             text: "日本語がぎっしり詰まった長い本文をせまい枠に入れます" });
      const hit = new LayoutGate();
      hit.box(1, "a", { x: 0, y: 0, w: 2, h: 2 });
      hit.box(1, "b", { x: 1, y: 1, w: 2, h: 2 });
      const nested = new LayoutGate();
      nested.box(1, "card", { x: 0, y: 0, w: 4, h: 4 });
      nested.box(1, "code", { x: 1, y: 1, w: 2, h: 2 });
      console.log(JSON.stringify([over.overflows.length, hit.collisions().length,
                                  nested.collisions().length]));
    """
    run = subprocess.run(
        ["node", "-e", probe, str(SCRIPTS / "layout-fit.js")],
        capture_output=True, text=True, check=True,
    )
    # An overflow, a real collision, and no complaint about an intentional nesting.
    assert run.stdout.strip().endswith("[1,1,0]")
