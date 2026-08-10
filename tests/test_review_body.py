"""Reviewer body persistence via `review --body <persona>=@<path>` (T1 of the
evidence-anchor gate).

Covers: the body round-trip into `.rig/runs/<id>/reviews/<persona>.md`, that
`--set` alone behaves exactly as before, that review.json's schema is untouched
in both cases, and the three edge cases — a `--body` persona with no verdict, an
unreadable/absent path, and re-recording (upsert).

Subprocess smoke tests against a throwaway git repo, mirroring
tests/test_confidence.py's fixtures.
"""

import json
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKBENCH = REPO_ROOT / "scripts" / "workbench.py"

BODY = "REJECT: the guard is missing.\n\n- `app/auth.py:42` returns before the check\n"


def run_cli(args, cwd):
    return subprocess.run([sys.executable, str(WORKBENCH), *args],
                          capture_output=True, text=True, cwd=cwd, timeout=60)


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture
def task_id(git_repo):
    r = run_cli(["new", "test task", "--type", "review", "--no-worktree"], git_repo)
    assert r.returncode == 0
    return next((git_repo / ".rig" / "runs").iterdir()).name


def review_json(git_repo, task_id):
    return json.loads((git_repo / ".rig" / "runs" / task_id / "review.json").read_text(encoding="utf-8"))


def body_path(git_repo, task_id, persona):
    return git_repo / ".rig" / "runs" / task_id / "reviews" / f"{persona}.md"


def write_body(git_repo, name="body.md", text=BODY):
    p = git_repo / name
    p.write_text(text, encoding="utf-8")
    return p


