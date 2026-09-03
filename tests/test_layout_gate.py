"""The layout gate, and the two properties that keep shipping it honest.

It was a `tool` pack until #580. A pack may not carry runnable code, so the
sensors lived in `scripts/layout/` and the pack carried Markdown transcriptions
of them; keeping the two copies equal needed a generator and a drift test. Both
existed only to serve the pack boundary, and both are gone with it. What still
has to hold is that the `measure` step runs nothing rig shipped — the one
command it executes is the project's own — and that the sensor it leads to
actually catches an overflow and a collision.
"""

import pathlib
import subprocess


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts" / "layout"


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("RIG_HOME", str(REPO_ROOT))
    monkeypatch.setenv("RIG_USER_HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("RIG_ORG_HOME", raising=False)


def test_the_gate_resolves_from_core_without_installing_anything(monkeypatch, tmp_path):
    from rig_workbench.packs.resolver import resolve_asset

    _isolated(monkeypatch, tmp_path)
    assert not (REPO_ROOT / "packs" / "domain" / "layout-gate").exists()

    # `project=tmp_path` is an empty directory: nothing is installed there, so a
    # resolution that succeeds can only have come from the shipped tier.
    for kind, name in (
        ("recipe", "layout-gate"),
        ("persona", "layout-builder"),
        ("persona", "layout-gate-reviewer"),
        ("policy", "layout-fit-rules"),
        ("output-contract", "layout-gate-verdict"),
        ("instruction", "layout-build"),
        ("instruction", "layout-measure"),
        ("instruction", "layout-gate-review"),
    ):
        resolved = resolve_asset(kind, name, project=tmp_path)
        assert resolved is not None, f"{kind}:{name} no longer resolves"
        assert resolved.tier == "core", f"{kind}:{name} resolved at {resolved.tier}"
        assert resolved.pack_id == "rig-core"
    assert (REPO_ROOT / "commands" / "layout-gate.md").is_file()


def test_the_only_host_commands_are_the_declared_project_gate():
    from rig_workbench.packs.manifest import parse_frontmatter_subset

    recipe = parse_frontmatter_subset(REPO_ROOT / "skills/engine/recipes/layout-gate.md")
    checks = [check for step in recipe["steps"] for check in step.get("checks", [])]
    # Two lines, both naming the same project-owned path. Absorbing the pack moved
    # where the recipe lives; it did not give the recipe anything of rig's to run.
    assert checks == ["test -x ./scripts/layout-gate.sh", "./scripts/layout-gate.sh"]
    assert (REPO_ROOT / "scripts" / "layout-gate.sh").is_file()


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
