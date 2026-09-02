"""The pack that drafts a pack from local material and stops before approval (#547, slice 1).

Rig drafts the assets and the evaluation case, hands declaration and checking to the pack
tools, and presents the result; a person approves. The three design questions the issue
left open are answered by not touching them: no URL is fetched, no provenance field is
invented for the manifest, and the prompt layer lives in a recipe rather than in
`rig_workbench`.
"""

import json
import pathlib
import subprocess
import sys

import yaml

from rig_workbench.packs.validation import validate_pack

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACK = ROOT / "packs" / "domain" / "pack-author"


def _frontmatter(path: pathlib.Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---", 2)[1])


def test_the_pack_validates_and_is_a_tool_because_it_runs_checks():
    manifest = validate_pack(PACK)
    assert manifest["type"] == "tool"
    assert {e["kind"] for e in manifest["entrypoints"]} == {"recipe", "command"}


def test_the_recipe_declares_the_tool_road_and_a_person_s_gate():
    recipe = _frontmatter(PACK / "recipes" / "pack-author.md")
    steps = {s["id"]: s for s in recipe["steps"]}
    assert list(steps) == ["intake", "draft", "declare", "present"]
    checks = "\n".join(steps["declare"]["checks"])
    for command in ("pack sync", "pack validate", "pack doctor", "pack test"):
        assert command in checks
    assert "RIG_PACK_DIR" in checks and "install" not in checks and "promote" not in checks
    assert steps["present"]["gate"] == "acceptance-gate"
    assert steps["present"]["personas"] == ["pack-draft-reviewer"]
    assert steps["draft"]["personas"] == ["pack-author"]


def test_the_rules_keep_material_local_and_approval_human():
    rules = (PACK / "facets" / "policies" / "pack-author-rules.md").read_text(encoding="utf-8")
    assert "URL は取りに行きません" in rules
    assert "`draft`" in rules and "`approved` に\n   しません" in rules or "approved" in rules
    assert "promote しません" in rules
    assert "`evidence`" in rules and "`sources` とは" in rules


def test_the_shipped_cases_pin_the_two_refusals():
    """Shipped as `approved` because `validate` refuses a prompt-bearing pack with no approved
    case, the same way the layout-gate pack shipped (#567): authored, not measured. `pack test`
    reports `structural_only` here, and the recipe's own rule — a *drafted* pack's case stays
    `draft` until a person promotes it — is about packs this recipe writes, not this one."""
    cases = sorted((PACK / "evals" / "cases").glob("*/case.json"))
    assert [c.parent.name for c in cases] == [
        "pack-author-presents-a-draft-never-an-approval", "pack-author-refuses-to-fetch-a-url"]
    for case in cases:
        doc = json.loads(case.read_text(encoding="utf-8"))
        assert doc["status"] == "approved"
        assert doc["provider_policy"] == {"allowed": [], "mode": "any"}
    url = json.loads(cases[1].read_text(encoding="utf-8"))
    assert "https://intranet.example" in url["target_inputs"]["request"]
    assert "contains:refused:" in url["target_expectations"]
    present = json.loads(cases[0].read_text(encoding="utf-8"))
    assert "contains:STRUCTURAL_ONLY" in present["target_expectations"]


def test_every_prompt_asset_is_referenced_and_the_wiki_is_injected():
    manifest = validate_pack(PACK)
    referenced = {(r["kind"], r["id"]) for r in manifest["references"]}
    for persona in ("pack-author", "pack-draft-reviewer"):
        assert ("persona", persona) in referenced
        fm = _frontmatter(PACK / "facets" / "personas" / f"{persona}.md")
        assert fm["inject"] == ["[[pack-authoring-road]]"]
    assert ("wiki", "pack-authoring-road") in referenced


def test_the_pack_cli_agrees_with_the_test_s_reading():
    env = {"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"}
    for sub in ("validate", "doctor"):
        proc = subprocess.run([sys.executable, "-m", "rig_workbench.cli", "pack", sub, str(PACK)],
                              capture_output=True, text=True, env=env, cwd=ROOT)
        assert proc.returncode == 0, proc.stdout + proc.stderr
