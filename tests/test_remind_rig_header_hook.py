import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "hooks" / "remind-rig-header.sh"


def test_active_rig_run_emits_user_prompt_submit_context_as_json(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('assistant: ▸ rig | recipe: bugfix | step: implement (4/7)\n')

    result = subprocess.run(
        [str(HOOK)],
        input=json.dumps({"transcript_path": str(transcript)}),
        text=True,
        capture_output=True,
        check=True,
    )

    output = json.loads(result.stdout)
    hook_output = output["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "UserPromptSubmit"
    assert "[rig run-continuity]" in hook_output["additionalContext"]


def test_no_active_rig_run_exits_successfully_without_output(tmp_path: Path) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("assistant: ordinary conversation\n")

    result = subprocess.run(
        [str(HOOK)],
        input=json.dumps({"transcript_path": str(transcript)}),
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout == ""
