"""Why an attested source was refused, not merely that it was (#467).

The check itself is unchanged by this file's subject; what changed is that its refusal
names which of four unrelated conditions failed. The distinction is not cosmetic. Reported
as "is not trusted" and nothing else, a mode carrying the group write bit is
indistinguishable from a tampered file, and the operator has no reason to suspect a
permission — thirty-one tests failed in a `git worktree` and passed in the main checkout of
the same commit, and finding out why took a bisect across three working trees.
"""

import os
import pathlib

from rig_workbench.orchestrate.providers import _untrusted_source_reasons


def _reasons(path: pathlib.Path, owner_uid: int | None = None) -> list[str]:
    info = os.stat(path)
    return _untrusted_source_reasons(info, info.st_uid if owner_uid is None else owner_uid)


def _attested(tmp_path: pathlib.Path, mode: int = 0o644) -> pathlib.Path:
    p = tmp_path / "source.md"
    p.write_text("material\n", encoding="utf-8")
    p.chmod(mode)
    return p


def test_a_source_that_meets_every_condition_gives_no_reasons(tmp_path):
    assert _reasons(_attested(tmp_path)) == []


def test_a_group_writable_source_names_its_mode_and_what_produced_it(tmp_path):
    """0664 is what `git` writes under umask 002, which is how a whole working tree
    acquires it at once. The mode alone would tell an operator what is wrong; naming the
    umask tells them why every file in the tree is wrong together."""
    reasons = _reasons(_attested(tmp_path, 0o664))
    assert len(reasons) == 1
    assert "0664" in reasons[0]
    assert "chmod go-w" in reasons[0]
    assert "umask 022" in reasons[0]


def test_a_world_writable_source_is_caught_by_the_same_condition(tmp_path):
    """Group and other are separate bits. A check watching only the group bit would pass
    a file the world can write, which is the more dangerous of the two."""
    reasons = _reasons(_attested(tmp_path, 0o646))
    assert len(reasons) == 1 and "0646" in reasons[0]


def test_a_source_with_a_second_hard_link_says_how_many_it_has(tmp_path):
    """The count is the evidence. "must have exactly one" without saying what was found
    leaves the operator unable to tell a swapped file from a backup tool's link."""
    source = _attested(tmp_path)
    os.link(source, tmp_path / "other-name.md")
    reasons = _reasons(source)
    assert len(reasons) == 1
    assert "2 hard links" in reasons[0]


def test_a_source_that_is_not_a_regular_file_says_so(tmp_path):
    """A FIFO rather than a directory, so the condition is isolated: a directory carries
    two links and mode 775 of its own and would trip three conditions at once, proving
    nothing about which one the message came from."""
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo, 0o644)
    assert _reasons(fifo) == ["it is not a regular file"]


def test_a_source_owned_by_someone_else_names_both_uids(tmp_path):
    """Creating a file owned by another user needs root, so the owner is varied instead —
    the condition compares two numbers and does not care which one moved."""
    source = _attested(tmp_path)
    reasons = _reasons(source, owner_uid=os.stat(source).st_uid + 1)
    assert len(reasons) == 1
    assert str(os.stat(source).st_uid) in reasons[0]
    assert str(os.stat(source).st_uid + 1) in reasons[0]


def test_every_failed_condition_is_reported_not_just_the_first(tmp_path):
    """The reason the four are collected rather than short-circuited. An operator who
    fixes the permission and re-runs, only to be refused again for the link count, learns
    nothing from the second refusal that the first could not have told them.
    """
    source = _attested(tmp_path, 0o664)
    os.link(source, tmp_path / "other-name.md")
    reasons = _reasons(source, owner_uid=os.stat(source).st_uid + 1)
    assert len(reasons) == 3
    assert any("0664" in r for r in reasons)
    assert any("hard links" in r for r in reasons)
    assert any("owned by uid" in r for r in reasons)
