"""The Stop hook that prompts for a new instinct (#306).

Every test here exists because this hook **blocks the stop**. That is not a note
in the margin: it prevents the session from ending and spends a full round-trip,
and most sessions have no instinct worth recording, so almost every firing is
spent saying "nothing this time". A blocking hook that fires when it should not
is worse than one that occasionally stays silent when it could have spoken.

The earlier version had that trade backwards. Every failure path — no session id,
unparseable payload, an unwritable marker directory — degraded to firing on
*every turn*, and nothing checked whether the session had touched rig at all. The
de-duplication was the first thing to break and its failure mode was maximum
noise. So the suite is mostly about silence: the hook must establish each
precondition affirmatively, and go quiet the moment one cannot be established.
"""

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "hooks" / "suggest-instincts.sh"

# The signature the hook looks for to decide the session engaged with rig.
RIG_TRANSCRIPT_LINE = '{"role":"assistant","text":"▸ rig | recipe: bugfix | step: test"}\n'


@pytest.fixture
def transcript(tmp_path):
    """A transcript that shows rig was used this session."""
    path = tmp_path / "transcript.jsonl"
    path.write_text(RIG_TRANSCRIPT_LINE, encoding="utf-8")
    return path


def _adopt(directory: Path) -> Path:
    """Make `directory` the kind of place the hook agrees to speak in.

    The hook is a project-learning feature and now refuses to fire outside an adopted,
    Git-backed project. Every test that expects it to fire therefore needs a cwd that is
    one, and saying so here is what keeps them honest: before this, they inherited pytest's
    own working directory and passed because a developer's checkout happens to contain
    `.rig`. That directory is gitignored, so no fresh checkout has it — CI included — and
    the same tests would have failed there for a reason that has nothing to do with what
    they are about.
    """
    (directory / ".rig").mkdir(exist_ok=True)
    if not (directory / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=directory, check=True)
    return directory


def _run(payload: dict, state_home: Path | None = None,
         cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
    if state_home is not None:
        env["XDG_STATE_HOME"] = str(state_home)
    # A scratch directory that is both the project and its state is what a real invocation
    # looks like. Tests that mean to exercise the project guard itself pass `cwd`
    # explicitly, and are left alone.
    if cwd is None and state_home is not None:
        cwd = _adopt(state_home)
    return subprocess.run([str(HOOK)], input=json.dumps(payload), text=True,
                          capture_output=True, check=True, env=env, cwd=cwd)


def _payload(transcript, session_id="session_abc123", **extra) -> dict:
    return {"stop_hook_active": False, "session_id": session_id,
            "transcript_path": str(transcript), **extra}


# ── when it should speak ─────────────────────────────────────────────────────
def test_a_rig_session_gets_one_prompt(tmp_path, transcript):
    output = json.loads(_run(_payload(transcript), tmp_path).stdout)
    assert output["decision"] == "block"
    assert "[rig instincts]" in output["reason"]
    assert "--add" in output["reason"]


def test_the_command_it_prints_is_one_the_reader_can_run(tmp_path, transcript):
    """It used to print `python3 scripts/workbench.py instincts --add` — a
    repo-relative path that exists in no project that installed rig, which is
    every project this hook ships to."""
    reason = json.loads(_run(_payload(transcript), tmp_path).stdout)["reason"]
    assert "rig-wb wb instincts --add" in reason or "workbench.py instincts --add" in reason
    assert "python3 scripts/workbench.py" not in reason


def test_it_says_that_saying_nothing_is_a_valid_answer(tmp_path, transcript):
    """Without this the blocked stop reads as a demand, and the model invents an
    instinct to satisfy it — which is how a learning store fills up with noise."""
    reason = json.loads(_run(_payload(transcript), tmp_path).stdout)["reason"]
    assert "most sessions won't have one" in reason


# ── once per session ─────────────────────────────────────────────────────────
def test_it_fires_once_then_stays_quiet(tmp_path, transcript):
    assert json.loads(_run(_payload(transcript), tmp_path).stdout)["decision"] == "block"
    for _ in range(3):
        again = _run(_payload(transcript), tmp_path)
        assert again.stdout == "", "the reminder fired more than once in one session"


def test_a_different_session_gets_its_own_prompt(tmp_path, transcript):
    assert _run(_payload(transcript, session_id="s1"), tmp_path).stdout
    second = _run(_payload(transcript, session_id="s2"), tmp_path)
    assert json.loads(second.stdout)["decision"] == "block"


def test_the_marker_does_not_live_under_tmpdir(tmp_path, transcript):
    """The once-per-session guarantee used to be keyed to `$TMPDIR`, so any
    environment handing out a per-invocation temp directory lost it without a
    word — and the symptom was the reminder firing every single turn."""
    _run(_payload(transcript), tmp_path)
    assert (tmp_path / "rig" / "instinct-prompts" / "session_abc123").is_file()


def test_a_session_id_cannot_escape_the_marker_directory(tmp_path, transcript):
    hostile = _payload(transcript, session_id="../../etc/passwd")
    assert json.loads(_run(hostile, tmp_path).stdout)["decision"] == "block"
    markers = list((tmp_path / "rig" / "instinct-prompts").iterdir())
    assert [m.name for m in markers] == ["______etc_passwd"]


def test_a_marker_it_cannot_write_means_it_says_nothing(tmp_path, transcript):
    """Failing to write the marker means it cannot promise to stay quiet on the
    next turn. Speaking anyway is how a one-shot prompt becomes an every-turn
    one, so an unprovable "first time" is treated as "not now"."""
    blocked = tmp_path / "state"
    blocked.mkdir()
    # A plain file where the marker directory needs to be. Blocking by file type
    # rather than by permission bits, so the test means the same thing when the
    # suite runs as root — where a 0o500 directory is still writable.
    (blocked / "rig").write_text("not a directory", encoding="utf-8")
    assert _run(_payload(transcript), blocked).stdout == ""


# ── when it must stay silent ─────────────────────────────────────────────────
def test_a_session_that_never_touched_rig_is_left_alone(tmp_path):
    """The defect behind the report. Nothing checked this, so the hook
    interrupted every session in every project that had the plugin installed,
    including ones with no connection to rig whatsoever."""
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"role":"user","text":"fix the CSS on the header"}\n',
                          encoding="utf-8")
    assert _run(_payload(transcript), tmp_path).stdout == ""


