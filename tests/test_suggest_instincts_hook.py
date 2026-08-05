import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "hooks" / "suggest-instincts.sh"


def test_stop_hook_blocks_with_instinct_consideration_as_json() -> None:
    result = subprocess.run(
        [str(HOOK)],
        input=json.dumps({"stop_hook_active": False}),
        text=True,
        capture_output=True,
        check=True,
    )

    output = json.loads(result.stdout)
    assert output["decision"] == "block"
    assert "[rig instincts]" in output["reason"]
    assert "workbench.py instincts --add" in output["reason"]


def test_recursive_stop_hook_exits_successfully_without_output() -> None:
    result = subprocess.run(
        [str(HOOK)],
        input=json.dumps({"stop_hook_active": True}),
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout == ""
