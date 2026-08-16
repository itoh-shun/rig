"""What a caller reading rig's JSON is allowed to rely on (#416 Phase 2).

The exit status says whether rig reached an answer. `--json` says what the answer
was, and that half was never a contract. `wb log --json` returns a bare list,
`coverage --json` returns `{items, summary}`, `eval affected --json` carries
`eval_affected_schema_version`, `mission-control` stamps `rig.mission-control/v1`,
and `wb gates` — the command SKILL.md calls the source of truth for acceptance
criteria IDs — had no `--json` at all. Some outputs say what they are; most do not,
so a consumer learns the shape changed by breaking.

The envelope fixes the part that matters first: **an output that identifies
itself**. `schema` names the shape and its version, `status` mirrors the exit
status so a consumer that captured stdout does not have to also have captured `$?`,
and `data` is the payload. A consumer can then refuse a version it does not know
instead of misreading it.

Existing outputs are not rewritten. They have consumers — this repo's own tests,
`mission_control`, the MCP adapter that reads `plan --json` — and breaking them to
tidy a contract would trade a real cost for a tidy one. Instead `LEGACY` names every
`--json` still on its own shape, and the ceiling below may only be lowered. That is
the same monotonic device the prompt-coverage ratchet uses, for the same reason: a
number that moves from the first day beats a threshold that fires on everything.
"""

import json
import subprocess
import sys

import pytest

from rig_workbench import exitcodes, jsonio

# Every `--json` still emitting its own shape. Entries come off this list as they
# adopt the envelope; nothing is added without raising the ceiling below, which is
# the review this exists to force.
EXPECTED_LEGACY = {
    "wb log", "wb board", "wb route", "wb status", "coverage", "asvs",
    "eval affected", "mission-control", "govern", "evidence", "orchestrate plan",
    "orchestrate graph", "packs", "baseline", "gh-requirement",
}

#: May only be lowered. Raising it means a new command shipped its own JSON shape
#: instead of the envelope, and that should be argued for in review, not merged in
#: passing.
LEGACY_CEILING = 15


def test_the_legacy_list_only_shrinks():
    assert len(jsonio.LEGACY) <= LEGACY_CEILING
    assert set(jsonio.LEGACY) == EXPECTED_LEGACY, (
        "the legacy inventory moved — if a command adopted the envelope, drop it "
        "from both lists and lower LEGACY_CEILING; if a new one shipped its own "
        "shape, say so in review."
    )


def test_a_command_cannot_be_both_enveloped_and_legacy():
    assert set(jsonio.LEGACY).isdisjoint(jsonio.ENVELOPED)


def test_the_envelope_identifies_itself():
    out = jsonio.envelope("gates", {"presets": {}})
    assert out["schema"] == "rig.gates/v1"
    assert out["status"] == "ok"
    assert out["data"] == {"presets": {}}


def test_status_says_the_same_thing_the_exit_code_does():
    """A consumer that captured stdout should not also need `$?` to know what
    happened, and the two must not be able to disagree."""
    assert jsonio.STATUS_FOR_EXIT[exitcodes.OK] == "ok"
    assert jsonio.STATUS_FOR_EXIT[exitcodes.REJECTED] == "rejected"
    assert jsonio.STATUS_FOR_EXIT[exitcodes.ERROR] == "error"
    for code, status in jsonio.STATUS_FOR_EXIT.items():
        assert jsonio.envelope("x", {}, status=status)["status"] == status
        assert jsonio.exit_for_status(status) == code


def test_an_unknown_status_is_refused_rather_than_emitted():
    """A typo'd status would be a lie in a machine-read field."""
    with pytest.raises(ValueError):
        jsonio.envelope("x", {}, status="passed")


def test_the_schema_version_is_part_of_the_name_not_a_sibling_field():
    """`rig.gates/v1` travels with the payload wherever it is copied; a separate
    `version` key gets dropped the first time somebody re-wraps the data."""
    assert jsonio.envelope("gates", {})["schema"].endswith("/v1")
    assert "version" not in jsonio.envelope("gates", {})


def run_cli(*argv):
    return subprocess.run([sys.executable, "-m", "rig_workbench.cli", *argv],
                          capture_output=True, text=True, timeout=120)


def test_gates_json_is_the_first_adopter_and_is_machine_readable():
    """`wb gates` is the authority SKILL.md points at for acceptance-criteria IDs,
    and it could only be read by parsing a Markdown-ish listing. It had no `--json`
    at all, so adding one breaks no consumer — which is what makes it the honest
    place to start."""
    result = run_cli("wb", "gates", "--json")
    assert result.returncode == exitcodes.OK, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema"] == "rig.gates/v1"
    assert payload["status"] == "ok"
    presets = payload["data"]["presets"]
    assert "standard" in presets and isinstance(presets["standard"], list)
    assert payload["data"]["task_types"]


def test_the_text_listing_still_works_untouched():
    result = run_cli("wb", "gates")
    assert result.returncode == exitcodes.OK
    assert "acceptance-gate presets" in result.stdout
