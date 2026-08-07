import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "hooks" / "suggest-instincts.sh"


def _run(payload: dict, tmpdir: Path | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
    if tmpdir is not None:
        env["TMPDIR"] = str(tmpdir)
    return subprocess.run(
        [str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )


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


def test_fires_once_per_session_then_stays_quiet(tmp_path) -> None:
    """The reminder blocks the stop, so repeating it costs a round-trip every turn.

    One prompt per session is enough to make the model consider recording an
    instinct; firing again on every subsequent turn just spends turns on
    "nothing this time".
    """
    payload = {"stop_hook_active": False, "session_id": "session_abc123"}

    first = _run(payload, tmp_path)
    assert json.loads(first.stdout)["decision"] == "block"

    for _ in range(3):
        again = _run(payload, tmp_path)
        assert again.stdout == "", "the reminder fired more than once in one session"


def test_a_different_session_gets_its_own_reminder(tmp_path) -> None:
    assert json.loads(_run({"stop_hook_active": False, "session_id": "s1"}, tmp_path).stdout)
    second = _run({"stop_hook_active": False, "session_id": "s2"}, tmp_path)
    assert json.loads(second.stdout)["decision"] == "block"


def test_a_session_id_cannot_escape_the_marker_directory(tmp_path) -> None:
    """Path separators in the id would otherwise write outside the marker dir."""
    hostile = {"stop_hook_active": False, "session_id": "../../etc/passwd"}
    assert json.loads(_run(hostile, tmp_path).stdout)["decision"] == "block"

    markers = list((tmp_path / "rig-instinct-hook").iterdir())
    assert [m.name for m in markers] == ["______etc_passwd"]


def test_without_a_session_id_it_still_fires(tmp_path) -> None:
    """Older clients send no session_id; losing the reminder entirely is worse."""
    for _ in range(2):
        result = _run({"stop_hook_active": False}, tmp_path)
        assert json.loads(result.stdout)["decision"] == "block"


def test_recursive_stop_hook_exits_successfully_without_output() -> None:
    result = subprocess.run(
        [str(HOOK)],
        input=json.dumps({"stop_hook_active": True}),
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout == ""
