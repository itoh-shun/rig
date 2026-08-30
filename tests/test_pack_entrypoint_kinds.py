"""An entrypoint may name any prompt surface, not only the two that can be invoked (#552).

`TYPE_ASSETS` forbids `knowledge`, `policy` and `reviewer` from owning a command or a
recipe. While an entrypoint's `kind` was restricted to exactly those two, those three types
could not declare an entrypoint at all — and every rule that anchors on one was therefore
unreachable for them:

* `validate_pack`'s `entrypoint lacks evaluation coverage` loop ran over nothing,
* `compose_case_prompt` refused every case for want of a `prompt_entrypoint`, so `pack test`
  could only ever report `structural_only`,
* `sign_pack` requires each case's `prompt_entrypoint` to be an id the manifest declares.

Yet those packs are prompt-bearing by definition — `PROMPT_KINDS` covers `wiki`, `policy`,
`persona` and `output-contract` — so `validate_pack` *required* each of them to ship an
approved evaluation case that could never be run. These tests pin both halves: the widening
that makes the case runnable, and the three refusals that keep it from being a way in.
"""

import copy
import json
import pathlib
import shutil

import pytest

from rig_workbench.packs.manifest import canonical
from rig_workbench.packs.model import PackError
from rig_workbench.packs.tester import compose_case_prompt
from rig_workbench.packs.validation import validate_pack
from test_eval_cases import valid_case
from test_pack_type_permissions import _pack, _write

PERSONA = "# Probe reviewer\n\nRead-only. Report one verdict and never edit.\n"
CONTRACT = "# Probe verdict\n\nEmit exactly one line: `verdict: ACCEPT` or `verdict: REJECT`.\n"
WIKI = "# Probe page\n\nThe sky over the probe site is recorded as green.\n"