def test_setup_in_a_git_directory_without_rig_state_is_left_alone(tmp_path):
    """The setup command may mention rig before a project adopts `.rig`."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"role":"assistant","text":"/rig:setup"}\n',
                          encoding="utf-8")
    assert _run(_payload(transcript), tmp_path, cwd=tmp_path).stdout == ""


def test_rig_in_a_non_git_directory_is_left_alone(tmp_path):
    """A non-Git working directory must not be interrupted by a project hook."""
    (tmp_path / ".rig").mkdir()
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(RIG_TRANSCRIPT_LINE, encoding="utf-8")
    assert _run(_payload(transcript), tmp_path, cwd=tmp_path).stdout == ""


def test_without_a_session_id_it_stays_silent(tmp_path, transcript):
    """Reversed deliberately. Older clients send no session id, and the previous
    version fired anyway on the reasoning that losing the reminder is worse than
    repeating it. For a *blocking* hook it is not: with no id there is no marker,
    so "fire anyway" means firing on every turn for the whole session."""
    payload = _payload(transcript)
    payload.pop("session_id")
    for _ in range(2):
        assert _run(payload, tmp_path).stdout == ""


@pytest.mark.parametrize("payload", ["not json", "[]", "null", '{"session_id": 7}'])
def test_an_unparseable_payload_is_not_an_invitation_to_fire(payload, tmp_path):
    result = subprocess.run([str(HOOK)], input=payload, text=True, capture_output=True,
                            check=True, env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                                             "XDG_STATE_HOME": str(tmp_path)})
    assert result.stdout == ""


def test_a_missing_transcript_is_not_assumed_to_be_a_rig_session(tmp_path):
    payload = _payload(tmp_path / "does-not-exist.jsonl")
    assert _run(payload, tmp_path).stdout == ""


def test_no_transcript_path_at_all_stays_silent(tmp_path, transcript):
    payload = _payload(transcript)
    payload.pop("transcript_path")
    assert _run(payload, tmp_path).stdout == ""


def test_a_recursive_stop_hook_exits_without_output(tmp_path, transcript):
    payload = _payload(transcript, stop_hook_active=True)
    assert _run(payload, tmp_path).stdout == ""