# ── body round-trip ───────────────────────────────────────────────────────────
def test_body_is_written_and_reads_back_verbatim(git_repo, task_id):
    src = write_body(git_repo)
    r = run_cli(["review", task_id, "--set", "security-reviewer=REJECT",
                 "--body", f"security-reviewer=@{src}"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert body_path(git_repo, task_id, "security-reviewer").read_text(encoding="utf-8") == BODY
    assert "reviews/security-reviewer.md" in r.stdout


def test_body_preserves_non_ascii_prose(git_repo, task_id):
    text = "REJECT: 認可チェックが抜けている（`app/auth.py:42`）。\n"
    src = write_body(git_repo, "ja.md", text)
    r = run_cli(["review", task_id, "--set", "design-reviewer=REJECT",
                 "--body", f"design-reviewer=@{src}"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert body_path(git_repo, task_id, "design-reviewer").read_text(encoding="utf-8") == text


def test_colon_persona_round_trips(git_repo, task_id):
    """rig's own reviewer agents are named `rig:security-reviewer`, and that is
    the name the fan-out records verdicts under — so the colon the guard regex
    permits has to survive all the way to the file and back. Nothing downstream
    splits a body filename on `:`; the anchor sensor takes whatever `reviews/`
    holds by name."""
    src = write_body(git_repo, "colon.md")
    r = run_cli(["review", task_id, "--set", "rig:security-reviewer=REJECT",
                 "--body", f"rig:security-reviewer=@{src}"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert body_path(git_repo, task_id, "rig:security-reviewer").read_text(
        encoding="utf-8") == BODY
    assert review_json(git_repo, task_id)["verdicts"][0]["persona"] == "rig:security-reviewer"
    assert "reviews/rig:security-reviewer.md" in r.stdout
    # …and the sensor's body reader picks it up under that exact label
    from rig_workbench.workbench.anchors import review_bodies
    assert [label for _f, label in review_bodies(git_repo / ".rig" / "runs" / task_id)] == \
        ["reviews/rig:security-reviewer.md"]


def test_relative_body_path_resolves_against_cwd(git_repo, task_id):
    write_body(git_repo)
    r = run_cli(["review", task_id, "--set", "p=APPROVE", "--body", "p=@body.md"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert body_path(git_repo, task_id, "p").read_text(encoding="utf-8") == BODY


def test_several_bodies_in_one_invocation(git_repo, task_id):
    a = write_body(git_repo, "a.md", "A: `x.py:1`\n")
    b = write_body(git_repo, "b.md", "B: `y.py:2`\n")
    r = run_cli(["review", task_id, "--set", "a=APPROVE", "--set", "b=REJECT",
                 "--body", f"a=@{a}", "--body", f"b=@{b}"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert body_path(git_repo, task_id, "a").read_text(encoding="utf-8") == "A: `x.py:1`\n"
    assert body_path(git_repo, task_id, "b").read_text(encoding="utf-8") == "B: `y.py:2`\n"


def test_empty_body_file_is_accepted_as_an_empty_body(git_repo, task_id):
    # Zero-anchor bodies are a reportable state downstream, not a CLI error.
    src = write_body(git_repo, "empty.md", "")
    r = run_cli(["review", task_id, "--set", "p=APPROVE", "--body", f"p=@{src}"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert body_path(git_repo, task_id, "p").read_text(encoding="utf-8") == ""


# ── --set alone is unchanged ──────────────────────────────────────────────────
def test_set_alone_still_works_and_writes_no_reviews_dir(git_repo, task_id):
    r = run_cli(["review", task_id, "--set", "security-reviewer=APPROVE"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout == f"{task_id} review verdicts: security-reviewer=APPROVE\n"
    assert not (git_repo / ".rig" / "runs" / task_id / "reviews").exists()

    data = review_json(git_repo, task_id)
    assert [v["persona"] for v in data["verdicts"]] == ["security-reviewer"]
    assert data["verdicts"][0]["verdict"] == "APPROVE"


def test_body_is_optional_for_a_subset_of_personas(git_repo, task_id):
    src = write_body(git_repo)
    r = run_cli(["review", task_id, "--set", "a=APPROVE", "--set", "b=REJECT",
                 "--body", f"b=@{src}"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not body_path(git_repo, task_id, "a").exists()
    assert body_path(git_repo, task_id, "b").exists()


# ── review.json schema is untouched ───────────────────────────────────────────
@pytest.mark.parametrize("with_body", [False, True])
def test_review_json_schema_unchanged(git_repo, task_id, with_body):
    args = ["review", task_id, "--set", "security-reviewer=APPROVE"]
    if with_body:
        args += ["--body", f"security-reviewer=@{write_body(git_repo)}"]
    r = run_cli(args, git_repo)
    assert r.returncode == 0, r.stdout + r.stderr

    data = review_json(git_repo, task_id)
    assert set(data) == {"task_id", "verdicts"}
    assert data["task_id"] == task_id
    for v in data["verdicts"]:
        assert set(v) == {"persona", "verdict", "recorded_at"}


def test_recorded_body_does_not_disturb_review_json_readers(git_repo, task_id):
    src = write_body(git_repo)
    run_cli(["review", task_id, "--set", "security-reviewer=APPROVE",
             "--body", f"security-reviewer=@{src}"], git_repo)
    # confidence reads review.json's verdicts; stats aggregates them
    r = run_cli(["confidence", task_id], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "security-reviewer: unmeasured" in r.stdout
    r = run_cli(["stats"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "security-reviewer" in r.stdout


# ── edge case: --body persona with no verdict ─────────────────────────────────
def test_body_without_a_matching_verdict_is_rejected(git_repo, task_id):
    src = write_body(git_repo)
    r = run_cli(["review", task_id, "--set", "security-reviewer=APPROVE",
                 "--body", f"typo-reviewer=@{src}"], git_repo)
    assert r.returncode == 1
    assert "typo-reviewer" in r.stderr
    assert not body_path(git_repo, task_id, "typo-reviewer").exists()
    # and the whole invocation is refused — no verdict is recorded either
    assert not (git_repo / ".rig" / "runs" / task_id / "review.json").exists()


def test_body_for_a_persona_recorded_in_an_earlier_invocation_is_accepted(git_repo, task_id):
    src = write_body(git_repo)
    run_cli(["review", task_id, "--set", "a=APPROVE"], git_repo)
    r = run_cli(["review", task_id, "--set", "b=REJECT", "--body", f"a=@{src}"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert body_path(git_repo, task_id, "a").read_text(encoding="utf-8") == BODY


def test_persona_with_a_path_separator_cannot_escape_the_run_dir(git_repo, task_id):
    src = write_body(git_repo)
    r = run_cli(["review", task_id, "--set", "../../pwn=APPROVE",
                 "--body", f"../../pwn=@{src}"], git_repo)
    assert r.returncode == 1
    assert "cannot be used as a --body filename" in r.stderr
    # without the guard the write would land here, two levels up from reviews/
    assert not (git_repo / ".rig" / "runs" / "pwn.md").exists()


# ── edge case: missing / unreadable path ──────────────────────────────────────
def test_missing_body_path_fails_before_any_verdict_is_recorded(git_repo, task_id):
    r = run_cli(["review", task_id, "--set", "security-reviewer=APPROVE",
                 "--body", "security-reviewer=@nope.md"], git_repo)
    assert r.returncode == 1
    assert "nope.md" in r.stderr
    assert not (git_repo / ".rig" / "runs" / task_id / "review.json").exists()


def test_unreadable_body_path_reports_instead_of_traceback(git_repo, task_id):
    (git_repo / "adir").mkdir()
    r = run_cli(["review", task_id, "--set", "p=APPROVE", "--body", "p=@adir"], git_repo)
    assert r.returncode == 1
    assert "cannot be read" in r.stderr
    assert "Traceback" not in r.stderr


def test_non_utf8_body_reports_instead_of_traceback(git_repo, task_id):
    (git_repo / "sjis.md").write_bytes("認可".encode("shift_jis"))
    r = run_cli(["review", task_id, "--set", "p=APPROVE", "--body", "p=@sjis.md"], git_repo)
    assert r.returncode == 1
    assert "cannot be read" in r.stderr
    assert "Traceback" not in r.stderr


def test_inline_text_without_the_at_prefix_is_rejected(git_repo, task_id):
    r = run_cli(["review", task_id, "--set", "p=APPROVE", "--body", "p=some prose"], git_repo)
    assert r.returncode == 1
    assert "@<path>" in r.stderr
    assert not body_path(git_repo, task_id, "p").exists()


def test_body_without_an_equals_sign_is_rejected(git_repo, task_id):
    r = run_cli(["review", task_id, "--set", "p=APPROVE", "--body", "p"], git_repo)
    assert r.returncode == 1
    assert "<persona>=@<path>" in r.stderr


# ── edge case: re-recording ───────────────────────────────────────────────────
def test_re_recording_a_body_overwrites_rather_than_appends(git_repo, task_id):
    first = write_body(git_repo, "first.md", "first: `x.py:1`\n")
    second = write_body(git_repo, "second.md", "second: `y.py:2`\n")
    run_cli(["review", task_id, "--set", "p=APPROVE", "--body", f"p=@{first}"], git_repo)
    r = run_cli(["review", task_id, "--set", "p=REJECT", "--body", f"p=@{second}"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert body_path(git_repo, task_id, "p").read_text(encoding="utf-8") == "second: `y.py:2`\n"

    data = review_json(git_repo, task_id)
    assert len(data["verdicts"]) == 1 and data["verdicts"][0]["verdict"] == "REJECT"


def test_re_recording_a_verdict_without_a_body_keeps_the_stored_body(git_repo, task_id):
    src = write_body(git_repo)
    run_cli(["review", task_id, "--set", "p=APPROVE", "--body", f"p=@{src}"], git_repo)
    r = run_cli(["review", task_id, "--set", "p=REJECT"], git_repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert body_path(git_repo, task_id, "p").read_text(encoding="utf-8") == BODY