def _with_entrypoints(pack: pathlib.Path, entrypoints: list[dict]) -> pathlib.Path:
    """Declare `entrypoints` on an already-built pack.

    A manifest's field set is exact, so declaring entrypoints means moving the pack to the
    catalog shape rather than adding one key. Entrypoints only *name* assets, so no hash
    changes and the manifest stays canonical — which is what lets these fixtures reach the
    entrypoint rules instead of failing the canonical-form check first.
    """
    manifest = json.loads((pack / "pack.yaml").read_text(encoding="utf-8"))
    manifest.update({
        "display_name": "Demo Pack", "description": "A pack built for one rule.",
        "capabilities": ["evaluation"], "entrypoints": entrypoints,
        "references": [], "resources": {},
    })
    (pack / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    return pack


def _bound_case(surfaces: list[str], entrypoint: str) -> dict:
    case = copy.deepcopy(valid_case())
    case["id"] = "demo-case"
    case["prompt_surfaces"] = surfaces
    case["prompt_entrypoint"] = entrypoint
    case["prompt_composition"] = list(surfaces)
    return case


def _reviewer(root: pathlib.Path, *, case: dict | None = None) -> pathlib.Path:
    """A reviewer pack carrying one persona and one output contract, and nothing else."""
    pack = root / "demo-pack"
    pack.mkdir(parents=True, exist_ok=True)
    persona = _write(pack, "facets/personas/probe-reviewer.md", PERSONA)
    contract = _write(pack, "facets/output-contracts/probe-verdict.md", CONTRACT)
    built = _pack(root, "reviewer", {"persona": persona, "output-contract": contract},
                  surface="persona:probe-reviewer")
    if case is not None:
        _write(built, "evals/cases/demo-case/case.json", canonical(case))
        manifest = json.loads((built / "pack.yaml").read_text(encoding="utf-8"))
        from rig_workbench.packs.manifest import digest
        manifest["hashes"]["evals/cases/demo-case/case.json"] = digest(
            built / "evals/cases/demo-case/case.json")
        (built / "pack.yaml").write_text(canonical(manifest), encoding="utf-8")
    return built


def test_a_reviewer_pack_may_name_its_persona_as_an_entrypoint(tmp_path):
    """The fix, stated positively. A reviewer pack's surface is the persona a caller reaches
    for; it is the only kind of entrypoint the type can have."""
    pack = _with_entrypoints(
        _reviewer(tmp_path),
        [{"id": "probe-review", "kind": "persona", "target": "probe-reviewer"}],
    )
    assert validate_pack(pack)["entrypoints"] == [
        {"id": "probe-review", "kind": "persona", "target": "probe-reviewer"}
    ]


def test_a_knowledge_pack_may_name_its_wiki_page_as_an_entrypoint(tmp_path):
    """The same for the type that can carry nothing but inert data."""
    pack = tmp_path / "demo-pack"
    pack.mkdir()
    wiki = _write(pack, "facets/knowledge/probe-page.md", WIKI)
    built = _with_entrypoints(
        _pack(tmp_path, "knowledge", {"wiki": wiki}, surface="wiki:probe-page"),
        [{"id": "probe-page", "kind": "wiki", "target": "probe-page"}],
    )
    assert validate_pack(built)["type"] == "knowledge"


@pytest.mark.parametrize("kind", ["eval-case", "eval-result", "resource"])
def test_an_inert_asset_kind_is_still_not_an_entrypoint(tmp_path, kind):
    """The widening is to `PROMPT_KINDS`, not to every asset kind. A recorded result or a
    stored file is not something a caller reaches for, and naming one as the pack's surface
    would let a case claim to measure a prompt that contains no prompt at all."""
    pack = _with_entrypoints(
        _reviewer(tmp_path),
        [{"id": "probe-review", "kind": kind, "target": "probe-reviewer"}],
    )
    with pytest.raises(PackError, match="pack entrypoint is invalid"):
        validate_pack(pack)


def test_an_entrypoint_still_has_to_name_something_the_pack_owns(tmp_path):
    """Widening the kind grants no reach: the target must still be an asset this pack
    declares, so a reviewer pack cannot point at a recipe it is forbidden to carry."""
    pack = _with_entrypoints(
        _reviewer(tmp_path),
        [{"id": "probe-review", "kind": "recipe", "target": "probe-reviewer"}],
    )
    with pytest.raises(PackError, match="entrypoint target is not owned"):
        validate_pack(pack)


def test_an_entrypoint_may_not_name_another_packs_persona(tmp_path):
    """The same refusal in the direction that matters more: a persona is a kind this type
    *can* own, so only ownership of the named asset separates a valid entrypoint from one
    that would attribute somebody else's prompt to this pack."""
    pack = _with_entrypoints(
        _reviewer(tmp_path),
        [{"id": "probe-review", "kind": "persona", "target": "someone-elses-reviewer"}],
    )
    with pytest.raises(PackError, match="entrypoint target is not owned"):
        validate_pack(pack)


def test_evaluation_coverage_now_reaches_a_reviewer_packs_entrypoint(tmp_path):
    """The coverage rule stops being vacuous for these types. An entrypoint no case is bound
    to is refused here exactly as it is for a skill pack — before the widening this loop had
    nothing to iterate over, so the rule was silently inapplicable to three of six types."""
    pack = _with_entrypoints(
        _reviewer(tmp_path),
        [{"id": "probe-verdict", "kind": "output-contract", "target": "probe-verdict"}],
    )
    with pytest.raises(PackError, match="entrypoint lacks evaluation coverage"):
        validate_pack(pack)


def test_a_reviewer_packs_case_composes_a_prompt_from_its_own_assets(tmp_path):
    """The point of the whole change: `pack test` can now compose this pack's prompt, so its
    quality becomes a measurement instead of a claim. Before the widening this raised
    `evaluation case lacks signed prompt composition` and no reviewer pack could get past it.
    """
    case = _bound_case(["persona:probe-reviewer", "contract:probe-verdict"], "probe-review")
    pack = _with_entrypoints(
        _reviewer(tmp_path, case=case),
        [{"id": "probe-review", "kind": "persona", "target": "probe-reviewer"}],
    )
    manifest = validate_pack(pack)

    prompt = compose_case_prompt(pack, manifest, case, project=tmp_path)

    assert "Read-only. Report one verdict and never edit." in prompt
    assert "verdict: ACCEPT" in prompt
    assert "persona:probe-reviewer (owner=demo-pack)" in prompt


def test_a_composition_that_omits_the_entrypoint_target_is_still_refused(tmp_path):
    """The anchor the entrypoint provides is unchanged by the widening: a case may not claim
    an entrypoint whose asset its prompt does not actually contain."""
    case = _bound_case(["persona:probe-reviewer", "contract:probe-verdict"], "probe-review")
    case["prompt_composition"] = ["contract:probe-verdict"]
    pack = _with_entrypoints(
        _reviewer(tmp_path, case=case),
        [{"id": "probe-review", "kind": "persona", "target": "probe-reviewer"}],
    )
    manifest = validate_pack(pack)

    with pytest.raises(PackError, match="evaluation composition omits entrypoint target"):
        compose_case_prompt(pack, manifest, case, project=tmp_path)


def test_a_persona_entrypoint_is_declarable_but_not_invokable(tmp_path):
    """`invoke_pack` has always carried this guard; until the widening no manifest could
    reach it. Declaring a surface says what the pack is measured on, and says nothing about
    running it — the two are separate, and this is where they separate."""
    from rig_workbench.packs.cli import invoke_pack

    pack = _with_entrypoints(
        _reviewer(tmp_path),
        [{"id": "probe-review", "kind": "persona", "target": "probe-reviewer"}],
    )
    project = tmp_path / "project"
    (project / ".rig" / "packs").mkdir(parents=True)
    shutil.copytree(pack, project / ".rig" / "packs" / "demo-pack")

    with pytest.raises(PackError, match="pack entrypoint kind is not invokable: persona"):
        invoke_pack("demo-pack:probe-review", [], project=project)
